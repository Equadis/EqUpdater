"""Run the 2.x (Tk) launcher under measurement, against the shared fake forum.

    python tk_wrapper.py REPO_OF_2X FIXTURE.json OUT.json [--scenarios]

The 2.x code is not modified: this imports it, swaps only its forum opener for
``fakesite.FakeSiteOpener`` (same bytes the 3.0 run parses), and watches the
window. Isolation (LOCALAPPDATA, HOME) is the orchestrator's job.

Marks (epoch seconds, so the orchestrator can subtract its own start time):
  window_mapped   first <Map> of the main window
  interactive     mapped AND the News lists have rows (cached or fetched)
Scenarios (--scenarios): per-step cost of scrolling both news lists, expanding
a post, switching tabs, and resizing - each step drives Tk to a finished
paint (``update()``), so the numbers are CPU-bound frame costs.
"""

import importlib.util
import json
import os
import statistics
import sys
import time

root, fixture, out = sys.argv[1:4]
scen = "--scenarios" in sys.argv
os.environ["EQUPDATER_NO_LANGUAGE_PROMPT"] = "1"
os.environ["EQUPDATER_NO_MIGRATION_PROMPT"] = "1"
os.environ["EQUPDATER_NO_DEFENDER_PROMPT"] = "1"
sys.path.insert(0, root)
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fakesite", os.path.join(here, "fakesite.py"))
fakesite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fakesite)

T0 = time.time()
from equpdater import app as m                                  # noqa: E402

site = fakesite.FakeSiteOpener(fixture)
m._news_open = lambda req, timeout: site(req, timeout)
marks = {"python_import_done": time.time()}
a = m.EqUpdaterApp()
marks["constructed"] = time.time()


def on_map(_e):
    marks.setdefault("window_mapped", time.time())


a.bind("<Map>", on_map)
state = {"done": False}


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


def step(fn):
    t = time.perf_counter()
    fn()
    a.update()
    return (time.perf_counter() - t) * 1000


def scenarios():
    res = {}
    a.update()
    # open the first few posts so there is real text to scroll through
    for key, toggle, opened in (
            ("ann", a._ann_toggle, lambda: a._ann()["open"]),
            ("patch", a._patch_toggle, lambda: getattr(a, "_patch_open", None) or set())):
        order = list(a._acc_lists[key]["order"])
        for iid in order[:4]:
            if iid not in opened():
                try:
                    toggle(iid)
                except Exception:
                    pass
    for _ in range(40):
        a.update()
        time.sleep(0.02)
    for key in ("ann", "patch"):
        canvas = a._acc_canvas[key]
        v = []
        for i in range(240):                      # down, then back up
            frac = (i / 120) if i < 120 else (2 - i / 120)
            v.append(step(lambda f=frac: canvas.yview_moveto(f * 0.9)))
        res["scroll_" + key] = stats(v)
    # expansion latency (cached text: no network)
    lat = []
    order = list(a._acc_lists["ann"]["order"])
    for iid in order[:6] * 3:
        lat.append(step(lambda i=iid: a._ann_toggle(i)))
    res["expand_ann"] = stats(lat)
    # tab switching (visited tabs)
    tabs = ["NEWS", "TWEAKS", "ADDONS", "MODS", "MPQ"]
    first, again = [], []
    for t in tabs * 3:
        ms = step(lambda tt=t: a._switch_tab(tt))
        (again if len(first) >= len(tabs) else first).append(ms)
    res["tab_first_visit"], res["tab_revisit"] = stats(first), stats(again)
    # resize
    a._switch_tab("NEWS")
    a.update()
    v = []
    for i in range(40):
        w, h = 1000 + (i % 20) * 12, 700 + (i % 20) * 8
        v.append(step(lambda w=w, h=h: a.geometry(f"{w}x{h}")))
    res["resize"] = stats(v)
    res["network_requests"] = site.requests
    return res


def check():
    if "window_mapped" in marks and "interactive" not in marks:
        lists = getattr(a, "_acc_lists", {})
        if lists.get("ann", {}).get("order") and lists.get("patch", {}).get("order"):
            marks["interactive"] = time.time()
            marks["requests_at_interactive"] = site.requests
            a.after(200, finish)
    if "interactive" not in marks and time.time() - T0 > 25:
        marks["timeout"] = True
        finish()
    else:
        a.after(5, check)


def finish():
    if state["done"]:
        return
    state["done"] = True
    marks["T0"] = T0
    if scen:
        marks["scenarios"] = scenarios()
    marks["requests_total"] = site.requests
    with open(out, "w") as f:
        json.dump(marks, f)
    # stay alive so the orchestrator can sample idle CPU / memory
    a.after(int(os.environ.get("BENCH_LINGER_MS", "0")) or 1, a.quit if not os.environ.get("BENCH_LINGER_MS") else (lambda: None))


a.after(5, check)
a.mainloop()
