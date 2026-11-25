"""Build the EqUpdater 3 beta for Linux: dist/EqUpdater3-v<version>-Linux-x86_64.AppImage.

Runs on Linux (the manual GitHub Actions workflow .github/workflows/beta.yml). It

1. builds the folder build with tools/build_v3.py (PyInstaller: Python, PySide6/Qt Quick, certifi,
   the QML, the skins and fonts; unused Qt areas pruned; the host's font stack left out);
2. copies in the few xcb helper libraries Qt's X11 plugin needs and many desktops do not have
   (libxcb-cursor and friends), found with ``ldd`` on the plugin and an allow-list, so nothing
   else from the build machine leaks in (GL/EGL, fontconfig, dbus, libc come from the player's system);
3. lays out an AppDir (AppRun, desktop entry, icon) and packs it with a pinned appimagetool whose
   sha256 is checked.

There is **no aria2c**: EqUpdater 3 does not download the game.

**Not built or run in the environment this was written in (Windows).** What can be checked without
Linux (the layout, the file contents, the library selection, the prune rules) is covered by
``tests/test_v3_appimage.py``; building the image, running it, Wayland/X11, and starting the game through
Wine or UMU are listed as untested in ``docs/v3/LINUX.md``.

    python3 tools/build_appimage_v3.py
"""

from __future__ import annotations

import configparser
import fnmatch
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from equpdater import branding                    # noqa: E402
from equpdater.version3 import APP_VERSION_V3     # noqa: E402

NAME = "EqUpdater3"
TITLE = "EqUpdater 3 Beta"
WORK = os.path.join(HERE, "build", "appimage3")
APPDIR = os.path.join(WORK, f"{NAME}.AppDir")
DIST = os.path.join(HERE, "dist")

def _pinned_tool() -> tuple:
    """The pinned appimagetool (url, sha256) of the 2.x AppImage build: one place to update, no retyped hash."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_equ2_appimage", os.path.join(HERE, "tools", "build_appimage.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.APPIMAGETOOL


APPIMAGETOOL = _pinned_tool()

#: The only system libraries copied into the image: Qt's xcb platform plugin needs these and a
#: default install often lacks them (Qt 6.5+ requires libxcb-cursor0). Everything else comes from
#: the player's system, as the AppImage excludelist advises.
BUNDLE_ALLOW = ("libxcb-cursor.so*", "libxcb-icccm.so*", "libxcb-image.so*", "libxcb-keysyms.so*",
                "libxcb-render-util.so*", "libxcb-shape.so*", "libxcb-xkb.so*", "libxcb-util.so*",
                "libxcb-xinerama.so*", "libxkbcommon-x11.so*")

APPRUN = """#!/bin/sh
# EqUpdater 3 beta's AppImage entry: run the bundled program from inside the mount.
HERE="${APPDIR:-$(dirname "$(readlink -f "$0")")}"
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/usr/bin/EqUpdater3" "$@"
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=EqUpdater 3 Beta
GenericName=OctoWoW updater
Comment=Mods, addons, texture packs, tweaks and launching for OctoWoW
Exec=EqUpdater3
Icon=equpdater3
Terminal=false
Categories=Game;Utility;
StartupWMClass=EqUpdater3
"""


def parse_ldd(output: str) -> list:
    """Absolute library paths from ``ldd`` output ("libfoo.so.1 => /usr/lib/x/libfoo.so.1 (0x...)")."""
    out = []
    for line in output.splitlines():
        m = re.match(r"\s*(\S+)\s*=>\s*(/\S+)", line)
        if m:
            out.append(m.group(2))
    return out


def select_bundle(paths: list) -> list:
    """The subset of ``paths`` to copy into the image (allow-list by file name)."""
    return sorted({p for p in paths if any(fnmatch.fnmatch(os.path.basename(p), pat) for pat in BUNDLE_ALLOW)})


def library_paths(plugin: str, run=subprocess.run) -> list:
    r = run(["ldd", plugin], capture_output=True, text=True)
    return parse_ldd(r.stdout)


def find_xcb_plugin(built: str) -> str | None:
    for d, _s, files in os.walk(built):
        if "libqxcb.so" in files:
            return os.path.join(d, "libqxcb.so")
    return None


def make_executable(path: str) -> None:
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def assemble(appdir: str, built: str, icon: str, libs=()) -> None:
    """Lay out the AppDir from the folder build. No network, no Linux needed (tested on Windows)."""
    if os.path.isdir(appdir):
        shutil.rmtree(appdir)
    usr_bin = os.path.join(appdir, "usr", "bin")
    shutil.copytree(built, usr_bin)
    lib_dir = os.path.join(appdir, "usr", "lib")
    os.makedirs(lib_dir)
    for lib in libs:
        shutil.copy2(lib, os.path.join(lib_dir, os.path.basename(lib)))
    apprun = os.path.join(appdir, "AppRun")
    with open(apprun, "w", newline="\n") as f:
        f.write(APPRUN)
    make_executable(apprun)
    with open(os.path.join(appdir, "equpdater3.desktop"), "w", newline="\n") as f:
        f.write(DESKTOP)
    shutil.copy(icon, os.path.join(appdir, "equpdater3.png"))
    shutil.copy(icon, os.path.join(appdir, ".DirIcon"))
    icons = os.path.join(appdir, "usr", "share", "icons", "hicolor", "256x256", "apps")
    os.makedirs(icons)
    shutil.copy(icon, os.path.join(icons, "equpdater3.png"))
    exe = os.path.join(usr_bin, NAME)
    if os.path.isfile(exe):
        make_executable(exe)


def check_desktop(path: str) -> list:
    """Problems with a .desktop file (empty list if it is fine)."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    problems = []
    if "Desktop Entry" not in cp:
        return ["no [Desktop Entry]"]
    d = cp["Desktop Entry"]
    for key in ("Type", "Name", "Exec", "Icon", "Categories"):
        if not d.get(key):
            problems.append(f"missing {key}")
    if d.get("Type") != "Application":
        problems.append("Type is not Application")
    return problems


