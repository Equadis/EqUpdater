"""Phase 7 measurements: start-up, memory and idle CPU for every skin and page.

    python tools/bench/phase7_bench.py --out bench-results/phase7.json [--runs 3] [--exe dist/EqUpdater3/EqUpdater3.exe]

One real process per sample (source tree, or the packaged exe with --exe), a
throwaway data folder, no network (EQU3_OFFLINE), the news fixture, 12 fake
addons. Per skin: time to first frame and to interactive (EQU3_MARKS), then
resident memory and CPU of the process tree after a settle period. Per page:
memory after opening that page and idling. Keep the machine idle while it runs.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SKINS = ("bubbles", "classic-2004", "minimal-dark", "minimal-light")
PAGES = ("news", "tweaks", "addons", "mods", "mpq")


def tree(proc):
    try:
        return [proc] + proc.children(recursive=True)
    except psutil.Error:
        return [proc]


def sample(proc, seconds):
    """(rss MB, cpu % of one core) of the process tree over ``seconds``."""
    for p in tree(proc):
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(seconds)
    rss = cpu = 0.0
    for p in tree(proc):
        try:
            rss += p.memory_info().rss
            cpu += p.cpu_percent(None)
        except psutil.Error:
            pass
    return rss / 1048576, cpu


def launch(skin, page, exe, fixture, settle, keep_open=True):
    data = tempfile.mkdtemp(prefix="equ3-p7-")
    marks = os.path.join(data, "marks.json")
    env = dict(os.environ, EQU3_DATA_DIR=data, EQU3_MARKS=marks, EQU3_SKIN=skin, EQU3_NO_SETUP="1",
               EQUPDATER_NO_LANGUAGE_PROMPT="1", EQU3_OFFLINE="1", EQU3_FAKE_ADDONS="12",
               EQU3_NEWS_FIXTURE=fixture, EQU3_OPEN=page)
    env.pop("EQU3_OFFLINE")                       # the news fixture must be served
    env["EQUPDATER_OFFLINE"] = "1"                # ...but nothing may reach a real server
    cmd = [exe] if exe else [sys.executable, "-m", "equpdater.qtui"]
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=ROOT if not exe else os.path.dirname(exe), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ps = psutil.Process(proc.pid)
        end = time.time() + 30
        while not os.path.exists(marks) and time.time() < end:
            time.sleep(0.05)
        try:
            with open(marks) as f:
                m = json.load(f)
        except (OSError, ValueError):
            m = {}
        time.sleep(settle)
        rss, cpu = sample(ps, 3.0)
        return {"first_frame_ms": m.get("first_frame_ms"), "interactive_ms": m.get("interactive_ms"),
                "wall_to_marks_ms": round((os.path.getmtime(marks) - t0) * 1000) if os.path.exists(marks) else None,
                "rss_mb": round(rss, 1), "cpu_pct": round(cpu, 2)}
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def med(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.median(vals), 1) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--exe")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--fixture", default=os.path.join(ROOT, "bench-results", "news-fixture.json"))
    ap.add_argument("--skins", nargs="*", default=list(SKINS))
    ap.add_argument("--pages", nargs="*", default=list(PAGES))
    a = ap.parse_args()
    if not os.path.exists(a.fixture):
        subprocess.check_call([sys.executable, os.path.join(HERE, "make_news_fixture.py"), a.fixture])
    out = {"machine": {"cpus": psutil.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1)},
           "target": a.exe or "source", "skins": {}, "pages": {}}
    for skin in a.skins:
        rows = [launch(skin, "news", a.exe, a.fixture, a.settle) for _ in range(a.runs)]
        out["skins"][skin] = {k: med(rows, k) for k in ("first_frame_ms", "interactive_ms", "rss_mb", "cpu_pct")}
        print(skin, out["skins"][skin], flush=True)
    for page in a.pages:
        rows = [launch(SKINS[0], page, a.exe, a.fixture, a.settle) for _ in range(max(1, a.runs - 1))]
        out["pages"][page] = {k: med(rows, k) for k in ("rss_mb", "cpu_pct")}
        print(page, out["pages"][page], flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
