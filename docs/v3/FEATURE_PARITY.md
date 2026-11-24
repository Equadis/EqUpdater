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
