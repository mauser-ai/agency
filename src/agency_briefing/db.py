"""Persistenza SQLite: metadati delle registrazioni, segmenti, eventi, coda lavori, riepiloghi.

Scelte:
- WAL + ``synchronous=NORMAL``: robusto a interruzioni di corrente su SD con buone prestazioni.
- Una connessione per thread (sqlite3 non condivide connessioni tra thread).
- Vincoli di unicità *nel database* per impedire avvii concorrenti e upload duplicati,
  così la protezione vale anche per processi diversi (CLI, demone, futura API).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'mic',          -- mic | import
    notebook_id TEXT,
    notebook_title TEXT,
    state TEXT NOT NULL,                       -- vedi models.RecState
    stop_requested INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    heartbeat_at TEXT,
    duration_seconds REAL,
    dir_path TEXT NOT NULL,
    file_path TEXT,                            -- file finale da caricare
    original_path TEXT,                        -- per import: file sorgente
    file_size INTEGER,
    mime_type TEXT,
    device TEXT,
    sample_rate INTEGER,
    channels INTEGER,
    upload_status TEXT NOT NULL DEFAULT 'not_uploaded',
    remote_source_id TEXT,
    error TEXT
);

-- Un solo microfono: al massimo una registrazione attiva alla volta.
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_recording
    ON recordings(kind)
    WHERE kind = 'mic' AND state IN ('requested', 'recording', 'stopping');

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id TEXT NOT NULL REFERENCES recordings(id),
    part INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    exit_code INTEGER,
    UNIQUE(recording_id, part)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id TEXT,
    job_id INTEGER,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_recording ON events(recording_id);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'upload',
    recording_id TEXT NOT NULL REFERENCES recordings(id),
    notebook_id TEXT NOT NULL,
    state TEXT NOT NULL,                       -- vedi models.JobState
    phase TEXT NOT NULL DEFAULT 'upload',      -- upload | processing
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    reconcile_misses INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error_code TEXT,
    last_error TEXT,
    remote_title TEXT NOT NULL,
    remote_source_id TEXT,
    remote_status TEXT,
    processing_error_since TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    uploaded_at TEXT,
    finished_at TEXT
);

-- Prevenzione duplicati: un solo lavoro "vivo" o riuscito per coppia registrazione/notebook.
CREATE UNIQUE INDEX IF NOT EXISTS ux_job_recording_notebook
    ON jobs(recording_id, notebook_id)
    WHERE state NOT IN ('failed', 'cancelled');

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id TEXT NOT NULL REFERENCES recordings(id),
    notebook_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    content_json TEXT NOT NULL,
    file_path TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _init_schema(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
        elif int(row["value"]) > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database creato da una versione più recente (schema {row['value']})")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Transazione esplicita con lock in scrittura immediato."""
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connect().execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, params)

    # --- settings ------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None or row["value"] is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), iso()),
        )

    def delete_setting(self, key: str) -> None:
        self.execute("DELETE FROM settings WHERE key=?", (key,))

    def add_event(self, kind: str, message: str, *, level: str = "info",
                  recording_id: str | None = None, job_id: int | None = None) -> None:
        self.execute(
            "INSERT INTO events(recording_id, job_id, at, level, kind, message) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (recording_id, job_id, iso(), level, kind, message),
        )
