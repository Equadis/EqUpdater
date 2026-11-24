# Performance — baseline and acceptance

## Baseline of 2.1.0 (this machine, Windows 10, Python 3.12.10) — measured 2026-11-24
| Metric | Result | Method |
|---|---|---|
| `import tkinter, PIL` | 0.03 s | `time.time()` around import |
| `import equpdater.app` | 0.82 s | same |
| Test suite wall time | 1 m 44 s | `python tools/check.py` |
| `bubbles.gif` | 23.6 MB, 622 frames, 400×225 | Pillow |
| `wow2004.gif` | 15.2 MB, 288 frames, 800×450 | Pillow |
| Installer artefact | exe 61.2 MB, AppImage 51.1 MB | HANDOFF |

### Baseline test results on `feat/equpdater-3.0` (before any 3.0 change)
Failures already present, **not caused by this work**: 4 in `tests/test_app_smoke.py` —
`TestAppStarts.test_linux_settings_fit_and_open_the_game_launcher` (3 subtests: arial/friz/opendyslexic, `Label … is not None`) and `TestLanguage.test_the_window_speaks_the_game_language` (status text mismatch: `Tweaks fehlgeschlagen…` vs expected German). Raw log is in the OS temp dir (`eq_baseline_tests.txt`). Not yet triaged (see ARCHITECTURE D4).

## Not yet measured (harness to be built in Phase 2: `tools/bench/`)
Cold/warm/cached start-to-interactive, idle CPU/RSS (animated on/off), animated-background CPU, tab switch, news scroll frame times, resize cost, network request count per session, 8 h soak. Each will be a script that writes JSON to `bench-results/` so 2.1.0 and 3.0 are compared by the same tool. **No improvement is claimed until both sides are measured.**

## Acceptance targets
See `ARCHITECTURE.md` §7.
