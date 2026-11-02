# AGENTS.md — contesto per agenti di sviluppo (Codex, Claude Code, …)

Leggi questo file, poi `docs/status.md` (cosa è fatto/verificato) e `docs/architecture.md`
(decisioni). Non serve ricostruire la conversazione che ha generato il progetto.

## Scopo
Dispositivo autonomo (Raspberry Pi 4 + microfono USB, sempre acceso) che registra i briefing
di una web agency, li conserva con metadati e li carica come **fonti audio** nei notebook
NotebookLM, poi interroga il notebook (domande, riepilogo strutturato). Tutto da terminale
(`gweb`) o dall'app per la barra dei menu del Mac (`macos/GwebBar`, SwiftUI), che esegue
`gweb … --json` via SSH: niente API di rete. Nessun modello AI locale: l'elaborazione è su
NotebookLM.

## Architettura in breve
```
App Mac ── ssh ── CLI (agency_briefing/cli, --json) ──► AgencyApp (app.py)  ── logica condivisa, ritorna dict
                                   │
          ┌────────────────────────┼─────────────────────────┐
     RecordingStore          JobQueue/UploadWorker      NotebookBackend (adapter)
     (recordings.py)          (queue.py)                 notebook/base.py
          │                        │                     ├─ notebooklm_py.py (reale)
     SQLite (db.py)  ◄─────────────┘                     └─ fake.py (test)
          ▲
Demone `gweb service run` (daemon.py, systemd utente)
   ├─ Recorder (recorder.py) → arecord (audio/capture.py) → segmenti WAV
   ├─ finalizer thread → finalize.py → ffmpeg (audio/encode.py) → .m4a
   └─ uploader thread → UploadWorker + keepalive cookie
```
- La CLI **non** avvia arecord per le registrazioni: scrive una richiesta nel DB, il demone
  la esegue (indipendenza da SSH). Fa eccezione `audio test` (cattura breve in primo piano).
- Unicità garantite da indici SQLite (una registrazione attiva; un lavoro vivo per
  registrazione+notebook) e da un file lock (una sola istanza del demone).

## Convenzioni
- Python ≥ 3.10, pacchetto `src/agency_briefing`, comando `gweb` (`pyproject.toml`).
- Testi utente, messaggi e documentazione in **italiano**; identificatori in inglese.
- Errori: solo sottoclassi di `errors.AgencyError` con `hint` operativo; ogni classe ha un
  `exit_code` (tabella in `docs/commands.md`). Mai `sys.exit` nella logica, solo nella CLI.
- Ogni comando di consultazione accetta `--json`; l'output JSON è un contratto per l'app
  Mac: aggiungere campi è ok, rinominarli/rimuoverli no senza aggiornare docs/commands.md.
- Orari in DB: ISO 8601 UTC con `Z` (`db.iso()`); in output: ora locale.
- notebooklm-py è **non ufficiale**: usa solo API verificate (elenco in
  `notebook/notebooklm_py.py` e `docs/architecture.md`). Ogni nuova chiamata va verificata
  sul pacchetto installato (`.venv/lib/python*/site-packages/notebooklm`) e testata in
  `tests/test_units.py::test_notebooklm_py_api_surface`.
- Le eccezioni della libreria si convertono SOLO in `notebook/notebooklm_py.map_exception`.
- L'audio locale si elimina SOLO in `cleanup.py` (`gweb recordings prune`, timer
  settimanale opzionale) e solo dopo verifica remota che la fonte sia READY; errori di
  rete/sessione interrompono senza eliminare. Mai altrove. Metadati e riepiloghi restano.
- Segreti: mai in log, output, repository. File credenziali 0600, directory 0700.
- Non aprire porte di rete. Nessun servizio esposto.

## Comandi di sviluppo e verifica
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # oppure: uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest                      # ~35 test, ~30 s; nessun hardware/account necessario
.venv/bin/ruff check src tests
.venv/bin/gweb --help
GWEB_CONFIG=/tmp/x.toml .venv/bin/gweb ...   # config isolata per prove manuali
```
I test usano `tests/fake_arecord.py` (simula arecord, scollegamenti, intestazioni non
chiuse), una finta `/proc/asound` (`GWEB_ASOUND_ROOT`) e il backend `fake`
(`GWEB_FAKE_FAIL` per iniettare guasti). Servono `ffmpeg`/`ffprobe` nel PATH.

Prove sul Raspberry (hardware reale):
```bash
./scripts/inspect-pi.sh             # sola lettura
gweb doctor
gweb audio devices && gweb audio test --duration 10
```

## Regole per NotebookLM
- Prima le verifiche di sola lettura (`notebook status`, `notebook list`, `notebook sources`).
- **Non caricare audio di prova sull'account dell'utente senza richiesta esplicita.** Per le
  prove usare `[notebooklm] backend = "fake"`.
- `gweb ask`/`briefing summarize` aggiungono turni alla conversazione del notebook; non
  usare mai `delete_conversation` / `--new` della libreria (distruttivo).

## Dove guardare
| Tema | File |
|---|---|
| Stati registrazione/upload | `models.py` |
| Schema DB | `db.py` |
| Macchina a stati della coda, anti-duplicati | `queue.py` (docstring) |
| Registrazione, riconnessione, ripresa | `recorder.py` |
| Formato audio e motivazioni | `audio/encode.py`, `docs/architecture.md` |
| Prompt riepilogo | `briefing.py` |
| Pulizia audio già su NotebookLM | `cleanup.py` |
| Servizio | `systemd.py`, `deploy/gweb.service` |
| App Mac (barra dei menu) | `macos/GwebBar/` (`RemoteGweb.swift` = comandi usati, `swift test`) |

Aggiorna `docs/status.md` a ogni incremento (cosa è verificato e dove).
