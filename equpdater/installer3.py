"""The EqUpdater 3.0 beta installer: one .exe that installs the 3.0 build next
to 2.x, never over it.

Unlike ``installer.py`` (2.x), this

* installs to ``%LOCALAPPDATA%/Programs/EqUpdater3`` and runs ``EqUpdater3.exe``;
* **never touches 2.x**: no shortcut that points at 2.x is moved, and 2.x's
  folder and settings are only ever read (see docs/v3/MIGRATION.md);
* refuses to install while EqUpdater 2.x or EqUpdater 3 is running, and says
  so, so two windows never edit one game folder (docs/v3/INSTALLATION.md);
* creates its own Start-menu entry ("EqUpdater 3 Beta"), offers a desktop one,
  and an Apps & features entry (this user only), and leaves every other
  shortcut alone;
* is staged like 2.x's: the new build is copied beside the old one, which is
  only moved aside once the copy is complete, and moved back if the swap fails;
* can remove itself (``--uninstall``): the program folder, its own shortcuts and
  its Apps & features entry, **never** settings, the game or 2.x;
* has a silent mode for scripts and for testing (``--silent``, ``--install-dir``,
  ``--log``), because a window-less program has no console.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import zipfile

from . import deploy
from .version3 import APP_VERSION_V3

APP = "EqUpdater3"
EXE = APP + ".exe"
TITLE = "EqUpdater 3 Beta"
PAYLOAD = "EqUpdater-app.zip"
MARKER = "install.json"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EqUpdater3"
SHORTCUT = TITLE + ".lnk"


class InstallError(RuntimeError):
    """Nothing was changed (or the previous version was put back)."""


def default_install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", APP)


def payload_path() -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, PAYLOAD)


def _exe_in_use(folder: str) -> bool:
    exe = os.path.join(folder, EXE)
    if not os.path.isfile(exe):
        return False
    try:
        with open(exe, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def preflight(install_dir: str, two_x_dir: str | None = None) -> str:
    """"" when installing is safe, else why not."""
    from .core import install as I
    if I.installed_2x_running(two_x_dir):
        return ("EqUpdater 2 is running. Close it first: two EqUpdater windows "
                "should not manage one game folder at once. Nothing was changed.")
    if _exe_in_use(install_dir):
        return f"{TITLE} is running. Close it and run the installer again. Nothing was changed."
    return ""


def _remove(path: str) -> bool:
    try:
        shutil.rmtree(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def unpack(payload: str, dest: str) -> str:
    """Extract the packed build into ``dest`` (replacing it) and return the
    folder holding EqUpdater3.exe. Entries that would leave ``dest`` are refused."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    root = os.path.realpath(dest)
    with zipfile.ZipFile(payload) as zf:
        for info in zf.infolist():
            target = os.path.realpath(os.path.join(dest, info.filename))
            if target != root and not target.startswith(root + os.sep):
                raise InstallError(f"Unsafe path in the package: {info.filename}")
        zf.extractall(dest)
    for folder, _dirs, files in os.walk(dest):
        if EXE in files:
            return folder
    raise InstallError(f"The package holds no {EXE}.")


def swap_in(build_dir: str, install_dir: str, rename=os.rename) -> str:
    """Put the folder build in place; returns the path of its exe. The old
    version is kept until the new one is complete and comes back on failure."""
    install_dir = os.path.abspath(install_dir)
    if not os.path.isfile(os.path.join(build_dir, EXE)):
        raise InstallError(f"{build_dir} has no {EXE}: the build is incomplete.")
    os.makedirs(os.path.dirname(install_dir), exist_ok=True)
    new, old = install_dir + ".new", install_dir + ".old"
    if not os.path.isdir(install_dir) and os.path.isdir(old):
        os.rename(old, install_dir)            # an earlier run stopped between steps
    _remove(old)
    _remove(new)
    try:
        shutil.copytree(build_dir, new)
    except OSError as e:
        _remove(new)
        raise InstallError(f"Could not copy the new version to {new}: {e}") from e
    had_old = os.path.isdir(install_dir)
    if had_old:
        try:
            rename(install_dir, old)
        except OSError as e:
            _remove(new)
            raise InstallError(f"Could not move the current version aside ({e}). "
                               "Nothing was changed.") from e
    try:
        rename(new, install_dir)
    except OSError as e:
        if had_old:
            os.rename(old, install_dir)        # the old version comes back
        _remove(new)
        raise InstallError(f"Could not put the new version in place ({e}). "
                           "The previous version is unchanged.") from e
    if had_old:
        _remove(old)
    return os.path.join(install_dir, EXE)


def _shortcut(lnk: str, exe: str) -> bool:
    try:
        deploy.write_shortcut(lnk, exe)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _write_marker(build_dir: str) -> None:
    """Says "EqUpdater 3 put this here", so an uninstall never deletes a folder it did not make."""
    import json
    import time
    with open(os.path.join(build_dir, MARKER), "w", encoding="utf-8") as f:
        json.dump({"app": APP, "version": APP_VERSION_V3, "installed": int(time.time())}, f)


