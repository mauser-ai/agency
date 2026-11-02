"""Coda persistente dei caricamenti e worker.

Macchina a stati di un lavoro (tabella ``jobs``)::

    pending ──► running ──► processing ──► succeeded   (fonte PRONTA)
       ▲           │  │            │
       │           │  └─► uncertain ──(riconciliazione: assente x2)──► pending
       │           │          └──────(riconciliazione: presente)────► processing
       │           ├─► blocked_auth ──(sessione di nuovo valida)──► pending/processing
       └─(retry con attesa progressiva)── errore temporaneo
                   └─► failed (errore permanente o tentativi esauriti)

Regole anti-duplicato:
1. Indice unico (registrazione, notebook) sui lavori non falliti/annullati.
2. Prima di OGNI invio si elencano le fonti del notebook e si cerca il marcatore
   ``[ID]`` nel titolo (o il nome file ``ID.ext``): se presente non si reinvia.
3. Un errore *durante* il trasferimento (timeout, rete, esito ``unconfirmed``) porta a
   ``uncertain``: non si reinvia finché la riconciliazione non conferma l'assenza della
   fonte per due volte a distanza di ``reconcile_settle_seconds``.
4. Un lavoro trovato ``running`` all'avvio del demone (crash/riavvio) diventa ``uncertain``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
from datetime import timedelta
from pathlib import Path

from .config import Config
from .db import Database, iso, parse_iso, utcnow
from .errors import (
    AuthRequired,
    Conflict,
    NotFound,
    NotReady,
    PermanentRemoteError,
    RateLimited,
    RemoteError,
    RemoteNotFound,
    TransientRemoteError,
    UncertainUpload,
    UsageError,
)
from .models import JOB_TO_UPLOAD_STATUS, Job, JobState, Recording, RecState
from .notebook.base import NotebookBackend, SourceState, source_matches_recording
from .recordings import RecordingStore

log = logging.getLogger(__name__)


def render_remote_title(cfg: Config, rec: Recording) -> str:
    created = parse_iso(rec.started_at or rec.created_at)
    date = created.astimezone().strftime("%Y-%m-%d %H:%M") if created else ""
    title = cfg.get("upload.title_template").format(title=rec.title, date=date, id=rec.id)
    if f"[{rec.id}]" not in title:  # il marcatore è indispensabile per la riconciliazione
        title = f"{title} [{rec.id}]"
    return title


class JobQueue:
    def __init__(self, cfg: Config, db: Database, store: RecordingStore):
        self.cfg = cfg
        self.db = db
        self.store = store

    # --- interrogazione --------------------------------------------------------
    def get(self, job_id: int) -> Job:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row is None:
            raise NotFound(f"Lavoro {job_id} non trovato", hint="Elenco: agency jobs list")
        return Job.from_row(row)

    def list(self, *, include_done: bool = True, limit: int = 50,
             recording_id: str | None = None) -> list[Job]:
        where, params = [], []
        if not include_done:
            where.append("state IN ({})".format(",".join("?" * len(JobState.OPEN))))
            params += list(JobState.OPEN)
        if recording_id:
            where.append("recording_id=?")
            params.append(recording_id)
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY id DESC LIMIT ?"
        return [Job.from_row(r) for r in self.db.query(sql, tuple(params) + (limit,))]

    def latest_for(self, recording_id: str, notebook_id: str | None = None) -> Job | None:
        sql = "SELECT * FROM jobs WHERE recording_id=?"
        params: tuple = (recording_id,)
        if notebook_id:
            sql += " AND notebook_id=?"
            params += (notebook_id,)
        row = self.db.one(sql + " ORDER BY id DESC LIMIT 1", params)
        return Job.from_row(row) if row else None

    def counts(self) -> dict[str, int]:
        rows = self.db.query("SELECT state, COUNT(*) n FROM jobs GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    # --- comandi ---------------------------------------------------------------
    def enqueue(self, rec: Recording, notebook_id: str) -> Job:
        if rec.state != RecState.READY or not rec.file_path:
            raise NotReady(f"La registrazione {rec.id} non è pronta (stato: {rec.state})",
                           hint="Attendi la fine della finalizzazione: agency recordings show "
                                + rec.id)
        if not Path(rec.file_path).is_file():
            if rec.purged_scope == "all":
                raise Conflict(f"L'audio di {rec.id} è stato eliminato dalla pulizia il "
                               f"{rec.purged_at[:10]}: non è più caricabile",
                               hint="La fonte resta disponibile nel notebook originale.")
            raise NotFound(f"File audio mancante: {rec.file_path}")
        now = iso()
        try:
            cur = self.db.execute(
                "INSERT INTO jobs(kind, recording_id, notebook_id, state, phase, max_attempts, "
                "remote_title, created_at, updated_at) VALUES('upload',?,?,?,?,?,?,?,?)",
                (rec.id, notebook_id, JobState.PENDING, "upload",
                 int(self.cfg.get("upload.max_attempts")), render_remote_title(self.cfg, rec),
                 now, now))
        except sqlite3.IntegrityError as exc:
            existing = self.db.one(
                "SELECT * FROM jobs WHERE recording_id=? AND notebook_id=? "
                "AND state NOT IN ('failed','cancelled')", (rec.id, notebook_id))
            job = Job.from_row(existing) if existing else None
            if job and job.state == JobState.SUCCEEDED:
                msg = f"Registrazione già caricata in questo notebook (lavoro {job.id})"
                hint = "Nessun nuovo upload: eviterebbe un duplicato."
            else:
                msg = f"Upload già in coda per questo notebook (lavoro {job.id if job else '?'})"
                hint = "Controlla con 'agency jobs list'; per forzare subito: agency jobs retry ID"
            raise Conflict(msg, hint=hint, details={"job": job.to_dict() if job else None}) \
                from exc
        job_id = int(cur.lastrowid)
        self.db.add_event("job_created", f"Upload verso {notebook_id}", recording_id=rec.id,
                          job_id=job_id)
        self._sync_recording(job_id)
        return self.get(job_id)

    def retry(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job.state in (JobState.SUCCEEDED, JobState.CANCELLED):
            raise Conflict(f"Il lavoro {job_id} è '{job.state}': non ritentabile")
        if job.state == JobState.RUNNING:
            raise Conflict(f"Il lavoro {job_id} è in esecuzione")
        if job.state == JobState.FAILED:
            # Riattivare un lavoro fallito può violare l'unicità se ne esiste un altro vivo.
            other = self.db.one(
                "SELECT id FROM jobs WHERE recording_id=? AND notebook_id=? AND id<>? "
                "AND state NOT IN ('failed','cancelled')", (job.recording_id, job.notebook_id,
                                                            job_id))
            if other:
                raise Conflict(f"Esiste già il lavoro {other['id']} per la stessa coppia")
        if job.remote_source_id:
            new_state, phase = JobState.PROCESSING, "processing"
        elif job.state == JobState.UNCERTAIN:
            new_state, phase = JobState.UNCERTAIN, "upload"  # riconcilia subito, non reinviare
        else:
            new_state, phase = JobState.PENDING, "upload"
        self._set(job_id, state=new_state, phase=phase, next_attempt_at=None, attempts=0
                  if job.state == JobState.FAILED else job.attempts,
                  processing_error_since=None, finished_at=None)
        self.db.add_event("job_retry", "Retry manuale", job_id=job_id,
                          recording_id=job.recording_id)
        return self.get(job_id)

    def cancel(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job.state in (JobState.SUCCEEDED, JobState.CANCELLED, JobState.RUNNING):
            raise Conflict(f"Impossibile annullare un lavoro '{job.state}'")
        self._set(job_id, state=JobState.CANCELLED, finished_at=iso())
        return self.get(job_id)

    def resolve(self, job_id: int, *, source_id: str | None, not_uploaded: bool) -> Job:
        """Risoluzione manuale di un esito incerto, dopo verifica nell'interfaccia web."""
        job = self.get(job_id)
        if job.state not in (JobState.UNCERTAIN, JobState.FAILED, JobState.BLOCKED_AUTH,
                             JobState.PENDING):
            raise Conflict(f"Il lavoro {job_id} è '{job.state}': niente da risolvere")
        if bool(source_id) == bool(not_uploaded):
            raise UsageError("Indica --source-id ID oppure --not-uploaded")
        if source_id:
            self._set(job_id, state=JobState.PROCESSING, phase="processing",
                      remote_source_id=source_id, next_attempt_at=None, uploaded_at=iso())
        else:
            self._set(job_id, state=JobState.PENDING, phase="upload", reconcile_misses=0,
                      next_attempt_at=None, attempts=0)
        self.db.add_event("job_resolved", f"Risolto manualmente (source_id={source_id})",
                          job_id=job_id, recording_id=job.recording_id)
        return self.get(job_id)

    # --- uso interno del worker ----------------------------------------------
    def _set(self, job_id: int, **fields) -> None:
        fields["updated_at"] = iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", tuple(fields.values()) + (job_id,))
        self._sync_recording(job_id)

    def _sync_recording(self, job_id: int) -> None:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row is None:
            return
        job = Job.from_row(row)
        rec = self.store.get(job.recording_id)
        # Lo stato di upload della registrazione riflette il notebook associato.
        if rec.notebook_id and rec.notebook_id != job.notebook_id:
            return
        self.store.update(rec.id, upload_status=JOB_TO_UPLOAD_STATUS[job.state],
                          remote_source_id=job.remote_source_id,
                          notebook_id=rec.notebook_id or job.notebook_id)

    def recover_after_restart(self) -> int:
        """Lavori 'running' interrotti da crash/riavvio: mai reinviare alla cieca."""
        n = 0
        for row in self.db.query("SELECT * FROM jobs WHERE state=?", (JobState.RUNNING,)):
            job = Job.from_row(row)
            if job.phase == "processing" or job.remote_source_id:
                self._set(job.id, state=JobState.PROCESSING, next_attempt_at=None)
            else:
                self._set(job.id, state=JobState.UNCERTAIN, error_code="INTERRUPTED",
                          last_error="Servizio interrotto durante l'upload: verifica remota",
                          next_attempt_at=None)
            n += 1
        return n

    def claim_due(self) -> Job | None:
        now = iso()
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE state IN (?,?,?,?) AND "
                "(next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY id LIMIT 1",
                (JobState.PENDING, JobState.PROCESSING, JobState.UNCERTAIN,
                 JobState.BLOCKED_AUTH, now)).fetchone()
            if row is None:
                return None
            job = Job.from_row(row)
            conn.execute("UPDATE jobs SET state=?, updated_at=? WHERE id=?",
                         (JobState.RUNNING, now, job.id))
        self._sync_recording(job.id)
        return job

    def backoff_seconds(self, attempts: int) -> float:
        base = float(self.cfg.get("upload.backoff_base_seconds"))
        cap = float(self.cfg.get("upload.backoff_max_seconds"))
        delay = min(cap, base * (2 ** max(0, attempts - 1)))
        return delay * (1 + random.uniform(0, 0.1))


