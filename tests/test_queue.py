"""Coda: persistenza, retry, esiti incerti, riconciliazione, duplicati, auth scaduta."""

import json
import struct

import pytest

from agency_briefing.app import AgencyApp
from agency_briefing.audio.wav import write_wav_header
from agency_briefing.config import load_config
from agency_briefing.errors import Conflict
from agency_briefing.models import JobState, RecState, UploadStatus
from agency_briefing.queue import UploadWorker


def _ready_recording(app, title="Briefing test", notebook="nb-demo-cliente-rossi"):
    src = app.cfg.state_dir / "in.wav"
    data = struct.pack("<h", 500) * 16000
    with open(src, "wb") as fh:
        write_wav_header(fh, 1, 16000, 16, len(data))
        fh.write(data)
    rec = app.recordings_import(str(src), title, notebook)["recording"]
    assert rec["state"] == RecState.READY
    assert rec["file_path"].endswith(".m4a"), "WAV importati vanno convertiti (issue #2138)"
    return rec


def _uploads(app):
    return json.loads((app.cfg.state_dir / "fake-notebooklm.json").read_text())["uploads"]


def _drain(app, steps=10):
    w = UploadWorker(app.cfg, app.queue, app.backend)
    out = []
    for _ in range(steps):
        j = w.run_once()
        if j is None:
            break
        out.append(j)
    return out


def test_upload_happy_path_distinguishes_uploaded_and_ready(app):
    rec = _ready_recording(app)
    job = app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    assert app.store.get(rec["id"]).upload_status == UploadStatus.QUEUED
    w = UploadWorker(app.cfg, app.queue, app.backend)
    j = w.run_once()
    assert j.state == JobState.PROCESSING and j.remote_source_id and j.uploaded_at
    assert app.store.get(rec["id"]).upload_status == UploadStatus.PROCESSING
    j = w.run_once()
    assert j.state == JobState.SUCCEEDED
    r = app.store.get(rec["id"])
    assert r.upload_status == UploadStatus.READY and r.remote_source_id == j.remote_source_id
    assert _uploads(app) == 1
    # Duplicato: rifiutato in modo esplicito
    with pytest.raises(Conflict):
        app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    assert job.id == j.id


def test_network_error_retries_with_backoff(app, monkeypatch):
    rec = _ready_recording(app)
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "network:2")
    jobs = _drain(app)
    states = [j.state for j in jobs]
    assert states[:2] == [JobState.PENDING, JobState.PENDING]
    assert states[-1] == JobState.SUCCEEDED
    assert _uploads(app) == 1


def test_timeout_after_upload_reconciles_without_duplicate(app, monkeypatch):
    rec = _ready_recording(app)
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "timeout_after_upload:1")
    jobs = _drain(app)
    assert jobs[0].state == JobState.UNCERTAIN
    assert app.store.get(rec["id"]).upload_status in (UploadStatus.UNCERTAIN,
                                                      UploadStatus.PROCESSING,
                                                      UploadStatus.READY)
    assert jobs[-1].state == JobState.SUCCEEDED
    assert _uploads(app) == 1, "la riconciliazione deve trovare la fonte già creata"


def test_timeout_before_upload_needs_two_absent_checks(app, monkeypatch):
    rec = _ready_recording(app)
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "timeout_before_upload:1")
    jobs = _drain(app)
    assert [j.state for j in jobs[:2]] == [JobState.UNCERTAIN, JobState.UNCERTAIN]
    assert jobs[-1].state == JobState.SUCCEEDED
    assert _uploads(app) == 1


def test_uncertain_stays_visible_when_reconciliation_impossible(app, monkeypatch):
    rec = _ready_recording(app)
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "timeout_before_upload:1")
    jobs = _drain(app, steps=1)
    assert jobs[0].state == JobState.UNCERTAIN
    monkeypatch.setenv("GWEB_FAKE_FAIL", "list_network:100")
    w = UploadWorker(app.cfg, app.queue, app.backend)
    for _ in range(3):
        j = w.run_once()
        assert j.state == JobState.UNCERTAIN  # mai reinvio senza verifica
    assert _uploads(app) == 0
    # Risoluzione manuale documentata
    j = app.queue.resolve(j.id, source_id=None, not_uploaded=True)
    assert j.state == JobState.PENDING


def test_expired_session_blocks_without_consuming_attempts(app, monkeypatch):
    rec = _ready_recording(app)
    job = app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "auth:1")
    w = UploadWorker(app.cfg, app.queue, app.backend)
    j = w.run_once()
    assert j.state == JobState.BLOCKED_AUTH and j.attempts == 0
    assert app.store.get(rec["id"]).upload_status == UploadStatus.BLOCKED_AUTH
    assert w.run_once() is None  # attende auth_recheck_minutes
    app.queue.retry(job.id)
    assert _drain(app)[-1].state == JobState.SUCCEEDED


def test_rate_limit_and_permanent_failure_then_manual_retry(app, monkeypatch):
    rec = _ready_recording(app)
    job = app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "rate_limit:1")
    j = UploadWorker(app.cfg, app.queue, app.backend).run_once()
    assert j.state == JobState.PENDING and j.error_code == "RATE_LIMITED"
    # File locale sparito -> errore permanente, poi retry manuale dopo ripristino
    from pathlib import Path

    f = Path(rec["file_path"])
    moved = f.with_suffix(".bak")
    f.rename(moved)
    app.queue.retry(job.id)
    j = UploadWorker(app.cfg, app.queue, app.backend).run_once()
    assert j.state == JobState.FAILED
    moved.rename(f)
    assert app.queue.retry(job.id).state == JobState.PENDING
    assert _drain(app)[-1].state == JobState.SUCCEEDED


