"""Pulizia: si elimina SOLO ciò che è verificato pronto su NotebookLM al momento."""

import json
import sqlite3
from pathlib import Path

import pytest

from agency_briefing.cleanup import render_timer_units
from agency_briefing.db import Database
from agency_briefing.errors import AuthRequired, Conflict, TransientRemoteError
from tests.test_queue import _drain, _ready_recording


def _uploaded(app, title="Briefing"):
    rec = _ready_recording(app, title=title)
    # simula registrazione dal microfono: aggiunge segmenti WAV originali
    seg = Path(rec["dir_path"]) / "segments"
    seg.mkdir()
    (seg / "part001.wav").write_bytes(b"\0" * 5000)
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    jobs = _drain(app)
    assert jobs[-1].state == "succeeded"
    return app.store.get(rec["id"])


def test_dry_run_deletes_nothing(app):
    rec = _uploaded(app)
    res = app.recordings_prune(execute=False, min_age_days=0)
    assert res["dry_run"] and [i["recording_id"] for i in res["would_delete"]] == [rec.id]
    assert Path(rec.file_path).exists() and (Path(rec.dir_path) / "segments/part001.wav").exists()


def test_prune_all_after_remote_verification(app):
    rec = _uploaded(app)
    summary = app.briefing_summarize(rec.id)
    res = app.recordings_prune(execute=True, min_age_days=0, mode="all")
    assert [i["recording_id"] for i in res["deleted"]] == [rec.id] and res["freed_bytes"] > 0
    r = app.store.get(rec.id)
    assert r.purged_at and r.purged_scope == "all"
    assert not Path(r.file_path).exists()
    assert not list(Path(r.dir_path).rglob("*.wav"))
    assert (Path(r.dir_path) / "metadata.json").exists()       # metadati restano
    assert Path(summary["file"]).exists()                       # riepiloghi restano
    assert app.recordings_show(rec.id)["recording"]["id"] == rec.id
    with pytest.raises(Conflict):                               # non più caricabile
        app.queue.enqueue(r, "nb-demo-interno")
    # seconda esecuzione: niente da fare
    assert app.recordings_prune(execute=True, min_age_days=0)["deleted"] == []


def test_mode_segments_keeps_uploaded_file(app):
    rec = _uploaded(app)
    app.recordings_prune(execute=True, min_age_days=0, mode="segments")
    r = app.store.get(rec.id)
    assert Path(r.file_path).exists() and r.purged_scope == "segments"
    assert not (Path(r.dir_path) / "segments").exists()
    # in seguito si può passare a 'all'
    app.recordings_prune(execute=True, min_age_days=0, mode="all")
    assert not Path(r.file_path).exists() and app.store.get(rec.id).purged_scope == "all"


def test_not_deleted_when_not_ready_or_too_recent(app):
    rec = _ready_recording(app, title="Mai caricata")
    res = app.recordings_prune(execute=True, min_age_days=0)
    assert res["deleted"] == [] and "non ancora caricata" in res["skipped"][0]["reason"]
    up = _uploaded(app, title="Recente")
    res = app.recordings_prune(execute=True, min_age_days=7)
    assert res["deleted"] == []
    assert any("meno di 7 giorni" in i["reason"] for i in res["skipped"])
    assert Path(rec["file_path"]).exists() and Path(up.file_path).exists()


def test_not_deleted_when_source_removed_from_notebook(app):
    rec = _uploaded(app)
    state_path = app.cfg.state_dir / "fake-notebooklm.json"
    state = json.loads(state_path.read_text())
    for nb in state["notebooks"]:
        nb["sources"] = []
    state_path.write_text(json.dumps(state))
    res = app.recordings_prune(execute=True, min_age_days=0)
    assert res["deleted"] == [] and "non più presente" in res["skipped"][0]["reason"]
    assert Path(rec.file_path).exists()


@pytest.mark.parametrize("fail,exc", [("list_network:100", TransientRemoteError),
                                      ("auth:100", AuthRequired)])
def test_remote_failure_aborts_without_deleting(app, monkeypatch, fail, exc):
    rec = _uploaded(app)
    monkeypatch.setenv("AGENCY_FAKE_FAIL", fail)
    with pytest.raises(exc):
        app.recordings_prune(execute=True, min_age_days=0)
    assert Path(rec.file_path).exists()
    assert app.store.get(rec.id).purged_at is None


def test_schema_migration_adds_columns(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES('schema_version','1');
        CREATE TABLE recordings(id TEXT PRIMARY KEY, title TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'mic', notebook_id TEXT, notebook_title TEXT,
            state TEXT NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0, stop_reason TEXT,
            created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT, heartbeat_at TEXT,
            duration_seconds REAL, dir_path TEXT NOT NULL, file_path TEXT, original_path TEXT,
            file_size INTEGER, mime_type TEXT, device TEXT, sample_rate INTEGER,
            channels INTEGER, upload_status TEXT NOT NULL DEFAULT 'not_uploaded',
            remote_source_id TEXT, error TEXT);
        INSERT INTO recordings(id,title,state,created_at,dir_path) VALUES('r1','x','ready','t','/d');
    """)
    conn.commit()
    conn.close()
    db = Database(db_path)
    cols = {r["name"] for r in db.query("PRAGMA table_info(recordings)")}
    assert {"purged_at", "purged_scope", "purged_bytes"} <= cols
    assert db.one("SELECT value FROM meta WHERE key='schema_version'")["value"] == "2"
    assert db.one("SELECT id FROM recordings")["id"] == "r1"


def test_timer_units():
    service, timer = render_timer_units("/x/agency", "Sun *-*-* 03:30:00")
    assert "ExecStart=/x/agency recordings prune --yes" in service
    assert "OnCalendar=Sun *-*-* 03:30:00" in timer and "Persistent=true" in timer
