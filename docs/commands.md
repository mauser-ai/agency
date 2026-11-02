# Riferimento comandi `agency`

Solo comandi realmente implementati (versione 0.1.0). Ogni comando ha `-h/--help` con esempi.
Opzioni globali: `--config FILE`, `--json` (anche per comando), `-v/-vv`, `--version`.

Legenda verifica: **T** = coperto da test automatici in questo repository (container x86,
arecord simulato, backend NotebookLM finto); **P** = da verificare sul Raspberry con
hardware reale; **R** = da verificare con account NotebookLM reale.

## Codici di uscita

| Codice | Significato | Esempi |
|---|---|---|
| 0 | ok | |
| 1 | errore generico / `doctor` con controlli falliti | |
| 2 | uso errato (opzioni, input non valido, modalità non interattiva senza argomenti) | `record start` senza `--title` |
| 3 | non trovato | ID registrazione inesistente, nessuna registrazione attiva |
| 4 | conflitto di stato | registrazione già attiva, upload duplicato |
| 5 | login NotebookLM necessario / sessione scaduta | |
| 6 | servizio in background non attivo | `record start` con demone fermo |
| 7 | errore remoto (rete, NotebookLM) | |
| 8 | non pronto | riepilogo prima che la fonte sia pronta |
| 9 | dispositivo audio | microfono assente/occupato |
| 10 | spazio/archiviazione | spazio insufficiente |

Con `--json` gli errori sono `{"ok": false, "error": {"code", "message", "hint", "details"}}`
su stdout, con lo stesso codice di uscita.

## Diagnostica e configurazione

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency doctor [--json]` | Hardware (modello Pi, arch, userland 32/64 bit), OS, RAM, Python, config, DB, spazio, arecord, ffmpeg, microfono, permessi `/dev/snd`, notebooklm-py, sessione e permessi, servizio, linger, demone. Exit 1 se qualcosa è `fail`. | T (container), P |
| `agency config show [--json]` | Configurazione effettiva, override da CLI, percorsi. | T |
| `agency config init [--force]` | Crea `~/.config/agency/config.toml` dall'esempio (backup `.bak` con `--force`). | T |
| `agency config path` | Percorso del file di configurazione. | T |

## Audio

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency audio devices [--json]` | Dispositivi di cattura da `/proc/asound` con ID scheda, PCM `plughw:CARD=…,DEV=…`, USB sì/no, selezionato. | T (finto /proc), P |
| `agency audio select [DEVICE\|auto]` | Microfono persistente (DB). Senza argomento: scelta interattiva. | T, P |
| `agency audio test [--duration 10] [--json]` | Cattura di prova in `~/.local/state/agency/audio-tests/`, picco/RMS dBFS ed esito (silenzio, saturazione, basso, ok). Rifiuta se una registrazione è attiva. | T (simulato), P |

## NotebookLM

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency notebook login` | Stampa la procedura Mac → Raspberry con i comandi esatti. | T |
| `agency notebook login --import FILE\|-` | Importa `storage_state.json` (stdin con `-`), lo valida, lo scrive 0600 atomicamente, sblocca i lavori `blocked_auth`, prova una lettura remota. Exit 5 se la sessione non è valida. `--remove-source` elimina il file sorgente. | T (import/permessi), R (validità) |
| `agency notebook login --import-master-token FILE\|-` | Opzionale: master token per re-mint automatico (richiede `pip install -e ".[headless]"`). | R |
| `agency notebook login --browser` | Login diretto (solo su macchine con display e Playwright). | — |
| `agency notebook status [--offline] [--json]` | File sessione, permessi, data, master token, ultimo keepalive; senza `--offline` elenca i notebook (sola lettura). | T (assenza sessione → 5), R |
| `agency notebook list [--json]` | Notebook dell'account; `*` = predefinito. | T (fake), R |
| `agency notebook select [ID\|prefisso\|titolo]` | Notebook predefinito persistente; senza argomento scelta numerata interattiva (exit 2 se non interattivo). | T (fake), R |
| `agency notebook current [--json]` | Predefinito (exit 3 se assente). | T |
| `agency notebook sources [-n NB] [--json]` | Fonti e stato di elaborazione (sola lettura). | T (fake), R |

## Registrazione (eseguita dal servizio)

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency record start --title T [--notebook NB] [--no-wait] [--json]` | Richiede l'avvio al demone; attende la conferma (max 20 s). Il notebook (esplicito o predefinito in quel momento) è copiato nella registrazione. Exit 6 se il servizio non è attivo, 4 se già in corso, 9 se il microfono non parte. | T (simulato), P |
| `agency record status [--json]` | Trascorso, audio salvato, segmenti, dispositivo, spazio libero, ultimi eventi. | T, P |
| `agency record stop [--wait-final] [--json]` | Arresto controllato; con `--wait-final` attende anche il file `.m4a`. | T, P |

