# EqUpdater 3.0 beta 1: QA report

Date: 2026-11-25. Machine: Windows 10 Home, one PC, the developer's own. Branch `feat/equpdater-3.0`.

**Bottom line:** the automated suite and the packaging checks pass. A small number of checks ran against real things on this machine. Most of `QA_CHECKLIST.md` (PLAY on a full client, mods and addons from the network, MPQ, tweaks on a real client) was **not** run by a person and is **not verified**. Do not read an automated pass as a real-system pass.

Three labels are used throughout:
* **Automated**: unit and Qt tests, offscreen, fake or temporary data. They prove the code does what the test says.
* **Real system**: run here against a real file, process, registry, network or the real built program.
* **Not verified**: no run of any kind, or only a simulation.

## Real-system checks (run on this machine)
| Check | Result |
|---|---|
| Real built `EqUpdater3-v3.0.0-beta.1-Windows-Installer.exe` in a sandbox: install, upgrade, refuse while running, refuse foreign folders, uninstall, Apps & features entry (`tools/qa/installer_sandbox.py`, `bench-results/installer-sandbox.json`) | see the JSON; re-run on the final build |
| Real EqUpdater 2 running: installer refuses and writes nothing | pass |
| Settings and a sandbox game unchanged by install, upgrade and uninstall (every file hashed before and after); `D:\OctoWoW` and `C:\Users\...\Games\OctoWoW` fingerprinted, not given to the installer, unchanged | pass |
| Installed program's own headless self-test (all pages, all seven skins) | pass |
| Auto Loot on a **copy** of the real modded 10.7 MB `WoW.exe` (`lootgate`): detect, switch off, switch on, restore | pass; restored copy is byte-identical to the original, the original was not touched |
| Starting that copy through `platforms.spawn_detached` | the process started and exited at once with `0xC0000135` (a DLL it needs was not beside the copy). **This does not verify PLAY**: the game never ran |
| OctoBot feed over the real network | pass: the Worker `/api/snapshot` is not deployed (PR 77 is a draft), so the client fell back to the static feed; the data was about 2 h old and the client **marked it stale** as designed |
| Rendering of all seven skins with the account chip and sidebar at 1x, at 200% scale and in a 900 px window (`tools/qa/skin_matrix.py`, `bench-results/shots/`) | looked at by the developer's assistant, not a person with a real HiDPI monitor. **Found and fixed one bug**: in a window narrower than 940 px the floating sidebar was see-through, so page text showed through it. It now has an opaque backing |

## Automated
Full suite (`python tools/check.py`) and `python tools/beta_check.py --package --tests`: see the final report for the counts. They include the installer lifecycle (12), social robustness (9), AppImage layout (16), OctoBot snapshot client (30) and per-skin geometry tests.

## Not verified
* **PLAY on a full, real client**, including the Wine/UMU path. Only the plan logic is tested.
* **Client install and updates**: there is no base-client downloader in 3.0 by design; "find your game", the archive import and the verify step were tested on fake and sandbox folders only.
* **Mod install and removal, addon management, MPQ, tweaks** (`patch_exe` on a real client): automated only. Not run against the real network or a real client.
* **Settings migration from a real 2.x install**: automated on sample data only.
* **Offline start** with the network off: the code path is automated; not run on a real offline machine.
* The real installer **window** (the screen a person sees), SmartScreen (the installer is unsigned), shortcuts, a real upgrade from an earlier beta.
* **HiDPI on a real high-density monitor**, screen readers, high-contrast mode.
* **Linux**: nothing built or run (`LINUX.md`).
* **OctoWoW account and presence**: no OctoWoW service exists. Sign-in and presence are disabled and **not** operational (`SOCIAL.md`, `OCTOWOW_INTEGRATION_PROPOSAL.md`).
* GitHub Actions workflows have never run.
