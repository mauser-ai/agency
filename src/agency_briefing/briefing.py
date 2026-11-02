"""Riepilogo strutturato di un briefing, calcolato da NotebookLM e salvato in locale.

Due domande:
1. limitata alla SOLA fonte della registrazione (``source_ids=[fonte]``);
2. solo se nel notebook esistono altre fonti pronte: confronto esplicito per individuare
   possibili richieste fuori preventivo, citando le fonti.

Non si inviano email e non si creano attività su servizi esterni.
"""

from __future__ import annotations

import json
from pathlib import Path

from .db import iso, utcnow
from .notebook.base import Answer, SourceState

PROMPT_VERSION = "briefing-v1"

SUMMARY_PROMPT = """Analizza ESCLUSIVAMENTE la fonte che contiene la registrazione del briefing «{title}».
Rispondi in italiano, in Markdown, con queste sezioni nell'ordine indicato:

## Sintesi del briefing
## Richieste del cliente
## Decisioni prese
## Attività emerse
## Responsabili e scadenze
Riporta responsabili e scadenze SOLO se sono dichiarati esplicitamente nella registrazione;
altrimenti scrivi "Nessuno indicato esplicitamente".
## Domande aperte

Regole: non inventare informazioni, non dedurre nomi o date non pronunciati;
se una sezione non ha contenuti scrivi "Nessun elemento emerso"; usa elenchi puntati."""

SCOPE_PROMPT = """Confronta le richieste emerse nel briefing «{title}» (fonte: la registrazione audio)
con le ALTRE fonti di questo notebook (preventivi, offerte, contratti, documenti di progetto).

## Possibili richieste fuori preventivo
Elenca solo le richieste del briefing che non risultano comprese nel perimetro descritto
nelle altre fonti, indicando per ciascuna la richiesta e il motivo, citando le fonti.
Se le altre fonti non descrivono un perimetro o un preventivo confrontabile, rispondi
esattamente: "Nessun confronto possibile: nessun preventivo o perimetro tra le fonti".
Non inventare voci di preventivo."""


def _answer_md(answer: Answer) -> str:
    lines = [answer.text.strip(), ""]
    if answer.citations:
        lines.append("**Citazioni**")
        for c in answer.citations:
            excerpt = (c.excerpt or "").replace("\n", " ").strip()
            if len(excerpt) > 300:
                excerpt = excerpt[:297] + "..."
            title = c.source_title or c.source_id or "?"
            lines.append(f"- [{c.number}] {title}: {excerpt}")
        lines.append("")
    return "\n".join(lines)


async def run_summary(session, *, notebook_id: str, source_id: str, title: str,
                      compare: str = "auto") -> dict:
    """compare: 'auto' (solo se ci sono altre fonti pronte), 'yes', 'no'."""
    summary = await session.ask(notebook_id, SUMMARY_PROMPT.format(title=title),
                                source_ids=[source_id])
    result: dict = {"summary": summary.to_dict(), "scope": None, "scope_skipped_reason": None}
    if compare == "no":
        result["scope_skipped_reason"] = "disattivato dall'utente"
        return result
    sources = await session.list_sources(notebook_id)
    others = [s for s in sources if s.id != source_id and s.status == SourceState.READY]
    if not others:
        result["scope_skipped_reason"] = "nessun'altra fonte pronta nel notebook"
        return result
    scope = await session.ask(notebook_id, SCOPE_PROMPT.format(title=title),
                              source_ids=[source_id] + [s.id for s in others])
    result["scope"] = scope.to_dict()
    result["compared_sources"] = [{"id": s.id, "title": s.title} for s in others]
    return result


def render_markdown(rec, notebook_title: str | None, result: dict) -> str:
    from .notebook.base import Answer, Citation

    def _ans(d: dict) -> Answer:
        return Answer(text=d["text"], conversation_id=d.get("conversation_id"),
                      citations=[Citation(**c) for c in d.get("citations", [])])

    out = [f"# Briefing: {rec.title}", "",
           f"- Registrazione: `{rec.id}`",
           f"- Notebook: {notebook_title or rec.notebook_id}",
           f"- Fonte: `{rec.remote_source_id}`",
           f"- Generato: {utcnow().astimezone().strftime('%Y-%m-%d %H:%M')}",
           f"- Prompt: {PROMPT_VERSION}", "",
           _answer_md(_ans(result["summary"]))]
    if result.get("scope"):
        out.append(_answer_md(_ans(result["scope"])))
    else:
        out += ["## Possibili richieste fuori preventivo", "",
                f"Confronto non eseguito: {result.get('scope_skipped_reason')}.", ""]
    return "\n".join(out)


def save_summary(db, summaries_dir: Path, rec, notebook_title: str | None,
                 result: dict) -> tuple[int, Path]:
    summaries_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")
    path = summaries_dir / f"{rec.id}-{stamp}.md"
    path.write_text(render_markdown(rec, notebook_title, result), encoding="utf-8")
    cur = db.execute(
        "INSERT INTO summaries(recording_id, notebook_id, source_id, created_at, prompt_version, "
        "content_json, file_path) VALUES(?,?,?,?,?,?,?)",
        (rec.id, rec.notebook_id, rec.remote_source_id, iso(), PROMPT_VERSION,
         json.dumps(result, ensure_ascii=False), str(path)))
    return int(cur.lastrowid), path
