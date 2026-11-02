"""CLI ``gweb``. Client sottile di ``AgencyApp``: nessuna logica di dominio qui."""

from __future__ import annotations

import functools
import getpass
import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import click

from .. import __version__
from ..config import config_file_path, example_config_text, load_config
from ..errors import AgencyError, ExitCode, UsageError
from ..logging_setup import setup_logging
from . import output as out

EPILOG_MAIN = """\b
Esempi:
  gweb doctor
  gweb notebook select
  gweb record start --title "Briefing nuovo sito"
  gweb record stop
  gweb recordings upload r261102-153012-a1b2
  gweb briefing summarize r261102-153012-a1b2

Codici di uscita: 0 ok · 1 errore · 2 uso errato · 3 non trovato · 4 conflitto di stato
5 login necessario · 6 servizio non attivo · 7 errore remoto · 8 non pronto
9 dispositivo audio · 10 spazio/archiviazione
"""


class Ctx:
    def __init__(self, config: str | None, json_mode: bool, verbose: int):
        if config:
            os.environ["GWEB_CONFIG"] = str(Path(config).expanduser().resolve())
        self.json = json_mode
        self.verbose = verbose
        self._app = None

    @property
    def app(self):
        if self._app is None:
            from ..app import AgencyApp

            self._app = AgencyApp(load_config())
        return self._app


def json_option(f):
    @click.option("--json", "json_flag", is_flag=True, help="Output JSON (per script e app).")
    @functools.wraps(f)
    def wrapper(*args, json_flag=False, **kwargs):
        ctx = click.get_current_context().find_object(Ctx)
        if json_flag:
            ctx.json = True
        return f(*args, **kwargs)

    return wrapper


def c() -> Ctx:
    return click.get_current_context().find_object(Ctx)


def emit(data, human) -> None:
    if c().json:
        out.dump_json(data)
    else:
        human(data)


@click.group(epilog=EPILOG_MAIN, context_settings={"help_option_names": ["-h", "--help"],
                                                   "max_content_width": 100})
@click.option("--config", type=click.Path(dir_okay=False), envvar="GWEB_CONFIG",
              help="File di configurazione (default ~/.config/gweb/config.toml).")
@click.option("--json", "json_mode", is_flag=True, help="Output JSON per tutti i comandi.")
@click.option("-v", "--verbose", count=True, help="Log più dettagliati (-vv per debug).")
@click.version_option(__version__, prog_name="gweb")
@click.pass_context
def cli(ctx, config, json_mode, verbose):
    """Registra briefing dal microfono USB e li carica come fonti su NotebookLM."""
    ctx.obj = Ctx(config, json_mode, verbose)
    level = logging.DEBUG if verbose > 1 else logging.INFO if verbose else logging.WARNING
    setup_logging(None, level)


# ============================================================================ doctor
@cli.command()
@json_option
def doctor():
    """Verifica hardware, dipendenze, microfono, sessione NotebookLM e servizio."""
    checks = c().app.doctor()

    def human(rows):
        icons = {"ok": click.style("✓", fg="green"), "warn": click.style("!", fg="yellow"),
                 "fail": click.style("✗", fg="red")}
        for r in rows:
            click.echo(f"{icons[r['status']]} {r['check']:<20} {r['detail']}")
            if r.get("hint"):
                click.echo(f"  {'':<20} → {r['hint']}")

    emit(checks, human)
    if any(r["status"] == "fail" for r in checks):
        sys.exit(ExitCode.ERROR)


# ============================================================================ config
@cli.group()
def config():
    """Configurazione (file TOML) e percorsi."""


@config.command("show")
@json_option
def config_show():
    """Mostra la configurazione effettiva e i percorsi di dati e credenziali."""
    data = c().app.config_show()

    def human(d):
        out.kv([("file", d["config_file"] + ("" if d["config_file_exists"] else
                                              " (non presente: valori di default)"))])
        click.echo("")
        for section, values in d["effective"].items():
            click.secho(f"[{section}]", bold=True)
            for k, v in values.items():
                click.echo(f"  {k} = {v!r}")
        if d["overrides"]["audio.device"]:
            click.echo(f"\nMicrofono scelto da CLI (prevale): {d['overrides']['audio.device']}")
        click.echo("")
        out.kv(list(d["paths"].items()))

    emit(data, human)


@config.command("init")
@click.option("--force", is_flag=True, help="Sovrascrive (con backup .bak) un file esistente.")
def config_init(force):
    """Crea ~/.config/gweb/config.toml dall'esempio commentato."""
    path = config_file_path()
    if path.exists() and not force:
        raise UsageError(f"{path} esiste già", hint="Modificalo a mano o usa --force (backup)")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    path.write_text(example_config_text(), encoding="utf-8")
    load_config(path)  # validazione
    out.ok(f"Configurazione creata: {path}")


