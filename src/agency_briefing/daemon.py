"""Demone ``gweb service run``: registrazione + coda upload + keepalive della sessione.

Thread:
- principale: controller registrazione (tick ogni ``poll_interval_seconds``) e heartbeat;
- ``finalizer`` (in Recorder): unione/codifica dei segmenti senza bloccare la cattura;
- ``uploader``: lavori della coda e keepalive dei cookie NotebookLM.
Un file lock garantisce una sola istanza.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import threading
import time

from . import __version__
from .db import iso
from .errors import AgencyError, AuthRequired, Conflict
from .models import JobState, RecState

log = logging.getLogger(__name__)


class AlreadyRunning(AgencyError):
    code = "ALREADY_RUNNING"


class Daemon:
    def __init__(self, app):
        from .queue import UploadWorker
        from .recorder import Recorder

        self.app = app
        self.cfg = app.cfg
        self.db = app.db
        self._stop = threading.Event()
        self.recorder = Recorder(self.cfg, self.db, app.store, on_finalized=self._on_finalized)
        self.worker = UploadWorker(self.cfg, app.queue, app.backend)
        self._lock_fh = None
        self._auth_mtime: float | None = None
        self.started_at = iso()

    def _acquire_lock(self) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_fh = open(self.cfg.lock_path, "a+")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunning("Il servizio è già in esecuzione",
                                 hint="gweb service status") from exc
        self._lock_fh.seek(0)
        self._lock_fh.truncate()
        self._lock_fh.write(str(os.getpid()))
        self._lock_fh.flush()

    def _heartbeat(self) -> None:
        self.db.set_setting("daemon", {"pid": os.getpid(), "heartbeat": iso(),
                                       "started_at": self.started_at, "version": __version__})

    def _on_finalized(self, rec) -> None:
        if not (self.cfg.get("upload.auto_after_stop") and rec.kind == "mic"
                and rec.state == RecState.READY and rec.notebook_id):
            return
        try:
            job = self.app.queue.enqueue(rec, rec.notebook_id)
            log.info("Upload automatico in coda: lavoro %s", job.id)
        except Conflict:
            pass
        except AgencyError as exc:
            log.warning("Upload automatico non accodato: %s", exc.message)

    def stop(self, *_args) -> None:
        log.info("Arresto del servizio richiesto")
        self._stop.set()

    # --- uploader ------------------------------------------------------------
    def _auth_file_changed(self) -> bool:
        path = self.cfg.storage_state_path
        mtime = path.stat().st_mtime if path.exists() else None
        changed = self._auth_mtime is not None and mtime != self._auth_mtime
        self._auth_mtime = mtime
        return changed

    def _keepalive(self) -> None:
        status = {"at": iso(), "ok": False, "error": None}
        try:
            asyncio.run(self.app.backend.keepalive())
            status["ok"] = True
        except AuthRequired as exc:
            status["error"] = "AUTH_REQUIRED"
            log.warning("Sessione NotebookLM non valida: %s", exc.message)
        except AgencyError as exc:
            status["error"] = exc.code
        except Exception as exc:  # noqa: BLE001
            status["error"] = type(exc).__name__
        self.db.set_setting("notebooklm.keepalive", status)

    def _uploader_loop(self) -> None:
        keepalive_s = float(self.cfg.get("notebooklm.keepalive_minutes")) * 60
        last_keepalive = 0.0
        self._auth_file_changed()
        while not self._stop.is_set():
            try:
                if self._auth_file_changed():
                    n = self.db.execute("UPDATE jobs SET next_attempt_at=NULL WHERE state=?",
                                        (JobState.BLOCKED_AUTH,)).rowcount
                    log.info("Sessione NotebookLM aggiornata: %d lavori sbloccati", n)
                job = self.worker.run_once()
                if job is not None:
                    log.info("Lavoro %s -> %s %s", job.id, job.state, job.last_error or "")
                    continue
                if keepalive_s > 0 and time.monotonic() - last_keepalive > keepalive_s and (
                        self.app.backend.local_auth_state().exists):
                    last_keepalive = time.monotonic()
                    self._keepalive()
            except Exception:  # noqa: BLE001
                log.exception("Errore nel ciclo upload")
            self._stop.wait(2.0)

    # --- principale ----------------------------------------------------------
    def run(self) -> int:
        self._acquire_lock()
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        log.info("Servizio gweb %s avviato (pid %d)", __version__, os.getpid())
        self._heartbeat()
        n = self.app.queue.recover_after_restart()
        if n:
            log.warning("%d lavori interrotti: verranno riconciliati", n)
        self.recorder.recover_on_startup()
        uploader = threading.Thread(target=self._uploader_loop, name="uploader", daemon=True)
        uploader.start()
        poll = float(self.cfg.get("service.poll_interval_seconds"))
        last_hb = 0.0
        while not self._stop.is_set():
            try:
                self.recorder.tick()
            except Exception:  # noqa: BLE001 - il ciclo non deve morire
                log.exception("Errore nel controller di registrazione")
            if time.monotonic() - last_hb >= 2:
                self._heartbeat()
                last_hb = time.monotonic()
            self._stop.wait(poll)
        self.recorder.shutdown()
        uploader.join(timeout=10)
        self.db.set_setting("daemon", {"pid": None, "heartbeat": None, "stopped_at": iso(),
                                       "version": __version__})
        self._release_lock()
        log.info("Servizio arrestato")
        return 0

    def _release_lock(self) -> None:
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
            finally:
                self._lock_fh.close()
                self._lock_fh = None
