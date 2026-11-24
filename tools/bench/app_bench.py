"""Compare the 2.x launcher with the 3.0 proof of concept, same machine, same
fake forum, same isolation.

    python tools/bench/app_bench.py --old D:/EqUpdater-queue --out bench-results/compare.json
        [--runs 5] [--idle-s 10] [--only startup scenarios]

``--old`` is a checkout of the 2.x release being compared (v2.1.4 = 496fa67),
run from source; ``--new`` defaults to this repository. Nothing touches real
user data: each run gets its own LOCALAPPDATA/HOME and an empty game folder.

Measured per run (see PERFORMANCE.md for definitions and caveats):
  visible_ms      process start -> a visible top-level window of that process
                  exists (polled from outside, every 4 ms, identical for both)
  interactive_ms  process start -> window mapped AND news rows on screen
  idle_cpu_pct    CPU of the process tree over --idle-s after interactive
  rss_mb          resident memory of the process tree at the end of that window
  requests        forum requests made up to interactive
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
NEW_ROOT = os.path.dirname(os.path.dirname(HERE))


def find_window(pid_set):
    """True once any visible, sizeable top-level window belongs to pid_set."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pid_set:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 300 and r.bottom - r.top > 300:
                found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return bool(found)


def tree_pids(proc):
    try:
        return {proc.pid} | {c.pid for c in proc.children(recursive=True)}
    except psutil.Error:
        return {proc.pid}


def tree_stats(proc, seconds):
    procs = [proc] + proc.children(recursive=True)
    for q in procs:
        q.cpu_percent(None)
    time.sleep(seconds)
    cpu = rss = 0.0
    for q in procs:
        try:
            cpu += q.cpu_percent(None)
            rss += q.memory_info().rss
        except psutil.Error:
            pass
    return cpu, rss / 1048576


def isolated(base, which, animated, seed_tk):
    home = os.path.join(base, "home")
    local = os.path.join(base, "local")
    game = os.path.join(base, "game")
    for d in (home, local, game):
        os.makedirs(d, exist_ok=True)
    env = dict(os.environ)
    env.update({"LOCALAPPDATA": local, "APPDATA": local, "USERPROFILE": home,
                "HOME": home, "PYTHONDONTWRITEBYTECODE": "1",
                "QT_QUICK_CONTROLS_STYLE": "Basic"})
    for k in [k for k in env if k.startswith("EQU3_")]:
        del env[k]
    if which == "old":
        cfg = os.path.join(local, "EqUpdater", "config.json")
        if seed_tk and not os.path.exists(cfg):
            os.makedirs(os.path.dirname(cfg), exist_ok=True)
            with open(cfg, "w") as f:
                json.dump({"out_dir": game, "app_version": "2.1.4",
                           "animated_background": animated, "language": "enUS"}, f)
    else:
        v3 = os.path.join(local, "EqUpdater", "v3")
        os.makedirs(v3, exist_ok=True)
        sp = os.path.join(v3, "settings.json")
        if not os.path.exists(sp):
            with open(sp, "w") as f:
                json.dump({"_schema": 1, "game_dir": game, "imported_from_2x": True,
                           "animated_background": animated, "skin": "bubbles"}, f)
        env["EQU3_DATA_DIR"] = v3
    return env