def register(exe: str, key: str = UNINSTALL_KEY) -> bool:
    """Apps & features entry (this user only; no administrator rights)."""
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            folder = os.path.dirname(exe)
            for name, value in (("DisplayName", TITLE), ("DisplayVersion", APP_VERSION_V3),
                                ("Publisher", "EqUpdater"), ("InstallLocation", folder),
                                ("DisplayIcon", exe), ("UninstallString", f'"{exe}" --uninstall'),
                                ("QuietUninstallString", f'"{exe}" --uninstall --silent')):
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        return True
    except (ImportError, OSError):
        return False


def unregister(key: str = UNINSTALL_KEY) -> bool:
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        return True
    except (ImportError, OSError):
        return False


def warm_cache(exe: str, run=subprocess.run, timeout: float = 90.0) -> bool:
    """Load every page once, headless, so Qt compiles the interface's QML now
    and the player's first launch starts from its cache (about a second
    sooner). Best effort: the beta works the same without it."""
    import tempfile
    out = os.path.join(tempfile.mkdtemp(prefix="equ3-warm-"), "report.json")
    try:
        r = run([exe, f"--self-test={out}", "--gui"], cwd=os.path.dirname(exe),
                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"), timeout=timeout,
                capture_output=True)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(os.path.dirname(out), ignore_errors=True)


