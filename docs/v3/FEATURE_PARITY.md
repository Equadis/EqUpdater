# Feature parity checklist (2.1.0 → 3.0)

Source of truth: `equpdater/app.py` function index + `HANDOFF.md` + tests. Status: `☐` not started, `◐` backend lifted, `☑` UI + tests done. **Audit is first-pass; each area gets a line-by-line re-read before its Phase 3 migration. Undocumented behaviour is assumed used.** Test column = existing tests that must keep passing.

## Shell / layout
☐ Logo upper-left (`_build_header`, `_draw_logo`) · ☐ nav News/Tweaks/Addons/Mods/MPQ + Settings gear (`_layout_nav`, `_draw_nav_tab`, badges via `_refresh_mods_badge`/`_refresh_addons_badge`) · ☐ update-available label (`_check_updater_update`, `fetch_updater_latest_tag`) · ☐ footer: status line (error is a link, `_status_error`), game version, app version, progress bar, Discord/support links · ☐ min size / resize / DPI (`_update_minsize`, `_enable_dpi_awareness`) · ☐ window icon, Windows AppUserModelID `eqomdx.EqUpdater.2` · ☐ title bar styling (`style_title_bar`) · ☐ single-instance/close handling (`_on_close`) · ☐ tooltips · ☐ first-run language prompt (`_ask_first_language`)

## Background / appearance
☐ Animated `bubbles.gif` + static `pubbles.png` fallback, setting `animated_background`, edge fades, pause when minimised · ☐ fonts Arial/Friz Quadrata/OpenDyslexic (+0.92 scale, Chinese disables, user fonts dir `%LOCALAPPDATA%\EqUpdater\fonts`) · ☐ languages en/de/ru/zh_cn/es/pt_br (`i18n`, `tests/test_i18n.py` — all strings must keep `tr()/N_()`) · ☐ accessibility (`accessibility`/`dyslexic_font` config keys)

## News (`news.py`, `tests/test_news.py`)
◐ forum f=2 / f=4 parsing, opening posts only, topic-id dedupe, challenge/forum-blocked detection, `news.json` fallback · ☐ accordions, latest expanded, author/date, links, Open in Browser, NEW marks (`news_seen`, `news_marks`), Read more/Show less, Load more (`_ann_more`), per-panel refresh, persistent cache (`news_cache`, `news_first_posts`), offline/error text (`_news_error`)

## Play / launch
☐ PLAY / UPDATE / UPDATE ALL / busy states (`_set_btn_*`, `_refresh_ready_state`) · ☐ split button + launch menu (`_toggle_launch_menu`, `_choose_launch_default`, `_launch_target`; `game_launcher` config; `tests/test_launch_options.py`) · ☐ detection of VanillaFixes.exe / WoW.exe / `*.bat` / custom · ☐ working dir + args + `platforms.child_env`, Linux/Wine launch · ☐ launch failure + watch (`_launch_failed`, `_watch_launch`, `LAUNCH_PROBLEMS`) · ☐ `close_on_launch`, `clear_wdb_on_launch`, client-lock guard (`client_exe_locked`, `tests/test_client_lock_guard.py`)

## Tweaks (`tests/test_tweak_sound_channels.py`)
☐ every `TWEAKS_ITEMS` entry with limits/defaults (`TWEAKS_DEFAULTS`) · ☐ Apply/Reset · ☐ `patch_exe` (`WoW.exe` patching, `refresh_pristine_wow`, `read_pristine_wow`) · ☐ `Config.wtf` write/update, sound channels 12–64, FOV default from display, language tweak + restart offer, res-fix confirm · ☐ pfUI default-profile patch · ☐ DXVK conf

## Mods (`test_mod_*`, `test_custom_mods.py`, `test_deploy.py`, `test_planner.py`)
☐ `MODS_REGISTRY`, official + ★ recommended (OctoLogin), DLL load order, `dlls.txt` reconcile/repair · ☐ install/uninstall/update (GitHub/Codeberg/OctoWoW Git), backup/stash/rollback (`_stash_mod_files`, `backup_component`) · ☐ enable/disable, ignore updates, adopt, identify unversioned, force-replace, re-check, version cache · ☐ custom git mods + custom asset mods, conflict detection · ☐ DLL opt-in, essential-mod consent · ☐ migration `_migrate_1_3`, Octo Updater import · ☐ planner product rules (HANDOFF “Product rules”) — **must be moved verbatim**

