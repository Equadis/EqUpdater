"""What the player is doing, for presence: the launcher is open, or the game is running (and on which realm).

Qt-free. ``GameWatcher`` looks at the real machine; ``SimulatedActivity`` (development only, never in a
packaged build) reads a file so the states that need a running client can be tested without one.
They are separate classes on purpose: the simulation can never leak into real detection.

**Realm detection is deliberately conservative.** The realm is reported only when the game is running AND
the game itself wrote the realm into ``WTF/Config.wtf`` (``SET realmName "..."``) after the launcher saw it
start. Nothing is inferred from anything the launcher shows or remembers: if the client has not said, the
player is simply "In-game" without a realm. **This has not been checked against a real OctoWoW client
logging into each realm** (``docs/v3/QA_REPORT.md``); a stronger signal (the realm server the client is
connected to) needs OctoWoW to publish where each realm's world server is.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass

LAUNCHER, IN_GAME = "launcher", "in_game"


@dataclass(frozen=True)
class Activity:
    kind: str = LAUNCHER          # launcher | in_game
    realm: str = ""               # a realm id, only while in_game and only if the client said so


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def game_running(game_dir: str) -> bool:
    """Is WoW.exe in this folder running? (Windows locks a running image; Linux asks the process list.)"""
    if not game_dir or not os.path.isfile(os.path.join(game_dir, "WoW.exe")):
        return False
    from .tweaks import client_exe_locked
    try:
        return bool(client_exe_locked(game_dir))
    except Exception:                                                    # noqa: BLE001 - never an error for presence
        return False


def realm_from_config(game_dir: str, realms) -> tuple:
    """(realm id or "", file mtime) from Config.wtf's ``realmName``. ``realms`` has .id and .name."""
    path = os.path.join(game_dir, "WTF", "Config.wtf")
    try:
        mtime = os.path.getmtime(path)
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read(200_000)
    except OSError:
        return "", 0.0
    m = re.search(r'^\s*SET\s+realmName\s+"([^"]{1,64})"', text, re.I | re.M)
    if not m:
        return "", mtime
    wanted = _norm(m.group(1))
    for r in realms:
        if _norm(r.name) == wanted or _norm(r.id) == wanted:
            return r.id, mtime
    return "", mtime


class GameWatcher:
    def __init__(self, game_dir, realms, running=game_running, config=realm_from_config, now=time.time):
        """``game_dir``: a function giving the folder; ``realms``: a function giving the realm list."""
        self.game_dir, self.realms, self.running, self.config, self.now = game_dir, realms, running, config, now
        self._since = None                    # when this run of the game was first seen

    def read(self) -> Activity:
        folder = self.game_dir()
        if not self.running(folder):
            self._since = None
            return Activity(LAUNCHER)
        if self._since is None:
            self._since = self.now()
        realm, mtime = self.config(folder, self.realms())
        # only a realm the game itself wrote during this run counts (2 s of file-time slack)
        return Activity(IN_GAME, realm if realm and mtime >= self._since - 2 else "")


class SimulatedActivity:
    """DEVELOPMENT ONLY. ``EQU3_SIM_ACTIVITY=<file>`` holding ``{"activity": "in_game", "realm": "nzoth"}``
    (or "launcher"). Ignored in a packaged build. Never used when the variable is unset."""

    def __init__(self, path: str, realms=None):
        self.path, self.realms = path, realms

    def read(self) -> Activity:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return Activity(LAUNCHER)
        if d.get("activity") != IN_GAME:
            return Activity(LAUNCHER)
        realm = str(d.get("realm") or "")
        ids = {r.id for r in self.realms()} if self.realms else None
        return Activity(IN_GAME, realm if realm and (ids is None or realm in ids) else "")


def make_source(game_dir, realms, env=None, frozen=None):
    """The simulation if (and only if) asked for in a development run, otherwise the real watcher."""
    env = os.environ if env is None else env
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if env.get("EQU3_SIM_ACTIVITY") and not frozen:
        return SimulatedActivity(env["EQU3_SIM_ACTIVITY"], realms)
    return GameWatcher(game_dir, realms)
