# monitor-sync — sync del lunedì del monitor (Raspberry)

Ogni lunedì alle 07:00 il Raspberry avvia la sincronizzazione completa del monitor di
`business.gweb.agency` per la **settimana ISO precedente**, come il link usato a mano
(`/app/monitor/api/full_sync.php?week=N&async=1`), ne segue l'avanzamento e salva l'esito.

## Come funziona
- `monitor_sync.py` (solo libreria standard Python): avvia con l'header `X-Monitor-Token`,
  segue `full_sync_status.php` ogni 20 s (max 90 min), riprova fino a 30 min se il server è
  già occupato da un'altra sync.
- Esito in `~/.local/state/monitor-sync/last.json` e storico in `history.jsonl`; log con
  `journalctl --user -u monitor-sync`.
- Timer systemd utente `monitor-sync.timer` (`Persistent=true`: se il Pi era spento alle 7
  parte alla riaccensione).

## Token
Generato sul Raspberry in `~/.config/monitor-sync/token` (0600). Sul server, in
`full_sync.php`, c'è solo la sua impronta SHA-256 (`MONITOR_AUTOMATION_TOKEN_SHA256`): il
token non passa mai in chiaro altrove. Per cambiarlo: rigenera il file, poi aggiorna
l'impronta (`printf %s "$(cat ~/.config/monitor-sync/token)" | sha256sum`) e ricarica
`full_sync.php`.

## Installazione / comandi
```bash
cd ~/monitor-sync && ./install.sh
```
```bash
python3 ~/monitor-sync/monitor_sync.py --dry-run      # cosa farebbe
python3 ~/monitor-sync/monitor_sync.py --week 44      # sync manuale di una settimana
systemctl --user list-timers monitor-sync.timer        # prossima esecuzione
cat ~/.local/state/monitor-sync/last.json              # ultimo esito
```

## Limiti noti
- `full_sync.php` usa l'anno corrente: il primo lunedì di gennaio la settimana precedente
  (52/53) appartiene all'anno prima. Lo script lo segnala nell'esito (`warning`).
- Gli endpoint chiamati da `full_sync.php` (`teamleaderApi/dashboard/...`) e alcune API del
  monitor (`kpi.php`, `cruscotto.php`, `cache_rebuild_local.php`) non hanno controlli di
  accesso nel codice: da proteggere sul server.
