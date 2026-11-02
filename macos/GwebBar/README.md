# GwebBar — app per la barra dei menu di macOS

Una finestrella attaccata alla barra in alto del Mac per comandare il registratore sul
Raspberry senza aprire il terminale:

1. **Nuova registrazione** (titolo facoltativo: diventa il nome della fonte su NotebookLM).
2. **Stop** quando il briefing finisce.
3. **Dove la carico?**: scegli un notebook esistente (con ricerca) oppure scrivi il nome di
   uno nuovo e premi *Crea e carica*. Puoi anche non caricarla e farlo dopo con *Carica…*
   dall'elenco delle ultime registrazioni.

Il resto lo fa il Raspberry: finisce di preparare il file, lo carica e segue l'elaborazione
su NotebookLM. Da quando premi *Stop* puoi chiudere il Mac. Nell'elenco vedi lo stato
(in coda, in caricamento, NotebookLM sta trascrivendo, pronta).

Durante la registrazione l'icona nella barra mostra il pallino e il tempo trascorso.

## Come funziona

Nessuna porta di rete e nessun servizio in più sul Pi: l'app esegue via SSH gli stessi
comandi del terminale con output JSON (`gweb record start/status/stop`, `notebook
list/create`, `recordings upload/list`, tutti con `--json`). Usa l'alias `raspberry` di
`~/.ssh/config`, quindi la stessa chiave, lo stesso host e il multiplexing della
connessione. `BatchMode=yes`: se serve una password l'app mostra l'errore invece di
restare appesa.

Se scegli il notebook mentre il Pi sta ancora codificando l'audio, gweb segna la richiesta
(`upload_on_ready`) e il servizio accoda l'upload appena il file è pronto.

## Installazione

Serve Xcode (o i Command Line Tools con Swift 5.10 o successivo), macOS 13 o successivo.

```bash
cd macos/GwebBar && ./build-app.sh --install
```

Crea `~/Applications/GwebBar.app` (binario universale, firma ad-hoc per uso personale) e
lo avvia. Per farlo partire a ogni accesso: icona ingranaggio → **Avvia al login**.

## Impostazioni (icona ingranaggio)

| Campo | Default | Note |
|---|---|---|
| Host SSH | `raspberry` | alias di `~/.ssh/config` oppure `utente@indirizzo`; serve l'accesso a chiave |
| Percorso di gweb | `~/.local/bin/gweb` | sul Raspberry |
| Avvia al login | spento | elemento di login di macOS (Impostazioni di Sistema → Generali → Elementi login) |

## Problemi

| Sintomo | Cosa fare |
|---|---|
| "Raspberry non raggiungibile" | `ssh raspberry` dal Terminale deve entrare senza password |
| "Servizio gweb fermo sul Raspberry" | `ssh raspberry 'systemctl --user start gweb'` |
| "Serve un nuovo login NotebookLM" | procedura di login nel README principale |
| "Registrazione non riuscita" | `ssh raspberry 'gweb doctor'` (microfono) |

## Sviluppo

```bash
swift build && swift test
```

```bash
SNAPSHOT_DIR=/tmp/gwebbar-snap swift test --filter SnapshotTests
```

Il secondo comando salva in PNG la finestrella nei suoi stati principali (riposo,
registrazione, scelta del notebook), sia in modalità chiara sia scura, per un controllo
visivo senza aprire l'app.