def _later(seconds: float) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))


class UploadWorker:
    """Esegue UN passo di un lavoro alla volta (chiamato in loop dal demone)."""

    def __init__(self, cfg: Config, queue: JobQueue, backend: NotebookBackend):
        self.cfg = cfg
        self.q = queue
        self.backend = backend

    def run_once(self) -> Job | None:
        job = self.q.claim_due()
        if job is None:
            return None
        try:
            asyncio.run(self._step(job))
        except Exception as exc:  # noqa: BLE001 - un lavoro non deve fermare il demone
            log.exception("Errore inatteso nel lavoro %s", job.id)
            self._on_error(job, exc)
        return self.q.get(job.id)

    async def _step(self, job: Job) -> None:
        rec = self.q.store.get(job.recording_id)
        async with self.backend.session() as s:
            if job.phase == "processing" and job.remote_source_id:
                await self._check_processing(s, job)
                return
            await self._upload(s, job, rec)

    async def _find_existing(self, s, job: Job, rec: Recording):
        sources = await s.list_sources(job.notebook_id)
        filename = Path(rec.file_path).name if rec.file_path else None
        return [x for x in sources if source_matches_recording(x, rec.id, filename)]

    async def _upload(self, s, job: Job, rec: Recording) -> None:
        q = self.q
        # 1. Riconciliazione (sempre, prima di qualsiasi invio)
        matches = await self._find_existing(s, job, rec)
        if matches:
            src = matches[0]
            if len(matches) > 1:
                q.db.add_event("duplicate_remote",
                               f"{len(matches)} fonti con lo stesso marcatore: uso {src.id}",
                               level="warning", job_id=job.id, recording_id=rec.id)
            q._set(job.id, state=JobState.PROCESSING, phase="processing",
                   remote_source_id=src.id, remote_status=src.status,
                   uploaded_at=job.uploaded_at or iso(), next_attempt_at=None,
                   error_code=None, last_error=None)
            q.db.add_event("reconciled", f"Fonte già presente: {src.id}", job_id=job.id,
                           recording_id=rec.id)
            return
        if job.state == JobState.UNCERTAIN or job.error_code in ("UPLOAD_UNCERTAIN",
                                                                  "INTERRUPTED"):
            if job.reconcile_misses < 1:
                settle = float(self.cfg.get("upload.reconcile_settle_seconds"))
                q._set(job.id, state=JobState.UNCERTAIN, reconcile_misses=job.reconcile_misses
                       + 1, next_attempt_at=_later(settle),
                       last_error="Fonte non trovata: nuova verifica prima di reinviare")
                return
            q.db.add_event("reconciled_absent", "Fonte assente in due verifiche: reinvio",
                           job_id=job.id, recording_id=rec.id)
        # 2. Invio
        if not rec.file_path or not Path(rec.file_path).is_file():
            raise PermanentRemoteError(f"File locale mancante: {rec.file_path}")
        attempts = job.attempts + 1
        q._set(job.id, attempts=attempts, state=JobState.RUNNING, phase="upload")
        q.db.add_event("upload_start", f"Tentativo {attempts}", job_id=job.id,
                       recording_id=rec.id)
        src = await s.upload_audio(job.notebook_id, Path(rec.file_path), title=job.remote_title,
                                   mime_type=rec.mime_type or "audio/mp4")
        # Upload COMPLETATO: la fonte non è ancora pronta.
        q._set(job.id, state=JobState.PROCESSING, phase="processing", remote_source_id=src.id,
               remote_status=src.status, uploaded_at=iso(), reconcile_misses=0,
               next_attempt_at=_later(min(10, self._poll())), error_code=None, last_error=None)
        q.db.add_event("uploaded", f"Upload completato, fonte {src.id} in elaborazione",
                       job_id=job.id, recording_id=rec.id)

    def _poll(self) -> float:
        return float(self.cfg.get("upload.processing_poll_seconds"))

    async def _check_processing(self, s, job: Job) -> None:
        q = self.q
        src = await s.get_source(job.notebook_id, job.remote_source_id)
        now = utcnow()
        if src is None:
            q._set(job.id, state=JobState.FAILED, error_code="SOURCE_MISSING",
                   last_error="La fonte non esiste più nel notebook (eliminata?)",
                   finished_at=iso())
            return
        if src.status == SourceState.READY:
            q._set(job.id, state=JobState.SUCCEEDED, remote_status=src.status,
                   finished_at=iso(), error_code=None, last_error=None)
            q.db.add_event("source_ready", f"Fonte {src.id} pronta", job_id=job.id,
                           recording_id=job.recording_id)
            return
        uploaded = parse_iso(job.uploaded_at) or now
        timeout = timedelta(minutes=float(self.cfg.get("upload.processing_timeout_minutes")))
        fields: dict = {"state": JobState.PROCESSING, "remote_status": src.status,
                        "next_attempt_at": _later(self._poll())}
        if src.status == SourceState.ERROR:
            # L'audio può riportare ERROR in modo transitorio durante la trascrizione.
            since = parse_iso(job.processing_error_since) or now
            fields["processing_error_since"] = iso(since)
            grace = timedelta(minutes=float(self.cfg.get("upload.processing_error_grace_minutes")))
            if now - since >= grace:
                q._set(job.id, state=JobState.FAILED, error_code="PROCESSING_FAILED",
                       remote_status=src.status, finished_at=iso(),
                       last_error="NotebookLM non è riuscito a elaborare l'audio")
                return
        else:
            fields["processing_error_since"] = None
        if now - uploaded > timeout:
            q._set(job.id, state=JobState.FAILED, error_code="PROCESSING_TIMEOUT",
                   remote_status=src.status, finished_at=iso(),
                   last_error="Elaborazione non conclusa entro il tempo massimo "
                              "(agency jobs retry ID per ricontrollare)")
            return
        q._set(job.id, **fields)

    def _on_error(self, job: Job, exc: BaseException) -> None:
        q = self.q
        current = q.get(job.id)
        msg = getattr(exc, "message", str(exc))[:500]
        if isinstance(exc, AuthRequired):
            # Non consuma tentativi; se l'esito era incerto lo resta (si riconcilia dopo).
            recheck = float(self.cfg.get("upload.auth_recheck_minutes")) * 60
            uncertain = job.state == JobState.UNCERTAIN or job.error_code in (
                "UPLOAD_UNCERTAIN", "INTERRUPTED")
            q._set(job.id, state=JobState.BLOCKED_AUTH,
                   error_code="UPLOAD_UNCERTAIN" if uncertain else exc.code,
                   last_error=msg, next_attempt_at=_later(recheck), attempts=job.attempts)
            return
        if isinstance(exc, UncertainUpload):
            fields = {"state": JobState.UNCERTAIN, "error_code": exc.code, "last_error": msg,
                      "reconcile_misses": 0,
                      "next_attempt_at": _later(float(self.cfg.get(
                          "upload.reconcile_settle_seconds")))}
            if exc.source_id:
                # La libreria ha comunicato l'ID: l'upload è registrato lato server.
                fields.update(state=JobState.PROCESSING, phase="processing",
                              remote_source_id=exc.source_id, uploaded_at=iso(),
                              next_attempt_at=_later(self._poll()))
            q._set(job.id, **fields)
            return
        if current.phase == "processing" and current.remote_source_id and isinstance(
                exc, (TransientRemoteError, RemoteError)) and not isinstance(
                exc, (PermanentRemoteError, RemoteNotFound)):
            delay = exc.retry_after if isinstance(exc, RateLimited) and exc.retry_after else \
                self._poll() * 2
            q._set(job.id, state=JobState.PROCESSING, error_code=getattr(exc, "code", "ERROR"),
                   last_error=msg, next_attempt_at=_later(delay))
            return
        if isinstance(exc, (PermanentRemoteError, RemoteNotFound)):
            q._set(job.id, state=JobState.FAILED, error_code=exc.code, last_error=msg,
                   finished_at=iso())
            return
        # Temporaneo (rete assente, 5xx, rate limit) o imprevisto: retry con attesa.
        was_uncertain = job.state == JobState.UNCERTAIN or current.error_code in (
            "UPLOAD_UNCERTAIN", "INTERRUPTED")
        if current.attempts >= current.max_attempts:
            q._set(job.id, state=JobState.FAILED, error_code=getattr(exc, "code", "ERROR"),
                   last_error=f"Tentativi esauriti: {msg}", finished_at=iso())
            return
        delay = q.backoff_seconds(max(1, current.attempts))
        if isinstance(exc, RateLimited) and exc.retry_after:
            delay = max(delay, float(exc.retry_after))
        q._set(job.id, state=JobState.UNCERTAIN if was_uncertain else JobState.PENDING,
               error_code=getattr(exc, "code", "ERROR"), last_error=msg,
               next_attempt_at=_later(delay))
