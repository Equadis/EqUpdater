# Parity ledger: 2.1.4 -> 3.0.0-beta.1

Current status of every area in the original audit (`FEATURE_PARITY.md` top half). **Done** = built in 3.0 and covered by automated tests; **Done, not seen live** = also true, but a real-system check is still owed (`QA_CHECKLIST.md`); **By design** = removed on purpose; **Missing** = not built (listed in `KNOWN_ISSUES.md`).

| Area | Status | Notes |
|---|---|---|
| Header, nav, badges, Settings gear, footer (status, version, links) | Done | Qt layout; minimum 860x600; tooltips lazy |
| Update-available label for the app itself | **Missing** | beta updates by installer |
| Window icon, taskbar identity | Done | separate AppUserModelID from 2.x |
| Native dark title bar | **Missing** | system frame |
| Single instance / close handling | Done (new) | one window per data folder; 2.x vs 3 not cross-checked at run time |
| First-run language prompt | Done | before "Find your game" |
| Animated background, still fallback, pause, setting, fades | Done | animation kept loaded; Qt leak avoided |
| Fonts (Arial / Friz / OpenDyslexic, 0.92 scale, Chinese lock) | Done | bundled fonts registered for the process; override applies live |
| Six languages, every string `tr()`/`N_()` | Done | QML strings use `app.t`, translated tables shared |
| Accessibility (dyslexic font key) | Done | imported from 2.x config |
| News: forum parsing, dedupe, NEW marks, accordions, cache, fallback | Done | benchmarks in `PERFORMANCE.md` |
| PLAY, launch menu, defaults, detection, args, env, Linux/Wine plan | Done, not seen live | pipeline tested with the spawn replaced |
| Launch failure messages, watcher, close-on-launch, clear WDB, client-lock guard | Done, not seen live | |
| UPDATE / UPDATE ALL base-client states | **By design** | no base client sync |
| Tweaks (all, limits, Apply/Reset, WoW.exe patch, Config.wtf, language, res-fix, DXVK conf, pfUI profile) | Done | WoW.exe and Auto Loot patch now atomic |
| Mods: registry, install/update/uninstall, stash/rollback, ignore, adopt, identify, force-replace, version cache | Done | planner rules unchanged (lifted verbatim) |
| Custom git mods and asset mods, conflicts, removal | Done | |
| Essential-mod consent, first-run "manage what is installed" | Done | the latter was found missing and fixed in beta prep |
| Octo Updater settings import | **Missing** (via 2.x it works) | |
| Addons: catalogue, scan, status, install/update/remove/adopt/switch/force, failure classes, case conflicts, SavedVariables | Done | pooled list; 250 addons measured |
| Custom addon URL | Done | |
| MPQ: scan, check, fetch, replace, link/unlink, sha, locale | Done | |
| Settings: folder change, open folder, toggles, Defender, install missing, log, Login Doctor, Ko-fi, language | Done | Defender / launcher not seen live |
| Verify game files | Done, **changed** | read-only scan against the manifest (no torrent) |
| Base client torrent sync, aria2, mirror status, firewall notice | **By design** | |
| Windows installer and shortcuts | Done (separate beta product) | 2.x installer untouched; not run on a clean machine |
| Linux AppImage | **Written, never built or run** | `LINUX.md`; needs a Linux machine |
| Diagnostics: Login Doctor checks, ENV/LAUNCH problems, session log, support ZIP with redaction | Done | redaction tested |
| Config: atomic writes, lock, v1->v2 migration, corrupt-file copy | Done | shared `config.py`, unchanged |
| Build / CI | Partly | `tools/build_v3.py` + self-test; `tests.yml` now installs PySide6 (Qt tests no longer skip) and `beta.yml` (manual) builds the installer artifact. **Neither workflow has been run on GitHub yet** |
| Account chip, friends sidebar, presence abstraction, Social settings | New | local friends work; **sign-in and live presence not operational** (no OctoWoW service; `SOCIAL.md`) |
| Skin engine, seven skins, OctoBot chip, conditional network layer, split stores, benchmarks | New | |

## What this means for the beta
Nothing that 2.x did for a player is missing except the in-app update notice, the dark title bar and (via a detour) Octo Updater import; the downloader is gone on purpose. What is *unproven* is real use: see `KNOWN_ISSUES.md` items 1-7.