@config.command("path")
def config_path():
    """Stampa il percorso del file di configurazione."""
    click.echo(str(config_file_path()))


# ============================================================================ audio
@cli.group()
def audio():
    """Microfono: elenco dispositivi, scelta e prova."""


@audio.command("devices")
@json_option
def audio_devices():
    """Elenca i dispositivi di cattura ALSA (indirizzati per ID scheda, non per indice)."""
    data = c().app.audio_devices()

    def human(d):
        rows = [["*" if x["selected"] else "", x["card_id"], x["alsa_pcm"],
                 "sì" if x["is_usb"] else "no", x["name"]] for x in d["devices"]]
        out.table(rows, ["", "ID SCHEDA", "PCM ALSA", "USB", "NOME"])
        click.echo(f"\nConfigurato: {d['configured']} → {d['resolved'] or 'non risolto'}")
        if d["error"]:
            out.warn(d["error"])

    emit(data, human)


@audio.command("select")
@click.argument("device", required=False)
@json_option
def audio_select(device):
    """Sceglie il microfono (ID scheda, PCM ALSA o 'auto'). Persistente."""
    if device is None:
        devices = c().app.audio_devices()["devices"]
        if not devices or not out.is_tty():
            raise UsageError("Indica il dispositivo", hint="gweb audio select Device")
        for i, d in enumerate(devices, 1):
            click.echo(f"{i}) {d['card_id']}  {d['name']}  ({d['alsa_pcm']})")
        idx = click.prompt("Numero", type=click.IntRange(1, len(devices)))
        device = devices[idx - 1]["card_id"]
    data = c().app.audio_select(device)
    emit(data, lambda d: out.ok(f"Microfono: {d['device']} → {d['resolved']}"))


@audio.command("test")
@click.option("--duration", "-d", default=10, show_default=True, type=int,
              help="Secondi di registrazione di prova.")
@json_option
def audio_test(duration):
    """Registra una prova breve e mostra i livelli (picco/RMS in dBFS).

    \b
    Esempio: gweb audio test --duration 10
    """
    def announce(pcm):
        if not c().json:
            click.echo(f"Registrazione di prova per {duration}s da {pcm}: parla nel microfono...")

    data = c().app.audio_test(duration, on_start=announce)

    def human(d):
        out.kv([("file", d["file"]), ("dispositivo", d["device"]),
                ("durata", f"{d['duration_seconds']}s"), ("dimensione", out.size(d["bytes"])),
                ("picco", f"{d['peak_dbfs']} dBFS"), ("rms", f"{d['rms_dbfs']} dBFS"),
                ("esito", d["verdict"])])

    emit(data, human)


# ============================================================================ notebook
@cli.group()
def notebook():
    """Sessione NotebookLM e scelta del notebook predefinito."""


MAC_INSTRUCTIONS = """\
Il login Google richiede un browser: si fa sul Mac, poi si trasferisce la sessione.

1) Sul Mac (una tantum), in un ambiente isolato:
   python3 -m venv ~/.venvs/notebooklm
   ~/.venvs/notebooklm/bin/pip install "notebooklm-py[browser]=={lib}"
   ~/.venvs/notebooklm/bin/playwright install chromium

2) Sul Mac, login (si apre Chromium; completa l'accesso Google, anche 2FA):
   ~/.venvs/notebooklm/bin/notebooklm login

3) Sul Mac, trasferisci la sessione via SSH (cifrata, nessun file temporaneo sul Pi):
   ssh {user}@{host} '{exe} notebook login --import -' \\
       < ~/.notebooklm/profiles/default/storage_state.json

4) Sul Mac, elimina la copia locale (evita che due client ruotino gli stessi cookie):
   rm ~/.notebooklm/profiles/default/storage_state.json

5) Sul Raspberry, verifica (sola lettura):
   gweb notebook status

Destinazione sul Raspberry: {path} (permessi 0600)
"""


def _read_input(src: str) -> bytes:
    if src == "-":
        if sys.stdin.isatty():
            raise UsageError("Nessun dato su stdin", hint="Usa: ssh ... < storage_state.json")
        return sys.stdin.buffer.read()
    p = Path(src).expanduser()
    if not p.is_file():
        raise UsageError(f"File non trovato: {p}")
    return p.read_bytes()


@notebook.command("login")
@click.option("--import", "import_src", metavar="FILE|-",
              help="Importa storage_state.json (da file o stdin '-').")
@click.option("--import-master-token", "token_src", metavar="FILE|-",
              help="Importa master_token.json (opzionale, vedi README: rischi).")
