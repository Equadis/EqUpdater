"""Game Diagnostics: what happened after PLAY, kept on disk, and a support ZIP.

Three things have to stay apart, because players and the people helping them
get misled when they do not:

- *starting a process* is not *the game started*: VanillaFixes exits 0 once it
  has handed over, and a wrapper exiting 0 proves only that the wrapper did;
- *the game started* is not *a clean session*: a running process says nothing
  about whether it later crashed;
- *a file is on disk* is not *it was loaded*, and neither is *it caused this*.

So an attempt is a list of observations with the time each was made, written to
disk as they happen so that the next EqUpdater can pick up where this one was
closed; the outcome is computed from that list (``classify``) and can be
recomputed when later evidence arrives. Nothing here decides whether to launch:
a failure in here is reported in the diagnostics view and PLAY goes on.

What the observations are read from, each confirmed against the real thing:

- ``Errors/<date> <time> Crash.txt``: the client's own crash report;
- ``VanillaFixes_d3d9.log`` and ``WoW_d3d9.log``: DXVK's logs, named after the
  process that loaded it;
- VanillaFixes reports its failures in a message box (no console, no log of
  its own), so while it runs without a game its window's text is read;
- on Windows, the Application log's "Application Error" entries.

Session text stays English for the reason the session log does (see i18n.py):
it is what gets sent to the people helping. Only ``OUTCOMES`` is translated."""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

from . import gamepaths, platforms
from .i18n import N_

SCHEMA = 1

#: Attempts kept, and what one attempt or all of them may use on disk.
MAX_ATTEMPTS = 40
MAX_ATTEMPT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 400

#: How often the processes are looked at while waiting, and afterwards.
POLL_S = 0.5
SLOW_POLL_S = 3.0
#: A slow start: how long WoW.exe may take before "not observed" is said. A
#: timeout says only that: the attempt stays open, and a late appearance
#: still counts, up to LATE_WINDOW_S.
OBSERVE_WINDOW_S = 120.0
LATE_WINDOW_S = 600.0
#: How long the game must have kept running to call its startup confirmed.
CONFIRM_RUNNING_S = 20.0

#: Bounds on what is read from any one log or report.
MAX_LOG_READ = 1024 * 1024
MAX_LINE = 4000
MAX_REPORT_READ = 128 * 1024
OUTPUT_TAIL_LINES = 40

GAME_EXE = "WoW.exe"
LAUNCHER_EXES = ("vanillafixes.exe",)

#: The outcome of an attempt, and its headline in the view (translated).
OUTCOMES = {
    "in_progress":    N_("Still being observed"),
    "blocked":        N_("Launch blocked before anything started"),
    "launcher_error": N_("The launcher started and exited with an error"),
    "launcher_says":  N_("The launcher started and reported a problem"),
    "no_game":        N_("The launcher exited, and WoW.exe was never observed"),
    "launcher_stuck": N_("The launcher is running, and WoW.exe was not observed"),
    "handed_off":     N_("Handed to another program; the result cannot be verified"),
    "running":        N_("WoW.exe is running"),
    "early_exit":     N_("WoW.exe closed before its startup could be confirmed"),
    "crash":          N_("WoW.exe crashed"),
    "exit_error":     N_("WoW.exe closed with an error code"),
    "exit_normal":    N_("WoW.exe closed normally"),
    "exit_unknown":   N_("WoW.exe closed; how it ended is not known"),
    "unavailable":    N_("Final outcome unavailable"),
}

#: Environment variables worth recording about a launch. Never the whole
#: environment: this is the list, and everything else is not looked at.
ENV_ALLOWLIST = (
    "WINEPREFIX", "WINEARCH", "WINEDEBUG", "WINEDLLOVERRIDES", "WINEESYNC",
    "WINEFSYNC", "PROTONPATH", "GAMEID", "STORE", "UMU_LOG", "PROTON_LOG",
    "PROTON_LOG_DIR", "PROTON_USE_WINED3D", "DXVK_LOG_LEVEL", "DXVK_LOG_PATH",
    "DXVK_HUD", "VK_ICD_FILENAMES", "STEAM_COMPAT_DATA_PATH",
    "STEAM_COMPAT_CLIENT_INSTALL_PATH",
)

#: Arguments that carry a secret in the argument after them, or after "=".
_SECRET_ARG = re.compile(
    r"(?i)^(--?)?(pass(word|wd)?|pwd|token|secret|auth|api[-_]?key|session|cookie)$")
_SECRET_WORD = (r"(?:pass(?:word|wd)?|pwd|token|secret|auth(?:orization)?|"
                r"api[-_]?key|session(?:id)?|cookie|access[-_]?key)")


# ──────────────────────────────────────────────────────────────────────────────
#  Redaction
# ──────────────────────────────────────────────────────────────────────────────

