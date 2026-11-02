#!/usr/bin/env python3
"""Sincronizzazione settimanale del monitor di business.gweb.agency dal Raspberry.

Ogni lunedì (timer systemd) avvia ``full_sync.php?week=<settimana precedente>&async=1``,
segue l'avanzamento su ``full_sync_status.php`` e salva l'esito in
``~/.local/state/monitor-sync/`` (``last.json`` + ``history.jsonl``).

Autenticazione: header ``X-Monitor-Token`` con il token in
``~/.config/monitor-sync/token`` (0600); sul server c'è solo la sua impronta SHA-256.

Uso: monitor_sync.py [--week N] [--dry-run]
Exit: 0 completata · 1 fallita sul server · 2 non avviata (rete, accesso, già occupato)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("MONITOR_BASE_URL", "https://business.gweb.agency/app/monitor/api")
TOKEN_FILE = Path.home() / ".config/monitor-sync/token"
STATE_DIR = Path.home() / ".local/state/monitor-sync"
POLL_SECONDS = 20
MAX_MINUTES = 90          # la sync completa dura di solito 10-20 minuti
BUSY_RETRIES = 6          # un'altra sync in corso: riprova ogni 5 minuti
BUSY_WAIT_SECONDS = 300


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


def previous_iso_week(today: dt.date | None = None) -> tuple[int, int]:
    year, week, _ = ((today or dt.date.today()) - dt.timedelta(days=7)).isocalendar()
    return year, week


def request(path: str, token: str | None = None, timeout: int = 60) -> tuple[int, dict]:
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": "gweb-monitor-sync/1"})
    if token:
        req.add_header("X-Monitor-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def save(result: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "last.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    with open(STATE_DIR / "history.jsonl", "a") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


def start(week: int, token: str) -> tuple[bool, str]:
    for attempt in range(1, BUSY_RETRIES + 1):
        code, body = request(f"full_sync.php?week={week}&async=1", token)
        if code == 200 and body.get("ok") and (body.get("started") or body.get("already_running")):
            return True, "già in corso" if body.get("already_running") else "avviata"
        if code == 409 and attempt < BUSY_RETRIES:
            log(f"Server occupato ({body.get('message', 'sync in corso')}): riprovo tra 5 minuti")
            time.sleep(BUSY_WAIT_SECONDS)
            continue
        return False, f"HTTP {code}: {body.get('message') or body.get('error') or 'risposta inattesa'}"
    return False, "server occupato troppo a lungo"


def follow(started_at: float) -> dict:
    deadline = started_at + MAX_MINUTES * 60
    last_label = None
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            _, st = request("full_sync_status.php", timeout=30)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log(f"Stato non leggibile ({exc}), riprovo")
            continue
        if st.get("current_label") != last_label:
            last_label = st.get("current_label")
            log(f"{st.get('progress_pct', 0)}% · {last_label}")
        if st.get("completed") or st.get("failed"):
            return st
    return {"failed": True, "error": f"nessun esito dopo {MAX_MINUTES} minuti"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", type=int, help="settimana ISO (default: la precedente)")
    ap.add_argument("--dry-run", action="store_true", help="mostra cosa farebbe, senza avviare")
    args = ap.parse_args()

    iso_year, week = previous_iso_week()
    week = args.week or week
    this_year = dt.date.today().year
    result = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "week": week, "iso_year": iso_year}
    if iso_year != this_year and not args.week:
        # full_sync.php usa l'anno corrente: lo segnaliamo invece di nasconderlo.
        log(f"Attenzione: la settimana {week} è del {iso_year}, il server userà il {this_year}")
        result["warning"] = "settimana dell'anno precedente"

    if args.dry_run:
        log(f"Avvierei full_sync.php?week={week}&async=1 su {BASE}")
        return 0
    try:
        token = TOKEN_FILE.read_text().strip()
    except OSError:
        log(f"Token mancante: {TOKEN_FILE}")
        return 2

    t0 = time.time()
    try:
        ok, message = start(week, token)
    except (urllib.error.URLError, TimeoutError) as exc:
        ok, message = False, f"rete: {exc}"
    log(f"Settimana {week}: sincronizzazione {message}")
    if not ok:
        save(result | {"ok": False, "stage": "start", "error": message})
        return 2

    st = follow(t0)
    duration = round(time.time() - t0)
    if st.get("completed"):
        log(f"Completata in {duration // 60} min")
        save(result | {"ok": True, "duration_seconds": duration})
        return 0
    error = st.get("error") or "errore"
    log(f"FALLITA: {st.get('current_label', '')} · {error} {st.get('error_body', '')[:200]}")
    save(result | {"ok": False, "stage": "sync", "failed_step": st.get("current_label"),
                   "error": error, "duration_seconds": duration})
    return 1


if __name__ == "__main__":
    sys.exit(main())