@click.option("--browser", is_flag=True,
              help="Login diretto con browser (solo su macchine con display e Playwright).")
@click.option("--remove-source", is_flag=True, help="Dopo --import FILE elimina il file sorgente.")
@json_option
def notebook_login(import_src, token_src, browser, remove_source):
    """Autenticazione NotebookLM: istruzioni per il Mac o import della sessione.

    \b
    Senza opzioni mostra la procedura completa Mac → Raspberry.
    Esempio (dal Mac):
      ssh pi@raspberrypi.local '~/agency-briefing/.venv/bin/gweb notebook login --import -' \\
          < ~/.notebooklm/profiles/default/storage_state.json
    """
    app = c().app
    if import_src or token_src:
        results = {}
        if import_src:
            results["storage_state"] = app.import_credentials(_read_input(import_src))
            if remove_source and import_src != "-":
                Path(import_src).expanduser().unlink()
                results["storage_state"]["source_removed"] = True
        if token_src:
            results["master_token"] = app.import_credentials(_read_input(token_src),
                                                             master_token=True)
        try:
            results["status"] = app.notebook_status(remote=True)["remote"]
        except AgencyError as exc:
            results["status"] = {"ok": False, **exc.to_dict()}

        def human(d):
            for k, v in d.items():
                if k != "status":
                    out.ok(f"{k} salvato in {v['saved_to']} (0600)")
            st = d["status"]
            if st and st.get("ok"):
                out.ok(f"Sessione valida: {st['notebooks']} notebook"
                       + (f" ({st['account']})" if st.get("account") else ""))
            else:
                out.warn(f"Verifica sessione non riuscita: {st.get('message')}")

        emit(results, human)
        if not (results["status"] or {}).get("ok"):
            sys.exit(ExitCode.AUTH_REQUIRED)
        return
    if browser:
        exe = shutil.which("notebooklm") or str(Path(sys.executable).with_name("notebooklm"))
        env = dict(os.environ, NOTEBOOKLM_HOME=str(app.cfg.notebooklm_home))
        app.cfg.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        rc = subprocess.call([exe, "--storage", str(app.cfg.storage_state_path), "login"],
                             env=env)
        if rc == 0 and app.cfg.storage_state_path.exists():
            app.cfg.storage_state_path.chmod(0o600)
        sys.exit(rc)
    from ..notebook.notebooklm_py import LIBRARY_VERSION

    exe = Path(sys.argv[0]).resolve() if sys.argv[0] else "gweb"
    text = MAC_INSTRUCTIONS.format(lib=LIBRARY_VERSION, user=getpass.getuser(),
                                   host=socket.gethostname() + ".local", exe=exe,
                                   path=app.cfg.storage_state_path)
    emit({"instructions": text, "storage_path": str(app.cfg.storage_state_path)},
         lambda d: click.echo(d["instructions"]))


@notebook.command("logout")
@json_option
def notebook_logout():
    """Elimina la sessione NotebookLM salvata sul Raspberry (per cambiare account).

    L'account Google non viene toccato. Gli upload restano in coda in attesa di login.
    """
    data = c().app.notebook_logout()
    emit(data, lambda d: out.ok("Sessione eliminata" if d["removed"]
                                else "Nessuna sessione da eliminare"))


@notebook.command("status")
@click.option("--offline", is_flag=True, help="Solo controlli locali, nessuna rete.")
@json_option
def notebook_status(offline):
    """Stato della sessione: file locale, permessi e prova remota di sola lettura."""
    data = c().app.notebook_status(remote=not offline)

    def human(d):
        loc = d["local"]
        out.kv([("backend", d["backend"]), ("file sessione", loc["storage_path"]),
                ("presente", "sì" if loc["exists"] else "no"),
                ("permessi", loc.get("mode")),
                ("aggiornato", out.local_time(loc.get("modified_at"))),
                ("master token", "sì" if loc.get("master_token_present") else "no"),
                ("notebook predefinito", (d["default_notebook"] or {}).get("title"))])
        if d.get("keepalive"):
            k = d["keepalive"]
            click.echo(f"ultimo keepalive: {out.local_time(k['at'])} "
                       f"{'ok' if k['ok'] else k['error']}")
        if d["remote"]:
            out.ok(f"Sessione valida: {d['remote']['notebooks']} notebook accessibili"
                   + (f" ({d['remote']['account']})" if d["remote"].get("account") else ""))
        else:
            s = d["session"]
            label = {"ok": "valida all'ultimo controllo", "expired": "SCADUTA: rifai il login",
                     "missing": "assente", "error": "ultimo controllo fallito",
                     "unchecked": "non ancora verificata"}[s["state"]]
            click.echo(f"sessione: {label}" + (f" ({s['account']})" if s["account"] else ""))

    emit(data, human)


