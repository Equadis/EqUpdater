"""Addons page benchmark: 10, 50, 100 and 250 addons, real (GPU) scene.

    python tools/bench/addons_bench.py --out bench-results/addons.json [--sizes 10 50 100 250]

One process per size (clean memory). Synthetic addons (no disk, no network):
``qtui.testing.fake_addon_status``. Measured:

  open_ms        switching to the Addons tab for the first time -> first frame
                 with rows on screen
  build_ms       the Python side: filter + sort + row building for the page
  items_alive    delegate items that exist (virtualisation: should not grow
                 with N)
  scroll_*       frame gaps while scrolling the whole list down and up (4 s)
  filter_ms      typing a query -> filtered rows presented (Python filter +
                 keyed model update + frame), and clearing it again
  update_ms      ONE addon changes state -> its row repainted (rows built on a
                 worker, model diffed by key, one dataChanged, next frame)
  rss_mb         process memory before the page / after scrolling it
  rows_changed   dataChanged events caused by that single update (must be 1)
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else None


def stats(v):
    return {"n": len(v), "p50_ms": round(statistics.median(v), 2) if v else None,
            "p95_ms": round(pct(v, 0.95), 2) if v else None,
            "p99_ms": round(pct(v, 0.99), 2) if v else None,
            "max_ms": round(max(v), 2) if v else None,
            "over_16ms": sum(1 for x in v if x > 16.7),
            "over_33ms": sum(1 for x in v if x > 33.4)}


def worker(n, skin):
    import tempfile
    sys.path.insert(0, ROOT)
    os.environ.update({"EQU3_DATA_DIR": tempfile.mkdtemp(prefix="equ3-ab-"),
                       "EQU3_FAKE_ADDONS": str(n), "EQU3_SKIN": skin,
                       "EQU3_OFFLINE": "1", "EQUPDATER_OFFLINE": "1"})
    import psutil
    from PySide6 import QtQuick                      # noqa: F401
    from PySide6.QtCore import QCoreApplication, QUrl
    from equpdater.qtui import main as m

    app, engine, p = m.build([])
    engine.load(QUrl.fromLocalFile(os.path.join(m.QML_DIR, "Main.qml")))
    win = engine.rootObjects()[0]
    swaps = {"n": 0}
    stamps = []

    def on_swap():
        swaps["n"] += 1
        stamps.append(time.perf_counter())
    win.frameSwapped.connect(on_swap)

    def pump(ms):
        end = time.perf_counter() + ms / 1000
        while time.perf_counter() < end:
            QCoreApplication.processEvents()
            time.sleep(0.001)

    def until(cond, limit=10000):
        end = time.perf_counter() + limit / 1000
        while time.perf_counter() < end:
            QCoreApplication.processEvents()
            if cond():
                return True
            time.sleep(0.001)
        return False

    def next_frame_ms(action, ready=None):
        n0 = swaps["n"]
        t = time.perf_counter()
        action()
        if ready:
            until(ready, 5000)
        n1 = swaps["n"]
        until(lambda: swaps["n"] > n1 or swaps["n"] > n0 + 1, 2000)
        return (time.perf_counter() - t) * 1000

    def walk(item):
        yield item
        for c in item.childItems():
            yield from walk(c)

    proc = psutil.Process()
    pump(800)
    res = {"n": n, "skin": skin, "rss_before_mb": round(proc.memory_info().rss / 1048576)}
    svc, lm = p["addons_service"], p["addons"].model

    t = time.perf_counter()
    svc.build_items(svc._snapshot(), "", {})
    res["build_ms"] = round((time.perf_counter() - t) * 1000, 2)

    res["open_ms"] = round(next_frame_ms(lambda: setattr(p["app"], "tab", "addons"),
                                         lambda: lm.rowCount() > 0), 1)
    pump(500)
    content = next(i for i in walk(win.property("contentItem"))
                   if i.objectName() == "addonsList")
    res["rows"] = lm.rowCount()
    res["items_alive"] = len([c for c in content.property("contentItem").childItems()
                              if c.property("height")])

    # scroll the whole list down and up, 4 s
    stamps.clear()
    total = content.property("contentHeight") - content.property("height")
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 4.0:
        ph = (time.perf_counter() - t0) / 4.0
        content.setProperty("contentY", (ph * 2 if ph < 0.5 else (1 - ph) * 2) * max(0, total))
        QCoreApplication.processEvents()
        time.sleep(0.001)
    gaps = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:])]
    res["scroll"] = stats(gaps)
    res["scroll"]["content_px"] = round(total)
    res["scrollable"] = total > 50
    content.setProperty("contentY", 0)
    pump(300)

    # filter: typing then clearing
    st = p["host"]._addons_status
    word = next(iter(st["addons"]))[:5].lower()
    def apply_filter(q):
        p["addons_service"].set_filter(q)
    res["filter_ms"] = round(next_frame_ms(lambda: apply_filter(word),
                                           lambda: lm.rowCount() < res["rows"]), 1)
    res["filter_rows"] = lm.rowCount()
    res["filter_clear_ms"] = round(next_frame_ms(lambda: apply_filter(""),
                                                 lambda: lm.rowCount() == res["rows"]), 1)

    # one addon changes
    pump(300)
    before = dict(lm.stats)
    # a row that is on screen (an off-screen row legitimately repaints nothing)
    from PySide6.QtCore import Qt
    visible_ids = [lm.index(i).data(Qt.ItemDataRole.UserRole + 3) for i in range(min(lm.rowCount(), 8))]
    folder = next(f for f in visible_ids
                  if f in st["addons"] and st["addons"][f]["status"] == "upToDate")
    def change():
        st["addons"][folder]["status"] = "updateAvailable"
        svc.rebuild()
    res["update_ms"] = round(next_frame_ms(change, lambda: lm.stats["changed"] > before["changed"]), 1)
    res["rows_changed"] = lm.stats["changed"] - before["changed"]
    res["structure_changes"] = (lm.stats["inserted"] - before["inserted"]) + \
        (lm.stats["removed"] - before["removed"]) + (lm.stats["resets"] - before["resets"])

    pump(500)
    res["rss_after_mb"] = round(proc.memory_info().rss / 1048576)
    print("RESULT " + json.dumps(res), flush=True)
    os._exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--sizes", nargs="*", type=int, default=[10, 50, 100, 250])
    ap.add_argument("--skin", default="bubbles")
    ap.add_argument("--worker", type=int)
    a = ap.parse_args()
    if a.worker:
        worker(a.worker, a.skin)
        return
    results = []
    for n in a.sizes:
        r = subprocess.run([sys.executable, __file__, "--worker", str(n), "--skin", a.skin],
                           capture_output=True, text=True, timeout=240, cwd=ROOT)
        line = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
        if line:
            results.append(json.loads(line[0][7:]))
            print(line[0][7:], flush=True)
        else:
            print(n, "FAILED", r.stderr[-500:], flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"skin": a.skin, "results": results}, f, indent=1)


if __name__ == "__main__":
    main()
