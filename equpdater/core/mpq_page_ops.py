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
from . import oplock
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


class MpqPageOps:
    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every
    widget and dialog goes through a hook method the host implements."""

    #: MPQ state -> (label, colour, tooltip). A patch MPQ carries no version,
    #: so "differs from what the source ships today" is the only comparison
    #: available -- and it is equally true of an older pack, a newer one, and
    #: one somebody made themselves. These say which is which instead of
    #: calling all three out of date and offering to replace them.
    _MPQ_STATES = {
        "modified":      (N_("Modified locally"), "#d4b43c",
                          N_("This file has changed since EqUpdater installed "
                          "or linked it. Automatic updates are held back.")),
        "unverifiable":  (N_("Unable to verify"), None,
                          N_("The source could not be checked, so nothing is "
                          "replaced.")),
        "sourceDiffers": (N_("Differs from source"), "#d4b43c",
                          N_("This is not the file its source publishes now. An MPQ carries no version, so it could be older, newer or edited – it is left alone. Replace installs the source's copy and keeps yours as a .bak file.")),
        "unconfirmed":   (N_("Source not confirmed"), None,
                          N_("The source publishes no checksum, so EqUpdater "
                          "cannot prove this file is its copy. Replace "
                          "installs the source's copy, keeps yours as a "
                          ".bak file, and tracks updates from then on.")),
    }


    def _scan_mpq_patches(self) -> list:
        """Lettered patch archives (patch-<letter>.mpq, case-insensitive) present in the
        game's Data folder, e.g. Patch-A.mpq / patch-b.MPQ. Sorted by letter."""
        out = self._game_path.get().strip()
        data_dir = gamepaths.game_path(out, "Data") if out else ""
        found = []
        if data_dir and os.path.isdir(data_dir):
            for name in os.listdir(data_dir):
                if re.fullmatch(r"patch-[a-z]\.mpq", name, re.IGNORECASE):
                    found.append(name)
        found.sort(key=str.lower)
        return found

    @staticmethod
    def _mpq_record(records: dict, filename: str):
        """A pack's record, matched case-insensitively: Windows does not care
        whether the file is patch-O.mpq or Patch-O.MPQ, so neither may this."""
        lc = filename.lower()
        return next((r for k, r in (records or {}).items()
                     if k.lower() == lc and isinstance(r, dict)), None)

    @staticmethod
    def _mpq_save_record(filename: str, record):
        """Write (or, with None, drop) one pack's record. Safe from a worker
        thread: update_config holds the config lock."""
        def _merge(c):
            recs = c.setdefault("mpq", {})
            for k in [k for k in recs if k.lower() == filename.lower()]:
                del recs[k]
            if record is not None:
                recs[filename] = record
        update_config(_merge)

    def _mpq_check(self):
        """Scan Data/ for lettered patches and ask each *linked* pack's source
        what it publishes now. Feeds the INSTALLED/AVAILABLE lists and the
        tab badge; the source checks run in the background so the list
        appears immediately.

        A pack nobody linked is not checked at all. Its file name matching
        one of ours is not evidence of where it came from, and comparing it
        with a source it may have nothing to do with would produce exactly
        the confident wrong answer this updater exists to avoid."""
        installed_files = self._scan_mpq_patches()
        installed_lc    = {f.lower() for f in installed_files}
        records = load_config().get("mpq", {}) or {}
        rows = []
        for f in installed_files:
            rec = self._mpq_record(records, f)
            src = mpq.source_of(rec, f)
            rows.append({"file": f, "entry": mpq_patch_for(f), "record": rec,
                         "source": src,
                         "state": None if src else "unmanaged",
                         "update": False})
        self._mpq_installed_rows = rows
        self._mpq_available_rows = [
            e for e in MPQ_PATCHES if e["file"].lower() not in installed_lc]
        self._render_mpq()

        out = self._game_path.get().strip()
        data_dir = gamepaths.game_path(out, "Data") if out else ""
        linked = [r for r in rows if r["source"]]
        if not (linked and data_dir):
            self._mpq_updates_count = 0
            self._refresh_mpq_badge()
            return

        def worker():
            results = {}
            for row in linked:
                name, rec = row["file"], row["record"]
                try:
                    local = sha256_file(os.path.join(data_dir, name))
                except OSError:
                    results[name] = ("unverifiable", False)
                    continue
                try:
                    remote, failed = resolve_mpq_source(row["source"], name), False
                except Exception:
                    # Could not ask. Not an update, not an error the user has
                    # to clear - just an unknown, and unknowns are left alone.
                    remote, failed = None, True
                results[name] = mpq.judge(rec, local, remote, failed)
                settled = mpq.settle(rec, local, remote)
                if settled:
                    self._mpq_save_record(name, settled)

            def apply():
                count = 0
                for row in self._mpq_installed_rows:
                    state = results.get(row["file"])
                    if state:
                        row["state"], row["update"] = state
                        count += bool(row["update"])
                self._mpq_updates_count = count
                self._refresh_mpq_badge()
                self._render_mpq()
            self.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _mpq_start(self, filename: str, text: str) -> str | None:
        """Mark one pack busy and return the Data folder, or None if another
        pack is busy or there is no game folder."""
        if getattr(self, "_mpq_busy", None):
            return None                              # one at a time
        out = self._game_path.get().strip()
        if not out:
            self._log_line("Set the game folder first.\n", "err")
            return None
        self._mpq_busy, self._mpq_busy_text = filename, text
        self._render_mpq()
        return gamepaths.game_path(out, "Data")

    def _mpq_finish(self):
        self._mpq_busy = None
        self._mpq_check()                            # re-scan + re-check + badge

    def _mpq_fetch(self, filename: str, source: dict, label: str,
                   replace: bool = False):
        """Install, update or replace one pack from `source`.

        An ordinary update only goes ahead if the file is still exactly what
        was recorded -- it may have changed since the row was drawn -- and
        then overwrites it, because it is the source's own older copy. A
        replace is the user overruling a held-back state after a warning,
        and keeps their file beside the new one."""
        data_dir = self._mpq_start(filename, tr("Downloading…"))
        if data_dir is None:
            return
        self._log_line(f"\nDownloading {label}…\n", "acct")
        path = os.path.join(data_dir, filename)

        def worker():
            err = None
            try:
                if not replace and os.path.exists(path):
                    rec = self._mpq_record(load_config().get("mpq", {}), filename)
                    if not (rec and rec.get("sha")
                            and sha256_file(path) == rec["sha"]):
                        raise RuntimeError(
                            "the file changed since it was checked; "
                            "nothing was replaced")
                remote = resolve_mpq_source(source, filename)
                with oplock.game_folder(os.path.dirname(data_dir.rstrip("/\\")),
                                        30.0, "installing a texture pack"):
                    sha = download_mpq(remote, data_dir, filename,
                                       keep_existing=replace)
                # Ours now, and this is the fingerprint of what we wrote -
                # which is what lets a later run tell an edited pack from an
                # untouched one, instead of calling both "out of date".
                self._mpq_save_record(filename, mpq.installed_record(
                    sha=sha, source=source, remote=remote,
                    installed_by=branding.APP_NAME))
            except Exception as e:
                err = str(e)

            def done():
                if err:
                    self._log_line(f"✗  {label} failed: {err}\n", "err")
                else:
                    self._log_line(f"✓  {label} installed.\n", "ok")
                self._mpq_finish()
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _mpq_replace(self, row):
        fname = row["file"]
        if not self._ask_yes_no(
                tr("Replace {name}?", name=fname),
                tr("{file} will be replaced with the copy from:\n    {source}\n\n"
                   "Your current file is not deleted. It is renamed to\n"
                   "    {file}.<date>.bak\n"
                   "in the Data folder, where the game ignores it. Rename it "
                   "back to undo.\n\nReplace it?",
                   file=fname, source=mpq.describe_source(row["source"]))):
            return
        entry = row.get("entry")
        self._mpq_fetch(fname, row["source"],
                        entry["name"] if entry else fname, replace=True)

    def _mpq_uninstall(self, filename: str):
        """Delete a pack from Data, after asking, and forget its record."""
        out = self._game_path.get().strip()
        if not out or getattr(self, "_mpq_busy", None):
            return
        if not self._ask_yes_no(
                tr("Uninstall {name}?", name=filename),
                tr("{file} is deleted from the game's Data folder.",
                   file=filename)):
            return
        try:
            os.remove(os.path.join(gamepaths.game_path(out, "Data"), filename))
        except FileNotFoundError:
            pass
        except OSError as e:
            self._log_line(f"Could not uninstall {filename}: {e}\n", "err")
            return
        self._mpq_save_record(filename, None)
        self._log_line(f"\n{filename} uninstalled.\n", "acct")
        self._mpq_check()

    def _mpq_unlink(self, filename: str):
        """Forget where a pack came from. The file stays exactly where it is;
        it simply stops being checked, as if it had been installed by hand."""
        self._mpq_save_record(filename, None)
        self._log_line("\n%s is no longer linked to a source; the file is "
                       "untouched.\n" % filename, "acct")
        self._mpq_check()

    def _mpq_link(self, filename: str, source: dict):
        """Record that `filename` comes from `source`. **Changes no file.**

        The source is asked what it publishes now and the pack is
        fingerprinted as it is. If the two agree, the pack is tracked from
        here like one EqUpdater installed; if they do not, the link is kept
        and the pack is held as differing from its source. If the source
        cannot be reached, nothing is recorded: a link that was never
        checked would read as one that was."""
        data_dir = self._mpq_start(filename, tr("Checking…"))
        if data_dir is None:
            return
        where = mpq.describe_source(source)

        def worker():
            err, rec = None, None
            try:
                local = sha256_file(os.path.join(data_dir, filename))
                remote = resolve_mpq_source(source, filename)
                rec = mpq.linked_record(sha=local, source=source, remote=remote)
                self._mpq_save_record(filename, rec)
            except Exception as e:
                err = str(e)

            def done():
                if err:
                    self._log_line("\n✗  Could not link %s to %s: %s\n"
                                   "Nothing was changed.\n"
                                   % (filename, where, err), "err")
                else:
                    self._log_line("\n%s is now linked to %s. Its file was "
                                   "not changed.\n%s\n"
                                   % (filename, where, mpq.link_outcome(rec)),
                                   "acct")
                self._mpq_finish()
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()
