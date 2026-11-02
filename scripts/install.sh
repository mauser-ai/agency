#!/usr/bin/env bash
# Installazione su Raspberry Pi (o altro Linux). Idempotente, non sovrascrive file esistenti.
#
#   ./scripts/install.sh              # venv + pacchetto + config + servizio
#   ./scripts/install.sh --with-apt   # installa anche alsa-utils, ffmpeg, python3-venv (sudo)
#   ./scripts/install.sh --no-service # senza systemd
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_DIR/.venv"
WITH_APT=0
WITH_SERVICE=1
for arg in "$@"; do
  case "$arg" in
    --with-apt) WITH_APT=1 ;;
    --no-service) WITH_SERVICE=0 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Opzione sconosciuta: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if [[ $WITH_APT -eq 1 ]]; then
  say "Pacchetti di sistema (solo quelli necessari, nessun upgrade generale)"
  sudo apt-get install -y --no-install-recommends alsa-utils ffmpeg python3-venv
fi

missing=()
for cmd in arecord ffmpeg ffprobe python3; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if (( ${#missing[@]} )); then
  echo "Mancano: ${missing[*]}" >&2
  echo "Installa con: sudo apt-get install -y alsa-utils ffmpeg python3-venv" >&2
  echo "(oppure rilancia con --with-apt)" >&2
  [[ " ${missing[*]} " == *" python3 "* ]] && exit 1
fi

PY=python3
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Serve Python >= 3.10 (trovato $($PY --version)). Su Raspberry Pi OS Bullseye usa uv:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.11" >&2
  exit 1
fi

say "Ambiente virtuale in $VENV"
[[ -d "$VENV" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e "$REPO_DIR"

say "Collegamento comando in ~/.local/bin/gweb"
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/gweb" "$HOME/.local/bin/gweb"

CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/gweb/config.toml"
if [[ -f "$CONFIG" ]]; then
  say "Configurazione esistente mantenuta: $CONFIG"
else
  "$VENV/bin/gweb" config init
fi

if ! id -nG | tr ' ' '\n' | grep -qx audio; then
  echo "ATTENZIONE: l'utente $(id -un) non è nel gruppo 'audio':" >&2
  echo "  sudo usermod -aG audio $(id -un)   (poi esci e rientra)" >&2
fi

if [[ $WITH_SERVICE -eq 1 ]]; then
  say "Servizio systemd utente"
  "$VENV/bin/gweb" service install
  if [[ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true)" != "yes" ]]; then
    echo "Per avviare il servizio al boot senza login (una volta sola):"
    echo "  sudo loginctl enable-linger $(id -un)"
  fi
fi

say "Verifica"
"$VENV/bin/gweb" doctor || true
echo
echo "Prossimo passo: gweb notebook login   (istruzioni per il login dal Mac)"
