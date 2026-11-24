# EqUpdater 3.0 — Architecture, technology decision, roadmap

Status: **Phase 1 (audit + architecture) complete, awaiting approval of the decisions in §9.** Branch: `feat/equpdater-3.0` (cut from `fix/mod-verification-recovery`, which is 4 commits ahead of `main`; see §9 D1).

## 1. Current architecture (2.1.0)

| Measure | Value |
|---|---|
| `equpdater/app.py` | 12,375 lines, ~160 top-level + ~450 methods, one `tk.Tk` subclass (`EqUpdaterApp`, line 4579 → end) |
| Other modules | 18 (≈6,000 lines): `news`, `ui`, `config`, `planner`, `states`, `mpq`, `gamepaths`, `platforms`, `deploy`, `installer`, `logindoctor`, `gitcompare`, `versions`, `hashing`, `i18n`, `octolauncher`, `branding` |
| Tests | 30 files, ~9,700 lines, 521 tests (HANDOFF) |
| Runtime | Python 3.12, Tk, Pillow, PyInstaller (Windows exe 61 MB, Linux AppImage 51 MB) |

**The key finding:** `app.py` is not just UI. Lines 1–4,520 are *backend* written as module-level functions that happen not to import Tk-state: torrent/aria2 sync, MPQ download, mod registry/install/uninstall, `dlls.txt` reconcile, custom mods, addon catalog/ancestry/install, tweaks and `Config.wtf`, news fetch. Lines 4,579–12,375 are `EqUpdaterApp`, where UI rendering, state and business decisions (Update All previews, launch, reconcile) are interleaved. So the rebuild is: **(a) mechanically lift the backend into `equpdater/core/*` unchanged (tests prove it), (b) replace only the Tk class.** Backend is not rewritten.

### Tk-coupled functionality (must be rebuilt, not lifted)
Everything in `EqUpdaterApp`: window/DPI, header/nav canvas drawing, `GradientButton`, `SlimScrollbar`, `AnimatedBackground` (ui.py), accordion news (`_render_accordion`, `_acc_*`, `_ann_*`), Tweaks/Mods/Addons/MPQ panels, Settings overlay, Login Doctor dialog, footer/progress, launch split-button menu, FontManager (Tk font registration), `after()` scheduling, `_poll` queue pump.

### Threads and scheduling today
`UpdateWorker`/`VerifyWorker` (threads, `queue.Queue` log+progress, drained by a Tk `_poll` loop), ad-hoc `threading.Thread` per news fetch, addon verify, mod identify, mirror check, update check, animation frame producer (ui.py `_FrameProducer`). No bounded pool, no request de-duplication, results marshalled to Tk via `after()`.

### Network and caches today
Forum listing + first-post fetch (news.py, urllib, BlazingFast challenge detection), `news.json` fallback, GitHub/Codeberg/OctoWoW-Git release lookups (`mod_release_cache`), addon catalogue + `addon_sha_cache` + `addon_ancestry_cache`, MPQ sha, updater tag check, mirror status. Caches live in `config.json` (single file, rewritten whole on every change, atomic via temp+rename under an RLock — good, keep the guarantees).

### Bottlenecks identified (to be confirmed with the benchmark harness, `PERFORMANCE.md`)
1. `bubbles.gif`: 23.6 MB, 622 frames @ 400×225 (30 ms nominal, 80–100 ms effective). Decoded/scaled/darkened in Python+Pillow on a worker thread, pasted per frame as `PhotoImage` on the Tk thread; every frame repaint goes through Tk's software canvas. `wow2004.gif`: 15.2 MB, 288 frames @ 800×450.
2. News: per-post `tk.Text` widgets with separate height measurement (`_news_text`, `_acc_*`), plus an outer canvas scroller → nested scroll authorities, text-height races, rebuild on `_acc_sync`/`_rewrap_news` (called from window-configure).
3. Whole-config JSON rewrite on each cache change; caches (news bodies, release data) share the file with user settings.
4. Resize path: `_on_window_configure → _begin_resize → _live_resize → _apply_resize → _stretch_lists → _rewrap_news` re-lays out all panels.
5. Import cost: `import equpdater.app` = 0.82 s before any window exists (measured).
6. Startup scans (`_discover_existing_mods`, addon verify, mod identify) — need caching keyed by mtime/size (scan-result reuse is a hard requirement).

## 2. Technology options

Scoring is against the brief's priorities: performance, parity, reliability, modular, skins, install, OctoBot.