## Archivio

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency recordings list [--limit N] [--json]` | ID, data, titolo, durata, stato locale, stato upload, notebook. | T |
| `agency recordings show ID [--json]` | Metadati completi, segmenti, lavori, riepiloghi, eventi. ID abbreviabili (prefisso univoco). | T |
| `agency recordings upload ID [--notebook NB] [--wait] [--json]` | Accoda l'upload (esplicito). Exit 4 se già caricata/in coda per quel notebook. | T (fake), R |
| `agency recordings import PATH --title T [--notebook NB] [--json]` | Copia nell'archivio; MP3/M4A senza conversione, altri formati → M4A. | T |
| `agency recordings finalize ID [--json]` | Ripete unione/codifica (es. dopo errore ffmpeg). | T (indiretto) |

## Coda

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency jobs list [--open] [--json]` | Stato, tentativi, prossimo tentativo, ID fonte, errore. | T |
| `agency jobs retry JOB_ID` | Retry immediato; se esiste già l'ID fonte ricontrolla solo l'elaborazione; se incerto riconcilia, non reinvia. | T |
| `agency jobs cancel JOB_ID` | Annulla un lavoro non concluso. | T (indiretto) |
| `agency jobs resolve JOB_ID --source-id SRC \| --not-uploaded` | Risoluzione manuale di un esito incerto. | T |
| `agency jobs run [--json]` | Elabora la coda in primo piano (solo a servizio fermo). | T |

Stati: `pending` (in coda/attesa retry) · `running` · `processing` (upload completato,
fonte in elaborazione) · `uncertain` · `blocked_auth` · `succeeded` (fonte pronta) ·
`failed` · `cancelled`.

## Domande e riepiloghi

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency ask "DOMANDA" [-n NB] [-r REC_ID] [-s SRC]... [--json]` | Risposta e citazioni `[n] titolo fonte: estratto`. `-r` limita alla fonte della registrazione (deve essere pronta). | T (fake), R |
| `agency briefing summarize REC_ID [--compare auto\|yes\|no] [--json]` | Riepilogo strutturato limitato alla fonte; confronto fuori preventivo solo se ci sono altre fonti pronte. Salvato in DB e `~/.local/share/agency/summaries/`. Exit 8 se non pronta. | T (fake), R |

## Servizio

| Comando | Descrizione | Verifica |
|---|---|---|
| `agency service install [--no-start] [--print]` | Scrive `~/.config/systemd/user/agency.service` (backup se diverso), `daemon-reload`, `enable`, `restart`. | `--print` T; P |
| `agency service start\|stop\|restart` | `systemctl --user …`. | P |
| `agency service status [--json]` | Unità, linger, demone (pid/heartbeat), registrazione attiva, contatori coda, keepalive, spazio. Exit 6 se il demone non è vivo. | T (parziale), P |
| `agency service logs [-n 100] [-f]` | `journalctl --user -u agency.service`; in mancanza `~/.local/state/agency/logs/agency.log`. | P |
| `agency service run` | Demone in primo piano (usato da systemd). | T (thread), P |
