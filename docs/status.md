# Stato del progetto

Ultimo aggiornamento: 2026-11-02 · versione 0.2.0

## Ambiente in cui è stato sviluppato e verificato
Il codice è stato scritto e testato in un **container cloud x86_64 (Ubuntu 24.04, Python
3.13, ffmpeg presente, senza ALSA/arecord, senza systemd utente, senza microfono)**, NON sul
Raspberry Pi: la sessione SSH sul Pi non era ancora disponibile. Tutto ciò che dipende
dall'hardware o dall'account Google è quindi **non verificato** finché non lo si esegue sul
dispositivo (vedi "Da verificare sul Raspberry").

## Verificato sul Raspberry reale (2026-11-02)
Dispositivo: **Raspberry Pi 4 Model B Rev 1.5, 4 GB (3,7 GiB), aarch64, userland 64 bit**,
Debian 13 trixie (kernel 6.18 rpt), Python 3.13.5, `vcgencmd get_throttled` = 0x0,
SD 115 GB (87 GB liberi). Utente `riccardo` (non `pi`), host `DartsVision`, raggiunto via
`dartsvision.local`: `raspberrypi.local` non risolve su questa rete.
- `./scripts/inspect-pi.sh`: ok. `arecord`, `ffmpeg`/`ffprobe` (encoder `aac`), `sox`,
  systemd presenti; `uv`/`pipx` assenti (non necessari). Utente nel gruppo `audio`.
- `./scripts/install.sh`: ok e ripetibile (venv, `~/.local/bin/gweb`, config mantenuta,
  servizio riscritto e riavviato). Nessun `sudo` necessario: pacchetti già presenti e
  `Linger=yes` già attivo.
- `gweb.service` utente: attivo, abilitato, heartbeat del demone ok dopo il riavvio.
- `gweb doctor`: tutto ✓ (microfono compreso) tranne la sessione NotebookLM (assente:
  login dal Mac non ancora fatto).
- `pytest` sul Pi: 45 test passati in ~55 s; `ruff` pulito.
- **Microfono: HAT I2S Keyestudio 2-Mic Pi HAT V1.0** (clone del ReSpeaker 2-Mic v1.0,
  codec WM8960 a 0x1a su i2c-1, `dtoverlay=wm8960-soundcard` in
  `/boot/firmware/config.txt`; nessun microfono USB). Scheda ALSA `wm8960soundcard`,
  PCM `plughw:CARD=wm8960soundcard,DEV=0`, scelta da `auto` senza configurazione.
  Mixer di default: `Capture` 63/63 (+30 dB), boost di ingresso +20 dB.
- `gweb audio test --duration 10`: **ok**, 48 kHz mono via `plughw`; due prove: picco
  −14,7 / RMS −35,4 dBFS e picco −26,7 / RMS −40,5 dBFS (stanza, senza misura del parlato). Verificato anche dopo un
  riavvio a freddo (2026-11-02 19:03): codec, scheda, `gweb doctor` (microfono ✓,
  servizio e demone ✓) senza interventi manuali.
- Problema incontrato: per alcuni avvii il codec non rispondeva su I2C
  (`wm8960 1-001a: Failed to issue reset … error -5`, scansione di i2c-1 vuota) per un
  contatto dell'HAT non inserito bene; dopo il rimontaggio risponde. Se ricapita senza
  voler riavviare: `echo 1-001a | sudo tee /sys/bus/i2c/drivers/wm8960/bind` crea la
  scheda (il probe differito della `simple-audio-card` riparte da solo).
- Rimosso dal Pi un kiosk preesistente (Chromium a schermo intero al login, progetto
  "7 Campanili") che bloccava il desktop: non riguarda gweb, nessun impatto sul servizio.

Correzioni emerse sul Pi:
- **Permessi dei dati**: con l'umask utente di Debian (0002) i comandi CLI creavano le
  directory dati/stato 0775 e il DB nasceva 0644 prima del `chmod`, così `gweb.db-wal` e
  `gweb.db-shm` (che contengono i dati) restavano 0644. Ora `ensure_dirs` crea tutte le
  directory 0700 e il DB nasce 0600 (SQLite eredita i permessi per WAL/SHM); i file
  esistenti vengono corretti all'apertura. Verificato sul Pi dopo il riavvio: directory
  0700, `gweb.db*` 0600. Test: `test_data_dirs_and_db_files_private_with_permissive_umask`.
- `gweb audio test` stampava "parla nel microfono..." anche senza microfono: ora il
  messaggio compare solo dopo aver risolto il dispositivo e indica quale (`plughw:...`).
- `audio.device = "auto"` accettava solo microfoni USB, quindi ignorava l'HAT I2S: ora
  preferisce l'USB e, se assente, usa la prima scheda di cattura. Verificato sul Pi con
  l'HAT (`auto → plughw:CARD=wm8960soundcard,DEV=0`).
- `audio test` segnalava "possibile saturazione" per il click di accensione del WM8960
  (un solo campione a fondo scala nei primi millisecondi; il resto a −27/−30 dBFS di
  picco): l'analisi dei livelli del test ora esclude i primi 0,5 s. Test:
  `test_levels_skip_codec_startup_click`.

## Completato e verificato (test automatici: `pytest`, 45 test, ~25 s x86 / ~55 s Pi 4)
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
1. ~~Collegarsi al Pi: `./scripts/inspect-pi.sh`, `./scripts/install.sh`, `gweb doctor`.~~
   Fatto il 2026-11-02 (vedi sopra).
2. ~~`gweb audio test --duration 10`~~ fatto con l'HAT (vedi sopra). Da valutare in una
   registrazione vera se il guadagno al massimo (+30 dB) dà rumore di fondo eccessivo
   (`alsamixer -c wm8960soundcard`, poi `sudo alsactl store`).
3. Login dal Mac, `gweb notebook status`, `gweb notebook list` (sola lettura).
4. Prova di registrazione reale breve; upload su un notebook di prova **solo dopo conferma**.
5. Aggiornare questo file con gli esiti reali (formato accettato, tempi di elaborazione).
6. Poi: API locale autenticata per l'app Mac; pulsante/LED GPIO.