| | **A. PySide6 + Qt Quick (QML)** | B. Python + pywebview (WebView2/WebKitGTK) | C. Tauri (Rust + system webview) | D. Stay on Tk, fix | E. PySide6 Widgets |
|---|---|---|---|---|---|
| Reuse of 12k lines of Python backend | **Direct, in-process** | Direct, but JS↔Py bridge for every call | **None** — rewrite in Rust or ship Python as sidecar process (violates "no extra processes") | Direct | Direct |
| Rendering | **GPU scene graph (RHI: D3D11/Metal/Vulkan/GL); animated image/video decoded off-thread** | Browser compositor (good) | Browser compositor (good) | CPU software canvas | CPU raster (QPainter) |
| Smooth 60 fps scrolling / virtualised lists | **`ListView`/`Flickable`, native kinetic scroll, delegate recycling** | Needs hand-built virtual list | Same as B | Not achievable (root cause of today's issues) | `QListView` ok, text rendering heavier |
| Animated background without full repaint | **Separate layer; `AnimatedImage` or `VideoOutput`/shader — foreground untouched** | CSS/video OK | CSS/video OK | Not without repaint | Whole-window repaint |
| 9-slice skins from images | **`BorderImage` is built in** | CSS `border-image` | CSS `border-image` | Hand-rolled | QSS border-image (limited) |
| Skin safety (no code from skins) | **Skin is data; QML only loads PNG/JSON via our loader** | Skin could inject CSS/JS unless sanitised | Same as B | n/a | n/a |
| Windows install story | Bundled in PyInstaller, no runtime needed | **Needs WebView2 runtime** (present on Win10 21H2+/11, absent on some) | Single exe; small | Current | Same as A |
| Linux story | AppImage with Qt (works; X11+Wayland) | **WebKitGTK is a distro dependency and a known source of breakage** | Same WebKitGTK problem | Current | Same as A |
| Idle CPU when static | ~0% (event driven, render loop only when dirty) | ~0–1% | ~0–1% | ~0% | ~0% |
| Size | +~45–70 MB after trimming unused Qt modules (exe ≈ 100–130 MB) | exe ≈ 55 MB | **≈ 10 MB** + sidecar | 61 MB | ≈ 80–100 MB |
| Memory (estimate) | 120–200 MB incl. decoded frames | 150–250 MB (webview process tree) | 100–200 MB | ~170 MB today (to measure) | 100–150 MB |
| Licensing | **LGPL v3** (PySide6): dynamic linking, ship licence text, no code changes to Qt | n/a | MIT/Apache | PSF | LGPL |
| Maintainability | QML + Python, well typed; testable via `QQmlApplicationEngine` offscreen | Web stack + Python | Web stack + Rust | Monolith | Python only |

**Recommendation: A — PySide6 with Qt Quick/QML.** It is the only option that gives GPU rendering, built-in virtual lists, 9-slice, and an isolated animated layer **while keeping the Python backend in-process** (no sidecar, no local server, no WebView runtime dependency, one packaging pipeline for Windows+Linux that is a straight evolution of today's PyInstaller flow). Costs: ~+50 MB download; LGPL compliance; a new language (QML) for the UI. Rejected: C (forces backend rewrite or a second process), B (WebView2/WebKitGTK dependency risk on exactly the Linux users who already needed fontconfig surgery), D/E (do not remove the root causes).

**This recommendation is a hypothesis until the Phase 2 proof of concept passes the gates in §7** (cached start ≤ ~1 s, 60 fps scroll, background CPU budget, install size). Fallback if it fails: E for panels with Qt Quick only for the background.

### Key technical choices inside A
- **Animated background:** convert GIFs once (build step, not runtime) to a looped, pre-darkened, pre-scaled **WebP/APNG sprite or short H.264/VP9 clip** and play it in a dedicated bottom-most layer (`AnimatedImage` with frame cache, or `MediaPlayer`+`VideoOutput`). Evaluate both against appearance (side-by-side diff), CPU and file size; GIF 23.6 MB → target < 6 MB. Pause on minimise/unfocus (`Qt.application.state`), FPS cap setting, PNG fallback.
- **Pages are QML components; state is Python `QObject`s/`QAbstractListModel`s** exposed via a small number of context objects (`App`, `Skin`, `News`, `Addons`, `Mods`, `Mpq`, `Tweaks`, `Launch`, `Diagnostics`, `Server`). Models emit `dataChanged` for single rows → no page rebuilds.
- **Concurrency:** one `TaskManager` (bounded `ThreadPoolExecutor`, keyed de-dup, cancel tokens, timeouts) results delivered as Qt signals (queued connections). No `after()`, no polling loops.
- **News:** one `ListView` per panel (one scroll authority), each post a delegate with one `Text` item (`textFormat: RichText`/`StyledText`, one height authority = `contentHeight`), links via `onLinkActivated`. No nested flickables, no sticky-header duplicate bodies (sticky header = `section.delegate`/`headerPositioning`, header only).

## 3. Proposed module layout

```
equpdater/
  core/            # NO Qt imports. Lifted from app.py, behaviour-identical, unit tested
    config/        # versioned schema, migrations, atomic write, store split (§6)
    net/           # http client (secure_urlopen, allowed hosts, challenge detection), conditional GET, backoff
    tasks/         # TaskManager (bounded pool, dedupe, cancel, timeout, progress)
    news/          # forum parsing (news.py moved), NewsService (cache, dedupe, pagination)
    game/          # client dir detection, version, torrent_tree checks, launch options, launch
    addons/        # catalogue, planner glue, install_addon_files, ancestry, failures
    mods/          # MODS_REGISTRY, install/uninstall, dlls.txt, custom mods, planner
    mpq/           # mpq.py + download/link
    tweaks/        # TWEAKS_ITEMS, patch_exe, Config.wtf writer
    diagnostics/   # logindoctor + env/launch problems + new DiagnosticsService
    install/       # installer, deploy, migration (EqUpdater, OctoLauncher, ZIP, torrent-existing)
    platform/      # platforms.py, gamepaths.py (Win/Linux/Wine)
    server/        # OctoBot client (population + status), semantics
    i18n/          # unchanged
  ui/              # Qt only
    shell/         # main.py, window, QML engine bootstrap, tray/window controls
    models/        # QAbstractListModel adapters over core services
    controllers/   # thin QObjects: page controllers, call core, emit signals
    qml/           # Main.qml, pages/, components/ (Button, SplitButton, Panel, ScrollBar…)
    skins/         # SkinManager, SkinLoader, validator, asset cache (Python) 
skins/             # official skins (data only)
tests/             # existing tests retained; new tests per layer
```

Rules: `core` never imports `ui`; `ui` never reaches into files directly; every QML component takes its visuals only from the `Skin` object (no colours/paths in QML). The legacy Tk app stays runnable on this branch until parity is signed off.

**Extraction method (safe, test-first):** move code with `git mv`-style slicing, keep the old names re-exported from `equpdater.app` until the Tk shell is deleted, run the full existing suite after each slice. No logic edits in the same commit as a move.

## 4. Skin specification (summary — full text in `SKINNING.md`)
Data-only skin folder: `manifest.json` (id, name, version, `skin_api`, author, licence, previews), `theme.json` (palette, fonts, spacing, radii, per-component dimensions), `layout.json` (positions/anchors of the fixed layout slots), and asset folders. Every component has a **stable identifier** (`panel.default`, `button.primary.hover`, `nav.tab.active`…); anything missing falls back to the built-in default skin per-asset. Loader: whitelist of extensions (png, webp, jpg, svg-subset→rasterised by us, ttf/otf, gif/webp for animation), size caps, path-traversal-proof zip import, JSON schema validation with human-readable errors, no QML/JS/Python ever loaded from a skin. 9-slice via `border: [l,t,r,b]` entries in `theme.json` consumed by `BorderImage`.

## 5. Application state & configuration
Split today's single `config.json` into: `settings.json` (user prefs, skin, launch pref), `state.json` (game install, active client, flags), `cache/` (news, release, addon sha/ancestry, server — separately versioned and safely deletable), `history/` (diagnostics). `schema_version` per file, forward migrations as pure functions with a backup of the pre-migration file. **First run of 3.0 reads the existing `config.json` and leaves it untouched** (so 2.x keeps working side by side); migration is copy-forward only. Debounced writes (coalesce within 500 ms), one writer thread, same atomic-replace discipline as `config._atomic_write`.

## 6. Hard constraint conflict flagged
Today's app **already contains a base-client sync** (`CLIENT_TORRENT_URL`, `fetch_torrent`, `run_aria2c`, `ensure_aria2c`, `UpdateWorker`). The 3.0 brief forbids a client downloader/torrent client, yet also forbids removing functionality without approval. **3.0 as specified will not carry that code over**; see decision D2.

## 7. Performance acceptance criteria (measured vs `PERFORMANCE.md` baseline)
Cached start to interactive ≤ 1.0 s (warm disk, i5-class); tab switch (visited) ≤ 100 ms; scroll p95 frame ≤ 16.7 ms with 500 news/addon rows; idle CPU ≤ 0.5% with animation off, ≤ 3% of one core animated at 30 fps; zero network requests during scroll; skin switch ≤ 500 ms with no restart; idle RSS ≤ today's; 8 h soak with no monotonic growth in RSS/threads/handles.

## 8. Roadmap
P1 audit/arch (done) → P2 shell + skin engine + Bubbles + animated bg + News + Play split-button + benchmarks (gate §7) → P3 lift & wire Addons/Mods/Tweaks/MPQ/Settings/Diagnostics/Game → P4 installer & migrations (EqUpdater, OctoLauncher, ZIP, torrent-existing) → P5 four more skins + skin manager/import/export/hot-reload/docs → P6 OctoBot (`OCTOBOT.md`) → P7 optimise/soak/cross-platform/all skins×pages → P8 separate beta channel, no stable replacement without approval.

## 9. Decisions needed from you
- **D1 Base branch.** Cut from `fix/mod-verification-recovery` (includes PLAY launch-choice commit `0631a78` needed for parity) rather than `main`. Alternative: merge that branch to `main` first.
- **D2 Torrent/aria2 base-client sync.** Drop in 3.0 (setup only configures an existing install), or keep as a clearly separate legacy feature?
- **D3 Framework.** Approve PySide6/QML pending the §7 gate.
- **D4 Baseline failures.** 4 smoke-test failures already exist on this branch (see `PERFORMANCE.md`); fix before the rebuild or carry as known?