class Redactor:
    """Take a player's paths, names and secrets out of text before it leaves.

    Applied to structured fields and imported text alike, at export, and to
    command lines as they are recorded. A source that cannot be filtered this
    way is not exported at all."""

    _RULES = [
        # URLs: no credentials and no query string, whatever else they carry.
        (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1"),
        (re.compile(r"(?i)\b(https?://[^\s?#\"']+)[?#][^\s\"']*"), r"\1?<removed>"),
        # Secrets written as name=value, name: value, or Bearer xxx.
        (re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+"), r"\1 <removed>"),
        (re.compile(r"(?i)(\b" + _SECRET_WORD + r"\b[\"']?\s*[:=]\s*)[^\s,;&\"']+"),
         r"\1<removed>"),
        (re.compile(r"(?i)(--?" + _SECRET_WORD + r"\s+)\S+"), r"\1<removed>"),
        # Who the computer belongs to: profile folders, in every spelling.
        (re.compile(r"(?i)\b([a-z]:[\\/]+users[\\/]+)[^\\/\r\n:*?\"<>|]+"), r"\1<user>"),
        (re.compile(r"(?i)(\bdrive_c/users/)[^/\s]+"), r"\1<user>"),
        (re.compile(r"(?<![\w.-])(/home/)[^/\s\"']+"), r"\1<user>"),
        (re.compile(r"(?<![\w.-])(/Users/)[^/\s\"']+"), r"\1<user>"),
        (re.compile(r"(?<![\w.-])(/var/home/)[^/\s\"']+"), r"\1<user>"),
        (re.compile(r"(?i)\bz:[\\/]+home[\\/]+[^\\/\r\n]+"), r"Z:\\home\\<user>"),
        # The client's own record of who was logged in.
        (re.compile(r"(?im)^(\s*(?:user|computer|username|hostname)\s*:).*$"),
         r"\1 <removed>"),
        # Account and character names, by where they live.
        (re.compile(r"(?i)(\bWTF[\\/]+Account[\\/]+)[^\\/\r\n]+([\\/]+[^\\/\r\n]+)?([\\/]+[^\\/\r\n]+)?"),
         r"\1<account>"),
        (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    ]

    def __init__(self, client_dir: str = "", home: str | None = None):
        self._literals = []
        home = os.path.expanduser("~") if home is None else home
        # Either slash: a Windows profile path has no "/" for basename to
        # split on when this runs on Linux (a Windows log read there).
        user = re.split(r"[\\/]", (home or "").rstrip("\\/"))[-1]
        for value, name in ((client_dir, "<client>"), (home, "<home>"),
                            (user, "<user>")):
            value = (value or "").rstrip("\\/")
            if len(value) < 3:
                continue
            forms = {value, value.replace("\\", "/"), value.replace("/", "\\")}
            if value.startswith("/"):    # how Wine writes a Unix path
                forms.add("Z:" + value.replace("/", "\\"))
            for form in forms:
                pattern = re.escape(form)
                if name == "<user>":
                    # A whole name, not part of a word: a player called
                    # "runner" must not turn "runner_missing" into
                    # "<user>_missing" (the CI machine's user is "runner").
                    pattern = r"(?<![\w])" + pattern + r"(?![\w])"
                self._literals.append((re.compile(pattern, re.I), name, len(form)))
        # Longest text first: a home folder before the user name inside it.
        self._literals.sort(key=lambda p: -p[2])
        self._literals = [(pattern, name) for pattern, name, _n in self._literals]

    def text(self, value) -> str:
        out = "" if value is None else str(value)
        # A log can hold a megabyte with no line break, and a pattern that
        # looks for a word and then an @ takes the square of that. No line
        # worth reading is this long.
        if len(out) > MAX_LINE:
            out = "\n".join(line if len(line) <= MAX_LINE
                            else line[:MAX_LINE] + " <rest of line cut>"
                            for line in out.split("\n"))
        for pattern, name in self._literals:
            out = pattern.sub(lambda _m, n=name: n, out)
        for pattern, repl in self._RULES:
            out = pattern.sub(repl, out)
        return out

    def data(self, value):
        """The same over everything in a JSON-shaped value, keys included."""
        if isinstance(value, dict):
            return {self.text(k): self.data(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.data(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def sanitise_argv(argv, redactor: Redactor) -> list:
    """A command line as it may be recorded: paths made relative, secrets
    gone. An argument after a secret-named flag goes whatever it looks like."""
    out, hide_next = [], False
    for arg in argv or []:
        arg = str(arg)
        if hide_next:
            out.append("<removed>")
            hide_next = False
            continue
        if _SECRET_ARG.match(arg):
            out.append(arg)
            hide_next = True
            continue
        out.append(redactor.text(arg))
    return out


def recorded_env(env: dict | None, redactor: Redactor) -> dict:
    """The allowlisted part of a launch's environment."""
    return {k: redactor.text(env[k]) for k in ENV_ALLOWLIST if env and k in env}


# ──────────────────────────────────────────────────────────────────────────────
#  Files: bounded reads and PE headers
# ──────────────────────────────────────────────────────────────────────────────

def read_tail(path: str, limit: int = MAX_LOG_READ) -> str:
    """The end of a text file, at most ``limit`` bytes."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - limit))
        return f.read(limit).decode("utf-8", "replace")


MACHINES = {0x14c: "x86", 0x8664: "x64", 0x1c0: "arm", 0xaa64: "arm64"}


def pe_machine(path: str) -> str | None:
    """A Windows binary's architecture; None when it is not one (or is cut
    short, which is how a damaged download usually looks). Raises OSError
    when it cannot be read."""
    with open(path, "rb") as f:
        head = f.read(64)
        if len(head) < 64 or head[:2] != b"MZ":
            return None
        offset = struct.unpack_from("<I", head, 0x3c)[0]
        f.seek(offset)
        pe = f.read(6)
    if len(pe) < 6 or pe[:4] != b"PE\0\0":
        return None
    machine = struct.unpack_from("<H", pe, 4)[0]
    return MACHINES.get(machine, hex(machine))


def sha256_file(path: str, cancel=None, limit: int | None = None) -> str | None:
    """A file's SHA-256, None when cancelled or over ``limit`` bytes."""
    import hashlib
    if limit is not None and os.path.getsize(path) > limit:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                return None
            chunk = f.read(1024 * 1024)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)


# ──────────────────────────────────────────────────────────────────────────────
#  Processes
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Proc:
    """One running process. ``key`` tells a process from a later one that
    was given the same pid."""
    pid: int
    name: str = ""
    start: float | None = None
    exe: str = ""
    args: list = field(default_factory=list)
    cwd: str | None = None
    ppid: int | None = None

    @property
    def key(self) -> tuple:
        return (self.pid, self.start)


class WindowsProbe:
    """Processes by name from a Toolhelp snapshot; the path and start time
    are asked for only of the names of interest, without elevation. A process
    of a game running as administrator cannot be opened from here: it is
    listed with no path, and belongs_to_client then cannot place it."""

    PROCESS_QUERY_LIMITED = 0x1000
    SYNCHRONIZE = 0x100000
    TH32CS_SNAPPROCESS = 0x2
    TH32CS_SNAPMODULE = 0x8
    TH32CS_SNAPMODULE32 = 0x10

    def __init__(self):
        import ctypes
        self.ct = ctypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u32 = ctypes.WinDLL("user32", use_last_error=True)
        self.k32.OpenProcess.restype = ctypes.c_void_p
        self.k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        self.handles = {}

    def _entries(self):
        ct, k32 = self.ct, self.k32
        from ctypes import wintypes

        class ENTRY(ct.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ct.c_size_t),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]
        snap = k32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        if snap in (None, ct.c_void_p(-1).value):
            return
        try:
            entry = ENTRY()
            entry.dwSize = ct.sizeof(ENTRY)
            k32.Process32FirstW.argtypes = [ct.c_void_p, ct.POINTER(ENTRY)]
            k32.Process32NextW.argtypes = [ct.c_void_p, ct.POINTER(ENTRY)]
            ok = k32.Process32FirstW(snap, ct.byref(entry))
            while ok:
                yield (entry.th32ProcessID, entry.th32ParentProcessID,
                       entry.szExeFile)
                ok = k32.Process32NextW(snap, ct.byref(entry))
        finally:
            k32.CloseHandle.argtypes = [ct.c_void_p]
            k32.CloseHandle(snap)

    def _details(self, pid):
        ct, k32 = self.ct, self.k32
        from ctypes import wintypes
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = k32.OpenProcess(self.PROCESS_QUERY_LIMITED, False, pid)
        if not handle:
            return "", None
        try:
            size = wintypes.DWORD(32768)
            buf = ct.create_unicode_buffer(size.value)
            k32.QueryFullProcessImageNameW.argtypes = [
                ct.c_void_p, wintypes.DWORD, wintypes.LPWSTR,
                ct.POINTER(wintypes.DWORD)]
            exe = buf.value if k32.QueryFullProcessImageNameW(
                handle, 0, buf, ct.byref(size)) else ""
            created, _a, _b, _c = (wintypes.FILETIME(), wintypes.FILETIME(),
                                   wintypes.FILETIME(), wintypes.FILETIME())
            k32.GetProcessTimes.argtypes = [ct.c_void_p] + [
                ct.POINTER(wintypes.FILETIME)] * 4
            start = None
            if k32.GetProcessTimes(handle, ct.byref(created), ct.byref(_a),
                                   ct.byref(_b), ct.byref(_c)):
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                start = ticks / 1e7 - 11644473600.0
            return exe, start
        finally:
            k32.CloseHandle.argtypes = [ct.c_void_p]
            k32.CloseHandle(handle)

    def snapshot(self, names) -> list:
        wanted = {n.lower() for n in names}
        out = []
        for pid, ppid, name in self._entries():
            if name.lower() in wanted:
                exe, start = self._details(pid)
                out.append(Proc(pid, name, start, exe, [exe] if exe else [],
                                None, ppid))
        return out

    def hold(self, proc: Proc):
        """Keep a handle on a process found running, so its exit code can
        still be read after it is gone."""
        from ctypes import wintypes
        if proc.key in self.handles:
            return
        self.k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = self.k32.OpenProcess(
            self.PROCESS_QUERY_LIMITED | self.SYNCHRONIZE, False, proc.pid)
        if handle:
            self.handles[proc.key] = handle

    def exit_code(self, proc: Proc):
        ct = self.ct
        from ctypes import wintypes
        handle = self.handles.get(proc.key)
        if not handle:
            return None
        code = wintypes.DWORD()
        self.k32.GetExitCodeProcess.argtypes = [ct.c_void_p, ct.POINTER(wintypes.DWORD)]
        if not self.k32.GetExitCodeProcess(handle, ct.byref(code)) \
                or code.value == 259:          # STILL_ACTIVE
            return None
        return code.value

    def window_texts(self, pid: int) -> list:
        """The text in a process's visible top-level windows: how VanillaFixes
        says what went wrong (a message box; it has no console or log)."""
        ct, u32 = self.ct, self.u32
        from ctypes import wintypes
        found = []
        Callback = ct.WINFUNCTYPE(wintypes.BOOL, ct.c_void_p, ct.c_void_p)

        def text_of(hwnd):
            n = u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return ""
            buf = ct.create_unicode_buffer(min(n, 2000) + 1)
            u32.GetWindowTextW(hwnd, buf, len(buf))
            return buf.value.strip()

        def on_child(hwnd, _):
            text = text_of(hwnd)
            if text and text.replace("&", "") not in ("OK", "Cancel", "Yes", "No"):
                found.append(text)
            return True

        def on_top(hwnd, _):
            owner = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ct.byref(owner))
            if owner.value == pid and u32.IsWindowVisible(hwnd):
                kind = ct.create_unicode_buffer(32)
                u32.GetClassNameW(hwnd, kind, 32)
                # #32770 is a dialog box: a launcher's own main window, which
                # is not a message, says nothing here.
                if kind.value == "#32770":
                    found.append(text_of(hwnd))
                    u32.EnumChildWindows(hwnd, Callback(on_child), None)
            return True
        for f in (u32.GetWindowTextLengthW, u32.IsWindowVisible):
            f.argtypes = [ct.c_void_p]
        u32.GetWindowTextW.argtypes = [ct.c_void_p, wintypes.LPWSTR, ct.c_int]
        u32.GetClassNameW.argtypes = [ct.c_void_p, wintypes.LPWSTR, ct.c_int]
        u32.GetWindowThreadProcessId.argtypes = [ct.c_void_p, ct.POINTER(wintypes.DWORD)]
        u32.EnumWindows.argtypes = [Callback, ct.c_void_p]
        u32.EnumChildWindows.argtypes = [ct.c_void_p, Callback, ct.c_void_p]
        u32.EnumWindows(Callback(on_top), None)
        return found[:12]

    def modules(self, pid: int) -> list:
        """The paths of what a process has loaded, 32-bit included."""
        ct, k32 = self.ct, self.k32
        from ctypes import wintypes

        class MODULE(ct.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("th32ModuleID", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("GlblcntUsage", wintypes.DWORD),
                        ("ProccntUsage", wintypes.DWORD),
                        ("modBaseAddr", ct.c_void_p),
                        ("modBaseSize", wintypes.DWORD), ("hModule", ct.c_void_p),
                        ("szModule", wintypes.WCHAR * 256),
                        ("szExePath", wintypes.WCHAR * 260)]
        snap = k32.CreateToolhelp32Snapshot(
            self.TH32CS_SNAPMODULE | self.TH32CS_SNAPMODULE32, pid)
        if snap in (None, ct.c_void_p(-1).value):
            return []
        out = []
        try:
            entry = MODULE()
            entry.dwSize = ct.sizeof(MODULE)
            k32.Module32FirstW.argtypes = [ct.c_void_p, ct.POINTER(MODULE)]
            k32.Module32NextW.argtypes = [ct.c_void_p, ct.POINTER(MODULE)]
            ok = k32.Module32FirstW(snap, ct.byref(entry))
            while ok:
                out.append(entry.szExePath)
                ok = k32.Module32NextW(snap, ct.byref(entry))
        finally:
            k32.CloseHandle.argtypes = [ct.c_void_p]
            k32.CloseHandle(snap)
        return out


class LinuxProbe:
    """/proc. The game under Wine or Proton is whatever process names WoW.exe
    in its arguments, whatever its host binary is called; start times come
    from /proc/<pid>/stat, in clock ticks since boot (comparable with each
    other, which is all a process's identity needs)."""

    def __init__(self, proc_root: str = "/proc"):
        self.root = proc_root

    def _start(self, pid):
        try:
            with open(os.path.join(self.root, str(pid), "stat"), "rb") as f:
                stat = f.read().decode("utf-8", "replace")
            fields = stat.rsplit(")", 1)[1].split()
            return float(fields[19]), int(fields[1])
        except (OSError, IndexError, ValueError):
            return None, None

    def snapshot(self, names) -> list:
        wanted = {n.lower() for n in names}
        out = []
        for pid, args, cwd in platforms._processes(self.root):
            for arg in args:
                name = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if name in wanted:
                    start, ppid = self._start(pid)
                    out.append(Proc(pid, name, start, arg, list(args), cwd, ppid))
                    break
        return out

    def hold(self, proc):
        pass

    def exit_code(self, proc):
        return None                 # not a child of ours: Linux does not say

    def window_texts(self, pid):
        return []

    def modules(self, pid: int) -> list:
        """What Wine has mapped: PE files appear in /proc/<pid>/maps by path."""
        out = []
        try:
            with open(os.path.join(self.root, str(pid), "maps"), errors="replace") as f:
                for line in f:
                    path = line.split(None, 5)[-1].strip() if line.count(" ") >= 5 else ""
                    if path.lower().endswith((".dll", ".exe")) and path not in out:
                        out.append(path)
        except OSError:
            pass
        return out[:300]


def default_probe():
    """This platform's probe, or None where processes cannot be looked at."""
    try:
        if platforms.WINDOWS:
            return WindowsProbe()
        if platforms.LINUX:
            return LinuxProbe()
    except Exception:
        return None
    return None


def belongs_to_client(proc: Proc, client_dir: str):
    """Whether a process is this installation's: True, False, or None when
    nothing says where it runs from. Only True ever counts as "the game
    started": a None is reported as a limit, never as success."""
    def same(a, b):
        norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(p)))
        return norm(a) == norm(b)

    if platforms.WINDOWS and not proc.args and not proc.cwd:
        return None
    for arg in ([proc.exe] if proc.exe else []) + list(proc.args):
        name = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name != proc.name.lower() and proc.name:
            continue
        if platforms.WINDOWS:
            if os.path.isabs(arg):
                return same(os.path.dirname(arg), client_dir)
            continue
        unix = platforms._windows_path_to_unix(arg)
        if unix is not None:
            return same(os.path.dirname(unix), client_dir)
        if "/" in arg or "\\" in arg:
            # A C:\ path inside a Wine prefix: only the working folder can say.
            break
    if proc.cwd:
        return same(proc.cwd, client_dir)
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  The attempt store
# ──────────────────────────────────────────────────────────────────────────────

class Store:
    """Recent attempts under the app's data folder, one folder each, so two
    attempts cannot overwrite each other. Each write goes to a temporary file
    and is renamed over the last, so a crash mid-write leaves the previous
    state, and an unreadable record still lists as what it is."""

    def __init__(self, root: str):
        self.root = root
        self.last_error: str | None = None

    def attempts_dir(self) -> str:
        return os.path.join(self.root, "attempts")

    def scans_dir(self) -> str:
        return os.path.join(self.root, "scans")

    def attempt_dir(self, attempt_id: str) -> str:
        return os.path.join(self.attempts_dir(), attempt_id)

    def write_json(self, path: str, value) -> bool:
        tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(value, f, indent=1, ensure_ascii=False)
            os.replace(tmp, path)
            return True
        except OSError as e:
            self.last_error = f"Could not write {os.path.basename(path)}: {e}"
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    def ids(self) -> list:
        try:
            return sorted((n for n in os.listdir(self.attempts_dir())
                           if os.path.isdir(self.attempt_dir(n))), reverse=True)
        except OSError:
            return []

    def load(self, attempt_id: str) -> dict:
        path = os.path.join(self.attempt_dir(attempt_id), "attempt.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            raise ValueError("not an attempt record")
        except (OSError, ValueError) as e:
            return {"schema": SCHEMA, "id": attempt_id, "damaged": str(e),
                    "started_at": None, "events": [], "finished": True,
                    "outcome": "unavailable",
                    "explanation": "This attempt's record could not be read."}

    def recent(self, limit: int = MAX_ATTEMPTS) -> list:
        return [self.load(i) for i in self.ids()[:limit]]

    def prune(self, keep: str | None = None):
        """Oldest first, past the count or the total size. Never the attempt
        being written."""
        ids = self.ids()
        total = 0
        for index, attempt_id in enumerate(ids):
            folder = self.attempt_dir(attempt_id)
            size = 0
            for base, _dirs, files in os.walk(folder):
                for name in files:
                    try:
                        size += os.path.getsize(os.path.join(base, name))
                    except OSError:
                        pass
            total += size
            if attempt_id != keep and (index >= MAX_ATTEMPTS or total > MAX_TOTAL_BYTES):
                shutil.rmtree(folder, ignore_errors=True)

    def save_scan(self, scan: dict) -> str | None:
        path = os.path.join(self.scans_dir(), scan["id"] + ".json")
        if not self.write_json(path, scan):
            return None
        try:
            names = sorted(n for n in os.listdir(self.scans_dir())
                           if n.endswith(".json"))
            for old in names[:-6]:
                os.remove(os.path.join(self.scans_dir(), old))
                try:
                    os.remove(os.path.join(self.scans_dir(),
                                           old[:-5] + ".inventory.txt"))
                except OSError:
                    pass
        except OSError:
            pass
        return path

    def latest_scan(self, client_dir: str | None = None) -> dict | None:
        try:
            names = sorted((n for n in os.listdir(self.scans_dir())
                            if n.endswith(".json")), reverse=True)
        except OSError:
            return None
        for name in names:
            try:
                with open(os.path.join(self.scans_dir(), name), encoding="utf-8") as f:
                    scan = json.load(f)
            except (OSError, ValueError):
                continue
            if client_dir is None or scan.get("client_root") == client_dir:
                return scan
        return None


_SEQUENCE = itertools.count()


class Attempt:
    """One press of PLAY. Everything on it survives a failure of its own: a
    disk that cannot be written is noted in the store and the launch goes on,
    which is why no method here raises."""

    def __init__(self, store: Store, client_dir: str, version: str, platform: str,
                 diagnostic: bool = False, clock=time.monotonic, wall=time.time):
        self.store = store
        self.clock, self.wall = clock, wall
        self._t0 = clock()
        self._lock = threading.RLock()
        self.redactor = Redactor(client_dir)
        # Sorts by time, however close together: the history is read in this
        # order, and the random end keeps two EqUpdaters from colliding.
        now = wall()
        attempt_id = (datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S-")
                      + f"{int(now * 1000) % 1000:03d}{next(_SEQUENCE) % 1000:03d}-"
                      + uuid.uuid4().hex[:6])
        self.data = {
            "schema": SCHEMA, "id": attempt_id, "started_at": wall(),
            "equpdater_version": version, "platform": platform,
            "client_root": client_dir, "client_build": None,
            "diagnostic": bool(diagnostic), "route": {}, "preflight": [],
            "blocked": None, "processes": [], "baseline": [], "events": [],
            "stage": "clicked", "limits": [], "components": [],
            "crash": {"reports": [], "older_reports": [], "events": []},
            "logs": [], "outcome": "in_progress", "explanation": "",
            "finished": False, "last_saved": None,
        }
        self.event("play_clicked", diagnostic=bool(diagnostic))

    @property
    def id(self) -> str:
        return self.data["id"]

    def folder(self) -> str:
        return self.store.attempt_dir(self.id)

    def output_path(self) -> str | None:
        """A file of this attempt's own for the runner's output."""
        try:
            os.makedirs(self.folder(), exist_ok=True)
            return os.path.join(self.folder(), "runner-output.txt")
        except OSError as e:
            self.store.last_error = f"Could not create {self.folder()}: {e}"
            return None

    def elapsed(self) -> float:
        return round(self.clock() - self._t0, 3)

    def save(self):
        with self._lock:
            self.data["last_saved"] = self.wall()
            self.data["outcome"], self.data["explanation"] = classify(self.data)
            self.store.write_json(os.path.join(self.folder(), "attempt.json"),
                                  self.data)

    def event(self, kind: str, **fields):
        with self._lock:
            events = self.data["events"]
            if len(events) < MAX_EVENTS:
                events.append({"t": self.elapsed(), "at": self.wall(),
                               "kind": kind, **fields})
            self.save()

    def stage(self, name: str):
        with self._lock:
            self.data["stage"] = name

    def has(self, kind: str) -> bool:
        return any(e["kind"] == kind for e in self.data["events"])

    def preflight(self, check: str, ok: bool, detail: str = "", code=None):
        with self._lock:
            self.data["preflight"].append({
                "check": check, "ok": bool(ok),
                "detail": self.redactor.text(detail), "code": code})

    def route(self, plan, target: str, label: str):
        """What is about to be started: the executable actually selected, the
        folder, the arguments and the allowlisted settings."""
        with self._lock:
            kind = plan.runner or "none"
            argv = sanitise_argv(plan.argv, self.redactor)
            self.data["route"] = {
                "runner": kind, "target": label,
                "target_path": self.redactor.text(target),
                "cwd": self.redactor.text(plan.cwd), "argv": argv,
                "env": recorded_env(plan.env, self.redactor),
                "handoff": handoff_of(plan.argv),
            }
        self.stage("validated")

    def block(self, stage: str, detail: str, error: OSError | None = None):
        """The launch stopped here, before any process existed."""
        with self._lock:
            self.data["blocked"] = {
                "stage": stage, "detail": self.redactor.text(detail),
                "errno": getattr(error, "errno", None),
                "winerror": getattr(error, "winerror", None),
                "error": self.redactor.text(str(error)) if error else None}
            self.event("blocked", stage=stage)
        self.finish()

    def process(self, role: str, pid: int, name: str, start=None, parent=None):
        with self._lock:
            for p in self.data["processes"]:
                if p["role"] == role and p["pid"] == pid:
                    p["start"] = start if start is not None else p["start"]
                    self.save()
                    return
            self.data["processes"].append({
                "role": role, "pid": pid, "name": name, "start": start,
                "parent": parent, "exit_code": None, "exited_after": None})
            self.save()

    def process_exit(self, role: str, code, after: float | None):
        with self._lock:
            for p in reversed(self.data["processes"]):
                if p["role"] == role and p["exited_after"] is None:
                    p.update(exit_code=code, exited_after=after)
                    break

    def limit(self, text: str):
        with self._lock:
            if text not in self.data["limits"]:
                self.data["limits"].append(text)

    def finish(self):
        with self._lock:
            self.data["finished"] = True
            self.save()
        self.store.prune(keep=self.id)

    @classmethod
    def resume(cls, store: Store, data: dict, clock=time.monotonic, wall=time.time):
        """An attempt read back from disk, its clock carried on from when it
        began, so later events still read as seconds after Play."""
        self = cls.__new__(cls)
        self.store, self.clock, self.wall = store, clock, wall
        self._t0 = clock() - max(0.0, wall() - (data.get("started_at") or wall()))
        self._lock = threading.RLock()
        self.redactor = Redactor(data.get("client_root") or "")
        self.data = data
        return self


class NullAttempt:
    """Stands in when an attempt could not even be created: PLAY goes on."""

    data = {"route": {}}
    id = ""

    def __getattr__(self, _name):
        return lambda *a, **k: None


def handoff_of(argv) -> str | None:
    """The program a command hands the game to when it is not the game's
    parent. Only Steam today: `steam -applaunch`, or a steam:// address."""
    for arg in argv or []:
        low = str(arg).lower()
        name = low.replace("\\", "/").rsplit("/", 1)[-1]
        if low.startswith("steam://") or name in ("steam", "steam.exe", "steam.sh"):
            return "Steam"
    return None


def diagnostic_env(runner: str, folder: str) -> dict:
    """Extra logging for one diagnostic launch, in the runner's own terms,
    only ever in that launch's environment. DXVK's lines go for any runner;
    Wine's and Proton's only where they are in use. Output goes to the
    attempt's folder."""
    env = {"DXVK_LOG_LEVEL": "info"}
    if runner in ("windows",):
        return env
    env.update({"DXVK_LOG_PATH": folder,
                "WINEDEBUG": "-all,err+all,+loaddll,+seh",
                "PROTON_LOG": "1", "PROTON_LOG_DIR": folder, "UMU_LOG": "1"})
    return env


STEAM_LOG_HELP = (
    "Steam starts the game itself, so EqUpdater cannot add logging to it. To "
    "get a Proton log: in Steam, open the game's Properties and set Launch "
    "Options to  PROTON_LOG=1 %command%  then play once. The log is "
    "steam-<appid>.log in your home folder. Remove the option afterwards, and "
    "attach the log to your report yourself.")


# ──────────────────────────────────────────────────────────────────────────────
#  Launch checks and components
# ──────────────────────────────────────────────────────────────────────────────

def preflight(attempt: Attempt, client_dir: str, exe: str, plan) -> None:
    """Record, cheaply, what is known before the process is created. A stat
    and a few header bytes: this runs on every PLAY, on the window's thread.
    Records only -- the launch decides for itself, as it always did."""
    if not client_dir or not os.path.isdir(client_dir):
        attempt.preflight("client_folder", False, f"not a folder: {client_dir!r}")
        return
    attempt.preflight("client_folder", True, client_dir)
    try:
        machine = pe_machine(exe)
        if machine is None:
            attempt.preflight("executable", False,
                              f"{os.path.basename(exe)} is not a valid Windows "
                              f"program (a damaged or partial file)")
        else:
            attempt.preflight("executable", True,
                              f"{os.path.basename(exe)} ({machine})")
    except OSError as e:
        attempt.preflight("executable", False,
                          f"{os.path.basename(exe)} cannot be read: {e}",
                          getattr(e, "winerror", None) or e.errno)
    cwd = getattr(plan, "cwd", None) or client_dir
    if os.path.isdir(cwd):
        if os.access(cwd, os.R_OK | os.X_OK):
            attempt.preflight("working_folder", True, cwd)
        else:
            attempt.preflight("working_folder", False, f"cannot be entered: {cwd}")
    else:
        attempt.preflight("working_folder", False, f"does not exist: {cwd}")
    for arg in getattr(plan, "argv", None) or []:
        left = re.search(r"\{[a-zA-Z_]+\}|%command%|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?",
                         str(arg))
        if left:
            attempt.preflight("arguments", False,
                              f"unresolved in the launch command: {left.group(0)}")


def launch_components(client_dir: str, target: str, hash_limit=64 * 1024 * 1024) -> list:
    """Each launch-related file: where it is, whether it can be read, its
    architecture and checksum, and how the chosen route uses it. *On disk* and
    *expected by this route* are stated separately: *loaded* comes later."""
    target_name = os.path.basename(target or "").lower()
    via_vf = target_name == "vanillafixes.exe"
    rows = [("VanillaFixes.exe", via_vf), ("VfPatcher.dll", via_vf),
            ("WoW.exe", True), ("dlls.txt", via_vf), ("d3d9.dll", False),
            ("dxvk.conf", False), ("dxgi.dll", False),
            ("mods/OctoLogin.dll", False), ("ReShade.ini", False)]
    names = dict((r[0].lower(), r[1]) for r in rows)
    entries = []
    try:
        with open(os.path.join(client_dir, "dlls.txt"), errors="replace") as f:
            for line in f:
                entry = line.strip().replace("\\", "/")
                if entry and not entry.startswith(("#", ";")):
                    entries.append(entry)
                    if entry.lower() not in names:
                        rows.append((entry, via_vf))
                        names[entry.lower()] = via_vf
    except OSError:
        pass
    listed = {e.lower() for e in entries}
    out = []
    for rel, expected in rows:
        path = os.path.join(client_dir, *rel.split("/"))
        row = {"file": rel, "expected_by_route": bool(expected),
               "listed_in_dlls_txt": rel.lower() in listed, "present": False}
        if not os.path.exists(path):
            if expected or row["listed_in_dlls_txt"]:
                out.append(row)
            continue
        row["present"] = True
        try:
            row["size"] = os.path.getsize(path)
            row["modified"] = os.path.getmtime(path)
            if rel.lower().endswith((".exe", ".dll")):
                row["machine"] = pe_machine(path)
            row["sha256"] = sha256_file(path, limit=hash_limit)
            row["readable"] = True
        except OSError as e:
            row["readable"] = False
            row["error"] = str(e)
        out.append(row)
    return out


def component_findings(components: list, target: str) -> list:
    """What the component list establishes on its own. Each requirement here
    is one VanillaFixes itself checks for (the messages are in its binary) or
    one the 32-bit game imposes."""
    via_vf = os.path.basename(target or "").lower() == "vanillafixes.exe"
    out = []
    by_name = {c["file"].lower(): c for c in components}
    for key, why in (("vfpatcher.dll", "VanillaFixes needs it beside itself and "
                      "stops with 'VfPatcher.dll not found' otherwise"),
                     ("wow.exe", "VanillaFixes needs it beside itself and stops "
                      "with 'WoW.exe not found' otherwise")):
        c = by_name.get(key)
        if via_vf and c and not c["present"]:
            out.append(f"{c['file']} is missing: {why}.")
    for c in components:
        name = c["file"]
        if not c["present"]:
            if c.get("listed_in_dlls_txt"):
                out.append(f"dlls.txt lists {name}, which is not there, so "
                           f"VanillaFixes cannot load it.")
            continue
        if c.get("readable") is False:
            out.append(f"{name} cannot be read: {c.get('error')}.")
        elif name.lower().endswith((".exe", ".dll")):
            machine = c.get("machine")
            if machine is None:
                out.append(f"{name} is not a valid Windows program (a damaged "
                           f"or partial file). Starting one gives Windows "
                           f"error 193.")
            elif machine != "x86" and (c.get("expected_by_route")
                                       or c.get("listed_in_dlls_txt")):
                out.append(f"{name} is {machine}; the game is 32-bit (x86) and "
                           f"cannot load it.")
    return out


# ──────────────────────────────────────────────────────────────────────────────
#  Crash evidence
# ──────────────────────────────────────────────────────────────────────────────

_REPORT_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2}) Crash\.txt$", re.I)
_REPORT_FIELDS = {
    "exe": re.compile(r"(?im)^Exe:\s*(.+?)\s*$"),
    "error": re.compile(r"(?im)^(ERROR\s+#.+?)\s*$"),
    "exception": re.compile(r"(?im)^Exception:\s*(0x[0-9a-f]+)\s*\(([^)]*)\)\s*at\s*([0-9a-f:]+)"),
    "build": re.compile(r"(?im)^WoWBuild:\s*(\d+)"),
    "frame": re.compile(r"(?im)^[0-9a-f]{8}\s+[0-9a-f]{8}\s+([0-9a-f]{4}:[0-9a-f]{8})\s+(.+?)\s*$"),
}


