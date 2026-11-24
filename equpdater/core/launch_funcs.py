"""Launch helpers: problem texts, WDB removal, the client version reader and
the Auto Loot enforcement run just before the game starts.

Lifted verbatim from ``equpdater/app.py``. No Tk, no Qt.
"""

import os
import re
import shutil

from .. import branding, gamepaths, lootgate, platforms
from ..i18n import N_, tr
from ..config import APP_DATA_DIR
from . import oplock
from .logsink import log
from .tweaks import client_exe_locked, load_tweaks_config
from .addons import writable_addons_path


def remove_wdb(client_dir: str):
    """Delete the client's WDB folder (server-data cache, safe to drop)."""
    wdb = gamepaths.game_path(client_dir, "WDB")
    if not os.path.isdir(wdb):
        return
    try:
        shutil.rmtree(wdb)
        log("WDB cache cleared.", "dim")
    except Exception as e:
        log(f"Could not clear WDB: {e}", "err")


def get_client_version(out_dir: str) -> str:
    """Read version + build from fixed offsets in the client's WoW.exe."""
    exe_path = os.path.join(out_dir, "WoW.exe")
    if not os.path.exists(exe_path):
        return ""
    try:
        # Read only the two small fields, not the whole ~5 MB binary.
        with open(exe_path, "rb") as f:
            f.seek(0x00437bfc)
            build   = f.read(4).decode("utf-8", errors="replace").rstrip("\x00")
            f.seek(0x00437c04)
            version = f.read(6).decode("utf-8", errors="replace").rstrip("\x00")
    except Exception:
        return ""
    # Unexpected build, or an exe half-written mid-update, reads junk there -
    # show nothing rather than garbage. A real read looks like version "1.12.1",
    # build "5875".
    if not re.fullmatch(r"\d+(?:\.\d+)+", version) or not build.isdigit():
        return ""
    return f"{version} ({build})"


#: What is wrong with a line of the Game launcher's environment variables,
#: by platforms.parse_env_lines reason.
ENV_PROBLEMS = {
    "no_equals": N_("Line {line}: write it as NAME=value."),
    "bad_name":  N_("Line {line}: a name starts with a letter or _ and has "
                    "only letters, digits and _."),
    "nul":       N_("Line {line}: the value contains a character that "
                    "cannot be used."),
}


def env_problem_text(problems) -> str:
    """One translated line per unusable environment variable line."""
    return "\n".join(tr(ENV_PROBLEMS[reason], line=number)
                     for number, reason in problems)


#: Why a Linux launch would fail at once, by platforms.launch_problems reason.
LAUNCH_PROBLEMS = {
    "runner_missing":     N_("'{value}' was not found in PATH."),
    "runner_not_file":    N_("The launcher program does not exist or cannot "
                             "be run:\n{value}"),
    "prefix_relative":    N_("The Wine prefix is not a full path:\n{value}"),
    "prefix_not_dir":     N_("The Wine prefix is a file, not a folder:\n{value}"),
    "prefix_parent":      N_("The Wine prefix's parent folder does not "
                             "exist:\n{value}"),
    "protonpath_missing": N_("PROTONPATH does not exist:\n{value}"),
}


def launch_problem_text(problems) -> str:
    """One translated paragraph per launch problem."""
    return "\n\n".join(tr(LAUNCH_PROBLEMS[reason], value=value)
                       for reason, value in problems)


#: How long after PLAY a runner that quits with an error counts as one that
#: could not start the game, and where its output goes (Linux).
LAUNCH_WATCH_MS = 8000


LAUNCH_OUTPUT = os.path.join(APP_DATA_DIR, "last-launch.log")


def enforce_autoloot(client_dir: str) -> list:
    """Make WoW.exe say what the Auto Loot checkbox says,
    just before the game starts. OctoLauncher patches WoW.exe to its own
    setting whenever it plays, so the bytes can change behind the checkbox.
    Returns what it did, for the log. Never prevents a launch."""
    notes = []
    wanted = load_tweaks_config().get("alwaysAutoLoot")
    exe = os.path.join(client_dir, "WoW.exe")
    state = lootgate.exe_state(exe)
    target = state if wanted is None else bool(wanted)
    if state is not None and (state != target
                              or not lootgate.exe_complete(exe, target)):
        if client_exe_locked(client_dir):
            notes.append("Auto Loot: WoW.exe does not match the setting, and "
                         "it is in use so it was left as it is.")
        else:
            lootgate.set_exe_state(exe, target)
            notes.append(
                f"Auto Loot: WoW.exe did not match the setting (another "
                f"launcher, or an older EqUpdater, patched it); set to "
                f"{'on' if target else 'off'}." if state != target else
                f"Auto Loot: WoW.exe now reads Shift when you loot "
                f"(set to {'on' if target else 'off'}).")
    try:
        if lootgate.remove_gate(writable_addons_path(client_dir)):
            notes.append("Auto Loot: removed the !EqLootGate addon; "
                         "WoW.exe does its job now.")
    except Exception:
        pass
    return notes
