# Briefing (GwebBar) — app per la barra dei menu di macOS

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

L'interfaccia è un **pannello a tendina sotto la barra dei menu** (come Fathom): si apre
cliccando l'icona, con la scorciatoia **⌥⌘B** da qualunque app, all'avvio e quando riapri
l'app da Spotlight (*GwebBar*). Si chiude cliccando fuori o con Esc. Se l'icona è nascosta
(sui Mac con il notch macOS nasconde quelle che non ci stanno; tieni premuto ⌘ e trascina
le icone per riordinarle), il pannello si apre comunque in alto a destra.

## Account NotebookLM dall'app

Ingranaggio → **Impostazioni**, sezione NotebookLM:

- **Accedi con Google… / Rinnova sessione… / Cambia account…**: si apre il browser sul Mac
  con una sessione pulita (puoi scegliere qualunque account); a login finito la sessione
  passa al Raspberry via SSH (`gweb notebook login --import -`, file 0600) e la copia sul
  Mac viene cancellata insieme al profilo del browser usato. La prima volta l'app prepara
  da sola lo strumento di login (`~/.venvs/notebooklm`, notebooklm-py 0.8.6, circa un
  minuto; serve Python 3.10+ sul Mac). Se l'account cambia, il notebook predefinito sul
  Pi viene azzerato.
- **Verifica ora**: prova di sola lettura su Google.
- **Scollega**: elimina la sessione dal Raspberry (`gweb notebook logout`); l'account
  Google non viene toccato.

**Avvisi**: ogni 30 secondi (4 durante registrazioni e caricamenti) l'app legge sul Pi lo
stato della sessione, senza contattare Google: il Raspberry la rinnova ogni 20 minuti e
registra l'esito. Se è scaduta o mancante, o se ci sono caricamenti fermi in attesa di
login, compare un banner arancione nella finestrella con *Accedi con Google…* e arriva una
notifica di macOS (una sola per volta, quando lo stato peggiora).

Nella sezione **Raspberry** si imposta l'indirizzo (alias di `~/.ssh/config`,
`utente@host`, indirizzo Tailscale) e si prova la connessione.

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

Crea `~/Applications/Briefing.app` (binario universale, icona dal logo GWEB) e lo avvia.

## Pacchetto per il team

```bash
cd macos/GwebBar && ./build-app.sh --dmg
```

Crea `build/Briefing-<versione>.dmg` con l'app, il collegamento ad Applicazioni e
[LEGGIMI.md](LEGGIMI.md) (istruzioni per i colleghi e per chi gestisce il Raspberry).
Firma con il certificato *Developer ID Application* del portachiavi. Per la notarizzazione
(niente avvisi di Gatekeeper sugli altri Mac) crea una volta il profilo, inserendo tu la
password per app di appleid.apple.com:

```bash
xcrun notarytool store-credentials gweb-notary --apple-id TUA_EMAIL_APPLE --team-id CL2VHCCFP7
```

poi `NOTARY_PROFILE=gweb-notary ./build-app.sh --dmg`.

**Accesso dei colleghi**: nessuna configurazione. `build-app.sh` include nel pacchetto la
chiave SSH condivisa `~/.config/gweb-team/team_ed25519` (fuori dal repository; per crearla:
`ssh-keygen -t ed25519 -N "" -C briefing-team -f ~/.config/gweb-team/team_ed25519` e
`ssh raspberry '~/agency-briefing/scripts/add-team-key.sh' < ~/.config/gweb-team/team_ed25519.pub`).
Sul Pi è autorizzata con `restrict` e il comando forzato `scripts/gweb-ssh-guard` (solo i
comandi gweb dell'app). Il DMG contiene quindi una credenziale: solo per il team.

**Icone**: `Sources/GwebBar/Logo.swift` ridisegna il logo in vettoriale (misurato sul PNG
originale); `scripts/MakeIcon.swift` produce l'iconset macOS (griglia 1024 con forma
824 px, fondo chiaro, ombra) e la barra dei menu usa la stessa sagoma come immagine
*template*.

## Impostazioni (icona ingranaggio)

| Campo | Default | Note |
|---|---|---|
| Indirizzo | `riccardo@dartsvision.local` | alias di `~/.ssh/config` oppure `utente@indirizzo`; serve l'accesso a chiave |
| Percorso di gweb | `~/.local/bin/gweb` | sul Raspberry |
| Account NotebookLM | — | login, rinnovo, cambio account, verifica, scollega (vedi sopra) |
| Avvia al login del Mac | spento | elemento di login di macOS (Impostazioni di Sistema → Generali → Elementi login) |

## Problemi

| Sintomo | Cosa fare |
|---|---|
| Icona nella barra assente | ⌥⌘B apre il pannello lo stesso. Per l'icona: ⌘+trascina le altre icone, o consentila in Impostazioni di Sistema → Barra dei menu |
| "Raspberry non raggiungibile" | `ssh raspberry` dal Terminale deve entrare senza password |
| "Servizio gweb fermo sul Raspberry" | `ssh raspberry 'systemctl --user start gweb'` |
| Banner "NotebookLM: sessione scaduta" | *Accedi con Google…* nel banner o nelle Impostazioni |
| "Serve Python 3.10 o successivo sul Mac" | installa Python da python.org, poi riprova il login |
| "Login non completato" | la finestra del browser è stata chiusa o sono passati 10 minuti: riprova |
| "Registrazione non riuscita" | `ssh raspberry 'gweb doctor'` (microfono) |
| Altro | registro dell'app: `~/Library/Logs/GwebBar.log` |

## Sviluppo

```bash
swift build && swift test
```

```bash
SNAPSHOT_DIR=/tmp/gwebbar-snap swift test --filter SnapshotTests
```

Prove reali (sola lettura) contro il Raspberry: `GWEBBAR_LIVE=raspberry swift test --filter LiveTests`.

Il secondo comando salva in PNG la finestrella nei suoi stati principali (riposo,
registrazione, scelta del notebook), sia in modalità chiara sia scura, per un controllo
visivo senza aprire l'app.