def parse_crash_report(path: str) -> dict | None:
    """The fields that matter from one report, or None when it cannot be read.
    Damaged content yields fewer fields, not an exception."""
    try:
        text = read_tail(path, MAX_REPORT_READ)
    except OSError:
        return None
    out = {"file": os.path.basename(path)}
    for key, pattern in _REPORT_FIELDS.items():
        m = pattern.search(text)
        if not m:
            continue
        if key == "exception":
            out.update(exception_code=m.group(1), exception=m.group(2),
                       address=m.group(3))
        elif key == "frame":
            out.update(module=m.group(2).replace("\\", "/").rsplit("/", 1)[-1],
                       offset=m.group(1))
        else:
            out[key] = m.group(1)
    m = _REPORT_NAME.search(os.path.basename(path))
    if m:
        try:
            out["reported_at"] = datetime.strptime(
                f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}",
                "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            pass
    return out


def crash_signature(report: dict) -> str:
    """What groups reports of the same crash: build, code, module, offset."""
    return "|".join(str(report.get(k) or "?") for k in
                    ("build", "exception_code", "module", "offset"))


def _exe_is_client(exe: str, client_dir: str) -> bool:
    """A report's Exe line against this client, allowing for how Wine writes
    the path (Z:\\home\\... or C:\\ inside a prefix, then only the name)."""
    if not exe:
        return False
    low = exe.replace("\\", "/").lower()
    if not low.endswith("/wow.exe"):
        return False
    where = low.rsplit("/", 1)[0]
    folder = os.path.normpath(client_dir).replace("\\", "/").lower().rstrip("/")
    return (where == folder or where == "z:" + folder
            or (low.startswith("c:/") and not platforms.WINDOWS))


