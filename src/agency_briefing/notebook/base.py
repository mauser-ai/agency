"""Interfaccia neutrale verso NotebookLM.

Registrazione, coda e CLI dipendono SOLO da questo modulo. Per sostituire notebooklm-py
(es. con un'API ufficiale) basta una nuova implementazione di ``NotebookBackend``.

Errori: le implementazioni devono convertire le proprie eccezioni in quelle di
``agency_briefing.errors`` (AuthRequired, TransientRemoteError, RateLimited,
UncertainUpload, RemoteNotFound, PermanentRemoteError).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


class SourceState:
    PREPARING = "preparing"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class NotebookInfo:
    id: str
    title: str
    sources_count: int | None = None
    modified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceInfo:
    id: str
    title: str | None
    status: str
    kind: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Citation:
    number: int | None
    source_id: str | None
    source_title: str | None
    excerpt: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Answer:
    text: str
    conversation_id: str | None
    citations: list[Citation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "conversation_id": self.conversation_id,
                "citations": [c.to_dict() for c in self.citations]}


@dataclass
class LocalAuthState:
    storage_path: str
    exists: bool
    mode: str | None = None
    secure_permissions: bool | None = None
    modified_at: str | None = None
    master_token_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotebookSession(ABC):
    """Sessione autenticata aperta. Tutte le operazioni sono di sola lettura tranne
    ``upload_audio`` e ``ask`` (che aggiunge un turno alla conversazione del notebook)."""

    @abstractmethod
    async def list_notebooks(self) -> list[NotebookInfo]: ...

    @abstractmethod
    async def get_notebook(self, notebook_id: str) -> NotebookInfo | None: ...

    @abstractmethod
    async def list_sources(self, notebook_id: str) -> list[SourceInfo]: ...

    @abstractmethod
    async def get_source(self, notebook_id: str, source_id: str) -> SourceInfo | None: ...

    @abstractmethod
    async def upload_audio(self, notebook_id: str, path: Path, *, title: str,
                           mime_type: str) -> SourceInfo:
        """Trasferisce il file. Il ritorno significa *upload completato*, NON fonte pronta."""

    @abstractmethod
    async def ask(self, notebook_id: str, question: str,
                  source_ids: list[str] | None = None) -> Answer: ...

    async def account_email(self) -> str | None:
        return None


class NotebookBackend(ABC):
    name = "abstract"

    @abstractmethod
    def local_auth_state(self) -> LocalAuthState: ...

    @abstractmethod
    def session(self) -> Any:
        """Async context manager che produce una ``NotebookSession``."""

    async def keepalive(self) -> None:
        """Rinnova i cookie di sessione (no-op se non necessario)."""
        async with self.session():
            return None

    def run(self, fn: Callable[[NotebookSession], Awaitable[T]]) -> T:
        """Esegue ``fn`` in una sessione da codice sincrono (CLI)."""

        async def _wrapper() -> T:
            async with self.session() as s:
                return await fn(s)

        return asyncio.run(_wrapper())


def recording_marker(recording_id: str) -> str:
    return f"[{recording_id}]"


def source_matches_recording(source: SourceInfo, recording_id: str,
                             filename: str | None = None) -> bool:
    """Riconosce la fonte di una registrazione: titolo con ``[ID]`` o nome file ``ID.ext``.

    Il secondo caso copre l'upload riuscito con rinomina fallita (notebooklm-py mantiene
    il nome del file se la rinomina best-effort non va a buon fine).
    """
    title = source.title or ""
    if recording_marker(recording_id) in title:
        return True
    if filename and title == filename:
        return True
    return title.startswith(recording_id + ".")


@asynccontextmanager
async def null_session(session: NotebookSession) -> AsyncIterator[NotebookSession]:
    yield session
