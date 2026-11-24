"""Game Diagnostics, the client scan, Login Doctor: the logic of the
Tk ``diagnostics_ui.View`` and ``_open_login_doctor`` without widgets.

Everything here reads: the launch records on disk (``diagnostics.Store``), the
game folder (``clientscan``), the manifest's metadata, the local login
configuration and plain connectivity. The two operations that write are
explicit and previewed: *Export support ZIP* (to a file the player picks, with
names, folder paths and secrets removed) and *Repair login configuration*
(login lines only, each file backed up as ``.octobak``). Opening any of it never
starts the game.

Lines for the views are ``(text, tone)`` with tone one of ``head``, ``big``,
``ok``, ``err``, ``warn``, ``dim`` or ``""``.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from .. import clientscan, diagnostics, logindoctor, platforms
from ..i18n import N_, tr
from . import logsink
from .launch_funcs import get_client_version
from .manifest import (_bdecode, _torrent_excluded_files, fetch_torrent,
                       torrent_version)
from .mods import MODS_REGISTRY
from .net import secure_urlopen
from .ops_host import OpsHost

OUTCOME_TONE = {
    "blocked": "err", "launcher_error": "err", "launcher_says": "err",
    "crash": "err", "exit_error": "err", "early_exit": "warn", "no_game": "warn",
    "launcher_stuck": "warn", "handed_off": "warn", "exit_unknown": "warn",
    "unavailable": "warn", "running": "ok", "exit_normal": "ok",
    "in_progress": "dim",
}
DOCTOR_MARK = {logindoctor.PASS: ("✓", "ok"), logindoctor.WARN: ("⚠", "warn"),
               logindoctor.FAIL: ("✗", "err")}


def when(epoch) -> str:
    if not epoch:
        return tr("unknown")
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def attempt_lines(data: dict, is_last: bool) -> list:
    """The detail of one recorded launch (``View.show_attempt``)."""
    outcome, explanation = diagnostics.classify(data)
    route = data.get("route") or {}
    out = [(tr("Last launch") if is_last else tr("Launch"), "head"),
           (when(data.get("started_at")), "dim"),
           (tr(diagnostics.OUTCOMES.get(outcome, outcome)),
            OUTCOME_TONE.get(outcome, "dim")),
           (explanation, ""), ("", "")]
    ended = [e.get("t") for e in data.get("events", []) if e.get("t") is not None]
    if ended:
        out.append((f"{tr('Duration')}: {ended[-1]:.1f} s", "dim"))
    if route:
        out.append((f"{tr('Route')}: {route.get('runner')}  "
                    f"{route.get('target') or ''}", "dim"))
        out.append((" ".join(route.get("argv") or []), "dim"))
    for p in [p for p in data.get("preflight", []) if not p.get("ok")]:
        out.append((f"✕ {p['check']}: {p['detail']}", "err"))
    for line in diagnostics.component_findings(data.get("components") or [],
                                               route.get("target") or ""):
        out.append((f"! {line}", "warn"))
    crash = data.get("crash") or {}
    for r in crash.get("reports") or []:
        out.append((f"{tr('Crash report')}: {r.get('file')}  "
                    f"{r.get('exception_code') or ''} {r.get('module') or ''}", "err"))
    for ev in crash.get("events") or []:
        out.append((f"{ev.get('provider')}: {ev.get('application')} "
                    f"{ev.get('exception_code')} {ev.get('module')} "
                    f"{ev.get('offset')}", "err"))
    for r in crash.get("older_reports") or []:
        out.append((f"{r.get('file')}: {r.get('why_excluded') or ''}", "dim"))
    if data.get("loaded_modules"):
        out += [("", ""), (tr("Observed loaded"), "head")]
        out += [(m, "dim") for m in data["loaded_modules"][:30]]
    if route.get("handoff"):
        out += [("", ""), (diagnostics.STEAM_LOG_HELP, "warn")]
    out += [("", ""), (tr("Timeline"), "head")]
    for e in data.get("events", []):
        at = "    ?" if e.get("t") is None else f"{e['t']:6.1f}s"
        out.append((f"{at}  {e.get('kind')}  {diagnostics.event_details(e)}", "dim"))
        if e.get("output_tail"):
            out.append((e["output_tail"], "dim"))
    for line in data.get("limits") or []:
        out.append((f"· {line}", "warn"))
    return out


def scan_lines(scan: dict) -> list:
    cov = scan.get("coverage") or {}
    n = clientscan.counts(scan)
    out = [(tr("Folder scan"), "head"),
           (when(scan.get("finished_at")) + f"  ({scan.get('mode')})", "dim"),
           (f"{tr('Confirmed problems')}: {n['confirmed']}   "
            f"{tr('Possible conflicts')}: {n['possible']}   "
            f"{tr('Information')}: {n['info']}", "big"), ("", ""),
           (tr("Coverage"), "head"),
           (f"{cov.get('files', 0)} files, {cov.get('bytes', 0) // (1024 * 1024)} MB "
            f"listed; {cov.get('skipped', 0)} skipped; {cov.get('unreadable', 0)} "
            f"unreadable; {cov.get('private_files', 0)} in private folders listed "
            f"by count only.", "dim"),
           (f"Manifest: {cov.get('manifest') or 'none'}, "
            f"{cov.get('manifest_files', 0)} files. Hash-checked "
            f"{cov.get('hashed', 0)}, not hash-checked {cov.get('hash_skipped', 0)}. "
            f"MPQ archives verified: {'yes' if cov.get('mpq_verified') else 'no'}."
            + (" Stopped early." if cov.get("cancelled") else ""), "dim"),
           ("", "")]
    for level, title, tone in (("confirmed", tr("Confirmed problems"), "err"),
                               ("possible", tr("Possible conflicts"), "warn"),
                               ("info", tr("Information"), "dim")):
        items = [f for f in scan.get("findings", []) if f.get("level") == level]
        if not items:
            continue
        out.append((title, "head"))
        for f in items[:200]:
            out.append((f.get("path") or f.get("code"), tone))
            out.append(("    " + (f.get("why") or ""), ""))
            if f.get("evidence"):
                out.append(("    " + f["evidence"], "dim"))
        out.append(("", ""))
    if scan.get("recent_changes"):
        out.append((tr("Recently changed launch files"), "head"))
        out += [(f"{when(c.get('modified'))}  {c.get('file')}", "dim")
                for c in scan["recent_changes"][:20]]
    return out


def client_manifest() -> dict | None:
    """The manifest (file names, sizes, piece hashes) read from the client
    torrent's metadata. Nothing is fetched into the game folder."""
    raw, files = fetch_torrent()
    decoded, _ = _bdecode(raw)
    info = decoded.get(b"info", {})
    return {"name": f"client torrent {torrent_version(raw)[:12]}",
            "files": files, "piece_length": int(info.get(b"piece length", 0)),
            "pieces": info.get(b"pieces") or b""}