@notebook.command("list")
@json_option
def notebook_list():
    """Elenca i notebook dell'account (sola lettura)."""
    data = c().app.notebook_list()
    emit(data, lambda d: out.table(
        [["*" if n["default"] else "", n["id"], n["title"], n["sources_count"],
          out.local_time(n["modified_at"])] for n in d],
        ["", "ID", "TITOLO", "FONTI", "MODIFICATO"]))


@notebook.command("select")
@click.argument("notebook_id", required=False)
@json_option
def notebook_select(notebook_id):
    """Imposta il notebook predefinito (ID, prefisso o parte del titolo; senza argomenti:
    scelta interattiva). Le registrazioni esistenti mantengono il proprio notebook."""
    app = c().app
    if notebook_id is None:
        if not out.is_tty():
            raise UsageError("Modalità non interattiva: indica NOTEBOOK_ID")
        nbs = app.notebook_list()
        if not nbs:
            raise UsageError("Nessun notebook nell'account")
        for i, n in enumerate(nbs, 1):
            mark = "*" if n["default"] else " "
            click.echo(f"{i:>3}{mark} {n['title']}  ({n['sources_count']} fonti)  {n['id']}")
        idx = click.prompt("Numero del notebook", type=click.IntRange(1, len(nbs)))
        notebook_id = nbs[idx - 1]["id"]
    data = app.notebook_select(notebook_id)
    emit(data, lambda d: out.ok(f"Notebook predefinito: {d['title']} ({d['id']})"))


@notebook.command("create")
@click.argument("title")
@click.option("--select", is_flag=True, help="Lo imposta anche come notebook predefinito.")
@json_option
def notebook_create(title, select):
    """Crea un notebook vuoto su NotebookLM.

    \b
    Esempio: gweb notebook create "Cliente Rossi - nuovo sito" --select
    """
    data = c().app.notebook_create(title, select=select)
    emit(data, lambda d: out.ok(f"Notebook creato: {d['title']} ({d['id']})"
                                + (" — predefinito" if d["default"] else "")))


@notebook.command("current")
@json_option
def notebook_current():
    """Mostra il notebook predefinito."""
    data = c().app.notebook_current()
    if data is None and not c().json:
        out.warn("Nessun notebook predefinito: gweb notebook select")
        sys.exit(ExitCode.NOT_FOUND)
    emit(data, lambda d: out.kv([("id", d["id"]), ("titolo", d["title"]),
                                 ("scelto il", out.local_time(d["selected_at"]))]))


@notebook.command("sources")
@click.option("--notebook", "-n", "nb", help="Notebook (default: predefinito).")
@json_option
def notebook_sources(nb):
    """Elenca le fonti di un notebook con il loro stato di elaborazione."""
    data = c().app.notebook_sources(nb)
    emit(data, lambda d: out.table(
        [[s["id"], s["title"], s["status"], s["kind"]] for s in d["sources"]],
        ["ID FONTE", "TITOLO", "STATO", "TIPO"]))


# ============================================================================ record
@cli.group()
def record():
    """Avvio, stato e arresto della registrazione (eseguita dal servizio)."""


def _print_rec(r: dict) -> None:
    out.kv([("id", r["id"]), ("titolo", r["title"]),
            ("notebook", r["notebook_title"] or r["notebook_id"]),
            ("stato", out.rec_state(r["state"])), ("upload", out.upload_state(r["upload_status"])),
            ("avviata", out.local_time(r["started_at"], "%Y-%m-%d %H:%M:%S")),
            ("terminata", out.local_time(r["ended_at"], "%Y-%m-%d %H:%M:%S")),
            ("durata", out.duration(r["duration_seconds"])),
            ("file", r["file_path"]), ("dimensione", out.size(r["file_size"])),
            ("dispositivo", r["device"]), ("fonte remota", r["remote_source_id"]),
            ("audio locale", (f"eliminato il {out.local_time(r['purged_at'])} "
                              f"({'tutto' if r['purged_scope'] == 'all' else 'solo WAV'}, "
                              f"{out.size(r['purged_bytes'])})") if r.get("purged_at")
             else "presente"),
            ("errore", r["error"])])


@record.command("start")
@click.option("--title", "-t", required=True, help="Titolo del briefing.")
@click.option("--notebook", "-n", "nb",
              help="Notebook da associare (default: predefinito al momento dell'avvio).")
