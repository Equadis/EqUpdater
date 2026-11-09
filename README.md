<p align="center">
  <img src="icon.png" alt="EqUpdater" width="96">
</p>

<h1 align="center">EqUpdater</h1>

<p align="center">
  A desktop updater and mod manager for <b>OctoWoW</b>, on <b>Windows and Linux</b>.<br>
  Game client, DLL mods, addons, texture packs, client tweaks and server news, in one place.
</p>

<p align="center">
  <img src="docs/screenshot.png" alt="EqUpdater's NEWS tab" width="720">
</p>

---

## Download

- [**Windows**](https://github.com/Equadis/EqUpdater/releases/latest/download/EqUpdater-Windows-Installer.exe): `EqUpdater-Windows-Installer.exe`
- [**Linux x86_64**](https://github.com/Equadis/EqUpdater/releases/latest/download/EqUpdater-Linux-x86_64.AppImage): `EqUpdater-Linux-x86_64.AppImage`
- [**All releases**](https://github.com/Equadis/EqUpdater/releases): release notes and every version

These links always fetch the newest release. No Python or build tools needed.

> **Windows Defender may wrongly flag the unsigned installer as
> `Trojan:Win32/Wacatac.B!ml`.** This is a known false positive for new,
> unsigned apps. To get past it: **Windows Security → Virus & threat
> protection → Protection history**, open the entry, **Actions → Allow on
> device**, then download again. If Windows says "Windows protected your PC",
> choose **More info → Run anyway**.

<p align="center">
  <img src="docs/mods.png" alt="The MODS tab: Managed, Installed locally and Available" width="720">
</p>

## Install

### Windows

Run `EqUpdater-Windows-Installer.exe`. It installs to
`%LOCALAPPDATA%\Programs\EqUpdater`, adds a Start menu entry (and optionally a
desktop shortcut), and launches the app.

- No administrator rights needed. It never touches your game folder.
- Afterwards it asks once whether to add your game folder to Windows Defender's
  exclusions, since some mods are wrongly flagged. Administrator rights are
  requested only if you say yes.
- **To update:** close EqUpdater, then run the newest installer. It replaces the
  app in place and keeps your settings, managed mods and addons, and backups
  (stored separately in `%LOCALAPPDATA%\EqUpdater`). If it fails part-way, your
  current version stays.

### Linux

```sh
chmod +x EqUpdater-Linux-x86_64.AppImage
./EqUpdater-Linux-x86_64.AppImage
```

Runs natively on x86_64 (Ubuntu 22.04+, Mint, Fedora, Arch and others). The
AppImage bundles everything, including `aria2c`. Settings live in
`~/.local/share/EqUpdater`. **To update:** download the newer AppImage and run
that instead.

The OctoWoW client is a Windows program, so **PLAY needs Wine, Proton or UMU.**
EqUpdater uses `wine` if installed, otherwise `umu-run`. To change that, open
**Settings → Game launcher**:

- **Launch command**: how the game starts. `{exe}` is the path of `WoW.exe` (or
  `VanillaFixes.exe`) and is appended if omitted; `{dir}` is the game folder.
  Examples: `wine`, `gamemoderun wine {exe}`, `umu-run`, `/path/to/proton run`.
- **Wine prefix**: passed as `WINEPREFIX`. Empty means the default.
- **Environment variables**: one `NAME=value` per line, such as `DXVK_HUD=fps`.
  Applied to the game only, and they override the settings above.

Nothing runs through a shell, but `~`, `$HOME` and `${HOME}` (and other
variables, including ones set on earlier lines) are expanded, and one pair of
quotes around a whole value is dropped. Paths with spaces need no quotes. Before
launching, PLAY checks that the runner is on `PATH`, the prefix is a full path
and any `PROTONPATH` folder exists. A line it cannot use is flagged immediately
and blocks PLAY until fixed. If the runner exits with an error right away, its
output is saved to `last-launch.log` next to the config and shown in Game
Diagnostics.

The game runs independently: closing EqUpdater never closes it. If a game
started through Bottles or another launcher shows only in Alt+Tab, run the same
command from a terminal. If it behaves the same, the cause is that launcher's or
your window manager's setting, not EqUpdater's.

**Addons folder case:** Linux distinguishes `Interface/AddOns` from
`Interface/Addons`; Windows and Wine do not. EqUpdater finds the folder in
either case and names it `AddOns`. If both exist, files only one holds are moved
into `AddOns`. For an addon in both with different files, you choose which copy
to keep, and the other goes to an `AddOns-conflicts-...` folder, never deleted.

Everything else works as on Windows. Keep the game anywhere, such as
`~/Games/OctoWoW` or inside a Wine prefix.

---

## Safe by design

**EqUpdater only automatically updates what it manages.** It never silently
overwrites:

- addons or mods you installed yourself
- a newer version than the one on offer
- files you have edited
- a different fork or source than the one you chose
- anything it cannot verify

When it holds something back, it says why. When you deliberately replace or
downgrade something, it asks first and keeps a backup.

- **Managed**: installed by EqUpdater, or handed over with **Manage**. Checked
  for updates.
- **Unmanaged**: installed by hand. Shown and left alone until you choose to
  manage it.

Mods, addons and texture packs share the same layout and icons: **Managed**,
**Installed locally** and **Available**. ↗ opens the repository, the grey ×
stops managing (your files stay), the red bin uninstalls, and the tray arrow
downloads.

---

## Features

### Game client
- Downloads, verifies and repairs the OctoWoW client, fixing only the files
  that are wrong.
- Never updates or patches while the game is running.
- **Update everything** with the gold update sign beside PLAY: every managed
  mod and addon with a proven update, nothing else.
- **Choose what PLAY starts** from the arrow beside it: `VanillaFixes.exe`,
  `WoW.exe`, or any `.bat`/`.cmd` in the game folder that starts one of them.
  `VanillaFixes.exe` is the default, because starting `WoW.exe` directly skips
  every mod in `dlls.txt`.
- Optional: keep a custom `speech.mpq` (MPQ tab → **Ignore speech.mpq**).
- New installs suggest `Games\OctoWoW` in your user folder, outside EqUpdater's
  own folder, so an app update can never touch the game.
- Downloads always use verified HTTPS, including under Wine.

### DLL mods
- VanillaFixes, ClassicAPI, Nampower, SuperWoW, UnitXP_SP3, VanillaHelpers,
  DXVK, TransmogFix, AuctionQueryThrottle, OctoLogin and more, each with a
  description and its latest version.
- Recommended mods are opt-in: you are asked once, on first run.
- DLLs already in your client are never replaced just because EqUpdater
  recognises them.
- `dlls.txt` is kept in the right load order, so an enabled mod always loads.
- **OctoLogin** (by Mustafa Yaman, MIT) connects to whichever OctoWoW login
  server answers first and picks the world server with the lowest ping. Its
  original repository is offline, so its last release is hosted unchanged at
  [Equadis/OctoLogin](https://github.com/Equadis/OctoLogin).
- **Mods from another launcher:** a version is recorded only when the files
  match a published release exactly. Otherwise the row reads **version
  unknown** and is never replaced automatically. Click it to replace the files
  with the latest release (after a warning and a backup), or uninstall with the
  bin. **can't check** means the source did not answer; click to retry. A failed
  replacement restores your current files.
- **Custom git mods** (Mods → **+ Add custom git mod**): paste a GitHub,
  Codeberg or OctoWoW Git repository or release link and pick a file from its
  latest release:
  - `.dll`: installed to `mods/` and registered in `dlls.txt`
  - `.zip`: unpacked into the game folder (its top folder dropped); a single mod
    DLL inside is registered in `dlls.txt`, and a mod needs no DLL at all
  - `.mpq`: placed in `Data/`

  Custom mods stay updated from that release and uninstall cleanly, including
  any folders they emptied. A ZIP is inspected first and the warning says what
  goes where. One that would write outside the game folder, contains a program,
  or overwrites another mod's or the game's own `Data` files is refused. Nothing
  installs until you confirm, so only add mods you trust.

### Addons
- ★ **Recommended** addons, a wider list of maintained community versions, and
  search.
- Add any addon from **GitHub, GitLab, Gitea, Codeberg or OctoWoW Git**
  (`https://octowow.st/git/<owner>/<repo>`). OctoWoW Git links without
  `https://`, or on `git.octowow.st/git/...`, are read as the canonical link.
- Tells newer, older and modified apart: a different commit is not assumed to be
  an update.
- Switch an addon to another fork, ignore its updates, or stop managing it.
- A failed install or update says why (for example a DDoS check, timeout or bad
  download), and the installed copy stays untouched until its replacement is
  ready.
- Your place in the list is kept when you install, update or remove.

### Texture packs (MPQ)
- Install packs such as **Octo Raid Visuals** by Rook.
- **Link a source** for a pack you installed yourself so it can be checked for
  updates: a GitHub, Codeberg or OctoWoW Git release, or a dl.octowow.st link.
  Linking changes no files.
- **Replace** keeps your old file beside the new one.

### Tweaks
Patched into `WoW.exe`: Auto Loot, Nameplate Range, Field of View, Far Clip,
Frill Distance, Camera Distance, Sound in Background, **Sound Channels**
(12-64) and the game's language. **Reset** asks before restoring defaults.

**Auto Loot** is your setting, whatever an addon says, and needs no addon. On, a
plain click loots everything and Shift opens the window; off, the reverse.
EqUpdater patches `WoW.exe` so the client checks Shift at the moment you click.
PLAY restores the patch if another launcher changed `WoW.exe`. An addon that
takes items from the loot window itself, such as pfUI's autopickup, is governed
by that addon's own setting.

### Troubleshooting
Under **Settings → Troubleshooting**:

- **Game Diagnostics** records each PLAY across restarts: what was started,
  whether `WoW.exe` appeared, and how it ended, stating only what was observed.
  - **Launch with diagnostics** starts the game once with extra logging.
  - **Scan OctoWoW folder** checks files against the client manifest, mod
    records and file headers (**Deep check** also verifies contents). It changes
    nothing, and separates real problems from possible intentional conflicts
    like a custom MPQ.
  - **Export support ZIP…** bundles a report to send when asking for help. You
    see the file list first and choose where it goes. User names, folder paths,
    account names and passwords are removed; `WTF`, SavedVariables and memory
    dumps are never included. Nothing is sent anywhere.
- **Login Doctor** checks what could stop the game logging in: `WoW.exe` and its
  build (1.18.1, 7272), the realmlist files, login lines in `WTF/Config.wtf`,
  OctoLogin, DNS for play.octowow.st, octowow.st and dl.octowow.st, the login
  port (3724) and the HTTPS servers EqUpdater uses. It never attempts a login or
  reads accounts, launcher sign-ins or tokens.
  - **Repair login configuration** sets the realmlist to `play.octowow.st` and
    removes a stale `SET realmList` from `Config.wtf`. It lists the files first,
    refuses while the game is running, and backs each up as `<file>.octobak`.
  - If nothing local is wrong, the problem is on the server or account side. If
    login hangs at "Authenticating", turn off any VPN or proxy.

### News
- **Announcements** from OctoWoW's
  [Announcements forum](https://octowow.st/forum/viewforum.php?f=2), newest
  first, ten at a time. If the forum is unreadable, the public `news.json`
  feed stands in.
- **Changelog** from the
  [Patch Notes and Changelog forum](https://octowow.st/forum/viewforum.php?f=4),
  with **Show more** for older posts. Long notes open to their first lines with
  **Read more**.
- Post titles stay pinned while you scroll a long post. Links are clickable, and
  **Open in Browser ↗** opens the topic.
- A red **NEW!** marks posts made since you last opened EqUpdater or in the last
  three days, whichever covers more.
- Loaded at launch and on refresh. If the forum cannot be read, News says why
  and keeps the last news received.

### Look and feel
- Animated underwater background (Settings → **Animated background**; off uses
  a still image and almost no CPU).
- Fonts: **Arial** (default), **Friz Quadrata** or **OpenDyslexic**, all
  bundled and switched live.
- Dark title bar on Windows. Resizable, and it remembers its size.
- Languages: English, Deutsch, Русский, 中文 (简体), Español, Português (BR).
  First launch asks; later use **Tweaks → Language**, which sets both the game's
  and EqUpdater's language.
- Bottom right: **Discord** opens the community server, and the cup opens
  **Ko-fi** to support EqUpdater.

<p align="center">
  <img src="docs/addons.png" alt="The ADDONS tab with its action icons" width="720">
</p>

---

## Known issues

- **OctoWoW's DDoS protection can occasionally challenge a request.** EqUpdater
  detects it: News falls back to the public feed or the last news received, and
  a changed OctoWoW Git addon shows **Unable to verify** with **Replace…** until
  the next check gets through. Installing still works.
- **Priority Sign In** from the official OctoLauncher does not work through
  EqUpdater. Login Doctor explains when the official launcher may help.

---

## Migrating

**From Octo Updater:** on first launch, EqUpdater finds your settings, backs
them up, and imports your game folder, locale, tweaks, mods and addons. The old
configuration is copied, never moved, so going back costs nothing.

**From OctoLauncher:** point EqUpdater at the same game folder. Mods and addons
found there appear under **Installed locally**, and one prompt (or **Manage
all**) brings them under EqUpdater. Nothing on disk changes. Cloned addons keep
their source and commit; a mod gets its version only when its files match a
published release exactly, otherwise it reads **version unknown**.

---

## Building from source

> **Testing EqUpdater? Use the CI builds, not a local build.** Get
> `EqUpdater-vX.Y.Z-Windows-Installer.exe` or
> `EqUpdater-vX.Y.Z-Linux-x86_64.AppImage` from a release, or from a pull
> request's **Release** workflow artifacts. A manual `python build.py` produces
> a developer folder (`dist/EqUpdater/`), not the installer, and does not
> install, update or fix shortcuts.

Requires Python 3.10+. One codebase builds both platforms:

```sh
python -m pip install pillow pyinstaller certifi
python -m equpdater              # run from source
python build.py                  # dist/EqUpdater/ (folder build for this OS)
python build.py --setup          # Windows: also the release installer .exe
python3 tools/build_appimage.py  # Linux: the release AppImage
```

On Windows, `install/INSTALL.cmd` does the same from a downloaded source tree
(installing Python if needed). On Linux, running from source needs Tk
(`python3-tk`) and `aria2` from your distribution.

Releases are built by GitHub Actions (`.github/workflows/release.yml`):
publishing a release for tag `vX.Y.Z` builds, tests and attaches both
artifacts from that commit. Platform differences live in
`equpdater/platforms.py`; everything else is shared.

### Tests

```sh
python tools/check.py           # full suite; prints only failures
python build.py --release       # builds only if the suite passes
```

The suite guards EqUpdater's safety rules. It runs in CI on Windows and Linux
for every change and before every release build.

---

## Credits

EqUpdater is a derivative work of **Octo Updater**.

> **Octo Updater** — Copyright (c) 2026 **rebasedkon**
> https://github.com/rebasedkon/octo-updater
>
> Support the author:
> [Ko-fi](https://ko-fi.com/rebased) ·
> [Buy Me a Coffee](https://buymeacoffee.com/rebased)
>
> Contact: inskon@proton.me ·
> [Discord (rebazed)](https://discord.com/users/287467238573867018)

Much of what EqUpdater does, including the client sync, the MPQ engine and the
addon installer, is the original author's work. Full attribution is in
[NOTICE](NOTICE); licence terms are in [LICENSE](LICENSE).

Bundled fonts keep their own licences; see [fonts/README.txt](fonts/README.txt).
Octo Raid Visuals is made by
[Rook](https://octowow.st/git/Rook/RaidVisuals-OctoWoW).
