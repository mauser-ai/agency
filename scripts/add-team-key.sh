#!/usr/bin/env bash
# Autorizza un collega ad usare l'app Briefing con questo Raspberry.
# Il collega copia il "codice di accesso" dall'app (Impostazioni → Accesso al Raspberry);
# tu lo passi qui su stdin, dal tuo Mac:
#   pbpaste | ssh raspberry '~/agency-briefing/scripts/add-team-key.sh'
# La chiave viene aggiunta con restrict + gweb-ssh-guard: solo comandi dell'app, niente shell.
# Per toglierla: elimina la riga con il suo nome da ~/.ssh/authorized_keys.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HOME/.local/bin/gweb-ssh-guard"
mkdir -p "$HOME/.local/bin" "$HOME/.ssh"
ln -sf "$HERE/gweb-ssh-guard" "$GUARD"
chmod 700 "$HOME/.ssh"

key="$(head -c 4096 | tr -d '\r' | grep -E '^(ssh-ed25519|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+( .*)?$' | head -1 || true)"
if [[ -z "$key" ]]; then
  echo "Codice di accesso non valido: deve iniziare con 'ssh-ed25519 '." >&2
  exit 2
fi
blob="$(cut -d' ' -f2 <<< "$key")"
name="$(cut -d' ' -f3- <<< "$key")"
if grep -qF "$blob" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
  echo "Già autorizzato: ${name:-chiave senza nome}"
  exit 0
fi
printf 'restrict,command="%s" %s\n' "$GUARD" "$key" >> "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
echo "Autorizzato: ${name:-chiave senza nome} (solo comandi dell'app Briefing)"
