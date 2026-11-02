import os
import stat
import struct
from pathlib import Path

import pytest

from agency_briefing.audio import encode
from agency_briefing.audio.capture import list_segment_files
from agency_briefing.audio.devices import list_capture_devices, resolve_device
from agency_briefing.audio.wav import analyze_levels, read_wav_info, repair_wav, write_wav_header
from agency_briefing.config import load_config
from agency_briefing.db import Database
from agency_briefing.errors import ConfigError, DeviceError
from agency_briefing.logging_setup import redact
from agency_briefing.notebook.base import SourceInfo, source_matches_recording


def test_config_defaults_and_validation(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.get("encoding.upload_format") == "m4a"
    assert not cfg.loaded_from_file
    bad = tmp_path / "bad.toml"
    bad.write_text('[audio]\nunknown = 1\n')
    with pytest.raises(ConfigError):
        load_config(bad)
    bad.write_text('[encoding]\nupload_format = "flac"\n')
    with pytest.raises(ConfigError):
        load_config(bad)
    bad.write_text('[upload]\nauto_after_stop = "yes"\n')
    with pytest.raises(ConfigError):
        load_config(bad)


def test_device_discovery_by_card_id(asound):
    devices = list_capture_devices()
    assert [d.card_id for d in devices] == ["Device"]
    assert devices[0].is_usb
    pcm, dev = resolve_device("auto")
    assert pcm == "plughw:CARD=Device,DEV=0"
    assert resolve_device("Device")[0] == pcm
    assert resolve_device("PnP")[0] == pcm
    assert resolve_device("hw:CARD=Other,DEV=0")[0] == "hw:CARD=Other,DEV=0"
    with pytest.raises(DeviceError):
        resolve_device("Inesistente")
    (asound / "pcm").write_text("00-00: x : x : playback 1\n")
    with pytest.raises(DeviceError):
        resolve_device("auto")


HAT_CARDS = """\
 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 2 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 3 [wm8960soundcard]: wm8960-soundca - wm8960-soundcard
                      wm8960-soundcard
"""
HAT_PCM = """\
00-00: MAI PCM i2s-hifi-0 : MAI PCM i2s-hifi-0 : playback 1
02-00: bcm2835 Headphones : bcm2835 Headphones : playback 8
03-00: bcm2835-i2s-wm8960-hifi wm8960-hifi-0 : bcm2835-i2s-wm8960-hifi wm8960-hifi-0 : playback 1 : capture 1
"""


def test_auto_falls_back_to_i2s_hat_and_prefers_usb(asound):
    # ReSpeaker 2-Mic HAT v1 (WM8960, dtoverlay=wm8960-soundcard) come su DartsVision.
    (asound / "cards").write_text(HAT_CARDS)
    (asound / "pcm").write_text(HAT_PCM)
    pcm, dev = resolve_device("auto")
    assert pcm == "plughw:CARD=wm8960soundcard,DEV=0"
    assert not dev.is_usb
    from tests.conftest import CARDS, PCM
    (asound / "cards").write_text(HAT_CARDS + CARDS.replace(" 3 [", " 4 ["))
    (asound / "pcm").write_text(HAT_PCM + PCM.replace("03-00", "04-00"))
    assert resolve_device("auto")[0] == "plughw:CARD=Device,DEV=0"


def _wav(path, seconds=1.0, rate=16000, broken=False):
    n = int(seconds * rate)
    data = b"".join(struct.pack("<h", 1000 if i % 2 else -1000) for i in range(n))
    with open(path, "wb") as fh:
        write_wav_header(fh, 1, rate, 16, 0x7FFFFFF0 if broken else len(data))
        fh.write(data)
        if broken:
            fh.write(b"\x01")  # frammento di campione incompleto
    return path


def test_wav_repair_and_levels(tmp_path):
    p = _wav(tmp_path / "part001.wav", broken=True)
    assert not read_wav_info(p).header_ok
    assert repair_wav(p) is True
    info = read_wav_info(p)
    assert info.header_ok and abs(info.duration_seconds - 1.0) < 1e-6
    assert repair_wav(p) is False
    lv = analyze_levels(p)
    assert -31 < lv.peak_dbfs < -30 and lv.verdict == "ok"


def test_levels_skip_codec_startup_click(tmp_path):
    # WM8960 sul Pi: un campione a fondo scala nei primi ms, poi rumore a -30 dBFS.
    p = _wav(tmp_path / "click.wav", seconds=2.0)
    with open(p, "r+b") as fh:
        fh.seek(44)
        fh.write(struct.pack("<h", 32767))
    assert analyze_levels(p).verdict.startswith("possibile saturazione")
    lv = analyze_levels(p, skip_seconds=0.5)
    assert -31 < lv.peak_dbfs < -30 and lv.verdict == "ok"
    assert abs(lv.duration_seconds - 2.0) < 1e-6
    assert analyze_levels(p, skip_seconds=5).peak_dbfs > -1  # file troppo corto: niente salto


def test_segment_natural_order(tmp_path):
    for name in ["part002.wav", "part001-10.wav", "part001-02.wav", "part001-01.wav", "x.wav"]:
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in list_segment_files(tmp_path)] == [
        "part001-01.wav", "part001-02.wav", "part001-10.wav", "part002.wav"]


