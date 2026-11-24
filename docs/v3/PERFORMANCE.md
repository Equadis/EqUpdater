# Performance: method, baseline, proof-of-concept results

All numbers are measured on one machine (Windows 10 Home 19045, 12 logical CPUs, 31.9 GB RAM, Python 3.12.10, PySide6 6.12.0, default GPU/D3D11 path) with `tools/bench/*`; raw JSON is in `bench-results/`. **Sample sizes are small (5 launches, one 4 s scroll per list); treat differences under ~15 % as noise.** Not measured yet: long soak/leak runs, Linux, HiDPI scale factors, low-end GPUs, frozen (PyInstaller) builds.

## What is compared
* **2.x baseline = v2.1.4** (`496fa67`, latest 2.x release), run from source in a clean checkout. `D:\EqUpdater-queue` happened to be a worktree at exactly that commit; it was used read-only (`PYTHONDONTWRITEBYTECODE=1`). A frozen v2.1.4 exe would need downloading the release asset and was not done.
* **3.0 = this branch's proof of concept** (`python -m equpdater.qtui`).
* Both read the **same fake forum** (`tools/bench/fakesite.py`, 60 posts per forum, ~40 % long) through their real parsers, in a throwaway `LOCALAPPDATA`/`HOME` with an empty game folder. No request leaves the machine.
* Start-up: `visible` = a visible ≥300×300 top-level window of the process tree exists (Win32 poll every 4 ms, identical for both). `interactive` = window mapped **and** news rows present. Idle = CPU/RSS of the process tree 3–13 s after interactive.
* Scenario numbers differ in kind (see caveat): **3.0** = gap between consecutive presented frames while the scene is driven (a missed 60 Hz frame is > 16.7 ms); latencies = action → next presented frame. **2.x** has no frame clock, so each step's cost is "drive Tk to a finished paint" (`update()`), which cannot see compositor time. 2.x scroll steps look fast because Tk moves a canvas; its weak spots are expand/tab/resize.

## Results (medians; cached start = news already in cache)

| | 2.x (v2.1.4) | 3.0 PoC | notes |
|---|---|---|---|
| Time to first visible window | 3,103 ms | **1,088 ms** | |
| Time to interactive (cached) | 3,121 ms | **1,249 ms** | target ≈ 1,000 ms **not yet met** |
| First launch, empty cache: visible / interactive | 2,691 / 3,648 ms | 1,334 / 1,489 ms *(measured before the lazy-TLS change; re-measure pending)* | |
| Idle CPU, animated background on | **46.9 %** of a core | **0.3 %** | |
| Idle CPU, animation off | 0.2 % | 0.0 % | |
| Idle memory (RSS), animation on / off | 98 / 80 MB | **192 / 185 MB** | **+~100 MB: target (≤ today) not met**, see below |
| Scroll, announcements (p50 / p99 / max, 4 s) | 2.8 / 6.9 / 7.8 ms per step* | 7.0 / 9.9 / 16.4 ms per frame; 0 frames > 33 ms | 144 swaps/s unthrottled (benchmark driver) |
| Scroll, changelog | 2.4 / 6.9 / 7.1 ms* | 7.0 / 9.5 / 17.0 ms; 1 frame > 16.7 ms | |
| Network requests while scrolling | n/m | **0** (asserted in tests too) | |
| Expand a cached post (p50 / p95) | 22 / 180 ms | **7.5 / 42 ms** | |
| Switch to a visited tab (p50 / p95) | 25 / 40 ms | **6.8 / 11 ms** | first visit: 52 / 130 ms vs 8.5 / 11 ms |
| Resize step (p50 / p95) | 234 / 817 ms | **33 / 41 ms** | still > 16.7 ms per step; resize is layout-bound |
| Switch skin, live | not possible (no skins) | **88 ms** (max 104) | |
| Test suite | 881 tests | 929 tests (+48), all pass | |

\* 2.x per-step cost, not a frame time (see caveat).

