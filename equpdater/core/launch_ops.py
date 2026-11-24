"""Logic-only operations lifted from the Tk window (see tools/lift_methods.py).

These methods keep their ``self`` signature: ``self`` is the *host* - the Tk
window today, the Qt services' host object in 3.0 - and must provide the hook
attributes the code uses (``_game_path.get()``, ``_status_var.set()``,
``_set_btn_busy()``, ``after()``, ``_ask_yes_no()`` ...). Nothing here imports
Tk or Qt.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from .. import (branding, gamepaths, gitlocal, i18n, lootgate, mpq, platforms,
                diagnostics)
from ..i18n import N_, tr
from ..config import (APP_DATA_DIR, BACKUP_DIR, CONFIG_FILE, ensure_dir,
                      load_config, new_addon_record, new_mod_record,
                      update_config)
from ..gitcompare import (GIT_HOSTS, Ancestry, ancestry, canonical_repo_url,
                          forge_named, repo_ref, same_repo,
                          short as short_sha, split_repo_url)
from ..hashing import files_hash, folder_hash
from ..planner import CAUSE_LOOKUP, Component, plan, skipped_notably, updatable
from ..states import Action, Plan, Status
from .logsink import log
from .net import ALLOWED_DOWNLOAD_HOSTS, secure_urlopen
from .tweaks import *            # noqa: F401,F403  (constants and functions)
from .errors import *            # noqa: F401,F403
from .mods import *              # noqa: F401,F403
from .addons import *            # noqa: F401,F403
from .mpq_ops import *           # noqa: F401,F403
from .launch_funcs import *      # noqa: F401,F403
from .. import diagnostics
UPDATER_VERSION = branding.APP_VERSION
from . import tweaks as _tw, errors as _er, mods as _md, addons as _ad, mpq_ops as _mq, launch_funcs as _lf

# names starting with an underscore are not carried by import *
globals().update({k: v for m in (_tw, _er, _md, _ad, _mq, _lf)
                  for k, v in vars(m).items() if k.startswith("_") and not k.startswith("__")})


class LaunchOps:
    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every
    widget and dialog goes through a hook method the host implements."""

    def _mods_have_errors(self) -> bool:
        return any(bool(s.get("error"))
                   for s in load_config().get("mods", {}).values())

    def _refresh_ready_state(self):
        """Recompute the footer status/button after an operation finishes.
        PLAY is only offered when the client files are up to date AND no mod
        is in an error state – otherwise the button stays grey and inactive."""
        self._refresh_launch_menu()
        # Never re-enable PLAY while addons are actively installing — this
        # guards against a stray call during the mods→addons setup chain or a
        # post-install verify flipping the button back on mid-download.
        if self._addons_installing:
            self._set_btn_busy(tr("Installing…"))
            self._status_var.set(tr("Downloading addons…"))
            return
        if not self._client_ready:
            self._status_var.set(tr("Update available!"))
            self._set_btn_update()
            return
        if self._mods_have_errors():
            self._set_btn_busy(tr("PLAY"))
            self._status_error(tr("Mod errors – check the MODS tab"),
                               lambda: self._show_page("MODS"))
        else:
            self._set_btn_play()
            self._status_var.set(tr("Everything up to date!"))

    def _launch_target(self, client_dir: str) -> str | None:
        """The file PLAY starts in this folder, looked up on disk each time:
        a game-folder change or a mod install needs nothing else to notice."""
        return platforms.resolve_launch_target(
            platforms.launch_options(client_dir),
            load_config().get("launch_default"))

    def _btn_click(self):
        self._close_launch_menu()
        if self._btn_mode == "play":
            self._launch_game()
        elif self._btn_mode == "update":
            self._start_update()

    def _diag_store(self):
        store = getattr(self, "_diag_store_obj", None)
        if store is None:
            store = self._diag_store_obj = diagnostics.Store(
                os.path.join(APP_DATA_DIR, "diagnostics"))
        return store

    def _diag_probe(self):
        if not hasattr(self, "_diag_probe_obj"):
            self._diag_probe_obj = diagnostics.default_probe()
        return self._diag_probe_obj

    def _diag_begin(self, client_dir: str, diagnostic: bool):
        """The record of one PLAY, on disk before anything else happens. If
        it cannot be made the game still starts: the view reports the error."""
        try:
            attempt = diagnostics.Attempt(
                self._diag_store(), client_dir, UPDATER_VERSION,
                sys.platform, diagnostic)
            if client_dir and os.path.isdir(client_dir):
                attempt.data["client_build"] = get_client_version(client_dir) or None
            diagnostics.begin_baseline(attempt, self._diag_probe())
            return attempt
        except Exception as e:
            self._log_line(f"Game Diagnostics could not record this launch: {e}\n",
                           "err")
            return diagnostics.NullAttempt()

    def _diag_watch(self, attempt, proc, exe_lbl: str, output):
        """Watch this attempt on its own thread. It outlives nothing it
        needs: what it sees is on disk, for whichever EqUpdater looks next."""
        if isinstance(attempt, diagnostics.NullAttempt):
            return
        try:
            direct = exe_lbl.lower() == platforms.GAME_EXE.lower() \
                and attempt.data["route"].get("runner") == "windows"
            role = "game" if direct else "launcher"
            attempt.process(role, proc.pid, exe_lbl)
            attempt.stage("process_created")
            attempt.event("spawned", role=role, pid=proc.pid, name=exe_lbl)
            observer = diagnostics.Observer(
                attempt, self._diag_probe(), proc, role, exe_lbl, output,
                on_change=self._diag_changed,
                output_copy=LAUNCH_OUTPUT if output else None)
            self._diag_observers = [o for o in getattr(self, "_diag_observers", [])
                                    if not o.done] + [observer]
            observer.start()
        except Exception as e:
            self._log_line(f"Game Diagnostics could not watch this launch: {e}\n",
                           "err")

    def _diag_recover(self):
        """Attempts the last EqUpdater left open: settle them with whatever
        evidence is there now, and carry on watching a game still running."""
        try:
            probe = self._diag_probe()
            store = self._diag_store()
            for data in diagnostics.recover(store, probe):
                diagnostics.watch_again(store, data, probe,
                                        on_change=self._diag_changed).start()
        except Exception as e:
            log(f"Game Diagnostics could not read its earlier attempts: {e}")

    def _launch_game(self, diagnostic: bool = False):
        """Launch the game detached.
        If VanillaFixes is installed use VanillaFixes.exe (it injects dlls then
        starts WoW.exe itself). Otherwise fall back to WoW.exe directly. On
        Linux either one is started through the game runner (see
        platforms.launch_plan)."""
        client_dir = self._game_path.get().strip()
        cfg        = load_config()
        attempt    = self._diag_begin(client_dir, diagnostic)

        # The loader reads dlls.txt in a moment. Anything that edited that file
        # since the last Apply - the official launcher, a restore, a hand edit
        # - can have dropped an enabled mod, and a mod dropped here is a mod
        # that silently does not exist in the game. Last chance to put it back.
        try:
            for name in reconcile_dlls_txt(client_dir, cfg.get("mods", {})):
                self._log_line(f"{name} was missing from dlls.txt - "
                               f"re-registered before launch.\n", "acct")
        except Exception as e:
            self._log_line(f"Could not check dlls.txt before launch: {e}\n", "err")
        try:
            for note in enforce_autoloot(client_dir):
                self._log_line(note + "\n", "acct")
        except Exception as e:
            self._log_line(f"Could not check Auto Loot before launch: {e}\n", "err")

        # Trust VanillaFixes.exe on disk, not the config record. That record
        # is absent after a game-folder switch, after a config reset, and on
        # any install this updater did not perform itself - and falling back
        # to WoW.exe in those cases launches the game with *none* of the
        # dlls.txt mods loaded, silently. Whether the loader is actually there
        # is the thing that matters. Unless the player chose otherwise, it is
        # what starts.
        exe_lbl = self._launch_target(client_dir) or platforms.GAME_EXE
        exe     = os.path.join(client_dir, exe_lbl)
        vf_installed = os.path.exists(os.path.join(client_dir,
                                                   platforms.LOADER_EXE))

        if os.path.exists(_dlls_txt_path(client_dir)):
            if not vf_installed:
                self._log_line(
                    "VanillaFixes.exe is missing, so the mods listed in "
                    "dlls.txt will NOT load. Install VanillaFixes from the "
                    "Mods tab.\n", "err")
            elif exe_lbl.lower() == platforms.GAME_EXE.lower():
                self._log_line(
                    "Starting WoW.exe directly, as chosen: the mods listed "
                    "in dlls.txt will not load.\n", "acct")

        if not os.path.exists(exe):
            self._log_line(f"{exe_lbl} not found at: {exe}\n", "err")
            attempt.block("executable", f"The launch target {exe_lbl} was not "
                          f"found in the game folder.")
            return
        if exe_lbl.lower().endswith(".exe") and self._launch_target_damaged(
                exe, exe_lbl, attempt):
            return

        # One-time DXVK first-launch notice (armed when DXVK was installed).
        if cfg.get("dxvk_notice_pending"):
            self._cfg = update_config(
                lambda c: c.pop("dxvk_notice_pending", None))
            self._show_info(
                tr("DXVK mod first launch"),
                tr("Initial shader compilation may cause temporary in-game stuttering during the first launch. This is a normal process while the game builds its shader cache.\n\nUsers with AMD GPUs experiencing stability issues can switch to DXVK 2.5.3."))

        # How to start it: the executable itself on Windows; on Linux the
        # player's launch command, else Wine, else UMU (platforms.launch_plan).
        plan = platforms.launch_plan(client_dir, exe,
                                     load_config().get("game_launcher"))
        if plan.env_problems:
            attempt.block("launch_settings",
                          "The launcher's environment variables have lines "
                          "that cannot be used: " + env_problem_text(plan.env_problems))
            self._log_line("Not launched: the game launcher's environment "
                           "variables have lines that cannot be used.\n",
                           "err")
            self._show_error(
                tr("Cannot start the game"),
                tr("Fix the environment variables in Settings → Game "
                   "launcher:\n\n{problems}",
                   problems=env_problem_text(plan.env_problems)))
            return
        if not plan.ok:
            attempt.block("launch_settings",
                          "No way to run the game was found: no usable launch "
                          "command, and no wine or umu-run on PATH.")
            self._log_line("No way to run the Windows game client was found "
                           "(no launch command set, no wine or umu-run on "
                           "PATH).\n", "err")
            self._show_error(
                tr("Cannot start the game"),
                tr("{app} found no way to run the Windows game client. "
                   "Install Wine, or set a launch command in Settings \u2192 "
                   "Game launcher.", app=branding.APP_NAME))
            return

        attempt.route(plan, exe, exe_lbl)
        diagnostics.preflight(attempt, client_dir, exe, plan)
        problems = platforms.launch_problems(plan)
        if problems:
            attempt.block("launch_settings", "; ".join(
                f"{reason}: {value}" for reason, value in problems))
            self._launch_failed(
                launch_problem_text(problems),
                "Not launched: " + "; ".join(f"{r}: {v}" for r, v in problems)
                + f"\n  command: {plan.argv}\n")
            return

        if self._cfg.get("clear_wdb_on_launch", False):
            remove_wdb(client_dir)

        output = None
        if plan.runner != "windows":
            try:
                ensure_dir(os.path.dirname(LAUNCH_OUTPUT))
                output = attempt.output_path() or LAUNCH_OUTPUT
            except OSError:
                pass
        if diagnostic:
            # This launch only: in its own environment, never saved anywhere.
            extra = diagnostics.diagnostic_env(plan.runner,
                                               attempt.folder() or APP_DATA_DIR)
            plan.env = {**(plan.env or platforms.child_env()), **extra}
            attempt.route(plan, exe, exe_lbl)
        try:
            proc = platforms.spawn_detached(plan.argv, cwd=plan.cwd,
                                            env=plan.env, output=output,
                                            console=plan.console)
        except OSError as e:
            attempt.block("process_creation",
                          f"{exe_lbl} could not be started: {e.strerror or e}", e)
            self._launch_failed(
                tr("{program} could not be started: {error}",
                   program=plan.argv[0], error=e.strerror or e),
                f"Failed to launch {exe_lbl}: {e}\n  command: {plan.argv}\n")
            return
        try:
            how = "" if plan.runner == "windows" else f" with {plan.runner}"
            self._log_line(f"Launched {exe_lbl}{how}!\n", "ok")
            self._diag_watch(attempt, proc, exe_lbl, output)
            if output:
                self._watch_launch(proc, plan.argv, output, 0)
            # Briefly disable PLAY so a double-click can't spawn two clients.
            self._set_btn_busy(tr("PLAY"))
            self._status_var.set(tr("Launching…"))
            # Optionally minimize (close) the updater to the taskbar shortly after launch
            if self._cfg.get("close_on_launch", False):
                self.after(1000, self._minimize_window)
            self.after(5000, self._refresh_ready_state)
        except Exception as e:
            self._log_line(f"Failed to launch {exe_lbl}: {e}\n", "err")

    def _launch_target_damaged(self, exe: str, exe_lbl: str, attempt) -> bool:
        """Stop before starting a file that is not a whole Windows program,
        and say what to do. Started anyway, Windows answers only "WinError
        193: %1 is not a valid Win32 application" -- which is what players saw
        when VanillaFixes.exe was empty or cut short. True when stopped."""
        try:
            problem = platforms.program_problem(exe)
        except OSError as e:
            problem, size = None, None
            self._log_line(f"Could not read {exe_lbl} before launch: {e}\n", "err")
        if problem is None:
            return False
        size = os.path.getsize(exe)
        attempt.block("executable", f"{exe_lbl} is {problem} ({size} bytes): "
                      f"not a complete Windows program.")
        self._log_line(f"Not launched: {exe_lbl} is {problem} ({size} bytes), "
                       f"not a complete Windows program.\n", "err")
        what = (tr("{program} is empty.", program=exe_lbl) if problem == "empty"
                else tr("{program} is damaged: it is not a complete Windows "
                        "program.", program=exe_lbl))
        loader = exe_lbl.lower() == platforms.LOADER_EXE.lower()
        if loader:
            if self._ask_yes_no(
                    tr("Cannot start the game"),
                    what + " " + tr("An interrupted download or an antivirus "
                                    "can leave it like this.") + "\n\n"
                    + tr("Reinstall VanillaFixes now?")):
                self._show_page("MODS")
                self._mod_force_replace("VanillaFixes")
        else:
            self._show_error(
                tr("Cannot start the game"),
                what + " " + tr("Run Settings → Verify game files to repair "
                                "it."))
        self._refresh_ready_state()
        return True

    def _launch_failed(self, message: str, details: str):
        """PLAY did not start the game: the details to the log, a short
        reason to the player, PLAY ready again."""
        self._log_line(details, "err")
        self._show_error(tr("Cannot start the game"),
                             tr("Could not launch the game.") + "\n\n" + message)
        self._refresh_ready_state()

    def _watch_launch(self, proc, argv, output, waited: int):
        """Linux: a runner that quits with an error within LAUNCH_WATCH_MS
        of PLAY did not start the game -- a bad prefix, a Proton that is not
        there. Say so, with the end of what it printed, rather than let
        PLAY come back as if nothing happened. Quitting cleanly is fine:
        VanillaFixes.exe exits once it has started WoW.exe."""
        code = proc.poll()
        if code is None:
            if waited < LAUNCH_WATCH_MS:
                self.after(250, lambda: self._watch_launch(
                    proc, argv, output, waited + 250))
            return
        if code == 0:
            return
        try:
            with open(output, encoding="utf-8", errors="replace") as f:
                lines = [l.rstrip() for l in f.read().splitlines() if l.strip()]
        except OSError:
            lines = []
        tail = "\n".join(lines[-40:])
        self._launch_failed(
            tr("{program} stopped at once (exit code {code}).",
               program=os.path.basename(argv[0]), code=code)
            + ("\n\n" + "\n".join(lines[-6:]) if lines else ""),
            f"The game runner exited with code {code} right after launch.\n"
            f"  command: {argv}\n  output ({output}):\n{tail}\n")
