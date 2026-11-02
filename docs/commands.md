# Riferimento comandi `gweb`

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
| `gweb doctor [--json]` | Hardware (modello Pi, arch, userland 32/64 bit), OS, RAM, Python, config, DB, spazio, arecord, ffmpeg, microfono, permessi `/dev/snd`, notebooklm-py, sessione e permessi, servizio, linger, demone. Exit 1 se qualcosa è `fail`. | T (container), P |
| `gweb config show [--json]` | Configurazione effettiva, override da CLI, percorsi. | T |
| `gweb config init [--force]` | Crea `~/.config/gweb/config.toml` dall'esempio (backup `.bak` con `--force`). | T |
| `gweb config path` | Percorso del file di configurazione. | T |

## Audio

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb audio devices [--json]` | Dispositivi di cattura da `/proc/asound` con ID scheda, PCM `plughw:CARD=…,DEV=…`, USB sì/no, selezionato. | T (finto /proc), P |
| `gweb audio select [DEVICE\|auto]` | Microfono persistente (DB). Senza argomento: scelta interattiva. | T, P |
| `gweb audio test [--duration 10] [--json]` | Cattura di prova in `~/.local/state/gweb/audio-tests/`, picco/RMS dBFS ed esito (silenzio, saturazione, basso, ok). Rifiuta se una registrazione è attiva. | T (simulato), P |

## NotebookLM

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb notebook login` | Stampa la procedura Mac → Raspberry con i comandi esatti. | T |
| `gweb notebook login --import FILE\|-` | Importa `storage_state.json` (stdin con `-`), lo valida, lo scrive 0600 atomicamente, sblocca i lavori `blocked_auth`, prova una lettura remota. Exit 5 se la sessione non è valida. `--remove-source` elimina il file sorgente. | T (import/permessi), R (validità) |
| `gweb notebook login --import-master-token FILE\|-` | Opzionale: master token per re-mint automatico (richiede `pip install -e ".[headless]"`). | R |
| `gweb notebook login --browser` | Login diretto (solo su macchine con display e Playwright). | — |
| `gweb notebook status [--offline] [--json]` | File sessione, permessi, data, master token, ultimo keepalive; senza `--offline` elenca i notebook (sola lettura). | T (assenza sessione → 5), R |
| `gweb notebook list [--json]` | Notebook dell'account; `*` = predefinito. | T (fake), R |
| `gweb notebook select [ID\|prefisso\|titolo]` | Notebook predefinito persistente; senza argomento scelta numerata interattiva (exit 2 se non interattivo). | T (fake), R |
| `gweb notebook create TITOLO [--select] [--json]` | Crea un notebook vuoto (scrittura: invio unico, nessun ritentativo automatico); `--select` lo rende predefinito. Usato dall'app Mac. | T (fake), R |
| `gweb notebook current [--json]` | Predefinito (exit 3 se assente). | T |
| `gweb notebook sources [-n NB] [--json]` | Fonti e stato di elaborazione (sola lettura). | T (fake), R |

## Registrazione (eseguita dal servizio)

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb record start --title T [--notebook NB] [--no-wait] [--json]` | Richiede l'avvio al demone; attende la conferma (max 20 s). Il notebook (esplicito o predefinito in quel momento) è copiato nella registrazione. Exit 6 se il servizio non è attivo, 4 se già in corso, 9 se il microfono non parte. | T (simulato), P |
| `gweb record status [--json]` | Trascorso, audio salvato, segmenti, dispositivo, spazio libero, ultimi eventi. | T, P |
| `gweb record stop [--wait-final] [--json]` | Arresto controllato; con `--wait-final` attende anche il file `.m4a`. | T, P |

## Archivio

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb recordings list [--limit N] [--json]` | ID, data, titolo, durata, stato locale, stato upload, notebook. | T |
| `gweb recordings show ID [--json]` | Metadati completi, segmenti, lavori, riepiloghi, eventi. ID abbreviabili (prefisso univoco). | T |
| `gweb recordings upload ID [--notebook NB] [--wait] [--json]` | Accoda l'upload (esplicito). Se la registrazione è ancora in arresto/finalizzazione, imposta il notebook e risponde `"scheduled": true`: il servizio accoda l'upload appena il file è pronto (chi lo chiede può disconnettersi). Exit 4 se già caricata/in coda per quel notebook, 8 se la registrazione è ancora in corso. | T (fake), R |
| `gweb recordings import PATH --title T [--notebook NB] [--json]` | Copia nell'archivio; MP3/M4A senza conversione, altri formati → M4A. | T |
| `gweb recordings prune [--yes] [--mode all\|segments] [--min-age-days N] [--json]` | Senza `--yes` simula. Elimina l'audio locale solo delle registrazioni con fonte verificata READY su NotebookLM al momento, pronta da almeno `min_age_days`. Rete assente / sessione scaduta → exit 7/5 senza eliminare nulla. Metadati, eventi e riepiloghi restano; una registrazione ripulita non è più caricabile (exit 4). | T (fake), R |
| `gweb recordings finalize ID [--json]` | Ripete unione/codifica (es. dopo errore ffmpeg). | T (indiretto) |

## Coda

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb jobs list [--open] [--json]` | Stato, tentativi, prossimo tentativo, ID fonte, errore. | T |
| `gweb jobs retry JOB_ID` | Retry immediato; se esiste già l'ID fonte ricontrolla solo l'elaborazione; se incerto riconcilia, non reinvia. | T |
| `gweb jobs cancel JOB_ID` | Annulla un lavoro non concluso. | T (indiretto) |
| `gweb jobs resolve JOB_ID --source-id SRC \| --not-uploaded` | Risoluzione manuale di un esito incerto. | T |
| `gweb jobs run [--json]` | Elabora la coda in primo piano (solo a servizio fermo). | T |

Stati: `pending` (in coda/attesa retry) · `running` · `processing` (upload completato,
fonte in elaborazione) · `uncertain` · `blocked_auth` · `succeeded` (fonte pronta) ·
`failed` · `cancelled`.

## Domande e riepiloghi

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb ask "DOMANDA" [-n NB] [-r REC_ID] [-s SRC]... [--json]` | Risposta e citazioni `[n] titolo fonte: estratto`. `-r` limita alla fonte della registrazione (deve essere pronta). | T (fake), R |
| `gweb briefing summarize REC_ID [--compare auto\|yes\|no] [--json]` | Riepilogo strutturato limitato alla fonte; confronto fuori preventivo solo se ci sono altre fonti pronte. Salvato in DB e `~/.local/share/gweb/summaries/`. Exit 8 se non pronta. | T (fake), R |

## Servizio

| Comando | Descrizione | Verifica |
|---|---|---|
| `gweb service install [--no-start] [--print]` | Scrive `~/.config/systemd/user/gweb.service` (backup se diverso), `daemon-reload`, `enable`, `restart`. Con `cleanup.enabled = true` installa e attiva anche `gweb-cleanup.timer` (esegue `recordings prune --yes` secondo `cleanup.schedule`); con `false` lo disattiva se presente. | `--print` T; P |
| `gweb service start\|stop\|restart` | `systemctl --user …`. | P |
| `gweb service status [--json]` | Unità, linger, demone (pid/heartbeat), registrazione attiva, contatori coda, keepalive, spazio. Exit 6 se il demone non è vivo. | T (parziale), P |
| `gweb service logs [-n 100] [-f]` | `journalctl --user -u gweb.service`; in mancanza `~/.local/state/gweb/logs/gweb.log`. | P |
| `gweb service run` | Demone in primo piano (usato da systemd). | T (thread), P |
