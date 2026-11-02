# agency-briefing

Registratore di briefing per **Raspberry Pi 4 + microfono USB** che carica le registrazioni
come fonti audio nei notebook **NotebookLM** e li interroga (domande con citazioni, riepilogo
strutturato). Tutto da terminale con il comando `gweb`; continua a funzionare chiudendo
SSH o spegnendo il Mac (servizio systemd). Nessun modello AI locale.

> L'integrazione usa [notebooklm-py](https://github.com/teng-lin/notebooklm-py) 0.8.6,
> libreria **non ufficiale** basata su API Google non documentate: può smettere di
> funzionare senza preavviso. È isolata dietro un adapter (`src/agency_briefing/notebook/`).

Documentazione: [architettura](docs/architecture.md) · [comandi](docs/commands.md) ·
[stato e limiti](docs/status.md) · [contesto per agenti](AGENTS.md)

## Requisiti
- Raspberry Pi OS (Bookworm o successivo; 32 o 64 bit), Python ≥ 3.10
- `alsa-utils` (arecord), `ffmpeg` (codifica AAC), `python3-venv`, `git`
- Microfono USB; utente nel gruppo `audio`
- Sul Mac (solo per il login Google): Python 3 e un browser via Playwright

## Installazione sul Raspberry

Tutti i comandi di questa sezione vanno eseguiti **sul Raspberry**, collegato via SSH
dal Mac (sostituisci utente e host con i tuoi):

```bash
ssh pi@raspberrypi.local
```

### 1. Scaricare il progetto

```bash
git clone https://github.com/mauser-ai/agency.git ~/agency-briefing
```

La cartella si chiama `~/agency-briefing`: tutti i percorsi di questo README la danno per
scontata. Aggiornamenti successivi: `cd ~/agency-briefing && git pull && ./scripts/install.sh`.

### 2. Ispezione (facoltativa, sola lettura)

```bash
cd ~/agency-briefing && ./scripts/inspect-pi.sh | tee ~/gweb-inspect.txt
```

Mostra modello, RAM, architettura, sistema, Python, spazio, schede audio e strumenti
installati, senza modificare nulla.

### 3. Pacchetti di sistema

Solo quelli necessari, nessun aggiornamento generale:

```bash
sudo apt-get install -y --no-install-recommends alsa-utils ffmpeg python3-venv
```

### 4. Installazione del programma e del servizio

```bash
./scripts/install.sh
```

`install.sh` crea `.venv`, installa il pacchetto, collega `~/.local/bin/gweb`, crea
`~/.config/gweb/config.toml` se assente (non sovrascrive), installa e avvia il servizio
utente `gweb.service` ed esegue `gweb doctor`. È ripetibile senza danni.

### 5. Avvio automatico al boot

Unico comando con sudo, una volta sola: fa partire il servizio anche senza login e lo
tiene attivo quando chiudi SSH.

```bash
sudo loginctl enable-linger "$USER"
```

### 6. Verifica del microfono

```bash
gweb doctor
```

```bash
gweb audio devices
```

```bash
gweb audio test --duration 10
```

Se c'è più di una scheda audio o la scelta automatica non è quella giusta, fissa il
microfono con il suo ID scheda (colonna `ID SCHEDA`):

```bash
gweb audio select Device
```

Se `gweb` non viene trovato (sessioni SSH non di login), apri una nuova sessione oppure usa
il percorso completo `~/agency-briefing/.venv/bin/gweb`.

### 7. Configurazione (facoltativa)

Il file è `~/.config/gweb/config.toml` (esempio commentato completo:
`examples/config.example.toml`). Funziona già con i valori predefiniti; le opzioni che
più probabilmente vorrai cambiare:

| Sezione | Chiave | Default | A cosa serve |
|---|---|---|---|
| `[upload]` | `auto_after_stop` | `false` | `true` = carica su NotebookLM appena fermi la registrazione |
| `[cleanup]` | `enabled` | `false` | `true` = pulizia settimanale dell'audio già pronto su NotebookLM |
| `[cleanup]` | `mode` | `"all"` | `"segments"` = tiene l'M4A, elimina solo i WAV |
| `[recording]` | `max_duration_minutes` | `240` | arresto automatico di sicurezza |
| `[recording]` | `min_free_mb` | `500` | spazio minimo per avviare; per briefing di 2 ore usa almeno `2000` |
| `[encoding]` | `bitrate` | `"96k"` | `"64k"` per file più piccoli con connessioni lente |

Dopo ogni modifica, applica e controlla:

```bash
gweb service install
```

```bash
gweb config show
```

## Login NotebookLM dal Mac (via SSH)

Il login Google richiede un browser, quindi si fa sul Mac e si trasferisce **solo** il
file di sessione `storage_state.json`. Non incollare mai password o cookie in chat o nel
terminale del Pi. `gweb notebook login` sul Pi stampa questa stessa procedura con il tuo
utente/host.

Sul Mac, una tantum:

```bash
python3 -m venv ~/.venvs/notebooklm
```

```bash
~/.venvs/notebooklm/bin/pip install "notebooklm-py[browser]==0.8.6"
```

```bash
~/.venvs/notebooklm/bin/playwright install chromium
```

