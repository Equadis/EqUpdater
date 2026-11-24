"""EqUpdater 3.0 (proof of concept) entry point.

    python -m equpdater.qtui

Start-up order is deliberate: the window is created from the skin and the
*cached* news immediately; everything that can be slow (the news refresh,
launch-file detection) is queued after the first frame and runs on workers.

Test/benchmark hooks (environment, all optional):
    EQU3_DATA_DIR        use this folder instead of <app data>/v3
    EQU3_MARKS=file      write start-up timing marks to this JSON file
    EQU3_EXIT_AFTER_MS   quit after this long (benchmarks)
    EQU3_NEWS_FIXTURE    serve news from a JSON fixture (no network)
    EQU3_FAKE_SITE       same fixture, served as forum HTML to the real parsers
    EQU3_OFFLINE=1       every request fails (offline behaviour)
    EQU3_SKIN=<id>       override the saved skin for this run
"""

from __future__ import annotations

import json
import os
import sys
import time

_T0 = time.perf_counter()
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QTimer, QUrl                      # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon             # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine              # noqa: E402

from .. import branding                                      # noqa: E402
from ..core import settings as core_settings                 # noqa: E402
from ..core.launch import LaunchService                      # noqa: E402
from ..core.news_service import Fetcher                      # noqa: E402
from ..core.tasks import TaskManager                         # noqa: E402
from . import bridge                                         # noqa: E402

QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")


class OfflineFetcher:
    requests = 0

    def listing(self, forum, start=0):
        raise OSError("offline")

    first_post = feed = lambda self, *a, **k: (_ for _ in ()).throw(OSError("offline"))


def build(argv=None):
    """Create everything and return (app, engine, parts). No window shown yet
    beyond what the QML itself declares."""
    marks = {"process_to_main_ms": round((time.perf_counter() - _T0) * 1000)}
    data = os.environ.get("EQU3_DATA_DIR")
    settings = core_settings.open_settings(data)
    if os.environ.get("EQU3_SKIN"):
        settings.data["skin"] = os.environ["EQU3_SKIN"]
    news_cache = core_settings.open_news_cache(data)

    app = QGuiApplication.instance() or QGuiApplication(argv or sys.argv)
    app.setApplicationName("EqUpdater")
    icon = os.path.join(branding.app_dir(), "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    dispatcher = bridge.Dispatcher()
    tasks = TaskManager(4, deliver=dispatcher.post)

    manager = bridge.SkinManager(os.path.join(branding.app_dir(), "skins"),
                                 core_settings.skins_dir() if not data
                                 else os.path.join(data, "skins"), settings)
    skin_bridge = bridge.SkinBridge(manager)
    app_bridge = bridge.AppBridge(settings)
    settings_bridge = bridge.SettingsBridge(settings, manager, skin_bridge,
                                            app_bridge)

    fixture = os.environ.get("EQU3_NEWS_FIXTURE")
    site = os.environ.get("EQU3_FAKE_SITE")
    if site:                                   # real parsers, fake forum
        from .testing import FakeSiteOpener
        opener = FakeSiteOpener(site)
        fetcher = Fetcher(opener=lambda req, timeout: opener(req, timeout))
        fetcher.site = opener
    elif os.environ.get("EQU3_OFFLINE") == "1":
        fetcher = OfflineFetcher()
    elif fixture:
        from .testing import FixtureFetcher
        fetcher = FixtureFetcher(fixture)
    else:
        fetcher = Fetcher()
    news_bridge = bridge.NewsBridge(news_cache, tasks, fetcher, settings)

    launch_service = LaunchService(settings)
    launch_bridge = bridge.LaunchBridge(launch_service, tasks, app_bridge)

    gd = settings.get("game_dir") or ""
    if gd and os.path.isdir(gd):
        app_bridge.setStatus(gd, False)
    else:
        app_bridge.setStatus("No game folder found", True)

    engine = QQmlApplicationEngine()
    engine.addImportPath(QML_DIR)
    ctx = engine.rootContext()
    for name, obj in (("app", app_bridge), ("skin", skin_bridge),
                      ("settings", settings_bridge), ("news", news_bridge),
                      ("launch", launch_bridge)):
        ctx.setContextProperty(name, obj)
    marks["objects_ready_ms"] = round((time.perf_counter() - _T0) * 1000)

    parts = dict(settings=settings, tasks=tasks, news=news_bridge,
                 launch=launch_bridge, app=app_bridge, skin=skin_bridge,
                 settings_bridge=settings_bridge, marks=marks, fetcher=fetcher,
                 dispatcher=dispatcher, manager=manager, cache=news_cache)
    return app, engine, parts


def run(argv=None) -> int:
    app, engine, p = build(argv)
    marks = p["marks"]
    engine.load(QUrl.fromLocalFile(os.path.join(QML_DIR, "Main.qml")))
    if not engine.rootObjects():
        print("EqUpdater: the interface could not be loaded", file=sys.stderr)
        return 1
    win = engine.rootObjects()[0]
    marks["qml_loaded_ms"] = round((time.perf_counter() - _T0) * 1000)

    def first_frame():
        if "first_frame_ms" not in marks:
            marks["first_frame_ms"] = round((time.perf_counter() - _T0) * 1000)
            win.frameSwapped.disconnect(first_frame)
            QTimer.singleShot(0, interactive)
    win.frameSwapped.connect(first_frame)

    def interactive():
        # Cached news is already in the models (they sync in the constructor);
        # slow work starts only now, after the first frame.
        marks["interactive_ms"] = round((time.perf_counter() - _T0) * 1000)
        marks["epoch_interactive"] = time.time()    # for cross-process timing
        p["news"].start()
        p["launch"].refresh()
        if os.environ.get("EQU3_OPEN"):             # QA/docs: show a panel
            QTimer.singleShot(800, lambda: p["app"].debugAction.emit(os.environ["EQU3_OPEN"]))
        out = os.environ.get("EQU3_MARKS")
        if out:
            with open(out, "w") as f:
                json.dump(marks, f)
    p["app"].on_window_shown = lambda: marks.setdefault(
        "window_shown_ms", round((time.perf_counter() - _T0) * 1000))

    shot = os.environ.get("EQU3_SCREENSHOT")
    if shot:
        # "path.png" after EQU3_SHOT_AFTER_MS (default 2500): QA and docs.
        QTimer.singleShot(int(os.environ.get("EQU3_SHOT_AFTER_MS", "2500")),
                          lambda: p["app"].screenshotRequested.emit(shot))
    if os.environ.get("EQU3_EXIT_AFTER_MS"):
        QTimer.singleShot(int(os.environ["EQU3_EXIT_AFTER_MS"]), app.quit)
    code = app.exec()
    p["settings"].flush()
    p["cache"].flush()
    p["tasks"].shutdown()
    return code


if __name__ == "__main__":
    sys.exit(run())