@click.option("--no-wait", is_flag=True, help="Non attendere la conferma del servizio.")
@json_option
def record_start(title, nb, no_wait):
    """Avvia una registrazione in background (continua anche chiudendo SSH).

    \b
    Esempio: gweb record start --title "Briefing nuovo sito" --notebook NOTEBOOK_ID
    """
    data = c().app.record_start(title, nb, wait=not no_wait)

    def human(d):
        r = d["recording"]
        out.ok(f"Registrazione {r['id']} «{r['title']}»: {out.rec_state(r['state'])}")
        click.echo(f"  notebook: {r['notebook_title'] or r['notebook_id'] or '-'}")
        click.echo("  stato: gweb record status · arresto: gweb record stop")
        for w in d["warnings"]:
            out.warn(w)

    emit(data, human)


@record.command("status")
@json_option
def record_status():
    """Stato della registrazione in corso (durata, segmenti, spazio, ultimi eventi)."""
    data = c().app.record_status()

    def human(d):
        if not d["daemon"]["alive"]:
            out.warn("Servizio non attivo")
        if not d["active"]:
            click.echo("Nessuna registrazione in corso.")
            if d["last"]:
                lr = d["last"]
                click.echo(f"Ultima: {lr['id']} «{lr['title']}» — {out.rec_state(lr['state'])}"
                           f", upload: {out.upload_state(lr['upload_status'])}")
            return
        r = d["recording"]
        click.secho(f"● {out.rec_state(r['state']).upper()}  {r['id']} «{r['title']}»",
                    fg="red", bold=True)
        out.kv([("trascorso", out.duration(d["elapsed_seconds"])),
                ("audio salvato", out.duration(d["recorded_seconds"])),
                ("segmenti", d["segments"]), ("dispositivo", r["device"]),
                ("notebook", r["notebook_title"] or r["notebook_id"]),
                ("spazio libero", f"{d['disk_free_mb']} MB"), ("avviso", r["error"])])
        for e in d["events"]:
            click.echo(f"  {out.local_time(e['at'], '%H:%M:%S')} {e['message']}")

    emit(data, human)


@record.command("stop")
@click.option("--wait-final", is_flag=True, help="Attendi anche la codifica del file finale.")
@json_option
def record_stop(wait_final):
    """Arresta la registrazione in corso (arresto controllato, file finalizzato)."""
    data = c().app.record_stop(wait=True, wait_final=wait_final)

    def human(d):
        r = d["recording"]
        out.ok(f"Registrazione {r['id']} arrestata: {out.rec_state(r['state'])}, "
               f"durata {out.duration(r['duration_seconds'])}")
        for w in d["warnings"]:
            out.warn(w)
        click.echo(f"  dettagli: gweb recordings show {r['id']}")
        click.echo(f"  caricamento: gweb recordings upload {r['id']}")

    emit(data, human)


# ============================================================================ recordings
@cli.group()
def recordings():
    """Archivio delle registrazioni e caricamento su NotebookLM."""


@recordings.command("list")
@click.option("--limit", default=30, show_default=True)
@json_option
def recordings_list(limit):
    """Elenca le registrazioni (più recenti prima)."""
    data = c().app.recordings_list(limit)
    emit(data, lambda d: out.table(
        [[r["id"], out.local_time(r["created_at"]), r["title"], out.duration(r["duration_seconds"]),
          out.rec_state(r["state"]), out.upload_state(r["upload_status"]),
          r["notebook_title"] or r["notebook_id"]] for r in d],
        ["ID", "CREATA", "TITOLO", "DURATA", "STATO", "UPLOAD", "NOTEBOOK"]))


@recordings.command("show")
@click.argument("recording_id")
@json_option
def recordings_show(recording_id):
    """Dettagli di una registrazione: metadati, segmenti, lavori, riepiloghi, eventi."""
    data = c().app.recordings_show(recording_id)

    def human(d):
        _print_rec(d["recording"])
        click.echo(f"\nSegmenti: {len(d['segments'])} "
                   f"({out.size(sum(s['bytes'] for s in d['segments']))})")
        if d["jobs"]:
            click.echo("\nLavori:")
            for j in d["jobs"]:
                click.echo(f"  #{j['id']} {j['state']} notebook={j['notebook_id']} "
                           f"tentativi={j['attempts']} {j['last_error'] or ''}")
        if d["summaries"]:
            click.echo("\nRiepiloghi:")
            for s in d["summaries"]:
                click.echo(f"  {out.local_time(s['created_at'])} {s['file_path']}")
        if d["events"]:
            click.echo("\nEventi:")
            for e in d["events"][-15:]:
                click.echo(f"  {out.local_time(e['at'], '%m-%d %H:%M:%S')} [{e['level']}] "
                           f"{e['message']}")

    emit(data, human)


