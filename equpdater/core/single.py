"""One EqUpdater 3 window per data folder, Qt-free.

Two windows would each show their own idea of the same game folder. The lock is
an operating-system file lock (released by the system if the process dies), so a
crash never leaves EqUpdater refusing to start. A restart starts the new process
before the old one has quite gone, so ``acquire`` waits a few seconds.
"""

from __future__ import annotations

import os
import time

from . import oplock


class SingleInstance:
    def __init__(self, directory: str, name: str = "instance.lock"):
        self.path = os.path.join(directory, name)
        self._fh = None

    def acquire(self, wait: float = 6.0, poll: float = 0.15) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        end = time.monotonic() + wait
        while True:
            fh = open(self.path, "a+b")
            if oplock._try_lock(fh):
                self._fh = fh
                return True
            fh.close()
            if time.monotonic() >= end:
                return False
            time.sleep(poll)

    def release(self) -> None:
        if self._fh is not None:
            oplock._unlock(self._fh)
            self._fh.close()
            self._fh = None


MESSAGE = ("EqUpdater 3 is already running.\n\nTwo windows must not manage one game folder at "
           "once. Use the window that is open (check the taskbar).")


def tell_user(message: str = MESSAGE) -> None:
    """A window-less program has no console: say it where the player will see it."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "EqUpdater 3 Beta", 0x40)
            return
        except Exception:                      # noqa: BLE001
            pass
    import sys
    print(message, file=sys.stderr)
