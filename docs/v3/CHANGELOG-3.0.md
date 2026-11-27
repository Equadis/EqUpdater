# EqUpdater 3.0 beta: what changed from 2.1.4

## New
* **A new interface** (Qt Quick, GPU-drawn) with the same five pages: News, Tweaks, Addons, Mods, MPQ.
* **Six skins**, selected in Settings: Bubbles (default), Classic (2004), Blizzard, Turtle, Minimal (Dark), Minimal (Light) (the last two are full skins in the style of those launchers, drawn from scratch). A skin is a folder of JSON and images; players can make their own (`SKINNING.md`, `COMPONENTS.md`, `starter-skin/`). There is no skin editor or marketplace, deliberately.
* **Animated background** (WebP) that pauses when minimised or, if you choose, unfocused. Idle CPU about 0.3 % against 47 % in 2.x with the animation on.
* **News rebuilt**: cached listing and bodies, no request on scroll, NEW marks, Read more / Load more per panel, works offline from cache.
* **Find your game**: finds the folder 2.x or OctoLauncher uses, or a ZIP/torrent copy, and can extract a ZIP/TAR you downloaded, safely (no path escapes, no links, never overwrites).
* **Server status chip** in the footer (players online, whether the login server answers), from OctoBot's public files; can be turned off.
* **Diagnostics in one place**: recorded launches with a plain-language outcome, a read-only "Verify game files", Login Doctor with a confirmed repair, a redacted support ZIP.
* **Per-item progress** ("3 / 12 pfUI") for batch installs and Update all; an Update-all button with a spinner.
* **Crash reports** and a log folder; one window per data folder.
* **Low-memory rendering** option; **pooled lists** that stay smooth with hundreds of addons.
* **Startup** about 0.8 s packaged (2.x: 3.1 s). Skin switch 90 ms, tab switch 7 ms.

* **Optional account and friends** (see `SOCIAL.md`): an account chip in the header, a collapsible friends sidebar with local friend management, presence states, and a Social section in Settings. **Sign-in and live presence are not operational: OctoWoW offers no service for either yet.** The friends list works locally and says its statuses are unavailable.

## Safer
* WoW.exe, Auto Loot, dlls.txt, Config.wtf, dxvk.conf, MPQ and pfUI files are written atomically (a failed write leaves the old file), and WoW.exe is never truncated before its replacement is ready (2.x could leave a 0-byte WoW.exe).
* One lock per game folder so two operations cannot interleave.
* Skins can never run code, read the game folder or reach the network.

## Removed or different
* **No base-client download or repair** (the aria2/torrent downloader is gone). Bring your own client.
* No in-app update notice yet; install the new installer.
* Linux AppImage not built yet.
* Memory use is higher (graphics device): see `KNOWN_ISSUES.md`.

## Unchanged on purpose
The planner's product rules (what Update all may touch and why), recovery, confirmations, mod/addon/MPQ ownership records, the settings file shared with 2.x, every translation (en, de, es, pt-BR, ru, zh-CN), and the lifted 2.x logic itself (`equpdater/core/*`), which is the same code with the Tk calls taken out.

## Numbers
Automated tests: 1,200 (2.x branch: 881), all passing; see `TESTING.md`. Measurements and method: `PERFORMANCE.md`.

## Beta prep (2026-11-25)
* Installer: uninstall (Apps & features, `--uninstall`), silent mode with exit codes, marker file, deferred self-delete fix.
* Server status: snapshot-first client with static fallback (OctoBot PR 77, not deployed).
* Social: unavailable services can no longer disturb the launcher; every skin lays out the account chip and sidebar (Turtle WoW `navOffset`); opaque sidebar backing in narrow windows.
* Linux: AppImage build script, pruned Qt layouts, CI job (never run). New docs: `LINUX.md`, `OCTOWOW_INTEGRATION_PROPOSAL.md`, `QA_REPORT.md`.

## Beta 1 revision after manual testing (2026-11-27)
* **Friends sidebar simplified**: always visible, no collapse or hide, no realm filters or sort; friends are OctoWoW forum user names; each shows the name, avatar, a presence dot and `Status ・ Realm` (realm only while in game); favourites first, then by presence, offline last; Add / Remove Friend, search, right-click menu, online count. No character, class, level, guild or zone anywhere.
* **Presence menu** on the dot beside your avatar: Online, Away, Do Not Disturb, Invisible. Invisible is sent to the service as `invisible` (a service must show you as offline).
* **Settings > Social removed.** Fixed defaults; 10 obsolete keys are deleted from existing settings on start-up. Refresh is one batched request about every five seconds and is not a setting. Friend login notifications are gone.
* **Language moved** from the Tweaks page to Settings (same stored value, same game patch).
* **Skins**: six (the standalone Minimal is removed; its users get Minimal (Dark)); names Blizzard, Turtle, Minimal (Dark), Minimal (Light); no "· EqUpdater" suffix; fixed order.
* **Classic (2004)**: PLAY uses the same steel button texture as the other Classic buttons.
* **Settings no longer closes** when something is selected (a tap that rebuilt its own button fell through to the scrim). The skin list is only replaced when it really changes.
* **Font selector**: each option drawn in its own face; **Object Sans** added (licence not confirmed, see `fonts/README.txt`).

## Beta 1 revision 2 (2026-11-27)
* **Layout**: the friends list no longer draws over Settings (Settings is above it); one set of spacing constants (`gap`, `margin`): the navigation tabs, the News panels and the pages-to-friends gap are the same distance apart, and Announcements and Friends are the same distance from the window's left and right edges, in every skin and size.
* **Settings**: an **Appearance** group (Skin, Font: compact drop-downs, each font drawn in its own face); the **Language** drop-down shows the country flag (bundled images) for the selected language and each option; **Low-memory rendering removed** (it only changed the launcher's own window).
* **Server status**: the dot is green (every realm online), yellow (partial, degraded, unknown or old data) or red (every realm offline, never from old data); the popover keeps the overall status, players, each realm, website and login response, shows age beside a stale value, and ends with one link, **Open source ↗** (OctoBot's status page).
* **One set of row actions** on Addons, Mods and MPQ: open folder, open repository, uninstall (trash), stop managing (X), always in that order and place, from one shared component.
* **Simplification**: explanatory paragraphs moved into tooltips (Tweaks rows, Linux launcher settings), dim text enlarged by 1 px.
* **Temporary username login and a shared test backend** (development only, see `SOCIAL_TEST_BACKEND.md`): friends are kept on the server, presence is a five-second heartbeat that expires, Invisible reads as offline to others, launcher-open versus in-game and the realm are detected (`core/gamewatch.py`), and a development-only simulation and multi-launcher tool allow testing without a game or several PCs.