def doctor_fetch(url: str, timeout: float) -> int:
    """One HTTPS request for Login Doctor. Any HTTP answer - an error status or
    the DDoS-protection page included - means the host was reached; only a
    failure to connect raises."""
    from .. import branding
    req = urllib.request.Request(url, headers={"User-Agent": branding.USER_AGENT})
    try:
        with secure_urlopen(req, timeout=timeout,
                            allowed_hosts={"octowow.st", "dl.octowow.st"}) as r:
            r.read(4096)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class DiagnosticsService:
    def __init__(self, host: OpsHost, tasks, manifest=None, fetch=None,
                 resolve=None, connect=None):
        self.host, self.tasks = host, tasks
        self._manifest = manifest or client_manifest
        self._fetch = fetch or doctor_fetch
        self._net = {k: v for k, v in (('resolve', resolve), ('connect', connect)) if v}
        self.scan_cancel: threading.Event | None = None
        self.scanning = self.exporting = False
        self.status = ""
        self.on_change = lambda: None          # the bridge repaints on this
        self.doctor: dict = {"client": "", "local": [], "network": None, "running": False}
        self._doctor_gen = 0

    # -- helpers -------------------------------------------------------------
    @property
    def store(self):
        return self.host._diag_store()

    def client(self) -> str:
        return self.host._game_path.get().strip()

    def say(self, text: str):
        self.status = text
        self.on_change()

    # -- recorded launches ------------------------------------------------------
    def attempts(self) -> list:
        rows = []
        for data in self.store.recent():
            outcome = data.get("outcome") or "unavailable"
            rows.append({"key": data.get("id"), "id": data.get("id"),
                         "outcome": outcome,
                         "outcomeText": tr(diagnostics.OUTCOMES.get(outcome, outcome)),
                         "tone": OUTCOME_TONE.get(outcome, "dim"),
                         "when": when(data.get("started_at"))[5:]})
        return rows

    def attempt_detail(self, attempt_id) -> list:
        recent = self.store.recent()
        for i, data in enumerate(recent):
            if data.get("id") == attempt_id:
                return attempt_lines(data, i == 0)
        return [(tr("No launch has been recorded yet."), "dim")]

    def recheck(self, attempt_id, on_done):
        """Look again for what an attempt left behind. Read-only; a worker."""
        store = self.store
        self.say(tr("Checking for new evidence…"))

        def work():
            fresh = store.load(attempt_id)
            if not fresh or fresh.get("damaged"):
                return
            later = [store.load(i).get("started_at") for i in store.ids() if i > attempt_id]
            later = [t for t in later if t]
            diagnostics.collect_evidence(fresh, until=min(later) if later else None)
            fresh["outcome"], fresh["explanation"] = diagnostics.classify(fresh)
            store.write_json(os.path.join(store.attempt_dir(attempt_id), "attempt.json"), fresh)

        def done(_r):
            self.say("")
            on_done()
        self.tasks.submit("diag:recheck", work, on_done=done,
                          on_error=lambda e: self.say(str(e)))

    def launch_diagnostic(self):
        """One launch with extra logging, for this launch only (the caller asks
        first)."""
        if self.host._btn_mode == "play":
            self.tasks.submit("launch:start", lambda: self.host._launch_game(diagnostic=True))

    def diagnostic_note(self) -> str:
        from ..config import load_config
        command = (load_config().get("game_launcher") or {}).get("launch_command") or ""
        note = tr("This starts the game once with extra logging, for this launch "
                  "only. Your settings are not changed.")
        if diagnostics.handoff_of(command.split()):
            note += "\n\n" + diagnostics.STEAM_LOG_HELP
        return note

    # -- the folder scan (also "Verify game files": read-only) -----------------------
    def start_scan(self, deep: bool, include_mpq: bool, on_done):
        client = self.client()
        if not client or not os.path.isdir(client):
            self.say(tr("No game folder is set."))
            return False
        if self.scanning:
            return False
        self.scanning = True
        self.scan_cancel = cancel = threading.Event()
        mode = clientscan.DEEP if deep else clientscan.QUICK
        last = {"t": 0.0}

        def progress(text, fraction=None):
            now = time.monotonic()
            if now - last["t"] < 0.2:
                return
            last["t"] = now
            shown = text + (f"  {fraction:.0%}" if fraction is not None else "")
            self.host.after(0, lambda t=shown: self.say(t))

        def work():
            scan = self.run_scan(client, mode, include_mpq, cancel, progress)
            self.store.save_scan(scan)
            return scan

        def done(scan):
            self.scanning = False
            self.say(tr("The scan stopped early; the results cover what it reached.")
                     if scan["coverage"].get("cancelled") else "")
            on_done(scan)

        def failed(exc):
            self.scanning = False
            self.say(str(exc))
        self.say(tr("Scanning…"))
        return self.tasks.submit("diag:scan", work, on_done=done, on_error=failed)

    def cancel_scan(self):
        if self.scan_cancel is not None:
            self.scan_cancel.set()
            self.say(tr("Stopping…"))

    def latest_scan(self):
        return self.store.latest_scan(self.client())

    def run_scan(self, client, mode, include_mpq, cancel, progress) -> dict:
        """One scan, gathering what it reuses. A manifest that cannot be read
        narrows coverage and says so (the scan never fails for it)."""
        from ..config import load_config
        from ..hashing import files_hash
        progress(tr("Reading the client manifest…"))
        try:
            manifest = self._manifest()
        except Exception as e:
            logsink.log(f"Game Diagnostics: no client manifest: {e}")
            manifest = None
        cfg = load_config()
        protected = set(_torrent_excluded_files()) | {"wow.exe"}
        if cfg.get("ignore_speech"):
            protected.add("speech.mpq")
        checks = []
        try:
            running = platforms.client_in_use(client)
            for c in logindoctor.local_checks(client, get_client_version(client), running):
                if c.state == logindoctor.PASS or getattr(c, "informational", False):
                    continue
                level = "confirmed" if c.state == logindoctor.FAIL else "possible"
                checks.append((level, c.message.format(**c.values), c.group))
        except Exception as e:
            logsink.log(f"Game Diagnostics: Login Doctor's checks failed: {e}")
        target = self.host._launch_target(client) or platforms.GAME_EXE
        components = diagnostics.launch_components(client, target)
        store = self.store
        os.makedirs(store.scans_dir(), exist_ok=True)
        inventory = os.path.join(store.scans_dir(),
                                 time.strftime("%Y%m%d-%H%M%S") + ".inventory.txt")
        scan = clientscan.scan(
            client, mode, include_mpq, manifest, cfg.get("mods"),
            {m["id"]: m["name"] for m in MODS_REGISTRY}, files_hash, protected,
            checks, components, cancel, progress, inventory)
        for line in diagnostics.component_findings(components, target):
            scan["findings"].append(clientscan.finding(
                "confirmed", "launch_component", line, "", "launch components"))
        return scan

    # -- the support bundle ---------------------------------------------------------------
    def export_preview(self, attempt_id) -> tuple:
        """(text to confirm, the plan) - what goes in, what is left out, and
        that names, paths and passwords are removed."""
        data = self.store.load(attempt_id)
        scan = self.store.latest_scan(data.get("client_root"))
        lines = [m for _t, m, _g in list(logsink.recent)][-500:]
        include, omitted = diagnostics.plan_bundle(data, scan, self.store, lines)
        text = (tr("This report will contain:") + "\n"
                + "\n".join(f"  • {name}" for name, _why in include) + "\n\n"
                + tr("Not included:") + "\n"
                + "\n".join(f"  • {name}: {why}" for name, why in omitted) + "\n\n"
                + tr("Your user name, folder paths and passwords are removed. "
                     "Nothing is sent anywhere: you choose who gets the file."))
        return text, (data, scan, lines)

    def export(self, plan, dest: str, on_done):
        data, scan, lines = plan
        self.exporting = True
        self.say(tr("Writing the report…"))

        def work():
            diagnostics.export_bundle(dest, data, scan, self.store, lines)
            return tr("Saved to {path}", path=dest)

        def done(text):
            self.exporting = False
            self.say(text)
            on_done()

        def failed(exc):
            self.exporting = False
            self.say(tr("The report could not be written: {error}", error=exc))
        self.tasks.submit("diag:export", work, on_done=done, on_error=failed)

    # -- Login Doctor ---------------------------------------------------------------------
    def doctor_run(self, on_change):
        """Local checks now (file reads), network checks on a worker."""
        self._doctor_gen += 1
        gen = self._doctor_gen
        client = self.client()
        if not client or not os.path.isdir(client):
            local = [logindoctor.Check(logindoctor.FAIL, N_("No game folder is set"), {})]
            running = False
        else:
            running = platforms.client_in_use(client)
            local = logindoctor.local_checks(client, get_client_version(client), running)
        self.doctor = {"client": client, "local": local, "running": running, "network": None}

        def work():
            try:
                return logindoctor.network_checks(self._fetch, **self._net)
            except Exception as e:                    # never leave the view waiting
                return [logindoctor.Check(logindoctor.FAIL,
                                          N_("Network checks failed: {error}"),
                                          {"error": str(e)}, "network")]

        def done(net):
            if gen == self._doctor_gen:
                self.doctor["network"] = net
                on_change()
        self.tasks.submit("doctor:network", work, on_done=done)
        on_change()

    def doctor_lines(self) -> list:
        st = self.doctor
        out = []

        def section(title, checks):
            out.append((tr(title), "head"))
            for c in checks:
                mark, tone = DOCTOR_MARK[c.state]
                out.append((f"  {mark}  " + tr(c.message, **c.values), tone))
            out.append(("", ""))
        local = st["local"]
        section(N_("Game client"), [c for c in local if c.group == "client"])
        realm = [c for c in local if c.group in ("realmlist", "config")]
        if realm:
            section(N_("Login configuration"), realm)
        octo = [c for c in local if c.group == "octologin"]
        if octo:
            section(N_("OctoLogin"), octo)
        net = st["network"]
        if net is None:
            out.append((tr("Checking the connection…"), "dim"))
        else:
            section(N_("Connection"), net)
            done = logindoctor.summary(local + net)
            if done:
                out.append((tr(done), ""))
        return out

    def priority_note(self) -> str:
        return tr(logindoctor.PRIORITY_NOTE)

    def repair_plan(self) -> list:
        c = self.doctor["client"]
        return logindoctor.repair_plan(c) if c and os.path.isdir(c) else []

    def can_repair(self) -> bool:
        return bool(self.repair_plan()) and not self.doctor["running"]

    def repair_text(self) -> str:
        client = self.doctor["client"]
        files = "\n".join("    " + os.path.relpath(p, client) for p, _t in self.repair_plan())
        return tr("These files will be changed:\n\n{files}\n\nOnly their login "
                  "lines change: the realm address is set to {host}. A copy of "
                  "each is kept beside it as {ext} (an existing copy is never "
                  "replaced).\n\nRepair now?", files=files,
                  host=logindoctor.LOGIN_HOST, ext=logindoctor.BACKUP_EXT)

    def repair(self) -> str:
        """Returns '' or the message to show. The caller has asked first."""
        client = self.doctor["client"]
        try:
            for path in logindoctor.repair(client, platforms.client_in_use):
                logsink.log(f"Login Doctor repaired {path}", "ok")
        except logindoctor.GameRunning:
            return tr("The game is running. Close it first: it rewrites these "
                      "files when it exits.")
        except OSError as e:
            return tr("Could not repair: {error}", error=e)
        return ""
