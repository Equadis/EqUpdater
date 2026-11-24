"""Adding a mod or addon EqUpdater does not list, Qt-free.

Same rules as the 2.x dialogs (``_open_custom_mod_dialog``,
``_open_custom_addon_dialog``, ``_remove_custom_mod``): the link is checked,
the latest release is read, the player picks a file, a ZIP is downloaded and
looked through, and nothing is installed until the player has confirmed the
warning. The decisions come from the lifted helpers in ``core/mods.py`` and
``core/addons.py``; this class is the dialog's state machine, which a view
drives with a link, a choice and a confirmation.
"""

from __future__ import annotations

import io
import os
import urllib.request
import zipfile

from .. import branding
from ..i18n import tr
from . import mods as M
from .addons import ADDON_GIT_HOSTS, check_custom_addon_url
from .net import ALLOWED_DOWNLOAD_HOSTS, secure_urlopen
from .ops_host import OpsHost


def asset_mod_warning(src: dict, name: str, files: list, dll: str | None) -> str:
    """What a ZIP or MPQ mod puts where, and what it loads (2.x wording)."""
    where = f"{M._custom_mod_host(src)}/{src['owner']}/{src['repo']}"

    def top(f):
        parts = f.split("/")
        depth = 3 if [p.lower() for p in parts[:2]] == ["interface", "addons"] else 1
        return "/".join(parts[:depth]) + ("/" if len(parts) > depth else "")
    tops = sorted({top(f) for f in files}, key=str.lower)
    shown = "\n".join("  " + t for t in tops[:8])
    if len(tops) > 8:
        shown += "\n  " + tr("and {count} more", count=len(tops) - 8)
    loads = (tr("It loads {dll} into the game every time it starts.", dll=dll) if dll else
             tr("It loads no .dll: the game reads its files as it would "
                "an addon's or a patch's."))
    return tr("{name} from {source} puts {count} files into the game "
              "folder:\n\n{where}\n\n{loads}\n\n"
              "A mod can change how the game behaves, and {app} does not "
              "check what it does. Only install mods you trust.\n\n"
              "Install it?", name=name, source=where, count=len(files),
              where=shown, loads=loads, app=branding.APP_NAME)


def dll_mod_warning(src: dict, dll: str) -> str:
    where = f"{M._custom_mod_host(src)}/{src['owner']}/{src['repo']}"
    return tr("{dll} from {source} will be installed to the mods folder and "
              "loaded into the game every time it starts.\n\n"
              "A mod runs code inside the game, with the same access to "
              "this computer as the game itself. {app} does not check what "
              "it does. Only install mods you trust.\n\n"
              "Install it?", dll=dll, source=where, app=branding.APP_NAME)