### Why 3.0 uses more memory, and what is next
RSS at stages (MB): Python 14 → +PySide 31 → +app imports 42 → +objects 54 → after QML load 104 → after first frames and news 185–205. The ~80–100 MB between "QML loaded" and "rendering" is the Direct3D/RHI context, swap-chain, glyph and texture caches; the bare Qt Quick window in the background-format test (one image, no app code) already idles at 99 MB. So roughly half of the gap is the price of GPU rendering and the other half is this app's scene and text. Candidates for Phase 7 (each to be measured, none assumed): decode the still at window size instead of 2284×1285, cap glyph/texture caches, fewer simultaneous delegates (`cacheBuffer`), release hidden pages' textures, check the software-GL fallback is not selected on any supported GPU. Accepted for now against the approved "≈ 50 MB larger is acceptable if noticeably faster"; the 2× idle memory is the one place where 3.0 is worse than 2.x.

### Why start-up is not at 1.0 s yet
Python imports ≈ 0.45 s (PySide6 QtGui 0.2 s, `core` modules 0.1 s, `platforms` 0.08 s) + `QQmlApplicationEngine.load` ≈ 0.55 s (QML is compiled from source at each start in this dev run). Removed so far: building the TLS context at import (−0.35 s, now on first request, off the UI path). Planned: precompile QML (`qmlcachegen`/resource bundle, normal for the frozen build), lazy `platforms`/`news` imports, and skip loading skins that are not selected beyond their manifest. The target is plausible but **not demonstrated**.

## Animated background: formats compared
`tools/bench/bg_bench.py`; 1280×720 window, 30 s steady state, one process per candidate, GPU = Windows "GPU Engine" counters for the pid (noisy). Source: `bubbles.gif`, 622 frames, 400×225, 19.7 s loop, **213 MB if every frame were decoded as RGBA** (22.6 MB on disk).

| Candidate | File | First frame | CPU | RSS | GPU | PSNR vs GIF |
|---|---|---|---|---|---|---|
| GIF, `AnimatedImage cache:true` | 22.6 MB | 226 ms | 0.16 % | **298 MB** | 1.8 % | n/a |
| **GIF, `cache:false` (streams)** | 22.6 MB | 232 ms | 0.10 % | 81 MB | 1.4 % | identical |
| Animated WebP q85, cache:true | 2.7 MB | 222 ms | 0.36 % | 301 MB | 1.2 % | 40.0–40.6 dB |
| **Animated WebP q85, `cache:false`** | **2.7 MB** | 220 ms | 0.42 % | **86 MB** | 1.5 % | 40.0–40.6 dB |
| H.264 400×226 (`MediaPlayer`+`VideoOutput`) | 1.6 MB | **1,032 ms** | 2.2 % | 136 MB | 3.1 % | n/m |
| H.264 960×540 | 6.6 MB | 1,009 ms | 2.3 % | 152 MB | 5.3 % | n/m |
| VP9 WebM 400×226 | 0.6 MB | 998 ms | 2.9 % | 124 MB | 2.9 % | n/m |
| Static PNG | 1.4 MB | 321 ms | 0 % | 99 MB | 0 % | n/a |

**Decision:** `AnimatedImage` with `cache:false` on an **animated WebP** (q85, original frame timings kept): same look (≈ 40 dB), 8× smaller file, ~0.4 % CPU, ~86 MB total, no decoded-frame cache (the cached variants hold ~215 MB, as predicted). Video was slower to start (1 s) and used 5–20× the CPU; it stays supported for skins that ship video (loaded lazily, so QtMultimedia is not initialised otherwise). It plays only when `settings.animatedBackground && app.animationsActive`; minimised → stops; "pause when unfocused" is a setting; a still is always underneath. The animation sits on its own layer; foreground items never bind to it. A configurable FPS cap was not implemented: with an image animation the file's own timing (≈ 30 fps) is the cap, and CPU is already ~0.4 %; revisit if a skin ships a higher-rate animation. Loop seamlessness is inherited from the GIF's own loop (no gap between last and first frame was observed; not instrumented).

## How to re-run
```
python tools/bench/make_news_fixture.py bench-results/news-fixture.json
python tools/bench/app_bench.py --old <2.x checkout> --out bench-results/compare.json   # ~15 min, leave the machine idle
python tools/bench/bg_bench.py --assets <dir with candidates> --seconds 30 --out bench-results/bg-formats.json
```
`app_bench.py --only scenarios|startup --targets old new`. Keep the machine idle while it runs; a concurrent run contaminated an early background benchmark (discarded, rerun cleanly).

