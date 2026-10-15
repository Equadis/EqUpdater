# EqUpdater development handoff

**Version:** 2.1.0 (released 2026-10-12, re-released with fixes 2026-10-14)
**Date:** 2026-10-15
**Project:** EqUpdater, derived from rebasedkon/octo-updater
**Repository:** https://github.com/Equadis/EqUpdater (renamed from
`eqomdx/EqUpdater` on 2026-10-14; GitHub redirects the old name)
**Owner:** Equadis (Oliver)

---

## State at handoff

- **2.1.0 is live** on the release page with both files, built from `main`
  at `7b47dd6` (custom git mods, addon scroll fix, installer Unicode fix):
  - `EqUpdater-v2.1.0-Windows.exe` (61,176,725 bytes)
  - `EqUpdater-v2.1.0-Linux-x86_64.AppImage` (51,096,056 bytes)
  Uploaded by hand on 2026-10-14 14:05 (see "Releasing" for why).
- **`main` is at `7124366`**, green on Windows and Linux. The only commits
  after the released build are README/handoff, the repo rename and a test
  fix; no app behaviour changed. `branding.APP_VERSION` = `"2.1.0"`.
- **The `v2.1.0` tag still points at `2ebb983`** (the PR #2 merge), not at
  the code the release files were built from. The tag could not be moved:
  a tag rule blocks it (403). Only the source-code zips on the release are
  affected; players use the two files.
- **Release notes** are current (custom mods, fixes, Defender note). One
  pending edit: the top note still says the false positive "has been
  reported to Microsoft"; change it to just "a known false positive for new,
  unsigned apps" (owner decided not to submit to Microsoft).
- **Open GitHub issues:** #1 (addon list jumping to the top) is fixed in the
  re-released 2.1.0 - close it. #3 "Vanilla Autologin Mod" (papafru) is
  undecided; see Backlog.
- **Branches:** only `main` (plus `claude/equpdater-linux-crash-h9g644`,
  the assistant's work branch, identical to `main`; safe to delete).
- **Sent to OctoWoW (2026-10-14):** a request to exempt EqUpdater's
  read-only requests from the BlazingFast challenge (see "OctoWoW DDoS
  protection"), and to change the site's Discord link to
  https://discord.gg/QtdNpKnU72. No reply yet.

---

## Product rules (do not break)

- Never automatically downgrade a known local version.
- Only Update All components that EqUpdater manages and can prove are newer.
- Unmanaged/manual addons and mods stay unmanaged until explicitly adopted.
- Locally modified, divergent, local-newer, ignored, unknown or
  source-changed components are held back, and the reason is shown.
- `dlls.txt` registration repair is allowed without replacing the DLL.
- Replacing or downgrading asks first and keeps a backup.
- DLL mods are opt-in (asked once). Custom mods need an explicit warning
  confirmation.
- **Never circumvent OctoWoW's DDoS protection** (no challenge solving, no
  borrowing another client's User-Agent).
- **Priority Sign In is out of scope:** do not read OctoLauncher's
  `auth.json` or tokens, call `/api/launcher/session` or `/api/launcher/
  address`, spoof `x-launcher-version`, or reverse-engineer launcher
  authentication. Login Doctor may only *recommend* the official
  OctoLauncher's Priority Sign In, and must not suggest EqUpdater's PLAY
  can use it.
- Every user-facing string in all six languages (`tests/test_i18n.py`).

---

## How to work on this repo

- **Run the suite:** `python tools/check.py` (Windows) or
  `xvfb-run -a python3.12 -m unittest discover -s tests -t .` (Linux).
  521 tests. "main thread is not in main loop" tracebacks printed by
  background threads during the run are pre-existing noise, not failures.
- **CI:** `.github/workflows/tests.yml` runs on every push (Windows +
  Linux/xvfb, 20 min timeout). `.github/workflows/release.yml` runs on
  pull requests, on `workflow_dispatch`, and on a published release (only
  then does it attach files to the release).
- **Tests that build an app** must not replace `threading.Thread` for the
  whole process (an inline animation/news worker never returns - it hung
  Windows CI); run only the worker under test inline. Close dialogs by
  destroying them or stub `_prompt_av_exclusion`/`_offer_reconcile`:
  `_close_settings` is "confirm" and can reach a Windows-only prompt. Do not
  assert "no overlay is open" - a first-run prompt may open its own; assert
  the dialog you opened is gone.
- **Test builds** use a fourth version number: `2.0.6.1`, `2.0.6.2`, ...
  so testers can tell builds apart. Bump `APP_VERSION` for every build sent
  to testers; bump to the release version only when releasing.
- **Commits** end with the attribution lines the session asks for; no model
  names in commits, code or docs.

## Releasing (as it actually works today)

The intended path: publish a GitHub release for tag `vX.Y.Z` -> the release
workflow builds, self-tests and attaches both files from that commit.

What happened with 2.1.0 and how to re-release a fixed build under the same
version:

1. Merge the fix to `main` and wait for `tests` to pass.
2. Run the **release** workflow on `main` (Actions -> release -> Run
   workflow). It builds and self-tests both files as workflow artifacts
   (it does not attach them).
3. Download both artifacts (each is a zip; unzip it), then on the release
   page: Edit -> delete the two old files -> drag in the `.exe` and the
   `.AppImage` (not the zips) -> Update release.
4. Check the release lists exactly the two files with new sizes.

Moving the tag (`git push --force origin vX.Y.Z`) and deleting tags are
refused by a tag rule (Settings -> Rules). Either relax it, or ship fixes
as `X.Y.Z+1` - simpler and keeps tag, source zip and files in step.

**Windows Defender** flags new builds as `Trojan:Win32/Wacatac.B!ml`, an ML
false positive common to unsigned PyInstaller apps; Chrome shows "Failed -
Virus detected". Players allow it in Windows Security -> Protection history
-> Allow on device. It fades as the file gains reputation; each new build
starts again. Real fixes: code signing; a bootloader built from source in
the release workflow often helps. Submitting to Microsoft
(https://www.microsoft.com/wdsi/filesubmission) clears a build in 1-3 days
but the owner prefers not to.

---

## What 2.1 added (summary)

- Native Linux (AppImage) and a real Windows installer, from one codebase.
- Login Doctor (Settings -> Troubleshooting) - `equpdater/logindoctor.py`.
- OctoLogin (★ recommended mod, `mods/OctoLogin.dll`, loads last).
- Custom git mods (Mods -> + Add custom git mod).
- Sound Channels tweak (12-64); Tweaks list scrolls.
- News from forum threads t=2848 / t=2816 with `news.json` fallback.
- Visible addon failures; addon list keeps its place; case-safe AddOns on
  Linux; Unicode-safe installer shortcuts; host font stack on Linux.
- Default game folder `~/Games/OctoWoW`; `Config.wtf` no longer gets a realm.

---

## Custom git mods (2026-10-14)

`app.py`, "Custom mods" block next to `remove_dll`, and
`_open_custom_mod_dialog` / `_remove_custom_mod` in the app class.

- `parse_mod_release_link` accepts a GitHub, Codeberg or OctoWoW Git
  repository, its `/releases` page, or a release download of a `.dll`
  (which preselects it). Anything else is refused with an N_ message.
- The dialog looks up the latest release (`_release_latest`, worker
  thread), lists its `.dll` assets (`release_dlls`), and on Install shows a
  warning (`_confirm_custom_mod`: runs code in the game, not checked, only
  trusted mods). Declining installs nothing.
- `add_custom_mod` stores `{kind, owner, repo, dll[, forge]}` in config
  `custom_mods`; `load_custom_mods` rebuilds the custom entries of
  `MODS_REGISTRY` **in place** (every reader sees them) - called when the
  Mods panel is built and after add/remove. Each entry is an ordinary mod:
  id `custom:<dll lower>`, `dest`/`register_dll` `mods/<dll>`,
  `asset_pattern` = `glob.escape(dll)`, not essential. So install, update
  (latest release), enable/disable, the planner and `dlls.txt` sweep all
  apply unchanged. Unknown `dlls.txt` entries sort after the known mods.
- `custom_mod_conflict` refuses non-plain names and any name a listed or
  custom mod already uses. ✕ on a custom row: confirm, delete the file,
  `remove_dll`, `forget_custom_mod` (record, mods entry, release cache).
- `install_mod` handles `gitea_release` (OctoWoW Git) and honours `dest`
  for Codeberg; `RELEASE_KINDS` lists the three release kinds.
- Tests: `tests/test_custom_mods.py` (16).

## OctoLogin and the mods folder (2026-10-12)

OctoLogin (fmustafayaman/OctoLogin, MIT) is installed from the release's
bare `OctoLogin.dll` to `mods/OctoLogin.dll`, as its own installer does,
and registered by that path; it reads `OctoLogin.ini` and writes
`OctoLogin.log` beside it. It is last in `DLL_LOAD_ORDER` (hooks only the
connect import and stands aside if another mod got there first).
`dlls.txt` entries compare without case or slash direction (`_dll_key`).
Essential: installed only with consent. Tests: `test_mod_octologin.py`.

## Addon failures (2026-10-10)

`addon_failure(e, url)` classifies an install/update failure (walking the
exception chain) into: challenge (DDoS page), forbidden 403, rate_limit 429,
not_found 404, server 5xx, http, timeout, dns, tls, connection, network,
archive (bad zip), lookup, conflict (AddOns case conflict), source,
no_space, permission, antivirus (Windows only), write, unknown - each an
`ADDON_FAILURES` template naming the host. `_addon_apply` logs the English
text and the exception chain, restores each failed row's previous state,
and `_report_addon_failures` shows one dialog (single addon) or one summary
(batch), offering the log. Failed updates keep the installed copy and its
record. `install_addon_files` moves the old copy aside and restores it if
the final rename fails. Renders during install/update/remove keep the
scroll position (`_render_addons(keep_scroll=True)`).
Tests: `tests/test_addon_failures.py`.

## Linux: what testers found (2026-10-06 .. 10-10)

- **Start-up crash** - `PIL._tkinter_finder` missing from the frozen build:
  added as a hidden import (`tests/test_build_hidden_imports.py`); the
  AppImage self-test builds the GUI.
- **AddOns letter case** - `equpdater/gamepaths.py`: every WoW path goes
  through `game_path()`; `prepare_addons_dir()` renames a lone
  `Interface/Addons` to `AddOns`, merges duplicates, collapses identical
  copies and records conflicts; `writable_addons_dir()` raises
  `AddonDirConflict` until the player chooses which copy to keep
  (`resolve_conflicts`, the other is moved to `AddOns-conflicts-<time>`).
  Wine `Z:\` paths map to native ones. Tests: `test_gamepaths.py`.
- **Fontconfig errors on Fedora** ("invalid attribute 'xsi:nil'") - the
  build machine's fontconfig 2.13 was bundled. `build.py` strips
  `platforms.HOST_FONT_STACK` (fontconfig, freetype, expat, libpng16,
  brotli) from the Linux folder build so the host's matching stack loads;
  `--self-test --gui` fails if any is bundled or fontconfig loads from the
  bundle. New host dependency: those libraries (on every desktop distro).
  Tests: `test_build_font_stack.py`.
- Mouse wheel scrolls lists; taskbar/launch environment cleaned
  (`platforms.child_env`).

## Windows installer shortcuts (2026-10-13)

`equpdater/deploy.py` reads/writes `.lnk` files through PowerShell.
PowerShell is told to write UTF-8 and the bytes are decoded in Python
(text mode decoded with the ANSI page and returned `None` -> "'NoneType'
object has no attribute 'splitlines'"). Shortcuts go through the shell's
Unicode `IShellLinkW` (C# compiled by `Add-Type`, source in `EQU_CS`);
`WScript.Shell` (ANSI-only) is the fallback where `Add-Type` is blocked.
`installer._shortcut` skips a shortcut that cannot be written instead of
failing an install that already succeeded. Tests: `test_deploy.py`
(real Cyrillic/CJK/accented shortcuts on Windows CI).

## Login Doctor (2026-10-08)

`equpdater/logindoctor.py` (pure checks, injectable network) and
`_open_login_doctor` in `app.py`. Checks `WoW.exe` and build (1.18.1,
7272; a mismatch is a warning), `realmlist.wtf` (root and
`Data/<locale>/`), `SET realmList` in `Config.wtf`, OctoLogin, DNS for
play/octowow.st/dl.octowow.st, TCP 3724 (informational) and HTTPS.
**Repair login configuration** sets the realmlist to `play.octowow.st`,
removes a stale `SET realmList`, refuses while the game runs, and keeps
`<file>.octobak` (never overwritten). Advice when nothing local is wrong:
turn off VPN/proxy at "Authenticating"; if it still fails, try the official
OctoLauncher with Priority Sign In (EqUpdater cannot use it).
Tests: `test_logindoctor.py`.

---

## Current UI

- Full-window background: `bubbles.gif` animated (default), `pubbles.png`
  static. Settings → **Animated background** (config `animated_background`,
  default true). `ui.AnimatedBackground` decodes/scales/darkens frames on a
  worker thread into a 4-frame queue; the Tk thread only pastes one
  PhotoImage per frame on an `after()` clock (the current GIF is 80-100 ms
  per frame, a 52 s loop; 0% CPU when off or minimised). Any failure logs and
  restores the PNG. `bubbles.jpg` retired.
- Edge fades (`ui.edge_fade_layers`) are baked into the still and every
  frame: the top eases to dark over 120 px, the bottom over the footer plus
  70 px. The header is drawn on the background canvas itself (it used to be
  a separate canvas with a still crop, which showed a hard line once the
  background moved), and the footer's status, version, progress bar and
  text are canvas items over the fade -- there is no solid footer block.
  `_layout_footer()` re-places them from the font's line height.
- Dark navy panels over the background.
- Reusable Canvas gradient buttons for PLAY / UPDATE / UPDATE ALL.
- The one-pixel/right-edge gap in the gradient button fill is fixed.
- Settings support heading is now **SUPPORT ME**.
- The old bottom attribution/support strip has been removed from Settings.
- Buy Me a Coffee opens `https://ko-fi.com/equadis`.

## Icon / Windows taskbar

The canonical source is `icon.png` and must remain the supplied pink octopus + wrench image.

Current SHA-256 of `icon.png`:

`605efe13b379d9c722e0ce0b630b40988e9f4e285b2d3d36c8ee20d3676c839c`

`icon.ico` is regenerated from that image and contains:

`16, 20, 24, 32, 40, 48, 64, 96, 128, 256 px`

`build.py` uses `icon.ico` for the PyInstaller executable and adds both `icon.ico` and `icon.png` as runtime data.

The runtime uses `iconphoto()` with `icon.png` first, then `iconbitmap()`/ICO as fallback.

Windows also receives this explicit AppUserModelID before Tk creates the window:

`eqomdx.EqUpdater.2`

This was added specifically to stop Windows reusing the old Octo/EqUpdater taskbar group/icon cache.

If a user has an old *pinned* shortcut, Windows may still show that pinned shortcut's cached icon until it is unpinned/re-pinned, but the running process now has its own taskbar identity and current icon.

## Fonts

Font choices in Settings:

1. Arial — default (a saved choice still wins)
2. Friz Quadrata
3. OpenDyslexic

The FONT section sits under GENERAL in Settings' right column; the panel
grows to fit (`_fit_settings_panel`), which keeps all three reachable in
OpenDyslexic (650 of a possible 680 px at 100% scaling).

OpenDyslexic currently uses a `0.92` size multiplier because 1.00 overflowed fixed-height UI and 0.82 was too small.

EqUpdater privately registers `.ttf` / `.otf` files on Windows using `AddFontResourceExW(..., FR_PRIVATE, ...)`; it does not need an administrator/system-wide font install.

**The fonts ship with EqUpdater** (2026-10-01). `fonts/` holds Friz
Quadrata (Regular, BoldItalic) and OpenDyslexic 0.990 (four styles) with its
OFL licence; `build.py` packs the folder into `_internal/fonts`, and
`_bundled_font_paths()` registers it plus anything dropped into
`%LOCALAPPDATA%\EqUpdater\fonts`. Arial is not shipped: it comes with Windows.
Friz Quadrata is commercial (URW); Ollie has permission to ship it, and
`fonts/README.txt` says it is not covered by any EqUpdater licence.

The old route -- the installer and the app fishing `friz*.zip`,
`arial*.zip`, `opendyslexic*.zip` out of Downloads, Desktop and Documents --
is gone. It left users without fonts whenever the zips were not there.

Settings draws each font's name in that font (`FontManager.preview_font`);
`apply_tree` skips widgets marked `keeps_own_font`, so the list keeps its
faces whichever font is selected.

`FontManager` still detects each family's real name before enabling a choice.

Embedded family names verified during this work:

- Friz: `Friz Quadrata`
- Arial: `Arial`
- OpenDyslexic: `OpenDyslexic`

Do not silently present OpenDyslexic while rendering a fallback family.

## Languages (2026-10-01)

EqUpdater's own text follows the saved Language tweak (it also sets the
game's language; it was called "Game Language" until 2026-10-01): English,
German, Russian, Simplified Chinese, Spanish, Portuguese (BR). A game
language without a translation shows English. `equpdater/i18n.py` holds the
mechanism; `equpdater/locales/{de,ru,zh_cn,es,pt_br}.py` hold one dict each,
keyed by the English text.

- Wrap shown text in `tr("…")`, with named placeholders:
  `tr("Delete {folder}?", folder=name)`. Text defined in a table at import
  time is marked `N_("…")` and passed through `tr()` where it is shown.
- `tests/test_i18n.py` collects every `tr`/`N_` literal and fails if any
  language lacks it, keeps a stale one, or changes the placeholders. Adding
  or rewording English text therefore means adding the five translations.
- The language is set once, in `EqUpdaterApp.__init__`, before anything is
  drawn. Applying (or resetting) tweaks with a new language offers a
  restart, asked in the new language, and only after WoW.exe is written.
- **First launch** (no config file yet) shows `_ask_first_language` before
  the language is set, so the window opens in the choice with no restart.
  Each button is in its own language. Closing it keeps what is saved
  (English, or what an Octo Updater import brought). The smoke tests set
  `EQUPDATER_NO_LANGUAGE_PROMPT=1`, since every test app is a first launch.
- **Not translated on purpose:** the session log (support reads it), news
  posts, addon descriptions from the catalogue, server/OS error text, and
  names. Stored errors stay English and are translated where shown.
- The translations were written by the assistant, not native speakers.
  Have a native speaker read each before relying on them.
- Friz Quadrata has no Cyrillic; Russian in Friz falls back to another face
  for those letters. Arial and OpenDyslexic cover the Latin and Cyrillic
  languages.
- None of the three fonts has Chinese; Windows draws it in its own Chinese
  font whatever is chosen. So in Chinese the FONT section in Settings is
  greyed out (`i18n.SYSTEM_FONT_LANGUAGES`); the saved choice is kept.
- Longer languages needed room: the Tweaks name column, the Mods name
  column and the footer buttons size themselves to their text.

## Install location and updates (2026-10-01)

The app lives in one place: `%LOCALAPPDATA%\Programs\EqUpdater\EqUpdater.exe`.
Player state stays in `%LOCALAPPDATA%\EqUpdater` and is never touched by the
installer.

Before this, INSTALL.cmd built into the download folder
(`<download>\dist\EqUpdater`) and pointed the desktop shortcut there. Every
version's zip is its own folder, so after updating, the shortcut still
opened the old version (a player's report: "2.0.5 installed, launcher still
opens 2.0.4").

`install/deploy.py` (called by install.ps1 after the build, step 4/4):

- copies the build to `EqUpdater.new`, renames the current copy to
  `EqUpdater.old`, renames `.new` into place, deletes `.old`. A failed swap
  puts `.old` back; a run that died between steps is repaired by the next
  run (`repair()`). A running EqUpdater.exe (cannot be opened for writing)
  stops it before anything changes.
- rewrites every shortcut whose target is a file named EqUpdater.exe -
  desktop (the real one, OneDrive included), Start menu, taskbar pinned - to
  the permanent exe, working directory and icon. Compared through
  GetLongPathNameW: the shell stores long paths, %TEMP% is often 8.3.
- lists old download folders as deletable, never a git checkout.

Tests: `tests/test_deploy.py`, with real .lnk files on Windows.

**Do not run INSTALL.cmd / install.ps1 from inside the Claude desktop app.**
Its processes see a private copy of %LOCALAPPDATA% (MSIX virtualization) but
the real desktop, so the install would land in the private copy and the
real shortcut would be pointed at it.

The first-run default game folder is `~/Games/OctoWoW` (it was
`<app folder>\OctoWoW`, which an update replaces), and `deploy` carries
anything that is not part of the build out of the old app folder before
deleting it (`carry_over`), so a game kept there by 2.0.6 survives.

## Windows and Linux (2026-10-05)

One codebase. `equpdater/platforms.py` holds only what differs by OS:

| | Windows | Linux |
|---|---|---|
| open a folder / URL | `explorer.exe` / webbrowser | `xdg-open` |
| sync's folder link | NTFS junction (`mklink /J`) | symlink |
| aria2c | pinned download, sha-checked (app.ensure_aria2c) | AppImage's own (beside the exe, `$APPDIR/usr/bin`), then PATH; never aria2c.exe under Wine |
| game running? | WoW.exe cannot be opened for writing | `/proc`: this client's WoW.exe / VanillaFixes.exe (Z:\ or Unix path, or working folder); wine/wineserver never count; unplaceable counts; read-only WoW.exe counts |
| PLAY | the exe, detached from the job | launch command (`{exe}`, `{dir}`), else `wine`, else `umu-run`; `WINEPREFIX` from Settings |
| child processes | no console | `child_env`: the frozen app's `LD_LIBRARY_PATH` and our `FONTCONFIG_FILE` are taken back out |
| fonts | GDI `AddFontResourceExW` (FR_PRIVATE) | fontconfig file including `/etc/fonts/fonts.conf` + bundled dirs, set before Tk starts (`ui.prepare_fonts`) |
| restart | `sys.executable` | `$APPIMAGE` when in an AppImage |

Settings on Linux has **Game launcher** (`game_launcher` in config:
`launch_command`, `wine_prefix`). Defender and Windows-firewall prompts are
Windows-only. `WoW.exe` is the same file, patched at the same offsets, on
both.

TLS: `make_ssl_context` loads certifi, the frozen bundle's own
`certifi/cacert.pem` (found directly, not only via certifi), and on Linux
the distribution's bundle; `CA_SOURCES` says which. Verification, hostname
checks and TLS >= 1.2 are never relaxed. (A Windows build under Wine failed
"unable to get local issuer certificate": certifi's load failed silently
and Wine's store is empty. dl.octowow.st verifies with certifi alone.)

**Release artifacts** (`.github/workflows/release.yml`, on a published
release; pull requests build them as workflow artifacts):
- `EqUpdater-vX.Y.Z-Windows.exe` - `build.py --setup`: a one-file
  installer (`equpdater/installer.py`, entry `EqUpdaterSetup.py`) holding
  the folder build; installs it with `deploy`, Start-menu entry, desktop
  shortcut on request, starts it. The app stays a folder build (no %TEMP%
  unpacking per launch).
- `EqUpdater-vX.Y.Z-Linux-x86_64.AppImage` - `tools/build_appimage.py` on
  ubuntu-22.04 with deadsnakes Python 3.12 + system Tk: folder build,
  pinned static aria2c (abcfy2 1.37.0 musl), pinned appimagetool 1.9.1
  (static runtime: no libfuse2 needed). Both downloads sha256-pinned.
- Each runs `EqUpdater.py --self-test=<json> --network` before upload; the
  AppImage also runs `--gui` under xvfb (builds the real window, checks the
  Pillow/Tk bridge, Login Doctor, the fonts and the font stack).

Tested on real Linux desktops by testers (Fedora, Ubuntu-likes) through
2.0.6.x test builds; see "Linux: what testers found" below.

## Git hosts, OctoWoW Git included (2026-10-01)

`gitcompare.GIT_HOSTS` is the one list of hosts addon sources may live on:
GitHub, GitLab, gitea.com, Codeberg and OctoWoW Git. Each is a `GitHost`
with an optional path **prefix** - OctoWoW's Gitea is mounted at
`octowow.st/git/`, so repositories are `/git/<owner>/<repo>` and the API is
`/git/api/v1`. `repo_ref()` / `parse_repo()` / `same_repo()`, the compare
URLs, the app's `_git_parts`, `is_allowed_git_url`, `ADDON_GIT_HOSTS`
(shown in the custom-addon dialog), `ADDON_ZIP_HOSTS` and the texture-pack
release sources (`mpq.RELEASE_HOSTS`, kind `gitea_release` with a `forge`
label) all read it. Adding another prefixed Gitea is one entry there.

On a known host a URL must be exactly `[<prefix>/]<owner>/<repo>` (optional
`.git`, trailing slash, `www.`); a page inside a repository is not one. An
unknown host is no longer guessed to be a Gitea.

**octowow.st/git's API (and git's info/refs) answers apps with the
BlazingFast "Just a moment please" page today**, like the forum.
`_refuse_challenge` raises `DdosCheckError` (message `DDOS_CHECK`) instead
of a JSON error or "corrupted archive".

Its **archives are not blocked**, and Gitea stamps the commit into the
zip's comment (`git archive`). So when a Gitea's commits API is blocked,
`addon_remote_sha` falls back to `_commit_from_archive`: it downloads
`<repo>/archive/HEAD.zip` (or the pinned branch's), reads the commit with
`archive_commit`, and keeps the bytes for ten minutes in `_ARCHIVES` so the
install that follows does not download again. Installs and update
detection work; ancestry (the compare API) still answers UNKNOWN, so a
changed addon is "Unable to verify" - never updated automatically - and
its row offers Replace… (`addon_offers_replace`: unverifiable with a
different remote commit). Verified live on 2026-10-01 against
olzon/GuildRecipes_Octo. When OctoWoW allowlists `EqUpdater/` (requested 2026-10-14) the API
answers and none of the fallback runs; the full update flow returns by
itself. Tests: `tests/test_git_hosts.py` (TestArchiveFallback).

## News / changelog

`equpdater/news.py` owns fetching/parsing; `app.py` only renders.

- **Announcements** = the newest post in forum thread **t=2848**
  (`fetch_newest_post_in_topic`, one request, `sk=t&sd=d`). If the forum
  cannot be read (typically the DDoS page), the newest item of
  **`https://octowow.st/news.json`** (OctoLauncher's public feed, validated
  against its schema) is shown instead, with a note saying why.
- **Changelog** = the newest `NEWS_CHANGELOG_COUNT` (8) posts in thread
  **t=2816** (`fetch_topic_posts`), each with its date, author and link.
- Read once at launch and on each refresh button; never on tab switch, no
  timer. The last good content is cached in the config and kept on screen
  when a refresh fails; the failure is shown and logged.
- The forum-listing readers (`fetch_forum_topics`, `fetch_latest_post`,
  `fetch_topic_list`) remain, tested, unused on screen.
- `news.json` freshness is not a release requirement. Gate wording: "News
  loads correctly; current forum content is used when reachable, and the
  public news feed/cache provides a safe fallback when the forum is
  unavailable."

## OctoWoW DDoS protection

octowow.st is behind **BlazingFast**: non-browser clients get a JavaScript
challenge (HTTP 200, `X-BF-Challenge`), detected by `news.is_challenge_page`
and `_refuse_challenge` -> `DdosCheckError`. OctoBot gets through via a
named User-Agent allowlist entry; EqUpdater must not borrow it.

Requested from OctoWoW on 2026-10-14 (any one of): exempt the read-only
paths (`/git/api/v1/*`, `/api/addons.json`, `/news.json`, the two forum
topics) from the challenge with normal rate limiting; or exempt User-Agent
prefix `EqUpdater/` on those paths; or issue an app key/header. If they
allowlist, nothing needs to change - News and OctoWoW Git update checks
start working for 2.1.0 users. If they issue a key/header, add it to the
requests to octowow.st in a 2.1.1.

What EqUpdater requests from octowow.st: forum t=2848 and t=2816 (launch,
refresh), `news.json` (fallback), `/api/addons.json` (daily), Gitea API
commits/compare/releases (cached 1 h), repository archives (install/update;
not blocked), `https://octowow.st/` (Login Doctor). User-Agent
`EqUpdater/<version>`.

## Important files

- `equpdater/app.py` — Tk app, panels, settings, mods/addons UI, footer/update flows, custom mods, addon failures
- `equpdater/ui.py` — FontManager, private font loading, background/gradient UI helpers
- `equpdater/news.py` — announcements/changelog fetching and parsing
- `equpdater/logindoctor.py` — Login Doctor checks and repair
- `equpdater/gamepaths.py` — case-safe WoW paths and the AddOns folder
- `equpdater/platforms.py` — everything that differs by OS; `HOST_FONT_STACK`
- `equpdater/deploy.py`, `equpdater/installer.py` — Windows install/update, shortcuts
- `equpdater/mpq.py` — texture-pack sources and judging
- `equpdater/planner.py` — central safe update planning
- `equpdater/versions.py` — version comparison
- `equpdater/gitcompare.py` — git hosts, source/commit ancestry checks
- `equpdater/hashing.py` — content fingerprints
- `equpdater/config.py` — schema/provenance/migration
- `equpdater/branding.py` — product identity, `APP_VERSION`, `PROJECT_REPO`, Windows App ID
- `equpdater/locales/` — de, es, pt_br, ru, zh_cn
- `EqUpdater.py` — entry point and `--self-test[=json] [--network] [--gui]`
- `build.py` — PyInstaller build (`--setup` Windows installer); `tools/build_appimage.py` — AppImage
- `.github/workflows/tests.yml`, `release.yml`
- `tests/` — 521 tests: safety, migration, news, GUI smoke, Linux paths, packaging

The Windows taskbar ID `eqomdx.EqUpdater.2` was deliberately not renamed
with the repository: changing it would split existing taskbar pins.

## Current tests

`python tools/check.py` -- 521 tests. Runs the suite in a child process
with faulthandler, prints only failures (name, exception, traceback) or, if
the process crashed, the test that was running and every thread's stack.
`python build.py --release` refuses to build if the suite fails.

**The installer does not run the suite** (since 2026-09-27); see the
Tk teardown race below for why tests close apps through `_close_app`.

`AnimatedBackground`'s worker is `ui._FrameProducer`, which holds no Tk
object; `AnimatedBackground.stop(wait)` joins it and releases the
PhotoImage on the Tk thread; `EqUpdaterApp.after` schedules nothing once
destroy has begun; tests close apps through `_close_app` (waits for the
worker, fails on a leak, keeps the app referenced so no worker can free it).
`TestTkTeardownRace` reproduces the old `Tcl_AsyncDelete` crash in a child
process.

## Consent and texture-pack sources (2026-09-24, second pass)

Closes the two remaining points of the "manage only what you were given"
feedback.

**DLLs are opt-in.** `auto_install_mods` defaults to `False` everywhere. The
first time a client is usable, `_ask_essential_mods` asks once (VanillaFixes
included, no exemption), stores `essential_mods_asked` and the answer; the
Settings toggle also counts as an answer. `config._CARRY_KEYS` no longer
carries Octo Updater's `auto_install_mods`.

**Fixed on the way: discovered DLLs were overwritten.** Discovery recorded
existing mods as unmanaged, enabled, no version; `_apply_mods_worker` read
that as "wanted, not installed" and installed over them on any Apply,
including the first-run one. The worker now refuses to install over files on
disk it does not own (unless `force`, or the record carries a failed-install
error), and `_install_missing_essential_mods` skips them. Fresh installs now
write a full `new_mod_record` with a fingerprint.

**Texture packs can be linked to a source.** `equpdater/mpq.py` is the pure
logic (parse, pick release asset, judge, settle), tested in
`tests/test_mpq.py`. Sources: catalogue entry, GitHub/Codeberg latest release
(optionally a named asset), or a `dl.octowow.st` URL with a `.sha256`
sidecar. Only linked packs are checked. A link whose bytes match the source
is tracked like an install; one that differs is `sourceDiffers` and never
updated; a release with no digest is `unconfirmed`. **Replace…** renames the
old file to `<file>.<stamp>.bak` in Data. Old `mpq` records (managed + sha +
url) read as catalogue installs.

## Historical bugs already fixed

Do not reintroduce these:

- Tk `_poll()` callback surviving destruction and emitting `invalid command name ..._poll`.
- PowerShell installer aborting because unittest writes normal progress to stderr.
- DLL update logic treating `remote != installed` as an update and downgrading local-newer versions.
- Catalog-matched manual addons being automatically taken over.
- different Git SHA being assumed to mean remote-newer.
- recommended repository changes being treated as normal updates.
- old `OctoUpdater.ico` / `EqUpdater.ico` resource naming.
- PLAY gradient leaving a narrow unpainted strip at its right edge.
- OpenDyslexic at 0.82 scale being too small.
- Settings bottom attribution/support strip taking space and becoming unreadably small.
- Essential mods (and VanillaFixes unconditionally) installed without asking.
- Apply installing over a discovered, unmanaged DLL.
- Unlinked texture packs compared with the catalogue by file name.
- OpenDyslexic overflowing Settings and hiding the FONT choice (no way back).
  FONT is now one row under the game folder, and the panel grows to fit.
- `__MACOSX/._*.otf` stubs from the OpenDyslexic zip imported as fonts.
- `install_addon_files` deleting the installed copy before the final rename
  (a failed rename lost it); it is moved aside and restored on failure.
- A failed addon update's error dropped as "stale" by the rescan, so the
  failure never showed.
- The installer reading PowerShell output in the ANSI code page ("'NoneType'
  object has no attribute 'splitlines'") and WScript.Shell mangling
  non-ANSI shortcut paths.
- A bundled (build machine's) fontconfig reading the host's newer
  `/etc/fonts` on Linux.
- Linux creating `Interface/AddOns` beside an existing `Interface/Addons`.
- Every addon install sending the Addons list back to the top.
- Tests replacing `threading.Thread` process-wide (a Windows CI hang), and
  asserting "no overlay open" when a first-run prompt can open its own.


## Backlog / next steps

1. **Close issue #1** (fixed in the re-released 2.1.0).
2. **Issue #3 "Vanilla Autologin Mod"** - not OctoLogin. Ask the reporter
   for the project link; it stores credentials, so decide whether to list
   it at all. Players can already add it themselves as a custom git mod.
3. **Release-notes line** about Microsoft (see State at handoff).
4. **Wait for OctoWoW's reply** on the DDoS exemption; act per "OctoWoW
   DDoS protection".
5. **Defender**: consider building PyInstaller's bootloader from source in
   `release.yml`, or code signing.
6. **Tag rule**: relax it, or release fixes as 2.1.1 instead of
   re-releasing 2.1.0.
7. **crash.log** for start-up errors: an early Linux fix (commit
   `d74ee16`, now only reachable by hash) wrote uncaught start-up errors
   to a log file; it never reached `main`. Worth redoing for 2.1.1 so
   testers have something to send after a crash.
8. Translations were written by the assistant; have native speakers check
   de/es/pt_br/ru/zh_cn.