def test_source_matching():
    rid = "r261102-153012-a1b2"
    assert source_matches_recording(SourceInfo("s", f"Briefing - x [{rid}]", "ready"), rid)
    assert source_matches_recording(SourceInfo("s", f"{rid}.m4a", "ready"), rid, f"{rid}.m4a")
    assert not source_matches_recording(SourceInfo("s", "Altro", "ready"), rid)


def test_redaction():
    text = "cookie __Secure-1PSID=abcdef123; SAPISID=zzz oauth_token: qqq"
    red = redact(text)
    assert "abcdef123" not in red and "zzz" not in red and "qqq" not in red


def test_notebooklm_py_api_surface():
    """Verifica che i metodi usati dall'adapter esistano nella versione installata."""
    import notebooklm as nlm
    from notebooklm import NotebookLMClient

    assert nlm.__version__ == "0.8.6"
    assert hasattr(NotebookLMClient, "from_storage")
    from notebooklm._chat import ChatAPI  # noqa: F401  (modulo interno, solo verifica)
    from notebooklm._notebooks import NotebooksAPI
    from notebooklm._sources import SourcesAPI

    for name in ("list", "get_or_none", "add_file", "wait_until_ready"):
        assert hasattr(SourcesAPI, name), name
    for name in ("list", "get_or_none", "create"):
        assert hasattr(NotebooksAPI, name), name
    for exc in ("AuthError", "RateLimitError", "NetworkError", "RPCTimeoutError", "ServerError",
                "NotFoundError", "ValidationError", "SourceAddError"):
        assert hasattr(nlm, exc), exc
    assert hasattr(nlm.NotebookLMError, "unconfirmed")


def test_exception_mapping():
    import notebooklm as nlm

    from agency_briefing.errors import (
        AuthRequired,
        RateLimited,
        TransientRemoteError,
        UncertainUpload,
    )
    from agency_briefing.notebook.notebooklm_py import map_exception

    assert isinstance(map_exception(FileNotFoundError("x")), AuthRequired)
    assert isinstance(map_exception(nlm.AuthError("x")), AuthRequired)
    rl = map_exception(nlm.RateLimitError("x", retry_after=7))
    assert isinstance(rl, RateLimited) and rl.retry_after == 7
    assert isinstance(map_exception(nlm.NetworkError("x")), TransientRemoteError)
    assert isinstance(map_exception(nlm.NetworkError("x"), during_upload=True), UncertainUpload)
    assert isinstance(map_exception(TimeoutError(), during_upload=True), UncertainUpload)


def test_data_dirs_and_db_files_private_with_permissive_umask(tmp_path, monkeypatch):
    # Debian/Raspberry Pi OS: umask utente 0002. Verificato sul Pi: il WAL nasceva 0644.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    old = os.umask(0o002)
    try:
        cfg = load_config(tmp_path / "missing.toml")
        cfg.ensure_dirs()
        db = Database(cfg.data_dir / "gweb.db")
        db.connect().execute("INSERT OR REPLACE INTO meta(key, value) VALUES('x', 'y')")
    finally:
        os.umask(old)
    for d in (cfg.data_dir, cfg.recordings_dir, cfg.summaries_dir, cfg.state_dir):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
    for suffix in ("", "-wal", "-shm"):
        p = cfg.data_dir / f"gweb.db{suffix}"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600, p
    db.close()


def test_encode_applies_audio_filter(tmp_path, monkeypatch):
    seg = _wav(tmp_path / "part001.wav", seconds=1.0)
    default = load_config(tmp_path / "missing.toml").get("encoding.audio_filter")
    # Il filtro di default deve essere accettato dall'ffmpeg installato.
    out, mime = encode.encode_segments([seg], tmp_path / "rec", fmt="m4a", bitrate="96k",
                                       channels=1, audio_filter=default)
    assert out.exists() and out.stat().st_size > 0 and mime == "audio/mp4"
    seen = []
    monkeypatch.setattr(encode, "_run", lambda argv, timeout=None: (
        seen.append(argv), Path(argv[-1]).write_bytes(b"x")))
    encode.encode_segments([seg], tmp_path / "a", fmt="m4a", bitrate="96k", channels=1,
                           audio_filter="highpass=f=90")
    encode.encode_segments([seg], tmp_path / "b", fmt="m4a", bitrate="96k", channels=1)
    assert seen[0][seen[0].index("-af") + 1] == "highpass=f=90"
    assert "-af" not in seen[1]
