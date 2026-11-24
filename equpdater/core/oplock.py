"""One writer per game folder, across processes.

Two EqUpdaters (2.x and 3.0 side by side, or two 3.0 windows) must not install
mods, patch ``WoW.exe`` and swap addon folders in the same game at the same
time. The lock is an operating-system file lock on a file in the *app data*
folder (never in the game folder), named after the game folder's path:

* released by the OS if the process dies - there is no stale lock to clean up;
* re-entrant within a process (an operation that calls another that also
  locks does not deadlock);
* waits up to ``timeout`` seconds, then raises ``GameFolderBusy`` with a
  message fit for the player, rather than hanging the app or proceeding.

Only builds that take the lock are protected by it. The released 2.1.4 does
not, so running 2.1.4 and 3.0 on one game folder at once is still unguarded;
the 3.0 beta installer warns about it (INSTALLATION.md).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager

from .. import branding

try:                                   # Windows
    import msvcrt
except ImportError:                    # POSIX
    msvcrt = None
    import fcntl


class GameFolderBusy(RuntimeError):
    pass


_local = threading.local()
_proc_locks: dict = {}
_proc_guard = threading.Lock()


def _lock_path(client_dir: str) -> str:
    key = os.path.normcase(os.path.abspath(client_dir))
    name = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
    d = os.path.join(branding.app_data_dir(), "locks")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name + ".lock")


def _try_lock(fh) -> bool:
    try:
        if msvcrt is not None:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh):
    try:
        if msvcrt is not None:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def game_folder(client_dir: str, timeout: float = 30.0, what: str = ""):
    """``with game_folder(dir): ...`` - exclusive for every EqUpdater using
    the lock, re-entrant for the calling thread."""
    path = _lock_path(client_dir)
    held = getattr(_local, "held", None)
    if held is None:
        held = _local.held = {}
    if held.get(path, 0) > 0:                       # already ours: nest
        held[path] += 1
        try:
            yield
        finally:
            held[path] -= 1
        return
    with _proc_guard:
        guard = _proc_locks.setdefault(path, threading.Lock())
    end = time.monotonic() + timeout
    # in-process exclusion first (a file lock does not exclude threads of one
    # process on every platform), then the cross-process one
    if not guard.acquire(timeout=max(0.0, timeout)):
        raise GameFolderBusy(_busy(what))
    fh = None
    try:
        fh = open(path, "a+b")
        if fh.tell() == 0 and os.path.getsize(path) == 0:
            fh.write(b"\0")
            fh.flush()
        while not _try_lock(fh):
            if time.monotonic() >= end:
                raise GameFolderBusy(_busy(what))
            time.sleep(0.1)
        held[path] = 1
        try:
            yield
        finally:
            held[path] = 0
            _unlock(fh)
    finally:
        if fh is not None:
            fh.close()
        guard.release()


def _busy(what: str) -> str:
    return ("Another EqUpdater is changing this game folder"
            + (f" ({what})" if what else "")
            + ". Wait for it to finish, then try again.")


def guarded(arg_index: int, what: str, timeout: float = 30.0):
    """Decorator: run the function holding the game folder's lock; the folder
    is the positional argument at ``arg_index``."""
    import functools

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kw):
            client = args[arg_index] if len(args) > arg_index else None
            if not client:
                return fn(*args, **kw)
            with game_folder(client, timeout, what):
                return fn(*args, **kw)
        return inner
    return wrap
