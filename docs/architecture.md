# Architettura

## Componenti

| # | Componente | Modulo | Responsabilità |
|---|---|---|---|
| 1 | Acquisizione audio | `audio/devices.py`, `audio/capture.py`, `audio/wav.py` | Scoperta microfono per ID scheda ALSA, processo `arecord` a segmenti, riparazione WAV, livelli |
| 2 | Gestione registrazioni | `recordings.py`, `finalize.py`, `audio/encode.py` | Metadati SQLite, struttura su disco, import, unione/codifica |
| 3 | Integrazione NotebookLM | `notebook/base.py` (interfaccia), `notebook/notebooklm_py.py`, `notebook/fake.py` | Adapter sostituibile, errori neutrali |
| 4 | Coda persistente | `queue.py` | Lavori di upload, retry, riconciliazione, anti-duplicati, verifica elaborazione |
| 5 | CLI | `cli/main.py`, `cli/output.py` | Client sottile di `AgencyApp` |
| 6 | Servizio | `daemon.py`, `recorder.py`, `systemd.py` | Demone supervisionato da systemd utente |
| — | Logica condivisa | `app.py` (`AgencyApp`) | Unico punto d'ingresso per CLI e futura API |

## Separazione di codice, configurazione, dati, credenziali

| Cosa | Percorso (default XDG) | Permessi |
|---|---|---|
| Codice + venv | `~/agency-briefing`, `~/agency-briefing/.venv` | utente |
| Configurazione | `~/.config/agency/config.toml` | utente |
| Credenziali NotebookLM | `~/.config/agency/credentials/notebooklm/profiles/default/storage_state.json` | dir 0700, file 0600 |
| Dati | `~/.local/share/agency/` (`agency.db`, `recordings/`, `summaries/`) | DB 0600, UMask 0077 nel servizio |
| Stato/log | `~/.local/state/agency/` (`logs/agency.log`, `daemon.lock`, `audio-tests/`) | utente |
| Unità systemd | `~/.config/systemd/user/agency.service` | utente |

Lo stato mutabile scelto da CLI (notebook predefinito, microfono) è nella tabella `settings`
del DB, così la CLI non riscrive mai `config.toml`.

## Flussi

### Registrazione
1. `agency record start` → verifica demone vivo (heartbeat < 15 s) e spazio → `INSERT`
   in `recordings` con stato `requested` e notebook risolto **in quel momento** (copia di
   ID e titolo: le modifiche successive al predefinito non la toccano). L'indice unico
   parziale `ux_one_active_recording` impedisce due registrazioni attive.
