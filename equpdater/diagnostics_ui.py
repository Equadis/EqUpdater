"""Settings -> Game Diagnostics: the view over equpdater/diagnostics.py and
clientscan.py. Scans and exports run on threads; Tk is only touched here, on
the window's thread. Opening this never starts the game."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from datetime import datetime

from . import app as appmod
from . import clientscan, diagnostics, logindoctor, platforms
from .app import SlimScrollbar
from .i18n import tr

_OUTCOME_COLOURS = {
    "blocked": "err", "launcher_error": "err", "launcher_says": "err",
    "crash": "err", "exit_error": "err", "early_exit": "warn", "no_game": "warn",
    "launcher_stuck": "warn", "handed_off": "warn", "exit_unknown": "warn",
    "unavailable": "warn", "running": "ok", "exit_normal": "ok",
    "in_progress": "dim",
}


def open_view(app):
    if app._settings_overlay is None:
        return
    old = getattr(app, "_diag_view", None)
    if old is not None and old.shade.winfo_exists():
        return
    app._diag_view = View(app)


def _when(epoch) -> str:
    if not epoch:
        return tr("unknown")
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


class View:
    def __init__(self, app):
        self.app = app
        app._diag_view = self
        self.store = app._diag_store()
        self.attempts = []
        self.selected = None
        self.scan_cancel = None
        self.scan_thread = None
        self.export_thread = None
        self.showing_scan = False
        self.build()
        self.refresh_from_disk()
        app.bind("<Escape>", lambda e: self.close())

    # ── building ──────────────────────────────────────────────────────────────

    def button(self, parent, text, command, side="left"):
        btn = tk.Label(parent, text=text, font=self.app._font(10), fg=appmod.C_TEXT,
                       bg=appmod.C_PANEL_BDR, cursor="hand2",
                       padx=self.app._px(10), pady=self.app._px(5))
        btn.pack(side=side, padx=(0, self.app._px(6)))
        btn._command = command
        btn._enabled = True
        btn.bind("<Button-1>", lambda e, b=btn: b._enabled and b._command())
        return btn

    def enable(self, btn, on: bool):
        btn._enabled = bool(on)
        btn.configure(fg=appmod.C_TEXT if on else appmod.C_TEXT_DIM,
                      cursor="hand2" if on else "arrow")

    def build(self):
        app = self.app
        px = app._px
        self.shade = tk.Frame(app._settings_overlay, bg="#0a0a0e")
        self.shade.place(x=0, y=0, relwidth=1, relheight=1)
        panel = tk.Frame(self.shade, bg=appmod.C_PANEL, highlightthickness=1,
                         highlightbackground=appmod.C_PANEL_BDR,
                         highlightcolor=appmod.C_PANEL_BDR)
        panel.place(relx=0.5, rely=0.5, anchor="center",
                    width=min(px(900), appmod.WIN_W - px(40)),
                    height=min(px(620), appmod.WIN_H - px(40)))
        hdr = tk.Frame(panel, bg=appmod.C_HDR, height=px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("GAME DIAGNOSTICS"), font=app._font(13, bold=True),
                 fg=appmod.C_PURPLE, bg=appmod.C_HDR).pack(side="left", padx=px(18))
        close = tk.Label(hdr, text="✕", font=app._font(12), fg=appmod.C_TEXT_DIM,
                         bg=appmod.C_HDR, cursor="hand2")
        close.pack(side="right", padx=px(16))
        close.bind("<Button-1>", lambda e: self.close())
        tk.Frame(panel, bg=appmod.C_PANEL_BDR, height=px(1)).pack(fill="x")

        body = tk.Frame(panel, bg=appmod.C_PANEL)
        body.pack(fill="both", expand=True, padx=px(16), pady=(px(10), px(12)))

        # The bottom rows go in first: packing from the bottom keeps them on
        # screen however small the window is, and the lists take what is left.
        self.status = tk.Label(body, text="", font=app._font(9), fg=appmod.C_TEXT_DIM,
                               bg=appmod.C_PANEL, anchor="w", justify="left",
                               wraplength=min(px(860), appmod.WIN_W - px(80)))
        self.status.pack(side="bottom", fill="x", pady=(px(6), 0))

        row2 = tk.Frame(body, bg=appmod.C_PANEL)
        row2.pack(side="bottom", fill="x", pady=(px(6), 0))
        self.scan_btn = self.button(row2, tr("Scan OctoWoW folder"), self.start_scan)
        self.deep = tk.BooleanVar(value=False)
        self.include_mpq = tk.BooleanVar(value=False)
        for text, var in ((tr("Deep check"), self.deep),
                          (tr("Include MPQ archives"), self.include_mpq)):
            tk.Checkbutton(row2, text=text, variable=var, font=app._font(9),
                           fg=appmod.C_TEXT, bg=appmod.C_PANEL,
                           selectcolor=appmod.C_LOG_BG,
                           activebackground=appmod.C_PANEL,
                           activeforeground=appmod.C_TEXT,
                           highlightthickness=0).pack(side="left")
        self.cancel_btn = self.button(row2, tr("Cancel"), self.cancel_scan)
        self.enable(self.cancel_btn, False)
        self.show_scan_btn = self.button(row2, tr("Show last scan"), self.show_scan,
                                         side="right")

        row1 = tk.Frame(body, bg=appmod.C_PANEL)
        row1.pack(side="bottom", fill="x", pady=(px(8), 0))
        self.launch_btn = self.button(row1, tr("Launch with diagnostics"),
                                      self.launch_diagnostic)
        self.recheck_btn = self.button(row1, tr("Check for new evidence"), self.recheck)
        self.export_btn = self.button(row1, tr("Export support ZIP…"), self.export,
                                      side="right")

        lists = tk.Frame(body, bg=appmod.C_PANEL)
        lists.pack(side="top", fill="both", expand=True)

        left = tk.Frame(lists, bg=appmod.C_PANEL, width=px(290))
        left.pack(side="left", fill="y", padx=(0, px(10)))
        left.pack_propagate(False)
        tk.Label(left, text=tr("Launch history"), font=app._font(10, bold=True),
                 fg=appmod.C_GOLD, bg=appmod.C_PANEL, anchor="w").pack(fill="x")
        holder = tk.Frame(left, bg=appmod.C_LOG_BG)
        holder.pack(fill="both", expand=True, pady=(px(4), 0))
        self.listbox = tk.Listbox(holder, bg=appmod.C_LOG_BG, fg=appmod.C_TEXT,
                                  font=app._font(9), relief="flat", bd=0,
                                  highlightthickness=0, activestyle="none",
                                  selectbackground=appmod.C_PANEL_BDR,
                                  selectforeground=appmod.C_GOLD,
                                  exportselection=False)
        bar = SlimScrollbar(holder, command=self.listbox.yview, bg=appmod.C_LOG_BG,
                            width=px(8))
        self.listbox.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.on_select())

        right = tk.Frame(lists, bg=appmod.C_LOG_BG)
        right.pack(side="left", fill="both", expand=True)
        self.text = tk.Text(right, wrap="word", bg=appmod.C_LOG_BG, fg=appmod.C_TEXT,
                            font=app._font(10), relief="flat", bd=0, padx=px(10),
                            pady=px(8), highlightthickness=0, cursor="arrow",
                            state="disabled")
        bar = SlimScrollbar(right, command=self.text.yview, bg=appmod.C_LOG_BG,
                            width=px(8))
        self.text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        t = self.text
        t.tag_configure("head", foreground=appmod.C_GOLD, font=app._font(10, bold=True))
        t.tag_configure("big", font=app._font(11, bold=True))
        t.tag_configure("ok", foreground=appmod.C_OK)
        t.tag_configure("warn", foreground="#d4b43c")
        t.tag_configure("err", foreground=appmod.C_ERR)
        t.tag_configure("dim", foreground=appmod.C_TEXT_DIM)

    def close(self):
        if self.scan_cancel is not None:
            self.scan_cancel.set()
        self.app._diag_view = None
        try:
            self.shade.destroy()
        except tk.TclError:
            pass
        if self.app._settings_overlay is not None:
            self.app.bind("<Escape>", lambda e: self.app._close_settings())

    def alive(self) -> bool:
        return self.app._diag_view is self

    # ── the list ──────────────────────────────────────────────────────────────

    def refresh_from_disk(self):
        if not self.alive():
            return
        self.attempts = self.store.recent()
        lb = self.listbox
        try:
            lb.delete(0, "end")
        except tk.TclError:
            return
        for data in self.attempts:
            outcome = data.get("outcome") or "unavailable"
            mark = {"ok": "✓", "err": "✕", "warn": "!", "dim": "…"}.get(
                _OUTCOME_COLOURS.get(outcome, "dim"), "")
            when = _when(data.get("started_at"))[5:]
            lb.insert("end", f" {mark}  {when}  "
                      + tr(diagnostics.OUTCOMES.get(outcome, outcome)))
        if self.attempts:
            index = 0
            if self.selected:
                for i, data in enumerate(self.attempts):
                    if data.get("id") == self.selected:
                        index = i
            lb.selection_clear(0, "end")
            lb.selection_set(index)
            lb.see(index)
            if not self.showing_scan:
                self.show_attempt(self.attempts[index])
        elif not self.showing_scan:
            self.write([(tr("No launch has been recorded yet."), "dim")])
        self.update_buttons()
        if self.store.last_error:
            self.say(self.store.last_error)

    def on_select(self):
        chosen = self.listbox.curselection()
        if not chosen or chosen[0] >= len(self.attempts):
            return
        data = self.attempts[chosen[0]]
        self.selected = data.get("id")
        self.showing_scan = False
        self.show_attempt(data)

    def current(self):
        chosen = self.listbox.curselection()
        if chosen and chosen[0] < len(self.attempts):
            return self.attempts[chosen[0]]
        return self.attempts[0] if self.attempts else None

    def update_buttons(self):
        scanning = self.scan_thread is not None and self.scan_thread.is_alive()
        exporting = self.export_thread is not None and self.export_thread.is_alive()
        self.enable(self.scan_btn, not scanning)
        self.enable(self.cancel_btn, scanning)
        self.enable(self.export_btn, not exporting and bool(self.attempts))
        self.enable(self.recheck_btn, bool(self.attempts))
        watching = any(not o.done for o in getattr(self.app, "_diag_observers", []))
        self.enable(self.launch_btn, self.app._btn_mode == "play" and not watching)
        self.enable(self.show_scan_btn, self.store.latest_scan() is not None)

    def say(self, text: str):
        try:
            self.status.configure(text=text)
        except tk.TclError:
            pass

    def write(self, parts):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, *tag in parts:
            t.insert("end", text + "\n", tuple(tag))
        t.configure(state="disabled")
        t.yview_moveto(0)

    # ── an attempt ────────────────────────────────────────────────────────────

    def show_attempt(self, data: dict):
        outcome, explanation = diagnostics.classify(data)
        route = data.get("route") or {}
        parts = [(tr("Last launch") if self.attempts and data is self.attempts[0]
                  else tr("Launch"), "head"),
                 (_when(data.get("started_at")), "dim"),
                 (tr(diagnostics.OUTCOMES.get(outcome, outcome)), "big",
                  _OUTCOME_COLOURS.get(outcome, "dim")),
                 (explanation,), ("",)]
        ended = [e.get("t") for e in data.get("events", []) if e.get("t") is not None]
        if ended:
            parts.append((f"{tr('Duration')}: {ended[-1]:.1f} s", "dim"))
        if route:
            parts.append((f"{tr('Route')}: {route.get('runner')}  "
                          f"{route.get('target') or ''}", "dim"))
            parts.append((" ".join(route.get("argv") or []), "dim"))
        failed = [p for p in data.get("preflight", []) if not p.get("ok")]
        for p in failed:
            parts.append((f"✕ {p['check']}: {p['detail']}", "err"))
        for line in diagnostics.component_findings(data.get("components") or [],
                                                   route.get("target") or ""):
            parts.append((f"! {line}", "warn"))
        crash = data.get("crash") or {}
        for r in crash.get("reports") or []:
            parts.append((f"{tr('Crash report')}: {r.get('file')}  "
                          f"{r.get('exception_code') or ''} {r.get('module') or ''}", "err"))
        for ev in crash.get("events") or []:
            parts.append((f"{ev.get('provider')}: {ev.get('application')} "
                          f"{ev.get('exception_code')} {ev.get('module')} "
                          f"{ev.get('offset')}", "err"))
        for r in crash.get("older_reports") or []:
            parts.append((f"{r.get('file')}: {r.get('why_excluded') or ''}", "dim"))
        if data.get("loaded_modules"):
            parts.append(("", ))
            parts.append((tr("Observed loaded"), "head"))
            parts.extend((m, "dim") for m in data["loaded_modules"][:30])
        if route.get("handoff"):
            parts += [("",), (diagnostics.STEAM_LOG_HELP, "warn")]
        parts += [("",), (tr("Timeline"), "head")]
        for e in data.get("events", []):
            at = "    ?" if e.get("t") is None else f"{e['t']:6.1f}s"
            parts.append((f"{at}  {e.get('kind')}  {diagnostics.event_details(e)}",
                          "dim"))
            if e.get("output_tail"):
                parts.append((e["output_tail"], "dim"))
        for line in data.get("limits") or []:
            parts.append((f"· {line}", "warn"))
        self.write(parts)

    def recheck(self):
        """Look again for what an attempt left behind: a report written after
        EqUpdater last looked, say. Read-only, and off this thread."""
        data = self.current()
        if not data or data.get("damaged"):
            return
        self.say(tr("Checking for new evidence…"))
        store = self.store

        def work():
            fresh = store.load(data["id"])
            later = [store.load(i).get("started_at") for i in store.ids() if i > data["id"]]
            later = [t for t in later if t]
            try:
                diagnostics.collect_evidence(fresh, until=min(later) if later else None)
                fresh["outcome"], fresh["explanation"] = diagnostics.classify(fresh)
                store.write_json(os.path.join(store.attempt_dir(data["id"]),
                                              "attempt.json"), fresh)
            except Exception as e:
                self.app.after(0, lambda m=str(e): self.say(m))
                return

            def done():
                if self.alive():
                    self.say("")
                    self.refresh_from_disk()
            self.app.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def launch_diagnostic(self):
        from tkinter import messagebox
        if self.app._btn_mode != "play":
            return
        settings = appmod.load_config().get("game_launcher") or {}
        command = settings.get("launch_command") or ""
        note = tr("This starts the game once with extra logging, for this launch "
                  "only. Your settings are not changed.")
        if diagnostics.handoff_of(command.split()):
            note += "\n\n" + diagnostics.STEAM_LOG_HELP
        if not messagebox.askokcancel(tr("Launch with diagnostics"), note,
                                      parent=self.app):
            return
        self.showing_scan = False
        self.selected = None
        self.app._launch_game(diagnostic=True)
        self.refresh_from_disk()

    # ── the scan ──────────────────────────────────────────────────────────────

    def start_scan(self):
        client = self.app._game_path.get().strip()
        if not client:
            self.say(tr("No game folder is set."))
            return
        self.scan_cancel = threading.Event()
        cancel = self.scan_cancel
        mode = clientscan.DEEP if self.deep.get() else clientscan.QUICK
        include_mpq = bool(self.include_mpq.get())
        store = self.store
        app = self.app
        last = {"t": 0.0}

        def progress(text, fraction=None):
            now = time.monotonic()
            if now - last["t"] < 0.2:
                return
            last["t"] = now
            shown = text + (f"  {fraction:.0%}" if fraction is not None else "")
            app.after(0, lambda t=shown: self.alive() and self.say(t))

        def work():
            try:
                scan = run_scan(app, store, client, mode, include_mpq, cancel, progress)
                store.save_scan(scan)
            except Exception as e:
                app.after(0, lambda m=str(e): self.scan_failed(m))
                return
            app.after(0, lambda: self.scan_done(scan))
        self.scan_thread = threading.Thread(target=work, daemon=True)
        self.scan_thread.start()
        self.update_buttons()
        self.say(tr("Scanning…"))

    def cancel_scan(self):
        if self.scan_cancel is not None:
            self.scan_cancel.set()
            self.say(tr("Stopping…"))

    def scan_failed(self, message: str):
        if self.alive():
            self.say(message)
            self.update_buttons()

    def scan_done(self, scan: dict):
        if not self.alive():
            return
        self.scan_thread = None
        self.update_buttons()
        self.say(tr("The scan stopped early; the results cover what it reached.")
                 if scan["coverage"].get("cancelled") else "")
        self.render_scan(scan)

    def show_scan(self):
        scan = self.store.latest_scan(self.app._game_path.get().strip())
        if scan:
            self.render_scan(scan)

    def render_scan(self, scan: dict):
        self.showing_scan = True
        cov = scan.get("coverage") or {}
        n = clientscan.counts(scan)
        parts = [(tr("Folder scan"), "head"),
                 (_when(scan.get("finished_at")) + f"  ({scan.get('mode')})", "dim"),
                 (f"{tr('Confirmed problems')}: {n['confirmed']}   "
                  f"{tr('Possible conflicts')}: {n['possible']}   "
                  f"{tr('Information')}: {n['info']}", "big"), ("",),
                 (tr("Coverage"), "head"),
                 (f"{cov.get('files', 0)} files, {cov.get('bytes', 0) // (1024 * 1024)} MB "
                  f"listed; {cov.get('skipped', 0)} skipped; "
                  f"{cov.get('unreadable', 0)} unreadable; "
                  f"{cov.get('private_files', 0)} in private folders listed by count only.",
                  "dim"),
                 (f"Manifest: {cov.get('manifest') or 'none'}, "
                  f"{cov.get('manifest_files', 0)} files. Hash-checked "
                  f"{cov.get('hashed', 0)}, not hash-checked {cov.get('hash_skipped', 0)}. "
                  f"MPQ archives verified: {'yes' if cov.get('mpq_verified') else 'no'}."
                  + (" Stopped early." if cov.get("cancelled") else ""), "dim"),
                 ("",)]
        for level, title, tag in (("confirmed", tr("Confirmed problems"), "err"),
                                  ("possible", tr("Possible conflicts"), "warn"),
                                  ("info", tr("Information"), "dim")):
            items = [f for f in scan.get("findings", []) if f.get("level") == level]
            if not items:
                continue
            parts.append((title, "head"))
            for f in items[:200]:
                parts.append((f.get("path") or f.get("code"), tag))
                parts.append(("    " + (f.get("why") or ""),))
                if f.get("evidence"):
                    parts.append(("    " + f["evidence"], "dim"))
            parts.append(("",))
        if scan.get("recent_changes"):
            parts.append((tr("Recently changed launch files"), "head"))
            parts.extend((f"{_when(c.get('modified'))}  {c.get('file')}", "dim")
                         for c in scan["recent_changes"][:20])
        self.write(parts)

    # ── the bundle ────────────────────────────────────────────────────────────

    def export(self):
        from tkinter import filedialog, messagebox
        data = self.current()
        if not data:
            return
        scan = self.store.latest_scan(data.get("client_root"))
        lines = [text for text, _tag in getattr(self.app, "_log_buffer", [])][-500:]
        include, omitted = diagnostics.plan_bundle(data, scan, self.store, lines)
        preview = (tr("This report will contain:") + "\n"
                   + "\n".join(f"  • {name}" for name, _why in include) + "\n\n"
                   + tr("Not included:") + "\n"
                   + "\n".join(f"  • {name}: {why}" for name, why in omitted) + "\n\n"
                   + tr("Your user name, folder paths and passwords are removed. "
                        "Nothing is sent anywhere: you choose who gets the file."))
        if not messagebox.askokcancel(tr("Export support ZIP…"), preview,
                                      parent=self.app):
            return
        dest = filedialog.asksaveasfilename(
            parent=self.app, title=tr("Export support ZIP…"), defaultextension=".zip",
            initialfile=f"EqUpdater-support-{data.get('id')}.zip",
            filetypes=[("ZIP", "*.zip")])
        if not dest:
            return
        self.say(tr("Writing the report…"))
        store = self.store

        def work():
            try:
                members, skipped = diagnostics.export_bundle(dest, data, scan, store, lines)
                text = tr("Saved to {path}", path=dest)
            except Exception as e:
                text = tr("The report could not be written: {error}", error=e)
            self.app.after(0, lambda t=text: self.export_done(t))
        self.export_thread = threading.Thread(target=work, daemon=True)
        self.export_thread.start()
        self.update_buttons()

    def export_done(self, text: str):
        if self.alive():
            self.say(text)
            self.export_thread = None
            self.update_buttons()


def client_manifest() -> dict | None:
    """The client's manifest: the torrent the client is synced from, read from
    the server (only read: nothing is fetched into the game folder)."""
    raw, files = appmod.fetch_torrent()
    decoded, _ = appmod._bdecode(raw)
    info = decoded.get(b"info", {})
    return {"name": f"client torrent {appmod.torrent_version(raw)[:12]}",
            "files": files, "piece_length": int(info.get(b"piece length", 0)),
            "pieces": info.get(b"pieces") or b""}


def run_scan(app, store, client, mode, include_mpq, cancel, progress) -> dict:
    """One scan, gathering what it reuses from elsewhere. Off the window's
    thread. A manifest that cannot be fetched narrows coverage and says so."""
    progress(tr("Reading the client manifest…"))
    try:
        manifest = client_manifest()
    except Exception as e:
        appmod.log(f"Game Diagnostics: no client manifest: {e}")
        manifest = None
    cfg = appmod.load_config()
    protected = set(appmod._torrent_excluded_files()) | {"wow.exe"}
    if cfg.get("ignore_speech"):
        protected.add("speech.mpq")
    checks = []
    try:
        running = platforms.client_in_use(client)
        for c in logindoctor.local_checks(client, appmod.get_client_version(client),
                                          running):
            if c.state == logindoctor.PASS or getattr(c, "informational", False):
                continue
            level = "confirmed" if c.state == logindoctor.FAIL else "possible"
            checks.append((level, c.message.format(**c.values), c.group))
    except Exception as e:
        appmod.log(f"Game Diagnostics: Login Doctor's checks failed: {e}")
    target = app._launch_target(client) or platforms.GAME_EXE
    components = diagnostics.launch_components(client, target)
    os.makedirs(store.scans_dir(), exist_ok=True)
    inventory = os.path.join(store.scans_dir(),
                             time.strftime("%Y%m%d-%H%M%S") + ".inventory.txt")
    scan = clientscan.scan(
        client, mode, include_mpq, manifest, cfg.get("mods"),
        {m["id"]: m["name"] for m in appmod.MODS_REGISTRY}, appmod.files_hash,
        protected, checks, components, cancel, progress, inventory)
    for line in diagnostics.component_findings(components, target):
        scan["findings"].append(clientscan.finding(
            "confirmed", "launch_component", line, "", "launch components"))
    return scan