def fetch(url: str, sha256: str) -> bytes:
    print(f"  fetching {url}")
    req = urllib.request.Request(url, headers={"User-Agent": branding.USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise SystemExit(f"checksum mismatch for {url}: {got} != {sha256}")
    return data


def main() -> str:
    if not sys.platform.startswith("linux"):
        raise SystemExit("The AppImage is built on Linux.")
    subprocess.run([sys.executable, os.path.join(HERE, "tools", "build_v3.py"), "--no-test"], cwd=HERE, check=True)
    built = os.path.join(DIST, NAME)
    if not os.path.isfile(os.path.join(built, NAME)):
        raise SystemExit(f"{built}/{NAME} is missing: the build failed")
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    plugin = find_xcb_plugin(built)
    libs = select_bundle(library_paths(plugin)) if plugin else []
    print("  system libraries copied in:", ", ".join(os.path.basename(x) for x in libs) or "none")
    assemble(APPDIR, built, os.path.join(HERE, "icon.png"), libs)
    problems = check_desktop(os.path.join(APPDIR, "equpdater3.desktop"))
    if problems:
        raise SystemExit("desktop entry: " + "; ".join(problems))
    tool = os.path.join(WORK, "appimagetool")
    with open(tool, "wb") as f:
        f.write(fetch(*APPIMAGETOOL))
    make_executable(tool)
    out = os.path.join(DIST, f"{NAME}-v{APP_VERSION_V3}-Linux-x86_64.AppImage")
    if os.path.exists(out):
        os.remove(out)
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    subprocess.run([tool, "--no-appstream", APPDIR, out], env=env, check=True)
    make_executable(out)
    print(f"\n  {out}  ({os.path.getsize(out) // (1024 * 1024)} MB)")
    return out


if __name__ == "__main__":
    main()