def run_once(which, root, fixture, base, animated=True, idle_s=10, keep=False):
    env = isolated(base, which, animated, True)
    marks_file = os.path.join(base, f"marks-{which}.json")
    if os.path.exists(marks_file):
        os.remove(marks_file)
    if which == "old":
        env["BENCH_LINGER_MS"] = "999999"
        cmd = [sys.executable, os.path.join(HERE, "tk_wrapper.py"), root, fixture, marks_file]
    else:
        env["EQU3_FAKE_SITE"] = fixture
        env["EQU3_MARKS"] = marks_file
        cmd = [sys.executable, "-m", "equpdater.qtui"]
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=root if which == "new" else HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ps = psutil.Process(proc.pid)
    visible = None
    deadline = time.time() + 40
    while time.time() < deadline and proc.poll() is None:
        if visible is None and find_window(tree_pids(ps)):
            visible = (time.time() - t0) * 1000
        if os.path.exists(marks_file) and visible is not None:
            break
        time.sleep(0.004)
    time.sleep(0.4)
    res = {"which": which, "visible_ms": round(visible) if visible else None}
    try:
        with open(marks_file) as f:
            marks = json.load(f)
        if which == "old":
            res["interactive_ms"] = round((marks["interactive"] - t0) * 1000)
            res["requests"] = marks.get("requests_at_interactive")
        else:
            res["interactive_ms"] = round((marks["epoch_interactive"] - t0) * 1000)
            res["python_marks"] = marks
    except (OSError, KeyError, ValueError):
        res["interactive_ms"] = None
    if idle_s and proc.poll() is None:
        time.sleep(3)                                  # let start-up work settle
        res["idle_cpu_pct"], res["rss_mb"] = [round(x, 2) for x in tree_stats(ps, idle_s)]
    for q in [ps] + ps.children(recursive=True):
        try:
            q.kill()
        except psutil.Error:
            pass
    proc.wait(timeout=10)
    return res


def med(rows, key):
    v = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.median(v), 1) if v else None


def summarise(rows):
    return {k: med(rows, k) for k in ("visible_ms", "interactive_ms", "idle_cpu_pct",
                                      "rss_mb", "requests")} | {"runs": len(rows)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", default=NEW_ROOT)
    ap.add_argument("--fixture", default=os.path.join(NEW_ROOT, "bench-results", "news-fixture.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--idle-s", type=int, default=10)
    ap.add_argument("--only", nargs="*", default=["startup", "scenarios"])
    ap.add_argument("--targets", nargs="*", default=["old", "new"])
    a = ap.parse_args()
    result = {"machine": {"cpu": psutil.cpu_count(), "python": sys.version.split()[0],
                          "ram_gb": round(psutil.virtual_memory().total / 2**30, 1)},
              "old": a.old, "new": a.new}
    targets = tuple(t for t in (("old", a.old), ("new", a.new)) if t[0] in a.targets)
    if "startup" in a.only:
        for which, root in targets:
            for animated in (True, False):
                label = f"{which}_anim_{'on' if animated else 'off'}"
                base = tempfile.mkdtemp(prefix=f"equ-bench-{which}-")
                rows = []
                first = run_once(which, root, a.fixture, base, animated, idle_s=0)
                result[label + "_first_launch_empty_cache"] = first
                for _ in range(a.runs):                      # cached: data dir kept
                    rows.append(run_once(which, root, a.fixture, base, animated,
                                         idle_s=a.idle_s))
                result[label + "_cached"] = summarise(rows)
                result[label + "_cached_rows"] = rows
                shutil.rmtree(base, ignore_errors=True)
                print(label, result[label + "_cached"], flush=True)
    if "scenarios" in a.only:
        for which, root in targets:
            base = tempfile.mkdtemp(prefix=f"equ-bench-{which}-s-")
            env = isolated(base, which, False, True)
            outf = os.path.join(base, "scen.json")
            if which == "old":
                cmd = [sys.executable, os.path.join(HERE, "tk_wrapper.py"), root,
                       a.fixture, outf, "--scenarios"]
                cwd = HERE
            else:
                cmd = [sys.executable, os.path.join(HERE, "qt_scenarios.py"), a.fixture, outf]
                cwd = root
                env.pop("EQU3_DATA_DIR", None)
            p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                               timeout=300)
            try:
                with open(outf) as f:
                    d = json.load(f)
                result["scenarios_" + which] = d.get("scenarios", d)
            except (OSError, ValueError):
                result["scenarios_" + which] = {"error": (p.stderr or "")[-600:]}
            shutil.rmtree(base, ignore_errors=True)
            print("scenarios", which, "done", flush=True)
    with open(a.out, "w") as f:
        json.dump(result, f, indent=1)


if __name__ == "__main__":
    main()