## Addons (`test_addon_failures.py`, `test_gitcompare.py`, `test_git_hosts.py`, `test_gamepaths.py`)
☐ catalogue, curated/recommended/blocked lists · ☐ scan installed, TOC read, status names, warnings · ☐ install/update/remove/Update All/adopt/switch source/force replace · ☐ git sha/ancestry/local-modified detection · ☐ custom addon URL · ☐ ignore updates · ☐ failure classification (`ADDON_FAILURES`, 20 categories) + log offer · ☐ AddOns case-conflict handling (Linux) · ☐ scroll/filter/selection preserved · ☐ SavedVariables never deleted · ☐ default addons auto-install, `auto_install_addons`

## MPQ (`test_mpq.py`)
☐ scan patches, per-file record, check/fetch/replace/link/unlink custom source, locale patches, sha verification, resume

## Settings
☐ game folder change + reconcile offer (`_game_folder_changed`) · ☐ open client folder · ☐ verify files · ☐ allow through antivirus (Windows) · ☐ aria2 firewall notice · ☐ mirror status · ☐ font, animated bg, close on launch, clear WDB, auto mods/addons, ignore speech · ☐ install missing essential mods / recommended addons · ☐ show logs · ☐ Login Doctor (`logindoctor.py`) incl. repair with `.octobak` · ☐ **SUPPORT ME** / Ko-fi · ☐ language

## Game management / install
☐ client dir detection (`gamepaths`, `octolauncher.py`), version (`get_client_version`), WDB clear · ☐ **base client torrent sync / aria2c / integrity / prune / shield** — **conflicts with 3.0 brief, pending D2** · ☐ Windows installer (`install/`, `deploy.py`, `installer.py`: shortcuts, swap-in-place, repair) · ☐ Linux AppImage (`tools/build_appimage.py`) · ☐ self-update check

## Diagnostics
☐ Login Doctor checks (WoW.exe build 1.18.1/7272, realmlist root+locale, Config.wtf, OctoLogin, DNS ×3, TCP 3724, HTTPS) · ☐ `ENV_PROBLEMS`, `LAUNCH_PROBLEMS` texts · ☐ session log + show logs, addon failure logs · ☐ `_log_mod_diagnostics`, `_log_addon_dir_state` · ☐ `describe_install_error` · ☐ AV exclusion · ☐ **export of diagnostic report with secret redaction (to be verified in code — audit item)**

## Config / state
◐ atomic write + RLock + `update_config` (`config.py`) · ☐ v1→v2 schema migration (`tests/test_config_migration.py`) · ☐ corrupt-config recovery (reads as `{}`, file kept) — **improve: keep `.corrupt` copy**

## Build / CI
☐ `build.py` (hidden imports, Linux host font stack strip, self-test `--gui`) · ☐ `tests.yml` Windows+Linux · ☐ `release.yml`

## New in 3.0 (no parity row)
Skin engine + 5 skins · OctoBot server chip · conditional/dedup/backoff network layer · split config/cache stores · benchmark harness


## Phase 2 status (proof of concept, `python -m equpdater.qtui`)
`☑` = built in 3.0 and tested; `◐` = partly.