def find_crash_reports(client_dir: str, since: float, until: float,
                       slack: float = 3.0) -> tuple:
    """(reports matching the time window and this client, older or unmatched
    ones). Only the first can support a conclusion. The second is listed
    labelled, because an old report is exactly the thing that misleads."""
    errors = gamepaths.game_path(client_dir, "Errors")
    matched, other = [], []
    try:
        names = [n for n in os.listdir(errors) if _REPORT_NAME.search(n)]
    except OSError:
        return [], []
    for name in sorted(names, reverse=True)[:60]:
        path = os.path.join(errors, name)
        try:
            when = os.path.getmtime(path)
        except OSError:
            continue
        report = parse_crash_report(path)
        if report is None:
            other.append({"file": name, "unreadable": True})
            continue
        report["modified"] = when
        report["signature"] = crash_signature(report)
        ours = _exe_is_client(report.get("exe", ""), client_dir)
        if ours and since - slack <= when <= until + slack:
            matched.append(report)
        else:
            report["why_excluded"] = ("another installation" if not ours
                                      else "outside this attempt's time")
            other.append(report)
    return matched, other[:5]


def _wevtutil(query: str, count: int = 40) -> str:
    return subprocess.run(
        ["wevtutil", "qe", "Application", f"/q:{query}", "/f:xml",
         f"/c:{count}", "/rd:true"],
        capture_output=True, text=True, timeout=15, errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout


def windows_events(client_dir: str, since: float, until: float, pids=(),
                   run=_wevtutil) -> list:
    """The Application log's fault entries for this client's game or launcher
    in this time window: fields only, never the raw log. An unavailable log is
    an empty answer. ``run`` is what asks the log."""
    from xml.etree import ElementTree as ET
    span_ms = int(max(0, time.time() - since + 60) * 1000)
    query = ("*[System[(Provider[@Name='Application Error'] or "
             "Provider[@Name='Application Hang']) and "
             f"TimeCreated[timediff(@SystemTime) <= {span_ms}]]]")
    try:
        xml = run(query)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for chunk in re.findall(r"<Event\b.*?</Event>", xml or "", re.S):
        chunk = re.sub(r"\sxmlns='[^']*'", "", chunk)
        chunk = re.sub(r'\sxmlns="[^"]*"', "", chunk)
        try:
            root = ET.fromstring(chunk)
        except ET.ParseError:
            continue
        created = root.find("System/TimeCreated")
        provider = root.find("System/Provider")
        when = None
        if created is not None:
            stamp = re.sub(r"\.\d+", "", created.get("SystemTime", "")).replace("Z", "+00:00")
            try:
                when = datetime.fromisoformat(stamp).timestamp()
            except ValueError:
                pass
        if when is None or not since - 3 <= when <= until + 3:
            continue
        data = [d.text or "" for d in root.findall("EventData/Data")]
        if len(data) < 11:
            continue
        app_name, app_path = data[0], data[10]
        if app_name.lower() not in ("wow.exe", "vanillafixes.exe"):
            continue
        if not _same_folder_path(app_path, client_dir):
            continue
        try:
            pid = int(data[8], 16)
        except ValueError:
            pid = None
        if pids and pid is not None and pid not in pids:
            continue
        out.append({"at": when, "provider": provider.get("Name") if provider is not None else "",
                    "application": app_name, "app_version": data[1],
                    "module": data[3], "module_version": data[4],
                    "exception_code": _hex_code(data[6]), "offset": data[7],
                    "pid": pid})
    return out


def _hex_code(value: str) -> str:
    value = (value or "").strip()
    return value if value.lower().startswith("0x") else "0x" + value


def _same_folder_path(path: str, client_dir: str) -> bool:
    folder = os.path.dirname(path.replace("\\", "/")).lower().rstrip("/")
    return folder == os.path.normpath(client_dir).replace("\\", "/").lower().rstrip("/")


#: Logs of known name and place, gathered when written during the attempt.
KNOWN_LOGS = ("VanillaFixes_d3d9.log", "WoW_d3d9.log", "ReShade.log",
              "mods/OctoLogin.log")


def fresh_logs(client_dir: str, since: float, slack: float = 3.0) -> list:
    """Known logs written since the attempt began: a reference, with the file
    read only at export."""
    out = []
    for rel in KNOWN_LOGS:
        path = os.path.join(client_dir, *rel.split("/"))
        try:
            when = os.path.getmtime(path)
        except OSError:
            continue
        if when >= since - slack:
            out.append({"file": rel, "modified": when,
                        "size": os.path.getsize(path)})
    return out


def collect_evidence(attempt_data: dict, until: float | None = None,
                     events_fn=None) -> None:
    """Look for what a finished (or abandoned) game left behind, and record it
    on the attempt. Safe to repeat: later evidence replaces earlier."""
    client = attempt_data.get("client_root") or ""
    since = attempt_data.get("started_at")
    if not client or since is None:
        return
    until = until or time.time()
    ended = _first(attempt_data, "game_exited")
    if ended and ended.get("at"):
        until = min(until, ended["at"] + 60)
    matched, older = find_crash_reports(client, since, until)
    if matched and _first(attempt_data, "already_running"):
        # This client was running before PLAY: its report could be either's.
        for report in matched:
            report["why_excluded"] = "a game from this folder was already running"
        older = matched + older
        matched = []
    crash = attempt_data.setdefault("crash", {})
    crash["reports"], crash["older_reports"] = matched, older
    if platforms.WINDOWS or events_fn is not None:
        pids = [p["pid"] for p in attempt_data.get("processes", [])
                if p.get("pid") and p.get("role") == "game"]
        ask = events_fn or windows_events
        try:
            crash["events"] = ask(client, since, until, pids)
        except Exception as e:
            crash["events"] = []
            attempt_data.setdefault("limits", []).append(
                f"The Windows event log could not be read: {e}")
    attempt_data["logs"] = fresh_logs(client, since)


# ──────────────────────────────────────────────────────────────────────────────
#  What happened
# ──────────────────────────────────────────────────────────────────────────────

def _events(data: dict, kind: str) -> list:
    return [e for e in data.get("events", []) if e.get("kind") == kind]


def _first(data: dict, kind: str):
    found = _events(data, kind)
    return found[0] if found else None


def _secs(value) -> str:
    return f"{value:.1f} seconds"


def _code(value) -> str:
    """An exit code as people look them up: Windows' NTSTATUS codes in hex."""
    if isinstance(value, int) and (value > 0xFFFF or value < 0):
        return f"0x{value & 0xFFFFFFFF:08X}"
    return str(value)


def classify(data: dict) -> tuple:
    """(outcome, explanation) from what was observed: the only place one is
    decided. Says what was seen, and goes no further than that supports."""
    if data.get("damaged"):
        return "unavailable", data.get("explanation", "")
    blocked = data.get("blocked")
    if blocked:
        text = blocked.get("detail") or ""
        if blocked.get("winerror") or blocked.get("errno"):
            text += (f" (Windows error {blocked['winerror']})" if blocked.get("winerror")
                     else f" (error {blocked['errno']})")
        return "blocked", f"Stopped at '{blocked.get('stage')}'. {text}".strip()

    route = data.get("route") or {}
    launcher_name = route.get("target") or "the launcher"
    runner = route.get("runner") or ""
    if runner not in ("windows", "", "none"):
        launcher_name = f"{launcher_name} through {runner}"
    crash = data.get("crash") or {}
    reports, faults = crash.get("reports") or [], crash.get("events") or []
    seen = _first(data, "game_observed")
    gone = _first(data, "game_exited")
    launcher_exit = _first(data, "launcher_exited")
    timed_out = _first(data, "not_observed_in_window")
    dialog = None
    for e in _events(data, "launcher_message"):
        dialog = e
    lost = _first(data, "observation_lost")

    if seen:
        handoff = ""
        if launcher_exit and launcher_exit.get("code") in (0, None):
            handoff = (f"{launcher_name} exited normally. ")
        started = f"{handoff}WoW.exe was observed {_secs(seen['t'])} after Play."
        if reports or faults:
            first = reports[0] if reports else faults[0]
            bits = [first.get("exception_code"), first.get("exception")]
            where = first.get("module")
            if where:
                bits.append(f"in {where}")
            lived = f" after {_secs(gone['ran_for'])}" if gone and gone.get("ran_for") else ""
            source = "the client's crash report" if reports else "the Windows event log"
            return "crash", (f"{started} It then crashed{lived}: "
                             + " ".join(str(b) for b in bits if b)
                             + f". From {source}.")
        if gone:
            code, ran = gone.get("code"), gone.get("ran_for")
            ran_text = f" after {_secs(ran)}" if ran is not None else ""
            if ran is not None and ran < CONFIRM_RUNNING_S and code in (0, None):
                return "early_exit", (
                    f"{started} It closed{ran_text}, before startup could be "
                    f"confirmed. No crash report or fault was found for it. An "
                    f"early exit can also be intentional.")
            if code == 0:
                return "exit_normal", f"{started} It closed normally{ran_text} (exit code 0)."
            if code is not None:
                return "exit_error", (f"{started} It closed{ran_text} with exit "
                                      f"code {_code(code)}. No crash report was "
                                      f"found.")
            return "exit_unknown", (f"{started} It is no longer running{ran_text}. "
                                    f"Its exit code could not be read, and no crash "
                                    f"report or fault was found.")
        if lost:
            return "unavailable", (f"{started} EqUpdater stopped watching (it was "
                                   f"closed) and the game is gone now. No crash "
                                   f"report or fault was found for this attempt.")
        if _first(data, "game_confirmed"):
            return "running", f"{started} It was still running when last checked."
        return "running", f"{started} Startup is not confirmed yet."

    if launcher_exit and launcher_exit.get("code") not in (0, None):
        ran = launcher_exit.get("t")
        text = (f"{launcher_name} exited with code {_code(launcher_exit['code'])} "
                f"after {_secs(ran)}. WoW.exe was not observed.")
        tail = (launcher_exit.get("output_tail") or "").strip()
        if dialog:
            text += f" It reported: {dialog['text']}"
        elif tail:
            text += f" Its last output: {tail.splitlines()[-1][:300]}"
        return "launcher_error", text
    if dialog:
        return "launcher_says", (f"{launcher_name} started, and WoW.exe was not "
                                 f"observed. It reported: {dialog['text']}")
    if route.get("handoff"):
        return "handed_off", (
            f"The launch request was handed to {route['handoff']}. EqUpdater "
            f"could not verify whether {route['handoff']} started WoW.exe. "
            f"Include a Proton log for this attempt.")
    if lost:
        return "unavailable", ("EqUpdater stopped watching before WoW.exe was "
                               "observed, and nothing found since says what "
                               "happened.")
    if launcher_exit and timed_out:
        return "no_game", (
            f"{launcher_name} exited after {_secs(launcher_exit['t'])} with code "
            f"{launcher_exit.get('code')}, and WoW.exe was not observed within "
            f"{OBSERVE_WINDOW_S:.0f} seconds. An exit of 0 says only that "
            f"{launcher_name} ended cleanly.")
    if timed_out:
        return "launcher_stuck", (
            f"{launcher_name} is running, and WoW.exe was not observed within "
            f"{OBSERVE_WINDOW_S:.0f} seconds.")
    if launcher_exit:
        return "in_progress", (f"{launcher_name} exited with code "
                               f"{launcher_exit.get('code')}. Waiting to observe WoW.exe.")
    if data.get("finished") and not _first(data, "spawned"):
        return "unavailable", "This attempt ended before a process was started."
    return "in_progress", "Waiting to observe WoW.exe."


class Observer:
    """Follows one attempt on a thread of its own until the game has gone, or
    the window for it has. ``step`` is one look; everything is on disk after
    each change, so nothing is lost if EqUpdater is closed."""

    def __init__(self, attempt: Attempt, probe, proc=None, role: str = "launcher",
                 launcher_name: str = "", output: str | None = None,
                 evidence_fn=collect_evidence, on_change=None, output_copy=None):
        self.a, self.probe, self.proc = attempt, probe, proc
        self.output_copy = output_copy
        self.role = role
        self.name = launcher_name
        self.output = output
        self.client = attempt.data.get("client_root") or ""
        self.baseline = {tuple(k) for k in attempt.data.get("baseline", [])}
        self.evidence_fn = evidence_fn
        self.on_change = on_change
        self.game: Proc | None = None
        self.game_seen_at = None
        self.launcher_done = proc is None
        self.done = False
        self._stop = threading.Event()
        self._confirmed = False
        self._said = set()
        self._ambiguous_noted = False

    # The launcher ------------------------------------------------------------

    def _output_tail(self) -> str:
        if not self.output:
            return ""
        try:
            lines = [l.rstrip() for l in read_tail(self.output).splitlines() if l.strip()]
        except OSError:
            return ""
        return self.a.redactor.text("\n".join(lines[-OUTPUT_TAIL_LINES:]))

    def _look_at_launcher(self):
        if self.launcher_done or self.proc is None:
            return
        code = self.proc.poll()
        if code is None:
            return
        self.launcher_done = True
        at = self.a.elapsed()
        tail = self._output_tail()
        role = self.role
        if self.output and self.output_copy:
            try:
                shutil.copyfile(self.output, self.output_copy)
            except OSError:
                pass
        self.a.process_exit(role, code, at)
        if role == "game":
            self._game_gone(code)
            return
        self.a.event("launcher_exited", code=code, name=self.name,
                     output_tail=tail)
        self._changed()

    def _look_for_dialog(self):
        """VanillaFixes has no console: a message box is its only voice."""
        if (self.proc is None or self.launcher_done or self.game is not None
                or self.a.elapsed() < 1.0):
            return
        try:
            texts = self.probe.window_texts(self.proc.pid)
        except Exception:
            return
        # The title alone is not a message: a window with text under it is.
        if len(texts) < 2:
            return
        message = " ".join(texts[1:])[:600]
        if message not in self._said:
            self._said.add(message)
            self.a.event("launcher_message", title=texts[0], text=message)
            self._changed()

    # The game ----------------------------------------------------------------

    def _look_for_game(self, snapshot):
        fresh = []
        for p in snapshot:
            if p.name.lower() != GAME_EXE.lower() or p.key in self.baseline:
                continue
            where = belongs_to_client(p, self.client)
            if where is True:
                fresh.append(p)
            elif where is None and not self._ambiguous_noted:
                self._ambiguous_noted = True
                self.a.limit("A WoW.exe is running that cannot be placed in any "
                             "folder (run as administrator, or a path inside a "
                             "Wine prefix), so it was not counted as this attempt.")
                self.a.save()
        if not fresh:
            return
        self.game = fresh[0]
        self.probe.hold(self.game)
        self.game_seen_at = self.a.elapsed()
        self.a.process("game", self.game.pid, self.game.name, self.game.start,
                       self.game.ppid)
        self.a.stage("game_observed")
        self.a.event("game_observed", pid=self.game.pid)
        self._changed()

    def _game_gone(self, code=None):
        if code is None and self.game is not None:
            code = self.probe.exit_code(self.game)
        ran = (self.a.elapsed() - self.game_seen_at
               if self.game_seen_at is not None else self.a.elapsed())
        if self.game is None:
            # Started directly: the handle is the game, and nothing else saw it.
            self.a.event("game_observed", pid=self.proc.pid, via="process handle")
        self.a.process_exit("game", code, self.a.elapsed())
        self.a.stage("game_exited")
        self._evidence()
        self.a.event("game_exited", code=code, ran_for=round(ran, 1))
        self._finish()

    def _evidence(self):
        try:
            self.evidence_fn(self.a.data, until=self.a.wall())
        except Exception as e:
            self.a.limit(f"Collecting crash evidence failed: {e}")

    def _modules(self):
        """Diagnostic launches: what the game has actually loaded. Observed,
        which is stronger than on disk and weaker than cause."""
        if not self.a.data.get("diagnostic") or self.game is None:
            return
        try:
            paths = self.probe.modules(self.game.pid)
        except Exception:
            return
        norm = os.path.normcase(os.path.normpath(self.client))
        loaded = sorted({self.a.redactor.text(p) for p in paths
                         if os.path.normcase(os.path.normpath(p)).startswith(norm)})
        self.a.data["loaded_modules"] = loaded[:200]
        self.a.save()

    # One look ------------------------------------------------------------------

    def step(self):
        if self.done:
            return
        self._look_at_launcher()
        if self.done:
            return
        try:
            snapshot = self.probe.snapshot([GAME_EXE]) if self.probe else []
        except Exception as e:
            snapshot = []
            self.a.limit(f"Processes could not be listed: {e}")
        now = self.a.elapsed()
        if self.game is None:
            self._look_for_game(snapshot)
        if self.game is None:
            self._look_for_dialog()
            if now >= OBSERVE_WINDOW_S and not self.a.has("not_observed_in_window"):
                self.a.event("not_observed_in_window", window=OBSERVE_WINDOW_S)
                self._evidence()
                self._changed()
            if now >= LATE_WINDOW_S:
                self.a.event("observation_ended")
                self._finish()
            return
        if not any(p.key == self.game.key for p in snapshot):
            self._game_gone()
            return
        if not self._confirmed and now - self.game_seen_at >= CONFIRM_RUNNING_S:
            self._confirmed = True
            self.a.stage("game_confirmed")
            self._modules()
            self.a.event("game_confirmed")
            self._changed()

    def _changed(self):
        if self.on_change:
            try:
                self.on_change(self.a)
            except Exception:
                pass

    def _finish(self):
        self.done = True
        self.a.finish()
        self._changed()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self.a.data["components"] = launch_components(
                self.client, self.name or GAME_EXE)
            self.a.save()
        except Exception as e:
            self.a.limit(f"Reading the launch components failed: {e}")
        while not self.done and not self._stop.is_set():
            self.step()
            interval = SLOW_POLL_S if (self.game is not None and self._confirmed) \
                or self.a.elapsed() > OBSERVE_WINDOW_S else POLL_S
            self._stop.wait(interval)

    def start(self):
        thread = threading.Thread(target=self.run, daemon=True,
                                  name=f"diagnostics-{self.a.id}")
        thread.start()
        return thread


def begin_baseline(attempt: Attempt, probe) -> list:
    """Which game processes were already here when PLAY was pressed: none of
    them can be this attempt's, whatever they do next."""
    if probe is None:
        attempt.limit("Processes cannot be observed on this system.")
        attempt.save()
        return []
    client = attempt.data.get("client_root") or ""
    try:
        running = probe.snapshot([GAME_EXE])
    except Exception as e:
        attempt.limit(f"Processes could not be listed: {e}")
        attempt.save()
        return []
    attempt.data["baseline"] = [list(p.key) for p in running]
    here = [p for p in running if belongs_to_client(p, client) is True]
    if here:
        attempt.event("already_running", pids=[p.pid for p in here])
    elif running:
        attempt.event("other_game_running", count=len(running))
    return running


def watch_again(store: Store, data: dict, probe, on_change=None) -> Observer:
    """Pick up an attempt whose game outlived the last EqUpdater."""
    attempt = Attempt.resume(store, data)
    observer = Observer(attempt, probe, on_change=on_change,
                        launcher_name=(data.get("route") or {}).get("target", ""))
    game = next(p for p in data["processes"] if p.get("role") == "game"
                and p.get("exited_after") is None)
    observer.game = Proc(game["pid"], game.get("name") or GAME_EXE, game.get("start"))
    seen = _first(data, "game_observed")
    observer.game_seen_at = seen["t"] if seen and seen.get("t") is not None else attempt.elapsed()
    observer._confirmed = bool(_first(data, "game_confirmed"))
    try:
        probe.hold(observer.game)
    except Exception:
        pass
    return observer


def recover(store: Store, probe=None, evidence_fn=collect_evidence) -> list:
    """After a restart: every attempt nobody is watching any more. Those whose
    game still runs are returned to be watched again; the others get whatever
    evidence can be found now, and say that the ending was not seen."""
    resume = []
    for attempt_id in store.ids():
        data = store.load(attempt_id)
        if data.get("finished") or data.get("damaged"):
            continue
        game = next((p for p in data.get("processes", [])
                     if p.get("role") == "game" and p.get("exited_after") is None), None)
        alive = False
        if game and probe is not None:
            try:
                alive = any(p.pid == game["pid"] and p.start == game.get("start")
                            for p in probe.snapshot([GAME_EXE]))
            except Exception:
                alive = False
        if alive:
            resume.append(data)
            continue
        data.setdefault("events", []).append(
            {"t": None, "at": time.time(), "kind": "observation_lost"})
        # A later attempt's crash is that attempt's.
        later = [store.load(i).get("started_at") for i in store.ids() if i > attempt_id]
        later = [t for t in later if t]
        try:
            evidence_fn(data, until=min(later)) if later else evidence_fn(data)
        except Exception:
            pass
        data["finished"] = True
        data["outcome"], data["explanation"] = classify(data)
        store.write_json(os.path.join(store.attempt_dir(attempt_id), "attempt.json"), data)
    return resume


# ──────────────────────────────────────────────────────────────────────────────
#  The support bundle
# ──────────────────────────────────────────────────────────────────────────────

MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 512 * 1024
#: Never in a bundle, by where they live, whatever asked for them.
_FORBIDDEN = re.compile(r"(?i)(^|[\\/])(WTF|SavedVariables|Screenshots|Cache)([\\/]|$)")


def _local_time(epoch) -> str:
    if epoch is None:
        return "unknown"
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def event_details(event: dict) -> str:
    """An event's own fields, for reading."""
    return "  ".join(f"{k}={v}" for k, v in event.items()
                     if k not in ("t", "at", "kind", "output_tail")
                     and v not in (None, "", False))


def support_summary(data: dict, scan: dict | None = None) -> str:
    """What a player sends and support reads first. English, and redacted by
    the caller along with everything else."""
    route = data.get("route") or {}
    outcome, explanation = classify(data)
    lines = [
        "EqUpdater Game Diagnostics summary",
        f"Attempt:          {data.get('id')}",
        f"When:             {_local_time(data.get('started_at'))}",
        f"EqUpdater:        {data.get('equpdater_version')}  ({data.get('platform')})",
        f"Client build:     {data.get('client_build') or 'unknown'}",
        f"Mode:             {'diagnostic launch' if data.get('diagnostic') else 'ordinary Play'}",
        "",
        "SYMPTOM / OUTCOME",
        f"  {OUTCOMES.get(outcome, outcome)}",
        f"  {explanation}",
        "",
        f"Last confirmed stage: {data.get('stage')}",
        f"Route:            {route.get('runner') or 'none'}: {route.get('target') or 'none selected'}",
        f"Command:          {' '.join(route.get('argv') or []) or 'none'}",
        f"Working folder:   {route.get('cwd') or 'none'}",
    ]
    if route.get("env"):
        lines.append("Settings:         " + ", ".join(
            f"{k}={v}" for k, v in route["env"].items()))
    failed = [p for p in data.get("preflight", []) if not p.get("ok")]
    if failed:
        lines += ["", "CHECKS THAT FAILED"]
        lines += [f"  {p['check']}: {p['detail']}" + (f" (code {p['code']})" if p.get("code") else "")
                  for p in failed]
    if data.get("processes"):
        lines += ["", "PROCESSES"]
        for p in data["processes"]:
            ended = (f"exited with code {p.get('exit_code')} at {p['exited_after']:.1f}s"
                     if p.get("exited_after") is not None else "no exit seen")
            lines.append(f"  {p.get('role')}: {p.get('name')} (pid {p.get('pid')}), {ended}")
    if data.get("events"):
        lines += ["", "TIMELINE"]
        for e in data["events"]:
            t = "      ?" if e.get("t") is None else f"{e['t']:7.1f}s"
            lines.append(f"  {t}  {e.get('kind')}  {event_details(e)}".rstrip())
    crash = data.get("crash") or {}
    if crash.get("reports") or crash.get("events"):
        lines += ["", "CRASH EVIDENCE (matched to this attempt)"]
        for r in crash.get("reports") or []:
            lines.append(f"  report {r.get('file')}: {r.get('error') or ''} "
                         f"signature {r.get('signature')}")
        for ev in crash.get("events") or []:
            lines.append(f"  event {ev.get('provider')}: {ev.get('application')} "
                         f"{ev.get('exception_code')} in {ev.get('module')} "
                         f"{ev.get('module_version')} at {ev.get('offset')}")
    if crash.get("older_reports"):
        lines += ["", "OLDER OR UNRELATED REPORTS (not part of the conclusion)"]
        for r in crash["older_reports"]:
            lines.append(f"  {r.get('file')}: {r.get('why_excluded') or 'unreadable'}")
    findings = component_findings(data.get("components") or [],
                                  route.get("target") or "")
    if findings:
        lines += ["", "LAUNCH COMPONENT FINDINGS"] + [f"  {f}" for f in findings]
    if data.get("loaded_modules"):
        lines += ["", "OBSERVED LOADED FROM THE CLIENT FOLDER"]
        lines += [f"  {m}" for m in data["loaded_modules"]]
    if scan:
        cov = scan.get("coverage") or {}
        lines += ["", "CLIENT SCAN", f"  {_local_time(scan.get('finished_at'))}, "
                  f"{scan.get('mode')}: {cov.get('files', 0)} files enumerated, "
                  f"{cov.get('skipped', 0)} skipped; manifest: {cov.get('manifest') or 'none'}"]
        for level, title in (("confirmed", "Confirmed problems"),
                             ("possible", "Possible conflicts")):
            items = [f for f in scan.get("findings", []) if f.get("level") == level]
            if items:
                lines.append(f"  {title} ({len(items)}):")
                lines += [f"    {f.get('path') or ''}: {f.get('why')}" for f in items[:25]]
    else:
        lines += ["", "CLIENT SCAN", "  Not run for this report."]
    limits = list(data.get("limits") or [])
    if limits:
        lines += ["", "COLLECTION LIMITS"] + [f"  {t}" for t in limits]
    return "\n".join(lines) + "\n"


def crash_extract(path: str, redactor: Redactor) -> str:
    """The header and stack of a crash report, filtered: the parts that say
    what crashed, not the memory dump the client appends."""
    text = read_tail(path, MAX_REPORT_READ)
    keep = text.split("Stack Trace (Using DBGHELP.DLL)")[0]
    frames = re.search(r"Stack Trace \(Using DBGHELP\.DLL\).*?(?=\n-{10,}\n\s*\n-{10,}|\Z)",
                       text, re.S)
    if frames:
        keep += frames.group(0)[:8000]
    return redactor.text(keep)[:MAX_MEMBER_BYTES]


def _runner_logs(store: Store, attempt_id: str) -> list:
    """Logs a diagnostic launch had Wine, Proton or DXVK write into this
    attempt's own folder: only there, so nothing else can be swept up."""
    folder = store.attempt_dir(attempt_id or "")
    try:
        return sorted(n for n in os.listdir(folder) if n.lower().endswith(".log"))[:8]
    except OSError:
        return []


def plan_bundle(data: dict, scan: dict | None, store: Store,
                session_lines=None) -> tuple:
    """(what will be included, what will not and why): the preview. No file is
    written, and no secret or path is shown here."""
    client = data.get("client_root") or ""
    include = [("summary.txt", "Summary: outcome, last stage, main findings"),
               ("timeline.json", "Timeline: events, processes and exit codes")]
    omitted = []
    if scan:
        include.append(("client-scan.json", "Client scan: coverage, findings, component hashes"))
    else:
        omitted.append(("client-scan.json", "no scan has been run"))
    output = os.path.join(store.attempt_dir(data.get("id", "")), "runner-output.txt")
    if os.path.isfile(output) and os.path.getsize(output):
        include.append(("runner-output.txt", "Launcher output (end of it, filtered)"))
    else:
        omitted.append(("runner-output.txt", "nothing was captured"))
    crash = data.get("crash") or {}
    for r in crash.get("reports") or []:
        include.append((f"crash-reports/{_safe_member(r.get('file', 'report'))}",
                        "Crash report extract, filtered"))
    if crash.get("events"):
        include.append(("crash-reports/windows-events.json", "Windows fault entries (fields only)"))
    for log in data.get("logs") or []:
        rel = log.get("file", "")
        if client and not _FORBIDDEN.search(rel):
            include.append((f"logs/{_safe_member(rel)}", "Log from this attempt (end of it, filtered)"))
    for name in _runner_logs(store, data.get("id")):
        include.append((f"runner-logs/{_safe_member(name)}",
                        "Log from this diagnostic launch (end of it, filtered)"))
    if session_lines:
        include.append(("session-log.txt", "End of this session's EqUpdater log, filtered"))
    else:
        omitted.append(("session-log.txt", "not available"))
    omitted.append(("Errors/*.dmp", "memory dumps are never included"))
    omitted.append(("WTF, SavedVariables, chat logs, game files", "never included"))
    return include, omitted


def _safe_member(name: str) -> str:
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "file"
    return base[:80]


def export_bundle(dest: str, data: dict, scan: dict | None, store: Store,
                  session_lines=None, home: str | None = None) -> tuple:
    """Write the ZIP: (members written, [(source, why omitted)]). Always valid,
    however little there is; a source that fails is listed as omitted. Written
    beside ``dest`` and renamed, so a failure leaves no half a ZIP."""
    client = data.get("client_root") or ""
    red = Redactor(client, home)
    members, omitted, total = [], [], 0
    planned, skipped = plan_bundle(data, scan, store, session_lines)
    omitted.extend(skipped)

    def add(zf, name, text):
        nonlocal total
        raw = text.encode("utf-8", "replace")
        if len(raw) > MAX_MEMBER_BYTES:
            raw = raw[-MAX_MEMBER_BYTES:]
        if total + len(raw) > MAX_BUNDLE_BYTES:
            omitted.append((name, "the bundle's size limit was reached"))
            return
        zf.writestr(name, raw)
        total += len(raw)
        members.append(name)

    timeline = {k: v for k, v in data.items() if k not in ("client_root",)}
    timeline["schema"] = SCHEMA
    tmp_fd, tmp = tempfile.mkstemp(prefix=".eqdiag-", suffix=".zip",
                                   dir=os.path.dirname(os.path.abspath(dest)))
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            add(zf, "summary.txt", red.text(support_summary(data, scan)))
            add(zf, "timeline.json", json.dumps(red.data(timeline), indent=1,
                                                ensure_ascii=False, default=str))
            if scan:
                add(zf, "client-scan.json", json.dumps(
                    red.data(exportable_scan(scan)), indent=1, ensure_ascii=False))
            output = os.path.join(store.attempt_dir(data.get("id", "")),
                                  "runner-output.txt")
            if os.path.isfile(output):
                try:
                    add(zf, "runner-output.txt", red.text(read_tail(output)))
                except OSError as e:
                    omitted.append(("runner-output.txt", f"unreadable: {e}"))
            crash = data.get("crash") or {}
            errors = gamepaths.game_path(client, "Errors") if client else ""
            for r in crash.get("reports") or []:
                name = _safe_member(r.get("file", "report"))
                try:
                    add(zf, f"crash-reports/{name}",
                        crash_extract(os.path.join(errors, r["file"]), red))
                except (OSError, KeyError) as e:
                    omitted.append((f"crash-reports/{name}", f"unreadable: {e}"))
            if crash.get("events"):
                add(zf, "crash-reports/windows-events.json",
                    json.dumps(red.data(crash["events"]), indent=1, default=str))
            for log in data.get("logs") or []:
                rel = log.get("file", "")
                if rel not in KNOWN_LOGS or _FORBIDDEN.search(rel):
                    omitted.append((rel, "not a known diagnostic source"))
                    continue
                try:
                    add(zf, f"logs/{_safe_member(rel)}", red.text(read_tail(
                        os.path.join(client, *rel.split("/")), 256 * 1024)))
                except OSError as e:
                    omitted.append((rel, f"unreadable: {e}"))
            for name in _runner_logs(store, data.get("id")):
                try:
                    add(zf, f"runner-logs/{_safe_member(name)}", red.text(read_tail(
                        os.path.join(store.attempt_dir(data["id"]), name), 256 * 1024)))
                except OSError as e:
                    omitted.append((name, f"unreadable: {e}"))
            if session_lines:
                add(zf, "session-log.txt", red.text("".join(session_lines[-500:])))
            add(zf, "omitted.txt", red.text("\n".join(
                f"{name}: {why}" for name, why in omitted) + "\n"))
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return members, omitted


def exportable_scan(scan: dict) -> dict:
    """A scan as it may leave: coverage, findings and the launch components.
    The full inventory stays on this computer."""
    return {k: scan.get(k) for k in ("schema", "id", "mode", "finished_at",
                                     "coverage", "findings", "components",
                                     "recent_changes", "client_build")}
