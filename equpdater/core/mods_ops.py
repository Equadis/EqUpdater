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
                octolauncher,
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


class ModsOps:
    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every
    widget and dialog goes through a hook method the host implements."""

    def _load_mods_state(self, max_age: float = _MOD_VERSION_CACHE_TTL):
        state = []
        for mod in MODS_REGISTRY:
            try:
                v = fetch_mod_latest_version_cached(mod, max_age=max_age)
            except Exception as e:
                _note_lookup(mod["id"], "failed", tr(str(e)))
                v = None
            if v:
                state.append({"id": mod["id"], "latest_version": v})
        self._mods_state = state
        client = load_config().get("out_dir") or ""
        try:
            self._identify_unversioned_mods(client)
        except Exception as e:
            log(f"Could not identify installed mod versions: {e}")
        self._log_mod_diagnostics()
        self.after(0, self._render_mod_rows)
        self.after(0, self._refresh_mods_badge)

    def _identify_unversioned_mods(self, client_dir: str, force: bool = False,
                                   only: str | None = None) -> list:
        """Name the release of mods whose files are here but whose version is
        not -- adopted or discovered ones, OctoLauncher's included -- by exact
        file match only (identify_installed_release). Records the version
        when one matches; otherwise leaves "unknown", which is the truth.

        Compared with: the latest release, and the version OctoLauncher
        wrote down when it is another one. A comparison's answer is kept per
        release and fingerprint, so files are downloaded to compare once,
        not on every start. Returns the names identified."""
        if not client_dir or not os.path.isdir(client_dir):
            return []
        cfg = load_config()
        mods_cfg = cfg.get("mods", {})
        cache = cfg.get("mod_identify_cache", {})
        hints = None
        found, seen = {}, {}
        for mod in MODS_REGISTRY:
            mid = mod["id"]
            st = mods_cfg.get(mid) or {}
            files = st.get("installed_files") or []
            if only is not None and mid != only:
                continue
            if (st.get("installed_version") or not files or st.get("error")
                    or not (st.get("managed") or st.get("discovered"))):
                continue
            if not all(os.path.exists(os.path.join(client_dir, f))
                       for f in files):
                continue
            fp = files_hash(client_dir, files)
            src = mod["source"]
            candidates = []
            if src["kind"] in RELEASE_KINDS:
                rel = _fetch_release_cached(mod)
                if rel:
                    candidates.append((_release_version(mod, rel), rel))
                if hints is None:
                    hints = octolauncher.version_hints(client_dir, MODS_REGISTRY)
                hint = hints.get(mid)
                if (hint and src.get("version_from") != "asset"
                        and all(hint != v for v, _ in candidates)):
                    candidates.append((hint, None))
            elif src.get("pinned_version"):
                candidates.append((src["pinned_version"], None))
            for ver, rel in candidates:
                key = f"{ver}|{fp}"
                known = (cache.get(mid) or {}).get(key)
                if known is not None and not force:
                    match = known
                else:
                    if rel is None and src["kind"] in RELEASE_KINDS:
                        rel = release_by_tag(src, ver)
                        if rel is None:
                            continue        # not answerable now; ask again later
                    match = identify_installed_release(mod, files, client_dir, rel)
                    seen.setdefault(mid, {})[key] = match
                if match:
                    found[mid] = (ver, fp)
                    break

        if not found and not seen:
            return []

        def _merge(c):
            mods = c.setdefault("mods", {})
            for mid, (ver, fp) in found.items():
                rec = mods.get(mid)
                if not rec or rec.get("installed_version"):
                    continue
                rec["installed_version"] = ver
                rec["version_evidence"] = "files match the published release"
                # The files are that release, byte for byte: the strongest
                # baseline there is for telling later edits apart.
                rec["content_hash"] = fp
                rec["hash_baseline"] = "release"
            ident = c.setdefault("mod_identify_cache", {})
            for mid, answers in seen.items():
                kept = dict(ident.get(mid) or {})
                kept.update(answers)
                ident[mid] = dict(list(kept.items())[-6:])
        self._cfg = update_config(_merge)
        names = []
        for mid, (ver, _fp) in found.items():
            mod = next(m for m in MODS_REGISTRY if m["id"] == mid)
            names.append(mod["name"])
            log(f"{mod['name']}: the installed files are release {ver}, "
                f"byte for byte - version recorded.")
        return names

    def _log_mod_diagnostics(self):
        """Say, once per check, why a mod that is here cannot be updated
        automatically: no answer from its source, or no known version."""
        mods_cfg = load_config().get("mods", {})
        lines = []
        for mod in MODS_REGISTRY:
            st = mods_cfg.get(mod["id"]) or {}
            if not st.get("installed_files"):
                continue
            look = MOD_LOOKUPS.get(mod["id"]) or {}
            if (look.get("outcome") in ("failed", "no-asset")
                    or not st.get("installed_version")):
                lines.append("  " + mod_lookup_line(mod, st))
        if lines:
            log("\nMods that cannot be checked or updated automatically:")
            for line in lines:
                log(line)

    def _mod_version_text(self, state: dict, live: dict | None) -> str:
        """The version beside a mod's name: what is installed, "unknown"
        when files are here and their release is not known, and the latest
        release only for a mod that is not installed. The latest release is
        never shown as if it were the installed one."""
        if state.get("installed_version"):
            return state["installed_version"]
        if state.get("installed_files"):
            return tr("unknown")
        return (live or {}).get("latest_version") or tr("unknown")

    def _mod_plans(self) -> dict:
        """Every mod's decision, keyed by id.

        The rows, the badge, Apply and Update All all read this. Before the
        planner existed each computed its own answer and they drifted: a row
        could offer an update the worker then skipped, and Update All could
        sweep a mod no row had ever offered."""
        client = self._game_path.get().strip()
        mods_cfg = load_config().get("mods", {})
        out = {}
        for mod in MODS_REGISTRY:
            state = mods_cfg.get(mod["id"], {})
            live = next((m for m in self._mods_state
                         if m["id"] == mod["id"]), None)
            out[mod["id"]] = mod_plan(mod, state, live, client)
        return out

    def _count_mod_updates(self) -> int:
        """The badge: mods that can be safely updated, and nothing else.

        A local-newer, modified or unmanaged mod is not a pending task and
        counting it as one is how a badge trains people to click Update All
        without reading."""
        return sum(1 for p in self._mod_plans().values() if p.will_update)

    def _mod_recheck(self, mod_id: str, force: bool = True):
        """Ask the mod's source again, now, past the cache -- and compare the
        installed files with what it publishes. Changes nothing on disk."""
        mod = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
        if not mod:
            return
        client = self._game_path.get().strip()
        self._log_line(f"\nChecking {mod['name']} again...\n", "acct")

        def work():
            try:
                v = fetch_mod_latest_version_cached(mod, force=force)
            except Exception as e:
                _note_lookup(mod_id, "failed", tr(str(e)))
                v = None
            try:
                self._identify_unversioned_mods(client, force=force, only=mod_id)
            except Exception as e:
                log(f"  Could not compare {mod['name']}: {e}")
            st = load_config().get("mods", {}).get(mod_id)
            log("  " + mod_lookup_line(mod, st))

            def done():
                self._mods_state = [m for m in self._mods_state
                                    if m["id"] != mod_id]
                if v:
                    self._mods_state.append({"id": mod_id, "latest_version": v})
                self._render_mod_rows()
                self._refresh_mods_badge()
            self.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _adopt_mod(self, mod_id: str):
        """Hand a discovered DLL over to EqUpdater, with consent.

        Adoption records ownership and a fingerprint of the files *as they
        are* -- it does not install anything. The user keeps the exact build
        they had; EqUpdater simply gains permission to offer them updates to
        it from here on, and can tell if those files change afterwards."""
        mod = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
        if not mod:
            return
        if not self._ask_yes_no(
                tr("Manage {name} with {app}?", name=mod["name"],
                   app=branding.APP_NAME),
                tr("{name} is already installed and {app} did not install "
                   "it.\n\n"
                   "Managing it means {app} may offer updates for it from the "
                   "configured source. Your current files are not changed "
                   "now, and a newer local build will still never be "
                   "replaced automatically.\n\n"
                   "Manage it?", name=mod["name"], app=branding.APP_NAME)):
            return
        self._record_mod_adoption(mod)
        self._log_line(f"\n{mod['name']} is now managed by "
                       f"{branding.APP_NAME}.\n", "acct")
        self._render_mod_rows()
        self._refresh_mods_badge()
        # Which release is it? Only an exact match with a published release
        # can say; until one does, the row reads "version unknown".
        self._mod_recheck(mod_id, force=False)

    def _record_mod_adoption(self, mod: dict):
        """Write down that EqUpdater looks after this mod's files as they are
        now. Installs nothing; the caller has the player's consent."""
        mod_id = mod["id"]
        client = self._game_path.get().strip()
        files = mod_found_files(mod, client)

        def _merge(c):
            mods = c.setdefault("mods", {})
            rec = dict(mods.get(mod_id) or {})
            rec.update(new_mod_record(
                managed=True, adopted=True,
                installed_version=rec.get("installed_version"),
                installed_files=files,
                content_hash=files_hash(client, files),
                enabled=True))
            rec["ignore_updates"] = bool(
                (mods.get(mod_id) or {}).get("ignore_updates", False))
            rec.pop("released", None)
            mods[mod_id] = rec
        self._cfg = update_config(_merge)

    def _update_mod(self, mod_id: str):
        """Download and install the newest release of a single mod (the
        per-row "update" label). Runs through the normal apply worker so
        errors/versions are recorded exactly like a manual Apply."""
        out = self._game_path.get().strip()
        if not out:
            return
        mod = next(m for m in MODS_REGISTRY if m["id"] == mod_id)
        self._log_line(f"\nUpdating {mod['name']}...\n", "acct")
        self._set_btn_busy(tr("Installing…"))
        self._status_var.set(tr("Downloading mods…"))
        threading.Thread(target=self._apply_mods_worker,
                         args=(out, mod_id), daemon=True).start()

    def _maybe_install_essential_mods(self):
        """Auto-install every mod flagged essential the first time this game
        folder is ready to use – i.e. on a brand-new install, or right after
        the game folder was changed to a new location. Both cases wipe the
        "mods" key from config, which is what this checks for, so once this
        has run (successfully or not – failures are recorded per-mod) it
        won't fire again until the folder changes.

        Reuses the normal apply worker so failures land in config exactly
        like a manual Apply would (per-mod "error" field, mod left disabled,
        installed_version cached the same way).

        **Discovery happens first, and it is the part that changed.** The
        inherited version installed its own preferred build of every
        essential mod the moment a game folder became usable -- including
        over a client that already had them, possibly at newer versions,
        possibly customised. A first run against somebody's existing install
        is not a fresh install and must not be treated as one.

        So anything already on disk is *recorded as found* rather than
        replaced: unmanaged, visible on the MODS page, and offered for
        adoption. Only the genuinely absent ones are installed."""
        if self._default_mods_install_started:
            return

        cfg = load_config()
        if cfg.get("mods"):
            return  # already initialized for this folder

        out = self._game_path.get().strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return  # game isn't actually installed here yet

        self._default_mods_install_started = True

        discovered = self._discover_existing_mods(out)

        # **DLLs go into the client only with consent.** The inherited
        # default installed the whole essential set -- VanillaFixes always,
        # even with the option off -- the moment a folder became usable.
        # Every one of these is code the game loads, so the first run asks,
        # once, and "Install essential mods" (Settings → General) remembers
        # the answer. VanillaFixes is asked about with the rest: it is the
        # loader that injects them, which makes it the one that matters most.
        mods_on = cfg.get("mods", {})
        missing = [m for m in MODS_REGISTRY
                   if m.get("essential", False) and m["id"] not in discovered
                   and not any(mods_on.get(t, {}).get("enabled")
                               for t in mod_twins(m["id"]))]
        auto_mods = bool(cfg.get("auto_install_mods", False))
        if missing and not auto_mods and not cfg.get("essential_mods_asked"):
            auto_mods = self._ask_essential_mods(missing)
        wanted = [m["id"] for m in missing] if auto_mods else []
        for mid in wanted:
            self._mod_pending_state.setdefault(mid, {})["enabled"] = True

        if discovered:
            self._log_line(
                "\nFound %d mod(s) already installed here: %s.\n"
                "Left untouched – open the MODS tab to have %s manage "
                "them.\n" % (len(discovered), ", ".join(sorted(discovered)),
                             branding.APP_NAME), "acct")

        if not wanted:
            self._refresh_mods_badge()
            return

        self._log_line("\nInstalling essential mods...\n", "acct")
        self._set_btn_busy(tr("Installing…"))
        self._status_var.set(tr("Downloading mods…"))
        threading.Thread(target=self._apply_mods_worker,
                         args=(out,), daemon=True).start()

    def _discover_existing_mods(self, client_dir: str) -> set:
        """Record every registry mod whose files are already in the client.

        Recorded as unmanaged with a fingerprint of the files as found, so
        the MODS page can show what is there, name it, and offer to manage
        it -- and so that if the user later adopts it, EqUpdater can tell
        whether the files changed in between.

        No version is claimed. The files were not installed by this updater
        and their release cannot be inferred from a DLL on disk; the row says
        "version not tracked" rather than guessing, because a guessed version
        is what a comparison would then act on."""
        found = {}
        for mod in MODS_REGISTRY:
            if not mod_files_on_disk(mod, client_dir):
                continue
            files = mod_found_files(mod, client_dir)
            rec = new_mod_record(
                managed=False, enabled=True,
                installed_files=files,
                content_hash=files_hash(client_dir, files),
                source_url=mod.get("repo_url"))
            rec["discovered"] = True
            found[mod["id"]] = rec

        if found:
            def _merge(c):
                mods = c.setdefault("mods", {})
                for mid, rec in found.items():
                    mods.setdefault(mid, rec)
            self._cfg = update_config(_merge)
        return set(found)

    def _apply_mods_worker(self, client_dir: str, only_mod_id: str | None = None,
                           force: bool = False):
        """Install, update and remove mods according to the plan.

        `force` is the only way a downgrade or a replacement of modified
        files can ever happen, and nothing sets it except an explicit user
        action that said so in a dialog first. Update All and every ordinary
        Apply leave it false."""
        # Work on a detached copy of the "mods" section; it's merged back into
        # the live config atomically at the end (update_config).
        mods_cfg = load_config().get("mods", {})

        # Registry order is install order (VanillaFixes first).
        ordered = MODS_REGISTRY

        pending = self._mod_pending_state
        # Arm the one-time "DXVK first launch" notice if DXVK gets (re)installed
        # this run — the new d3d9.dll invalidates the shader cache.
        set_dxvk_notice = False

        for mod in ordered:
            mid   = mod["id"]
            if only_mod_id is not None and mid != only_mod_id:
                continue
            state = mods_cfg.get(mid, {})

            # Read enabled/ignore from pending UI changes first, fall back to
            # saved config. No registry-default fallback here: a mod is only
            # ever "enabled" because the user (or the one-time default-mods
            # seed in _maybe_install_essential_mods) explicitly said so.
            enabled    = pending.get(mid, {}).get("enabled",        state.get("enabled", False))
            ignore_upd = pending.get(mid, {}).get("ignore_updates", state.get("ignore_updates", False))

            # A targeted single-mod update/retry always means "install this
            # mod". Without this, retrying a failed install is a no-op: the
            # error handler recorded enabled=False, so needs_install stays
            # False and the mod is skipped (only its error gets cleared).
            if only_mod_id is not None and mid == only_mod_id:
                enabled = True

            installed_ver = state.get("installed_version")
            # Adopted mods can have real files but no known release version.
            # Version knowledge governs updates, never presence/removal.
            tracked = state.get("installed_files") or []
            on_disk = [rel for rel in tracked
                       if os.path.exists(os.path.join(client_dir, rel))]
            is_installed = bool(tracked) and len(on_disk) == len(tracked)
            owned = bool(state.get("managed", bool(installed_ver)))
            explicit = force or only_mod_id == mid

            needs_install   = enabled and not is_installed
            needs_uninstall = not enabled and owned and bool(on_disk)

            # Some files are gone and some are not. Putting the set back
            # writes over the rest -- fine for EqUpdater's own install of a
            # known version, not for an adopted copy nobody has identified.
            # That waits for the row's reinstall, which asks first.
            if (needs_install and on_disk and not explicit
                    and not (installed_ver and state.get("installed_by"))):
                needs_install = False
                log("")
                log(f"{mod['name']}: some files are missing and the installed "
                    f"version is unknown - not reinstalled automatically.")

            # **Never install over files nobody gave us.** A discovered mod is
            # recorded unmanaged, enabled, and with no version -- which reads
            # as "wanted and not installed" above, so any Apply (the first-run
            # install included) would have written EqUpdater's build over the
            # user's own DLL. Adoption is the way in, not this. A record that
            # carries an error was ours when the install failed, so a retry
            # may still finish the job.
            if (needs_install and not force and not owned
                    and not state.get("error")
                    and mod_files_on_disk(mod, client_dir)):
                needs_install = False
                if mid in pending:
                    log("")
                    log(f"{mod['name']}: already here and not managed by "
                        f"{branding.APP_NAME}, so it was left alone. Click "
                        f"\"not managed\" on its row to manage it.")

            needs_version_lookup = needs_install or (enabled and is_installed and not ignore_upd)
            latest_ver  = None
            mod_release = None
            if needs_version_lookup:
                try:
                    if mod["source"]["kind"] in RELEASE_KINDS:
                        mod_release = _fetch_release_cached(mod)
                        latest_ver  = _release_version(mod, mod_release) if mod_release else None
                    else:
                        latest_ver = fetch_mod_latest_version_cached(mod)
                except Exception:
                    pass

            # **The decision, and the only place it is made.** Whether an
            # installed mod gets replaced is the planner's answer, not this
            # worker's opinion -- so the row, the badge, Update All and this
            # loop cannot disagree, and the safety invariants hold here
            # because they hold in one function that is tested directly.
            #
            # The old line here was `latest_ver != installed_ver`, which is
            # how V90 became V89.
            mod_state = dict(state)
            mod_state.update({"enabled": enabled, "ignore_updates": ignore_upd})
            decision = mod_plan(
                mod, mod_state,
                {"latest_version": latest_ver} if latest_ver else None,
                client_dir)

            needs_update = enabled and is_installed and (
                decision.will_update or (force and latest_ver is not None))

            # Asked to replace, with nothing to replace with: say so, and
            # keep what is here. Never a silent no-op.
            if force and enabled and is_installed and latest_ver is None:
                look = MOD_LOOKUPS.get(mid) or {}
                reason = look.get("reason") or tr("the source did not answer")
                log("")
                log(f"{mod['name']}: not replaced - could not check the latest "
                    f"release ({reason}).")
                mods_cfg.setdefault(mid, dict(state))["update_error"] = reason

            # Anything that looked like it wanted updating and deliberately
            # was not gets said out loud. A silent skip is indistinguishable
            # from a bug, and from the outside it looks exactly like the
            # updater not working.
            if (enabled and is_installed and not needs_update
                    and not needs_uninstall and decision.skipped
                    and decision.action not in (Action.SKIP_UP_TO_DATE,
                                                Action.SKIP_NOT_INSTALLED,
                                                Action.SKIP_DISABLED)):
                log("")
                log(decision.log_line())

            if not (needs_install or needs_uninstall or needs_update):
                if mid in pending:
                    mods_cfg.setdefault(mid, {}).update({
                        "enabled":        enabled,
                        "ignore_updates": ignore_upd,
                    })
                # A previously failed mod that the user leaves disabled on a
                # later Apply counts as dismissed — clear the error so it
                # stops blocking the PLAY button.
                if not enabled and state.get("error"):
                    mods_cfg.setdefault(mid, {})["error"] = None
                # An installed, enabled, up-to-date mod still has to be listed
                # in dlls.txt to load, and nothing else re-checks that. The
                # three paths that write the file are install, update and
                # uninstall, so once it drifts out of step with the config --
                # a restore from one of the installer's backups, an interrupted
                # run, a mod installed before DLL_LOAD_ORDER existed -- the mods
                # list reports the mod as enabled forever while VanillaFixes
                # never injects it. That is a silent failure with nothing to
                # find: the UI and the config both say yes, and only the file
                # says no.
                if enabled and is_installed and mod.get("register_dll"):
                    if ensure_dll(client_dir, mod["register_dll"]):
                        log("")
                        log(f"{mod['name']} was enabled but missing "
                            f"from dlls.txt - re-registered "
                            f"{mod['register_dll']}.")
                continue

            status = (tr("Installing {name}…", name=mod["name"]) if needs_install
                      else tr("Updating {name}…", name=mod["name"]) if needs_update
                      else tr("Removing {name}…", name=mod["name"]))
            self.after(0, lambda t=status: self._status_var.set(t))

            # Files that are here and about to be written over are copied
            # aside first, so a download or install that fails part-way
            # leaves the mod as it was instead of half gone.
            stash, touched, keep_stash = None, False, False
            try:
                if (needs_install or needs_update) and on_disk:
                    stash = _stash_mod_files(client_dir, on_disk)
                touched = True
                if needs_install:
                    log(f"\nInstalling {mod['name']} {latest_ver}...")
                    written = install_mod(mod, client_dir, release=mod_release)
                    if not written:
                        raise RuntimeError(N_("the download did not contain "
                                              "the mod's files"))
                    if mod.get("register_dll"):
                        add_dll(client_dir, mod["register_dll"])
                    resolved_ver = mod.pop("_resolved_version", None) or latest_ver or "unknown"
                    # A full provenance record, fingerprint included: without
                    # one a first install could never be told apart from one
                    # the user has since edited.
                    mods_cfg[mid] = new_mod_record(
                        managed=True, enabled=True,
                        installed_version=resolved_ver,
                        installed_files=written,
                        content_hash=files_hash(client_dir, written),
                        source_url=mod.get("repo_url"))
                    mods_cfg[mid]["ignore_updates"] = ignore_upd
                    if mid == "dxvk":
                        set_dxvk_notice = True
                    log(f"  \u2713 {mod['name']} installed.")

                elif needs_uninstall:
                    log(f"\nUninstalling {mod['name']}...")
                    uninstall_mod(mod, client_dir)
                    if mod.get("register_dll"):
                        remove_dll(client_dir, mod["register_dll"])
                    # The record stays, saying we no longer own anything
                    # here. Dropping the key would make the mod look
                    # never-seen -- and a never-seen mod whose files are
                    # still on disk is exactly the unmanaged case, which
                    # would then be offered for adoption seconds after the
                    # user asked for it to be removed.
                    mods_cfg[mid] = new_mod_record(managed=False, enabled=False)
                    mods_cfg[mid]["ignore_updates"] = ignore_upd
                    log(f"  \u2713 {mod['name']} uninstalled.")

                elif needs_update:
                    log(f"\nUpdating {mod['name']} "
                        f"{installed_ver or tr('unknown')} \u2192 {latest_ver}...")
                    uninstall_mod(mod, client_dir)
                    written = install_mod(mod, client_dir, release=mod_release)
                    if not written:
                        raise RuntimeError(N_("the download did not contain "
                                              "the mod's files"))
                    if mod.get("register_dll"):
                        add_dll(client_dir, mod["register_dll"])
                    mods_cfg[mid] = new_mod_record(
                        managed=True, enabled=True,
                        installed_version=latest_ver,
                        installed_files=written,
                        content_hash=files_hash(client_dir, written),
                        source_url=mod.get("repo_url"))
                    mods_cfg[mid]["ignore_updates"] = ignore_upd
                    if mid == "dxvk":
                        set_dxvk_notice = True
                    log(f"  \u2713 {mod['name']} updated.")

            except Exception as e:
                err = describe_install_error(e)
                log(f"  \u2717 {mod['name']}: {err}")
                kept = bool(on_disk) and not needs_uninstall and (
                    not touched or (stash is not None and
                                    _restore_mod_files(client_dir, stash,
                                                       on_disk)))
                if kept:
                    # The mod is as it was: same files, same record, and the
                    # failure noted beside it -- the mod still works, so this
                    # does not hold PLAY back.
                    rec = dict(state)
                    rec.update(enabled=enabled, ignore_updates=ignore_upd,
                               update_error=err)
                    mods_cfg[mid] = rec
                    log(f"  Your previous {mod['name']} files were kept.")
                elif on_disk:
                    # Could not put them back. The record keeps naming the
                    # files, so they are not lost from view, and the error
                    # holds PLAY until it is dealt with. The copy stays where
                    # the log says it is.
                    rec = dict(state)
                    rec["error"] = err
                    mods_cfg[mid] = rec
                    keep_stash = True
                else:
                    mods_cfg[mid] = new_mod_record(managed=False, enabled=False)
                    mods_cfg[mid]["ignore_updates"] = ignore_upd
                    mods_cfg[mid]["error"] = err
            finally:
                if stash is not None and not keep_stash:
                    shutil.rmtree(stash, ignore_errors=True)

        # Merge just the "mods" key into the current on-disk config (which
        # other threads may have written to during this long install), rather
        # than saving our stale whole-config snapshot.
        sorted_mods = dict(sorted(mods_cfg.items(),
                                  key=lambda kv: kv[0].lower()))
        def _merge(c):
            c["mods"] = sorted_mods
            if set_dxvk_notice:
                c["dxvk_notice_pending"] = True
        fresh_cfg  = update_config(_merge)
        fresh_mods = fresh_cfg.get("mods", {})
        # Keep pending checkbox toggles on a targeted single-mod update —
        # they were never applied and would be lost otherwise.
        if only_mod_id is None:
            self._mod_pending_state = {}

        # Whatever the scope of this run, every enabled mod must be in
        # dlls.txt when it ends. See reconcile_dlls_txt for the case that
        # made this a separate step.
        for name in reconcile_dlls_txt(client_dir, fresh_mods):
            log(f"{name} was enabled but missing from dlls.txt - re-registered.")

        self.after(0, lambda: self._mods_finished(fresh_mods))

    def _mod_force_replace(self, mod_id: str):
        """The same escape hatch for a DLL mod.

        The V90 -> V89 case is the reason it is worded the way it is: this is
        the *only* path that can install an older release over a newer one,
        and it says so before it does."""
        mod = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
        client = self._game_path.get().strip()
        if not mod or not client:
            return

        state = load_config().get("mods", {}).get(mod_id, {})
        live = next((m for m in self._mods_state if m["id"] == mod_id), None)
        decision = mod_plan(mod, state, live, client)
        if (decision.status is Status.UNVERIFIABLE
                and decision.detail.get("cause") == CAUSE_LOOKUP) \
                or not decision.remote:
            # Nothing to install: the source has not said what its release
            # is. Replacing files with "whatever downloads" is not a repair.
            look = MOD_LOOKUPS.get(mod_id) or {}
            self._show_info(
                tr("Replace {name}?", name=mod["name"]),
                tr("Could not check the latest release: {reason}.",
                   reason=look.get("reason") or tr("the source did not answer"))
                + "\n\n" + tr("Nothing is changed. Click to check again."))
            return
        remote = decision.remote

        note = ""
        if decision.status is Status.LOCAL_NEWER:
            note = "\n\n" + tr("This is a DOWNGRADE: {local} is installed and "
                               "{remote} is what the source offers.",
                               local=decision.local, remote=decision.remote)
        elif decision.status is Status.MODIFIED:
            note = "\n\n" + tr("The files here differ from the ones EqUpdater "
                               "installed.")
        elif not decision.local:
            note = "\n\n" + tr("EqUpdater cannot tell which version is "
                               "installed now. If yours is newer, or a custom "
                               "build, this replaces it with an older one.")

        if not self._ask_ok_cancel(
                tr("Replace {name}?", name=mod["name"]),
                tr("Install {version} over the copy already here.",
                   version=remote) + note + "\n\n"
                + tr("The current files are backed up to {backups} first.",
                     backups=BACKUP_DIR) + " "
                + tr("If the download fails, they stay as they are.")):
            return

        for rel in (state.get("installed_files")
                    or mod.get("installed_files") or []):
            backup_component(os.path.join(client, rel), "mods")

        self._log_line("\nReplacing %s at your request.\n" % mod["name"],
                       "acct")
        self._set_btn_busy(tr("Installing\u2026"))
        threading.Thread(target=self._apply_mods_worker,
                         args=(client, mod_id, True), daemon=True).start()
