"""Logica applicativa condivisa. La CLI (e in futuro un'API HTTP autenticata per l'app Mac)
chiama SOLO questi metodi, che ritornano strutture serializzabili e sollevano AgencyError.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .audio.capture import build_capture_argv, list_segment_files, run_capture_once
from .audio.devices import list_capture_devices, resolve_device
from .audio.encode import ffmpeg_available
from .audio.wav import analyze_levels
from .briefing import run_summary, save_summary
from .config import Config, load_config, secure_mkdir
from .db import Database, iso, parse_iso, utcnow
from .errors import (
    AgencyError,
    AuthRequired,
    Conflict,
    DeviceError,
    NotFound,
    NotReady,
    RemoteError,
    ServiceUnavailable,
    StorageError,
    UsageError,
)
from .finalize import finalize_recording
from .models import JobState, Recording, RecState, UploadStatus
from .notebook import create_backend
from .notebook.base import NotebookInfo, SourceState
from .queue import JobQueue, UploadWorker
from .recorder import effective_device_spec, free_mb, recorded_seconds
from .recordings import RecordingStore


def _looks_like_full_id(value: str) -> bool:
    return len(value) >= 30 or value.startswith("nb-")


class AgencyApp:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load_config()
        self.cfg.ensure_dirs()
        self.db = Database(self.cfg.db_path)
        self.store = RecordingStore(self.cfg, self.db)
        self.queue = JobQueue(self.cfg, self.db, self.store)
        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = create_backend(self.cfg)
        return self._backend

    # ------------------------------------------------------------------ servizio
    def daemon_state(self) -> dict:
        info = self.db.get_setting("daemon") or {}
        hb = parse_iso(info.get("heartbeat"))
        age = (utcnow() - hb).total_seconds() if hb else None
        pid = info.get("pid")
        pid_alive = False
        if pid:
            try:
                os.kill(int(pid), 0)
                pid_alive = True
            except (OSError, ValueError):
                pid_alive = False
        alive = bool(pid_alive and age is not None
                     and age <= float(self.cfg.get("service.heartbeat_stale_seconds")))
        return {"alive": alive, "pid": pid if pid_alive else None,
                "heartbeat_age_seconds": round(age, 1) if age is not None else None,
                "started_at": info.get("started_at"), "version": info.get("version")}

    def require_daemon(self) -> None:
        if not self.daemon_state()["alive"]:
            raise ServiceUnavailable(
                "Il servizio in background non è attivo",
                hint="Avvialo con 'systemctl --user start gweb' (installazione: "
                     "'gweb service install'); per prove: 'gweb service run' in un "
                     "altro terminale.")

    def service_status(self) -> dict:
        from . import systemd

        unit = systemd.unit_state(self.cfg.get("service.unit_name"))
        active = self.store.active()
        return {"daemon": self.daemon_state(), "systemd": unit,
                "active_recording": active.to_dict() if active else None,
                "jobs": self.queue.counts(),
                "keepalive": self.db.get_setting("notebooklm.keepalive"),
                "disk_free_mb": round(free_mb(self.cfg.data_dir))}

    # ------------------------------------------------------------------ config
    def config_show(self) -> dict:
        c = self.cfg
        return {
            "config_file": str(c.source_path), "config_file_exists": c.loaded_from_file,
            "effective": c.to_dict(),
            "overrides": {"audio.device": self.db.get_setting("audio.device")},
            "paths": {"database": str(c.db_path), "recordings": str(c.recordings_dir),
                      "summaries": str(c.summaries_dir), "logs": str(c.log_dir),
                      "notebooklm_storage": str(c.storage_state_path)},
        }

    # ------------------------------------------------------------------ audio
    def audio_devices(self) -> dict:
        devices = list_capture_devices()
        spec = effective_device_spec(self.cfg, self.db)
        selected, error = None, None
        try:
            selected, _ = resolve_device(spec)
        except DeviceError as exc:
            error = exc.message
        return {"configured": spec, "resolved": selected, "error": error,
                "devices": [d.to_dict() | {"selected": d.alsa_pcm == selected} for d in devices]}

    def audio_select(self, spec: str | None) -> dict:
        if spec is None or spec == "auto":
            self.db.delete_setting("audio.device")
            spec = self.cfg.get("audio.device")
        else:
            resolve_device(spec)  # valida subito
            self.db.set_setting("audio.device", spec)
        pcm, _ = resolve_device(spec)
        return {"device": spec, "resolved": pcm}

    def audio_test(self, duration: int) -> dict:
        if not 1 <= duration <= 120:
            raise UsageError("La durata deve essere tra 1 e 120 secondi")
        active = self.store.active()
        if active:
            raise Conflict(f"Il microfono è in uso dalla registrazione {active.id}",
                           hint="Ferma la registrazione prima del test")
        a = self.cfg.section("audio")
        pcm, _ = resolve_device(effective_device_spec(self.cfg, self.db))
        out = self.cfg.tests_dir / f"test-{utcnow().astimezone().strftime('%Y%m%d-%H%M%S')}.wav"
        argv = build_capture_argv(a["capture_command"], pcm, sample_format=a["sample_format"],
                                  channels=int(a["channels"]), rate=int(a["sample_rate"]),
                                  segment_seconds=None, output=out, duration=duration)
        res = run_capture_once(argv, timeout=duration + 20)
        if res.returncode != 0 or not out.exists():
            raise DeviceError(f"Test di registrazione fallito (codice {res.returncode}): "
                              f"{res.stderr.strip()[-400:]}",
                              hint="Verifica il dispositivo con 'gweb audio devices'; "
                                   "'Device or resource busy' = microfono in uso.")
        levels = analyze_levels(out)
        return {"file": str(out), "device": pcm, "bytes": out.stat().st_size,
                "sample_rate": a["sample_rate"], "channels": a["channels"],
                **levels.to_dict()}

    # ------------------------------------------------------------------ NotebookLM
    def auth_local(self) -> dict:
        return self.backend.local_auth_state().to_dict()

    def import_credentials(self, data: bytes, *, master_token: bool = False) -> dict:
        """Salva lo stato di autenticazione ricevuto dal Mac con permessi 0600.

        Il contenuto non viene mai stampato né registrato nei log.
        """
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UsageError("Il file ricevuto non è JSON valido") from exc
        if not master_token and not (isinstance(parsed, dict)
                                     and isinstance(parsed.get("cookies"), list)):
            raise UsageError("Non sembra un storage_state.json (manca l'elenco 'cookies')",
                             hint="Usa il file creato da 'notebooklm login' sul Mac")
        target = self.cfg.master_token_path if master_token else self.cfg.storage_state_path
        secure_mkdir(self.cfg.credentials_dir)
        secure_mkdir(self.cfg.notebooklm_home)
        secure_mkdir(target.parent)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".import-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        # Sblocca subito i lavori in attesa di login.
        self.db.execute("UPDATE jobs SET next_attempt_at=NULL WHERE state=?",
                        (JobState.BLOCKED_AUTH,))
        result = {"saved_to": str(target), "mode": "0600"}
        if not master_token:
            result["cookies"] = len(parsed["cookies"])
        return result

    def notebook_status(self, *, remote: bool = True) -> dict:
        local = self.auth_local()
        out = {"backend": self.backend.name, "local": local, "remote": None,
               "keepalive": self.db.get_setting("notebooklm.keepalive"),
               "default_notebook": self.db.get_setting("notebook.default")}
        if not remote:
            return out
        if not local["exists"]:
            raise AuthRequired("Nessuna sessione NotebookLM su questo dispositivo",
                               details=out)

        async def check(s):
            nbs = await s.list_notebooks()  # sola lettura
            return {"ok": True, "notebooks": len(nbs), "account": await s.account_email()}

        out["remote"] = self.backend.run(check)
        return out

    def notebook_list(self) -> list[dict]:
        async def fn(s):
            return await s.list_notebooks()

        default = (self.db.get_setting("notebook.default") or {}).get("id")
        return [n.to_dict() | {"default": n.id == default} for n in self.backend.run(fn)]

    def resolve_notebook(self, spec: str | None, *, allow_offline: bool = False
                         ) -> NotebookInfo | None:
        """ID completo, prefisso dell'ID o parte del titolo. ``None`` -> notebook predefinito."""
        if spec is None:
            d = self.db.get_setting("notebook.default")
            return NotebookInfo(id=d["id"], title=d.get("title")) if d else None
        try:
            notebooks = self.backend.run(lambda s: s.list_notebooks())
        except RemoteError:
            if allow_offline and _looks_like_full_id(spec):
                return NotebookInfo(id=spec, title=None)
            raise
        exact = [n for n in notebooks if n.id == spec]
        if exact:
            return exact[0]
        cand = [n for n in notebooks if n.id.startswith(spec)] or \
               [n for n in notebooks if spec.lower() in n.title.lower()]
        if len(cand) == 1:
            return cand[0]
        if not cand:
            raise NotFound(f"Notebook '{spec}' non trovato", hint="gweb notebook list")
        raise UsageError(f"'{spec}' corrisponde a {len(cand)} notebook",
                         details={"matches": [n.to_dict() for n in cand]},
                         hint="Usa l'ID completo (gweb notebook list)")

    def notebook_select(self, spec: str) -> dict:
        nb = self.resolve_notebook(spec)
        value = {"id": nb.id, "title": nb.title, "selected_at": iso()}
        self.db.set_setting("notebook.default", value)
        return value

    def notebook_current(self) -> dict | None:
        return self.db.get_setting("notebook.default")

    def notebook_sources(self, spec: str | None) -> dict:
        nb = self._require_notebook(spec)
        sources = self.backend.run(lambda s: s.list_sources(nb.id))
        return {"notebook": nb.to_dict(), "sources": [x.to_dict() for x in sources]}

    def _require_notebook(self, spec: str | None) -> NotebookInfo:
        nb = self.resolve_notebook(spec)
        if nb is None:
            raise UsageError("Nessun notebook indicato e nessun notebook predefinito",
                             hint="Usa --notebook ID oppure 'gweb notebook select'")
        return nb

    # ------------------------------------------------------------------ registrazione
    def record_start(self, title: str, notebook: str | None, *, wait: bool = True) -> dict:
        title = title.strip()
        if not title:
            raise UsageError("Il titolo non può essere vuoto")
        self.require_daemon()
        min_free = float(self.cfg.get("recording.min_free_mb"))
        if free_mb(self.cfg.data_dir) < min_free:
            raise StorageError(f"Spazio libero insufficiente (< {min_free:.0f} MB)",
                               hint="Libera spazio prima di registrare")
        warnings = []
        nb = self.resolve_notebook(notebook, allow_offline=True)
        if nb is None:
            warnings.append("Nessun notebook associato: andrà indicato al caricamento")
        elif nb.title is None:
            warnings.append("NotebookLM non raggiungibile: notebook associato solo per ID")
        rec = self.store.create_mic_request(title, nb.id if nb else None,
                                            nb.title if nb else None)
        if wait:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                rec = self.store.get(rec.id)
                if rec.state != RecState.REQUESTED:
                    break
                time.sleep(0.3)
            if rec.state == RecState.FAILED:
                raise DeviceError(f"Registrazione non avviata: {rec.error}",
                                  details={"recording": rec.to_dict()})
            if rec.state == RecState.REQUESTED:
                warnings.append("Il servizio non ha ancora preso in carico la richiesta")
        return {"recording": rec.to_dict(), "warnings": warnings}

    def record_status(self) -> dict:
        rec = self.store.active()
        daemon = self.daemon_state()
        if rec is None:
            last = self.store.list(limit=1)
            return {"active": False, "daemon": daemon,
                    "last": last[0].to_dict() if last else None}
        started = parse_iso(rec.started_at)
        return {
            "active": True, "daemon": daemon, "recording": rec.to_dict(),
            "elapsed_seconds": round((utcnow() - started).total_seconds()) if started else 0,
            "recorded_seconds": round(recorded_seconds(self.store, rec)),
            "segments": len(list_segment_files(self.store.segments_dir(rec))),
            "disk_free_mb": round(free_mb(self.cfg.data_dir)),
            "events": self.store.events(rec.id, limit=5),
        }

    def record_stop(self, *, wait: bool = True, wait_final: bool = False) -> dict:
        rec = self.store.active()
        if rec is None:
            raise NotFound("Nessuna registrazione attiva")
        self.store.update(rec.id, stop_requested=1)
        if not self.daemon_state()["alive"]:
            return {"recording": self.store.get(rec.id).to_dict(), "warnings": [
                "Servizio non attivo: l'arresto e la finalizzazione avverranno al suo avvio "
                "(systemctl --user start gweb)"]}
        if wait:
            terminal = (RecState.READY, RecState.FAILED) if wait_final else \
                (RecState.FINALIZING, RecState.READY, RecState.FAILED)
            deadline = time.monotonic() + (1800 if wait_final else 30)
            while time.monotonic() < deadline:
                rec = self.store.get(rec.id)
                if rec.state in terminal:
                    break
                time.sleep(0.5)
        return {"recording": self.store.get(rec.id).to_dict(), "warnings": []}

    # ------------------------------------------------------------------ archivio
    def recordings_list(self, limit: int = 50) -> list[dict]:
        return [r.to_dict() for r in self.store.list(limit=limit)]

    def recordings_show(self, rid: str) -> dict:
        rec = self.store.get(rid)
        segments = [{"file": p.name, "bytes": p.stat().st_size}
                    for p in list_segment_files(self.store.segments_dir(rec))]
        summaries = [dict(r) for r in self.db.query(
            "SELECT id, created_at, file_path FROM summaries WHERE recording_id=? ORDER BY id",
            (rec.id,))]
        return {"recording": rec.to_dict(), "segments": segments,
                "jobs": [j.to_dict() for j in self.queue.list(recording_id=rec.id)],
                "summaries": summaries, "events": self.store.events(rec.id)}

    def recordings_import(self, path: str, title: str, notebook: str | None) -> dict:
        nb = self.resolve_notebook(notebook, allow_offline=True)
        rec = self.store.import_file(Path(path), title.strip() or Path(path).stem,
                                     nb.id if nb else None, nb.title if nb else None)
        rec = finalize_recording(self.cfg, self.store, rec.id)
        if rec.state == RecState.FAILED:
            raise AgencyError(f"Import non riuscito: {rec.error}")
        return {"recording": rec.to_dict()}

    def recordings_finalize(self, rid: str) -> dict:
        rec = self.store.get(rid)
        if rec.state in RecState.ACTIVE:
            raise Conflict("La registrazione è ancora attiva")
        if rec.purged_at:
            raise Conflict("I segmenti audio sono stati eliminati dalla pulizia")
        rec = finalize_recording(self.cfg, self.store, rec.id)
        return {"recording": rec.to_dict()}

    def recordings_upload(self, rid: str, notebook: str | None, *, wait: bool = False) -> dict:
        rec = self.store.get(rid)
        warnings: list[str] = []
        if notebook:
            # Offline è ammesso un ID completo: la coda attenderà la rete.
            nb = self.resolve_notebook(notebook, allow_offline=True)
        elif rec.notebook_id:
            nb = NotebookInfo(id=rec.notebook_id, title=rec.notebook_title)
        else:
            nb = self.resolve_notebook(None)
            if nb is None:
                raise UsageError("La registrazione non ha un notebook associato",
                                 hint="gweb recordings upload ID --notebook NOTEBOOK_ID")
            warnings.append(f"Uso il notebook predefinito: {nb.title or nb.id}")
        if rec.notebook_id != nb.id:
            if rec.upload_status in (UploadStatus.NOT_UPLOADED, UploadStatus.FAILED,
                                     UploadStatus.CANCELLED) or not rec.notebook_id:
                self.store.set_notebook(rec.id, nb.id, nb.title)
                rec = self.store.get(rec.id)
            else:
                warnings.append("Caricamento in un notebook diverso da quello associato: "
                                "l'associazione della registrazione resta invariata")
        job = self.queue.enqueue(rec, nb.id)
        if not self.daemon_state()["alive"]:
            warnings.append("Servizio non attivo: il caricamento partirà al suo avvio "
                            "(oppure esegui 'gweb jobs run')")
        elif wait:
            deadline = time.monotonic() + float(self.cfg.get("upload.request_timeout_seconds"))
            while time.monotonic() < deadline:
                job = self.queue.get(job.id)
                if job.state not in (JobState.PENDING, JobState.RUNNING):
                    break
                time.sleep(1)
        return {"job": self.queue.get(job.id).to_dict(), "warnings": warnings}

    # ------------------------------------------------------------------ pulizia
    def recordings_prune(self, *, execute: bool, min_age_days: float | None = None,
                         mode: str | None = None) -> dict:
        """Elimina l'audio locale SOLO dopo aver verificato su NotebookLM che la fonte è pronta.

        Senza ``execute`` è una simulazione (nessun file toccato). Errori di rete o di
        sessione interrompono tutto prima di qualsiasi eliminazione.
        """
        from . import cleanup

        mode = mode or self.cfg.get("cleanup.mode")
        if mode not in ("all", "segments"):
            raise UsageError("mode deve essere 'all' o 'segments'")
        age = float(self.cfg.get("cleanup.min_age_days") if min_age_days is None
                    else min_age_days)
        items = cleanup.plan(self, mode=mode, min_age_days=age)
        if any(i.action == "delete" for i in items):
            self.backend.run(lambda s: cleanup.verify_remote(s, items))
        if execute:
            cleanup.execute(self, items, mode)
        deleted = [i for i in items if i.action == "delete"]
        return {"dry_run": not execute, "mode": mode, "min_age_days": age,
                "deleted" if execute else "would_delete": [i.to_dict() for i in deleted],
                "skipped": [i.to_dict() for i in items if i.action != "delete"],
                "freed_bytes" if execute else "would_free_bytes": sum(i.bytes for i in deleted),
                "disk_free_mb": round(free_mb(self.cfg.data_dir))}

    # ------------------------------------------------------------------ coda
    def jobs_run(self, max_steps: int = 50) -> list[dict]:
        """Esegue la coda in primo piano (diagnostica / servizio fermo)."""
        if self.daemon_state()["alive"]:
            raise Conflict("Il servizio è attivo e sta già elaborando la coda")
        self.queue.recover_after_restart()
        worker = UploadWorker(self.cfg, self.queue, self.backend)
        done = []
        for _ in range(max_steps):
            job = worker.run_once()
            if job is None:
                break
            done.append(job.to_dict())
        return done

    # ------------------------------------------------------------------ domande
    def ask(self, question: str, notebook: str | None, *, recording: str | None = None,
            sources: list[str] | None = None) -> dict:
        if not question.strip():
            raise UsageError("La domanda è vuota")
        source_ids = list(sources or [])
        if recording:
            rec = self.store.get(recording)
            if not rec.remote_source_id or rec.upload_status != UploadStatus.READY:
                raise NotReady(f"La fonte della registrazione {rec.id} non è pronta "
                               f"(stato: {rec.upload_status})", hint="gweb jobs list")
            source_ids.append(rec.remote_source_id)
            notebook = notebook or rec.notebook_id
        nb = self._require_notebook(notebook)
        answer = self.backend.run(lambda s: s.ask(nb.id, question, source_ids or None))
        return {"notebook": nb.to_dict(), "question": question, "source_ids": source_ids,
                **answer.to_dict()}

    def briefing_summarize(self, rid: str, *, compare: str = "auto") -> dict:
        rec = self.store.get(rid)
        if not rec.notebook_id:
            raise NotReady("La registrazione non è associata a un notebook")
        job = self.queue.latest_for(rec.id, rec.notebook_id)
        if job is None or not job.remote_source_id:
            raise NotReady(f"La registrazione {rec.id} non è stata caricata "
                           f"(stato: {rec.upload_status})",
                           hint=f"gweb recordings upload {rec.id}")

        async def fn(s):
            if job.state != JobState.SUCCEEDED:
                src = await s.get_source(rec.notebook_id, job.remote_source_id)
                if src is None or src.status != SourceState.READY:
                    raise NotReady(
                        "Upload completato ma la fonte non è ancora pronta "
                        f"(stato remoto: {src.status if src else 'assente'})",
                        hint="Riprova tra qualche minuto; stato: gweb jobs list")
                self.queue._set(job.id, state=JobState.SUCCEEDED, remote_status=src.status,
                                finished_at=iso())
            return await run_summary(s, notebook_id=rec.notebook_id,
                                     source_id=job.remote_source_id, title=rec.title,
                                     compare=compare)

        result = self.backend.run(fn)
        rec = self.store.get(rec.id)
        sid, path = save_summary(self.db, self.cfg.summaries_dir, rec, rec.notebook_title, result)
        return {"summary_id": sid, "file": str(path), "recording_id": rec.id, **result}

    # ------------------------------------------------------------------ diagnostica
    def doctor(self) -> list[dict]:
        checks: list[dict] = []

        def add(name, status, detail="", hint=None):
            checks.append({"check": name, "status": status, "detail": detail, "hint": hint})

        model = Path("/proc/device-tree/model")
        add("hardware", "ok", (model.read_text(errors="ignore").strip("\x00\n")
                               if model.exists() else "non Raspberry Pi / modello ignoto")
            + f" · {platform.machine()} · userland {platform.architecture()[0]}")
        osr = Path("/etc/os-release")
        pretty = next((ln.split("=", 1)[1].strip('"') for ln in
                       (osr.read_text().splitlines() if osr.exists() else [])
                       if ln.startswith("PRETTY_NAME=")), platform.platform())
        add("sistema", "ok", pretty)
        try:
            mem_kb = next(int(ln.split()[1]) for ln in Path("/proc/meminfo").read_text()
                          .splitlines() if ln.startswith("MemTotal"))
            add("ram", "ok", f"{mem_kb // 1024} MB")
        except (OSError, StopIteration):
            pass
        add("python", "ok" if sys.version_info >= (3, 10) else "fail",
            f"{platform.python_version()} ({sys.executable})")
        add("config", "ok" if self.cfg.loaded_from_file else "warn",
            str(self.cfg.source_path) + ("" if self.cfg.loaded_from_file else " (assente: "
                                         "default)"),
            None if self.cfg.loaded_from_file else "gweb config init")
        add("database", "ok", str(self.cfg.db_path))
        free = free_mb(self.cfg.data_dir)
        add("spazio", "ok" if free >= self.cfg.get("recording.min_free_mb") else "fail",
            f"{free:.0f} MB liberi in {self.cfg.data_dir}",
            None if free >= self.cfg.get("recording.min_free_mb") else "Libera spazio")
        cap = self.cfg.get("audio.capture_command").split()[0]
        add("arecord", "ok" if shutil.which(cap) else "fail", shutil.which(cap) or cap,
            None if shutil.which(cap) else "sudo apt install alsa-utils")
        ff = self.cfg.get("encoding.ffmpeg")
        add("ffmpeg", "ok" if ffmpeg_available(ff) else "fail", shutil.which(ff) or ff,
            None if ffmpeg_available(ff) else "sudo apt install ffmpeg")
        try:
            pcm, dev = resolve_device(effective_device_spec(self.cfg, self.db))
            add("microfono", "ok", f"{pcm}" + (f" ({dev.name})" if dev else ""))
        except DeviceError as exc:
            add("microfono", "fail", exc.message, exc.hint)
        snd = Path("/dev/snd")
        if snd.exists():
            ok = all(os.access(p, os.R_OK | os.W_OK) for p in snd.glob("pcmC*c"))
            add("permessi audio", "ok" if ok else "fail", "/dev/snd",
                None if ok else "sudo usermod -aG audio $USER (poi nuovo login)")
        try:
            from importlib.metadata import version

            add("notebooklm-py", "ok", version("notebooklm-py"))
        except Exception:  # noqa: BLE001
            add("notebooklm-py", "fail", "non installato", "pip install -e .")
        local = self.auth_local()
        if not local["exists"]:
            add("sessione NotebookLM", "warn", "assente", "gweb notebook login")
        elif local.get("secure_permissions") is False:
            add("sessione NotebookLM", "fail", f"permessi {local['mode']}",
                f"chmod 600 {local['storage_path']}")
        else:
            add("sessione NotebookLM", "ok", f"file presente ({local['storage_path']})")
        from . import systemd

        unit = systemd.unit_state(self.cfg.get("service.unit_name"))
        if not unit["systemd_user"]:
            add("systemd utente", "warn", "non disponibile in questa sessione")
        else:
            add("servizio", "ok" if unit["active"] == "active" else "warn",
                f"installato={unit['installed']} attivo={unit['active']} "
                f"abilitato={unit['enabled']}",
                None if unit["active"] == "active" else "gweb service install")
        if unit["linger"] is False:
            add("linger", "warn", "il servizio non parte all'avvio senza login",
                "sudo loginctl enable-linger $USER")
        d = self.daemon_state()
        add("demone", "ok" if d["alive"] else "warn",
            f"pid {d['pid']}, heartbeat {d['heartbeat_age_seconds']}s" if d["alive"]
            else "non in esecuzione")
        return checks

__all__ = ["AgencyApp", "Recording", "__version__"]
