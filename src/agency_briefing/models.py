"""Stati e modelli di dominio condivisi da CLI, demone e futura API."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, fields
from typing import Any


class RecState:
    REQUESTED = "requested"      # la CLI ha chiesto l'avvio, il demone non l'ha ancora preso
    RECORDING = "recording"      # arecord in esecuzione (o in riconnessione)
    STOPPING = "stopping"        # arresto in corso
    FINALIZING = "finalizing"    # unione segmenti e codifica file da caricare
    READY = "ready"              # file finale pronto localmente
    FAILED = "failed"            # nessun audio utilizzabile
    ACTIVE = (REQUESTED, RECORDING, STOPPING)


class UploadStatus:
    NOT_UPLOADED = "not_uploaded"
    QUEUED = "queued"            # in coda / in attesa di retry
    UPLOADING = "uploading"
    PROCESSING = "processing"    # upload COMPLETATO, fonte non ancora pronta
    READY = "ready"              # fonte PRONTA per essere interrogata
    UNCERTAIN = "uncertain"      # esito incerto: serve riconciliazione
    BLOCKED_AUTH = "blocked_auth"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobState:
    PENDING = "pending"
    RUNNING = "running"
    PROCESSING = "processing"
    UNCERTAIN = "uncertain"
    BLOCKED_AUTH = "blocked_auth"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OPEN = (PENDING, RUNNING, PROCESSING, UNCERTAIN, BLOCKED_AUTH)


JOB_TO_UPLOAD_STATUS = {
    JobState.PENDING: UploadStatus.QUEUED,
    JobState.RUNNING: UploadStatus.UPLOADING,
    JobState.PROCESSING: UploadStatus.PROCESSING,
    JobState.UNCERTAIN: UploadStatus.UNCERTAIN,
    JobState.BLOCKED_AUTH: UploadStatus.BLOCKED_AUTH,
    JobState.SUCCEEDED: UploadStatus.READY,
    JobState.FAILED: UploadStatus.FAILED,
    JobState.CANCELLED: UploadStatus.CANCELLED,
}

UPLOAD_STATUS_LABEL = {
    UploadStatus.NOT_UPLOADED: "non caricata",
    UploadStatus.QUEUED: "in coda",
    UploadStatus.UPLOADING: "caricamento",
    UploadStatus.PROCESSING: "caricata, in elaborazione",
    UploadStatus.READY: "fonte pronta",
    UploadStatus.UNCERTAIN: "esito incerto",
    UploadStatus.BLOCKED_AUTH: "attende login",
    UploadStatus.FAILED: "errore",
    UploadStatus.CANCELLED: "annullata",
}

REC_STATE_LABEL = {
    RecState.REQUESTED: "avvio richiesto",
    RecState.RECORDING: "in registrazione",
    RecState.STOPPING: "arresto",
    RecState.FINALIZING: "finalizzazione",
    RecState.READY: "pronta",
    RecState.FAILED: "errore",
}


def _from_row(cls, row: sqlite3.Row | dict):
    keys = {f.name for f in fields(cls)}
    data = {k: row[k] for k in (row.keys() if hasattr(row, "keys") else row) if k in keys}
    return cls(**data)


@dataclass
class Recording:
    id: str
    title: str
    kind: str
    state: str
    created_at: str
    dir_path: str
    notebook_id: str | None = None
    notebook_title: str | None = None
    stop_requested: int = 0
    stop_reason: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    heartbeat_at: str | None = None
    duration_seconds: float | None = None
    file_path: str | None = None
    original_path: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    device: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    upload_status: str = UploadStatus.NOT_UPLOADED
    remote_source_id: str | None = None
    error: str | None = None
    purged_at: str | None = None
    purged_scope: str | None = None
    purged_bytes: int | None = None
    upload_on_ready: int = 0

    @classmethod
    def from_row(cls, row) -> Recording:
        return _from_row(cls, row)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Job:
    id: int
    kind: str
    recording_id: str
    notebook_id: str
    state: str
    phase: str
    attempts: int
    max_attempts: int
    reconcile_misses: int
    remote_title: str
    created_at: str
    updated_at: str
    next_attempt_at: str | None = None
    error_code: str | None = None
    last_error: str | None = None
    remote_source_id: str | None = None
    remote_status: str | None = None
    processing_error_since: str | None = None
    uploaded_at: str | None = None
    finished_at: str | None = None

    @classmethod
    def from_row(cls, row) -> Job:
        return _from_row(cls, row)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