@recordings.command("upload")
@click.argument("recording_id")
@click.option("--notebook", "-n", "nb", help="Notebook di destinazione (default: associato).")
@click.option("--wait", is_flag=True, help="Attendi la fine del trasferimento.")
@json_option
def recordings_upload(recording_id, nb, wait):
    """Mette in coda il caricamento della registrazione come fonte audio.

    \b
    Esempi:
      gweb recordings upload r261102-153012-a1b2
      gweb recordings upload r261102-153012-a1b2 --notebook NOTEBOOK_ID
    """
    data = c().app.recordings_upload(recording_id, nb, wait=wait)

    def human(d):
        j = d["job"]
        out.ok(f"Lavoro #{j['id']} → notebook {j['notebook_id']}: {j['state']}")
        if j["state"] == "processing":
            click.echo("  Upload completato: la fonte è in elaborazione su NotebookLM.")
        click.echo("  stato: gweb jobs list")
        for w in d["warnings"]:
            out.warn(w)

    emit(data, human)


@recordings.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--title", "-t", required=True, help="Titolo del briefing.")
@click.option("--notebook", "-n", "nb", help="Notebook da associare.")
@json_option
def recordings_import(path, title, nb):
    """Importa un audio esistente (MP3/M4A senza conversione; altri formati codificati).

    \b
    Esempio: gweb recordings import /percorso/audio.wav --title "Briefing esterno"
    """
    data = c().app.recordings_import(path, title, nb)
    emit(data, lambda d: (out.ok(f"Importata {d['recording']['id']}"),
                          _print_rec(d["recording"])))


@recordings.command("prune")
@click.option("--yes", is_flag=True, help="Esegue davvero (senza: solo simulazione).")
@click.option("--min-age-days", type=float, help="Override di cleanup.min_age_days.")
@click.option("--mode", type=click.Choice(["all", "segments"]),
              help="all = tutto l'audio locale; segments = solo WAV (tiene l'M4A).")
@json_option
def recordings_prune(yes, min_age_days, mode):
    """Elimina l'audio locale delle registrazioni già PRONTE su NotebookLM.

    Ogni fonte viene ricontrollata su NotebookLM prima di eliminare; se la rete o la
    sessione non funzionano non si elimina nulla. Metadati e riepiloghi restano.
    Attenzione: con --mode all l'audio originale non è recuperabile da NotebookLM.

    \b
    Esempi:
      gweb recordings prune                 # simulazione: cosa verrebbe eliminato
      gweb recordings prune --yes
      gweb recordings prune --yes --mode segments --min-age-days 0
    """
    data = c().app.recordings_prune(execute=yes, min_age_days=min_age_days, mode=mode)

    def human(d):
        todo = d["deleted"] if yes else d["would_delete"]
        verb = "Eliminati" if yes else "Da eliminare (simulazione)"
        click.secho(f"{verb}: {len(todo)} registrazioni, "
                    f"{out.size(d['freed_bytes'] if yes else d['would_free_bytes'])} "
                    f"(modalità {d['mode']}, età minima {d['min_age_days']:g} giorni)", bold=True)
        for i in todo:
            click.echo(f"  {i['recording_id']}  {i['title']}  {out.size(i['bytes'])}")
        if d["skipped"]:
            click.echo("Mantenute:")
            for i in d["skipped"]:
                click.echo(f"  {i['recording_id']}  {i['title']}  — {i['reason']}")
        if not yes and todo:
            click.echo("Per eseguire: gweb recordings prune --yes")
        click.echo(f"Spazio libero: {d['disk_free_mb']} MB")

    emit(data, human)


@recordings.command("finalize")
@click.argument("recording_id")
@json_option
def recordings_finalize(recording_id):
    """Ripete unione e codifica dei segmenti (es. dopo un errore di ffmpeg)."""
    data = c().app.recordings_finalize(recording_id)
    emit(data, lambda d: _print_rec(d["recording"]))


# ============================================================================ jobs
@cli.group()
def jobs():
    """Coda persistente dei caricamenti."""


JOB_HELP = {
    "pending": "in coda", "running": "in corso", "processing": "caricato, fonte in elaborazione",
    "uncertain": "esito incerto: riconciliazione", "blocked_auth": "attende login",
    "succeeded": "fonte pronta", "failed": "fallito", "cancelled": "annullato",
}


