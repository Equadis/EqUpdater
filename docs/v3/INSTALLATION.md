# Installing and migrating to EqUpdater 3.0 (beta)

**EqUpdater never downloads the OctoWoW game client.** Get the client from
OctoWoW (ZIP or torrent), then point EqUpdater at it. EqUpdater manages what is
*on top of* the client: mods, addons, MPQ packs, tweaks, launching and diagnostics.

## First run: "Find your game"

On the first run with no game folder (and from Settings → Game folder → **Find…**)
EqUpdater lists the game folders it can see, without changing anything:

| Source | Where it looks |
|---|---|
| EqUpdater 2.x | `out_dir` in its `config.json` (read, never written) |
| OctoLauncher | `clientDir` / `activeClientDir` in its `settings.json` only. Its sign-in file (`auth.json`) is never opened |
| Usual places | `~/Games/OctoWoW`, `C:\Games\OctoWoW`, `D:\OctoWoW`, Program Files, and similar |

Each folder is described by what is in it:

* **Complete**: a real `WoW.exe` (valid program, over 1 MB) and the game's `Data` archives.
* **Unfinished download**: the same, plus leftovers of a torrent (`.aria2`, `.part`, `.!ut`, `.bc!`, `.qbt`, `.td`). It can be used, but finish the download in your torrent program first.
* **No game**: not offered as a choice.

**Use this folder** makes it the game folder (the same step as changing it in Settings: records for the previous folder are cleared, as in 2.x). It is refused while the game is running from that folder.

## An archive you already downloaded

*Choose a ZIP or TAR…* reads the archive's index first (nothing is written), then *Extract to…* asks for a destination.

* Supported: `.zip`, `.tar`, `.tar.gz`/`.tgz`, `.tar.bz2`, `.tar.xz`. 7z and RAR are refused with advice to extract them with your own archive program and choose the folder.
* Refused before anything is written: paths that would leave the folder (`..`, absolute, drive letters), links, anything that is not a file or folder, an archive with no `WoW.exe`, a `WoW.exe` under 1 MB, absurd expansion (over 200x and over 1 GB), over 200,000 entries, too little free space (size plus 64 MB).
* A single wrapper folder (`OctoWoW/WoW.exe`) is dropped.
* Files are written to `<destination>.extracting` and moved into place only when complete and holding a valid `WoW.exe`. Cancel or failure removes the partial folder. **A destination that already has files is never touched**: choose an empty or new folder.
* Then it becomes the game folder.

## Moving from EqUpdater 2.x

3.0 reads 2.x's `config.json` and never writes its copy of the shared data back unless a shared record changes (mods, addons, tweaks, MPQ records, game folder, launch default). See `MIGRATION.md` for the table. Nothing in 2.x is removed or repointed:

* 3.0 ships as a separate beta build and installer; the 2.x shortcuts keep pointing at 2.x.
* Both can be installed. Do not run both at once on the same game folder: the per-folder operation lock and the game-running checks protect file writes, but the two windows would each show their own state.
* The 3.0 installer must refuse while 2.x is running (`core/install.installed_2x_running`, the same "is the program writable" check 2.x's own installer uses).

## From OctoLauncher

Nothing is copied from it except the game folder. Mods it installed are recognised the same way 2.x recognises them (a hint, then an exact file match against the release; `octolauncher.py`). OctoLauncher keeps working; EqUpdater does not edit its files.

## From a ZIP or torrent copy

Point at the folder (Find…, or Choose a folder…). If the torrent was never finished the folder is labelled unfinished. EqUpdater does not resume or repair the download; Verify game files (Settings → Troubleshooting) reads the folder against the client manifest and reports, read-only.

## The 3.0 beta package (Windows)

`python tools/build_v3.py --setup` builds, from the source tree:

| Output | What |
|---|---|
| `dist/EqUpdater3/EqUpdater3.exe` (+ `_internal`) | the folder build, about 111 MB on disk (PySide6/Qt is 76 MB of it) |
| `dist/EqUpdater3-v<version>-Windows-Installer.exe` | the one-file beta installer, about 68 MB (the Classic artwork added 8) |

* It is a **separate product beside 2.x**: installs to `%LOCALAPPDATA%\Programs\EqUpdater3`, runs `EqUpdater3.exe`, adds its own Start-menu entry "EqUpdater 3 Beta" and offers a desktop one. **No 2.x shortcut, folder or setting is moved or written** (a test checks a 2.x `.lnk` is untouched).
* **Refuses while EqUpdater 2 is running** (or the beta is), says why, changes nothing.
* **Staged**: the new build is copied beside the old, which moves aside only once the copy is complete and comes back if the swap fails. A run that stopped half way is finished by the next. Entries in the package that would leave the folder are refused.
* After installing it opens every page once, headless, so Qt compiles the interface's QML up front: the player's first launch is about a second quicker (2.0 s cold, 1.0 s warm to the first frame here).
* What is left out of the package: Tk and Pillow (3.0 does not use them), every Qt area the interface never imports (multimedia, web, 3D, charts, other QML styles, translations, the software OpenGL fallback), 102 MB in all. `tests/test_v3_installer.py` fails if the QML or the Python ever imports something that was left out. Skins that animate with a **video** file would need QtMultimedia added back; no official skin does.
* **Self-test**: `EqUpdater3.exe --self-test=<file> [--gui]` checks the package carries its skins, fonts, QML, icon and TLS bundle; with `--gui` it loads the real interface offscreen, opens every page and overlay in every skin and fails on any QML error. `build_v3.py` runs it on every build.
* Data stays in `%LOCALAPPDATA%\EqUpdater` (2.x's `config.json`, read; 3.0's own `v3/`).
* Not signed: Windows SmartScreen and some antivirus programs distrust new unsigned programs, as with 2.x.

## Not built yet

* Linux AppImage for 3.0 (`tools/build_v3.py` builds the folder on Linux, but the AppImage wrapper and a Linux test run are still to do).
* Detecting 2.x's installer shortcuts and offering to keep or retire them (the beta never changes them).
* Precompiled QML inside the package (Qt's `qmlcachegen` is for resource-based builds; the install-time warm-up gets most of the benefit).