def test_queue_survives_restart_and_interrupted_upload_is_uncertain(cfg_path):
    app1 = AgencyApp(load_config(cfg_path))
    rec = _ready_recording(app1)
    job = app1.queue.enqueue(app1.store.get(rec["id"]), rec["notebook_id"])
    app1.queue.claim_due()  # simula worker che muore durante l'upload
    app1.db.close()
    app2 = AgencyApp(load_config(cfg_path))
    assert app2.queue.get(job.id).state == JobState.RUNNING
    assert app2.queue.recover_after_restart() == 1
    j = app2.queue.get(job.id)
    assert j.state == JobState.UNCERTAIN and j.error_code == "INTERRUPTED"
    assert _drain(app2)[-1].state == JobState.SUCCEEDED
    assert _uploads(app2) == 1


def test_processing_error_marks_failed(app, monkeypatch):
    monkeypatch.setenv("GWEB_FAKE_FAIL", "processing_error:1")
    app.cfg.data["upload"]["processing_error_grace_minutes"] = 0
    rec = _ready_recording(app)
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    jobs = _drain(app)
    assert jobs[-1].state == JobState.FAILED and jobs[-1].error_code == "PROCESSING_FAILED"


def test_summary_requires_ready_source_and_is_saved(app):
    from agency_briefing.errors import NotReady

    rec = _ready_recording(app)
    with pytest.raises(NotReady):
        app.briefing_summarize(rec["id"])
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    _drain(app)
    res = app.briefing_summarize(rec["id"])
    assert res["scope"] is None and "nessun'altra fonte" in res["scope_skipped_reason"]
    from pathlib import Path

    text = Path(res["file"]).read_text()
    assert rec["id"] in text and "Citazioni" in text
    # Con un'altra fonte pronta il confronto viene eseguito
    rec2 = _ready_recording(app, title="Preventivo letto")
    app.queue.enqueue(app.store.get(rec2["id"]), rec2["notebook_id"])
    _drain(app)
    res = app.briefing_summarize(rec["id"])
    assert res["scope"] is not None and len(res["compared_sources"]) == 1


def test_notebook_association_is_per_recording(app):
    app.notebook_select("nb-demo-cliente-rossi")
    rec = _ready_recording(app, notebook=None)
    assert rec["notebook_id"] == "nb-demo-cliente-rossi"
    app.notebook_select("nb-demo-interno")
    assert app.store.get(rec["id"]).notebook_id == "nb-demo-cliente-rossi"


def test_upload_requested_during_finalization_is_queued_when_ready(app):
    from agency_briefing.daemon import Daemon

    rec = _ready_recording(app, notebook=None)
    app.store.update(rec["id"], state=RecState.FINALIZING, kind="mic")
    out = app.recordings_upload(rec["id"], "nb-demo-interno")
    assert out["scheduled"] is True and out["job"] is None
    r = app.store.get(rec["id"])
    assert r.upload_on_ready == 1 and r.notebook_id == "nb-demo-interno"
    assert app.queue.list() == []
    # Fine della finalizzazione: il demone accoda l'upload e azzera la richiesta.
    app.store.update(rec["id"], state=RecState.READY)
    Daemon(app)._on_finalized(app.store.get(rec["id"]))
    jobs = app.queue.list()
    assert len(jobs) == 1 and jobs[0].notebook_id == "nb-demo-interno"
    assert app.store.get(rec["id"]).upload_on_ready == 0
    _drain(app)
    assert _uploads(app) == 1


def test_upload_rejected_while_still_recording(app):
    from agency_briefing.errors import NotReady

    rec = _ready_recording(app)
    app.store.update(rec["id"], state=RecState.RECORDING)
    with pytest.raises(NotReady):
        app.recordings_upload(rec["id"], "nb-demo-interno")


def _sources(app, notebook="nb-demo-cliente-rossi"):
    st = json.loads((app.cfg.state_dir / "fake-notebooklm.json").read_text())
    return next(n for n in st["notebooks"] if n["id"] == notebook)["sources"]


def test_source_gets_exactly_the_chosen_title(app):
    rec = _ready_recording(app, title="Riunione Orsini preventivatore")
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    assert _drain(app)[-1].state == JobState.SUCCEEDED
    assert [s["title"] for s in _sources(app)] == ["Riunione Orsini preventivatore"]


def test_same_title_as_older_source_is_not_mistaken_for_ours(app):
    first = _ready_recording(app, title="Brief settimanale")
    app.queue.enqueue(app.store.get(first["id"]), first["notebook_id"])
    _drain(app)
    second = _ready_recording(app, title="Brief settimanale")
    app.queue.enqueue(app.store.get(second["id"]), second["notebook_id"])
    jobs = _drain(app)
    assert jobs[-1].state == JobState.SUCCEEDED
    assert _uploads(app) == 2, "una fonte omonima già esistente non è la nostra"
    assert len({s["id"] for s in _sources(app)}) == 2


def test_lost_title_is_restored_when_source_is_ready(app, monkeypatch):
    rec = _ready_recording(app, title="Kickoff sito Tritium")
    app.queue.enqueue(app.store.get(rec["id"]), rec["notebook_id"])
    monkeypatch.setenv("GWEB_FAKE_FAIL", "title_lost:1")
    assert _drain(app)[-1].state == JobState.SUCCEEDED
    assert [s["title"] for s in _sources(app)] == ["Kickoff sito Tritium"]
