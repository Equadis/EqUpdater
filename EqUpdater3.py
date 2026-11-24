"""Frozen-build entry point of EqUpdater 3.0 (the Qt Quick front end).

PyInstaller runs its entry script as ``__main__`` with no parent package, so
this starts the package by absolute import (as ``EqUpdater.py`` does for 2.x).

``--self-test=<file>`` checks a packaged build without a person at it: it
writes what the build carries (version, skins, fonts, QML, icon, TLS bundle)
to <file> as JSON and exits 0 when all of it is there. ``--gui`` also loads
the real interface (Qt's offscreen platform, a throwaway data folder) and
fails on any QML error. CI and ``build.py --v3`` run it on the built folder.
"""

import json
import os
import sys


def _self_test(out_path: str, gui: bool) -> int:
    import tempfile
    from equpdater import branding
    from equpdater.core import skin as skin_mod
    problems, report = [], {}
    res = branding.resource_dir()
    report["resource_dir"] = res
    skins = os.path.join(res, "skins")
    found = sorted(n for n in os.listdir(skins)
                   if os.path.isfile(os.path.join(skins, n, "manifest.json"))) \
        if os.path.isdir(skins) else []
    report["skins"] = found
    for need in ("bubbles", "minimal-dark"):
        if need not in found:
            problems.append(f"skin {need} is missing")
    fonts = os.path.join(res, "fonts")
    report["fonts"] = sorted(os.listdir(fonts)) if os.path.isdir(fonts) else []
    if not report["fonts"]:
        problems.append("no bundled fonts")
    from equpdater.qtui import main as qmain
    report["qml_main"] = os.path.isfile(os.path.join(qmain.QML_DIR, "Main.qml"))
    if not report["qml_main"]:
        problems.append("Main.qml is missing")
    report["icon"] = os.path.isfile(os.path.join(res, "icon.png"))
    if not report["icon"]:
        problems.append("icon.png is missing")
    from equpdater.qtui.bridge import APP_VERSION_V3
    report["version"] = APP_VERSION_V3
    try:
        from equpdater.core import net
        net.make_ssl_context()
        report["tls"] = True
    except Exception as e:                      # noqa: BLE001
        report["tls"] = False
        problems.append(f"TLS: {e}")
    try:
        import PySide6
        report["pyside6"] = PySide6.__version__
    except ImportError as e:
        problems.append(f"PySide6: {e}")
    if gui and not problems:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        data = tempfile.mkdtemp(prefix="equ3-selftest-")
        os.environ.update({"EQU3_DATA_DIR": data, "EQU3_NO_SETUP": "1",
                           "EQUPDATER_NO_LANGUAGE_PROMPT": "1", "EQU3_OFFLINE": "1"})
        messages = []
        from PySide6.QtCore import QUrl, qInstallMessageHandler
        qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))
        app, engine, p = qmain.build([])
        engine.load(QUrl.fromLocalFile(os.path.join(qmain.QML_DIR, "Main.qml")))
        report["gui_loaded"] = bool(engine.rootObjects())
        if not report["gui_loaded"]:
            problems.append("the interface did not load")
        messages = [m for m in messages
                    if "font directory" not in m and "no longer ships fonts" not in m]
        errs = [m for m in messages if "Error" in m or "Cannot" in m or "is not a type" in m
                or "module" in m and "not installed" in m]
        report["qml_errors"] = errs[:10]
        if errs:
            problems.append(f"{len(errs)} QML error(s)")
        import time as _t

        def settle(ms=150):
            end = _t.time() + ms / 1000
            while _t.time() < end:
                app.processEvents()
        for sid in sorted(p["manager"].skins):
            p["settings_bridge"].setSkin(sid)
            # every page and overlay, so a Qt module left out of the package
            # shows up here and not on a player's screen
            for target in ("news", "tweaks", "mods", "addons", "mpq", "settings",
                           "diag:attempt", "diag:scan", "diag:doctor"):
                p["app"].debugAction.emit(target)
                settle()
            for kind in ("mod", "addon"):
                p["custom"].open(kind)
                settle()
            p["custom"].close()
            p["setup"].open()
            settle()
            p["setup"].close()
            settle()
        report["pages_opened"] = True
        report["skins_switched"] = sorted(p["manager"].skins)
    report["problems"] = problems
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return 1 if problems else 0


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        if arg.startswith("--self-test="):
            sys.exit(_self_test(arg.split("=", 1)[1], "--gui" in sys.argv))
    from equpdater.qtui.main import run
    sys.exit(run())
