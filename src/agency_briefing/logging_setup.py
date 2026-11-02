"""Configurazione log: stderr (journald) + file a rotazione, con redazione dei segreti."""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(r"(__Secure-[0-9A-Za-z_-]+|SID|HSID|SSID|APISID|SAPISID|OSID|LSID)"
               r"(\s*[=:]\s*)([^\s;,'\"]+)"),
    re.compile(r"(oauth_token|master_token|aas_et|Token|Authorization)(\s*[=:]\s*)"
               r"([^\s;,'\"]+)", re.IGNORECASE),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, ()
        return True


def setup_logging(log_dir: Path | None, level: int = logging.INFO, *,
                  to_stderr: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    flt = RedactingFilter()
    if to_stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        sh.addFilter(flt)
        root.addHandler(sh)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "agency.log", maxBytes=1_000_000,
                                                  backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(flt)
        root.addHandler(fh)
    # La libreria NotebookLM è verbosa a DEBUG: la teniamo a WARNING.
    logging.getLogger("notebooklm").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
