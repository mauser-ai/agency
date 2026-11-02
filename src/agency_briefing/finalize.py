"""Finalizzazione: dai segmenti/import al file da caricare. Idempotente e ripetibile."""

from __future__ import annotations

import logging
from pathlib import Path

from .audio.capture import list_segment_files
from .audio.encode import (
    PASSTHROUGH_MIME,
    EncodeError,
    encode_segments,
    probe_duration,
    transcode_file,
)
from .audio.wav import read_wav_info, repair_wav
from .config import Config
from .db import iso
from .models import Recording, RecState
from .recordings import RecordingStore

log = logging.getLogger(__name__)


def repair_segments(store: RecordingStore, rec: Recording) -> list[Path]:
    """Ripara le intestazioni e scarta i segmenti vuoti. Ritorna i segmenti validi."""
    valid: list[Path] = []
    for seg in list_segment_files(store.segments_dir(rec)):
        try:
            if repair_wav(seg):
                store.db.add_event("segment_repaired", f"Intestazione riparata: {seg.name}",
                                   level="warning", recording_id=rec.id)
            info = read_wav_info(seg)
        except (ValueError, OSError) as exc:
            store.db.add_event("segment_invalid", f"{seg.name} non leggibile: {exc}",
                               level="warning", recording_id=rec.id)
            continue
        if info.data_size_actual > 0:
            valid.append(seg)
    return valid


def finalize_recording(cfg: Config, store: RecordingStore, recording_id: str) -> Recording:
    rec = store.get(recording_id)
    store.update(rec.id, state=RecState.FINALIZING)
    enc = cfg.section("encoding")
    try:
        if rec.kind == "import":
            src = next((p for p in (Path(rec.dir_path) / "import").iterdir()
                        if not p.name.endswith(".part")), None)
            if src is None:
                raise EncodeError("File importato mancante")
            ext = src.suffix.lower()
            if ext == "." + enc["upload_format"] or ext in PASSTHROUGH_MIME:
                # Nessuna conversione inutile: formato già accettato.
                final = src
                mime = PASSTHROUGH_MIME.get(ext, "audio/wav")
            else:
                final, mime = transcode_file(src, Path(rec.dir_path) / rec.id,
                                             fmt=enc["upload_format"], bitrate=enc["bitrate"],
                                             ffmpeg=enc["ffmpeg"])
            duration = probe_duration(final, enc["ffprobe"])
        else:
            segments = repair_segments(store, rec)
            if not segments:
                store.update(rec.id, state=RecState.FAILED,
                             error=(rec.error or "") + " Nessun audio registrato.")
                return store.get(rec.id)
            duration = sum(read_wav_info(s).duration_seconds for s in segments)
            final, mime = encode_segments(segments, Path(rec.dir_path) / rec.id,
                                          fmt=enc["upload_format"], bitrate=enc["bitrate"],
                                          channels=int(rec.channels or cfg.get("audio.channels")),
                                          ffmpeg=enc["ffmpeg"])
    except EncodeError as exc:
        log.error("Finalizzazione %s fallita: %s", rec.id, exc.message)
        store.db.add_event("finalize_failed", exc.message, level="error", recording_id=rec.id)
        # Lo stato FAILED con segmenti presenti è recuperabile: 'gweb recordings finalize'.
        store.update(rec.id, state=RecState.FAILED, error=f"Finalizzazione fallita: {exc.message}")
        return store.get(rec.id)
    store.update(rec.id, state=RecState.READY, file_path=str(final), mime_type=mime,
                 file_size=final.stat().st_size,
                 duration_seconds=round(duration, 2) if duration else rec.duration_seconds,
                 ended_at=rec.ended_at or iso())
    store.db.add_event("finalized", f"File pronto: {final.name}", recording_id=rec.id)
    return store.get(rec.id)
