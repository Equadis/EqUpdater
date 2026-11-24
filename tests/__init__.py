"""Test-suite isolation.

The tests redirect %LOCALAPPDATA% / XDG_DATA_HOME to a temp folder, but the
app's default game folder is ``~/Games/OctoWoW``. On a machine that really has
that folder (every developer's), an app started by a test treated it as the
player's install: the one-time 1.3 migration re-patched its ``WoW.exe`` and
rewrote ``WTF/Config.wtf``. CI has no such folder, so it never showed there.

Give the whole run a throwaway home before any test module imports the app
(``DEFAULT_GAME_DIR`` is computed at import). Run via ``unittest discover``
this package is imported first; run a single module and it still is.
"""

import atexit
import os
import shutil
import tempfile

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
