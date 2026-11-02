# Stato del progetto

Ultimo aggiornamento: 2026-11-02 · versione 0.2.0

## Ambiente in cui è stato sviluppato e verificato
Il codice è stato scritto e testato in un **container cloud x86_64 (Ubuntu 24.04, Python
3.13, ffmpeg presente, senza ALSA/arecord, senza systemd utente, senza microfono)**, NON sul
Raspberry Pi: la sessione SSH sul Pi non era ancora disponibile. Tutto ciò che dipende
dall'hardware o dall'account Google è quindi **non verificato** finché non lo si esegue sul
dispositivo (vedi "Da verificare sul Raspberry").

## Completato e verificato (test automatici: `pytest`, 33 test, ~25 s)
- Configurazione TOML con default, validazione di chiavi e tipi, `config init/show/path`.
- Scoperta microfono da `/proc/asound` (finto) per **ID scheda**, `auto` = primo USB,
  errore operativo se assente.
- WAV: riparazione intestazioni non chiuse, durata, livelli dBFS, ordinamento naturale dei
  segmenti generati da `arecord --max-file-time`.
- Ciclo completo con demone reale e `arecord` simulato: avvio, stato, stop, unione di più
  segmenti, codifica M4A/AAC (verificata con ffprobe), stato "non caricata" (upload esplicito).
- Protezione avvii concorrenti (indice SQLite) e istanza unica del demone (file lock).
- Microfono scollegato e ricollegato → ripresa su nuovo segmento; scollegato
  definitivamente → chiusura `device_lost` con audio conservato.
- Riavvio del servizio durante la registrazione (SIGTERM) → ripresa automatica; crash con
  heartbeat vecchio → finalizzazione con intestazione riparata.
- Demone come processo separato con SIGTERM reale (prova manuale end-to-end, vedi sotto).
- Coda: persistenza tra istanze, retry con attesa progressiva, rate limit, errore
  permanente + retry manuale, sessione scaduta (`blocked_auth` senza consumare tentativi),
  timeout dopo l'invio → riconciliazione senza duplicati, timeout prima dell'invio → doppia
  verifica di assenza prima di reinviare, riconciliazione impossibile → resta `uncertain`
  visibile + `jobs resolve`, lavoro interrotto da crash → `uncertain`, errore di elaborazione
  → `failed/PROCESSING_FAILED`, duplicato rifiutato con exit 4.
- Distinzione upload completato (`processing`) / fonte pronta (`succeeded`).
- Associazione notebook per registrazione indipendente dal predefinito.
- Riepilogo: rifiutato se non pronto (exit 8), salvato in DB e Markdown, confronto fuori
  preventivo solo con altre fonti pronte.
- CLI come processo: help, `--json`, codici di uscita 2/3/4/6/9, import credenziali da stdin
  con permessi 0600/0700 e senza stampa del contenuto, istruzioni di login.
- Adapter notebooklm-py: superficie API verificata sul pacchetto 0.8.6 installato;
  mappatura eccezioni testata; senza sessione `notebook status` → exit 5.
- Redazione di cookie/token nei log.
- Pulizia `recordings prune`: simulazione senza effetti, eliminazione solo dopo verifica
  remota READY, modalità `all`/`segments`, esclusione per età minima, fonte rimossa dal
  notebook, rete assente o sessione scaduta (nessuna eliminazione), metadati e riepiloghi
  conservati, ricaricamento rifiutato, migrazione schema v1→v2, unità timer generate.

Prova manuale end-to-end eseguita (backend fake, arecord simulato): `service run` →
`notebook select rossi` → `record start` → SIGTERM al demone → riavvio (ripresa) →
`record stop --wait-final` → `recordings upload --wait` → `jobs list` (in elaborazione) →
`briefing summarize` → file `.m4a` AAC 48 kHz mono di 79,4 s, DB 0600, credenziali 0700.

## NON verificato (richiede il Raspberry o l'account)
| Cosa | Come verificarlo |
|---|---|
| Modello, RAM, architettura (32/64 bit), OS e Python del Pi | `./scripts/inspect-pi.sh`, `gweb doctor` |
| `arecord` reale col microfono USB, formati supportati, nome della scheda | `gweb audio devices`, `gweb audio test --duration 10` |
| Riconnessione con scollegamento fisico dell'USB | registra, scollega 10 s, ricollega, `gweb recordings show ID` (eventi) |
| Tempo di codifica AAC sul Pi 4 per registrazioni lunghe | `gweb record stop --wait-final` dopo 30–60 min |
| systemd utente, linger, avvio al boot, `service logs` | `gweb service install`, `sudo reboot`, `gweb service status` |
| Login dal Mac e import della sessione | procedura README, poi `gweb notebook status` |
| `notebook list/sources`, `ask` con citazioni reali | sola lettura prima |
| Upload audio reale, tempi di elaborazione, accettazione M4A da NotebookLM | **solo su tua richiesta esplicita**, su un notebook di prova |
| Comportamento della rinomina del titolo (`[ID]`) sull'upload reale | `gweb notebook sources` dopo il primo upload |
| Timer di pulizia settimanale (`gweb-cleanup.timer`) | `cleanup.enabled = true`, `gweb service install`, `systemctl --user list-timers` |
| Durata reale dei cookie con keepalive ogni 20 min | `gweb notebook status` nei giorni successivi |

## Limitazioni note
- notebooklm-py è non ufficiale: API e formati possono cambiare; versione bloccata a 0.8.6.
- La compatibilità di NotebookLM con M4A è dedotta (formato audio comune; il WAV ha un
  problema documentato nella libreria) ma **non provata** su questo account.
- `ask` e `briefing summarize` aggiungono turni alla conversazione corrente del notebook
  (la libreria permette una conversazione nuova solo cancellando quella esistente: non lo
  facciamo).
- La limitazione del riepilogo alla singola fonte usa `source_ids`; il confronto usa la
  fonte + le altre fonti pronte. La qualità dipende da NotebookLM.
- Il titolo remoto contiene `[ID]` per la riconciliazione: se lo rinomini a mano nel
  notebook, la riconciliazione automatica non lo riconosce (resta `jobs resolve`).
- Un riavvio del Pi entro 180 s riprende la registrazione (voluto); oltre, finalizza.
- Nessuna API di rete per l'app Mac (prevista, vedi architecture.md).
- Gli audio vengono eliminati solo dalla pulizia (disattivata di default). Con
  `mode = "all"` l'audio originale non è recuperabile se NotebookLM non lo rende
  riscaricabile (non verificato).

## Prossimi passi
1. Collegarsi al Pi: `./scripts/inspect-pi.sh`, `./scripts/install.sh`, `gweb doctor`.
2. `gweb audio test --duration 10`, scegliere il microfono se serve.
3. Login dal Mac, `gweb notebook status`, `gweb notebook list` (sola lettura).
4. Prova di registrazione reale breve; upload su un notebook di prova **solo dopo conferma**.
5. Aggiornare questo file con gli esiti reali (formato accettato, tempi di elaborazione).
6. Poi: API locale autenticata per l'app Mac; pulsante/LED GPIO.
