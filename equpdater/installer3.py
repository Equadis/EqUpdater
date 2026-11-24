"""The EqUpdater 3.0 beta installer: one .exe that installs the 3.0 build next
to 2.x, never over it.

Unlike ``installer.py`` (2.x), this

* installs to ``%LOCALAPPDATA%/Programs/EqUpdater3`` and runs ``EqUpdater3.exe``;
* **never touches 2.x**: no shortcut that points at 2.x is moved, and 2.x's
  folder and settings are only ever read (see docs/v3/MIGRATION.md);
* refuses to install while EqUpdater 2.x or EqUpdater 3 is running, and says
  so, so two windows never edit one game folder (docs/v3/INSTALLATION.md);
* creates its own Start-menu entry ("EqUpdater 3 Beta") and offers a desktop
  one, and leaves every other shortcut alone;
* is staged like 2.x's: the new build is copied beside the old one, which is
  only moved aside once the copy is complete, and moved back if the swap fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import zipfile

from . import deploy

APP = "EqUpdater3"
EXE = APP + ".exe"
TITLE = "EqUpdater 3 Beta"
PAYLOAD = "EqUpdater-app.zip"


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
            folders=None, two_x_dir: str | None = None, warm=warm_cache) -> tuple:
    """Install from ``payload``; returns (exe, shortcuts created). Raises
    InstallError, with the previous version intact, when it cannot."""
    install_dir = os.path.abspath(install_dir or default_install_dir())
    why = preflight(install_dir, two_x_dir)
    if why:
        raise InstallError(why)
    staging = install_dir + ".payload"
    os.makedirs(os.path.dirname(install_dir), exist_ok=True)
    try:
        exe = swap_in(unpack(payload, staging), install_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if warm:
        warm(exe)
    folders = folders if folders is not None else deploy.shortcut_folders()
    created = []
    start = folders[1] if len(folders) > 1 else None
    if start and os.path.isdir(start):
        lnk = os.path.join(start, TITLE + ".lnk")
        if _shortcut(lnk, exe):                  # our own entry: (re)written, never another's
            created.append(lnk)
    desktop = os.path.join(folders[0], TITLE + ".lnk") if folders else None
    if desktop and not os.path.exists(desktop) and want_desktop() and _shortcut(desktop, exe):
        created.append(desktop)
    return exe, created


def main() -> int:
    """The installer's window: one line of progress, a question the first time,
    a message if it fails."""
    import tkinter as tk
    from tkinter import messagebox

    from . import platforms

    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg="#081a35")
    root.resizable(False, False)
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
            result["value"] = install(payload_path(), want_desktop=want_desktop)
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
