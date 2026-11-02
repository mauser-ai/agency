"""Configurazione: file TOML statico + percorsi separati per codice, dati, stato e credenziali.

Ordine di risoluzione del file di configurazione:
1. variabile d'ambiente ``GWEB_CONFIG``
2. ``$XDG_CONFIG_HOME/gweb/config.toml`` (default ``~/.config/gweb/config.toml``)

Se il file non esiste si usano i default. Lo stato mutabile (notebook predefinito,
microfono scelto da CLI) NON sta qui ma nella tabella ``settings`` di SQLite, così la
CLI non riscrive mai il file dell'utente.
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .errors import ConfigError


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


DEFAULTS: dict[str, dict[str, Any]] = {
    "paths": {
        "data_dir": str(_xdg("XDG_DATA_HOME", ".local/share") / "gweb"),
        "state_dir": str(_xdg("XDG_STATE_HOME", ".local/state") / "gweb"),
        "credentials_dir": str(_xdg("XDG_CONFIG_HOME", ".config") / "gweb" / "credentials"),
    },
    "audio": {
        # "auto" = primo dispositivo di cattura USB; oppure ID scheda ALSA (es. "Device")
        # oppure PCM ALSA completo (es. "plughw:CARD=Device,DEV=0"). Mai l'indice numerico.
        "device": "auto",
        "sample_rate": 48000,
        "channels": 1,
        "sample_format": "S16_LE",
        "segment_seconds": 300,
        "capture_command": "arecord",
    },
    "recording": {
        "max_duration_minutes": 240,
        "min_free_mb": 500,
        "critical_free_mb": 150,
        "reconnect_timeout_seconds": 120,
        "resume_window_seconds": 180,
        "request_timeout_seconds": 60,
    },
    "encoding": {
        # Formato del file caricato su NotebookLM. I segmenti WAV originali restano sempre.
        "upload_format": "m4a",  # m4a | mp3 | wav
        "bitrate": "96k",
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
    },
    "upload": {
        "auto_after_stop": False,
        "title_template": "{title} - {date} [{id}]",
        "max_attempts": 8,
        "backoff_base_seconds": 30,
        "backoff_max_seconds": 3600,
        "request_timeout_seconds": 900,
        "processing_poll_seconds": 30,
        "processing_timeout_minutes": 180,
        "processing_error_grace_minutes": 20,
        "reconcile_settle_seconds": 120,
        "auth_recheck_minutes": 15,
    },
    "notebooklm": {
        "backend": "notebooklm-py",  # notebooklm-py | fake
        "profile": "default",
        "keepalive_minutes": 20,
        "timeout_seconds": 60,
    },
    "cleanup": {
        # Pulizia periodica (timer systemd) dei file audio la cui fonte è PRONTA su NotebookLM.
        "enabled": False,
        "schedule": "Sun *-*-* 03:30:00",  # sintassi OnCalendar di systemd
        "min_age_days": 7,                 # giorni dalla conferma "fonte pronta"
        "mode": "all",                     # all = tutto l'audio | segments = solo WAV, tiene l'M4A
    },
    "service": {
        "unit_name": "gweb.service",
        "poll_interval_seconds": 0.5,
        "heartbeat_stale_seconds": 15,
    },
}

VALID_FORMATS = {"m4a", "mp3", "wav"}


def config_file_path() -> Path:
    env = os.environ.get("GWEB_CONFIG")
    if env:
        return Path(env).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "gweb" / "config.toml"


def _merge(base: dict, override: dict, prefix: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key not in out:
            raise ConfigError(
                f"Chiave di configurazione sconosciuta: {prefix}{key}",
                hint="Confronta con examples/config.example.toml",
            )
        if isinstance(out[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"La sezione [{prefix}{key}] deve essere una tabella")
            out[key] = _merge(out[key], value, f"{prefix}{key}.")
        else:
            default = out[key]
            if isinstance(default, bool) and not isinstance(value, bool):
                raise ConfigError(f"{prefix}{key} deve essere true/false")
            if isinstance(default, (int, float)) and not isinstance(default, bool):
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ConfigError(f"{prefix}{key} deve essere un numero")
            if isinstance(default, str) and not isinstance(value, str):
                raise ConfigError(f"{prefix}{key} deve essere una stringa")
            out[key] = value
    return out


@dataclass
class Config:
    data: dict[str, dict[str, Any]]
    source_path: Path
    loaded_from_file: bool

    def section(self, name: str) -> dict[str, Any]:
        return self.data[name]

    def get(self, dotted: str) -> Any:
        section, key = dotted.split(".", 1)
        return self.data[section][key]

    # --- percorsi ----------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return Path(self.data["paths"]["data_dir"]).expanduser()

    @property
    def state_dir(self) -> Path:
        return Path(self.data["paths"]["state_dir"]).expanduser()

    @property
    def credentials_dir(self) -> Path:
        return Path(self.data["paths"]["credentials_dir"]).expanduser()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gweb.db"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def summaries_dir(self) -> Path:
        return self.data_dir / "summaries"

    @property
    def tests_dir(self) -> Path:
        return self.state_dir / "audio-tests"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "daemon.lock"

    @property
    def notebooklm_home(self) -> Path:
        """Directory passata a notebooklm-py come NOTEBOOKLM_HOME."""
        return self.credentials_dir / "notebooklm"

    @property
    def storage_state_path(self) -> Path:
        profile = self.data["notebooklm"]["profile"]
        return self.notebooklm_home / "profiles" / profile / "storage_state.json"

    @property
    def master_token_path(self) -> Path:
        return self.storage_state_path.with_name("master_token.json")

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.recordings_dir, self.summaries_dir, self.state_dir,
                     self.log_dir, self.tests_dir, self.credentials_dir):
            secure_mkdir(path)

    def validate(self) -> None:
        fmt = self.data["encoding"]["upload_format"]
        if fmt not in VALID_FORMATS:
            raise ConfigError(f"encoding.upload_format non valido: {fmt}",
                              hint=f"Valori ammessi: {', '.join(sorted(VALID_FORMATS))}")
        if self.data["notebooklm"]["backend"] not in {"notebooklm-py", "fake"}:
            raise ConfigError("notebooklm.backend deve essere 'notebooklm-py' o 'fake'")
        if self.data["cleanup"]["mode"] not in {"all", "segments"}:
            raise ConfigError("cleanup.mode deve essere 'all' o 'segments'")
        if self.data["cleanup"]["min_age_days"] < 0:
            raise ConfigError("cleanup.min_age_days deve essere >= 0")
        if self.data["audio"]["channels"] not in (1, 2):
            raise ConfigError("audio.channels deve essere 1 o 2")
        if self.data["audio"]["segment_seconds"] < 10:
            raise ConfigError("audio.segment_seconds deve essere >= 10")
        profile = self.data["notebooklm"]["profile"]
        if not profile or "/" in profile or profile.startswith("."):
            raise ConfigError("notebooklm.profile non valido")

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)


def secure_mkdir(path: Path) -> None:
    """Crea una directory con permessi 0700 (dati, audio, credenziali)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def load_config(path: Path | None = None) -> Config:
    path = path or config_file_path()
    override: dict = {}
    loaded = False
    if path.exists():
        try:
            with open(path, "rb") as fh:
                override = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Errore di sintassi in {path}: {exc}") from exc
        loaded = True
    data = _merge(DEFAULTS, override)
    cfg = Config(data=data, source_path=path, loaded_from_file=loaded)
    cfg.validate()
    return cfg


def example_config_text() -> str:
    """Testo del file di configurazione di esempio (senza credenziali)."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[2] / "examples" / "config.example.toml",):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    # Fallback per installazioni non editabili
    lines = ["# Configurazione gweb (generata). Tutte le chiavi sono opzionali.", ""]
    for section, values in DEFAULTS.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (int, float)):
                rendered = str(value)
            else:
                rendered = '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
            lines.append(f"# {key} = {rendered}")
        lines.append("")
    return "\n".join(lines)
