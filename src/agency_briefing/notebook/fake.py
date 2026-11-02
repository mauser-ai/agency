"""Backend finto, persistente su file JSON. Serve per test e prove senza account Google.

Attivazione: ``[notebooklm] backend = "fake"`` nel config.

Iniezione di guasti (solo test): variabile ``AGENCY_FAKE_FAIL`` con voci separate da
virgola ``modo[:quante_volte]``. Modi: ``auth``, ``network``, ``rate_limit``,
``timeout_before_upload``, ``timeout_after_upload`` (la fonte viene creata ma l'upload
risulta incerto), ``list_network`` (fallisce l'elenco fonti), ``processing_error``.
``AGENCY_FAKE_READY_AFTER``: secondi prima che una fonte diventi pronta (default 1).
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from ..errors import (
    AuthRequired,
    PermanentRemoteError,
    RateLimited,
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

DEFAULT_NOTEBOOKS = [
    {"id": "nb-demo-cliente-rossi", "title": "Cliente Rossi - nuovo sito", "sources": []},
    {"id": "nb-demo-interno", "title": "Agenzia - interno", "sources": []},
]


class FakeBackend(NotebookBackend):
    name = "fake"

    def __init__(self, state_path: Path):
        self.state_path = state_path

    @contextmanager
    def _locked(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(str(self.state_path) + ".lock", "a+")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if self.state_path.exists():
                state = json.loads(self.state_path.read_text())
            else:
                state = {"notebooks": json.loads(json.dumps(DEFAULT_NOTEBOOKS)), "fail": {},
                         "uploads": 0}
            yield state
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            os.replace(tmp, self.state_path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def state(self) -> dict:
        with self._locked() as s:
            return json.loads(json.dumps(s))

    def local_auth_state(self) -> LocalAuthState:
        return LocalAuthState(storage_path="(backend finto)", exists=True, mode="0o600",
                              secure_permissions=True)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[NotebookSession]:
        s = _FakeSession(self)
        s.maybe_fail("auth")
        yield s


class _FakeSession(NotebookSession):
    def __init__(self, backend: FakeBackend):
        self.b = backend

    def maybe_fail(self, mode: str) -> bool:
        spec = os.environ.get("AGENCY_FAKE_FAIL", "")
        for item in filter(None, (x.strip() for x in spec.split(","))):
            name, _, count = item.partition(":")
            if name != mode:
                continue
            with self.b._locked() as st:
                used = st["fail"].get(name, 0)
                limit = int(count) if count else 10**9
                if used >= limit:
                    return False
                st["fail"][name] = used + 1
            if mode == "auth":
                raise AuthRequired("Sessione finta scaduta")
            if mode in ("network", "list_network"):
                raise TransientRemoteError("Rete non raggiungibile (simulato)")
            if mode == "rate_limit":
                raise RateLimited("Troppe richieste (simulato)", retry_after=1)
            if mode == "timeout_before_upload":
                raise UncertainUpload("Timeout durante l'upload (simulato, nessuna fonte creata)")
            return True
        return False

    def _nb(self, st: dict, notebook_id: str) -> dict:
        for nb in st["notebooks"]:
            if nb["id"] == notebook_id:
                return nb
        raise RemoteNotFound(f"Notebook {notebook_id} non trovato")

    @staticmethod
    def _src(src: dict) -> SourceInfo:
        status = src["status"]
        if status == SourceState.PROCESSING and time.time() >= src["ready_at"]:
            status = SourceState.ERROR if src.get("will_fail") else SourceState.READY
        return SourceInfo(id=src["id"], title=src["title"], status=status, kind="audio",
                          created_at=src.get("created_at"))

    async def list_notebooks(self) -> list[NotebookInfo]:
        self.maybe_fail("network")
        st = self.b.state()
        return [NotebookInfo(id=n["id"], title=n["title"], sources_count=len(n["sources"]))
                for n in st["notebooks"]]

    async def get_notebook(self, notebook_id: str) -> NotebookInfo | None:
        st = self.b.state()
        for n in st["notebooks"]:
            if n["id"] == notebook_id:
                return NotebookInfo(id=n["id"], title=n["title"], sources_count=len(n["sources"]))
        return None

    async def list_sources(self, notebook_id: str) -> list[SourceInfo]:
        self.maybe_fail("network")
        self.maybe_fail("list_network")
        st = self.b.state()
        return [self._src(s) for s in self._nb(st, notebook_id)["sources"]]

    async def get_source(self, notebook_id: str, source_id: str) -> SourceInfo | None:
        self.maybe_fail("network")
        st = self.b.state()
        for s in self._nb(st, notebook_id)["sources"]:
            if s["id"] == source_id:
                return self._src(s)
        return None

    async def upload_audio(self, notebook_id: str, path: Path, *, title: str,
                           mime_type: str) -> SourceInfo:
        self.maybe_fail("network")
        self.maybe_fail("rate_limit")
        self.maybe_fail("timeout_before_upload")
        if not path.is_file():
            raise PermanentRemoteError(f"File non trovato: {path}")
        will_fail = self.maybe_fail("processing_error")
        ready_after = float(os.environ.get("AGENCY_FAKE_READY_AFTER", "1"))
        src = {"id": "src-" + uuid.uuid4().hex[:12], "title": title,
               "status": SourceState.PROCESSING, "ready_at": time.time() + ready_after,
               "will_fail": will_fail, "mime": mime_type, "bytes": path.stat().st_size,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with self.b._locked() as st:
            self._nb(st, notebook_id)["sources"].append(src)
            st["uploads"] = st.get("uploads", 0) + 1
        if self.maybe_fail("timeout_after_upload"):
            raise UncertainUpload("Timeout dopo l'invio (simulato, fonte creata)")
        return SourceInfo(id=src["id"], title=title, status=SourceState.PROCESSING, kind="audio")

    async def ask(self, notebook_id: str, question: str,
                  source_ids: list[str] | None = None) -> Answer:
        self.maybe_fail("network")
        sources = await self.list_sources(notebook_id)
        used = [s for s in sources if source_ids is None or s.id in source_ids]
        cits = [Citation(number=i + 1, source_id=s.id, source_title=s.title,
                         excerpt="(estratto simulato)") for i, s in enumerate(used[:2])]
        text = (f"[Risposta simulata dal backend finto] Domanda: {question[:120]}... "
                f"Fonti considerate: {len(used)} [1]")
        return Answer(text=text, conversation_id="conv-fake", citations=cits)

    async def account_email(self) -> str | None:
        return "demo@example.invalid"
