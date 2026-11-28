"""What the player is doing, for presence: the launcher is open, or the player is connected to a realm (and which).

Qt-free, read-only. ``GameWatcher`` looks at the real machine; ``SimulatedActivity`` (development only, never in a
packaged build) reads a file so the states that need a running client can be tested without one. They are separate
classes on purpose: the simulation can never leak into real detection.

What is detected, and how (investigated on a real client; ``docs/v3/SOCIAL_TEST_BACKEND.md``):

* **The game is open**: ``WoW.exe`` in the game folder is running (Windows locks a running image).
* **Connected to a realm**: that process has an established TCP connection to a *world server*: any non-local
  address that is not the login server (the ``realmlist`` host, port 3724). At the login screen only the login
  connection exists; at character selection and in the world a connection to the realm's world server exists.
  This is told from the operating system's connection table (read-only, no access to the game's memory).
* **Which realm**: only from a *known world-server address* (published by OctoWoW, or recorded by a tester with
  ``tools/qa/realm_probe.py --learn``). **``WTF/Config.wtf`` is not used**: on a real client it was rewritten in the
  middle of a session, 45 minutes after the realm connection began, so its ``realmName`` (the last realm chosen) and
  its file time say nothing reliable about the realm connected right now. If the address is unknown, or two known
  realms would both fit, the realm is left empty and the player simply shows as "In-game": it is never guessed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass

LAUNCHER, IN_GAME = "launcher", "in_game"
LOGIN_PORTS = (3724,)


@dataclass(frozen=True)
class Activity:
    kind: str = LAUNCHER          # launcher | in_game (connected to a realm)
    realm: str = ""               # a realm id, only while in_game and only if a known world server says so


# ── the pieces, each testable on its own ────────────────────────────────────────────────────

def game_running(game_dir: str) -> bool:
    """Is WoW.exe in this folder running? (Windows locks a running image; Linux asks the process list.)"""
    if not game_dir or not os.path.isfile(os.path.join(game_dir, "WoW.exe")):
        return False
    from .tweaks import client_exe_locked
    try:
        return bool(client_exe_locked(game_dir))
    except Exception:                                                    # noqa: BLE001 - never an error for presence
        return False


def _is_local(host: str) -> bool:
    return host.startswith(("127.", "169.254.", "0.")) or host in ("::1", "")


def world_endpoints(conns, login_ports=LOGIN_PORTS, login_hosts=frozenset()) -> list:
    """The (host, port) of the established connections that are to a world server: not local, not the login server."""
    out = []
    for host, port in conns:
        if _is_local(host) or port in login_ports and (not login_hosts or host in login_hosts) or host in login_hosts:
            continue
        if (host, port) not in out:
            out.append((host, port))
    return out


def realm_for(realms, endpoints, known) -> str:
    """The realm id the connected world server belongs to, or "" . ``known``: {"host:port": realm id}. One clear
    answer or none: two different realms, an unknown server or an unknown realm id all give ""."""
    ids = {r.id for r in realms}
    found = {known.get(f"{h}:{p}") for h, p in endpoints}
    found.discard(None)
    found &= ids
    return next(iter(found)) if len(found) == 1 else ""


def load_known(path: str) -> dict:
    """World-server addresses recorded on this computer: {"host:port": realm id}."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


# ── the operating system (read-only) ────────────────────────────────────────────────────────

def _windows_game_pids() -> list:
    import ctypes
    from ctypes import wintypes
    TH32CS_SNAPPROCESS = 0x2

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pids = []
    try:
        e = ENTRY()
        e.dwSize = ctypes.sizeof(ENTRY)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == "wow.exe":
                pids.append(e.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return pids


def _windows_tcp(pids) -> list:
    """Established IPv4 connections owned by ``pids``: [(remote host, remote port)]."""
    import ctypes
    import socket
    import struct
    from ctypes import wintypes
    iphlp = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)          # AF_INET, TCP_TABLE_OWNER_PID_ALL
    buf = ctypes.create_string_buffer(size.value)
    if iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 5, 0) != 0:
        return []
    n = struct.unpack_from("<I", buf, 0)[0]
    out = []
    for i in range(n):
        state, _la, _lp, ra, rp, pid = struct.unpack_from("<6I", buf, 4 + i * 24)
        if state == 5 and pid in pids:                                           # MIB_TCP_STATE_ESTAB
            out.append((socket.inet_ntoa(struct.pack("<I", ra)), socket.ntohs(rp & 0xFFFF)))
    return out


