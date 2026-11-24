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
from . import tweaks as _tw, errors as _er, mods as _md, addons as _ad, mpq_ops as _mq

# names starting with an underscore are not carried by import *
globals().update({k: v for m in (_tw, _er, _md, _ad, _mq)
                  for k, v in vars(m).items() if k.startswith("_") and not k.startswith("__")})


class SettingsOps:
    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every
    widget and dialog goes through a hook method the host implements."""

    def _offer_language_restart(self):
        """EqUpdater's text is drawn once, so a new Language shows in
        EqUpdater after a restart. Offered, never forced, and asked in the
        language just chosen -- the one the player can read."""
        wanted = i18n.ui_language(
            load_tweaks_config().get("locale", DEFAULT_LOCALE))
        current = i18n.language()
        if wanted == current:
            return
        i18n.set_language(wanted)
        title = tr("Restart {app}?", app=branding.APP_NAME)
        body = tr("{app} restarts to show its text in the language you "
                  "chose.\n\nRestart now?", app=branding.APP_NAME)
        i18n.set_language(current)
        if self._ask_yes_no(title, body):
            self._restart()

    def _game_folder_changed(self, new_val: str):
        """Wipe all state tied to the previous game folder – the folder-scoped
        config (mods/addons install records) and session TTLs/badges – and
        point everything at new_val. Called from _close_settings when the user
        has switched to a different folder."""
        if os.path.exists(os.path.join(new_val, "WoW.exe")):
            remove_wdb(new_val)

        # Wipe folder-scoped config and set the new path in one atomic merge.
        # This also re-arms the default-mods / recommended-addons auto-install.
        # Settings copied from a Windows or Wine install name the old folder
        # as a Windows path, which is no folder here at all: choosing the
        # game folder then is moving the settings across, not switching to
        # another game, so the mod and addon records are kept. Each is
        # checked against the files in the new folder as usual -- a record
        # whose files differ shows as changed, never as up to date.
        old_dir = self._cfg.get("out_dir") or ""
        keep_records = platforms.foreign_windows_path(old_dir)

        def _wipe(c):
            c["out_dir"] = new_val
            if not keep_records:
                for k in ("mods", "addons"):
                    c.pop(k, None)
        self._cfg = update_config(_wipe)
        if keep_records:
            self._log_line(f"Settings came from a Windows/Wine install "
                           f"({old_dir}); mod and addon records kept and "
                           f"checked against the new folder.\n", "acct")

        self._mod_pending_state = {}
        self._default_mods_install_started = False
        self._default_addons_install_started = False
        self._client_ready = False

        # Reset session-level state so nothing from the previous folder is
        # served from memory: addons verify + rendered list, news timers, badges.
        self._addons_verified_ts = 0.0
        self._addons_status = {"state": "idle", "addons": {}, "available": []}
        self._addon_errors = {}
        self._mod_updates_count = 0
        self._addon_updates_count = 0
        self._mpq_updates_count = 0
        self._mpq_installed_rows = []
        self._mpq_available_rows = list(MPQ_PATCHES)
        self._refresh_nav_badges()
        self._update_all_chain = False
        self._update_all_running = False
        self._refresh_update_all_btn()
        self._render_addons()
        self._render_mpq()

        self._log_line("\nGame folder changed – state reset.\n", "acct")

    def _install_missing_essential_mods(self):
        """Install every essential mod not already present. Used when the user
        turns 'Install essential mods' on after the fact."""
        if self._running:
            return
        out = self._game_path.get().strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return  # no client yet — the fresh-folder auto-install handles it
        mods_cfg = load_config().get("mods", {})
        pending = False
        for mod in MODS_REGISTRY:
            if not mod.get("essential", False):
                continue
            state = mods_cfg.get(mod["id"], {})
            if (state.get("installed_version")
                    and mod_installed_files_present(mod, out)):
                continue  # already installed
            if mod_files_on_disk(mod, out):
                continue  # somebody else's copy: shown for adoption, not replaced
            if any(mods_cfg.get(t, {}).get("enabled")
                   for t in mod_twins(mod["id"])):
                continue  # another build of it is on (a custom fork)
            self._mod_pending_state.setdefault(mod["id"], {})["enabled"] = True
            pending = True
        if not pending:
            return
        self._log_line("\nInstalling essential mods...\n", "acct")
        self._set_btn_busy(tr("Installing…"))
        self._status_var.set(tr("Downloading mods…"))
        threading.Thread(target=self._apply_mods_worker,
                         args=(out,), daemon=True).start()

    def _install_missing_recommended_addons(self):
        """Install every recommended addon not already present. Used when the
        user turns 'Install recommended addons' on afterwards."""
        if self._addons_busy:
            return
        out = self._game_path.get().strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return
        ap = addons_path(out)
        recs = [{"folder": name, "status": "available", "git": url,
                 "branch": None, "ref": None, "toc": {},
                 "description": None, "error": None}
                for name, url in RECOMMENDED_ADDONS.items()
                if not os.path.isdir(os.path.join(ap, name))]
        if not recs:
            return
        self._log_line("\nInstalling recommended addons...\n", "acct")
        self._addon_apply(recs)