## Findings the benchmark forced (fixed)
* Reading `skin.theme` (a nested Python dict) from every QML binding rebuilt the whole map on each access: skin switch 640 ms → **88 ms**, expand p95 120 → 42 ms, scroll max 148 → 16 ms after routing every read through one cached JS object (`Style.theme`).
* Manually compensating ListView scroll for items changing height *above* the viewport made the view jump (contentY 19,633 → 331); Qt's own anchoring is correct, and `tests/test_v3_qt.py` now holds that.
* Post bodies are created only while a post is open (Loader), so collapsed posts cost no text layout.

## Acceptance targets (ARCHITECTURE.md §7) after the proof of concept
Met: tab switch ≤ 100 ms, no network on scroll, expand "immediate", idle CPU ≈ 0 with animation off (and ≈ 0.3 % with it on), skin switch without restart, scroll p99 within a frame at ~144 swaps/s. **Not met: interactive ≈ 1.0 s (1.25 s), idle memory ≤ 2.x (192 vs 98 MB).** Not yet measured: 8 h soak, repeated skin/tab churn for leaks, large addon lists (page not migrated), HiDPI, Linux.


## Addons page: 10, 50, 100 and 250 addons (`tools/bench/addons_bench.py`)
Real GPU scene, one process per size, synthetic addons (no disk, no network), Bubbles and Minimal Dark, Windows 10, same machine as above. Raw data: `bench-results/addons.json`, `addons-minimal-dark.json`. Small samples (one 4 s scroll per size): read differences under ~30 % as noise; the first run of a session also pays shader/cache warm-up (the 410 ms "open" at N=10).

| Addons | Rows | Delegates alive | Open page | Scroll p50 / p99 frame | Frames > 33 ms | Filter (type) | Filter (clear) | One addon changes | Rows repainted | Memory added by the page |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 13 | 14 | ~30 ms | 11 / 23 ms (list too short to scroll) | 0 | 9 ms | 8 ms | 8 ms | 1 | 61 MB |
| 50 | 53 | 17 | 30 ms | 11 / 22 ms | 0 | 7 ms | 11 ms | 9 ms | 1 | 70 MB |
| 100 | 103 | 18 | 32 ms | 11 / 25 ms | 2 | 7 ms | 16 ms | 10 ms | 1 | 71 MB |
| 250 | 253 | 18 | 35 ms | 6 / 23 ms | 2 | 11 ms | 23 ms | 11 ms | 1 | 72 MB |
(Minimal Dark shown; Bubbles is within noise: 250 addons p50 11 ms, p99 24 ms, 0 frames over 33 ms.)