class CustomSourceService:
    """State of the 'add custom mod' dialog. ``status`` is (text, tone) with
    tone one of ``dim``/``ok``/``err``; ``choices`` the release's files."""

    def __init__(self, host: OpsHost, tasks, release=None, download=None):
        self.host, self.tasks = host, tasks
        self._release = release or (lambda src: M._release_latest(src, raise_errors=True))
        self._download = download or self._fetch_zip
        self.on_change = lambda: None
        self.reset()

    def reset(self):
        self.src, self.release, self.choices = None, None, []
        self.status, self.busy = ("", "dim"), False

    def _say(self, text, tone="dim"):
        self.status = (text, tone)
        self.on_change()

    # -- mods ----------------------------------------------------------------
    def link_changed(self):
        """Typing a new link forgets the previous lookup."""
        self.reset()
        self.on_change()

    def find(self, link: str) -> bool:
        if self.busy:
            return False
        try:
            src = M.parse_mod_release_link(link)
        except ValueError as e:
            self._say(tr(str(e)), "err")
            return False
        self.busy = True
        self._say(tr("Looking up the latest release…"), "dim")

        def work():
            try:
                return self._release(src), None
            except Exception as e:
                return None, str(e)

        def done(res):
            rel, err = res
            self.busy = False
            if err:
                self._say(tr("Could not read the releases: {error}", error=tr(err)), "err")
                return
            files = M.release_mod_files(rel)
            if src.get("asset"):
                files = [d for d in files if d.lower() == src["asset"].lower()]
            if not files:
                self._say(tr("The latest release has no .dll, .zip or .mpq file to install. "
                             "An addon with no releases is added in Addons: Add custom git addon."),
                          "err")
                return
            self.src, self.release, self.choices = src, rel, files
            self._say(tr("Latest release: {tag}. Choose the file to install:",
                         tag=(rel or {}).get("tag_name") or "?"), "ok")

        return bool(self.tasks.submit("custom-find", work, on_done=done))

    def _fetch_zip(self, url: str) -> list:
        req = urllib.request.Request(url, headers={"User-Agent": M.UA})
        with secure_urlopen(req, timeout=120, allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
            data = r.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return [d for _, d in M.mod_zip_plan(zf.namelist())]

    def prepare(self, name: str, done) -> bool:
        """Check the chosen file and call ``done(warning, plan)`` when the player
        can be asked, or say why not. A ZIP is read first, off the GUI thread."""
        if not self.src or not name or self.busy:
            return False
        src = self.src
        client = self.host._game_path.get().strip()
        low = name.lower()
        if low.endswith(".zip"):
            asset = next((a for a in (self.release or {}).get("assets") or []
                          if a.get("name") == name), None)
            if asset is None:
                self._say(tr("Could not read {name}: {error}", name=name, error="?"), "err")
                return False
            self.busy = True
            self._say(tr("Looking inside {name}…", name=name), "dim")

            def work():
                try:
                    return self._download(asset["browser_download_url"]), None
                except ValueError as e:
                    return None, tr(str(e))
                except Exception as e:
                    return None, tr("Could not read {name}: {error}", name=name, error=e)

            def got(res):
                files, err = res
                self.busy = False
                if err:
                    self._say(err, "err")
                    return
                self._say("", "ok")
                self._ready(name, files, M.mod_zip_dll(files), done)
            return bool(self.tasks.submit("custom-zip", work, on_done=got))
        if low.endswith(".mpq"):
            dest = f"Data/{name}"
            if client and os.path.exists(os.path.join(client, dest)):
                self._say(tr("Data already has a file named {name}.", name=name), "err")
                return False
            self._ready(name, [dest], None, done)
            return True
        problem = M.custom_mod_conflict(name, src)
        if problem:
            self._say(tr(problem), "err")
            return False
        done(dll_mod_warning(src, name), {"name": name, "files": None, "dll": name})
        return True

    def _ready(self, name, files, dll, done):
        problem = M.custom_files_conflict(files, self.src)
        if problem:
            self._say(tr(problem), "err")
            return
        done(asset_mod_warning(self.src, name, files, dll),
             {"name": name, "files": files, "dll": dll})

    def add(self, plan: dict) -> bool:
        """The player confirmed: record the mod and install it (as 2.x)."""
        h, src = self.host, self.src
        name, files, dll = plan["name"], plan["files"], plan["dll"]
        loaded = os.path.basename(dll) if dll else None
        twins = [m for m in M.MODS_REGISTRY
                 if loaded and M.mod_dll_name(m) == loaded.lower() and h._mod_is_on(m["id"])]
        source = f"{M._custom_mod_host(src)}/{src['owner']}/{src['repo']}"
        if twins and not h._confirm_replace_twins(source, loaded, twins):
            return False
        try:
            mid = (M.add_custom_asset_mod(src, name, files, dll) if files is not None
                   else M.add_custom_mod(src, name))
        except ValueError as e:
            self._say(tr(str(e)), "err")
            return False
        h._log_line(f"\nAdded custom mod {name} from {source}.\n", "acct")
        h._rebuild_mod_rows()
        if twins:
            for m in twins:
                h._mod_pending_state.setdefault(m["id"], {})["enabled"] = False
            h._mod_pending_state.setdefault(mid, {})["enabled"] = True
            if h._game_path.get().strip():
                h._apply_mods()
        elif h._game_path.get().strip():
            h._update_mod(mid)
        self.reset()
        return True

    def remove_text(self, mod_id: str):
        """(title, body) of the question before a custom mod is removed, or None."""
        mod = next((m for m in M.MODS_REGISTRY if m["id"] == mod_id), None)
        if mod is None or not mod.get("custom"):
            return None
        files = mod["source"].get("asset_pattern", "").lower().endswith((".zip", ".mpq"))
        body = (tr("The files {name} put into the game folder are deleted, and it is "
                   "removed from this list.", name=mod["name"]) if files else
                tr("{name} is deleted from the mods folder, taken out of dlls.txt and "
                   "removed from this list.", name=mod["name"]))
        return tr("Remove {name}?", name=mod["name"]), body

    def remove(self, mod_id: str) -> bool:
        h = self.host
        mod = next((m for m in M.MODS_REGISTRY if m["id"] == mod_id), None)
        if mod is None or not mod.get("custom"):
            return False
        client = h._game_path.get().strip()
        if client:
            try:
                M.uninstall_mod(mod, client)
                if mod.get("register_dll"):
                    M.remove_dll(client, mod["register_dll"])
            except OSError as e:
                h._log_line(f"Could not remove {mod['name']}: {e}\n", "err")
                return False
        M.forget_custom_mod(mod_id)
        h._mod_pending_state.pop(mod_id, None)
        h._log_line(f"\nRemoved custom mod {mod['name']}.\n", "dim")
        h._rebuild_mod_rows()
        return True

    # -- addons ---------------------------------------------------------------
    @staticmethod
    def addon_hosts() -> str:
        return tr("Allowed hosts: {hosts}", hosts=", ".join(ADDON_GIT_HOSTS))

    @staticmethod
    def check_addon(link: str):
        """(message, tone): green when the link will work, silent while half-typed."""
        _url, _folder, problem = check_custom_addon_url(link)
        if problem is None:
            return tr("Valid URL. Click Install to continue."), "ok"
        return "", "err"

    def install_addon(self, link: str):
        """Returns "" when started, else the reason it was refused."""
        url, folder, problem = check_custom_addon_url(link)
        if problem is not None:
            return tr(problem)
        self.host._log_line(f"\nInstalling custom addon {folder}…\n", "acct")
        self.host._addon_apply([{"folder": folder, "status": "available", "git": url,
                                 "branch": None, "ref": None, "toc": {},
                                 "description": None, "error": None, "custom": True}])
        return ""
