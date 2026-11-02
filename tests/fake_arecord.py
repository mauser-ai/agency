#!/usr/bin/env python3
"""Simulatore di ``arecord`` per i test (nessun hardware audio necessario).

Supporta: -D -f -c -r -t -q -d --max-file-time FILE. Scrive un tono a 440 Hz in tempo
(accelerato da FAKE_ARECORD_SPEED). Come arecord: intestazione provvisoria, chiusa su
SIGINT/SIGTERM; con --max-file-time rinomina il primo file in NAME-01.wav.
Variabili: FAKE_ARECORD_FAIL=1 (esce subito con errore), FAKE_ARECORD_EXIT_AFTER=S
(simula microfono scollegato dopo S secondi), FAKE_ARECORD_SPEED (default 1).
"""

import math
import os
import signal
import struct
import sys
import time

args = sys.argv[1:]
opts = {"-c": "1", "-r": "48000", "-d": None, "--max-file-time": None}
out = None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-D", "-f", "-c", "-r", "-t", "-d", "--max-file-time"):
        opts[a] = args[i + 1]
        i += 2
        continue
    if a == "-q":
        i += 1
        continue
    out = a
    i += 1

if os.environ.get("FAKE_ARECORD_FAIL"):
    print("arecord: main:830: audio open error: No such file or directory", file=sys.stderr)
    sys.exit(1)

channels, rate = int(opts["-c"]), int(opts["-r"])
duration = float(opts["-d"]) if opts["-d"] else None
seg_time = float(opts["--max-file-time"]) if opts["--max-file-time"] else None
exit_after = float(os.environ.get("FAKE_ARECORD_EXIT_AFTER", "0") or 0)
speed = float(os.environ.get("FAKE_ARECORD_SPEED", "1"))
stop = False


def handler(_s, _f):
    global stop
    stop = True


signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGTERM, handler)

block = channels * 2


def header(fh, data_size):
    fh.seek(0)
    fh.write(b"RIFF" + struct.pack("<I", (36 + data_size) & 0xFFFFFFFF) + b"WAVE")
    fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, 16))
    fh.write(b"data" + struct.pack("<I", data_size & 0xFFFFFFFF))


base, ext = os.path.splitext(out)
filecount = 0


def open_file(n):
    if n == 0:
        name = out
    else:
        if n == 1:
            os.rename(out, f"{base}-01{ext}")
        name = f"{base}-{n + 1:02d}{ext}"
    fh = open(name, "wb")
    header(fh, 0x7FFFFFF0)  # provvisoria, come arecord
    return fh


fh = open_file(0)
written = 0
seg_written = 0
t0 = time.monotonic()
chunk = rate // 10
phase = 0
while not stop:
    frames = bytearray()
    for _ in range(chunk):
        v = int(8000 * math.sin(2 * math.pi * 440 * phase / rate))
        frames += struct.pack("<h", v) * channels
        phase += 1
    fh.write(frames)
    fh.flush()
    written += chunk
    seg_written += chunk
    elapsed_audio = written / rate
    if duration and elapsed_audio >= duration:
        break
    if exit_after and elapsed_audio >= exit_after:
        os._exit(1)  # simula scollegamento: intestazione non chiusa
    if seg_time and seg_written / rate >= seg_time:
        header(fh, seg_written * block)
        fh.close()
        filecount += 1
        fh = open_file(filecount)
        seg_written = 0
    time.sleep(max(0.0, 0.1 / speed - 0.002))

header(fh, seg_written * block)
fh.close()
sys.exit(0)
