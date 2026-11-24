"""3.0 scenarios on the real (GPU) scene graph, same intent as tk_wrapper.py.

    python tools/bench/qt_scenarios.py FIXTURE.json OUT.json [--skin ID]

Frame times are the gaps between consecutive ``frameSwapped`` signals while
the scene is driven continuously, so a missed 60 Hz frame shows as > 16.7 ms.
(2.x's equivalent is per-step cost: Tk has no frame clock. The two are
reported side by side, with that caveat, in PERFORMANCE.md.)

Latencies (expand, tab switch, skin switch) are action -> next presented frame.
"""

import json
import os
import statistics
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(here)))
fixture, out = sys.argv[1], sys.argv[2]
skin = sys.argv[sys.argv.index("--skin") + 1] if "--skin" in sys.argv else "bubbles"

import tempfile                                                # noqa: E402

data = tempfile.mkdtemp(prefix="equ3-bench-")
os.environ.update({"EQU3_DATA_DIR": data, "EQU3_FAKE_SITE": fixture,
                   "EQU3_SKIN": skin})
from PySide6 import QtQuick                                      # noqa: E402,F401
from PySide6.QtCore import QCoreApplication, QObject, QUrl     # noqa: E402
from equpdater.qtui import main as m                           # noqa: E402


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


def walk(item):
    yield item
    for ch in item.childItems():
        yield from walk(ch)


app, engine, p = m.build([])
engine.load(QUrl.fromLocalFile(os.path.join(m.QML_DIR, "Main.qml")))
win = engine.rootObjects()[0]
frames = []
swaps = {"n": 0}


def on_swap():
    swaps["n"] += 1
    frames.append(time.perf_counter())


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


def next_frame_ms(action):
    """Run ``action``; ms until the next frame is presented."""
    n0 = swaps["n"]
    t = time.perf_counter()
    action()
    until(lambda: swaps["n"] > n0, 2000)
    return (time.perf_counter() - t) * 1000


pump(300)
p["news"].start()
until(lambda: p["tasks"].active_count() == 0)
pump(400)
res = {"skin": skin}
lv = {n: next(i for i in walk(win.property("contentItem")) if i.objectName() == n)
      for n in ("announcementsList", "changelogList")}
svc = p["news"].service

# open several long posts so there is real text to scroll through
for key, br in (("announcements", p["news"].announcements), ("changelog", p["news"].changelog)):
    for iid in svc.visible_ids(key)[:4]:
        if not svc.entry(key, iid)["expanded"]:
            br.toggle(iid)
until(lambda: p["tasks"].active_count() == 0)
pump(500)
req0 = p["fetcher"].site.requests if hasattr(p["fetcher"], "site") else None

for name, view in lv.items():
    frames.clear()
    total = view.property("contentHeight") - view.property("height")
    steps = 240
    t_end = time.perf_counter() + 4.0                 # a 4 s down-and-up scroll
    t0 = time.perf_counter()
    while time.perf_counter() < t_end:
        ph = (time.perf_counter() - t0) / 4.0
        pos = (ph * 2 if ph < 0.5 else (1 - ph) * 2) * max(0, total) * 0.9
        view.setProperty("contentY", pos)
        QCoreApplication.processEvents()
        time.sleep(0.001)
    gaps = [(b - a) * 1000 for a, b in zip(frames, frames[1:])]
    res["scroll_" + name] = stats(gaps)
    res["scroll_" + name]["fps"] = round(len(frames) / 4.0, 1)
    res["scroll_" + name]["content_px"] = round(total)
    view.setProperty("contentY", 0)
    pump(200)

res["requests_during_scroll"] = (p["fetcher"].site.requests - req0) if req0 is not None else None

# expansion latency (cached text) - toggle a few posts repeatedly
lat = []
ids = svc.visible_ids("announcements")[:6]
for iid in ids * 3:
    lat.append(next_frame_ms(lambda i=iid: p["news"].announcements.toggle(i)))
res["expand_cached"] = stats(lat)

# tab switching: first visit builds the page, a revisit only shows it
first, again = [], []
for rnd in range(3):
    for tab in ("tweaks", "addons", "mods", "mpq", "news"):
        ms = next_frame_ms(lambda t=tab: setattr(p["app"], "tab", t))
        (first if rnd == 0 else again).append(ms)
res["tab_first_visit"], res["tab_revisit"] = stats(first), stats(again)

# resizing
frames.clear()
vals = []
for i in range(40):
    w, h = 1000 + (i % 20) * 12, 700 + (i % 20) * 8
    vals.append(next_frame_ms(lambda w=w, h=h: (win.setProperty("width", w),
                                               win.setProperty("height", h))))
res["resize"] = stats(vals)

# skin switching, live
sw = []
for sid in ("minimal-dark", "bubbles", "minimal-dark", "bubbles"):
    sw.append(next_frame_ms(lambda s=sid: p["settings_bridge"].setSkin(s)))
res["skin_switch"] = stats(sw)

res["python_ms"] = p["marks"]
res["network_requests_total"] = p["fetcher"].site.requests if hasattr(p["fetcher"], "site") else None
with open(out, "w") as f:
    json.dump(res, f, indent=1)
p["tasks"].shutdown()
os._exit(0)