@jobs.command("list")
@click.option("--open", "only_open", is_flag=True, help="Solo lavori non conclusi.")
@json_option
def jobs_list(only_open):
    """Elenca i lavori di caricamento con stato, tentativi e ultimo errore."""
    data = [j.to_dict() for j in c().app.queue.list(include_done=not only_open)]
    emit(data, lambda d: out.table(
        [[j["id"], j["recording_id"], JOB_HELP.get(j["state"], j["state"]), j["attempts"],
          out.local_time(j["next_attempt_at"], "%m-%d %H:%M") if j["next_attempt_at"] else "",
          j["remote_source_id"], (j["error_code"] or "") + (" " + j["last_error"]
                                                           if j["last_error"] else "")]
         for j in d],
        ["ID", "REGISTRAZIONE", "STATO", "TENT.", "PROSSIMO", "FONTE", "ERRORE"]))


@jobs.command("retry")
@click.argument("job_id", type=int)
@json_option
def jobs_retry(job_id):
    """Ritenta subito un lavoro (fallito, in attesa o incerto). Non crea duplicati:
    prima di reinviare verifica sempre se la fonte esiste già nel notebook."""
    job = c().app.queue.retry(job_id)
    emit(job.to_dict(), lambda j: out.ok(f"Lavoro #{j['id']}: {j['state']}"))


@jobs.command("cancel")
@click.argument("job_id", type=int)
@json_option
def jobs_cancel(job_id):
    """Annulla un lavoro non concluso."""
    job = c().app.queue.cancel(job_id)
    emit(job.to_dict(), lambda j: out.ok(f"Lavoro #{j['id']} annullato"))


@jobs.command("resolve")
@click.argument("job_id", type=int)
@click.option("--source-id", help="La fonte esiste: indica il suo ID (gweb notebook sources).")
@click.option("--not-uploaded", is_flag=True, help="Verificato che la fonte NON esiste.")
@json_option
def jobs_resolve(job_id, source_id, not_uploaded):
    """Risolve a mano un esito incerto dopo aver controllato il notebook."""
    job = c().app.queue.resolve(job_id, source_id=source_id, not_uploaded=not_uploaded)
    emit(job.to_dict(), lambda j: out.ok(f"Lavoro #{j['id']}: {j['state']}"))


@jobs.command("run")
@json_option
def jobs_run():
    """Elabora la coda in primo piano (solo con servizio fermo; diagnostica)."""
    data = c().app.jobs_run()
    emit(data, lambda d: [click.echo(f"#{j['id']} → {j['state']} {j['last_error'] or ''}")
                          for j in d] or click.echo("Nessun lavoro da eseguire."))


# ============================================================================ ask / briefing
def _print_answer(d: dict) -> None:
    click.echo(d["text"].strip())
    if d.get("citations"):
        click.secho("\nCitazioni:", bold=True)
        for cit in d["citations"]:
            excerpt = (cit.get("excerpt") or "").replace("\n", " ").strip()
            if len(excerpt) > 220:
                excerpt = excerpt[:217] + "..."
            click.echo(f"  [{cit['number']}] {cit.get('source_title') or cit.get('source_id')}"
                       f"\n      {excerpt}")


@cli.command()
@click.argument("question")
@click.option("--notebook", "-n", "nb", help="Notebook (default: predefinito).")
@click.option("--recording", "-r", help="Limita la domanda alla fonte di questa registrazione.")
@click.option("--source", "-s", "sources", multiple=True, help="Limita a queste fonti (ripetibile).")
@json_option
def ask(question, nb, recording, sources):
    """Fa una domanda al notebook e mostra risposta e citazioni.

    \b
    Esempio: gweb ask --notebook NOTEBOOK_ID "Quali decisioni sono state prese?"
    Nota: la domanda si aggiunge alla conversazione corrente del notebook.
    """
    data = c().app.ask(question, nb, recording=recording, sources=list(sources))
    emit(data, _print_answer)


@cli.group()
def briefing():
    """Riepiloghi strutturati dei briefing."""


@briefing.command("summarize")
@click.argument("recording_id")
@click.option("--compare", type=click.Choice(["auto", "yes", "no"]), default="auto",
              show_default=True,
              help="Confronto con le altre fonti per richieste fuori preventivo.")
@json_option
def briefing_summarize(recording_id, compare):
    """Riepilogo (sintesi, richieste, decisioni, attività, responsabili/scadenze espliciti,
    domande aperte) limitato alla fonte della registrazione. Salvato in locale."""
    data = c().app.briefing_summarize(recording_id, compare=compare)

    def human(d):
        _print_answer(d["summary"])
        click.echo("")
        if d.get("scope"):
            _print_answer(d["scope"])
        else:
            click.echo(f"Confronto fuori preventivo non eseguito: {d['scope_skipped_reason']}")
        click.echo("")
        out.ok(f"Salvato: {d['file']}")

    emit(data, human)


# ============================================================================ service
@cli.group()
def service():
    """Servizio systemd utente in background."""


