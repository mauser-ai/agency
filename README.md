# agency-briefing

Registratore di briefing per **Raspberry Pi 4 + microfono USB** che carica le registrazioni
come fonti audio nei notebook **NotebookLM** e li interroga (domande con citazioni, riepilogo
strutturato). Tutto da terminale con il comando `agency`; continua a funzionare chiudendo
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

```bash
ssh pi@raspberrypi.local            # sostituisci utente/host
./scripts/inspect-pi.sh              # (facoltativo) ispezione in sola lettura, dopo il clone
```

```bash
git clone https://github.com/mauser-ai/agency-briefing.git ~/agency-briefing
```

```bash
cd ~/agency-briefing && ./scripts/inspect-pi.sh | tee ~/agency-inspect.txt
```

```bash
sudo apt-get install -y --no-install-recommends alsa-utils ffmpeg python3-venv
```

```bash
./scripts/install.sh
```

```bash
sudo loginctl enable-linger "$USER"
```

`install.sh` crea `.venv`, installa il pacchetto in modalità modificabile, collega
`~/.local/bin/agency`, crea `~/.config/agency/config.toml` se assente (non sovrascrive),
installa e avvia il servizio utente `agency.service`, esegue `agency doctor`.
`enable-linger` (unico comando con sudo) fa partire il servizio al boot senza login.

Verifica:

```bash
agency doctor
```

```bash
agency audio devices
```

```bash
agency audio test --duration 10
```

Se `~/.local/bin` non è nel PATH (sessioni non di login), usa
`~/agency-briefing/.venv/bin/agency`.

## Login NotebookLM dal Mac (via SSH)

Il login Google richiede un browser, quindi si fa sul Mac e si trasferisce **solo** il
file di sessione `storage_state.json`. Non incollare mai password o cookie in chat o nel
terminale del Pi. `agency notebook login` sul Pi stampa questa stessa procedura con il tuo
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
ssh pi@raspberrypi.local '~/agency-briefing/.venv/bin/agency notebook login --import -' < ~/.notebooklm/profiles/default/storage_state.json
```

Elimina la copia sul Mac, così un solo client usa (e ruota) quei cookie:

```bash
rm ~/.notebooklm/profiles/default/storage_state.json
```

Sul Pi, verifica di sola lettura:

```bash
agency notebook status
```

**Sessione scaduta**: `notebook status` esce con codice 5 e i caricamenti passano a
"attende login" senza consumare tentativi. Ripeti login e import dal Mac: la coda riparte
da sola appena il file cambia. Il servizio rinnova i cookie ogni 20 minuti (come
raccomandato dalla libreria) per ridurre le scadenze.

**Opzionale – master token** (`notebooklm login --master-token --account EMAIL` sul Mac,
`pip install -e ".[headless]"` sul Pi, `agency notebook login --import-master-token -`):
permette il rinnovo automatico anche dopo la scadenza dei cookie, ma è una credenziale
**dell'intero account Google, durevole, che sopravvive al cambio password**. Usala solo con
un account dedicato e valuta il rischio; revoca da Google Account → Sicurezza → Dispositivi.

## Uso tipico

```bash
agency notebook list
```

```bash
agency notebook select
```

```bash
agency record start --title "Briefing nuovo sito"
```

Puoi chiudere SSH: la registrazione continua. Più tardi:

```bash
agency record status
```

```bash
agency record stop --wait-final
```

```bash
agency recordings list
```

```bash
agency recordings upload RECORDING_ID --wait
```

```bash
agency jobs list
```

Quando lo stato è "fonte pronta" (upload completato ≠ fonte pronta: NotebookLM deve
trascrivere l'audio, può richiedere minuti):

```bash
agency briefing summarize RECORDING_ID
```

```bash
agency ask --recording RECORDING_ID "Quali decisioni sono state prese?"
```

Il riepilogo è salvato in `~/.local/share/agency/summaries/`. Per caricare automaticamente
dopo lo stop: `auto_after_stop = true` nella sezione `[upload]` del config.

## Dove sono i dati

| Cosa | Percorso |
|---|---|
| Config | `~/.config/agency/config.toml` (esempio: `examples/config.example.toml`) |
| Sessione NotebookLM | `~/.config/agency/credentials/notebooklm/profiles/default/storage_state.json` (0600) |
| Database | `~/.local/share/agency/agency.db` |
| Audio | `~/.local/share/agency/recordings/<ID>/segments/*.wav` (originali) e `<ID>.m4a` (caricato) |
| Riepiloghi | `~/.local/share/agency/summaries/` |
| Log | `journalctl --user -u agency` e `~/.local/state/agency/logs/agency.log` |

Gli audio originali non vengono mai eliminati automaticamente: controlla lo spazio con
`agency doctor` (un'ora ≈ 345 MB di WAV + 43 MB di M4A a 48 kHz mono).

## Risoluzione dei problemi

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| `Nessun microfono USB di cattura trovato` (exit 9) | Microfono scollegato o non riconosciuto | `arecord -l`, `lsusb`; `agency audio select <ID scheda>` |
| `Device or resource busy` | Microfono usato da un'altra applicazione o da una registrazione | `agency record status`; ferma l'altro programma |
| `audio test` esito "silenzio" | Guadagno a zero / mute | `alsamixer -c <ID scheda>` (F4 = capture) |
| `Permission denied` su `/dev/snd` | Utente non nel gruppo audio | `sudo usermod -aG audio $USER`, poi nuovo login e `agency service restart` |
| `Il servizio in background non è attivo` (exit 6) | Demone fermo | `agency service status`, `agency service logs`, `systemctl --user start agency` |
| Servizio non parte al boot | Linger disattivato | `sudo loginctl enable-linger $USER` |
| Lavoro `blocked_auth` / exit 5 | Sessione Google scaduta | Ripeti il login dal Mac e `--import -` |
| Lavoro `uncertain` | Timeout durante l'upload: esito sconosciuto | Attendi: la coda verifica da sola il notebook. Se resta incerto: `agency notebook sources -n NB`; se vedi la fonte con `[ID]` nel titolo `agency jobs resolve JOB --source-id SRC`, altrimenti `agency jobs resolve JOB --not-uploaded` |
| Lavoro `failed` `PROCESSING_FAILED` | NotebookLM non ha elaborato l'audio | Controlla la fonte nell'interfaccia web; eliminala e `agency jobs retry JOB` dopo averla rimossa, oppure prova `upload_format = "mp3"` e `agency recordings finalize ID` |
| Registrazione `failed` "Finalizzazione fallita" | Errore ffmpeg | `agency recordings finalize ID` (i segmenti sono intatti) |
| Riepilogo exit 8 | Fonte non ancora pronta | `agency jobs list`; riprova più tardi |
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
