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

## 2. Collegati al Raspberry (una volta sola)

1. Nel pannello, rotella ⚙︎ → **Impostazioni**.
2. In **Raspberry** l'indirizzo è già `riccardo@dartsvision.local`: premi
   **Salva e prova la connessione**.
3. Se risponde "✓ Raspberry raggiungibile" hai finito. Se dice che l'accesso è rifiutato:
   in **Accesso al Raspberry** premi **Crea il mio codice di accesso**, poi
   **Copia codice di accesso** e mandalo a Riccardo (Slack, mail: è pubblico, non contiene
   password). Quando ti conferma, premi di nuovo *Salva e prova la connessione*.

Il codice permette alla tua app di usare solo le funzioni di Briefing sul Raspberry, niente
altro.

**Fuori dall'ufficio** `dartsvision.local` non si raggiunge: serve Tailscale sul Mac, e al
posto dell'indirizzo si usa quello Tailscale del Raspberry (chiedilo a Riccardo).

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

Autorizzare un collega (dal tuo Mac, con il codice che ti ha mandato copiato negli appunti):

```bash
pbpaste | ssh raspberry '~/agency-briefing/scripts/add-team-key.sh'
```

La chiave viene aggiunta in `~/.ssh/authorized_keys` con `restrict` e il comando forzato
`gweb-ssh-guard`: può eseguire solo i comandi di gweb usati dall'app (registrazione,
elenco e upload, notebook), non una shell. Per revocarla: elimina la riga con il suo nome
(`briefing-<utente>@<mac>`) da `~/.ssh/authorized_keys` sul Raspberry.

Nuova versione dell'app: `macos/GwebBar/build-app.sh --dmg` nel repository.
