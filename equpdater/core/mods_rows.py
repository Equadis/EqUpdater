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
from . import tweaks as _tw, errors as _er, mods as _md, addons as _ad

# names starting with an underscore are not carried by import *
globals().update({k: v for m in (_tw, _er, _md, _ad)
                  for k, v in vars(m).items() if k.startswith("_") and not k.startswith("__")})


class ModsRows:
    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every
    widget and dialog goes through a hook method the host implements."""

    #: What each planner status offers on a mod row: the label, its colour,
    #: whether clicking it does anything, and the tooltip that explains it.
    #: Gold means "this will act"; olive and dim mean "this is information".
    #: A status the user cannot act on must not look like a button -- that is
    #: how a warning gets clicked through.
    _MOD_ACTIONS = {
        Status.UPDATE_AVAILABLE: (N_("update"), "act",
                                  N_("Install the newer version from the "
                                  "configured source.")),
        Status.MISSING_FILES:    (N_("reinstall"), "act",
                                  N_("The installed files are gone. Reinstall "
                                  "to put them back.")),
        Status.LOCAL_NEWER:      (N_("local newer"), "info",
                                  N_("The installed version is newer than the "
                                  "one the configured source offers. "
                                  "EqUpdater will not replace it.")),
        Status.UNMANAGED:        (N_("not managed"), "adopt",
                                  N_("Found on disk, installed by something "
                                  "else. EqUpdater will not touch it until "
                                  "you ask it to.")),
        Status.MODIFIED:         (N_("modified"), "info",
                                  N_("These files differ from the ones "
                                  "EqUpdater installed. Automatic updates "
                                  "are held back.")),
        Status.UNVERIFIABLE:     (N_("can't verify"), "info",
                                  N_("The installed version and the one the "
                                  "source offers cannot be put in order, so "
                                  "nothing is replaced automatically.")),
        Status.VERSION_UNKNOWN:  (N_("version unknown"), "recover",
                                  N_("EqUpdater looks after these files but "
                                  "cannot tell which version they are, so it "
                                  "does not update them automatically.")),
        Status.DIFFERENT_SOURCE: (N_("other source"), "info",
                                  N_("Installed from a different repository "
                                  "than the one now configured.")),
    }


    # The two game-resolution fixes can't coexist: enabling one prompts to
    # replace the other (see _confirm_res_fix_toggle).
    _RES_FIX_CONFLICTS = {"no1600x1200": "VanillaMultiMonitorFix",
                          "VanillaMultiMonitorFix": "no1600x1200"}

    # Per-mod (title, message, confirm) shown when enabling either resolution
    # fix. confirm=True asks Yes/No and only proceeds on Yes; confirm=False is
    # an OK-only acknowledgement that always proceeds.
    _RES_FIX_NOTICE = {
        "no1600x1200": (
            "No1600x1200",
            N_("No1600x1200 mod may cause the game to crash on launch on some "
            "systems.\n\n"
            "VanillaMultiMonitorFix is the recommended resolution fix. Use "
            "No1600x1200 only if VanillaMultiMonitorFix doesn't solve your "
            "problem.\n\n"
            "Enable No1600x1200 anyway?"),
            True),
        "VanillaMultiMonitorFix": (
            "VanillaMultiMonitorFix",
            N_("Enable this mod only if the game doesn't detect your monitor's "
            "native resolution.\n\n"
            "VanillaMultiMonitorFix may interfere with correct resolution "
            "detection. By default, it's recommended to first try launching the "
            "game with DXVK, which may fix the resolution detection issue.\n\n"
            "Enable VanillaMultiMonitorFix only if you're actually experiencing "
            "resolution issues."),
            False),
    }

    #: The Mods tab's sections, in order.
    MOD_SECTIONS = (N_("MANAGED"), N_("INSTALLED LOCALLY"), N_("AVAILABLE"))


    def _toggle_mod(self, mod_id: str, var):
        # Turning on one of the conflicting resolution fixes needs confirmation
        # before it becomes a pending change.
        if var.get() and mod_id in self._RES_FIX_CONFLICTS:
            if not self._confirm_res_fix_toggle(mod_id, var):
                return   # backed out — var + pending already reset
        if var.get():
            twins_on = [m for m in MODS_REGISTRY
                        if m["id"] in mod_twins(mod_id)
                        and self._mod_is_on(m["id"])]
            me = next(m for m in MODS_REGISTRY if m["id"] == mod_id)
            if twins_on:
                if not self._confirm_replace_twins(
                        (me.get("repo_url") or me["name"]).replace("https://", ""),
                        os.path.basename(me["register_dll"]), twins_on):
                    var.set(False)
                    self._mod_pending_state.pop(mod_id, None)
                    self._refresh_apply_btn_visibility()
                    return
                for m in twins_on:
                    self._mod_pending_state.setdefault(
                        m["id"], {})["enabled"] = False
                    twin_var = self._mod_row_vars.get(m["id"], {}).get("enabled")
                    if twin_var is not None:
                        twin_var.set(False)
        self._mod_pending_state.setdefault(mod_id, {})["enabled"] = var.get()
        self._refresh_apply_btn_visibility()

    def _mod_is_on(self, mod_id: str) -> bool:
        """Whether a mod is currently on in the UI: a pending toggle wins,
        otherwise its saved enabled state."""
        pending = self._mod_pending_state.get(mod_id, {})
        if "enabled" in pending:
            return bool(pending["enabled"])
        return bool(load_config().get("mods", {}).get(mod_id, {}).get("enabled"))

    def _confirm_res_fix_toggle(self, mod_id: str, var) -> bool:
        """Confirm enabling no1600x1200 / VanillaMultiMonitorFix. They conflict,
        so first offer to replace the other one, then show that mod's own notice
        (_RES_FIX_NOTICE). Returns True to proceed, or False if the user backed
        out – in which case the checkbox and pending state are reset to 'off'."""
        other = self._RES_FIX_CONFLICTS[mod_id]
        name  = next(m["name"] for m in MODS_REGISTRY if m["id"] == mod_id)
        oname = next(m["name"] for m in MODS_REGISTRY if m["id"] == other)

        def _cancel():
            var.set(False)
            self._mod_pending_state.pop(mod_id, None)
            self._refresh_apply_btn_visibility()

        # 1) Conflict — only when the other fix is currently on.
        replace_other = self._mod_is_on(other)
        if replace_other and not self._ask_yes_no(
                tr("Conflicting mods"),
                tr("{name} and {other} both affect the game's resolution "
                   "detection and can't be used together – only one can be "
                   "active at a time.\n\n"
                   "VanillaMultiMonitorFix is recommended if you experience "
                   "resolution issues in the game.\n\n"
                   "Replace {other} with {name}?", name=name, other=oname)):
            _cancel()
            return False

        # 2) That mod's own notice (crash risk / usage guidance). A confirm
        # notice can still cancel the toggle; an OK-only one just informs.
        title, message, confirm = self._RES_FIX_NOTICE[mod_id]
        message = tr(message)
        if confirm:
            if not self._ask_yes_no(title, message):
                _cancel()
                return False
        else:
            self._show_info(title, message)

        # Confirmed — schedule the other fix for removal and untick its box.
        if replace_other:
            self._mod_pending_state.setdefault(other, {})["enabled"] = False
            other_var = self._mod_row_vars.get(other, {}).get("enabled")
            if other_var is not None:
                other_var.set(False)
        return True

    def _set_ignore(self, mod_id: str, var):
        """Saved at once: with no on/off boxes left, there is no Apply to
        wait for."""
        value = bool(var.get())
        self._cfg = update_config(lambda c: c.setdefault("mods", {}).setdefault(
            mod_id, {}).__setitem__("ignore_updates", value))
        self._mod_pending_state.get(mod_id, {}).pop("ignore_updates", None)
        if not self._mod_pending_state.get(mod_id, True):
            self._mod_pending_state.pop(mod_id, None)
        self._render_mod_rows()
        self._refresh_mods_badge()
        self._refresh_apply_btn_visibility()

    def _found_mods(self) -> list:
        """Mods here that EqUpdater does not manage."""
        client = self._game_path.get().strip()
        if not client:
            return []
        mods_cfg = load_config().get("mods", {})
        return [m for m in MODS_REGISTRY
                if not (mods_cfg.get(m["id"]) or {}).get("released")
                and mod_plan(m, mods_cfg.get(m["id"], {}), None,
                             client).status is Status.UNMANAGED]

    def _found_addons(self) -> list:
        """Addons here that EqUpdater does not manage, that have a source to
        track, and that nobody released from management on purpose."""
        saved = load_config().get("addons", {}) or {}
        return [r for r in self._addons_status["addons"].values()
                if r.get("status") == "unmanaged"
                and (r.get("suggested_git") or r.get("git"))
                and not (saved.get(r["folder"]) or {}).get("released")]

    def _manage_all_found(self):
        """One question for everything OctoLauncher (or anything else) left
        here, instead of one click per row. Consent, not assumption: it says
        what will be managed, and no file changes."""
        mods, addons = self._found_mods(), self._found_addons()
        if not mods and not addons:
            return
        if not self._ask_yes_no(
                tr("Manage what is already installed?"),
                tr("{app} found {mods} mods and {addons} addons it did not "
                   "install, from OctoLauncher or by hand.\n\n"
                   "Managing them means {app} can update and remove them. "
                   "Your current files are not changed now. Anything it "
                   "cannot prove is safe to update stays as it is until you "
                   "choose, and a build newer than the source's is never "
                   "replaced automatically.\n\n"
                   "Manage them all?", app=branding.APP_NAME,
                   mods=len(mods), addons=len(addons))):
            return
        for mod in mods:
            self._record_mod_adoption(mod)
        for rec in addons:
            self._record_addon_adoption(rec)
        self._log_line(f"\n{branding.APP_NAME} now manages {len(mods)} mods and "
                       f"{len(addons)} addons it found here. "
                       f"No files were changed.\n", "acct")
        self._render_mod_rows()
        self._refresh_mods_badge()
        self._refresh_manage_all()
        client = self._game_path.get().strip()

        def work():
            try:
                self._identify_unversioned_mods(client)
                self._identify_adopted_addons(client)
            except Exception as e:
                log(f"Could not identify everything found here: {e}")
            self.after(0, self._after_identify)
        threading.Thread(target=work, daemon=True).start()

    def _confirm_replace_twins(self, source: str, dll: str,
                               twins: list) -> bool:
        """Ask to turn off the other build(s) of ``dll`` that are on."""
        others = ", ".join(sorted(
            (m.get("repo_url") or m["name"]).replace("https://", "")
            for m in twins))
        return self._ask_yes_no(
            tr("Two builds of one mod"),
            tr("{dll} from {source} and {dll} from {other} are two builds of "
               "the same mod. Only one can be loaded.\n\n"
               "Turn off the one from {other} and use {source}?",
               dll=dll, source=source, other=others))

    def _mod_section(self, mod: dict, state: dict) -> str:
        """MANAGED: files here that EqUpdater looks after. INSTALLED
        LOCALLY: files here that it does not. AVAILABLE: not here."""
        here, managed = self._mod_icon_state(mod, state)
        if not here:
            return "AVAILABLE"
        return "MANAGED" if managed else "INSTALLED LOCALLY"

    def _mod_build_list(self, mods_sorted, mods_cfg) -> list:
        """The rows to build, each section's header first: (title, count)
        for a header, the mod for a row. A closed section has no rows."""
        groups = {t: [] for t in self.MOD_SECTIONS}
        for mod in mods_sorted:
            groups[self._mod_section(mod, mods_cfg.get(mod["id"], {}))].append(mod)
        out = []
        opened = getattr(self, "_mod_sections_open", {})
        for title in self.MOD_SECTIONS:
            out.append((title, len(groups[title])))
            if opened.get(title, True):
                out.extend(groups[title])
        return out

    def _mod_install(self, mod_id: str):
        """Install a mod from its row's download icon: the same questions
        the old checkbox asked (a resolution fix that conflicts, two builds of
        one mod), then an Apply for just what they agreed to."""
        if self._btn_mode == "busy":
            return
        var = self._new_bool_var(True)
        self._toggle_mod(mod_id, var)
        if not var.get() or not self._mod_pending_state.get(
                mod_id, {}).get("enabled"):
            return                              # backed out of a question
        self._apply_mods()

    def _mod_icon_state(self, mod: dict, state: dict) -> tuple:
        """What decides a mod row's icons: whether its files are here, and
        whether EqUpdater manages them."""
        client = self._game_path.get().strip()
        return (bool(client) and bool(mod_found_files(mod, client)),
                bool(state.get("managed")))

    def _mod_icons_stale(self, mods_cfg: dict) -> bool:
        """Whether any built row shows icons for a state it is no longer in
        (installed, uninstalled, adopted or released since it was drawn)."""
        built = getattr(self, "_mod_built_sections", {})
        for mod in MODS_REGISTRY:
            state = mods_cfg.get(mod["id"], {})
            refs = self._mod_row_vars.get(mod["id"])
            if refs is not None and refs.get("icons") != self._mod_icon_state(
                    mod, state):
                return True
            # Moved section -- into one that is closed, or out of one.
            if built and built.get(mod["id"]) != self._mod_section(mod, state):
                return True
        return False

    def _mod_uninstall(self, mod_id: str):
        """Uninstall a mod now: its files are deleted and its dlls.txt line
        removed, managed or not, without a release lookup -- the same as
        unticking it and pressing Apply, for this one mod."""
        mod = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
        client = self._game_path.get().strip()
        if mod is None or not client or self._btn_mode == "busy":
            return
        if not self._ask_yes_no(
                tr("Uninstall {name}?", name=mod["name"]),
                tr("{name}'s files are deleted from the game folder and it is "
                   "taken out of dlls.txt.", name=mod["name"])):
            return
        state = load_config().get("mods", {}).get(mod_id, {})
        try:
            if not state.get("installed_files"):
                # Never recorded: the files found here, and only those.
                mod = dict(mod, installed_files=mod_found_files(mod, client))
                update_config(lambda c: c.setdefault("mods", {}).setdefault(
                    mod_id, {}).__setitem__("installed_files",
                                            mod["installed_files"]))
            uninstall_mod(mod, client)
            if mod.get("register_dll"):
                remove_dll(client, mod["register_dll"])
        except OSError as e:
            self._log_line(f"Could not uninstall {mod['name']}: {e}\n", "err")
            return

        def _merge(c):
            mods = c.setdefault("mods", {})
            rec = new_mod_record(managed=False, enabled=False)
            rec["ignore_updates"] = bool((mods.get(mod_id) or {}).get(
                "ignore_updates", False))
            mods[mod_id] = rec
        self._cfg = update_config(_merge)
        self._mod_pending_state.pop(mod_id, None)
        self._log_line(f"\n{mod['name']} uninstalled.\n", "acct")
        self._rebuild_mod_rows()
        self._refresh_mods_badge()
        self._refresh_apply_btn_visibility()
        self._refresh_ready_state()

    def _mod_release(self, mod_id: str):
        """Stop managing a mod. Its files stay exactly where they are and it
        still loads; EqUpdater just stops updating it, and "Manage all"
        leaves it out. The row's "not managed" takes it back."""
        mod = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
        if mod is None:
            return

        def _merge(c):
            rec = c.setdefault("mods", {}).setdefault(mod_id, {})
            rec.update(managed=False, adopted=False, installed_by=None,
                       released=True, error=None)
        self._cfg = update_config(_merge)
        self._mod_pending_state.pop(mod_id, None)
        self._log_line("\n%s is no longer managed by %s; its files are "
                       "untouched.\n" % (mod["name"], branding.APP_NAME), "acct")
        self._rebuild_mod_rows()
        self._refresh_mods_badge()
        self._refresh_apply_btn_visibility()
