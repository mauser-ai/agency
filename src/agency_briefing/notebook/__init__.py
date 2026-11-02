"""Integrazione NotebookLM dietro un adapter sostituibile."""

from __future__ import annotations

from ..config import Config
from .base import NotebookBackend


def create_backend(cfg: Config) -> NotebookBackend:
    backend = cfg.get("notebooklm.backend")
    if backend == "fake":
        from .fake import FakeBackend

        return FakeBackend(cfg.state_dir / "fake-notebooklm.json")
    from .notebooklm_py import NotebookLMPyBackend

    return NotebookLMPyBackend(
        home=cfg.notebooklm_home,
        storage_path=cfg.storage_state_path,
        timeout=float(cfg.get("notebooklm.timeout_seconds")),
        upload_timeout=float(cfg.get("upload.request_timeout_seconds")),
    )
