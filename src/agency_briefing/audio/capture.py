"""Processo di cattura basato su ``arecord`` (alsa-utils).

Motivi della scelta:
- presente nei repository di Raspberry Pi OS, CPU trascurabile anche su ARMv7;
- con ``--max-file-time`` chiude un file WAV ogni N secondi: un crash fa perdere al
  massimo l'intestazione dell'ultimo segmento (riparabile), non l'intero briefing;
- su SIGINT/SIGTERM finalizza correttamente l'intestazione.

Nomi dei file: ogni avvio del processo (avvio iniziale o ripresa dopo scollegamento /
riavvio del servizio) usa un prefisso ``partNNN``. arecord rinomina il primo file in
``partNNN-01.wav`` quando apre il secondo; ``list_segment_files`` ordina in modo naturale.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
from pathlib import Path

from ..errors import DeviceError

_SEG_RE = re.compile(r"^part(\d+)(?:-(\d+))?\.wav$")


def build_capture_argv(command: str, pcm: str, *, sample_format: str, channels: int,
                       rate: int, segment_seconds: int | None, output: Path,
                       duration: int | None = None) -> list[str]:
    argv = shlex.split(command) + ["-q", "-D", pcm, "-f", sample_format, "-c", str(channels),
                                    "-r", str(rate), "-t", "wav"]
    if segment_seconds:
        argv += ["--max-file-time", str(segment_seconds)]
    if duration:
        argv += ["-d", str(duration)]
    argv.append(str(output))
    return argv


def segment_sort_key(path: Path) -> tuple[int, int]:
    m = _SEG_RE.match(path.name)
    if not m:
        return (10**9, 0)
    return (int(m.group(1)), int(m.group(2) or 0))


def list_segment_files(seg_dir: Path) -> list[Path]:
    files = [p for p in seg_dir.glob("part*.wav") if _SEG_RE.match(p.name)]
    return sorted(files, key=segment_sort_key)


class CaptureProcess:
    """Un'esecuzione di arecord che scrive ``partNNN*.wav`` in ``seg_dir``."""

    def __init__(self, argv: list[str], log_path: Path):
        self.argv = argv
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self._log = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab")
        try:
            self.proc = subprocess.Popen(
                self.argv, stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log,
                close_fds=True,
            )
        except FileNotFoundError as exc:
            self._log.close()
            raise DeviceError(
                f"Comando di cattura non trovato: {self.argv[0]}",
                hint="Installa alsa-utils: sudo apt install alsa-utils",
            ) from exc

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def poll(self) -> int | None:
        return self.proc.poll() if self.proc else 0

    def stop(self, timeout: float = 10.0) -> int | None:
        """Arresto controllato: SIGINT (arecord chiude l'intestazione), poi SIGKILL."""
        if not self.proc:
            return None
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGINT)
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except ProcessLookupError:
                pass
        if self._log:
            self._log.close()
            self._log = None
        return self.proc.returncode

    def tail_log(self, max_bytes: int = 600) -> str:
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                return fh.read().decode(errors="replace").strip()
        except OSError:
            return ""


def run_capture_once(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Cattura bloccante a durata fissa (usata da 'gweb audio test')."""
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout, text=True,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise DeviceError(f"Comando di cattura non trovato: {argv[0]}",
                          hint="Installa alsa-utils: sudo apt install alsa-utils") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceError("La cattura di prova non è terminata in tempo",
                          hint="Il dispositivo potrebbe essere bloccato o in uso") from exc
