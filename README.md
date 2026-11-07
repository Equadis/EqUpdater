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

- [**Windows**](https://github.com/Equadis/EqUpdater/releases/latest/download/EqUpdater-Windows-Installer.exe) — `EqUpdater-Windows-Installer.exe`
- [**Linux x86_64**](https://github.com/Equadis/EqUpdater/releases/latest/download/EqUpdater-Linux-x86_64.AppImage) — `EqUpdater-Linux-x86_64.AppImage`
- [**Release page**](https://github.com/Equadis/EqUpdater/releases/latest) — release notes and every version

These links always download the newest release. The current one is
**v2.1.4**:
[`EqUpdater-v2.1.4-Windows-Installer.exe`](https://github.com/Equadis/EqUpdater/releases/download/v2.1.4/EqUpdater-v2.1.4-Windows-Installer.exe) ·
[`EqUpdater-v2.1.4-Linux-x86_64.AppImage`](https://github.com/Equadis/EqUpdater/releases/download/v2.1.4/EqUpdater-v2.1.4-Linux-x86_64.AppImage).
The release page has every version.

Nothing else is needed: no Python, no build tools.

> **Windows Defender may wrongly flag the unsigned installer as
> `Trojan:Win32/Wacatac.B!ml`.** That is a known false positive for new,
> unsigned apps. If the download is blocked: **Windows Security → Virus &
> threat protection → Protection history**, open the entry, **Actions →
> Allow on device**, and download it again. If Windows says "Windows
> protected your PC", choose **More info → Run anyway**.

## What's new in 2.1.4

<p align="center">
  <img src="docs/mods.png" alt="The MODS tab: Managed, Installed locally and Available" width="720">
</p>

- **Mods and Addons, sorted the same way:** **Managed**, **Installed
  locally** and **Available**, each with the same icons: ↗ opens the
  repository, the grey × stops managing (your files stay), the red bin
  uninstalls, and the arrow into a tray downloads.
- **Moving from OctoLauncher:** mods and addons another launcher installed
  are found, and one question (or **Manage all**) brings them under
  EqUpdater. A mod's version, or an addon's commit, is recorded only when its
  files match the published release exactly.
- **Update everything** with the gold arrow beside PLAY. It is there only
  when something can be updated safely.
- **Choose what PLAY starts**, **Game Diagnostics**, an **Auto Loot** setting
  that addons cannot undo, a longer changelog under **Show more**, and News
  headers that stay in view while you read.

<p align="center">
  <img src="docs/addons.png" alt="The ADDONS tab with its action icons" width="720">
</p>

## Install

### Windows

1. Download **`EqUpdater-Windows-Installer.exe`** (link above).
2. Run it. It installs EqUpdater to
   `%LOCALAPPDATA%\Programs\EqUpdater`, adds it to the Start menu (and,
   if you like, the desktop), and starts it.

No administrator rights are needed, it never touches your game folder, and
it works with non-English user names and folder names. After installing or
updating, it asks once whether to add your game folder to Windows Defender's
exclusions (some mods are wrongly flagged); that step asks for administrator
rights only if you say yes.

**Updating:** download `EqUpdater-Windows-Installer.exe` again and run it
(close EqUpdater first). It replaces the app in that same place and points
every EqUpdater shortcut there. Your settings, managed mods and addons, and
backups live separately in `%LOCALAPPDATA%\EqUpdater` and are kept. If
anything goes wrong part-way, the version you had stays in place.

### Linux

1. Download **`EqUpdater-Linux-x86_64.AppImage`** (link above).
2. Make it executable and run it:

   ```sh
   chmod +x EqUpdater-Linux-x86_64.AppImage
   ./EqUpdater-Linux-x86_64.AppImage
   ```

EqUpdater runs natively on Linux (x86_64; Ubuntu 22.04 and newer, Mint,
Fedora, Arch and others). The AppImage carries everything it needs,
including the `aria2c` the client download uses. Settings live in
`~/.local/share/EqUpdater`. **Updating:** download the newer AppImage and
run that one instead.

The OctoWoW client itself is a Windows program, so **PLAY needs a way to
run Windows games: Wine, Proton or UMU.** By default EqUpdater uses `wine`
if it is installed, otherwise `umu-run`. To use anything else - a
particular Proton, Lutris, Faugus, a custom Wine prefix - open
**Settings → Game launcher**:

- **Launch command** - the command that starts the game. `{exe}` stands
  for the path of `WoW.exe` (or `VanillaFixes.exe`); without `{exe}` the
  path is added at the end. `{dir}` is the game folder. Examples:
  `wine`, `gamemoderun wine {exe}`, `umu-run`,
  `/path/to/proton run`.
- **Wine prefix** - passed as `WINEPREFIX`; leave it empty for the default.
- **Environment variables** - one `NAME=value` per line, for example
  `DXVK_HUD=fps` or `PROTON_LOG=1`. They are given to the game only, when
  PLAY starts it, and win over anything set above. Spaces and `=` are part
  of the value. A line EqUpdater cannot use is pointed out straight away,
  and PLAY will not start the game until it is fixed.

Nothing is run through a shell, so EqUpdater does what a shell would with
the Wine prefix, the variables and the launch command: `~`, `$HOME` and
`${HOME}` (or any other variable, including one from a line above) become
their values, and one pair of quotes around a whole value is dropped -
`"$HOME/Games/pfx"` is `/home/you/Games/pfx`. A path with spaces needs no
quotes. Before starting, PLAY checks that the runner is on `PATH`, that the
prefix is a full path and that a `PROTONPATH` folder exists; a runner that
quits with an error right away is reported, its output kept in
`last-launch.log` next to the config, and Game Diagnostics has it for each launch.

The game runs on its own: closing EqUpdater never closes it. It gets your
desktop session as EqUpdater got it, without EqUpdater's own launch details
(the desktop's startup ID and the AppImage's variables), so its window is
its own in the taskbar. If a game started through Bottles or another
launcher still shows only in Alt+Tab, start the same command from a
terminal: if it behaves the same there, it is that launcher's or the
window manager's setting (for example a Wine virtual desktop), not
EqUpdater's.

Linux tells `Interface/AddOns` and `Interface/Addons` apart; Windows and
Wine do not. EqUpdater finds the addons folder in whatever case it has and
names it `Interface/AddOns`. If a game folder has two of them, everything
only one holds is moved into `AddOns`, and for an addon both hold with
different files you choose which copy to keep -- the other is moved into an
`AddOns-conflicts-...` folder, never deleted. Settings copied from a Wine
install keep their addon and mod records.

Everything else - updates, mods, addons, texture packs, tweaks, the
`WoW.exe` patch - works the same as on Windows. Keep the game anywhere,
for example `~/Games/OctoWoW` or inside a Wine prefix.

---

## Safe by design

**EqUpdater only automatically updates what it manages.** It will never
silently overwrite:

- addons or mods you installed yourself
- a newer version than the one being offered
- files you have edited
- a different fork or source than the one you chose
- anything it cannot verify

When it holds something back, it says why. When you deliberately replace or
downgrade something, it asks first and keeps a backup.

### Managed and unmanaged

- **Managed** — installed by EqUpdater, or handed over with **Manage**.
  Checked for updates.
- **Unmanaged** — installed by hand. Shown and left exactly as it is, until
  you choose to manage it.

An update is offered only for what is managed and confirmed newer; the rest
is left as it is.

---

## Features

### Game client
- Downloads, verifies and repairs the OctoWoW client, repairing only the
  files that are actually wrong.
- Will not update or patch while the game is running.
- Optional: keep a custom `speech.mpq` (MPQ tab → **Ignore speech.mpq**).
- A new install suggests `Games\OctoWoW` in your user folder, outside
  EqUpdater's own folder, so an EqUpdater update can never touch the game.
- Downloads always use verified HTTPS, under Wine too; certificate checks
  are never switched off.
- **Choose what PLAY starts**: the arrow at the right end of PLAY lists
  `VanillaFixes.exe`, `WoW.exe` and any `.bat` or `.cmd` script in the game
  folder that starts one of them. Check one to make it the default. Starting
  `WoW.exe` directly leaves out every mod in `dlls.txt`, so `VanillaFixes.exe`
  stays the default until you choose otherwise.

### DLL mods
- VanillaFixes, ClassicAPI, Nampower, SuperWoW, UnitXP_SP3, VanillaHelpers,
  DXVK, TransmogFix, AuctionQueryThrottle, OctoLogin and more, each with its
  description and latest version.
- **OctoLogin** by Mustafa Yaman (fmustafayaman, MIT) connects the game to
  whichever OctoWoW login server answers first and picks the world server
  with the lowest ping. Its own repository is no longer online, so its last
  release is hosted unchanged at
  [Equadis/OctoLogin](https://github.com/Equadis/OctoLogin).
- **Recommended mods are opt-in** — you are asked once, on first run.
- DLLs already in your client are never replaced just because EqUpdater
  recognises them.
- `dlls.txt` is kept in step and in the right load order, so an enabled mod
  always actually loads.
- Sorted into **Managed**, **Installed locally** and **Available**. The
  download icon installs a mod; the red bin uninstalls it (no internet
  needed); the grey × stops managing it and leaves its files where they are.
- **Mods adopted from another launcher, OctoLauncher included**: a mod's
  version is recorded only when its files match a published release exactly.
  Otherwise the row reads **version unknown**, and it is never replaced
  automatically. Click it to replace the files with the latest release, after
  a warning and a backup, or uninstall it with the bin. **can't check** means
  the mod's source did not answer; click it to try again. If a replacement
  fails, your current files are put back.
- **Custom git mods** (Mods → **+ Add custom git mod**): paste a GitHub,
  Codeberg or OctoWoW Git repository or release link and pick a file from
  its latest release:
  - a `.dll` is installed to `mods/` and registered in `dlls.txt`;
  - a `.zip` is unpacked into the game folder (its own top folder dropped),
    and a single mod DLL in it is registered in `dlls.txt` -- a mod needs no
    DLL at all;
  - an `.mpq` goes to `Data/`.

  It is kept updated from that release (a new version's file name is found
  too) and removed cleanly with the bin, the folders it emptied included. A ZIP is
  looked through first and the warning says what goes where; one that would
  write outside the game folder, carries a program, or overwrites another
  mod's files or the game's own `Data` files is refused. A mod can change
  how the game behaves, so nothing is installed until you confirm — only add
  mods you trust.

### Addons
- ★ **Recommended** addons, a wider list of maintained community versions,
  and search.
- Add any addon from **GitHub, GitLab, Gitea, Codeberg and OctoWoW Git**
  (`https://octowow.st/git/<owner>/<repo>`). OctoWoW Git links written
  without `https://`, or on its other address `git.octowow.st/git/...`, are
  read as that same canonical link; no other address is rewritten.
- Knows the difference between newer, older and modified — a different
  commit is not assumed to be an update.
- Switch an addon to another fork, ignore updates for one, or stop managing
  it. An addon cloned by OctoLauncher keeps its own source and commit when
  you manage it.
- **Update everything at once** with the gold arrow beside PLAY: every
  managed addon and mod with a proven update, and nothing else.
- A failed install or update says why (for example the host's DDoS check,
  a timeout or a bad download), and the installed copy stays untouched until
  its replacement is in place.
- Installing, updating or removing an addon keeps your place in the list.

### Texture packs (MPQ)
- Install packs such as **Octo Raid Visuals** by Rook.
- **Link a source** for a pack you installed yourself, so it can be checked
  for updates: a GitHub, Codeberg or OctoWoW Git release, or a
  dl.octowow.st link. Linking changes no files.
- **Replace** keeps your old file beside the new one.
- Stop managing a pack (grey ×) or uninstall it (red bin), as on the other
  tabs.

### Tweaks
- Patched into `WoW.exe`: Auto Loot, Nameplate Range, Field of View, Far
  Clip, Frill Distance, Camera Distance, Sound in Background, **Sound
  Channels** (12–64) and the game's language.
- **Reset** asks before putting everything back to defaults.

**Auto Loot** is yours to decide, whatever an addon says. EqUpdater flips one
branch in `WoW.exe`, so a plain click loots everything and Shift opens the
window (or the other way round). SuperWoW's `SetAutoloot` feeds the same flag,
and addons with their own auto-loot take items from the window, so a small
addon, `!EqLootGate`, loads first and stops both, without changing any addon's
settings. PLAY puts `WoW.exe` back if another launcher changed it, and a client
EqUpdater has not patched yet keeps what it had until you choose. `/eqloot` in
game counts what it stopped. It cannot stop a DLL that writes to the game's
memory, an addon that clicks the loot buttons itself, or anything while
`!EqLootGate` is disabled in the AddOns list.

### Game Diagnostics
- **Settings → Troubleshooting → Game Diagnostics** keeps a record of each
  PLAY across restarts: what was started, whether `WoW.exe` then appeared, and
  how it ended, stating what was seen and no more. A launcher exiting cleanly
  is not reported as the game starting.
- **Launch with diagnostics** starts the game once with extra logging for that
  launch only. Steam starts the game itself, so there the view tells you how to
  get a Proton log.
- **Scan OctoWoW folder** lists every file and checks them against the client's
  manifest, mod records and file headers; **Deep check** also verifies contents.
  It changes nothing. Problems are kept apart from possible conflicts: a custom
  MPQ or an extra addon may well be on purpose.
- **Export support ZIP…** writes one file to send when asking for help: you see
  the list first, and choose where it goes. User names, folder paths, account
  names and passwords are removed; `WTF`, SavedVariables and memory dumps are
  never included. Nothing is sent anywhere by EqUpdater.

### Login Doctor
- **Settings → Troubleshooting → Login Doctor** checks what on your computer
  could stop the game logging in: `WoW.exe` and its build (1.18.1, 7272),
  the realmlist files (`realmlist.wtf`, and `Data/<locale>/realmlist.wtf`
  where there is one), the login lines in `WTF/Config.wtf`, OctoLogin, name
  lookups for play.octowow.st, octowow.st and dl.octowow.st, the login
  server's port (3724) and the HTTPS servers EqUpdater uses. It never tries
  to log in and never reads accounts, launcher sign-ins or tokens.
- **Repair login configuration** sets the realmlist to `play.octowow.st` and
  removes a stale `SET realmList` from `Config.wtf`. It lists the files
  first, refuses while the game is running, changes only those lines, and
  keeps a copy of each file as `<file>.octobak` (an existing copy is never
  replaced).
- If nothing local is wrong it says so: the problem is then on the server or
  the account. If login stops at "Authenticating", turn off any VPN or
  proxy; if normal login still fails, try launching the game through the
  official OctoLauncher using Priority Sign In. EqUpdater cannot use Priority
  Sign In itself.

### News
- Announcements: every topic in OctoWoW's
  [Announcements forum](https://octowow.st/forum/viewforum.php?f=2), newest
  first, ten at a time with **Load more**. The newest is open; click any
  title to open or close its post. While the forum is unreadable, OctoWoW's
  public news feed (`news.json`) stands in.
- Changelog: the newest topics in the
  [Patch Notes and Changelog forum](https://octowow.st/forum/viewforum.php?f=4),
  each opening to its patch notes; the newest is open. Long notes open to
  their first lines, with **Show more** and **Show less**.
- While you read a long post, its title stays at the top of the panel; click
  it to close the post.
- Links in posts can be clicked, and **Open in Browser ↗** opens the topic
  on the forum.
- A red **NEW!** marks a post made since you last opened EqUpdater, or in
  the last three days -- whichever covers more.
- Both are read once at launch, and again when you press refresh.
- If the forum cannot be read, the News tab says why and keeps the last news
  it received.

### Look and feel
- Bottom right, above the version: **Discord** opens the community's
  Discord, and the cup opens **Ko-fi** to support EqUpdater.
- Animated underwater background (Settings → **Animated background**; off
  uses a still image and almost no CPU).
- Fonts: **Arial** (default), **Friz Quadrata** or **OpenDyslexic**, switched
  live in Settings. All three come with EqUpdater; nothing to install.
- Dark title bar to match (Windows).
- Resizable, and it remembers its size. Bigger shows more news; at its
  smallest it is just the tabs and the PLAY/UPDATE buttons.
- In your language: English, Deutsch, Русский, 中文 (简体), Español or
  Português (BR). The first launch asks; after that it is **Tweaks →
  Language**, which sets both the game's language and EqUpdater's.
  Changing it offers a restart.

---

## Known issues

- **OctoWoW's DDoS protection can still occasionally challenge a
  request.** EqUpdater recognises the challenge page: News then shows the
  public news feed or the last news received, and a changed OctoWoW Git
  addon shows **Unable to verify** with **Replace…** until the next check
  gets through (installing still works).
- **Priority Sign In** from the official OctoLauncher cannot be used through
  EqUpdater. Login Doctor explains when it may help.

---

## Coming from Octo Updater

On first launch, EqUpdater finds your Octo Updater settings, backs them up and
imports your game folder, locale, tweaks, mods and addons. Your old
configuration is **copied, never moved**, so going back costs nothing.

## Coming from OctoLauncher

Point EqUpdater at the same game folder. It lists the mods and addons it
finds there under **Installed locally** and asks once whether to manage them
all; nothing on disk changes when you say yes. Addons OctoLauncher cloned
keep their source and commit, and a mod gets its version when its files
match a published release exactly. Anything that cannot be identified reads
**version unknown** and can be replaced or uninstalled from its row.

---

## Building from source (developers)

> **Testing EqUpdater? Do not build it yourself.** Use the files GitHub
> Actions builds: `EqUpdater-vX.Y.Z-Windows-Installer.exe` and
> `EqUpdater-vX.Y.Z-Linux-x86_64.AppImage` - from a release, or for a pull
> request from its **Release** workflow run's artifacts. Those are what
> users get. A manual `python build.py` makes a developer folder
> (`dist/EqUpdater/`: `EqUpdater.exe` beside an `_internal` folder), which
> is not the installer and does not install, update or fix shortcuts.

One codebase builds both platforms. With Python 3.10 or newer:

```sh
python -m pip install pillow pyinstaller certifi
python -m equpdater              # run it from source
python build.py                  # dist/EqUpdater/ - the folder build for this OS
python build.py --setup          # Windows: also the release installer .exe
python3 tools/build_appimage.py  # Linux: the release AppImage
```

On Windows, `install/INSTALL.cmd` does the same from a downloaded source
tree (installs Python if needed, builds, installs to
`%LOCALAPPDATA%\Programs\EqUpdater`). Running from source on Linux needs
Tk (`python3-tk`) and `aria2` from your distribution.

Releases are built by GitHub Actions (`.github/workflows/release.yml`):
publishing a release for tag `vX.Y.Z` builds, tests and attaches
`EqUpdater-vX.Y.Z-Windows-Installer.exe` and
`EqUpdater-vX.Y.Z-Linux-x86_64.AppImage`, both from that commit.
Platform differences live in `equpdater/platforms.py`; everything else is
shared.

### Tests

```sh
python tools/check.py           # the whole suite; prints only what failed
python build.py --release       # builds only if the suite passes
```

The suite guards EqUpdater's safety rules. It runs in CI on Windows and
Linux for every change and before every release build; it is not part of
installing.

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

Much of what EqUpdater does — the client sync, the MPQ engine and the addon
installer — is the original author's work. Full attribution is in
[NOTICE](NOTICE); licence terms are in [LICENSE](LICENSE).

The bundled fonts keep their own licences; see
[fonts/README.txt](fonts/README.txt).

Octo Raid Visuals is made by
[Rook](https://octowow.st/git/Rook/RaidVisuals-OctoWoW).
