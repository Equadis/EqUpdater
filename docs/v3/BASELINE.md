# Baseline used for parity and benchmarking

| Item | Value |
|---|---|
| Latest 2.x release | **v2.1.4** (2026-11-08), tag → `496fa677c217b5025bb2ecdee8a8f808764b8e7e` (= `origin/main` when fetched 2026-11-24) |
| Branch base | `fix/mod-verification-recovery` @ `0631a78`, **already contained in v2.1.4** (the four extra commits — incl. PLAY launch choice `0631a78` and mod-verification recovery — were merged to `main` before 2.1.4). My Phase 1 assumption that the branch was ahead of `main` was wrong: the local `main` ref was stale. |
| 3.0 branch | `feat/equpdater-3.0` = `0631a78` + docs + test-isolation fix, then `origin/main` merged in (`28d395c`) so the branch carries all of v2.1.4. Nothing is merged back to `main`. |
| Parity/benchmark reference | **`496fa67` (v2.1.4)** — benchmark the 2.x app *from source at this commit* (`python -m equpdater`) on the same machine, same Python, same display, as the 3.0 proof of concept. A frozen-exe comparison needs downloading the release asset; not done without your OK. |
| Test baseline | 881 tests pass in ~127 s on Windows 10 / Python 3.12.10 (`python tools/check.py`) after the isolation fix below. |

## The four baseline failures (resolved in `9ffa5e0`)

All four had **one root cause: environment-specific, caused by a test-isolation assumption that was wrong.**

| Test | Failure | Classification |
|---|---|---|
| `TestAppStarts.test_linux_settings_fit_and_open_the_game_launcher` ×3 subtests | settings/launcher overlay state differed from the test's expectation | environment-specific: app saw the developer's real `~/Games/OctoWoW` |
| `TestLanguage.test_the_window_speaks_the_game_language` | start-up status was "Tweaks failed — check the log" instead of "Ready/Update available" | same |

Mechanism: tests redirect only `LOCALAPPDATA`/`XDG_DATA_HOME`. The app's default game folder is `~/Games/OctoWoW` (`DEFAULT_GAME_DIR`). With a config file present but no `app_version` (the language test saves tweaks first), `_new_version_installed → _migrate_1_3` runs against that folder and calls `_apply_tweaks_worker` → `patch_exe`, which opens the real `WoW.exe` for `"wb"`. CI has no such folder, so it passed there. No test was disabled or weakened; `tests/__init__.py` now gives the whole run a throwaway `HOME`/`USERPROFILE`.

**Side effect found:** on this machine `~/Games/OctoWoW/WoW.exe` is **0 bytes** (mtime 2026-11-09 02:44) and `WTF/Config.wtf` was rewritten minutes earlier. That is consistent with an earlier test run having patched the real folder. I have not touched it. If that install matters, it needs its `WoW.exe` restored.
