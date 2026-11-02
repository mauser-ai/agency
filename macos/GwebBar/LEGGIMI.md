# Briefing — registra le riunioni con il Raspberry dell'ufficio

Briefing è una piccola app per la barra dei menu del Mac. Con un clic fa partire la
registrazione sul Raspberry dell'ufficio (il microfono sul tavolo); allo Stop scegli il
notebook di NotebookLM e il Raspberry carica tutto da solo. Puoi chiudere il Mac subito dopo.

## 1. Installa

1. Apri `Briefing-1.0.0.dmg` e trascina **Briefing** nella cartella **Applicazioni**.
2. Apri Briefing da Applicazioni (o con Spotlight: ⌘ spazio, "Briefing").
   Se macOS dice che non può verificare lo sviluppatore: Impostazioni di Sistema →
   Privacy e sicurezza → in fondo, **Apri comunque**.

Non c'è un'icona nel Dock: Briefing vive in alto a destra, nella barra dei menu (logo GWEB).
Se non la vedi (sui Mac con il notch le icone in eccesso vengono nascoste), apri il pannello
con **⌥⌘B** (Opzione + Comando + B) da qualunque app.

## 2. Collegamento

Nessuna configurazione: in ufficio Briefing si collega da solo al Raspberry. In basso nel
pannello vedi "Raspberry collegato". Fuori dall'ufficio non si raggiunge (serve Tailscale).

## 3. Usa

- **Nuova registrazione**: il titolo è facoltativo (diventa il nome della fonte su NotebookLM).
- **Stop** a fine riunione → **Dove la carico?**: scegli un notebook o creane uno nuovo.
- Sotto vedi le ultime registrazioni: caricamento, trascrizione, "Pronta su NotebookLM".
  Quelle non caricate hanno *Carica…*.
- Una registrazione alla volta: se un collega sta registrando lo vedi nel pannello.

## Da sapere

- **L'account NotebookLM è uno per tutto il team**, quello collegato al Raspberry. In
  Impostazioni → NotebookLM lo vedi; *Cambia account* o *Scollega* lo cambiano per tutti:
  fallo solo se concordato. Se la sessione scade arriva una notifica e compare un banner
  "Accedi con Google…" (il login richiede Python 3.10+ sul Mac la prima volta).
- Per avviare Briefing a ogni accensione: Impostazioni → Generale → **Avvia al login del Mac**.
- Problemi: il registro dell'app è in `~/Library/Logs/GwebBar.log`.

---

## Per chi gestisce il Raspberry

L'app contiene una chiave SSH condivisa del team (presa da `~/.config/gweb-team/` sul Mac di
chi prepara il pacchetto), autorizzata sul Raspberry con `restrict` e il comando forzato
`gweb-ssh-guard`: può usare solo le funzioni di Briefing, non una shell. Il DMG va quindi
dato solo ai colleghi, non pubblicato. Per revocarla: togli la riga `briefing-team` da
`~/.ssh/authorized_keys` sul Raspberry, crea una nuova chiave e rifai il pacchetto.

Nuova versione dell'app: `macos/GwebBar/build-app.sh --dmg` nel repository.
