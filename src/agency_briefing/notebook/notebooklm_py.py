"""Implementazione di ``NotebookBackend`` con notebooklm-py 0.8.6 (libreria NON ufficiale).

API usate (verificate sul pacchetto installato, vedi docs/architecture.md):
- ``NotebookLMClient.from_storage(path=..., timeout=..., upload_timeout=httpx.Timeout)``
  come async context manager; all'uscita la libreria salva i cookie ruotati.
- ``client.notebooks.list()`` / ``client.notebooks.get_or_none(id)`` /
  ``client.notebooks.create(title)`` (invio unico; su perdita della risposta l'eccezione
  è marcata ``unconfirmed``)
- ``client.sources.list(nb)`` / ``client.sources.get_or_none(nb, sid)``
- ``client.sources.add_file(nb, path, mime_type, title=...)``: ritorna appena i byte sono
  trasferiti (``wait=False``); lo stato PROCESSING restituito è un segnaposto.
- ``client.chat.ask(nb, question, source_ids=[...])`` -> ``AskResult`` con ``references``
  (``ChatReference.source_id``, ``citation_number``, ``cited_text``).
- ``client.get_account_email(live_fallback=False)``
- Eccezioni: ``AuthError``, ``RateLimitError.retry_after``, ``NetworkError``,
  ``RPCTimeoutError``, ``ServerError``, ``NotFoundError``, ``ValidationError``,
  ``NotebookLMError.unconfirmed`` (scrittura con esito sconosciuto).
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..errors import (
    AuthRequired,
    PermanentRemoteError,
    RateLimited,
    RemoteError,
    RemoteNotFound,
    TransientRemoteError,
    UncertainUpload,
)
from .base import (
    Answer,
    Citation,
    LocalAuthState,
    NotebookBackend,
    NotebookInfo,
    NotebookSession,
    SourceInfo,
    SourceState,
)

log = logging.getLogger(__name__)

LIBRARY_VERSION = "0.8.6"


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


def _status_name(status) -> str:
    name = getattr(status, "name", str(status)).lower()
    return {
        "ready": SourceState.READY,
        "processing": SourceState.PROCESSING,
        "preparing": SourceState.PREPARING,
        "error": SourceState.ERROR,
    }.get(name, SourceState.UNKNOWN)


def _source(s) -> SourceInfo:
    kind = None
    try:
        kind = getattr(s.kind, "value", str(s.kind))
    except Exception:  # noqa: BLE001 - proprietà derivata, non critica
        pass
    return SourceInfo(id=s.id, title=s.title, status=_status_name(s.status), kind=kind,
                      created_at=_iso(getattr(s, "created_at", None)))


def _notebook(n) -> NotebookInfo:
    return NotebookInfo(id=n.id, title=n.title or "(senza titolo)",
                        sources_count=getattr(n, "sources_count", None),
                        modified_at=_iso(getattr(n, "modified_at", None)))


def _safe_message(exc: BaseException) -> str:
    # I messaggi della libreria sono già redatti; tronchiamo comunque.
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def map_exception(exc: BaseException, *, during_upload: bool = False) -> RemoteError:
    """Converte le eccezioni di notebooklm-py/httpx negli errori neutrali."""
    import httpx
    import notebooklm as nlm

    if isinstance(exc, RemoteError):
        return exc
    msg = _safe_message(exc)
    if isinstance(exc, FileNotFoundError):
        return AuthRequired("Nessuna sessione NotebookLM salvata su questo dispositivo")
    if isinstance(exc, (nlm.AuthError, nlm.AuthExtractionError)):
        return AuthRequired(f"Sessione NotebookLM non valida o scaduta ({msg})")
    headless = getattr(nlm.exceptions, "HeadlessReauthError", None)
    if headless and isinstance(exc, headless):
        return AuthRequired(f"Rinnovo automatico della sessione non riuscito ({msg})")
    source_id = getattr(exc, "source_id", None)
    unconfirmed = bool(getattr(exc, "unconfirmed", False))
    if during_upload and (unconfirmed or source_id):
        return UncertainUpload(f"Esito upload sconosciuto ({msg})", source_id=source_id)
    if isinstance(exc, nlm.RateLimitError):
        return RateLimited(f"Limite temporaneo del servizio ({msg})",
                           retry_after=getattr(exc, "retry_after", None))
    if isinstance(exc, nlm.NotFoundError):
        return RemoteNotFound(f"Risorsa non trovata su NotebookLM ({msg})")
    transient = (nlm.NetworkError, nlm.ServerError, httpx.TransportError, TimeoutError,
                 ConnectionError, nlm.WaitTimeoutError)
    if isinstance(exc, transient):
        if during_upload:
            # Il trasferimento può essere arrivato a destinazione: niente reinvio alla cieca.
            return UncertainUpload(f"Interruzione durante l'upload ({msg})", source_id=source_id)
        return TransientRemoteError(f"Errore di rete o del servizio ({msg})",
                                    hint="Verifica la connessione; la coda ritenterà.")
    if isinstance(exc, (nlm.ValidationError, nlm.ConfigurationError, nlm.ClientError,
                        nlm.SourceAddError, nlm.NotebookLimitError)):
        return PermanentRemoteError(f"Richiesta rifiutata da NotebookLM ({msg})")
    if isinstance(exc, nlm.NotebookLMError):
        if during_upload:
            return UncertainUpload(f"Errore durante l'upload ({msg})", source_id=source_id)
        return TransientRemoteError(f"Errore NotebookLM ({msg})")
    if during_upload:
        return UncertainUpload(f"Errore imprevisto durante l'upload ({msg})")
    return RemoteError(f"Errore imprevisto ({msg})")


class _Session(NotebookSession):
    def __init__(self, client):
        self.client = client

    async def _call(self, coro, *, during_upload: bool = False):
        try:
            return await coro
        except RemoteError:
            raise
        except Exception as exc:  # noqa: BLE001 - mappatura centralizzata
            raise map_exception(exc, during_upload=during_upload) from exc

    async def list_notebooks(self) -> list[NotebookInfo]:
        items = await self._call(self.client.notebooks.list())
        return [_notebook(n) for n in items]

    async def get_notebook(self, notebook_id: str) -> NotebookInfo | None:
        n = await self._call(self.client.notebooks.get_or_none(notebook_id))
        return _notebook(n) if n else None

    async def create_notebook(self, title: str) -> NotebookInfo:
        return _notebook(await self._call(self.client.notebooks.create(title)))

    async def list_sources(self, notebook_id: str) -> list[SourceInfo]:
        items = await self._call(self.client.sources.list(notebook_id))
        return [_source(s) for s in items]

    async def get_source(self, notebook_id: str, source_id: str) -> SourceInfo | None:
        s = await self._call(self.client.sources.get_or_none(notebook_id, source_id))
        return _source(s) if s else None

    async def upload_audio(self, notebook_id: str, path: Path, *, title: str,
                           mime_type: str) -> SourceInfo:
        s = await self._call(
            self.client.sources.add_file(notebook_id, path, mime_type, title=title),
            during_upload=True,
        )
        info = _source(s)
        # Il PROCESSING restituito è un segnaposto: lo stato reale si legge con get_source.
        if info.status == SourceState.READY:
            info.status = SourceState.PROCESSING
        return info

    async def ask(self, notebook_id: str, question: str,
                  source_ids: list[str] | None = None) -> Answer:
        result = await self._call(
            self.client.chat.ask(notebook_id, question, source_ids=source_ids))
        titles: dict[str, str | None] = {}
        if result.references:
            try:
                titles = {s.id: s.title for s in await self.list_sources(notebook_id)}
            except RemoteError:
                titles = {}
        citations = [
            Citation(number=r.citation_number, source_id=r.source_id,
                     source_title=titles.get(r.source_id), excerpt=r.cited_text)
            for r in (result.references or [])
        ]
        return Answer(text=result.answer or "", conversation_id=result.conversation_id,
                      citations=citations)

    async def account_email(self) -> str | None:
        try:
            return await self.client.get_account_email(live_fallback=False)
        except Exception:  # noqa: BLE001 - informazione accessoria
            return None


class NotebookLMPyBackend(NotebookBackend):
    name = "notebooklm-py"

    def __init__(self, *, home: Path, storage_path: Path, timeout: float = 60.0,
                 upload_timeout: float = 900.0):
        self.home = home
        self.storage_path = storage_path
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        # La libreria legge NOTEBOOKLM_HOME per profili e master token: lo isoliamo
        # nella directory credenziali del progetto (permessi 0700).
        os.environ["NOTEBOOKLM_HOME"] = str(home)
        os.environ.pop("NOTEBOOKLM_AUTH_JSON", None)  # mai credenziali da variabili d'ambiente

    def local_auth_state(self) -> LocalAuthState:
        p = self.storage_path
        if not p.exists():
            return LocalAuthState(storage_path=str(p), exists=False,
                                  master_token_present=p.with_name("master_token.json").exists())
        st = p.stat()
        mode = stat.S_IMODE(st.st_mode)
        return LocalAuthState(
            storage_path=str(p), exists=True, mode=oct(mode),
            secure_permissions=(mode & 0o077) == 0,
            modified_at=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            master_token_present=p.with_name("master_token.json").exists(),
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[NotebookSession]:
        import httpx
        from notebooklm import NotebookLMClient

        if not self.storage_path.exists():
            raise AuthRequired("Nessuna sessione NotebookLM salvata su questo dispositivo")
        upload_timeout = httpx.Timeout(connect=30.0, read=self.upload_timeout,
                                       write=self.upload_timeout, pool=60.0)
        ctx = NotebookLMClient.from_storage(path=str(self.storage_path), timeout=self.timeout,
                                            upload_timeout=upload_timeout)
        try:
            client = await ctx.__aenter__()
        except Exception as exc:  # noqa: BLE001
            raise map_exception(exc) from exc
        try:
            yield _Session(client)
        except BaseException as exc:
            await ctx.__aexit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - chiusura: non far fallire l'operazione
                log.warning("Chiusura sessione NotebookLM: %s", _safe_message(exc))