def install(payload: str, install_dir: str | None = None, want_desktop=lambda: True,
            folders=None, two_x_dir: str | None = None, warm=warm_cache,
            registry_key: str | None = UNINSTALL_KEY) -> tuple:
    """Install from ``payload``; returns (exe, shortcuts created). Raises
    InstallError, with the previous version intact, when it cannot.

    Only three things are ever written: the program folder (and its short-lived
    ``.new``/``.old``/``.payload`` neighbours), this program's own shortcuts, and its
    Apps & features entry. Settings, the game and 2.x are not touched."""
    install_dir = os.path.abspath(install_dir or default_install_dir())
    why = preflight(install_dir, two_x_dir)
    if why:
        raise InstallError(why)
    staging = install_dir + ".payload"
    os.makedirs(os.path.dirname(install_dir), exist_ok=True)
    try:
        build = unpack(payload, staging)
        _write_marker(build)
        exe = swap_in(build, install_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if registry_key:
        register(exe, registry_key)
    if warm:
        warm(exe)
    folders = folders if folders is not None else deploy.shortcut_folders()
    created = []
    start = folders[1] if len(folders) > 1 else None
    if start and os.path.isdir(start):
        lnk = os.path.join(start, SHORTCUT)
        if _shortcut(lnk, exe):                  # our own entry: (re)written, never another's
            created.append(lnk)
    desktop = os.path.join(folders[0], SHORTCUT) if folders else None
    if desktop and not os.path.exists(desktop) and want_desktop() and _shortcut(desktop, exe):
        created.append(desktop)
    return exe, created


def uninstall(install_dir: str | None = None, folders=None, registry_key: str | None = UNINSTALL_KEY,
              defer=None, shortcut_target=None) -> list:
    """Remove EqUpdater 3: its program folder, its own shortcuts and its Apps & features entry.

    **Never** touches ``%LOCALAPPDATA%/EqUpdater`` (settings, shared records, 3's own data), the
    game folder, EqUpdater 2 or any other shortcut. The folder is removed only if EqUpdater 3
    made it (the marker file) and it is not running. ``defer(path)`` is called instead of
    removing the folder when the caller is running from inside it (the program cannot delete
    itself); it must remove the folder after the caller exits. Returns what was removed."""
    install_dir = os.path.abspath(install_dir or default_install_dir())
    removed = []
    if not os.path.isfile(os.path.join(install_dir, MARKER)):
        raise InstallError(f"{install_dir} was not made by the {TITLE} installer; nothing was removed.")
    if _exe_in_use(install_dir) and defer is None:
        raise InstallError(f"{TITLE} is running. Close it and try again. Nothing was removed.")
    folders = folders if folders is not None else deploy.shortcut_folders()
    exe = os.path.join(install_dir, EXE)
    for folder in folders:
        lnk = os.path.join(folder, SHORTCUT)
        if not os.path.isfile(lnk):
            continue
        try:                                           # only ours: it must point at this program
            target = (shortcut_target or (lambda p: deploy.read_shortcut(p)[0]))(lnk)
        except (OSError, subprocess.SubprocessError):
            target = ""
        if target and os.path.normcase(os.path.normpath(target)) != os.path.normcase(os.path.normpath(exe)):
            continue
        try:
            os.remove(lnk)
            removed.append(lnk)
        except OSError:
            pass
    if registry_key and unregister(registry_key):
        removed.append("Apps & features entry")
    for leftover in (install_dir + ".new", install_dir + ".old", install_dir + ".payload"):
        if os.path.isdir(leftover):
            _remove(leftover)
    if defer is not None:
        defer(install_dir)
        removed.append(install_dir + " (removed after this program exits)")
    elif _remove(install_dir):
        removed.append(install_dir)
    else:
        raise InstallError(f"Could not remove {install_dir}. Close anything using it and try again.")
    return removed


def deferred_delete(path: str, spawn=subprocess.Popen) -> None:
    """Remove ``path`` a moment after this process has exited (Windows)."""
    # One string, not a list: cmd does not understand the backslash-escaped quotes a list would produce.
    cmd = f'cmd /c "ping -n 3 127.0.0.1 >nul & rd /s /q "{path}""'
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    spawn(cmd, creationflags=flags, close_fds=True, cwd=os.environ.get("TEMP") or None)


def parse_args(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="EqUpdater3 installer", add_help=True)
    ap.add_argument("--uninstall", action="store_true", help="remove EqUpdater 3 (settings and the game are kept)")
    ap.add_argument("--silent", action="store_true", help="no windows, no questions")
    ap.add_argument("--install-dir", help=f"default: {default_install_dir()}")
    ap.add_argument("--desktop", action="store_true", help="silent mode: also create a desktop shortcut")
    ap.add_argument("--no-shortcuts", action="store_true", help="make no shortcuts at all")
    ap.add_argument("--no-launch", action="store_true", help="do not start EqUpdater 3 afterwards")
    ap.add_argument("--no-warm", action="store_true", help="skip the QML warm-up")
    ap.add_argument("--no-register", action="store_true", help="make no Apps & features entry")
    ap.add_argument("--log", help="write the outcome here (the installer has no console)")
    return ap.parse_args(argv)


def run_silent(args) -> int:
    lines, code = [], 0
    folders = [] if args.no_shortcuts else None
    key = None if args.no_register else UNINSTALL_KEY
    try:
        if args.uninstall:
            lines += ["removed: " + r for r in uninstall(args.install_dir, folders=folders, registry_key=key)]
        else:
            exe, created = install(payload_path(), args.install_dir, want_desktop=lambda: args.desktop,
                                   folders=folders, warm=None if args.no_warm else warm_cache, registry_key=key)
            lines += [f"installed: {exe}"] + [f"shortcut: {c}" for c in created]
            if not args.no_launch:
                from . import platforms
                platforms.spawn_detached([exe], cwd=os.path.dirname(exe))
    except InstallError as e:
        lines.append(f"error: {e}")
        code = 1
    text = "\n".join(lines) + "\n"
    if args.log:
        with open(args.log, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return code


def main(argv=None) -> int:
    """The installer's window: one line of progress, a question the first time,
    a message if it fails. With ``--silent`` there are no windows at all."""
    args = parse_args(argv)
    if args.silent:
        return run_silent(args)
    import tkinter as tk
    from tkinter import messagebox

    from . import platforms

    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg="#081a35")
    root.resizable(False, False)
    if args.uninstall:
        root.withdraw()
        if not messagebox.askyesno(TITLE, f"Remove {TITLE}?\n\nYour settings and your game files are kept.",
                                   parent=root):
            return 0
        try:
            uninstall(args.install_dir)
        except InstallError as e:
            messagebox.showerror(TITLE, str(e), parent=root)
            return 1
        messagebox.showinfo(TITLE, f"{TITLE} was removed. Your settings and your game files were not touched.",
                            parent=root)
        return 0
    status = tk.Label(root, text=f"Installing {TITLE}…", font=("Segoe UI", 11),
                      fg="#eee9dc", bg="#081a35", padx=32, pady=24)
    status.pack()
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry("+%d+%d" % ((root.winfo_screenwidth() - w) // 2,
                              (root.winfo_screenheight() - h) // 3))
    result, asked, answer = {}, threading.Event(), {}

    def ask_desktop():
        answer["yes"] = messagebox.askyesno(TITLE, f"Put a {TITLE} shortcut on the desktop?",
                                            parent=root)
        asked.set()

    def want_desktop():
        root.after(0, ask_desktop)
        asked.wait()
        return answer.get("yes", True)

    def work():
        try:
            result["value"] = install(payload_path(), args.install_dir, want_desktop=want_desktop)
        except Exception as e:                  # shown to the player below
            result["error"] = e

    def finish():
        if "error" not in result and "value" not in result:
            root.after(100, finish)
            return
        if "error" in result:
            messagebox.showerror(TITLE, f"{TITLE} could not be installed:\n\n{result['error']}",
                                 parent=root)
            result["code"] = 1
            root.destroy()
            return
        exe = result["value"][0]
        try:
            platforms.spawn_detached([exe], cwd=os.path.dirname(exe))
        except OSError as e:
            messagebox.showerror(TITLE, f"Installed to {exe}, but it could not be started: {e}",
                                 parent=root)
        result["code"] = 0
        root.destroy()

    threading.Thread(target=work, daemon=True).start()
    root.after(100, finish)
    root.mainloop()
    return result.get("code", 1)
