"""Utilità WAV senza dipendenze: riparazione intestazione, durata, livelli.

Se il processo di cattura viene ucciso (SIGKILL, mancanza di corrente) l'intestazione
RIFF può riportare una dimensione errata. I campioni PCM sono comunque su disco:
``repair_wav`` ricalcola le dimensioni dalla lunghezza reale del file.
"""

from __future__ import annotations

import array
import math
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WavInfo:
    channels: int
    sample_rate: int
    bits_per_sample: int
    data_offset: int
    data_size_header: int
    data_size_actual: int

    @property
    def block_align(self) -> int:
        return self.channels * self.bits_per_sample // 8

    @property
    def duration_seconds(self) -> float:
        if not self.block_align or not self.sample_rate:
            return 0.0
        return self.data_size_actual / self.block_align / self.sample_rate

    @property
    def header_ok(self) -> bool:
        return self.data_size_header == self.data_size_actual


def read_wav_info(path: Path) -> WavInfo:
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"{path} non è un file WAV")
        fmt = None
        while True:
            chunk = fh.read(8)
            if len(chunk) < 8:
                raise ValueError(f"{path}: chunk 'data' mancante")
            cid, csize = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if cid == b"fmt ":
                body = fh.read(csize)
                _tag, channels, rate, _br, _ba, bits = struct.unpack("<HHIIHH", body[:16])
                fmt = (channels, rate, bits)
                if csize % 2:
                    fh.read(1)
            elif cid == b"data":
                if fmt is None:
                    raise ValueError(f"{path}: chunk 'fmt' mancante")
                offset = fh.tell()
                block = fmt[0] * fmt[2] // 8 or 1
                actual = (size - offset) // block * block
                return WavInfo(fmt[0], fmt[1], fmt[2], offset, csize, actual)
            else:
                fh.seek(csize + (csize % 2), os.SEEK_CUR)


def repair_wav(path: Path) -> bool:
    """Corregge le dimensioni RIFF/data. Ritorna True se il file è stato modificato."""
    info = read_wav_info(path)
    if info.header_ok:
        return False
    total = info.data_offset + info.data_size_actual
    with open(path, "r+b") as fh:
        fh.truncate(total)  # elimina un eventuale frammento di campione incompleto
        fh.seek(4)
        fh.write(struct.pack("<I", total - 8))
        fh.seek(info.data_offset - 4)
        fh.write(struct.pack("<I", info.data_size_actual))
        fh.flush()
        os.fsync(fh.fileno())
    return True


def write_wav_header(fh, channels: int, rate: int, bits: int, data_size: int) -> None:
    block = channels * bits // 8
    fh.write(b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE")
    fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, bits))
    fh.write(b"data" + struct.pack("<I", data_size))


@dataclass
class Levels:
    peak_dbfs: float
    rms_dbfs: float
    duration_seconds: float

    @property
    def verdict(self) -> str:
        if self.peak_dbfs <= -60:
            return "silenzio: microfono muto o guadagno a zero"
        if self.peak_dbfs >= -0.5:
            return "possibile saturazione: riduci il guadagno"
        if self.rms_dbfs < -45:
            return "segnale molto basso: avvicina il microfono o alza il guadagno"
        return "ok"

    def to_dict(self) -> dict:
        return {"peak_dbfs": round(self.peak_dbfs, 1), "rms_dbfs": round(self.rms_dbfs, 1),
                "duration_seconds": round(self.duration_seconds, 2), "verdict": self.verdict}


def _db(value: float, full_scale: float) -> float:
    return 20 * math.log10(value / full_scale) if value > 0 else -120.0


def analyze_levels(path: Path, skip_seconds: float = 0.0) -> Levels:
    """Picco e RMS in dBFS per WAV PCM 16 bit (formato usato dal registratore).

    ``skip_seconds`` esclude l'inizio del file: alcuni codec (WM8960 sugli HAT I2S)
    producono un click di accensione a fondo scala che falserebbe il picco.
    """
    info = read_wav_info(path)
    if info.bits_per_sample != 16:
        raise ValueError("Analisi livelli supportata solo per PCM 16 bit")
    skip = int(skip_seconds * info.sample_rate) * info.block_align
    if skip >= info.data_size_actual:
        skip = 0
    peak = 0
    sq = 0.0
    count = 0
    with open(path, "rb") as fh:
        fh.seek(info.data_offset + skip)
        remaining = info.data_size_actual - skip
        while remaining > 0:
            buf = fh.read(min(remaining, 1 << 16))
            if not buf:
                break
            remaining -= len(buf)
            samples = array.array("h")
            samples.frombytes(buf[: len(buf) // 2 * 2])
            if sys.byteorder == "big":
                samples.byteswap()
            if samples:
                peak = max(peak, max(samples), -min(samples))
                sq += sum(s * s for s in samples)
                count += len(samples)
    rms = math.sqrt(sq / count) if count else 0.0
    return Levels(_db(peak, 32768), _db(rms, 32768), info.duration_seconds)
