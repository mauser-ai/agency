"""Ciclo avvio/arresto con il demone reale e un arecord simulato; recupero dopo crash."""

import threading
import time
from pathlib import Path

import pytest

from agency_briefing.app import AgencyApp
from agency_briefing.audio.capture import list_segment_files
from agency_briefing.audio.wav import read_wav_info, write_wav_header
from agency_briefing.config import load_config
from agency_briefing.daemon import AlreadyRunning, Daemon
from agency_briefing.db import iso
from agency_briefing.errors import Conflict, DeviceError, ServiceUnavailable
from agency_briefing.models import RecState


def wait_for(pred, timeout=20.0, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condizione non raggiunta")


@pytest.fixture
def daemon(cfg_path):
    app = AgencyApp(load_config(cfg_path))
    d = Daemon(app)
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    client = AgencyApp(load_config(cfg_path))
    wait_for(lambda: client.daemon_state()["alive"])
    yield d, client
    d.stop()
    t.join(timeout=30)


def test_start_requires_running_service(app):
    with pytest.raises(ServiceUnavailable):
        app.record_start("x", None)


def test_record_cycle_and_concurrency(daemon):
    d, app = daemon
    with pytest.raises(AlreadyRunning):
        Daemon(AgencyApp(app.cfg))._acquire_lock()
    res = app.record_start("Briefing nuovo sito", "nb-demo-cliente-rossi")
    rec = res["recording"]
    assert rec["state"] == RecState.RECORDING
    assert rec["device"] == "plughw:CARD=Device,DEV=0"
    with pytest.raises(Conflict):
        app.record_start("Secondo", None)
    time.sleep(1.5)  # ~30 s di audio con FAKE_ARECORD_SPEED=20 -> più segmenti
    st = app.record_status()
    assert st["active"] and st["recorded_seconds"] > 0
    out = app.record_stop(wait=True, wait_final=True)["recording"]
    assert out["state"] == RecState.READY, out
    assert out["file_path"].endswith(".m4a") and Path(out["file_path"]).stat().st_size > 0
    assert out["duration_seconds"] > 5
    segs = list_segment_files(app.store.segments_dir(app.store.get(out["id"])))
    assert len(segs) >= 2 and all(read_wav_info(s).header_ok for s in segs)
    assert out["upload_status"] == "not_uploaded"  # upload esplicito per default
    assert app.store.active() is None


def test_device_missing_fails_cleanly(daemon, asound):
    _d, app = daemon
    (asound / "pcm").write_text("00-00: x : x : playback 1\n")
    with pytest.raises(DeviceError):
        app.record_start("Senza microfono", None)


def test_microphone_unplug_reconnects(daemon, monkeypatch):
    _d, app = daemon
    monkeypatch.setenv("FAKE_ARECORD_EXIT_AFTER", "24")  # il processo "cade" dopo 8s di audio
    app.record_start("Con scollegamento", None)
    rid = app.store.active().id
    wait_for(lambda: any(e["kind"] == "capture_resumed" for e in
                         app.db.query("SELECT kind FROM events WHERE recording_id=?", (rid,))))
    monkeypatch.delenv("FAKE_ARECORD_EXIT_AFTER")
    out = app.record_stop(wait=True, wait_final=True)["recording"]
    assert out["state"] == RecState.READY
    parts = {r["part"] for r in app.db.query("SELECT part FROM segments WHERE recording_id=?",
                                             (rid,))}
    assert len(parts) >= 2


def test_microphone_lost_for_good_closes_recording(daemon, monkeypatch, asound):
    _d, app = daemon
    monkeypatch.setenv("FAKE_ARECORD_EXIT_AFTER", "30")
    app.record_start("Microfono perso", None)
    rid = app.store.active().id
    time.sleep(0.8)
    (asound / "pcm").write_text("00-00: x : x : playback 1\n")  # scheda sparita
    rec = wait_for(lambda: (r := app.store.get(rid)).state in (RecState.READY,
                                                                RecState.FAILED) and r)
    assert rec.state == RecState.READY and rec.stop_reason == "device_lost"
    assert "Microfono non disponibile" in rec.error


def test_crash_recovery_finalizes_and_repairs(cfg_path):
    app = AgencyApp(load_config(cfg_path))
    rec = app.store.create_mic_request("Interrotta", None, None)
    seg_dir = app.store.segments_dir(rec)
    seg_dir.mkdir(parents=True)
    data = b"\x10\x00" * 16000 * 3
    with open(seg_dir / "part001.wav", "wb") as fh:  # intestazione mai chiusa
        write_wav_header(fh, 1, 16000, 16, 0x7FFFFFF0)
        fh.write(data)
    app.store.update(rec.id, state=RecState.RECORDING, started_at=iso(),
                     heartbeat_at="2020-01-01T00:00:00.000000Z", channels=1)
    d = Daemon(AgencyApp(load_config(cfg_path)))
    d.recorder.recover_on_startup()
    r = wait_for(lambda: (x := app.store.get(rec.id)).state == RecState.READY and x)
    assert r.stop_reason == "interrupted" and "interrotta" in r.error
    assert abs(r.duration_seconds - 3.0) < 0.1
    assert read_wav_info(seg_dir / "part001.wav").header_ok
    d.recorder.shutdown()


def test_service_restart_resumes_recording(cfg_path):
    app = AgencyApp(load_config(cfg_path))
    d1 = Daemon(AgencyApp(load_config(cfg_path)))
    t1 = threading.Thread(target=d1.run, daemon=True)
    t1.start()
    wait_for(lambda: app.daemon_state()["alive"])
    app.record_start("Ripresa", None)
    rid = app.store.active().id
    time.sleep(0.8)
    d1.stop()  # come SIGTERM di systemd
    t1.join(timeout=30)
    assert app.store.get(rid).state == RecState.RECORDING  # non è un arresto utente
    d2 = Daemon(AgencyApp(load_config(cfg_path)))
    t2 = threading.Thread(target=d2.run, daemon=True)
    t2.start()
    wait_for(lambda: app.daemon_state()["alive"])
    wait_for(lambda: len(app.db.query("SELECT part FROM segments WHERE recording_id=?",
                                      (rid,))) >= 2)
    out = app.record_stop(wait=True, wait_final=True)["recording"]
    assert out["state"] == RecState.READY
    d2.stop()
    t2.join(timeout=30)
