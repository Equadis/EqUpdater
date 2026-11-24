"""Animated-background benchmark: one candidate per process, same window.

    python tools/bench/bg_bench.py --assets DIR [--seconds 20] [--out FILE.json]

DIR holds the candidate files (see CANDIDATES). Each candidate runs in a fresh
process so memory numbers are not polluted by an earlier one. Measured, all for
this process only:

  first_frame_ms  process start -> first frame the scene shows
  cpu_pct         CPU time / wall time (% of one core) over the steady window
  rss_mb / peak   resident / peak working set at the end of the window
  gpu_pct         sum of "GPU Engine" utilisation counters for the pid (Windows)
  swaps_per_s     QQuickWindow.frameSwapped rate (how often the scene redraws)

The GPU counter is the Windows performance counter; elsewhere it is None.
"""

import argparse
import json
import os
import subprocess
import sys
import time

T0 = time.perf_counter()

CANDIDATES = {
    # name: (file, kind, options)
    "gif_cache":    ("bubbles.gif", "image", {"cache": True}),
    "gif_nocache":  ("bubbles.gif", "image", {"cache": False}),
    "webp_cache":   ("b400.webp", "image", {"cache": True}),
    "webp_nocache": ("b400.webp", "image", {"cache": False}),
    "mp4_400":      ("b400.mp4", "video", {}),
    "mp4_960":      ("b960.mp4", "video", {}),
    "webm_400":     ("b400.webm", "video", {}),
    "static_png":   ("pubbles.png", "static", {}),
}

QML_IMAGE = """
import QtQuick
Item {
  width: 1280; height: 720
  Rectangle { anchors.fill: parent; color: "#031a30" }
  AnimatedImage {
    anchors.fill: parent; source: "%(url)s"
    fillMode: Image.PreserveAspectCrop; smooth: true
    cache: %(cache)s; playing: true
    onFrameChanged: bench.frame()
    onStatusChanged: if (status === Image.Ready) bench.frame()
  }
}
"""
QML_STATIC = QML_IMAGE.replace("AnimatedImage", "Image").replace(
    "cache: %(cache)s; playing: true", "cache: true").replace(
    "onFrameChanged: bench.frame()\n", "")
QML_VIDEO = """
import QtQuick
import QtMultimedia
Item {
  width: 1280; height: 720
  Rectangle { anchors.fill: parent; color: "#031a30" }
  MediaPlayer {
    id: mp; source: "%(url)s"; loops: MediaPlayer.Infinite
    videoOutput: vo
    Component.onCompleted: play()
  }
  VideoOutput {
    id: vo; anchors.fill: parent; fillMode: VideoOutput.PreserveAspectCrop
    Connections { target: vo.videoSink; function onVideoFrameChanged() { bench.frame() } }
  }
}
"""


def worker(name, assets, seconds):
    import psutil
    from PySide6.QtCore import QObject, Slot, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView

    fname, kind, opts = CANDIDATES[name]
    path = os.path.join(assets, fname)
    url = QUrl.fromLocalFile(path).toString()

    class Bench(QObject):
        first = None
        frames = 0

        @Slot()
        def frame(self):
            if self.first is None:
                self.first = time.perf_counter()

    app = QGuiApplication(sys.argv)
    bench = Bench()
    view = QQuickView()
    view.rootContext().setContextProperty("bench", bench)
    tpl = {"image": QML_IMAGE, "static": QML_STATIC, "video": QML_VIDEO}[kind]
    qml = tpl % {"url": url, "cache": str(opts.get("cache", True)).lower()}
    qml_path = os.path.join(os.environ.get("TEMP", "."), f"bgbench_{name}.qml")
    with open(qml_path, "w", encoding="utf-8") as f:
        f.write(qml)
    swaps = [0]
    view.frameSwapped.connect(lambda: swaps.__setitem__(0, swaps[0] + 1))
    view.setSource(QUrl.fromLocalFile(qml_path))
    view.resize(1280, 720)
    view.show()
    proc = psutil.Process()
    state = {"cpu0": None, "wall0": None, "swaps0": 0, "gpu": None}

    def start_window():
        t = proc.cpu_times()
        state["cpu0"] = t.user + t.system
        state["wall0"] = time.perf_counter()
        state["swaps0"] = swaps[0]
        if os.name == "nt":                  # sample GPU while the window runs
            ps = ("(Get-Counter -Counter '\\GPU Engine(pid_%d*)\\Utilization "
                  "Percentage' -SampleInterval 1 -MaxSamples 6).CounterSamples"
                  " | Measure-Object CookedValue -Sum | Select -Expand Sum"
                  % os.getpid())
            state["gpu"] = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def finish():
        t = proc.cpu_times()
        wall = time.perf_counter() - state["wall0"]
        cpu = (t.user + t.system) - state["cpu0"]
        mem = proc.memory_info()
        gpu = None
        if state["gpu"] is not None:
            try:
                out, _ = state["gpu"].communicate(timeout=15)
                gpu = round(float(out.strip().replace(",", ".")) / 6, 2)
            except Exception:
                gpu = None
        res = {
            "candidate": name, "file": fname, "kind": kind,
            "file_mb": round(os.path.getsize(path) / 1048576, 2),
            "first_frame_ms": None if bench.first is None
            else round((bench.first - T0) * 1000),
            "cpu_pct": round(100 * cpu / wall, 2),
            "rss_mb": round(mem.rss / 1048576),
            "peak_mb": round(getattr(mem, "peak_wset", mem.rss) / 1048576),
            "gpu_pct": gpu,
            "swaps_per_s": round((swaps[0] - state["swaps0"]) / wall, 1),
            "window_s": round(wall, 1),
        }
        print("RESULT " + json.dumps(res))
        app.quit()

    from PySide6.QtCore import QTimer
    QTimer.singleShot(5000, start_window)                 # let it settle
    QTimer.singleShot(int((5 + seconds) * 1000), finish)
    app.exec()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", required=True)
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--worker")
    a = ap.parse_args()
    if a.worker:
        worker(a.worker, a.assets, a.seconds)
        return
    results = []
    for name in (a.only or CANDIDATES):
        p = subprocess.run([sys.executable, __file__, "--worker", name,
                            "--assets", a.assets, "--seconds", str(a.seconds)],
                           capture_output=True, text=True, timeout=180)
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
        if line:
            results.append(json.loads(line[0][7:]))
            print(line[0][7:], flush=True)
        else:
            print(name, "FAILED", p.stderr[-400:], flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"python": sys.version.split()[0], "results": results}, f, indent=2)


if __name__ == "__main__":
    main()