☑ Shell, header/nav/footer layout, version, Discord/support links, Settings overlay (skin, animation options) · ☑ Skin engine + Bubbles + Minimal Dark (proof) · ☑ animated background (+ pause minimised/unfocused, disable, still fallback) · ☑ News: Announcements + Changelog, expand/collapse, latest open by default, author/date, links, Open in Browser, NEW!, Read more/Show less (14 lines), Load more / Show more, refresh per panel, persistent cache of listing **and** bodies, offline/error line with cached content kept, pinned-topic ordering, topic-id dedupe, 24 h body refresh, no request on scroll · ☑ PLAY split-button, option detection, saved default (kept when file missing), checkbox menu · ◐ Play start (no pre-launch pipeline, no launch-failure dialogs, no watcher, no `close_on_launch`) · ◐ news.json fallback (Announcements only, newest item + note, as 2.x) · ☐ sticky headers (dropped on purpose: 2.x's sticky header was a source of duplicated text; not reintroduced) · ☐ tab pages other than News (placeholder) · ☐ status line states (UPDATE / Everything up to date / error-as-link) · ☐ update-all arrow · ☐ translations of new strings; QML string extraction · ☐ keyboard focus order audit · ☐ screen-reader labels exist on controls, not yet verified with a reader · ☐ everything under Tweaks/Addons/Mods/MPQ/Settings/Diagnostics/Install

**Removed on purpose (approved):** base-client download via aria2/torrent (ARIA2_AUDIT.md), and per your clarification, skin import/export UI, skin editor, hot-reload tooling.


## Phase 3 status
☑ **Tweaks** (backend lifted verbatim to `core/tweaks.py`, page rebuilt in QML): all 9 tweaks with limits, clamping, language dropdown, Auto Loot checkbox, FOV suggestion, Apply (save → patch WoW.exe → Config.wtf), Reset with confirmation, busy/one-at-a-time, error line · **hardened:** `patch_exe` now refuses a base under 1 MB / not `MZ`, builds in memory, verifies size, and swaps in atomically (2.x truncated WoW.exe first; see the 0-byte WoW.exe found on the dev machine) · ◐ not yet: "restart to change EqUpdater's language" offer, `_migrate_1_3` re-patch (still runs only in the Tk app), Auto Loot `enforce_autoloot` at launch (stays in app.py until the Play pre-launch pipeline is wired)
☐ Mods · ☐ Addons · ☐ MPQ · ☐ Settings (game folder, fonts, language, launcher, Login Doctor, logs) · ☐ Diagnostics · ☐ Game folder detection/verify UI · ☐ Installer/migrations

Lift tool: `tools/lift.py` moves named top-level definitions verbatim and leaves a re-export (no logic change); `pyflakes` and the full suite check it. Tests that patched `app.<name>` for moved code now patch the namespace the function really reads (`func.__globals__`).

Known flaky test (pre-existing, order/timing dependent): `test_custom_mods…test_installs_only_the_chosen_dll_into_mods` once saw a background Tk app's forum requests while it had `secure_urlopen` patched; passes alone and on re-run.


## Phase 3 slice report: Mods (2026-11-24)
**Migrated (backend):** the whole mods backend to `core/mods.py` (80 names: registry, release lookups, install/uninstall/stash/restore, dlls.txt, custom mods, planner glue); shared error text to `core/errors.py`; the addons backend to `core/addons.py` (59 names: catalogue, ownership, git lookups/ancestry, archive install, failure classes, pfUI patch). Logic-only operations lifted verbatim as mixins: `core/mods_ops.py` (apply worker, state load, identification, recheck, adopt, update, force-replace, essential-mods flow), `core/addons_ops.py` (verify, apply, remove, adopt, release, switch source, update-all, defaults, failure report), `core/mods_rows.py` (sections, install/uninstall/release, manage-all, toggles, twin and resolution-fix confirmations). `core/ops_host.py` is the Tk-free host. Dialog calls go through hooks (`_ask_yes_no` ...) and the end of an Apply run through `_mods_finished`.

**Migrated (UI):** Mods page in QML: sections MANAGED / INSTALLED LOCALLY / AVAILABLE with counts and collapse, Manage all, and per row: version, description, the error or update-failed line, the action word with tooltip (update to X, reinstall, not managed -> adopt, version unknown -> recover, can't check -> retry; local newer / modified / can't verify are information only), right-click "install anyway", Ignore updates, install, uninstall (with confirmation), stop managing, open repository. Modal question dialog, keyed incremental model (one mod changes: one row repaints, scroll position kept), skinned tooltips; checked in Bubbles and Minimal Dark.

**Remaining in Mods:** custom git mod dialog (`_open_custom_mod_dialog`, `_remove_custom_mod`, about 500 lines of Tk) and its confirmations; Update All button, preview dialog and the mods-then-addons chain; first-run essential-mods question and default-addons chain wired into the Qt shell; per-mod progress (status line only so far); `_migrate_1_3` and the migration offer on first launch.

**Addons page:** not started in QML. The backend and operations are lifted and covered by the existing suite; the service, model, page and the 10/50/100/250 benchmark come next.

**Known limitations:** `_mod_icons_stale` is a no-op in the Qt host (rows are rebuilt by key); `_status_var` is a thin shim; dialogs use a nested event loop (modal, like Tk).

**Tests added:** `test_v3_core.TestModsService` (7), `test_v3_qt.TestModsPage` (5), `test_v3_safety` (10), earlier slices too. **Suite: 962 tests, all pass.** `tests/ns.py` (namespace-aware patching) replaced about 120 `app.<name>` patches; `tests/__init__.py` blocks the network and fails any test that leaves a worker thread running.

**Regressions found and fixed during the lift:** tests patching `app.secure_urlopen` and similar silently stopped intercepting moved code (they would have reached the network) - fixed with `patch_all`; a Tk app started by tests made real requests (client manifest, both forums, news.json) - now blocked by `EQUPDATER_OFFLINE`; the earlier "flaky" custom-mods test was exactly that traffic.

**Commits:** mods lift `72d6c17`, mixins `7a8b802`, Mods page `788c475`, safety and lock in the commit after it (`git log`).

### Tweaks gaps still open (tracked; do not drop)
1. EqUpdater language restart offer after changing the Language tweak -> Settings slice (language). **Open.**
2. One-time 1.3 re-patch (`_migrate_1_3`; runs only in the Tk app; matters for upgraded 2.x installs) -> game-launch lifecycle / first-run slice. **Open.**
3. Auto Loot enforcement at Play (`enforce_autoloot` is still in `app.py`; needs the Play pre-launch pipeline: dlls.txt reconcile, Auto Loot gate, diagnostics attempt, WDB clear) -> launch lifecycle slice. **Open.**

## Shared records and concurrent use (Phase 3)
* Mods, addons and tweaks records stay in 2.x's `config.json` (schema v2), read and written through `config.update_config` (process-local lock, read-modify-write, temp + fsync + `os.replace`). 3.0-only data (skin, animation, cache, launch default) is in `v3/` and never read by 2.x, so version-specific settings stay isolated.
* Tests and benchmarks run with `EQU3_DATA_DIR`/`LOCALAPPDATA` redirected, a throwaway `HOME` and the network blocked (`EQUPDATER_OFFLINE`); asserted in `test_v3_qt.TestTweaksPage`.
* A per-game-folder OS lock (`core/oplock.py`, SAFETY.md) serialises installs and patches among 3.0 processes. The released 2.1.4 does not take it, so do not run 2.1.4 and 3.0 on the same folder at the same time (to be stated in the beta notes).


## Phase 3 slice report: Addons (2026-11-24)
**Migrated (UI + service):** `core/addons_service.py` (Qt-free: rows, status words, warnings, filter, footer, click routing) and the QML Addons page: search box (Python filter, debounced 150 ms, space/case insensitive), sections MANAGED / INSTALLED LOCALLY / AVAILABLE with counts and collapse (recommended addons first in AVAILABLE), per row: WoW-colour title (escaped rich text), star for recommended, description, status word with tooltip (Update with `old -> new` commit tooltip, Manage for found addons, can't check -> retry, downloading..., Addon error, held-back states as plain statements: local newer / modified / different source / diverged / unverifiable / ignored / not versioned, and compatibility warnings: made for another client, missing dependencies, never for pfUI), Replace... where 2.x offered it, switch-source icon, open repository, install, uninstall (confirmation), stop managing, the "could not check / error" line, footer (Checking... / Update all / Nothing to update - N held back / Everything up to date), nav badge from the same rule as 2.x (only proven updates).
**Backend now Tk-free end to end:** the verify/apply/remove/adopt/release/switch/force-replace/update-all/default-install operations run on `OpsHost`; the addon-folder case-conflict prompt (`_addon_dir_conflict_prompt`) is lifted too, with a yes/no/cancel hook (my first host stub asked it on every verify and hung the page: found by the skin/page test, fixed, regression tests added).
**Performance:** see PERFORMANCE.md "Addons page". Pooled delegates, lazy tooltips, keyed model with real row moves (an installed addon moves from AVAILABLE to MANAGED as a move, not a reset).
**Remaining in Addons:** custom addon URL dialog (`_open_custom_addon_dialog`), the "found addons" Manage-all offer on first run and the OctoLauncher migration prompt, Update All with preview and the mods->addons chain (shared with Mods), per-addon progress text beyond `downloading...`, the pfUI lua writes (not atomic), `_addon_updates` count in the status area.
**Known limitations:** the page uses 2.x's `ADDONS_VERIFY_TTL` cache, so opening the tab does not rescan within its TTL (same as 2.x); the benchmark uses synthetic addons, not a real 250-addon install with real remote checks (needs network and a large AddOns folder: to run in Phase 7 on a prepared machine).
**Tests added:** `TestAddonsService` (11: sections, search, status rules, warnings, rich text safety, footer, click routing, badge, no question when folders are fine, ask-once/decide-later), `TestAddonsPage` (6: unopened page allocates nothing, virtualised list, both skins, one row repainted, move not reset, search, scroll kept, section collapse without reset). **Suite: 977 + new, all pass.**
**Regressions found/fixed:** host prompt hang (above); a QML syntax slip caught by an error-checking helper; a per-row popup cost (above).


## Phase 3 slice report: MPQ and Settings (2026-11-24)

### MPQ
**Backend lifted:** `core/mpq_ops.py` (catalogue `MPQ_PATCHES`, `mpq_patch_for`, `sha256_file`, `fetch_mpq_sha256`, `resolve_mpq_source`, checksum-verified `download_mpq` with `.part` + replace and an optional dated `.bak`, release cache) and `core/mpq_page_ops.py` (`MpqPageOps`: scan of `Data/patch-<letter>.mpq`, check against linked sources, install/update/replace/uninstall/unlink/link, one-pack-at-a-time busy state, the state table). The download is now taken under the game-folder lock. `core/mpq_service.py` shapes rows.
**Page:** INSTALLED / AVAILABLE sections, pack name + file, description, credit ("By ... / Project page"), source line ("Source: not linked" + Link source...), state words with tooltips (modified, unable to verify, differs from source, source not confirmed), Update / Install / Replace... (asks first, keeps the old file as `.bak`) / uninstall (asks) / stop managing, "Ignore speech.mpq", the link dialog (catalogue entry or pasted address, refuses a bad address with the reason, links change no files), nav badge for updates. A pack nobody linked is listed but never checked or offered an update (as in 2.x).
**Not carried over:** nothing removed. **Remaining:** none known beyond the common items below.
**Tests:** `TestMpqService` (7), `TestMpqPage` (3) + the existing `test_mpq` / texture-pack tests, which now patch the network where the code reads it.

### Settings
**Migrated:** game folder (shown, changed by typing or by the native folder picker, validated, `Open folder`); the change runs the lifted `_game_folder_changed` (clears the old folder's mod/addon records, resets session state, except when the old path was a Windows/Wine path being moved across). **The game folder is a shared record** (`out_dir` in the 2.x config); `settings.json` is only a fallback, so 2.x and 3.0 cannot disagree. Clear WDB on launch, Minimize on launch, Install essential mods, Install recommended addons (turning one on installs what is missing when Settings closes, off cancels it; a choice made here counts as the answer to the first-run question), Animated background, Pause when unfocused (3.0), Language (read-only; follows the Language tweak), Font (skin default / Arial / Friz Quadrata / OpenDyslexic, applied live to every text style, OpenDyslexic scaled to 0.92, locked in Chinese as in 2.x; bundled fonts registered from `fonts/`; imported from 2.x), Skin, Open skins folder, Restore default skin, Show Session Log (last 2,000 lines).
**Tracked Tweaks gaps:**
1. **Language restart offer: DONE.** After a Tweaks Apply/Reset has finished writing, `_offer_language_restart` asks (in the new language, never forced); yes starts a new process and closes this one. EqUpdater's own text now follows the saved Language at start (the PoC had been English-only: found while wiring this).
2. **One-time 1.3 re-patch: DONE.** `_new_version_installed` / `_migrate_1_3` lifted verbatim into the shared mixin and run after the first frame on a worker (re-patch frillDistance, raise frillDensity, clean stale dlls.txt entries, refresh dxvk.conf, drop obsolete keys); only for non-first-run installs older than 1.3, stamped once. Config.wtf write made atomic. Tested.
3. **Auto Loot enforcement at Play: OPEN** (needs the Play pre-launch pipeline, next).
**Deliberately not migrated:** *Download mirror* status and *Verify game files* via the torrent (the base-client downloader is gone; `ARIA2_AUDIT.md`), the firewall notice for aria2c, "pending reconcile" (belongs to the client sync). *Verify game files* returns with Diagnostics as a read-only manifest check.
**Remaining in Settings:** Login Doctor, Game Diagnostics, Exclude from Defender, Game launcher... (Linux) - all in the Diagnostics slice; first-run language prompt (2.x asked once on a first launch; the Qt shell does not yet).
**Tests:** `TestGeneralService` (8), `TestVersionMigration` (2), `TestSettingsPage` (5). **Suite: 1,004, all pass.**
**Regressions found/fixed:** a closure over a Qt signal inside the class body (import error), a Qt `FolderDialog` result needing `file:///` stripping, UI always English in the PoC (language never set).
