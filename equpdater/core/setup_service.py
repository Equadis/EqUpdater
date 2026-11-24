"""The "find or add your game" flow, Qt-free (see ``core/install.py``).

A view drives it: ``scan()`` lists the game folders this computer seems to
have, ``use(path)`` makes one the game folder, ``inspect_archive`` /
``extract`` put a ZIP or TAR the player already downloaded into a folder they
pick. It never downloads the game, and nothing is written by scanning.
"""

from __future__ import annotations

import threading
import time

from ..i18n import tr
from . import install as I


class SetupService:
    def __init__(self, host, tasks, change_dir, find=None):
        """``change_dir(path) -> "" or reason`` is GeneralService.change_game_dir."""
        self.host, self.tasks, self.change_dir = host, tasks, change_dir
        self._find = find or I.find_installs
        self.on_change = lambda: None
        self.candidates: list = []
        self.status = ("", "dim")
        self.scanning = self.extracting = False
        self.progress = 0.0
        self.plan = None
        self.cancel_flag = threading.Event()

    def say(self, text, tone="dim"):
        self.status = (text, tone)
        self.on_change()

    # -- find ----------------------------------------------------------------------
    def scan(self):
        if self.scanning:
            return False
        self.scanning = True
        self.say(tr("Looking for the game on this computer…"))

        def work():
            return [c.as_dict() for c in self._find()]

        def done(rows):
            self.scanning = False
            self.candidates = rows
            usable = [r for r in rows if r["usable"]]
            self.say(tr("Found {count} game folder(s).", count=len(usable)) if usable else
                     tr("No game was found. Choose the folder, or extract the archive you downloaded."),
                     "ok" if usable else "warn")

        def failed(exc):
            self.scanning = False
            self.say(str(exc), "err")
        return bool(self.tasks.submit("setup:scan", work, on_done=done, on_error=failed))

    def use(self, path: str) -> str:
        """Make ``path`` the game folder. Returns "" or the reason it was refused."""
        info = I.inspect_folder(path)
        if not info.usable:
            msg = tr("That folder does not hold the game.")
            self.say(msg, "err")
            return msg
        busy = I.folder_busy(path)
        if busy:
            self.say(tr(busy), "err")
            return tr(busy)
        err = self.change_dir(path)
        if err:
            self.say(tr(err), "err")
            return tr(err)
        self.say(tr("Using {path}", path=path), "ok")
        return ""

    # -- archives --------------------------------------------------------------------
    def inspect_archive(self, archive: str):
        """The archive's summary, or None (the reason is in ``status``)."""
        try:
            self.plan = I.plan_archive(archive)
        except I.InstallError as e:
            self.plan = None
            self.say(tr(str(e)), "err")
            return None
        if not self.plan.has_exe:
            self.say(tr("There is no WoW.exe in that archive, so it is not the game."), "err")
            return None
        self.say(tr("{files} files, {mb} MB. Choose an empty folder to put them in.",
                    files=self.plan.files, mb=self.plan.total // (1 << 20)), "ok")
        return self.plan

    def extract(self, archive: str, dest: str, on_done) -> bool:
        if self.extracting:
            return False
        self.extracting, self.progress = True, 0.0
        self.cancel_flag = flag = threading.Event()
        last = {"t": 0.0}

        def progress(done, total):
            now = time.monotonic()
            if now - last["t"] < 0.2:
                return
            last["t"] = now
            frac = done / total if total else 0.0
            self.host.after(0, lambda f=frac: self._progress(f))

        def work():
            return I.extract_archive(archive, dest, progress, flag.is_set)

        def done(path):
            self.extracting, self.progress = False, 1.0
            self.say(tr("Extracted to {path}", path=path), "ok")
            on_done(path)

        def failed(exc):
            self.extracting = False
            if isinstance(exc, I.Cancelled):
                self.say(tr("Stopped. Nothing was kept."), "warn")
            elif isinstance(exc, I.InstallError):
                self.say(tr(str(exc)), "err")
            else:
                self.say(tr("Could not extract: {error}", error=exc), "err")
        self.say(tr("Extracting…"))
        return bool(self.tasks.submit("setup:extract", work, on_done=done, on_error=failed))

    def _progress(self, frac):
        if self.extracting:
            self.progress = frac
            self.on_change()

    def cancel(self):
        self.cancel_flag.set()
