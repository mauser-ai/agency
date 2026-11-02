"""Scoperta dei dispositivi di cattura ALSA.

Si leggono ``/proc/asound/cards`` e ``/proc/asound/pcm`` invece di affidarsi all'indice
numerico della scheda, che può cambiare tra un avvio e l'altro o ricollegando l'USB.
Il dispositivo viene indirizzato per **ID scheda** (es. ``plughw:CARD=Device,DEV=0``).
``plughw`` lascia ad ALSA la conversione di frequenza/canali se il microfono non supporta
nativamente il formato richiesto (molti microfoni USB sono solo 44.1/48 kHz).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from ..errors import DeviceError

_CARD_RE = re.compile(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(\S+)\s+-\s+(.*)$")
_PCM_RE = re.compile(r"^(\d+)-(\d+):\s*([^:]*):\s*([^:]*)(?::(.*))?$")


def asound_root() -> Path:
    return Path(os.environ.get("GWEB_ASOUND_ROOT", "/proc/asound"))


@dataclass
class CaptureDevice:
    card_index: int
    card_id: str
    driver: str
    name: str
    long_name: str
    device: int
    pcm_name: str
    is_usb: bool

    @property
    def alsa_pcm(self) -> str:
        return f"plughw:CARD={self.card_id},DEV={self.device}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["alsa_pcm"] = self.alsa_pcm
        return data


def list_capture_devices(root: Path | None = None) -> list[CaptureDevice]:
    root = root or asound_root()
    cards_file = root / "cards"
    if not cards_file.exists():
        return []
    cards: dict[int, dict] = {}
    lines = cards_file.read_text(errors="replace").splitlines()
    for i, line in enumerate(lines):
        m = _CARD_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        long_name = lines[i + 1].strip() if i + 1 < len(lines) else ""
        driver = m.group(3)
        cards[idx] = {
            "card_id": m.group(2),
            "driver": driver,
            "name": m.group(4).strip(),
            "long_name": long_name,
            "is_usb": driver == "USB-Audio" or (root / f"card{idx}" / "usbid").exists()
            or "usb" in long_name.lower(),
        }
    devices: list[CaptureDevice] = []
    pcm_file = root / "pcm"
    if pcm_file.exists():
        for line in pcm_file.read_text(errors="replace").splitlines():
            m = _PCM_RE.match(line.strip())
            if not m:
                continue
            rest = " ".join(p for p in (m.group(4), m.group(5) or "") if p)
            if "capture" not in rest:
                continue
            card_idx, dev = int(m.group(1)), int(m.group(2))
            card = cards.get(card_idx)
            if card is None:
                continue
            devices.append(CaptureDevice(card_index=card_idx, device=dev,
                                         pcm_name=m.group(3).strip(), **card))
    else:  # fallback: directory pcmNc
        for idx, card in cards.items():
            for pcm_dir in sorted((root / f"card{idx}").glob("pcm*c")):
                dev = int(pcm_dir.name[3:-1])
                devices.append(CaptureDevice(card_index=idx, device=dev, pcm_name="",
                                             **card))
    return devices


def resolve_device(spec: str, root: Path | None = None) -> tuple[str, CaptureDevice | None]:
    """Converte la configurazione in un PCM ALSA. Ritorna (pcm, dispositivo o None).

    - ``auto``: primo dispositivo di cattura USB
    - stringa con ``:`` o nomi ALSA noti: usata così com'è (es. ``plughw:CARD=Device,DEV=0``)
    - altrimenti: ID scheda o parte del nome
    """
    spec = (spec or "auto").strip()
    if ":" in spec or spec in {"default", "sysdefault", "pulse", "null"}:
        match = None
        m = re.search(r"CARD=([^,]+)", spec)
        if m:
            match = next((d for d in list_capture_devices(root) if d.card_id == m.group(1)),
                         None)
        return spec, match
    devices = list_capture_devices(root)
    if spec == "auto":
        usb = [d for d in devices if d.is_usb]
        if not usb:
            raise DeviceError(
                "Nessun microfono USB di cattura trovato",
                hint="Collega il microfono e verifica con 'gweb audio devices' "
                     "(o 'arecord -l'). Puoi anche impostare audio.device manualmente.",
                details={"devices": [d.to_dict() for d in devices]},
            )
        return usb[0].alsa_pcm, usb[0]
    exact = [d for d in devices if d.card_id == spec]
    if not exact:
        exact = [d for d in devices if spec.lower() in (d.name + " " + d.long_name).lower()]
    if not exact:
        raise DeviceError(
            f"Dispositivo di cattura '{spec}' non presente",
            hint="Microfono scollegato? Controlla con 'gweb audio devices'.",
        )
    return exact[0].alsa_pcm, exact[0]


def device_present(pcm: str, root: Path | None = None) -> bool:
    """Verifica se la scheda indicata nel PCM è presente (per la riconnessione)."""
    m = re.search(r"CARD=([^,]+)", pcm)
    if not m:
        return True  # PCM generico: non verificabile, lasciamo provare arecord
    return any(d.card_id == m.group(1) for d in list_capture_devices(root))
