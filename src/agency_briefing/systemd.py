"""Servizio systemd *utente* (nessun privilegio root a regime).

Scelta: unità utente + ``loginctl enable-linger`` (unico comando con sudo, una volta sola),
così il servizio parte all'avvio, sopravvive alla chiusura di SSH e si gestisce con
``systemctl --user`` senza sudo. Nessuna porta di rete aperta.
"""

from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_TEMPLATE = """[Unit]
Description=Agency briefing: registrazione e caricamento su NotebookLM
Documentation=file://{readme}
StartLimitIntervalSec=600
StartLimitBurst=10

[Service]
Type=simple
ExecStart={exec_path} service run
Restart=on-failure
RestartSec=5
# SIGTERM solo al processo principale: il demone chiude arecord con SIGINT
# (intestazione WAV finalizzata), poi systemd invia SIGKILL ai residui.
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=60
Environment=PYTHONUNBUFFERED=1
{config_env}UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
"""


def agency_executable() -> str:
    exe = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if exe and exe.name == "agency" and exe.exists():
        return str(exe)
    candidate = Path(sys.executable).with_name("agency")
    if candidate.exists():
        return str(candidate)
    found = shutil.which("agency")
    return found or str(candidate)


def unit_path(unit_name: str) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / unit_name


def render_unit(config_path: Path | None = None) -> str:
    readme = Path(__file__).resolve().parents[2] / "README.md"
    config_env = ""
    if os.environ.get("AGENCY_CONFIG") and config_path:
        config_env = f"Environment=AGENCY_CONFIG={config_path}\n"
    return UNIT_TEMPLATE.format(exec_path=agency_executable(), readme=readme,
                                config_env=config_env)


def _run(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def systemctl_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        res = _run(["systemctl", "--user", "show-environment"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def unit_state(unit_name: str) -> dict:
    info = {"systemd_user": systemctl_available(), "unit_file": str(unit_path(unit_name)),
            "installed": unit_path(unit_name).exists(), "active": None, "enabled": None,
            "linger": linger_enabled()}
    if info["systemd_user"]:
        info["active"] = _run(["systemctl", "--user", "is-active", unit_name]).stdout.strip()
        info["enabled"] = _run(["systemctl", "--user", "is-enabled", unit_name]).stdout.strip()
    return info


def linger_enabled() -> bool | None:
    if not shutil.which("loginctl"):
        return None
    try:
        res = _run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"], 5)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip() == "yes"


def install_unit(unit_name: str, config_path: Path | None, *, enable: bool = True,
                 start: bool = True) -> list[str]:
    """Scrive il file di unità (con backup se diverso) e lo abilita. Ritorna i passaggi."""
    steps: list[str] = []
    path = unit_path(unit_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_unit(config_path)
    if path.exists() and path.read_text() != content:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        steps.append(f"backup dell'unità esistente: {backup}")
    path.write_text(content)
    steps.append(f"unità scritta: {path}")
    if not systemctl_available():
        steps.append("systemd utente non disponibile in questa sessione: abilitazione saltata")
        return steps
    _run(["systemctl", "--user", "daemon-reload"])
    steps.append("systemctl --user daemon-reload")
    if enable:
        res = _run(["systemctl", "--user", "enable", unit_name])
        steps.append(f"systemctl --user enable {unit_name}: "
                     f"{'ok' if res.returncode == 0 else res.stderr.strip()}")
    if start:
        res = _run(["systemctl", "--user", "restart", unit_name])
        steps.append(f"systemctl --user restart {unit_name}: "
                     f"{'ok' if res.returncode == 0 else res.stderr.strip()}")
    return steps


def control(action: str, unit_name: str) -> subprocess.CompletedProcess:
    return _run(["systemctl", "--user", action, unit_name], timeout=90)


def logs_command(unit_name: str, lines: int, follow: bool) -> list[str] | None:
    if not shutil.which("journalctl"):
        return None
    argv = ["journalctl", "--user", "-u", unit_name, "-n", str(lines), "--no-pager", "-o",
            "short-iso"]
    if follow:
        argv.append("-f")
    return argv
