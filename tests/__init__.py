"""Test-suite isolation.

1. **Home.** The tests redirect %LOCALAPPDATA% / XDG_DATA_HOME to a temp folder,
   but the app's default game folder is ``~/Games/OctoWoW``. On a machine that
   really has that folder, an app started by a test treated it as the player's
   install: the one-time 1.3 migration re-patched its ``WoW.exe``. So the whole
   run gets a throwaway home before any test imports the app.

2. **No network.** A freshly started Tk app asks the network for the client
   manifest, both forums and news.json on its own. ``EQUPDATER_OFFLINE`` makes
   ``core.net.secure_urlopen`` fail at once instead of sending anything, so no
   test can reach the internet by accident. (A test that installs its own fake
   ``secure_urlopen`` is unaffected.)

3. **No stray workers.** Workers an app starts must not outlive the test that
   caused them: one running into a *later* test used that test's fake network
   and recorded its requests (the old "flaky" custom-mods test). Threads started
   from ``equpdater/app.py`` are recorded, and after every test the suite waits
   for them to finish and fails the test if one does not.
"""

import atexit
import os
import shutil
import sys
import tempfile
import threading
import unittest

_HOME = tempfile.mkdtemp(prefix="equ-test-home-")
for _var in ("HOME", "USERPROFILE"):
    os.environ[_var] = _HOME
os.environ.pop("HOMEDRIVE", None)
os.environ.pop("HOMEPATH", None)
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)

# The "manage what is already here" question is a modal dialog: a test that
# wants it asks for it (see tests/test_migration.py).
os.environ["EQUPDATER_NO_MIGRATION_PROMPT"] = "1"
# Likewise the one-time Defender exclusion question (tests/test_defender_prompt.py).
os.environ["EQUPDATER_NO_DEFENDER_PROMPT"] = "1"
os.environ["EQUPDATER_OFFLINE"] = "1"
os.environ["EQU3_NO_SETUP"] = "1"          # the first-run "find your game" panel stays shut

# ── worker accounting ────────────────────────────────────────────────────────
_STARTED: list = []
_real_start = threading.Thread.start


def _recording_start(self):
    try:
        origin = sys._getframe(1).f_code.co_filename.replace("\\", "/")
    except ValueError:
        origin = ""
    if origin.endswith(("equpdater/app.py", "equpdater/core/tasks.py")):
        _STARTED.append(self)
    return _real_start(self)


threading.Thread.start = _recording_start
STRAY_LIMIT_S = 15.0


def stray_workers(wait: float = STRAY_LIMIT_S) -> list:
    """Wait up to ``wait`` s for recorded workers; return those still alive."""
    import time
    end = time.monotonic() + wait
    alive = [t for t in _STARTED if t.is_alive()]
    while alive and time.monotonic() < end:
        alive[0].join(timeout=0.05)
        alive = [t for t in alive if t.is_alive()]
    _STARTED[:] = [t for t in _STARTED if t.is_alive()]
    return alive


_real_run = unittest.TestCase.run


def _run(self, result=None):
    out = _real_run(self, result)
    left = stray_workers()
    if left and result is not None:
        names = ", ".join(sorted({t.name for t in left}))
        result.addFailure(self, (AssertionError, AssertionError(
            f"worker thread(s) outlived the test: {names}"), None))
    return out


unittest.TestCase.run = _run
