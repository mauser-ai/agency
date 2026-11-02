import os
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FAKE_ARECORD = f"{sys.executable} {HERE / 'fake_arecord.py'}"

CARDS = """\
 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 3 [Device         ]: USB-Audio - USB PnP Sound Device
                      C-Media Electronics Inc. USB PnP Sound Device at usb-0000:01:00.0-1.3, full speed
"""
PCM = """\
00-00: bcm2835 Headphones : bcm2835 Headphones : playback 8
03-00: USB Audio : USB Audio : playback 1 : capture 1
"""


@pytest.fixture
def asound(tmp_path, monkeypatch):
    root = tmp_path / "asound"
    root.mkdir()
    (root / "cards").write_text(CARDS)
    (root / "pcm").write_text(PCM)
    monkeypatch.setenv("GWEB_ASOUND_ROOT", str(root))
    return root


@pytest.fixture
def cfg_path(tmp_path, asound, monkeypatch):
    ffmpeg = "ffmpeg"
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(f"""
        [paths]
        data_dir = "{tmp_path / 'data'}"
        state_dir = "{tmp_path / 'state'}"
        credentials_dir = "{tmp_path / 'cred'}"
        [audio]
        capture_command = "{FAKE_ARECORD}"
        sample_rate = 16000
        segment_seconds = 10
        [recording]
        min_free_mb = 1
        critical_free_mb = 0
        reconnect_timeout_seconds = 3
        resume_window_seconds = 30
        [encoding]
        ffmpeg = "{ffmpeg}"
        [upload]
        backoff_base_seconds = 0
        reconcile_settle_seconds = 0
        processing_poll_seconds = 0
        [notebooklm]
        backend = "fake"
        keepalive_minutes = 0
        [service]
        poll_interval_seconds = 0.1
    """))
    monkeypatch.setenv("GWEB_CONFIG", str(path))
    monkeypatch.setenv("GWEB_FAKE_READY_AFTER", "0")
    monkeypatch.delenv("GWEB_FAKE_FAIL", raising=False)
    monkeypatch.setenv("FAKE_ARECORD_SPEED", "20")
    return path


@pytest.fixture
def app(cfg_path):
    from agency_briefing.app import AgencyApp
    from agency_briefing.config import load_config

    a = AgencyApp(load_config(cfg_path))
    yield a
    a.db.close()


def pytest_configure(config):
    os.environ.setdefault("TZ", "Europe/Rome")
