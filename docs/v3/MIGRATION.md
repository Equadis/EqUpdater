# Migration: 2.x → 3.0 (strategy and what is built)

**Rule: 3.0's own data (settings, cache, skins) lives in `v3/` and 2.x's `config.json` is only *read* for it.** The *backend* modules lifted from 2.x (tweaks now; mods, addons, MPQ next) keep reading and writing their existing records in `config.json` through the same locked, atomic `config.update_config`, so 2.x and 3.0 stay consistent while both exist. Tests and benchmarks redirect that file (`EQU3_DATA_DIR`).

| 2.x data (`<app data>/EqUpdater/`) | 3.0 | Status |
|---|---|---|
| `config.json` (settings, mods/addons records, caches) | read once; never modified | `core/settings.import_legacy` |
| `out_dir` → game folder | `settings.json: game_dir` | built |
| `launch_default` | `settings.json: launch_default` (kept even if its file is briefly missing) | built, tested |
| `animated_background` | same key | built |
| `game_launcher` (Linux command / env) | same key, same `platforms.launch_plan` | carried; UI in Phase 3 |
| `news_seen` (NEW! marks) | same | built |
| `news_cache`, `news_first_posts` | **not imported**: 3.0 has its own `cache/news.json` and refills it on first run (cache, not user data) | by design |
| `mods`, `addons`, `custom_mods`, tweaks, MPQ records, `ignore_updates`, provenance | still read by the *backend modules that own them* in Phase 3 from the same `config.json` schema v2 (planner/ownership rules unchanged). Moving them to a 3.0 store is a later, separate decision, because it would break running 2.x beside 3.0 | Phase 3 |
| fonts choice, language | read from 2.x config in Phase 3 | Phase 3 |
| `%LOCALAPPDATA%\EqUpdater\fonts` | unchanged location | Phase 3 |

3.0 files live in `<app data>/v3/`: `settings.json`, `cache/news.json`, `skins/` (player skins). Each store has `_schema`; older files are walked forward by migration functions after a `.vN.bak` copy; a corrupt file is kept as `.corrupt-<time>` and defaults are used (`core/store.py`, tested).

## Game installs (Phase 4: built, see `INSTALLATION.md`)
`core/install.py`: finds an existing game (2.x config, OctoLauncher settings, usual places), describes it read-only (complete / unfinished torrent / none), extracts a local ZIP or TAR safely (traversal, links, space, never overwrites, staged), and guards (2.x running, game running). `core/setup_service.py` + `SetupOverlay.qml` are the first-run "Find your game" panel and Settings → Find…. The installer/AppImage and qmlcachegen packaging are the next step.

## Risks tracked
* Running 2.x and 3.0 at once both touching `dlls.txt`/addons: Phase 3 keeps the 2.x single-instance and client-lock guards.
* The 2.x installer shortcuts point at `EqUpdater.exe`; 3.0 ships as a separate beta build and installer so nothing repoints until you approve.
