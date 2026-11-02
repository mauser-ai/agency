"""Pulizia dei file audio locali la cui fonte è già PRONTA su NotebookLM.

Regole di sicurezza (una registrazione viene toccata solo se TUTTE valgono):
1. registrazione in stato ``ready`` (non attiva, non in finalizzazione);
2. esiste un lavoro di upload ``succeeded`` con ID fonte;
3. sono passati almeno ``min_age_days`` giorni da quando la fonte è risultata pronta;
4. **verifica remota al momento della pulizia**: la fonte con quell'ID è presente nel
   notebook e ha stato READY. Se il notebook non esiste più o la fonte è sparita/non pronta,
   la registrazione viene saltata;
5. se NotebookLM non è raggiungibile o la sessione è scaduta, la pulizia si interrompe
   SENZA eliminare nulla (riproverà al prossimo giro).

Cosa viene eliminato: ``segments/*.wav`` (modalità ``segments``), più il file caricato e la
copia importata (modalità ``all``). Restano SEMPRE: riga nel DB, ``metadata.json``,
``capture.log``, riepiloghi. Il file originale fuori dall'archivio (``import`` da percorso
esterno) non viene mai toccato.

Nota: NotebookLM conserva la fonte (trascrizione interrogabile); non è garantito che
l'audio originale sia riscaricabile da lì. Con ``mode = "all"`` l'audio è perso.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from .db import iso, parse_iso, utcnow
from .errors import RemoteNotFound
from .models import JobState, Recording, RecState
from .notebook.base import SourceState

log = logging.getLogger(__name__)


@dataclass
class PruneItem:
    recording_id: str
    title: str
    notebook_id: str | None
    source_id: str | None
    files: list[str] = field(default_factory=list)
    bytes: int = 0
    action: str = "skip"          # delete | skip
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def files_to_delete(rec: Recording, mode: str, recordings_root: Path) -> list[Path]:
    rec_dir = Path(rec.dir_path)
    if not _inside(rec_dir, recordings_root):
        return []  # mai fuori dall'archivio
    paths: list[Path] = sorted((rec_dir / "segments").glob("*.wav"))
    if mode == "all":
        if rec.file_path:
            paths.append(Path(rec.file_path))
        paths += sorted(p for p in (rec_dir / "import").glob("*") if p.is_file())
    seen, out = set(), []
    for p in paths:
        if p.is_file() and _inside(p, rec_dir) and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def plan(app, *, mode: str, min_age_days: float) -> list[PruneItem]:
    """Fase locale: candidati e motivi di esclusione, senza rete."""
    items: list[PruneItem] = []
    cutoff = utcnow() - timedelta(days=min_age_days)
    for row in app.db.query("SELECT * FROM recordings ORDER BY created_at"):
        rec = Recording.from_row(row)
        files = files_to_delete(rec, mode, app.cfg.recordings_dir)
        if not files:
            continue  # niente da eliminare (già pulita)
        job_row = app.db.one(
            "SELECT * FROM jobs WHERE recording_id=? AND state=? AND remote_source_id IS NOT NULL "
            "ORDER BY (notebook_id = ?) DESC, id DESC LIMIT 1",
            (rec.id, JobState.SUCCEEDED, rec.notebook_id or ""))
        item = PruneItem(recording_id=rec.id, title=rec.title,
                         notebook_id=job_row["notebook_id"] if job_row else rec.notebook_id,
                         source_id=job_row["remote_source_id"] if job_row else None,
                         files=[str(p) for p in files],
                         bytes=sum(p.stat().st_size for p in files))
        if rec.state != RecState.READY:
            item.reason = f"registrazione in stato '{rec.state}'"
        elif job_row is None:
            item.reason = "non ancora caricata o fonte non pronta"
        else:
            ready_at = parse_iso(job_row["finished_at"]) or parse_iso(rec.ended_at)
            if ready_at and ready_at > cutoff:
                item.reason = (f"fonte pronta da meno di {min_age_days:g} giorni "
                               f"({ready_at.astimezone().strftime('%Y-%m-%d')})")
            else:
                item.action = "delete"  # provvisorio: manca la verifica remota
        items.append(item)
    return items


async def verify_remote(session, items: list[PruneItem]) -> None:
    """Conferma su NotebookLM. Errori di rete/auth si propagano: nessuna eliminazione."""
    by_nb: dict[str, list[PruneItem]] = {}
    for it in items:
        if it.action == "delete":
            by_nb.setdefault(it.notebook_id, []).append(it)
    for nb_id, group in by_nb.items():
        try:
            sources = {s.id: s for s in await session.list_sources(nb_id)}
        except RemoteNotFound:
            for it in group:
                it.action, it.reason = "skip", "notebook non trovato su NotebookLM"
            continue
        for it in group:
            src = sources.get(it.source_id)
            if src is None:
                it.action, it.reason = "skip", "fonte non più presente nel notebook"
            elif src.status != SourceState.READY:
                it.action, it.reason = "skip", f"fonte remota in stato '{src.status}'"
            else:
                it.reason = "fonte pronta su NotebookLM (verificato ora)"


def execute(app, items: list[PruneItem], mode: str) -> None:
    for it in items:
        if it.action != "delete":
            continue
        rec = app.store.get(it.recording_id)
        if rec.state != RecState.READY:  # cambiata nel frattempo
            it.action, it.reason = "skip", f"stato cambiato in '{rec.state}'"
            continue
        freed = 0
        for f in files_to_delete(rec, mode, app.cfg.recordings_dir):
            size = f.stat().st_size
            f.unlink()
            freed += size
        seg_dir = Path(rec.dir_path) / "segments"
        if seg_dir.is_dir() and not any(seg_dir.iterdir()):
            seg_dir.rmdir()
        scope = "all" if mode == "all" or rec.purged_scope == "all" else "segments"
        app.store.update(rec.id, purged_at=iso(), purged_scope=scope,
                         purged_bytes=(rec.purged_bytes or 0) + freed)
        app.db.add_event("purged", f"Eliminati {len(it.files)} file audio locali ({mode}); "
                         f"fonte {it.source_id} verificata pronta", recording_id=rec.id)
        it.bytes = freed
        log.info("Pulizia %s: %d byte liberati", rec.id, freed)


def render_timer_units(exe: str, schedule: str) -> tuple[str, str]:
    service = f"""[Unit]
Description=Agency: pulizia audio locali già pronti su NotebookLM

[Service]
Type=oneshot
ExecStart={exe} recordings prune --yes
UMask=0077
NoNewPrivileges=yes
"""
    timer = f"""[Unit]
Description=Agency: pulizia settimanale degli audio già su NotebookLM

[Timer]
OnCalendar={schedule}
Persistent=true
RandomizedDelaySec=15min

[Install]
WantedBy=timers.target
"""
    return service, timer
