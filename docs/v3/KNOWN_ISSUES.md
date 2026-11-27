# Known issues and limits (3.0.0-beta.1)

Honest list, most serious first. "Not verified" means the code exists and its logic is tested with fakes, but it has not been seen working for real.

## Not verified on a real system (needs a person)
1. **PLAY against a real game.** The whole launch pipeline (dlls.txt check, Auto Loot, preflight, spawn, minimise, watch, failure messages) is tested with the process start replaced. It has not been run against a real `WoW.exe` or `VanillaFixes.exe` by the developer in this build. `QA_CHECKLIST.md` item 1.
2. **Real installs of mods and addons over the network.** Install/update/uninstall/stash/rollback logic is the 2.x code and is covered by tests with fake releases; a live end-to-end install in 3.0 has not been done.
3. **The installer on a clean machine.** It was run into a temporary folder through its own code and the installed copy passed its self-test; the real installer window, Start-menu entry, desktop shortcut and SmartScreen behaviour have not been seen.
4. **Windows Defender exclusion** (the UAC prompt) and **Linux "Game launcher"** settings: logic tested with the system call replaced.
5. **Linux and Wine, macOS:** 3.0 has no AppImage and was not run on Linux at all. The Linux code paths are the 2.x ones; the Qt front end is expected to work but is untested there.
6. **HiDPI and screen readers.** Qt scales automatically and controls carry accessibility roles and names, but nothing has been checked at 150-200 % scaling or with Narrator/NVDA.
7. **Long runs.** A 5-minute scripted soak is flat; hours have not been run.

## Account and friends (beta 1 addition; `SOCIAL.md`)
* **Sign-in does not work, because there is nothing to sign in to.** OctoWoW has published no OAuth/OIDC service or API; the Sign In button says so. EqUpdater will not ask for your forum password or scrape the site. The sign-in code (OAuth with PKCE) is tested against a fake server only.
* **Friend statuses are unavailable**, because OctoWoW offers no character-presence service. The list is local; every friend shows a hollow "?" and the sidebar says why. Nothing is shared about you; the Social privacy settings are saved for when a service exists.
* Adding a friend by **forum user name** needs a service that resolves accounts to characters; the option is explained but unavailable.

## Behaviour you will notice
* **Memory is higher than 2.x**: about 186-217 MB idle against 98 MB, mostly the graphics driver. *Settings > Low-memory rendering* (restart) saves 30-45 MB at a little more CPU. Packaged start-up is about 0.8 s.
* **The server chip is often dim and "out of date".** It reads OctoBot's public files on GitHub Pages, which are refreshed far less often than promised (hours, measured). It never shows the server as offline because *EqUpdater* could not fetch an update; only OctoBot's own recent probe can colour the dot. A fresh, once-a-minute feed needs a change on OctoBot's side that has been proposed, not made (`OCTOBOT.md`).
* **No update notice.** 2.x showed "Update available" for itself; 3 does not. Update by running the new installer.
* **The footer count and status are not EqUpdater's measurement**; they are OctoBot's.
* **Not code-signed.** Windows SmartScreen and some antivirus programs distrust new unsigned programs. (A false positive is reported to the antivirus vendor by the project, not by you.)
* **Forum requests still identify as `EqUpdater/2.1.4`.** OctoWoW's protection was tuned to that pattern; the beta keeps it until they say a new version string is fine.
* **First launch after installing takes a few seconds longer**: the installer warms Qt's QML cache once.
* **A Qt bug** leaks memory when an animated WebP is reloaded; EqUpdater avoids reloading (a skin's animation stays loaded once visited, about 5 MB per animated skin).

## Missing or different from 2.x, on purpose
* **No base-client download or repair** (aria2/torrent removed): extract or point at a client you have. Verify game files is read-only.
* **No skin editor, marketplace or in-app skin import**: skins are folders you edit yourself.
* **Video skin backgrounds** (MP4/WebM) are not in the package (QtMultimedia left out to save 25 MB); no official skin uses one.
* **7z and RAR archives** cannot be extracted by "Find your game"; use your archive program, then choose the folder.
* No "pending client reconcile", "mirror status" or aria2 firewall notice (all belonged to the downloader).

## Missing from 2.x and not planned for the beta
* A native dark title bar (3 uses the system frame).
* Importing settings of the older *Octo Updater* directly into 3 (2.x did this on its first run; run 2.x once first, or enter the game folder again).
* Per-item progress for MPQ downloads and first-run installs (a single item shows the busy bar).

## Open decisions
* **Classic (2004) artwork**: the frame, button frame and login scene were supplied by the project owner and look game-derived. Redistribution rights must be confirmed before a public build (`SKIN_ASSETS.md`). Until then do not share the beta publicly.
* **OctoBot static snapshot** (option C in `OCTOBOT.md`): needs the OctoBot owner's approval.
* Whether the beta may use a new User-Agent string with OctoWoW's protection.

## Reporting
See `BETA.md`. Please include the support ZIP (Settings > Troubleshooting) and your skin.

## Added in beta prep (see `QA_REPORT.md` for what was and was not verified)
* The OctoBot snapshot endpoint is not deployed; the footer chip uses the static feed, which is often hours old, and says so.
* The Linux AppImage has never been built or run. Starting the game through Wine, UMU or Proton is untested.
* The installer is unsigned (SmartScreen may warn).
* **Object Sans** was supplied without a licence file; it is in the local beta build only. Confirm its redistribution terms before any public installer (`tools/beta_check.py` warns).
* At the smallest window (860 px) the friends list is 210 px wide and long status lines are cut with an ellipsis.
* Friends are added by forum user name only and are not confirmed with the other person: there is no service to ask.
