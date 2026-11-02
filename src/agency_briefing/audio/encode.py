"""Unione dei segmenti e codifica del file da caricare (ffmpeg).

Perché si codifica invece di caricare il WAV:
- notebooklm-py 0.8.6 documenta (issue #2138) che un ``.wav`` si carica ma l'elaborazione
  termina in ``status=ERROR``; M4A/AAC e MP3 sono formati audio comuni e molto più piccoli;
- un'ora di WAV 48 kHz mono è ~345 MB, oltre il limite per fonte di NotebookLM;
  in AAC 96 kbit/s è ~43 MB.
I segmenti WAV originali non vengono mai cancellati.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..errors import AgencyError

FORMAT_ARGS = {
    "m4a": (["-c:a", "aac", "-movflags", "+faststart", "-f", "mp4"], "audio/mp4", ".m4a"),
    "mp3": (["-c:a", "libmp3lame", "-f", "mp3"], "audio/mpeg", ".mp3"),
    "wav": (["-c:a", "pcm_s16le", "-f", "wav"], "audio/wav", ".wav"),
}

PASSTHROUGH_MIME = {".m4a": "audio/mp4", ".mp3": "audio/mpeg"}


class EncodeError(AgencyError):
    code = "ENCODE_ERROR"


def ffmpeg_available(ffmpeg: str) -> bool:
    return shutil.which(ffmpeg) is not None


def _run(argv: list[str], timeout: float | None = None) -> None:
    try:
        # nice: la codifica non deve rubare CPU alla cattura in corso
        cmd = (["nice", "-n", "10"] if shutil.which("nice") else []) + argv
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise EncodeError(f"{argv[0]} non trovato", hint="sudo apt install ffmpeg") from exc
    if res.returncode != 0:
        raise EncodeError(f"ffmpeg ha fallito (codice {res.returncode}): "
                          f"{res.stderr.strip()[-500:]}")


def encode_segments(segments: list[Path], output_base: Path, *, fmt: str, bitrate: str,
                    channels: int, ffmpeg: str = "ffmpeg",
                    audio_filter: str = "") -> tuple[Path, str]:
    """Unisce ``segments`` in un unico file. Scrittura atomica (``.part`` + rename).

    ``audio_filter`` (catena ``-af`` di ffmpeg) si applica solo al file prodotto: sul Pi
    con l'HAT WM8960 il guadagno alto lascia un fruscio a ~-38 dBFS che il filtro di
    default porta a ~-55 dBFS senza toccare la voce.
    """
    if not segments:
        raise EncodeError("Nessun segmento audio da unire")
    args, mime, ext = FORMAT_ARGS[fmt]
    final = output_base.with_suffix(ext)
    tmp = final.with_name(final.name + ".part")
    list_file = output_base.with_name(output_base.name + ".concat.txt")
    with open(list_file, "w", encoding="utf-8") as fh:
        for seg in segments:
            escaped = str(seg.resolve()).replace("'", "'\\''")
            fh.write(f"file '{escaped}'\n")
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_file), "-vn", "-ac", str(channels)]
    if audio_filter:
        argv += ["-af", audio_filter]
    if fmt != "wav":
        argv += ["-b:a", bitrate]
    argv += args + [str(tmp)]
    _run(argv)
    with open(tmp, "rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    list_file.unlink(missing_ok=True)
    return final, mime


def transcode_file(src: Path, output_base: Path, *, fmt: str, bitrate: str,
                   ffmpeg: str = "ffmpeg") -> tuple[Path, str]:
    args, mime, ext = FORMAT_ARGS[fmt]
    final = output_base.with_suffix(ext)
    tmp = final.with_name(final.name + ".part")
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
            "-vn"]
    if fmt != "wav":
        argv += ["-b:a", bitrate]
    argv += args + [str(tmp)]
    _run(argv)
    os.replace(tmp, final)
    return final, mime


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float | None:
    if not shutil.which(ffprobe):
        return None
    try:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json",
             str(path)], capture_output=True, text=True, timeout=60)
        return float(json.loads(res.stdout)["format"]["duration"])
    except (subprocess.SubprocessError, KeyError, ValueError, json.JSONDecodeError):
        return None