2. Il demone (tick 0,5 s) prende la richiesta, risolve il microfono (`auto` = prima scheda
   USB con cattura; mai l'indice numerico), avvia
   `arecord -D plughw:CARD=<id>,DEV=0 -f S16_LE -c 1 -r 48000 -t wav --max-file-time 300 partNNN.wav`.
   Se il processo muore entro 0,5 s → stato `failed` con il messaggio di arecord.
3. Monitoraggio ogni tick: heartbeat (2 s), richiesta di stop, durata massima, spazio (10 s,
   soglia critica → stop con audio conservato), processo terminato → evento
   `capture_lost`, riconnessione ogni 3 s se la scheda ricompare (nuovo `partNNN+1`), oltre
   `reconnect_timeout_seconds` → chiusura `device_lost`.
4. `agency record stop` → `stop_requested=1` → SIGINT ad arecord (chiude l'intestazione) →
   `finalizing` → thread finalizer: riparazione intestazioni, ffmpeg concat+AAC → file
   `<ID>.m4a` scritto come `.part` e rinominato atomicamente → `ready`.
5. Con `upload.auto_after_stop = true` la finalizzazione accoda l'upload.

### Riavvii e crash
- SIGTERM del servizio (restart/arresto): segmento chiuso bene, stato resta `recording`.
  Al riavvio, se l'ultimo heartbeat è entro `resume_window_seconds` (180 s) la registrazione
  **riprende** su un nuovo segmento (evento `resume`); altrimenti viene finalizzata con
  l'audio disponibile e l'errore "Registrazione interrotta…". Nota: un riavvio rapido del
  Pi durante un briefing quindi riprende a registrare.
- SIGKILL / mancanza di corrente: le intestazioni WAV non chiuse vengono riparate
  (`wav.repair_wav`) ricalcolando le dimensioni dalla lunghezza del file; si perde al massimo
  l'ultimo frammento non scritto su disco.
- Richieste `requested` più vecchie di 60 s all'avvio → `failed` (niente avvii a sorpresa).

### Upload e coda
Macchina a stati in `queue.py`. Punti chiave:
- **Upload completato ≠ fonte pronta**: `processing` = byte trasferiti e ID fonte noto;
  `succeeded` = `sources.get_or_none(...).status == READY`. In output: "caricata, in
  elaborazione" vs "fonte pronta".
- Prima di ogni invio: elenco fonti e ricerca del marcatore `[<ID registrazione>]` nel
  titolo (il titolo remoto è `"{title} - {date} [{id}]"`) o del nome file `<ID>.m4a`
  (caso rinomina fallita: notebooklm-py mantiene il nome del file).
- Errore durante il trasferimento (timeout, rete, `NotebookLMError.unconfirmed`) →
  `uncertain`: nessun reinvio finché due riconciliazioni a distanza di
  `reconcile_settle_seconds` non confermano l'assenza. Se l'elenco fonti non è
  raggiungibile il lavoro resta `uncertain` e visibile; risoluzione manuale:
  `agency jobs resolve ID --source-id SRC` oppure `--not-uploaded`.
- Lavoro `running` trovato all'avvio del demone → `uncertain` (`INTERRUPTED`).
- Sessione scaduta (`AuthError`) → `blocked_auth` senza consumare tentativi; si sblocca
  quando cambia `storage_state.json` (import di una nuova sessione) o ogni 15 min.
- Rate limit → attesa `retry_after`; rete/5xx → attesa progressiva 30 s·2ⁿ (max 1 h, +10%
  jitter), massimo 8 tentativi; errori di validazione → `failed`.
- Elaborazione: polling ogni 30 s; `ERROR` tollerato per 20 min (l'audio può riportare
  ERROR transitorio durante la trascrizione), poi `failed/PROCESSING_FAILED`; oltre 180 min
  `failed/PROCESSING_TIMEOUT` (ricontrollabile con `jobs retry`).

### Pulizia dell'audio già su NotebookLM
`agency recordings prune` (manuale) o `agency-cleanup.timer` (systemd utente, di default
domenica 03:30, `Persistent=true` = recupera il giro perso se il Pi era spento). Una
registrazione viene ripulita solo se: stato `ready`, lavoro `succeeded` con ID fonte, fonte
pronta da ≥ `min_age_days` (7) e **verifica remota al momento** (`list_sources` del notebook:
fonte presente e READY). Notebook sparito o fonte rimossa/non pronta → mantenuta. Rete o
sessione in errore → interruzione senza eliminare nulla. Modalità `all` (tutto l'audio) o
`segments` (solo WAV, tiene l'M4A). Restano riga DB (`purged_at`, `purged_scope`,
`purged_bytes`), `metadata.json`, eventi, riepiloghi; i percorsi sono verificati interni
all'archivio. Una registrazione ripulita con `all` non è più caricabile né rifinalizzabile.
Rischio accettato con `all`: non è garantito che NotebookLM permetta di riscaricare l'audio.

### Riepilogo
`agency briefing summarize ID`: richiede lavoro con fonte pronta (verifica remota se non
ancora `succeeded`). Domanda 1 con `source_ids=[fonte della registrazione]` (sezioni:
sintesi, richieste, decisioni, attività, responsabili/scadenze solo se espliciti, domande
aperte). Domanda 2 **solo se** esistono altre fonti `READY`: confronto per possibili
richieste fuori preventivo, con istruzione di rispondere "Nessun confronto possibile" se
non c'è un perimetro. Risultato in `summaries` (JSON completo) e
`~/.local/share/agency/summaries/<ID>-<timestamp>.md`.

## Decisioni tecniche

| Decisione | Motivazione |
|---|---|
| **notebooklm-py 0.8.6** (bloccata con `==`) | Ultima versione su PyPI al 2026-11-02. Libreria non ufficiale: aggiornamenti solo dopo verifica dell'API. |
| Cattura con `arecord` | In Raspberry Pi OS, CPU trascurabile anche su ARMv7, finalizza il WAV su SIGINT; nessuna dipendenza Python nativa. |
| `plughw:CARD=<id>` | L'indice della scheda cambia; `plughw` converte frequenza/canali se il microfono non supporta 48 kHz mono. |
| Segmenti WAV da 5 min | Un crash compromette al massimo un segmento (riparabile). PCM grezzo = nessuna perdita di qualità durante la cattura. |
| Upload in **M4A/AAC 96 kbit/s** | La documentazione di notebooklm-py 0.8.6 (issue #2138, `docs/python-api.md`) riporta che un `.wav` si carica ma l'elaborazione termina in `status=ERROR`. Inoltre 1 h WAV 48 kHz mono ≈ 345 MB contro ≈ 43 MB in AAC. La conversione è quindi necessaria, non estetica. MP3 disponibile (`upload_format = "mp3"`). |
| Import MP3/M4A senza conversione | Formati già adatti: nessuna conversione inutile. Altri formati (WAV, OGG, FLAC…) → codificati. |
| SQLite (WAL) + filesystem | Nessuna ragione emersa per altro: un solo nodo, scritture rare, transazioni e vincoli di unicità tra processi. |
| CLI ↔ demone via DB | Nessun socket/porta: richieste durevoli, sopravvivono a riavvii, testabili; la futura API userà gli stessi metodi di `AgencyApp`. |
| systemd **utente** + linger | Nessun root a regime; `systemctl --user` senza sudo; unico comando sudo: `loginctl enable-linger`. |
| Backend `fake` | Prove complete (coda, duplicati, riepilogo) senza caricare nulla sull'account reale. |
| Login sul Mac + `--import -` via SSH | Il login Google richiede un browser (Playwright). Il Pi è headless; la libreria documenta la copia di `storage_state.json` su server headless. Trasferimento via stdin SSH: cifrato, nessun file temporaneo, scritto 0600 atomicamente. |
| Pulizia con verifica remota, disattivata di default | Eliminazione irreversibile: la regola "è già su NotebookLM" va verificata nel momento in cui si elimina, non dedotta dal DB locale; la si attiva con una riga di config. Timer systemd invece del demone: esecuzione visibile (`systemctl --user list-timers`), log separati, recupero dei giri persi. |
| Keepalive cookie ogni 20 min | La documentazione della libreria raccomanda 15–20 min per profili inattivi (rotazione `__Secure-1PSIDTS`); aprire/chiudere il client salva i cookie ruotati. |

## API notebooklm-py verificate (0.8.6)
Verificate leggendo il pacchetto installato e la documentazione del tag v0.8.6:
- `NotebookLMClient.from_storage(path, timeout, upload_timeout=httpx.Timeout, ...)` come
  async context manager; `FileNotFoundError` se il file manca.
- `client.notebooks.list()`, `client.notebooks.get_or_none(id)` → `Notebook(id, title,
  sources_count, modified_at, …)`.
- `client.sources.list(nb)`, `client.sources.get_or_none(nb, sid)` → `Source(id, title,
  status: SourceStatus{UNKNOWN,PROCESSING,READY,ERROR,PREPARING}, kind, …)`.
- `client.sources.add_file(nb, path, mime_type, *, wait=False, title=None)`: ritorna dopo il
  trasferimento; `PROCESSING` restituito è un segnaposto; una rinomina fallita mantiene il
  nome file; le eccezioni dopo la registrazione portano `source_id`.
- `client.chat.ask(nb, question, source_ids=None, conversation_id=None)` → `AskResult(answer,
  conversation_id, references: list[ChatReference(source_id, citation_number, cited_text…)])`.
  Senza `conversation_id` prosegue la conversazione corrente del notebook.
- `client.get_account_email(live_fallback=False)`.
- Eccezioni: `AuthError`, `AuthExtractionError`, `RateLimitError.retry_after`,
  `NetworkError`, `RPCTimeoutError`, `ServerError`, `NotFoundError`, `ValidationError`,
  `SourceAddError`, `NotebookLMError.unconfirmed`.
- Autenticazione headless: copia di `storage_state.json` (o `NOTEBOOKLM_AUTH_JSON`, non
  usato qui); opzionale `master_token.json` accanto allo storage (extra `[headless]`) per il
  re-mint automatico — vedi rischi nel README.

Non usati di proposito: `chat.delete_conversation` e `ask --new` (distruttivi), download di
artefatti, generazione audio overview, API Android.

## Pronto per il futuro (non implementato)
- **API per l'app Mac**: aggiungere un modulo `api/` (es. FastAPI) che chiama `AgencyApp`,
  in ascolto su socket Unix o su localhost/Tailscale con autenticazione a token; nessun
  cambiamento a registrazione/coda.
- **Pulsante fisico e LED**: un thread nel demone (gpiozero) che crea richieste come la CLI
  (`store.create_mic_request` / `stop_requested`) e legge lo stato per il LED.
- **Display KPI / gestionale / fatturazione / dashboard**: componenti separati; i KPI
  calcolati dai dati del gestionale, NotebookLM/AI solo per interpretarli.
