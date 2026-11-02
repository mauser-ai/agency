"""Gestione delle registrazioni: metadati (SQLite) e file (filesystem).

Struttura su disco per ogni registrazione::

    <data_dir>/recordings/<ID>/
        segments/part001.wav ...   # audio originale, mai cancellato automaticamente
        capture.log                # stderr di arecord
        <ID>.m4a                   # file finale caricato su NotebookLM
        import/<nome originale>    # solo per 'recordings import'
        metadata.json              # copia leggibile dei metadati (backup)
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import Config
from .db import Database, iso, utcnow
from .errors import Conflict, NotFound, StorageError, UsageError
from .models import Recording, RecState, UploadStatus


def new_recording_id(now: datetime | None = None) -> str:
    now = (now or utcnow()).astimezone()
    return now.strftime("r%y%m%d-%H%M%S-") + secrets.token_hex(2)


class RecordingStore:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db

    # --- lettura -------------------------------------------------------------
    def get(self, recording_id: str) -> Recording:
        row = self.db.one("SELECT * FROM recordings WHERE id=?", (recording_id,))
        if row is None:
            rows = self.db.query("SELECT * FROM recordings WHERE id LIKE ? ORDER BY created_at",
                                 (recording_id + "%",))
            if len(rows) == 1:
                row = rows[0]
            elif len(rows) > 1:
                raise UsageError(f"ID ambiguo '{recording_id}': {len(rows)} registrazioni",
                                 hint="Usa più caratteri dell'ID")
        if row is None:
            raise NotFound(f"Registrazione {recording_id} non trovata",
                           hint="Elenco: agency recordings list")
        return Recording.from_row(row)

    def list(self, limit: int = 50, state: str | None = None) -> list[Recording]:
        sql = "SELECT * FROM recordings"
        params: tuple = ()
        if state:
            sql += " WHERE state=?"
            params = (state,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        return [Recording.from_row(r) for r in self.db.query(sql, params + (limit,))]

    def active(self) -> Recording | None:
        row = self.db.one(
            "SELECT * FROM recordings WHERE kind='mic' AND state IN (?,?,?) "
            "ORDER BY created_at DESC LIMIT 1", RecState.ACTIVE)
        return Recording.from_row(row) if row else None

    def events(self, recording_id: str, limit: int = 50) -> list[dict]:
        rows = self.db.query("SELECT at, level, kind, message FROM events WHERE recording_id=? "
                             "ORDER BY id DESC LIMIT ?", (recording_id, limit))
        return [dict(r) for r in reversed(rows)]

    def segments_dir(self, rec: Recording) -> Path:
        return Path(rec.dir_path) / "segments"

    # --- scrittura -----------------------------------------------------------
    def create_mic_request(self, title: str, notebook_id: str | None,
                           notebook_title: str | None) -> Recording:
        rid = new_recording_id()
        rec_dir = self.cfg.recordings_dir / rid
        try:
            with self.db.tx() as conn:
                conn.execute(
                    "INSERT INTO recordings(id, title, kind, notebook_id, notebook_title, state, "
                    "created_at, dir_path) VALUES(?,?,?,?,?,?,?,?)",
                    (rid, title, "mic", notebook_id, notebook_title, RecState.REQUESTED, iso(),
                     str(rec_dir)))
        except sqlite3.IntegrityError as exc:
            active = self.active()
            raise Conflict(
                "C'è già una registrazione attiva" + (f": {active.id} «{active.title}»"
                                                       if active else ""),
                hint="Fermala con 'agency record stop' prima di avviarne un'altra.",
            ) from exc
        return self.get(rid)

    def update(self, recording_id: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE recordings SET {cols} WHERE id=?",
                        tuple(fields.values()) + (recording_id,))
        self.write_metadata_file(recording_id)

    def write_metadata_file(self, recording_id: str) -> None:
        row = self.db.one("SELECT * FROM recordings WHERE id=?", (recording_id,))
        if row is None:
            return
        rec_dir = Path(row["dir_path"])
        if not rec_dir.exists():
            return
        tmp = rec_dir / "metadata.json.tmp"
        tmp.write_text(json.dumps(dict(row), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, rec_dir / "metadata.json")

    def set_notebook(self, recording_id: str, notebook_id: str, title: str | None) -> None:
        self.update(recording_id, notebook_id=notebook_id, notebook_title=title)

    def import_file(self, src: Path, title: str, notebook_id: str | None,
                    notebook_title: str | None) -> Recording:
        """Copia un audio esterno nell'archivio (l'originale non viene toccato)."""
        src = src.expanduser().resolve()
        if not src.is_file():
            raise NotFound(f"File non trovato: {src}")
        free = shutil.disk_usage(self.cfg.data_dir).free
        if free < src.stat().st_size + self.cfg.get("recording.min_free_mb") * 1024 * 1024:
            raise StorageError("Spazio insufficiente per importare il file",
                               hint="Libera spazio sulla scheda SD.")
        rid = new_recording_id()
        rec_dir = self.cfg.recordings_dir / rid
        (rec_dir / "import").mkdir(parents=True)
        dest = rec_dir / "import" / src.name
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        now = iso()
        self.db.execute(
            "INSERT INTO recordings(id, title, kind, notebook_id, notebook_title, state, "
            "created_at, dir_path, original_path, file_size, upload_status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rid, title, "import", notebook_id, notebook_title, RecState.FINALIZING, now,
             str(rec_dir), str(src), dest.stat().st_size, UploadStatus.NOT_UPLOADED))
        self.db.add_event("import", f"Importato da {src}", recording_id=rid)
        return self.get(rid)
