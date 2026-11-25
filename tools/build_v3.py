"""Build EqUpdater 3.0 (the Qt Quick front end) as a PyInstaller folder build.

    python tools/build_v3.py              # dist/EqUpdater3/EqUpdater3.exe, then the self-test
    python tools/build_v3.py --release    # the same, only after the test suite passes
    python tools/build_v3.py --setup      # Windows: also the beta installer .exe
    python tools/build_v3.py --no-test    # skip the self-test

Separate from ``build.py`` (2.x) on purpose: a different name, folder, install
location and shortcuts, so building or installing the beta never touches a
2.x install. A folder build, not one file, for the reason ``build.py``
gives (nothing is unpacked into %TEMP% on every launch).

What goes in: the package, the QML (``equpdater/qtui/qml``), the skins, the
fonts, the icon and certifi's CA bundle. What stays out: Tk and Pillow (3.0
does not use them) and the Qt modules the interface never imports
(multimedia, web, 3D, charts ...), which is most of Qt's weight.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from equpdater import branding  # noqa: E402
from equpdater.qtui.bridge import APP_VERSION_V3  # noqa: E402

NAME = "EqUpdater3"
WINDOWS = os.name == "nt"
DIST = os.path.join(HERE, "dist")
WORK = os.path.join(HERE, "build", NAME)
ENTRY = os.path.join(HERE, "EqUpdater3.py")
SETUP_ENTRY = os.path.join(HERE, "EqUpdater3Setup.py")
ICON, ICON_PNG = os.path.join(HERE, "icon.ico"), os.path.join(HERE, "icon.png")

#: Qt modules the interface does not import. QtMultimedia is only for a skin
#: whose animation is a video file (EqUi/VideoLayer.qml); no official skin is.
EXCLUDES = ["tkinter", "PIL", "_tkinter",
            "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
            "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
            "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
            "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
            "PySide6.QtQuick3D", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
            "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc",
            "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtPositioning",
            "PySide6.QtLocation", "PySide6.QtScxml", "PySide6.QtRemoteObjects",
            "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSvgWidgets",
            "PySide6.QtOpenGLWidgets", "PySide6.QtPrintSupport", "PySide6.QtTextToSpeech",
            "PySide6.QtSpatialAudio", "PySide6.QtHttpServer", "PySide6.QtStateMachine"]


def run(cmd: list) -> None:
    print("  $ " + " ".join(cmd))
    if subprocess.run(cmd, cwd=HERE).returncode != 0:
        raise SystemExit("build failed")


def pyinstaller_cmd() -> list:
    sep = os.pathsep
    cmd = [sys.executable, "-m", "PyInstaller", "--onedir", "--windowed", "--noconfirm",
           "--name", NAME, "--distpath", DIST, "--workpath", WORK, "--specpath", WORK,
           "--hidden-import", "equpdater.qtui.main",
           "--collect-data", "certifi",
           # QML files are read from disk by Qt: they sit where QML_DIR finds them.
           "--add-data", os.path.join(HERE, "equpdater", "qtui", "qml") + sep + "equpdater/qtui/qml",
           "--add-data", os.path.join(HERE, "skins") + sep + "skins",
           "--add-data", os.path.join(HERE, "fonts") + sep + "fonts",
           "--add-data", ICON_PNG + sep + ".",
           "--add-data", ICON + sep + "."]
    if WINDOWS and os.path.exists(ICON):
        cmd += ["--icon", ICON]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    return cmd + [ENTRY]


#: Left out of the folder after PyInstaller: QML styles other than Basic (the
#: interface sets its own look), whole Qt areas the QML never imports, Qt's
#: own translations, the software OpenGL fallback and the QML debugger.
PRUNE_DIRS = ["translations", "plugins/qmltooling",
              "qml/QtQuick/Controls/Material", "qml/QtQuick/Controls/Universal",
              "qml/QtQuick/Controls/Fusion", "qml/QtQuick/Controls/Imagine",
              "qml/QtQuick/Controls/FluentWinUI3", "qml/QtQuick/Controls/Windows",
              "qml/QtQuick/Controls/macOS", "qml/QtQuick/Controls/iOS",
              "qml/QtQuick/Controls/Android", "qml/QtQuick/NativeStyle",
              "qml/QtQuick/VirtualKeyboard", "qml/QtQuick/Particles", "qml/QtQuick/Scene2D",
              "qml/QtQuick/Scene3D", "qml/QtQuick/tooling", "qml/QtQuick/Effects",
              "qml/QtQuick3D", "qml/Qt3D", "qml/QtCharts", "qml/QtGraphs",
              "qml/QtDataVisualization", "qml/QtLocation", "qml/QtMultimedia",
              "qml/QtTest", "qml/Qt5Compat", "qml/QtQuick/LocalStorage",
              "qml/QtQuick/Shapes", "qml/QtQuick/VectorImage", "qml/QtQuick/Timeline"]
PRUNE_FILES = ["opengl32sw.dll", "Qt6Quick3D*", "Qt63D*", "Qt6Charts*", "Qt6Graphs*",
               "Qt6DataVisualization*", "Qt6Location*", "Qt6Multimedia*", "Qt6RemoteObjects*",
               "Qt6Pdf*", "Qt6Sql*", "Qt6Test*", "Qt6QuickControls2Material*",
               "Qt6QuickControls2Universal*", "Qt6QuickControls2Fusion*",
               "Qt6QuickControls2Imagine*", "Qt6QuickControls2FluentWinUI3*",
               "Qt6QuickControls2Windows*", "Qt6LabsStyleKit*", "Qt6QuickNativeStyle*",
               "Qt6QuickParticles*", "Qt6QuickVectorImage*", "Qt6QuickTimeline*",
               "Qt6QuickShapes*", "Qt6VirtualKeyboard*", "Qt6Bluetooth*", "Qt6Nfc*",
               "Qt6Sensors*", "Qt6SerialPort*", "Qt6Positioning*", "Qt6Scxml*",
               "Qt6WebSockets*", "Qt6WebChannel*", "Qt6Designer*", "Qt6Help*",
               "Qt6SvgWidgets*", "Qt6OpenGLWidgets*", "Qt6PrintSupport*", "Qt6TextToSpeech*",
               "Qt6SpatialAudio*", "Qt6HttpServer*", "Qt6StateMachine*"]


def qt_roots(internal: str) -> list:
    """Where Qt's files are under PySide6: beside the modules on Windows, in ``Qt/`` (libraries
    in ``Qt/lib``) on Linux."""
    base = os.path.join(internal, "PySide6")
    return [r for r in (base, os.path.join(base, "Qt")) if os.path.isdir(r)]


def prune(internal: str) -> int:
    """Remove what the interface never loads; returns the bytes freed. Works on both layouts:
    ``Qt6Core.dll`` beside the modules (Windows) and ``libQt6Core.so.6`` in ``Qt/lib`` (Linux)."""
    import fnmatch
    freed = 0
    for qt in qt_roots(internal):
        for rel in PRUNE_DIRS:
            d = os.path.join(qt, *rel.split("/"))
            if os.path.isdir(d):
                freed += folder_size(d)
                shutil.rmtree(d)
        for folder in (qt, os.path.join(qt, "lib")):
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                stem = name[3:] if name.startswith("lib") else name           # libQt6Pdf.so.6 -> Qt6Pdf.so.6
                if any(fnmatch.fnmatch(stem, pat) or fnmatch.fnmatch(name, pat) for pat in PRUNE_FILES):
                    p = os.path.join(folder, name)
                    if os.path.isfile(p):
                        freed += os.path.getsize(p)
                        os.remove(p)
    return freed


def strip_host_font_stack(internal: str) -> list:
    """Linux: the host's fontconfig/freetype are used, not the build machine's (an old bundled
    fontconfig reading a newer /etc/fonts floods the terminal; see platforms.HOST_FONT_STACK and
    build.py). Returns the names removed. A no-op on Windows."""
    removed = []
    if WINDOWS:
        return removed
    from equpdater.platforms import HOST_FONT_STACK
    for folder in (internal, *(os.path.join(r, "lib") for r in qt_roots(internal))):
        for name in HOST_FONT_STACK:
            p = os.path.join(folder, name)
            if os.path.lexists(p):
                os.remove(p)
                removed.append(name)
    return removed


def folder_size(path: str) -> int:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(path) for f in fs)


def self_test(exe: str, gui: bool = True) -> dict:
    out = os.path.join(tempfile.mkdtemp(prefix="equ3-st-"), "report.json")
    cmd = [exe, f"--self-test={out}"] + (["--gui"] if gui else [])
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run(cmd, cwd=os.path.dirname(exe), env=env, timeout=180)
    if not os.path.exists(out):
        raise SystemExit(f"the self-test wrote no report (exit {r.returncode})")
    with open(out, encoding="utf-8") as f:
        report = json.load(f)
    report["exit_code"] = r.returncode
    return report


def build_setup(app_folder: str) -> str:
    """Windows: pack the folder build into the one-file beta installer."""
    import zipfile
    from equpdater.installer3 import PAYLOAD
    os.makedirs(WORK, exist_ok=True)
    payload = os.path.join(WORK, PAYLOAD)
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, _d, files in os.walk(app_folder):
            for name in files:
                p = os.path.join(folder, name)
                zf.write(p, os.path.relpath(p, app_folder))
    name = f"{NAME}-v{APP_VERSION_V3}-Windows-Installer"
    cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--noconfirm",
           "--name", name, "--distpath", DIST, "--workpath", os.path.join(WORK, "setup"),
           "--specpath", WORK, "--add-data", payload + os.pathsep + ".",
           "--add-data", ICON_PNG + os.pathsep + ".",
           "--exclude-module", "PySide6", "--exclude-module", "PIL"]
    if os.path.exists(ICON):
        cmd += ["--icon", ICON]
    run(cmd + [SETUP_ENTRY])
    built = os.path.join(DIST, name + ".exe")
    if not os.path.exists(built):
        raise SystemExit(f"{built} is missing")
    return built


def main() -> None:
    if "--release" in sys.argv:
        sys.path.insert(0, os.path.join(HERE, "tools"))
        from check import run_suite
        passed, report = run_suite(os.path.join(HERE, "build", "test-log.txt"))
        print(report)
        if not passed:
            raise SystemExit("The test suite failed. Not building a release.")
    for needed in ("PyInstaller", "PySide6", "certifi"):
        import importlib.util
        if importlib.util.find_spec(needed) is None:
            raise SystemExit(f"{needed} is not installed.\n"
                             "    python -m pip install --user pyinstaller pyside6 certifi")
    print(f"Building {NAME} {APP_VERSION_V3} ...")
    run(pyinstaller_cmd())
    folder = os.path.join(DIST, NAME)
    exe = os.path.join(folder, NAME + (".exe" if WINDOWS else ""))
    if not os.path.exists(exe):
        raise SystemExit(f"PyInstaller reported success but {exe} is missing")
    freed = prune(os.path.join(folder, "_internal"))
    if not WINDOWS:
        print("  host font stack left out:", ", ".join(strip_host_font_stack(os.path.join(folder, "_internal"))) or "none bundled")
    print(f"\n  {exe}\n  folder: {folder_size(folder) / 1048576:.0f} MB "
          f"(left out {freed / 1048576:.0f} MB Qt never loads)")
    if "--no-test" not in sys.argv:
        rep = self_test(exe)
        print(json.dumps(rep, indent=1))
        if rep["problems"] or rep["exit_code"]:
            raise SystemExit("the self-test failed")
        print("  self-test: passed")
    if "--setup" in sys.argv:
        if not WINDOWS:
            raise SystemExit("--setup builds the Windows installer, on Windows.")
        print(f"\n  {build_setup(folder)}")


if __name__ == "__main__":
    main()
