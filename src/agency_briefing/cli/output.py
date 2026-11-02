"""Rendering dell'output: tabelle leggibili oppure JSON stabile (``--json``)."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from ..db import parse_iso
from ..models import REC_STATE_LABEL, UPLOAD_STATUS_LABEL


def dump_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def local_time(value: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    dt = parse_iso(value) if value else None
    return dt.astimezone().strftime(fmt) if dt else "-"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def size(n: int | None) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def rec_state(state: str) -> str:
    return REC_STATE_LABEL.get(state, state)


def upload_state(state: str) -> str:
    return UPLOAD_STATUS_LABEL.get(state, state)


def table(rows: list[list[Any]], headers: list[str]) -> None:
    if not rows:
        click.echo("(nessun elemento)")
        return
    cells = [[str(c) if c is not None else "-" for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    widths = [min(w, 48) for w in widths]

    def fmt(row):
        out = []
        for i, c in enumerate(row):
            c = c if len(c) <= widths[i] else c[: widths[i] - 1] + "…"
            out.append(c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    click.secho(fmt(headers), bold=True)
    for r in cells:
        click.echo(fmt(r))


def kv(pairs: list[tuple[str, Any]]) -> None:
    width = max(len(k) for k, _ in pairs) if pairs else 0
    for k, v in pairs:
        click.echo(f"{k.ljust(width)}  {v if v not in (None, '') else '-'}")


def ok(msg: str) -> None:
    click.secho("✓ " + msg, fg="green")


def warn(msg: str) -> None:
    click.secho("! " + msg, fg="yellow", err=True)


def error(message: str, hint: str | None = None) -> None:
    click.secho(f"Errore: {message}", fg="red", err=True)
    if hint:
        click.echo(f"  → {hint}", err=True)


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