Login (si apre Chromium; completa l'accesso Google, anche 2FA):

```bash
~/.venvs/notebooklm/bin/notebooklm login
```

Trasferimento cifrato via SSH, senza file temporanei sul Pi (scritto con permessi 0600):

```bash
ssh pi@raspberrypi.local '~/agency-briefing/.venv/bin/gweb notebook login --import -' < ~/.notebooklm/profiles/default/storage_state.json
```

Elimina la copia sul Mac, così un solo client usa (e ruota) quei cookie:

```bash
rm ~/.notebooklm/profiles/default/storage_state.json
```

Sul Pi, verifica di sola lettura:

```bash
gweb notebook status
```

**Sessione scaduta**: `notebook status` esce con codice 5 e i caricamenti passano a
"attende login" senza consumare tentativi. Ripeti login e import dal Mac: la coda riparte
da sola appena il file cambia. Il servizio rinnova i cookie ogni 20 minuti (come
raccomandato dalla libreria) per ridurre le scadenze.

**Opzionale – master token** (`notebooklm login --master-token --account EMAIL` sul Mac,
`pip install -e ".[headless]"` sul Pi, `gweb notebook login --import-master-token -`):
permette il rinnovo automatico anche dopo la scadenza dei cookie, ma è una credenziale
**dell'intero account Google, durevole, che sopravvive al cambio password**. Usala solo con
un account dedicato e valuta il rischio; revoca da Google Account → Sicurezza → Dispositivi.

## Uso tipico

```bash
gweb notebook list
```

```bash
gweb notebook select
```

```bash
gweb record start --title "Briefing nuovo sito"
```

Puoi chiudere SSH: la registrazione continua. Più tardi:

```bash
gweb record status
```

```bash
gweb record stop --wait-final
```

```bash
gweb recordings list
```

```bash
gweb recordings upload RECORDING_ID --wait
```

```bash
gweb jobs list
```

Quando lo stato è "fonte pronta" (upload completato ≠ fonte pronta: NotebookLM deve
trascrivere l'audio, può richiedere minuti):

```bash
gweb briefing summarize RECORDING_ID
```

```bash
gweb ask --recording RECORDING_ID "Quali decisioni sono state prese?"
```

Il riepilogo è salvato in `~/.local/share/gweb/summaries/`. Per caricare automaticamente
dopo lo stop: `auto_after_stop = true` nella sezione `[upload]` del config.

## Dove sono i dati

| Cosa | Percorso |
|---|---|
| Config | `~/.config/gweb/config.toml` (esempio: `examples/config.example.toml`) |
| Sessione NotebookLM | `~/.config/gweb/credentials/notebooklm/profiles/default/storage_state.json` (0600) |
| Database | `~/.local/share/gweb/gweb.db` |
| Audio | `~/.local/share/gweb/recordings/<ID>/segments/*.wav` (originali) e `<ID>.m4a` (caricato) |
| Riepiloghi | `~/.local/share/gweb/summaries/` |
| Log | `journalctl --user -u gweb` e `~/.local/state/gweb/logs/gweb.log` |

Un'ora occupa ≈ 345 MB di WAV + 43 MB di M4A (48 kHz mono). Gli audio non vengono
eliminati, a meno di attivare la pulizia qui sotto.

## Pulizia settimanale dell'audio già su NotebookLM

Elimina l'audio locale delle registrazioni la cui fonte è **pronta su NotebookLM**: ogni
fonte viene ricontrollata sul notebook prima di eliminare; se la rete o la sessione non
funzionano non si elimina nulla. Metadati e riepiloghi restano.

Prova in simulazione (non elimina nulla):

```bash
gweb recordings prune
```

Attivazione: in `~/.config/gweb/config.toml` imposta `enabled = true` nella sezione
`[cleanup]` (ogni domenica alle 03:30, solo fonti pronte da almeno 7 giorni), poi:

```bash
gweb service install
```

```bash
systemctl --user list-timers gweb-cleanup.timer
```

> ⚠️ NotebookLM conserva la fonte (contenuti interrogabili), ma non è garantito che l'audio
> originale si possa riscaricare. Con `mode = "all"` l'audio è perso. Con
> `mode = "segments"` elimini solo i WAV e tieni l'M4A (circa il 90% di spazio recuperato).

## Risoluzione dei problemi

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| `Nessun microfono di cattura trovato` (exit 9) | Microfono scollegato o non riconosciuto; HAT non montato o driver non caricato | `arecord -l`, `lsusb`, `dmesg \| grep -i wm8960`; `gweb audio select <ID scheda>` |
| `Device or resource busy` | Microfono usato da un'altra applicazione o da una registrazione | `gweb record status`; ferma l'altro programma |
| `audio test` esito "silenzio" | Guadagno a zero / mute | `alsamixer -c <ID scheda>` (F4 = capture) |
| `Permission denied` su `/dev/snd` | Utente non nel gruppo audio | `sudo usermod -aG audio $USER`, poi nuovo login e `gweb service restart` |
| `Il servizio in background non è attivo` (exit 6) | Demone fermo | `gweb service status`, `gweb service logs`, `systemctl --user start gweb` |
| Servizio non parte al boot | Linger disattivato | `sudo loginctl enable-linger $USER` |
| Lavoro `blocked_auth` / exit 5 | Sessione Google scaduta | Ripeti il login dal Mac e `--import -` |
| Lavoro `uncertain` | Timeout durante l'upload: esito sconosciuto | Attendi: la coda verifica da sola il notebook. Se resta incerto: `gweb notebook sources -n NB`; se vedi la fonte con `[ID]` nel titolo `gweb jobs resolve JOB --source-id SRC`, altrimenti `gweb jobs resolve JOB --not-uploaded` |
| Lavoro `failed` `PROCESSING_FAILED` | NotebookLM non ha elaborato l'audio | Controlla la fonte nell'interfaccia web; eliminala e `gweb jobs retry JOB` dopo averla rimossa, oppure prova `upload_format = "mp3"` e `gweb recordings finalize ID` |
| Registrazione `failed` "Finalizzazione fallita" | Errore ffmpeg | `gweb recordings finalize ID` (i segmenti sono intatti) |
| Riepilogo exit 8 | Fonte non ancora pronta | `gweb jobs list`; riprova più tardi |
| NotebookLM risponde in modo inatteso / errori di parsing | API non ufficiale cambiata | Controlla le release di notebooklm-py; aggiorna la versione bloccata solo dopo verifica |

Sicurezza: nessuna porta di rete aperta; servizio senza root (`NoNewPrivileges`,
`UMask=0077`); credenziali fuori dal repository (`.gitignore` esclude audio, DB, log e
segreti); i log redigono pattern di cookie/token.

## Sviluppo

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest && .venv/bin/ruff check src tests
```

I test non richiedono hardware né account (arecord simulato, `/proc/asound` finto,
backend NotebookLM `fake`). Vedi [AGENTS.md](AGENTS.md).
