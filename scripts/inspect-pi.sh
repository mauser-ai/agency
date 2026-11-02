#!/usr/bin/env bash
# Ispezione del Raspberry Pi in SOLA LETTURA (nessuna modifica al sistema).
# Uso: ./scripts/inspect-pi.sh | tee ~/agency-inspect.txt
set -u

section() { printf '\n===== %s =====\n' "$*"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 || true; }

section "Directory e repository"
run pwd
run ls -la
[ -d .git ] && run git status --short --branch
[ -f AGENTS.md ] && echo "AGENTS.md presente"

section "Hardware"
run cat /proc/device-tree/model; echo
run uname -a
run getconf LONG_BIT
run dpkg --print-architecture
run free -h
run vcgencmd get_throttled

section "Sistema operativo"
run cat /etc/os-release
run python3 --version
run python3 -c 'import platform,struct;print(platform.machine(), struct.calcsize("P")*8, "bit")'

section "Spazio"
run df -h / "$HOME"

section "Audio"
run cat /proc/asound/cards
run cat /proc/asound/pcm
run arecord -l
run arecord -L
run lsusb
run id -nG

section "Strumenti"
for c in arecord ffmpeg ffprobe sox git uv pipx systemctl loginctl journalctl; do
  printf '%-10s %s\n' "$c" "$(command -v "$c" || echo '-')"
done
command -v ffmpeg >/dev/null && ffmpeg -hide_banner -encoders 2>/dev/null | grep -E ' aac | libmp3lame '

section "Servizi del progetto"
run systemctl --user status agency.service --no-pager
run loginctl show-user "$(id -un)" -p Linger
run ls -la ~/.config/agency ~/.local/share/agency 2>/dev/null
