# Write-safety review (WoW.exe, DLLs, addons, config)

Rule: never truncate a valid existing file before its replacement is prepared and validated. Reviewed 2026-11-24 across `core/*`, `lootgate.py`, `mpq.py`, `logindoctor.py`, `config.py`.

| Operation | Before | Now |
|---|---|---|
| Patch `WoW.exe` (tweaks, language) | opened `"wb"` first (truncates); the clean base could be a 0-byte cache | validated base (>= 1 MB, `MZ`), built in memory, size-checked, written beside and `os.replace`d (`core/tweaks.patch_exe`) |
| Mod DLL/file install | `_write_whole`: temp + replace (already safe); downloads go to `.part`/tmp | unchanged; stash/restore of old files on failure kept |
| Custom zip mod extraction | wrote each member straight over the target | each member via `atomic.replacing` |
| `dlls.txt` | plain `open(...,"w")` (the mod loading list) | atomic |
| `Config.wtf` write/update | plain `"w"` | atomic |
| `dxvk.conf` | plain `"w"` | atomic |
| Addon install | old folder moved aside, restored if the final rename fails (already safe) | unchanged; whole operation under the folder lock |
| MPQ download | `.part` then `os.replace`, optional `.bak` of the previous (already safe) | unchanged |
| Login Doctor realmlist repair | temp file + `.octobak` (already safe) | unchanged |
| 2.x `config.json` | temp + fsync + replace under one lock (already safe) | unchanged; across processes it is last-writer-wins for the whole file |
| `lootgate.set_exe_state` (Auto Loot bytes in `WoW.exe`) | in-place `r+b` byte patches (a few bytes at fixed offsets) | **open item**: not truncating, but a crash mid-write could leave the patch sites inconsistent. `exe_complete()` detects that and `enforce_autoloot` repairs it at Play. Moves to build-then-replace when wired into the Play pre-launch step |
| pfUI profile / first-run lua | plain `"w"` | **open**: low risk (addon-owned files); convert with the Addons page |

**Executable validation vs client variants.** `patch_exe` checks the *clean base* (cached `base-WoW.exe`, else the on-disk file) is at least 1,000,000 bytes and starts with `MZ`. It does not check for a specific size or hash, so every supported variant passes (the files seen are 5 to 11 MB); the only post-patch check is that the patch did not change the length. If a variant under 1 MB ever exists, `MIN_BASE_BYTES` is one line.

## Shared operation lock
`core/oplock.py`: an OS file lock (msvcrt / fcntl) in the app-data folder, keyed by the game folder path. Exclusive across processes, re-entrant per thread, 30 s wait then `GameFolderBusy("Another EqUpdater is changing this game folder ...")`, released by the OS if a process dies. Taken by `install_mod`, `uninstall_mod`, `install_addon_files`, `patch_exe` (MPQ and Auto Loot follow when migrated). Tested in-process and across processes (a second process holding it, then killed).

**Limit:** only builds that contain the lock honour it. The released 2.1.4 does not, so 2.1.4 and 3.0 running together on one folder are not protected. The beta notes must say so, and the 3.0 beta should refuse to start installs if a 2.x EqUpdater process is seen running (to do, Phase 4).

**`config.json`:** every writer uses `update_config` (lock + read-modify-write + atomic replace), so two processes cannot corrupt the file, but a lost update between processes is possible for the same key. Mod and addon operations are serialised by the folder lock, which removes the realistic collision.
