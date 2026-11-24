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


class AddonsOps:
    """Lifted from EqUpdaterApp (tools/lift_methods.py). Logic only: every
    widget and dialog goes through a hook method the host implements."""

    #: Addon status -> (label, colour, tooltip). Anything that is not an
    #: offer to act reads as plain text in a muted colour: a state the user
    #: cannot act on is not drawn to look like a button.
    _ADDON_STATES = {
        "localNewer":      (N_("Local version newer"), "#d4b43c",
                            N_("The installed commit is ahead of the source's. "
                            "EqUpdater will not roll it back.")),
        "modified":        (N_("Modified locally"), "#d4b43c",
                            N_("These files differ from the ones EqUpdater "
                            "installed. Automatic updates are held back so "
                            "your changes survive.")),
        "differentSource": (N_("Different source"), "#d4b43c",
                            N_("Installed from a different repository than the "
                            "one now recommended. Switching forks is a "
                            "choice, not an update.")),
        "diverged":        (N_("Different revision"), "#d4b43c",
                            N_("The installed commit and the remote one do not contain each other – another branch, a fork, or rebased history.")),
        "unverifiable":    (N_("Unable to verify"), "#91a3bd",
                            N_("Which side is newer could not be established, "
                            "so nothing is replaced.")),
        "ignored":         (N_("Ignored"), "#91a3bd",
                            N_("Excluded from updates at your request.")),
        "unknown":         (N_("Not versioned"), "#91a3bd", ""),
    }


    def _identify_adopted_addons(self, client_dir: str):
        """Find the commit of managed addons that have none: from their own
        clone when its files still match it, otherwise by comparing them
        with the source's latest commit. Only an exact match records a sha.
        An answer is kept per commit and fingerprint, so an addon is
        downloaded to compare once, not on every check."""
        if not client_dir:
            return
        cfg = load_config()
        cache = cfg.get("addon_identify_cache", {})
        found, seen = {}, {}
        root = addons_path(client_dir)
        for folder, saved in list((cfg.get("addons") or {}).items()):
            dirp = os.path.join(root, folder)
            git = saved.get("git")
            if (not addon_is_managed(saved) or saved.get("sha") or not git
                    or not os.path.isdir(dirp)):
                continue
            fp = folder_hash(dirp)
            clone = gitlocal.read_clone(dirp)
            if (clone and clone.sha and same_repo(clone.remote, git)
                    and gitlocal.clone_is_clean(dirp)):
                found[folder] = (clone.sha, clone.branch, fp)
                continue
            remote = addon_remote_sha(git, saved.get("branch"), saved.get("ref"))
            if not remote:
                continue
            key = f"{remote}|{fp}"
            if (cache.get(folder) or {}).get(key) is False:
                continue
            match = self._addon_is_commit(client_dir, folder, git, remote, fp)
            if match is None:
                continue                    # no answer; ask again later
            seen.setdefault(folder, {})[key] = match
            if match:
                found[folder] = (remote, saved.get("branch"), fp)

        def _merge(c):
            addons = c.setdefault("addons", {})
            for folder, (sha, branch, fp) in found.items():
                rec = addons.get(folder)
                if not rec or rec.get("sha"):
                    continue
                rec["sha"] = sha
                rec["branch"] = rec.get("branch") or branch
                rec["content_hash"] = fp
                rec["hash_baseline"] = "commit"
            ident = c.setdefault("addon_identify_cache", {})
            for folder, answers in seen.items():
                kept = dict(ident.get(folder) or {})
                kept.update(answers)
                ident[folder] = dict(list(kept.items())[-4:])
        if found or seen:
            update_config(_merge)
        for folder, (sha, _b, _f) in found.items():
            log(f"{folder}: the installed files are commit {sha[:10]} - recorded.")

    def _maybe_install_default_addons(self):
        """Auto-install the recommended addons the first time this game
        folder is ready – the same one-shot mechanism as the default mods:
        the absence of the "addons" key in config means "never initialized
        for this folder", and a game-folder change wipes it to re-arm.
        Runs after the default mods finished (chained from the mods apply
        completion) or directly when mods were already initialized."""
        if self._default_addons_install_started:
            return
        if load_config().get("addons") is not None:
            return  # already initialized for this folder

        out = self._game_path.get().strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return  # game isn't actually installed here yet

        self._default_addons_install_started = True

        # Mark this folder as initialized even if every install fails, so
        # the batch doesn't re-fire on the next verify.
        update_config(lambda c: c.setdefault("addons", {}))

        # "Install recommended addons" (Settings → General): when
        # off, skip the batch install but still verify so the ADDONS tab lists
        # them as available for manual install.
        if not load_config().get("auto_install_addons", False):
            self._addons_verify()
            return

        ap = addons_path(out)
        recs = [{"folder": name, "status": "available", "git": url,
                 "branch": None, "ref": None, "toc": {},
                 "description": None, "error": None}
                for name, url in RECOMMENDED_ADDONS.items()
                if not os.path.isdir(os.path.join(ap, name))]
        if not recs:
            # Nothing to install (e.g. switched to a folder that already has
            # the addons) — still run a verify so the ADDONS tab badge shows
            # any available updates without the user opening the tab.
            self._addons_verify()
            return
        self._log_line("\nInstalling recommended addons...\n", "acct")
        self._addon_apply(recs)

    def _addons_verify(self, force=False, remote_checks=True):
        """Scan Interface/AddOns, match against the catalog, and check every
        tracked addon's remote commit sha (config-cached). With
        remote_checks=False the scan is guaranteed network-free: shas come
        from the cache only (used for post-install/update refreshes)."""
        if self._addons_busy:
            return
        # A recent verify result is already rendered — plain tab switches
        # within the TTL don't need a rescan or a rebuild at all.
        if (not force and self._addons_status["state"] == "done"
                and (time.time() - self._addons_verified_ts)
                < ADDONS_VERIFY_TTL):
            return
        client = self._game_path.get().strip()
        if client and self._addon_dir_conflict_prompt(client):
            return
        self._addons_busy = True
        had_content = bool(self._addons_status["addons"]
                           or self._addons_status["available"])
        self._addons_status = {**self._addons_status, "state": "verifying"}
        if had_content:
            # Keep showing the existing list while checking in background.
            self._refresh_addons_footer()
        else:
            self._render_addons()

        def worker():
            try:
                catalog = fetch_addons_catalog(force=force)
            except Exception:
                # offline — fall back to whatever the config still holds
                catalog = (load_config().get("addons_catalog_cache", {})
                           .get("catalog") or [])

            # The catalogue writes some OctoWoW Git sources under another
            # name for the same forge; read them as the canonical URL so
            # they validate, and are saved, as octowow.st/git.
            available = [{"folder": a.get("name"), "status": "available",
                          "git": canonical_repo_url(a.get("git")),
                          "branch": a.get("branch"),
                          "ref": a.get("ref"), "toc": a.get("toc") or {},
                          "description": a.get("description"), "error": None}
                         for a in catalog
                         if a.get("name") and a["name"] not in BLOCKED_ADDONS]

            # Curated recommendations: apply git-URL overrides on top of the
            # catalog, and synthesize entries for recommended addons the
            # catalog doesn't carry (or has renamed). Overridden forks may
            # use a different default branch, so branch/ref are reset.
            by_name = {a["folder"]: a for a in available}
            for name, override in CURATED_ADDONS.items():
                rec = by_name.get(name)
                if rec is None:
                    available.append(
                        {"folder": name, "status": "available",
                         "git": override, "branch": None, "ref": None,
                         "toc": {}, "description": None, "error": None})
                elif not _same_git_repo(rec.get("git"), override):
                    rec.update(git=override, branch=None, ref=None)

            addons = {}
            records = load_config().get("addons", {})
            ap = addons_path(client) if client else ""
            if ap and os.path.isdir(ap):
                for name in sorted(os.listdir(ap)):
                    if name == lootgate.FOLDER:
                        continue        # part of the Auto Loot setting
                    if name.startswith(("Blizzard_", "Turtle_")):
                        continue
                    dirp = os.path.join(ap, name)
                    if not os.path.isdir(dirp):
                        continue
                    rec = {"folder": name, "status": "unknown", "git": None,
                           "branch": None, "ref": None, "toc": {},
                           "description": None, "error": None}
                    toc_path = os.path.join(dirp, f"{name}.toc")
                    if not os.path.exists(toc_path):
                        rec.update(status="invalid",
                                   error=N_("Missing .toc file"))
                        addons[name] = rec
                        continue
                    rec["toc"] = read_toc_file(toc_path)
                    avail = next((a for a in available
                                  if a["folder"] == name), None)
                    if avail:
                        rec["description"] = avail["description"]
                    saved = records.get(name)
                    override = CURATED_ADDONS.get(name)

                    # What the catalogue currently recommends for this folder
                    # name. A *suggestion*, and never more than that until
                    # the user says so: a folder called Questie matching a
                    # Questie entry is a coincidence of naming, not a claim
                    # of ownership over whatever is in it.
                    suggested = override or (avail or {}).get("git")
                    rec["suggested_git"] = suggested
                    rec["managed"] = addon_is_managed(saved)
                    if not rec["managed"]:
                        # A clone knows where it came from, which outranks
                        # what the catalogue would recommend for the name --
                        # and works for an addon the catalogue has never heard
                        # of. OctoLauncher's addons are all clones.
                        clone = gitlocal.read_clone(dirp)
                        if clone and is_allowed_git_url(
                                canonical_repo_url(clone.remote).removesuffix(".git")):
                            rec["suggested_git"] = canonical_repo_url(
                                clone.remote).removesuffix(".git")
                        rec["catalog_git"] = suggested
                        rec["catalog_ref"] = (avail or {}).get("ref")
                        rec["catalog_branch"] = (avail or {}).get("branch")

                    if not rec["managed"]:
                        # **Found, not owned.** The inherited updater matched
                        # this folder against the catalogue and offered to
                        # "update" it -- which meant deleting somebody's
                        # manually installed addon and putting a different
                        # copy there. It is shown, its .toc is read, a
                        # possible source is offered, and nothing is touched.
                        rec.update(git=canonical_repo_url(saved.get("git"))
                                   if saved else None,
                                   status="unmanaged")
                        addons[name] = rec
                        continue

                    # Managed: everything below asks about the source the
                    # files actually came from, never about the catalogue's
                    # current preference. Asking a different fork for "the
                    # latest commit" and comparing it with ours is not a
                    # question with a meaningful answer.
                    rec.update(git=canonical_repo_url(saved.get("git")),
                               branch=saved.get("branch"),
                               ref=saved.get("ref"),
                               custom=saved.get("custom_source",
                                                saved.get("custom", False)))

                    lookup_failed = False
                    if remote_checks:
                        remote = addon_remote_sha(
                            rec["git"], rec["branch"], rec["ref"],
                            force=force)
                    else:
                        remote = addon_cached_sha(
                            rec["git"], rec["branch"], rec["ref"])
                        if remote is None:
                            # No cached answer and no network wanted: treat
                            # it as current rather than guessing, and do not
                            # record a failure the user would have to clear.
                            remote = saved.get("sha")
                    if remote is None:
                        lookup_failed = True

                    # Which came first. A different sha is an observation;
                    # this is the question. Skipped when the answer cannot
                    # change anything -- same sha, or nothing to compare.
                    anc = None
                    if (remote and saved.get("sha")
                            and remote != saved.get("sha") and remote_checks):
                        anc = addon_ancestry(rec["git"], saved["sha"], remote,
                                             force=force)
                    elif remote and remote == saved.get("sha"):
                        anc = Ancestry.IDENTICAL

                    comp = addon_component(
                        name, saved, installed=True, remote_sha=remote,
                        configured_source=suggested,
                        current_hash=(folder_hash(dirp)
                                      if saved.get("content_hash") else None),
                        ancestry_result=anc, lookup_failed=lookup_failed,
                        installed_version=rec["toc"].get("Version"))
                    decision = plan(comp)
                    rec["plan"] = decision
                    rec["status"] = ADDON_STATUS_NAMES.get(
                        decision.status, "unverifiable")
                    rec["reason"] = decision.reason
                    rec["remote_sha"] = remote
                    rec["installed_sha"] = saved.get("sha")
                    if decision.status is Status.UNVERIFIABLE and lookup_failed:
                        # Not an addon error: nothing is wrong with the files,
                        # which stay as they are, nor with the record. The
                        # next look asks again.
                        rec["check_failed"] = (
                            addon_lookup_reason(rec["git"], rec["branch"],
                                                rec["ref"])
                            or N_("the source did not answer"))
                        log(f"Addon source check failed: {name} from "
                            f"{rec['git']}: {rec['check_failed']}")
                    addons[name] = rec

            # Overlay install failures from this session: the rescan drops
            # them (a failed install leaves no folder on disk), so re-attach
            # errors to the matching available row — or synthesize one for a
            # failed custom addon. Errors for now-installed folders are stale
            # and dropped.
            for folder in [f for f in self._addon_errors if f in addons]:
                self._addon_errors.pop(folder, None)
            by_name = {a["folder"]: a for a in available}
            for folder, info in self._addon_errors.items():
                rec = by_name.get(folder)
                if rec is None:
                    rec = {"folder": folder, "status": "available",
                           "git": info.get("git"), "branch": None,
                           "ref": None, "toc": {}, "description": None,
                           "error": None}
                    available.append(rec)
                rec["error"] = info["error"]

            def done():
                changed = (addons != self._addons_status["addons"]
                           or available != self._addons_status["available"])
                self._addons_status = {"state": "done", "addons": addons,
                                       "available": available}
                self._addons_verified_ts = time.time()
                self._addons_busy = False
                self._refresh_addons_badge()
                if changed:
                    self._render_addons(keep_scroll=True)
                else:
                    self._refresh_addons_footer()
                self._refresh_manage_all()
                self.after(500, self._offer_manage_found)
                self._identify_addons_once()
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _addon_apply(self, recs, force: bool = False):
        """Install/update the given addon records sequentially.

        **The last line of defence, and it belongs here.** Every addon
        install in the application funnels through this one method, so this
        is the one place that can guarantee invariant 2 no matter which
        caller got confused: a folder that exists on disk and is not managed
        by EqUpdater is not written over.

        The guard is redundant with the planner, deliberately. The planner
        decides correctly and every caller consults it -- but "every caller"
        is a claim about code that will be edited by people who have not read
        this docstring, and the cost of it being false once is somebody's
        addon. `force` is the explicit path (see _addon_force_replace) and is
        never set by a sweep."""
        client = self._game_path.get().strip()
        if not client or self._addons_busy or not recs:
            return

        if not force:
            managed = load_config().get("addons", {})
            safe, refused = [], []
            for rec in recs:
                folder = rec.get("folder")
                on_disk = bool(client) and os.path.isdir(
                    os.path.join(addons_path(client), folder or ""))
                if on_disk and not addon_is_managed(managed.get(folder)):
                    refused.append(folder)
                else:
                    safe.append(rec)
            for folder in refused:
                log("")
                log(f"{folder}:")
                log(f"  Installed on disk, and not managed by "
                    f"{branding.APP_NAME}.")
                log("  Skipping: refusing to overwrite an addon this "
                    "updater did not install.")
            recs = safe
            if not recs:
                self._addons_verify(remote_checks=False)
                return

        self._addons_busy = True
        self._addons_installing = True
        before = {}          # folder -> (status, was it listed as installed)
        for rec in recs:
            before[rec["folder"]] = (
                rec.get("status"),
                rec["folder"] in self._addons_status["addons"])
            rec["status"] = "downloading"
            self._addons_status["addons"].setdefault(rec["folder"], rec)
        self._render_addons(keep_scroll=True)
        # PLAY is inactive while addons download/install, same as for mods.
        self._set_btn_busy(tr("Installing…"))
        self._status_var.set(tr("Downloading addons…"))

        failures = []        # (folder, update?, template, values)

        def worker():
            for index, rec in enumerate(recs):
                self.after(0, lambda i=index, n=len(recs), f=rec["folder"]:
                           self._progress_set(i, n, f))
                self.after(0, lambda n=rec["folder"]: self._status_var.set(
                    tr("Installing {name}…", name=n)))
                updating = before[rec["folder"]][1] or os.path.isdir(
                    os.path.join(addons_path(client), rec["folder"]))
                try:
                    rec["git"] = addon_install_source(rec.get("git"))
                    sha = addon_remote_sha(rec["git"], rec.get("branch"),
                                           rec.get("ref"), raise_errors=True)
                    if not sha:
                        raise RuntimeError(N_("Could not resolve remote commit"))
                    install_addon_files(client, rec["folder"], rec["git"], sha)
                    if rec["folder"] == "pfUI":
                        patch_pfui_default_profile(client)
                    # Provenance, written the moment the files land: we
                    # installed this, from this repository, at this commit,
                    # and this is the fingerprint of what arrived. The
                    # fingerprint is what lets a later run tell "the user
                    # edited this" from "the user did not", which the commit
                    # sha alone never could.
                    record = new_addon_record(
                        managed=True, git=rec["git"],
                        branch=rec.get("branch"), ref=rec.get("ref"),
                        sha=sha,
                        content_hash=folder_hash(
                            os.path.join(addons_path(client), rec["folder"])),
                        custom_source=bool(rec.get("custom")))
                    old_rec = load_config().get("addons", {}).get(
                        rec["folder"]) or {}
                    record["ignore_updates"] = bool(
                        old_rec.get("ignore_updates", False))
                    update_config(lambda c, f=rec["folder"], r=record:
                                  c.setdefault("addons", {}).__setitem__(f, r))
                    self._addon_errors.pop(rec["folder"], None)
                    rec.update(status="upToDate", error=None)
                    log(f"  ✓ Addon {rec['folder']} installed.")
                except Exception as e:
                    _cat, template, values = addon_failure(e, rec.get("git"))
                    op = "update" if updating else "install"
                    log(f"  ✗ Addon {rec['folder']} ({op} failed): "
                        f"{template.format(**values)}", "err")
                    log("    " + " <- ".join(
                        f"{type(x).__name__}: {x}" for x in _error_chain(e)),
                        "dim")
                    failures.append((rec["folder"], updating, template,
                                     values))
                    rec["error"] = None
                    if not updating:
                        # Shown under the catalogue row until it installs.
                        self._addon_errors[rec["folder"]] = {
                            "error": tr(template, **values),
                            "git": rec.get("git")}

            def done():
                self._progress_clear()
                self._addons_busy = False
                self._addons_installing = False
                self._addons_verified_ts = 0.0   # make the re-verify run
                self._refresh_ready_state()      # PLAY active again
                # No row is left reading "downloading…": a failed one is put
                # back the way it was before it was asked for.
                for folder, *_ in failures:
                    status, listed = before.get(folder, (None, False))
                    rec = next((r for r in recs if r["folder"] == folder), None)
                    if rec is not None:
                        rec["status"] = status or "available"
                    if not listed:
                        self._addons_status["addons"].pop(folder, None)
                if failures:
                    self._status_error(
                        tr("Some addons failed – see the message for details"),
                        lambda f=list(failures), n=len(recs):
                            self._report_addon_failures(f, n))
                # Cache-only refresh: the install itself already resolved and
                # cached the shas — no further API requests are needed.
                self._addons_verify(remote_checks=False)
                if not self._addons_busy:        # the verify did not start
                    self._render_addons(keep_scroll=True)
                if failures:
                    self._report_addon_failures(failures, len(recs))
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _report_addon_failures(self, failures, total: int):
        """One dialog for whatever failed: the addon and the reason when one
        did, a single summary when several did. Offers the log, which has
        the detail."""
        if len(failures) == 1:
            folder, updating, template, values = failures[0]
            title = (tr("Failed to update {name}", name=folder) if updating
                     else tr("Failed to install {name}", name=folder))
            body = tr(template, **values)
            if updating:
                body += "\n\n" + tr("The installed copy was left as it was.")
        else:
            title = tr("Addons: {n} of {total} failed", n=len(failures),
                       total=total)
            lines = []
            for folder, updating, template, values in failures[:12]:
                lines.append("• " + (tr("{name} (update)", name=folder)
                                     if updating else folder)
                             + ": " + tr(template, **values))
            if len(failures) > 12:
                lines.append(tr("…and {n} more", n=len(failures) - 12))
            body = "\n".join(lines)
            if total > len(failures):
                body += "\n\n" + tr("The other {n} finished.",
                                       n=total - len(failures))
        body += "\n\n" + tr("Open the log for details?")
        self._last_addon_failure = (title, body)
        if self._ask_yes_no(title, body, icon="error"):
            self._show_logs()

    def _addon_update_all(self):
        """Every addon that can be *proved* to have a newer commit on the
        source it was installed from. Nothing else.

        The old version swept `status == "outOfDate"`, which covered four
        unrelated situations -- a newer remote, a manually installed addon, a
        locally edited one, and a catalogue preferring a different fork -- and
        replaced all four."""
        recs = [r for r in self._addons_status["addons"].values()
                if r["status"] == "updateAvailable"]
        self._addon_apply(recs)

    def _addon_remove(self, folder: str):
        if not self._ask_yes_no(
                tr("Remove addon"),
                tr("Delete {folder} and all of its files?", folder=folder)):
            return
        client = self._game_path.get().strip()
        if not client or self._addons_busy:
            return
        try:
            dirp = os.path.join(writable_addons_path(client), folder)
            if os.path.isdir(dirp):
                shutil.rmtree(dirp)
            update_config(lambda c: c.get("addons", {}).pop(folder, None))
            self._log_line(f"Removed addon {folder}\n", "dim")
        except Exception as e:
            self._log_line(f"Failed to remove addon {folder}: {e}\n", "err")
        self._addons_status["addons"].pop(folder, None)
        self._addon_errors.pop(folder, None)
        self._refresh_addons_badge()
        self._render_addons(keep_scroll=True)

    def _addon_adopt(self, rec):
        """Hand a manually installed addon over to EqUpdater, with consent.

        **This installs nothing.** It records that the user wants EqUpdater
        to look after the folder, pins the source it should track, and
        fingerprints the files exactly as they are. The addon on disk is
        untouched; what changes is that EqUpdater may now offer updates for
        it, and can tell afterwards whether those files changed.

        No commit sha is claimed unless the files prove one: a clean clone
        of that source, or an exact match with its latest commit. Whatever
        else is in the folder did not necessarily come from the repository
        the catalogue suggests, and inventing a sha would let the next run
        "update" a fork into a different fork. With no sha the planner answers
        "unable to verify", and Replace is the way out."""
        folder = rec["folder"]
        git = rec.get("suggested_git") or rec.get("git")
        if not git:
            return
        if not self._ask_yes_no(
                tr("Manage {name} with {app}?", name=folder,
                   app=branding.APP_NAME),
                tr("{name} is installed and {app} did not install it.\n\n"
                   "Managing it means {app} will track:\n    {source}\n\n"
                   "Your current files are not changed now. {app} will tell "
                   "you when that source has a newer version, and will never "
                   "replace files you have edited without asking.\n\n"
                   "Manage it?", name=folder, app=branding.APP_NAME,
                   source=git)):
            return
        self._record_addon_adoption(rec)
        self._log_line("\n%s is now managed by %s, tracking %s.\n"
                       "Its files were not changed.\n"
                       % (folder, branding.APP_NAME, git), "acct")
        client = self._game_path.get().strip()

        def work():
            try:
                self._identify_adopted_addons(client)
            except Exception as e:
                log(f"Could not identify {folder}: {e}")
            self.after(0, self._after_identify)
        threading.Thread(target=work, daemon=True).start()

    def _record_addon_adoption(self, rec: dict):
        """Write down that EqUpdater looks after this folder, tracking the
        source it came from. Installs nothing; the caller has the player's
        consent. A source other than the catalogue's stays the player's: the
        addon keeps updating from where it came from, and is not offered as
        "installed from a different source" for the next fork along."""
        folder = rec["folder"]
        git = rec.get("suggested_git") or rec.get("git")
        client = self._game_path.get().strip()
        catalog = rec.get("catalog_git")
        same = bool(catalog) and same_repo(catalog, git)
        record = new_addon_record(
            managed=True, adopted=True, git=git,
            branch=rec.get("catalog_branch") if same else None,
            ref=rec.get("catalog_ref") if same else None,
            custom_source=bool(catalog) and not same,
            content_hash=folder_hash(
                os.path.join(addons_path(client), folder)) if client else None)
        update_config(lambda c, f=folder, r=record:
                      c.setdefault("addons", {}).__setitem__(f, r))

    def _identify_addons_once(self):
        """Once a session: find the commit of addons adopted before this
        could, and of any since. Records written by an older version get
        theirs here, with no click needed."""
        if getattr(self, "_addons_identify_started", False):
            return
        client = self._game_path.get().strip()
        pending = [f for f, r in (load_config().get("addons") or {}).items()
                   if addon_is_managed(r) and not r.get("sha") and r.get("git")]
        if not client or not pending:
            return
        self._addons_identify_started = True

        def work():
            before = {f: r.get("sha") for f, r in
                      (load_config().get("addons") or {}).items()}
            try:
                self._identify_adopted_addons(client)
            except Exception as e:
                log(f"Could not identify adopted addons: {e}")
            after = {f: r.get("sha") for f, r in
                     (load_config().get("addons") or {}).items()}
            if before != after:
                self.after(0, self._after_identify)
        threading.Thread(target=work, daemon=True).start()

    def _addon_release(self, folder: str):
        """Stop managing an addon. The files stay exactly where they are.

        Releasing ownership is not uninstalling: the row moves to INSTALLED
        MANUALLY and every automatic flow stops considering it. This is the
        escape hatch for somebody who wants to look after one addon by hand
        without turning the whole updater off."""
        rec = (load_config().get("addons", {}) or {}).get(folder)
        if not rec:
            return

        def _merge(c):
            r = c.setdefault("addons", {}).setdefault(folder, {})
            r["managed"] = False
            r["installed_by"] = None
            r["released"] = True        # "Manage all" leaves this one out
        update_config(_merge)
        self._log_line("\n%s is no longer managed by %s; its files are "
                       "untouched.\n" % (folder, branding.APP_NAME), "acct")
        self._addons_verified_ts = 0.0
        self._addons_verify(force=False)

    def _addon_switch_source(self, folder: str, new_git: str):
        """Point a managed addon at a different repository, deliberately.

        Recorded as a *custom* source, which means the catalogue will not
        quietly point it somewhere else again later. The installed sha is
        dropped: it belongs to the old repository's history and comparing it
        against the new one's commits would be meaningless. The addon then
        reads "unable to verify" until it is installed from the new source
        once -- which is accurate, and better than a confident wrong answer."""
        current = (load_config().get("addons", {}) or {}).get(folder, {})
        if not self._ask_yes_no(
                tr("Switch source for {name}?", name=folder),
                tr("{name} is currently tracked from:\n    {current}\n\n"
                   "Switch to:\n    {new}\n\n"
                   "This does not change any files now. The next update for "
                   "this addon will come from the new repository, which may "
                   "be a different project with different content.\n\n"
                   "Switch?", name=folder,
                   current=current.get("git") or tr("(none)"), new=new_git)):
            return

        def _merge(c):
            r = c.setdefault("addons", {}).setdefault(folder, {})
            r["git"] = new_git
            r["branch"] = None
            r["ref"] = None
            r["sha"] = None
            r["custom_source"] = True
            r["managed"] = True
        update_config(_merge)
        self._log_line("\n%s now tracks %s.\n" % (folder, new_git), "acct")
        self._addons_verified_ts = 0.0
        self._addons_verify(force=True)

    def _all_plans(self) -> list:
        """Everything EqUpdater has an opinion about, mods and addons."""
        return list(self._mod_plans().values()) + self._addon_plans()

    def _update_all(self):
        """Update everything EqUpdater manages and can prove is behind.

        It does **not** mean "make this installation match the catalogue".
        Mods run through the normal Apply worker, which asks the planner per
        mod; when it finishes, `_update_all_chain` hands over to the addons.
        Both halves consult the same decisions the preview just showed, so
        what runs is what was agreed to."""
        if self._update_all_running or self._btn_mode == "busy" \
                or self._addons_busy or getattr(self, "_running", False):
            return                          # a second click while it runs
        out = self._game_path.get().strip()
        if not out or self._update_all_count() <= 0:
            return

        plans = self._all_plans()
        if not self._update_all_preview(plans):
            self._refresh_update_all_btn()
            return

        mods = self._mod_updates_count
        addons = self._addon_updates_count
        self._log_line("\nUpdating everything: %d mod(s), %d addon(s)...\n"
                       % (mods, addons), "acct")
        # Every hold-back, in the log, with its reason. A skip nobody is told
        # about is indistinguishable from the updater not working.
        for p in skipped_notably(plans):
            self._log_line("  \u2022 %s \u2014 %s\n"
                           % (p.component_id, p.reason), "")

        self._update_all_running = True
        if mods > 0:
            self._update_all_chain = addons > 0
            self._set_btn_busy(tr("Installing\u2026"))
            self._status_var.set(tr("Downloading mods\u2026"))
            threading.Thread(target=self._apply_mods_worker,
                             args=(out,), daemon=True).start()
        else:
            self._addon_update_all()
        self._refresh_update_all_btn()

    def _chain_addon_update_all(self):
        """The addons half of an update-all, once the mods are done. The
        chain flag stays up until the addons are busy, so the spinner does not
        blink out in between."""
        self._addon_update_all()
        self._update_all_chain = False
        self._refresh_update_all_btn()

    def _addon_plans(self) -> list:
        """The addons' decisions, taken from the last verify.

        Addon state is expensive to establish -- a commit lookup and possibly
        an ancestry call per addon -- so the verify records each decision on
        the row and this reads them back rather than asking again."""
        out = []
        for rec in self._addons_status["addons"].values():
            p = rec.get("plan")
            if p is not None:
                out.append(p)
        return out

    def _addon_force_replace(self, rec):
        """Overwrite a managed addon with the latest from its source, after
        saying plainly what will be lost.

        This is the way out of "modified locally", "local version newer" and
        "different revision" - the three states that hold an update back. It
        exists precisely so those states can be strict: a user who genuinely
        wants the remote copy has one button, and everybody else is not
        silently given it.

        The folder is copied into the backups directory first, and where it
        went is printed in the log, because "replace my changes" is only a
        reasonable thing to agree to if the changes are recoverable."""
        folder = rec["folder"]
        client = self._game_path.get().strip()
        if not client or not rec.get("git"):
            return

        why = {
            "modified": tr("Your local changes to this addon will be lost."),
            "localNewer": tr("The installed copy is NEWER than the source's. "
                             "Replacing it is a downgrade."),
            "diverged": tr("The installed copy came from different history – another branch or fork. Its contents may differ substantially."),
            "unverifiable": tr("The source has a different version, but "
                               "whether it is newer or older than yours "
                               "could not be checked."),
        }.get(rec.get("status"),
              tr("The installed copy will be replaced."))

        if not self._ask_ok_cancel(
                tr("Replace {name}?", name=folder),
                why + "\n\n"
                + tr("A copy of the current folder is saved to {backups} "
                     "first.\n\nReplace it with the latest from:\n    {source}",
                     backups=BACKUP_DIR, source=rec["git"])):
            return

        dest = backup_component(
            os.path.join(addons_path(client), folder), "addons")
        if dest:
            self._log_line("\nBacked up %s to %s\n" % (folder, dest), "acct")
        self._log_line("Replacing %s at your request "
                       "(%s).\n" % (folder, rec.get("reason") or "forced"),
                       "acct")
        # The one caller that passes force: the user has read what will be
        # lost, agreed to it, and the backup is already taken.
        self._addon_apply([rec], force=True)

    def _addon_dir_conflict_prompt(self, client: str) -> bool:
        """When Interface/AddOns and another case of it (Interface/Addons)
        both hold different copies of the same addon: ask, once per
        session, which copies to keep. The others are moved aside, never
        deleted. Returns True when the scan should wait (the player chose
        to decide later), False to go on."""
        try:
            state = gamepaths.prepare_addons_dir(client)
        except OSError as e:
            self._log_line(f"Could not check the addons folder: {e}\n", "err")
            return False
        _log_addon_dir_state(state)
        if not state.conflicts or getattr(self, "_addon_conflict_asked", False):
            return False
        self._addon_conflict_asked = True
        other = os.path.dirname(state.conflicts[0][1])
        names = "\n".join("    " + c[0] for c in state.conflicts)
        choice = self._ask_yes_no_cancel(
            tr("Two addon folders"),
            tr("This game folder has two addon folders whose names differ only "
               "in letter case:\n    {other}\n    {addons}\n\nEverything that "
               "was in only one of them is now in {addons}. These addons are "
               "in both, with different files:\n{names}\n\nYes: keep the "
               "copies from {other}.\nNo: keep the copies in {addons}.\n"
               "Cancel: decide later. Until then {app} installs, updates and "
               "removes no addons.\n\nThe copies you do not keep are moved "
               "into an AddOns-conflicts folder next to them, not deleted.",
               other=other, addons=state.path, names=names,
               app=branding.APP_NAME),
            icon="warning")
        if choice is None:
            self._log_line("Addon changes are paused until the two addon "
                           "folders are resolved (ADDONS tab, Check for "
                           "updates, asks again after a restart).\n", "err")
            return False
        try:
            aside, moved = gamepaths.resolve_conflicts(
                client, "other" if choice else "addons")
        except OSError as e:
            self._log_line(f"Could not resolve the addon folders: {e}\n", "err")
            return False
        _ADDON_CONFLICT_LOGGED[:] = []
        for entry in moved:
            self._log_line(f"Moved {entry} aside to {aside}\n", "ok")
        return False