* Cost does **not** grow with the number of addons: delegates alive stay at 14-18, page memory is flat (~60-70 MB incl. the page's textures), building all rows in Python takes 0.6 ms (10) to 7 ms (250) on a worker, a changed addon repaints exactly one row with no insert, remove or reset (asserted in `test_v3_qt.TestAddonsPage`), and the search runs in Python on a snapshot.
* **Scroll frame times** are gaps between presented frames while the whole list is scrolled down and up in 4 s (about 3,000 px/s, far faster than a mouse wheel). The scene presents at 100+ fps unthrottled here, so "p50 11 ms" means frames are not the limit; a handful of frames over 33 ms per 500 remain at 100 and 250 addons (first appearance of a delegate type, GC); not zero, not yet explained.

### What the first runs found (fixed)
| Version of the page | 250 addons: scroll p50 | memory added | notes |
|---|---|---|---|
| a `Loader` and a fresh delegate per row, a `ToolTip` popup built per icon | 83 ms (12 fps), 41 frames > 33 ms | 190 MB (152 -> 342) | each delegate created ~40 objects incl. 5-6 hidden popups; creation was the cost, and it grew with the distance scrolled |
| tooltips created only while hovered | 83 ms | 160 MB | removed the popups, not the creation cost |
| **pooled delegates** (`reuseItems: true`, `DelegateChooser`, one component per row type) | **8.6 ms**, 3 frames > 33 ms | **70 MB** | delegates are recycled; only the model roles change |
The same applies to any long list in the app: use `reuseItems` with a chooser, build popups lazily. (Mods and News still use the older pattern; both are short and measured fine, to be converted with the 3.0 list audit in Phase 7.)

## Phase 7: optimisation pass (2026-11-24)
Same machine as above. Tools: `tools/bench/phase7_bench.py` (start-up, memory, idle CPU for every skin and page, one real process per sample, medians of 3), `tools/bench/soak.py` (scripted cycling of pages, skins, overlays and the footer chip for minutes, sampling memory, Python and QML object counts, threads and handles). Raw data: `bench-results/phase7-before.json`, `phase7-after.json`, `phase7-packaged.json`, `soak.json`.

| | before | after |
|---|---|---|
| Time to first frame, source run, 5 skins | 1,240-1,317 ms | **1,081-1,114 ms** (0.96 s running alone) |
| Time to first frame, **packaged exe** | not measured | **770-834 ms** |
| QML load (alone) | 1,105-1,337 ms from process start | **800-850 ms** |
| Idle memory, Bubbles / Classic / Minimal skins | 225 / 218 / 195 MB | **217 / 209 / 186 MB** |
| Memory per page (news, tweaks, addons, mods, mpq) | 223-229 MB | 216-223 MB |
| Soak, 6 min of cycling (before the fix) | **250 -> 475 MB, never levelling** | 5 min: **245-276 MB, flat** (handles 1,388 +/- 8, threads 59 +/- 3, QML objects constant at 945, Python objects flat at ~40 k) |
| Soak, one skin switch to an animated skin | **~8 MB leaked each** | none |

### What was done
1. **Overlays are built when first wanted** (`LazyOverlay.qml`): Settings, Diagnostics, Setup, the custom mod/addon dialog, the language picker and the question dialog were all built at start-up and are rarely opened. 300 ms off QML load, 10 MB off memory. A test asserts nothing is built until it is needed, that a real question still shows and is answerable, and that an overlay stays built once built.
2. **A real leak, found by the soak test and reproduced in isolation:** Qt's animated-WebP reader leaks the file's data (about 1.5x its size: 8 MB for Classic's scene, 3.4 MB for Bubbles' ocean, ~0.4 MB for a GIF) each time a movie is released. The background destroyed and rebuilt its AnimatedImage on every skin switch or animation toggle. Now each animation is loaded once, kept, and shown or hidden (paused when hidden): a one-time cost of about 5 MB per animated skin the player visits, and no growth however often they switch. Tests: no more than one animation object per animated skin however many switches; the setting hides and pauses without destroying. (A Qt bug, not ours; if a later Qt fixes it the pool is harmless.)
3. **Mods list uses pooled delegates** like Addons (`DelegateChooser` + `reuseItems`). **News does not**: measured pooled vs not (2 runs each): scroll p50 5.5 vs 5.5-6.0 ms, max 13 vs 18-22 ms, expand p95 41-44 vs 38-39 ms: no benefit, a worse worst case, so it stays unpooled (variable-height posts with lazily built bodies).
4. **Low-memory rendering** (Settings, restart to apply; `QT_QUICK_BACKEND=software` equivalent via `EQU3_RENDERER=software`): no GPU device or swap chain: Bubbles 226 -> 179 MB, Minimal Dark 197 -> 161 MB (about 30-45 MB), first frame about 130 ms sooner, a little more CPU while an animation plays. Compared with the other Qt graphics backends it is the only one that helps: OpenGL 289-307 MB and Direct3D 12 252-292 MB are both worse than the default Direct3D 11. It is also the safe choice on a machine, VM or Wine without a working graphics driver.

### Where the memory is
Python 14 MB -> + PySide 31 -> + app imports 42 -> + objects 54 -> + QML 104 -> rendering 185-220. The jump to rendering is the Direct3D device, swap chain and the driver's own allocations, which the app does not control (software rendering removes it). Remaining app-owned memory is small. 2.x's 98 MB does not include a GPU device; the target of "no more than today" is **not met with the GPU renderer** and is met only approximately with software rendering (161-180 MB).

### Not done / limits
* Start-up is under 1.0 s packaged (0.77-0.83 s) but 1.08-1.1 s from source; Python imports (about 0.4 s: PySide6 QtCore 0.13 s, the lifted `core` modules 0.1 s, `urllib`/`http.client` 0.05 s) were not made lazy because they sit in 2.x code the lift keeps verbatim.
* The soak ran on this machine for 5-6 minutes, not hours; the flat counters make an hours-long run likely clean, but it has not been done.
* Linux and HiDPI scaling were not measured (no Linux machine here; HiDPI is Qt's automatic scaling, checked only by the existing layout tests).

