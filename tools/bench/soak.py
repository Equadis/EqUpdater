"""Soak test: drive the real interface for minutes and watch for leaks.

    python tools/bench/soak.py --minutes 4 --out bench-results/soak.json

Every few hundred milliseconds one scripted action runs: switch page, switch
skin, open and close Settings / Diagnostics / the server popover / the custom
mod dialog, refresh the footer chip. Memory (RSS), Python object count, QML
object count, threads and (Windows) handle count are sampled every 5 s. The
run fails (exit 1) when, after a warm-up, memory grows by more than
``--max-growth-mb`` or handles/threads keep climbing.

The news fixture and a fake server feed are used; nothing leaves the machine.
"""

import argparse
import gc
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=3.0)
    ap.add_argument("--warmup", type=float, default=0.25, help="fraction of the run before the baseline")
    ap.add_argument("--max-growth-mb", type=float, default=25.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-anim", action="store_true", help="turn the animated background off")
    ap.add_argument("--skin-set", nargs="*", help="cycle only these skins")
    ap.add_argument("--only", nargs="*", help="action groups: tab skin settings server diag custom fetch")
    a = ap.parse_args()

    import psutil
    data = tempfile.mkdtemp(prefix="equ3-soak-")
    fx = os.path.join(ROOT, "bench-results", "news-fixture.json")
    srv = os.path.join(data, "server.json")
    now = time.time()
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - s))   # noqa: E731
    with open(srv, "w") as f:
        json.dump({"live": {"all": {"timestamp": iso(60), "online": 1200, "source": "census"}},
                   "status": {"schema": 1, "sample": {"timestamp": iso(120), "website": "available",
                              "tcp": "reachable", "auth": "responsive", "latency_ms": 700,
                              "realms": {"cthun": "online", "nzoth": "online", "yshaarj": "online"}}}}, f)
    os.environ.update({"EQU3_DATA_DIR": data, "EQU3_NEWS_FIXTURE": fx, "EQU3_SERVER_FIXTURE": srv,
                       "EQU3_NO_SETUP": "1", "EQUPDATER_NO_LANGUAGE_PROMPT": "1",
                       "EQUPDATER_OFFLINE": "1", "EQU3_FAKE_ADDONS": "60"})
    from PySide6.QtCore import QCoreApplication, QMetaObject, QUrl
    from equpdater.qtui import main as m

    app, engine, p = m.build([])
    engine.load(QUrl.fromLocalFile(os.path.join(m.QML_DIR, "Main.qml")))
    win = engine.rootObjects()[0]
    win.setProperty("width", 1000)
    win.setProperty("height", 700)
    me = psutil.Process()
    skins = a.skin_set or sorted(p["manager"].skins)
    pages = ["news", "tweaks", "addons", "mods", "mpq"]
    p["server"].start()
    if a.no_anim:
        p["settings"].set("animated_background", False)
        p["app"].animationChanged.emit()

    def handles():
        try:
            return me.num_handles() if os.name == "nt" else me.num_fds()
        except psutil.Error:
            return None

    def qml_objects():
        try:
            from PySide6.QtCore import QObject
            return len(win.findChildren(QObject))
        except Exception:                      # noqa: BLE001
            return None

    groups = {
        "tab": lambda i: setattr(p["app"], "tab", pages[(i // 3) % len(pages)]),
        "skin": lambda i: p["settings_bridge"].setSkin(skins[(i // 12) % len(skins)]),
        "settings": lambda i: QMetaObject.invokeMethod(win, "openSettings"),
        "server": lambda i: p["app"].debugAction.emit("server"),
        "diag": lambda i: p["diag"].openView("attempt") if i % 2 else p["diag"].close(),
        "custom": lambda i: p["custom"].open("mod") if i % 2 else p["custom"].close(),
        "fetch": lambda i: p["server_feed"].fetch_once(),
    }
    chosen = [g for g in (a.only or groups) if g in groups]
    actions = [(lambda i, g=g: groups[g](i)) for g in chosen]
    actions = actions * 40

    samples, t0, n = [], time.time(), 0
    next_sample = t0
    end = t0 + a.minutes * 60
    while time.time() < end:
        actions[n % len(actions)](n)
        n += 1
        t = time.time()
        slice_end = t + 0.25
        while time.time() < slice_end:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        if time.time() >= next_sample:
            gc.collect()
            samples.append({"t_s": round(time.time() - t0), "rss_mb": round(me.memory_info().rss / 1048576, 1),
                            "py_objects": len(gc.get_objects()), "threads": me.num_threads(),
                            "handles": handles(), "qml_objects": qml_objects(), "actions": n})
            print(samples[-1], flush=True)
            next_sample += 5

    base = samples[int(len(samples) * a.warmup)]
    last = samples[-1]
    growth = last["rss_mb"] - base["rss_mb"]
    result = {"minutes": a.minutes, "actions": n, "baseline": base, "final": last,
              "rss_growth_mb": round(growth, 1),
              "py_object_growth": last["py_objects"] - base["py_objects"],
              "thread_growth": last["threads"] - base["threads"],
              "handle_growth": (last["handles"] - base["handles"]) if last["handles"] is not None else None,
              "samples": samples}
    problems = []
    if growth > a.max_growth_mb:
        problems.append(f"memory grew {growth:.1f} MB after warm-up")
    if result["thread_growth"] > 4:
        problems.append(f"{result['thread_growth']} more threads")
    if result["handle_growth"] is not None and result["handle_growth"] > 60:
        problems.append(f"{result['handle_growth']} more handles")
    result["problems"] = problems
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, indent=1))
    p["tasks"].shutdown()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
