"""CLI end-to-end come processo separato: codici di uscita, --json, import credenziali."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

AGENCY = str(Path(sys.executable).with_name("gweb"))


def run(*args, stdin=None, env=None):
    res = subprocess.run([AGENCY, *args], capture_output=True, text=True, input=stdin,
                         env={**os.environ, **(env or {})}, timeout=60)
    return res.returncode, res.stdout, res.stderr


def test_help_and_version(cfg_path):
    code, outp, _ = run("--help")
    assert code == 0 and "Esempi" in outp
    code, outp, _ = run("record", "start", "--help")
    assert code == 0 and "--notebook" in outp
    assert run("--version")[0] == 0


def test_config_show_json(cfg_path):
    code, outp, _ = run("config", "show", "--json")
    data = json.loads(outp)
    assert code == 0 and data["effective"]["notebooklm"]["backend"] == "fake"
    assert "storage_state.json" in data["paths"]["notebooklm_storage"]


def test_exit_codes(cfg_path):
    code, outp, _ = run("recordings", "show", "nope", "--json")
    assert code == 3 and json.loads(outp)["error"]["code"] == "NOT_FOUND"
    code, _, err = run("record", "start", "--title", "x")
    assert code == 6 and "servizio" in err.lower()
    code, _, _ = run("record", "stop")
    assert code == 3
    code, _, _ = run("record", "start")
    assert code == 2  # opzione obbligatoria mancante
    code, _, _ = run("jobs", "retry", "999")
    assert code == 3


def test_notebook_flow_with_fake_backend(cfg_path):
    code, outp, _ = run("notebook", "list", "--json")
    nbs = json.loads(outp)
    assert code == 0 and {n["id"] for n in nbs} >= {"nb-demo-interno"}
    code, outp, _ = run("notebook", "select", "interno", "--json")
    assert code == 0 and json.loads(outp)["id"] == "nb-demo-interno"
    code, outp, _ = run("notebook", "current", "--json")
    assert json.loads(outp)["id"] == "nb-demo-interno"
    code, _, _ = run("notebook", "select")  # non interattivo senza ID
    assert code == 2
    code, outp, _ = run("ask", "Quali decisioni?", "--json")
    assert code == 0 and json.loads(outp)["notebook"]["id"] == "nb-demo-interno"
    code, outp, _ = run("notebook", "create", "Cliente Bianchi", "--select", "--json")
    nb = json.loads(outp)
    assert code == 0 and nb["title"] == "Cliente Bianchi" and nb["default"] is True
    code, outp, _ = run("notebook", "list", "--json")
    assert any(n["id"] == nb["id"] and n["default"] for n in json.loads(outp))
    code, _, _ = run("notebook", "create", "  ", "--json")
    assert code == 2


def test_audio_devices_and_test(cfg_path):
    code, outp, _ = run("audio", "devices", "--json")
    d = json.loads(outp)
    assert code == 0 and d["resolved"] == "plughw:CARD=Device,DEV=0"
    code, outp, _ = run("audio", "test", "--duration", "1", "--json",
                        env={"FAKE_ARECORD_SPEED": "50"})
    t = json.loads(outp)
    assert code == 0 and t["verdict"] == "ok" and t["duration_seconds"] >= 1
    code, _, _ = run("audio", "test", "--duration", "1", env={"FAKE_ARECORD_FAIL": "1"})
    assert code == 9


def test_import_upload_jobs_run(cfg_path, tmp_path):
    import struct

    from agency_briefing.audio.wav import write_wav_header

    wav = tmp_path / "esterno.wav"
    data = struct.pack("<h", 300) * 16000
    with open(wav, "wb") as fh:
        write_wav_header(fh, 1, 16000, 16, len(data))
        fh.write(data)
    code, outp, _ = run("recordings", "import", str(wav), "--title", "Briefing esterno",
                        "--notebook", "nb-demo-cliente-rossi", "--json")
    assert code == 0, outp
    rid = json.loads(outp)["recording"]["id"]
    code, outp, err = run("recordings", "upload", rid, "--json")
    assert code == 0 and "Servizio non attivo" in json.loads(outp)["warnings"][-1]
    code, outp, _ = run("recordings", "upload", rid, "--json")
    assert code == 4  # duplicato
    code, outp, _ = run("jobs", "run", "--json")
    assert code == 0 and json.loads(outp)[-1]["state"] == "succeeded"
    code, outp, _ = run("recordings", "list", "--json")
    assert json.loads(outp)[0]["upload_status"] == "ready"
    code, outp, _ = run("briefing", "summarize", rid, "--json")
    assert code == 0 and Path(json.loads(outp)["file"]).exists()


def test_credentials_import_permissions_and_no_echo(cfg_path, tmp_path):
    secret = json.dumps({"cookies": [{"name": "SID", "value": "SEGRETO-NON-STAMPARE"}],
                         "origins": []})
    code, outp, err = run("notebook", "login", "--import", "-", stdin=secret)
    assert "SEGRETO" not in outp + err
    from agency_briefing.config import load_config

    cfg = load_config(cfg_path)
    p = cfg.storage_state_path
    assert p.exists() and stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.credentials_dir.stat().st_mode) == 0o700
    code, _, _ = run("notebook", "login", "--import", "-", stdin="non json")
    assert code == 2
    code, outp, _ = run("notebook", "login")
    assert code == 0 and "notebook login --import -" in outp


def test_session_summary_states_and_logout(cfg_path):
    from agency_briefing.app import AgencyApp
    from agency_briefing.config import load_config

    code, outp, _ = run("notebook", "status", "--json")
    d = json.loads(outp)
    assert code == 0 and d["session"]["state"] == "ok"
    app = AgencyApp(load_config(cfg_path))
    # Il demone segnala che Google ha rifiutato la sessione.
    app.db.set_setting("notebooklm.keepalive",
                       {"at": "2999-01-01T00:00:00Z", "ok": False, "error": "AUTH_REQUIRED"})
    code, outp, _ = run("notebook", "status", "--offline", "--json")
    assert code == 0 and json.loads(outp)["session"]["state"] == "expired"
    # Cambio account: il notebook predefinito del vecchio account viene azzerato.
    app.db.set_setting("notebooklm.account", "vecchio@example.com")
    app.db.set_setting("notebook.default", {"id": "nb-x", "title": "x"})
    app.db.delete_setting("notebooklm.keepalive")
    app.db.set_setting("notebooklm.last_check", {"at": "2000-01-01T00:00:00Z", "ok": True})
    st = app.notebook_status(remote=True)
    if st["remote"].get("account"):
        assert st["remote"]["account_changed"] and st["default_notebook"] is None
    assert st["session"]["state"] == "ok"
    code, outp, _ = run("notebook", "logout", "--json")
    assert code == 0 and "session" in json.loads(outp)