def _linux_tcp() -> list:
    """Established connections of processes that look like the game (Wine/UMU/Proton): read from /proc, best effort."""
    inodes = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().lower()
        except OSError:
            continue
        if b"wow.exe" not in cmd:
            continue
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
                if link.startswith("socket:["):
                    inodes.add(link[8:-1])
        except OSError:
            continue
    out = []
    try:
        for line in open("/proc/net/tcp").read().splitlines()[1:]:
            f = line.split()
            if f[3] == "01" and f[9] in inodes:
                host, port = f[2].split(":")
                ip = ".".join(str(int(host[i:i + 2], 16)) for i in (6, 4, 2, 0))
                out.append((ip, int(port, 16)))
    except OSError:
        pass
    return out


def game_connections(game_dir: str) -> list:
    """The remote (host, port) of the game's established TCP connections. [] when not running or not readable."""
    try:
        if sys.platform == "win32":
            pids = _windows_game_pids()
            return _windows_tcp(set(pids)) if pids else []
        if sys.platform.startswith("linux"):
            return _linux_tcp()
    except Exception:                                                    # noqa: BLE001 - presence must never fail the launcher
        pass
    return []


def login_hosts_of(game_dir: str) -> set:
    """The addresses of the login server named in realmlist.wtf (its connection is not a realm connection)."""
    import re
    import socket
    hosts = set()
    for rel in ("realmlist.wtf", os.path.join("Data", "enUS", "realmlist.wtf")):
        try:
            text = open(os.path.join(game_dir, rel), encoding="utf-8", errors="ignore").read(2000)
        except OSError:
            continue
        m = re.search(r"realmlist\s+\"?([^\s\"]+)", text, re.I)
        if m:
            try:
                hosts.update(socket.gethostbyname_ex(m.group(1))[2])
            except OSError:
                pass
    return hosts


class GameWatcher:
    """Reads the machine. ``running``, ``connected`` and ``realm`` can be replaced (tests); the defaults share one
    look at the connection table per read."""

    def __init__(self, game_dir, realms, running=game_running, connected=None, realm=None, known_path=None, now=time.time):
        self.game_dir, self.realms, self.running, self.now = game_dir, realms, running, now
        self.known_path = known_path
        self._endpoints: list = []
        self._login_hosts: set = set()
        self._login_at = 0.0
        self.connected = connected or self._connected
        self.realm = realm or self._realm

    def _connected(self, folder) -> bool:
        if self.now() - self._login_at > 300:                       # the login host's addresses change rarely
            self._login_hosts, self._login_at = login_hosts_of(folder), self.now()
        self._endpoints = world_endpoints(game_connections(folder), LOGIN_PORTS, self._login_hosts)
        return bool(self._endpoints)

    def _realm(self, folder, realms) -> str:
        known = load_known(self.known_path) if self.known_path else {}
        for r in realms:                                             # addresses OctoWoW publishes, if it ever does
            addr = getattr(r, "world", "")
            if addr:
                known.setdefault(addr, r.id)
        return realm_for(realms, self._endpoints, known)

    def read(self) -> Activity:
        folder = self.game_dir()
        if not self.running(folder) or not self.connected(folder):
            return Activity(LAUNCHER)                                # closed, or open at the login screen: not playing
        return Activity(IN_GAME, self.realm(folder, self.realms()))


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


def make_source(game_dir, realms, env=None, frozen=None, known_path=None):
    """The simulation if (and only if) asked for in a development run, otherwise the real watcher."""
    env = os.environ if env is None else env
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if env.get("EQU3_SIM_ACTIVITY") and not frozen:
        return SimulatedActivity(env["EQU3_SIM_ACTIVITY"], realms)
    return GameWatcher(game_dir, realms, known_path=known_path)
