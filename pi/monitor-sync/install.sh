#!/usr/bin/env bash
# Installa sul Raspberry la sync del lunedì del monitor (timer systemd utente).
#   ./install.sh            # installa/aggiorna e attiva il timer (lunedì 07:00)
# Il token va in ~/.config/monitor-sync/token (0600); l'impronta SHA-256 in full_sync.php.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/monitor-sync.service" <<EOF
[Unit]
Description=Sync settimanale del monitor business.gweb.agency
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HERE/monitor_sync.py
UMask=0077
EOF

cat > "$UNIT_DIR/monitor-sync.timer" <<'EOF'
[Unit]
Description=Sync del monitor ogni lunedì mattina

[Timer]
OnCalendar=Mon *-*-* 07:00:00
# Se il Pi era spento alle 7, parte appena si riaccende.
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now monitor-sync.timer
systemctl --user list-timers monitor-sync.timer --no-pager
[ -s "$HOME/.config/monitor-sync/token" ] || echo "ATTENZIONE: manca il token in ~/.config/monitor-sync/token"
