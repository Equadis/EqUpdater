# QA checklist for the beta (what the automated suite cannot prove)

Automated: 1,190+ tests (core logic, every page and overlay in all five skins, installer logic, packaged self-test). This list is what a person must see. Tick on a **clean Windows user profile** with a real OctoWoW client, then once on Linux/Wine when an AppImage exists. Note the build (`3.0.0-beta.1`), Windows version and GPU.

## 0. Install
- [ ] Run the installer exe with 2.x *closed*: it installs, starts, and "EqUpdater 3 Beta" is in the Start menu. 2.x's shortcuts still start 2.x.
- [ ] Run it with 2.x *open*: it refuses with a clear message and changes nothing.
- [ ] Run it again (same version): nothing breaks; the app still starts.
- [ ] Run it while EqUpdater 3 is open: refuses.
- [ ] SmartScreen / antivirus: note what appears.
- [ ] First launch: language question appears once; game found or Find-your-game panel; no empty window.

## 1. PLAY (highest priority)
- [ ] With a complete client: PLAY starts the game; the window minimises if "Minimize on launch" is on; PLAY returns when the game exits.
- [ ] Launch menu: pick VanillaFixes / WoW.exe / a .bat; the choice is remembered and shared with 2.x.
- [ ] "Clear WDB on launch": the WDB folder is emptied only when on.
- [ ] Auto Loot on/off in Tweaks, then PLAY: the behaviour in game matches (shift-loot).
- [ ] A mod in error, a missing `dlls.txt` entry, a damaged loader, the game already running: each gives a clear message and does not start a second game.
- [ ] Linux/Wine: Game launcher settings (command, prefix, environment) are honoured.

## 2. Game folder and first run
- [ ] Find: 2.x folder, OctoLauncher folder, a ZIP copy, an unfinished torrent copy are described correctly and nothing is changed by looking.
- [ ] Extract a real client ZIP to a new folder: progress, cancel (partial folder removed), result usable.
- [ ] Change folder in Settings: the old folder's records are cleared; mods/addons lists follow.
- [ ] The first-run question about mods/addons already there appears once.

## 3. Mods
- [ ] Install, update, disable, uninstall each official mod; `dlls.txt` matches.
- [ ] A hand-installed mod is listed under INSTALLED LOCALLY and adoptable ("Manage all").
- [ ] Add a custom git mod (a .dll, a .zip with files, an .mpq): the warning lists what goes where; remove it.
- [ ] Update all (mods then addons), preview shown only when something is held back; spinner; footer progress "n / total".
- [ ] 2.x and 3 agree about the same folder afterwards (open 2.x, no surprises).

## 4. Addons and MPQ
- [ ] Install/update/remove recommended addons; pfUI default profile applied once; SavedVariables untouched.
- [ ] A locally modified addon is *not* overwritten without asking.
- [ ] Custom git addon by URL; failure messages (bad URL, rate limit) are understandable.
- [ ] MPQ packs: install, update, replace (keeps `.bak`), uninstall, link a custom source.

## 5. Tweaks, Settings, Diagnostics
- [ ] Apply and Reset tweaks; open `Config.wtf` and check; a language change offers the restart.
- [ ] Settings: every toggle persists across restart; Low-memory rendering restarts and applies.
- [ ] Verify game files (read-only: nothing in the game folder changes), Login Doctor (repair asks first, keeps `.octobak`), Game Diagnostics, Export support ZIP (open it: no user name or paths).
- [ ] Open logs folder; force a crash report if possible.

## 6. Look and feel
- [ ] All five skins on every page and every dialog: nothing cut off, unreadable or overlapping; the Classic frame corners do not collide with content.
- [ ] Animated background plays smoothly, pauses when minimised, switches off from Settings; skin switch is instant.
- [ ] Window resize and maximise; minimum size; 125/150/200 % scaling; two monitors with different scaling.
- [ ] Keyboard only: Tab through the header, a page, a dialog; Escape closes overlays; Narrator/NVDA reads the nav and buttons.
- [ ] Server chip: dot, number, popover; "Show server status" off removes it.

## 6b. Account and friends
- [ ] Sign In (no service yet): the explanation appears; nothing pretends to sign in; "Create account" opens octowow.st in the browser.
- [ ] Sidebar: collapse and expand (remembered after a restart); add a friend by name and realm; the same name on another realm is a separate friend; duplicates refused; favourite, search, realm filter, sort; remove.
- [ ] Every friend shows the hollow "?" and the note explaining why; the online count is 0; nothing is requested (watch the network).
- [ ] Settings > Social: every choice survives a restart; turning the sidebar off removes it and the page area widens.
- [ ] Narrow the window below 940 px with the sidebar open: it overlays the page instead of squeezing it.
- [ ] Turtle WoW and Battle.net: every page, dialog, menu and tooltip looks like the skin; switching skins and restarting keep the choice.
- [ ] When OctoWoW provides sign-in and presence (`SOCIAL.md` section 4): repeat with a real account, a rate limit and an outage.

## 7. Endurance
- [ ] Leave it open 4+ hours, switching pages and skins now and then: memory in Task Manager stays near where it started.
- [ ] Sleep/resume the PC, change network, disconnect the network: no error dialogs; news and chip recover.

## 8. Not breaking 2.x
- [ ] After using 3, 2.x starts, shows the same game folder, mods and addons, and works.
- [ ] Uninstalling the beta (delete its folder) leaves 2.x untouched.
