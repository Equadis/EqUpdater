# EqUpdater 3 Beta: guide for testers

EqUpdater 3 is a rebuilt EqUpdater: a faster, skinnable window with the same jobs (mods, addons, MPQ patches, tweaks, launching, diagnostics). **It is a beta. It has been tested by an automated suite and by its developer on one Windows machine; it has not yet been tried by anyone else, on Linux, or against a long-running real game session.** Please read "Before you start".

## Before you start
* **It installs beside EqUpdater 2, not over it.** 2.x keeps working and its shortcuts are not touched. Going back is just using 2.x again.
* **Do not run both at once.** Both edit the same game folder, mods list and settings. The installer refuses while 2.x is running, and 3 refuses a second copy of itself, but nothing stops you opening 2.x while 3 is open. Close one first.
* **It never downloads the game.** Get the OctoWoW client (ZIP or torrent) yourself. On first run EqUpdater looks for a game already on your computer, or extracts an archive you downloaded. It does not resume or repair an unfinished download.
* **Your settings carry over, and are shared.** The game folder, mods, addons, tweaks and MPQ records are the same ones 2.x uses (`%LOCALAPPDATA%\EqUpdater\config.json`). Changing them in 3 changes them in 2.x too. 3's own files (skin choice, news cache, server cache, crash reports) are in `%LOCALAPPDATA%\EqUpdater\v3\`.
* **Back up what matters.** Copy `WTF`, `Interface\AddOns` and `Data\patch-*.mpq` somewhere before your first Apply/Update. EqUpdater keeps copies of files it replaces and never deletes your SavedVariables, but a beta is a beta.

## Install
1. Run `EqUpdater3-v3.0.0-beta.1-Windows-Installer.exe`. Windows SmartScreen may warn that it is unrecognised: it is not code-signed yet.
2. It installs to `%LOCALAPPDATA%\Programs\EqUpdater3`, adds "EqUpdater 3 Beta" to the Start menu, offers a desktop shortcut and starts.
3. First run: choose a language, then pick your game folder (or extract your archive).

To update to a later beta, run its installer (close EqUpdater first). To remove it, delete `%LOCALAPPDATA%\Programs\EqUpdater3` and the shortcuts; your settings stay.

## What to try (and tell us)
The things automated tests cannot prove are in `QA_CHECKLIST.md`. The most valuable, in order:
1. **PLAY**: does the game start the way it does from 2.x, with your mods? Does it refuse sensibly when something is wrong?
2. **Mods and Addons**: install, update, remove, "Update all", a custom git mod/addon, anything 2.x did that you rely on.
3. **Tweaks**: Apply and check the game's `Config.wtf` and behaviour; Auto Loot.
4. **Skins**: try all five (Settings). Anything unreadable, cut off or ugly.
5. **Settings > Troubleshooting**: Verify game files (read-only), Login Doctor, Game Diagnostics.
6. **Leave it open for a few hours**, switch pages and skins, and tell us if the memory (Task Manager) keeps climbing.

## Reporting a problem
* **Settings > Troubleshooting > Game Diagnostics > Export support ZIP**, or **Open logs folder** for crash reports (`crash-*.txt`).
* Both remove your user name, folder paths and passwords, and **nothing is sent anywhere**: you choose who receives the file. Look inside before sending if you like.
* Say what you did, what you expected, and what happened. A screenshot helps.

## What it collects
Nothing about you. It makes only these network requests: OctoWoW's forum (news), GitHub/Codeberg/OctoWoW Git (mods, addons, releases), OctoWoW's download host (MPQ packs), and OctoBot's public status files on GitHub Pages (the footer chip; **Settings > "Show server status in the footer"** turns it off, and then no such requests are made). No analytics, no crash upload.

## Where things are
| | |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\EqUpdater3` |
| Shared settings and records with 2.x | `%LOCALAPPDATA%\EqUpdater\config.json` |
| 3's own data | `%LOCALAPPDATA%\EqUpdater\v3\` (`settings.json`, `cache\`, `skins\`, `logs\`) |
| Your own skins | `%LOCALAPPDATA%\EqUpdater\v3\skins\` (Settings > Skin > Open skins folder; see `SKINNING.md`) |

Known problems are in `KNOWN_ISSUES.md`; what changed from 2.x is in `CHANGELOG-3.0.md`.