@service.command("status")
@json_option
def service_status():
    """Stato del servizio, del demone, della registrazione e della coda."""
    data = c().app.service_status()

    def human(d):
        u, dm = d["systemd"], d["daemon"]
        out.kv([("unità", f"{u['unit_file']} ({'installata' if u['installed'] else 'assente'})"),
                ("systemd", f"attivo={u['active']} abilitato={u['enabled']}"),
                ("linger", u["linger"]),
                ("demone", f"vivo, pid {dm['pid']}, heartbeat {dm['heartbeat_age_seconds']}s"
                 if dm["alive"] else "non attivo"),
                ("registrazione", (d["active_recording"] or {}).get("id") or "nessuna"),
                ("coda", ", ".join(f"{k}={v}" for k, v in d["jobs"].items()) or "vuota"),
                ("pulizia automatica", (f"attiva, prossima: {u['cleanup_timer']['next']}"
                                        if u.get("cleanup_timer") else "non installata")),
                ("spazio libero", f"{d['disk_free_mb']} MB")])

    emit(data, human)
    if not data["daemon"]["alive"]:
        sys.exit(ExitCode.SERVICE_UNAVAILABLE)


@service.command("logs")
@click.option("--lines", "-n", default=100, show_default=True)
@click.option("--follow", "-f", is_flag=True)
def service_logs(lines, follow):
    """Log del servizio (journald; in alternativa il file di log locale)."""
    from .. import systemd

    app = c().app
    argv = systemd.logs_command(app.cfg.get("service.unit_name"), lines, follow)
    if argv and systemd.systemctl_available():
        sys.exit(subprocess.call(argv))
    log_file = app.cfg.log_dir / "gweb.log"
    if not log_file.exists():
        raise UsageError("Nessun log disponibile", hint=str(log_file))
    sys.exit(subprocess.call(["tail", "-n", str(lines)] + (["-f"] if follow else [])
                             + [str(log_file)]))


@service.command("install")
@click.option("--no-start", is_flag=True, help="Installa e abilita senza avviare.")
@click.option("--print", "print_only", is_flag=True, help="Mostra l'unità senza scriverla.")
def service_install(no_start, print_only):
    """Installa e abilita l'unità systemd utente (avvio automatico, riavvio su errore)."""
    from .. import systemd

    app = c().app
    if print_only:
        click.echo(systemd.render_unit(app.cfg.source_path))
        return
    for step in systemd.install_unit(app.cfg.get("service.unit_name"), app.cfg.source_path,
                                     start=not no_start):
        click.echo("  " + step)
    for step in systemd.install_cleanup_timer(app.cfg.get("cleanup.schedule"),
                                              bool(app.cfg.get("cleanup.enabled"))):
        click.echo("  " + step)
    if systemd.linger_enabled() is False:
        out.warn("Per l'avvio al boot senza login esegui una volta: "
                 "sudo loginctl enable-linger $USER")


def _ctl(action):
    from .. import systemd

    res = systemd.control(action, c().app.cfg.get("service.unit_name"))
    if res.returncode != 0:
        raise AgencyError(f"systemctl --user {action} fallito: {res.stderr.strip()}")
    out.ok(f"{action}: ok")


@service.command("start")
def service_start():
    """Avvia il servizio."""
    _ctl("start")


@service.command("stop")
def service_stop():
    """Arresta il servizio (una registrazione in corso riprende al riavvio entro la finestra
    configurata, altrimenti viene finalizzata)."""
    _ctl("stop")


@service.command("restart")
def service_restart():
    """Riavvia il servizio."""
    _ctl("restart")


@service.command("run")
def service_run():
    """Esegue il demone in primo piano (usato da systemd; utile per debug)."""
    from ..daemon import Daemon

    app = c().app
    setup_logging(app.cfg.log_dir, logging.DEBUG if c().verbose > 1 else logging.INFO)
    sys.exit(Daemon(app).run())


# ============================================================================ entry
def main(argv: list[str] | None = None) -> None:
    json_mode = "--json" in (argv if argv is not None else sys.argv[1:])
    try:
        cli.main(args=argv, standalone_mode=False, prog_name="gweb")
    except click.exceptions.Abort:
        click.echo("Annullato.", err=True)
        sys.exit(130)
    except click.ClickException as exc:
        if json_mode:
            out.dump_json({"ok": False, "error": {"code": "USAGE", "message": exc.format_message()}})
        else:
            exc.show()
        sys.exit(ExitCode.USAGE)
    except AgencyError as exc:
        if json_mode:
            out.dump_json({"ok": False, "error": exc.to_dict()})
        else:
            out.error(exc.message, exc.hint)
        sys.exit(exc.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
