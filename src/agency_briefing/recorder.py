"""Controller della registrazione, eseguito SOLO dal demone.

La CLI non avvia processi audio per le registrazioni: scrive una richiesta nel DB
(stato ``requested`` / flag ``stop_requested``) e il demone la esegue. Così la
registrazione è indipendente dalla sessione SSH ed è supervisionata da systemd.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .audio.capture import CaptureProcess, build_capture_argv, list_segment_files
from .audio.devices import device_present, resolve_device
from .audio.wav import read_wav_info
from .config import Config
from .db import Database, iso, parse_iso, utcnow
from .errors import AgencyError, DeviceError
from .finalize import finalize_recording
from .models import Recording, RecState
from .recordings import RecordingStore

log = logging.getLogger(__name__)

REQUEST_EXPIRY_SECONDS = 60


def effective_device_spec(cfg: Config, db: Database) -> str:
    return db.get_setting("audio.device") or cfg.get("audio.device")


def free_mb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024 * 1024)


def recorded_seconds(store: RecordingStore, rec: Recording) -> float:
    total = 0.0
    for seg in list_segment_files(store.segments_dir(rec)):
        try:
            info = read_wav_info(seg)
        except (ValueError, OSError):
            continue
        total += info.duration_seconds
    return total


@dataclass
class _Active:
    rec_id: str
    pcm: str
    part: int
    capture: CaptureProcess | None
    started_monotonic: float = field(default_factory=time.monotonic)
    lost_since: float | None = None
    last_retry: float = 0.0
    last_heartbeat: float = 0.0
    last_disk_check: float = 0.0


class Recorder:
    def __init__(self, cfg: Config, db: Database, store: RecordingStore,
                 on_finalized=None):
        self.cfg = cfg
        self.db = db
        self.store = store
        self.active: _Active | None = None
        self.on_finalized = on_finalized
        self._finalize_q: queue.Queue[str | None] = queue.Queue()
        self._finalizer = threading.Thread(target=self._finalize_loop, name="finalizer",
                                           daemon=True)
        self._finalizer.start()

    # --- ciclo di vita -------------------------------------------------------
    def recover_on_startup(self) -> None:
        now = utcnow()
        window = float(self.cfg.get("recording.resume_window_seconds"))
        for row in self.db.query("SELECT * FROM recordings WHERE state IN (?,?,?,?)",
                                 (RecState.REQUESTED, RecState.RECORDING, RecState.STOPPING,
                                  RecState.FINALIZING)):
            rec = Recording.from_row(row)
            if rec.state == RecState.REQUESTED:
                age = (now - parse_iso(rec.created_at)).total_seconds()
                if age > REQUEST_EXPIRY_SECONDS:
                    self._fail(rec.id, "Richiesta di avvio scaduta (servizio non attivo)")
                continue
            if rec.state == RecState.FINALIZING:
                self._finalize_q.put(rec.id)
                continue
            last = parse_iso(rec.heartbeat_at) or parse_iso(rec.started_at) or now
            gap = (now - last).total_seconds()
            if rec.state == RecState.RECORDING and not rec.stop_requested and gap <= window:
                self.db.add_event("resume", f"Ripresa dopo riavvio del servizio (pausa {gap:.0f}s)",
                                  level="warning", recording_id=rec.id)
                try:
                    self._start_capture(rec, resumed=True)
                except AgencyError as exc:
                    self._end(rec.id, reason="resume_failed", error=exc.message)
                continue
            reason = "stop" if rec.stop_requested else "interrupted"
            error = None if rec.stop_requested else (
                f"Registrazione interrotta (servizio fermo per {gap:.0f}s): "
                "audio recuperato fino all'interruzione")
            self.db.add_event("recovered", error or "Arresto completato dopo il riavvio",
                              level="warning", recording_id=rec.id)
            self._end(rec.id, reason=reason, error=error, ended_at=iso(last))

    def shutdown(self) -> None:
        """SIGTERM del servizio: chiude bene il segmento ma NON segna l'arresto utente.

        Se il servizio riparte entro ``resume_window_seconds`` la registrazione continua.
        """
        if self.active and self.active.capture:
            self.active.capture.stop()
            self._close_segment(self.active)
            self.store.update(self.active.rec_id, heartbeat_at=iso())
        self.active = None
        self._finalize_q.put(None)
        self._finalizer.join(timeout=120)

    # --- tick ---------------------------------------------------------------
    def tick(self) -> None:
        if self.active is None:
            row = self.db.one("SELECT * FROM recordings WHERE kind='mic' AND state=? "
                              "ORDER BY created_at LIMIT 1", (RecState.REQUESTED,))
            if row is not None:
                rec = Recording.from_row(row)
                try:
                    self._start_capture(rec)
                except AgencyError as exc:
                    log.error("Avvio registrazione %s fallito: %s", rec.id, exc.message)
                    self._fail(rec.id, exc.message + (f" — {exc.hint}" if exc.hint else ""))
            return
        self._monitor(self.active)

    # --- interni --------------------------------------------------------------
    def _fail(self, rec_id: str, message: str) -> None:
        self.store.update(rec_id, state=RecState.FAILED, error=message, ended_at=iso())
        self.db.add_event("failed", message, level="error", recording_id=rec_id)

    def _start_capture(self, rec: Recording, resumed: bool = False) -> None:
        min_free = float(self.cfg.get("recording.min_free_mb"))
        if free_mb(self.cfg.data_dir) < min_free:
            raise AgencyError(f"Spazio libero insufficiente (< {min_free:.0f} MB)",
                              hint="Libera spazio sulla scheda SD")
        pcm, _dev = resolve_device(effective_device_spec(self.cfg, self.db))
        seg_dir = self.store.segments_dir(rec)
        seg_dir.mkdir(parents=True, exist_ok=True)
        row = self.db.one("SELECT MAX(part) AS p FROM segments WHERE recording_id=?", (rec.id,))
        part = int(row["p"] or 0) + 1
        active = _Active(rec_id=rec.id, pcm=pcm, part=part, capture=None)
        self._spawn(active, rec)
        fields = dict(state=RecState.RECORDING, heartbeat_at=iso(), device=pcm,
                      sample_rate=int(self.cfg.get("audio.sample_rate")),
                      channels=int(self.cfg.get("audio.channels")))
        if not rec.started_at:
            fields["started_at"] = iso()
        self.store.update(rec.id, **fields)
        self.active = active
        if not resumed:
            self.db.add_event("started", f"Registrazione avviata su {pcm}", recording_id=rec.id)
        log.info("Registrazione %s in corso su %s (parte %d)", rec.id, pcm, part)

    def _spawn(self, active: _Active, rec: Recording | None = None) -> None:
        a = self.cfg.section("audio")
        rec = rec or self.store.get(active.rec_id)
        seg_dir = self.store.segments_dir(rec)
        argv = build_capture_argv(a["capture_command"], active.pcm,
                                  sample_format=a["sample_format"], channels=int(a["channels"]),
                                  rate=int(a["sample_rate"]),
                                  segment_seconds=int(a["segment_seconds"]),
                                  output=seg_dir / f"part{active.part:03d}.wav")
        cap = CaptureProcess(argv, Path(rec.dir_path) / "capture.log")
        cap.start()
        time.sleep(0.5)
        rc = cap.poll()
        if rc is not None:
            cap.stop()
            raise DeviceError(
                f"Il processo di cattura è terminato subito (codice {rc}): "
                f"{cap.tail_log(300) or 'nessun dettaglio'}",
                hint="Microfono scollegato o in uso da un altro programma? "
                     "Verifica con 'gweb audio test'.")
        self.db.execute("INSERT INTO segments(recording_id, part, started_at) VALUES(?,?,?)",
                        (rec.id, active.part, iso()))
        active.capture = cap

    def _close_segment(self, active: _Active) -> None:
        rc = active.capture.poll() if active.capture else None
        self.db.execute("UPDATE segments SET ended_at=?, exit_code=? WHERE recording_id=? "
                        "AND part=? AND ended_at IS NULL",
                        (iso(), rc, active.rec_id, active.part))

    def _monitor(self, a: _Active) -> None:
        now = time.monotonic()
        rec = self.store.get(a.rec_id)
        if now - a.last_heartbeat >= 2:
            self.store.update(rec.id, heartbeat_at=iso())
            a.last_heartbeat = now
        if rec.stop_requested:
            self._stop(a, reason="user")
            return
        started = parse_iso(rec.started_at)
        max_s = float(self.cfg.get("recording.max_duration_minutes")) * 60
        if started and (utcnow() - started).total_seconds() >= max_s:
            self.db.add_event("max_duration", "Durata massima raggiunta: arresto automatico",
                              level="warning", recording_id=rec.id)
            self._stop(a, reason="max_duration")
            return
        if now - a.last_disk_check >= 10:
            a.last_disk_check = now
            critical = float(self.cfg.get("recording.critical_free_mb"))
            if free_mb(self.cfg.data_dir) < critical:
                self._stop(a, reason="disk_full",
                           error=f"Spazio su disco sotto {critical:.0f} MB: registrazione "
                                 "fermata, audio fino a questo punto conservato")
                return
        if a.capture is not None and a.capture.poll() is not None:
            rc = a.capture.poll()
            detail = a.capture.tail_log(300)
            a.capture.stop()
            self._close_segment(a)
            a.capture = None
            a.lost_since = now
            msg = f"Cattura interrotta (codice {rc}): {detail or 'microfono scollegato?'}"
            log.warning("%s — %s", rec.id, msg)
            self.db.add_event("capture_lost", msg, level="warning", recording_id=rec.id)
            self.store.update(rec.id, error="Microfono perso: tentativo di riconnessione")
        if a.capture is None:
            timeout = float(self.cfg.get("recording.reconnect_timeout_seconds"))
            if a.lost_since is not None and now - a.lost_since > timeout:
                self._stop(a, reason="device_lost",
                           error=f"Microfono non disponibile da oltre {timeout:.0f}s: "
                                 "registrazione chiusa con l'audio acquisito")
                return
            if now - a.last_retry >= 3:
                a.last_retry = now
                try:
                    if device_present(a.pcm):
                        a.part += 1
                        self._spawn(a, rec)
                        a.lost_since = None
                        self.store.update(rec.id, error=None)
                        self.db.add_event("capture_resumed", "Microfono di nuovo disponibile: "
                                          "registrazione ripresa", recording_id=rec.id)
                except AgencyError as exc:
                    log.info("Riconnessione non ancora riuscita: %s", exc.message)

    def _stop(self, a: _Active, *, reason: str, error: str | None = None) -> None:
        self.store.update(a.rec_id, state=RecState.STOPPING)
        if a.capture:
            a.capture.stop()
            self._close_segment(a)
        self.active = None
        self._end(a.rec_id, reason=reason, error=error)

    def _end(self, rec_id: str, *, reason: str, error: str | None = None,
             ended_at: str | None = None) -> None:
        rec = self.store.get(rec_id)
        fields = dict(state=RecState.FINALIZING, stop_reason=reason, ended_at=ended_at or iso(),
                      duration_seconds=round(recorded_seconds(self.store, rec), 2))
        if error:
            fields["error"] = error
        elif rec.error and rec.error.startswith("Microfono perso"):
            fields["error"] = None
        self.store.update(rec_id, **fields)
        self.db.add_event("stopped", f"Arresto ({reason})", recording_id=rec_id)
        self._finalize_q.put(rec_id)

    def _finalize_loop(self) -> None:
        while True:
            rec_id = self._finalize_q.get()
            if rec_id is None:
                return
            try:
                rec = finalize_recording(self.cfg, self.store, rec_id)
                if self.on_finalized:
                    self.on_finalized(rec)
            except Exception:  # noqa: BLE001
                log.exception("Errore nella finalizzazione di %s", rec_id)

    def finalize_async(self, rec_id: str) -> None:
        self._finalize_q.put(rec_id)
