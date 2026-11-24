# aria2 / torrent dependency audit (done before removing anything)

Approved: 3.0 does **not** download the base client — no aria2, no torrent client, no hidden/legacy downloader. This lists everything that touches aria2 or the torrent so that unrelated features are not broken by the removal. **Nothing has been removed yet**; the 2.x Tk app on this branch is unchanged, removal happens when the backend is lifted into `core/` (Phase 3).

## Pure downloader — removed in 3.0
`CLIENT_TORRENT_URL`, `ensure_aria2c`, `run_aria2c`, `stop_aria2c`, `parse_aria_progress`, `clear_torrent_resume_state`, `_ensure_torrent_junction` (directory link for aria2 staging), `UpdateWorker.run` download phases (sync/reconcile), `shield_protected_files`/`unshield_protected_files`/`recover_protected_files` (exist only to keep aria2 from overwriting mod files), `platforms.find_aria2c`/`bundled_aria2c`/`aria2c_name`, aria2 fetch + pin in `build.py`/`tools/build_appimage.py`, `_maybe_show_aria2_firewall_notice` and its config key `aria2_firewall_notice_shown`, `active_torrent_hash`, Windows Defender/firewall wording that mentions aria2c, `prune_stale_client_files` **auto-delete** (see below).

## Depends on the torrent *metadata*, not on downloading — kept, re-sourced
These only need the list of files, sizes and piece hashes (a manifest). Reading a manifest distributes nothing.
| Feature | Today | 3.0 |
|---|---|---|
| "Update available / up to date" (`VerifyWorker`, `torrent_selection`) | fetches `.torrent`, compares sizes | keep: compare against manifest; **report** missing/changed files; no download of client files |
| Deep verify / "Verify game files" (`_verify_game_files`, `torrent_all_selection`, `torrent_tree_intact`) | aria2 hash-checks pieces | already partly in `clientscan.py` (deep scan = piece-hash verification against the manifest, read-only). Reuse that; pure-Python SHA-1 piece verify, cancellable |
| Pristine WoW.exe base (`refresh_pristine_wow`, `read_pristine_wow`, `PRISTINE_WOW_PATH`) | cache taken right after a sync | **unrelated to downloading and required by Tweaks** (`patch_exe` patches from the pristine copy). Keep. Take the cache when verify shows `WoW.exe` pristine (the existing marker check at `_LOCALE_ASSERT_OFFSET`), not "after sync" |
| `logindoctor` probe of `dl.octowow.st/download/client.torrent` ("Client download server") | HTTPS reachability | keep as a connectivity check (HEAD/GET of manifest), relabel |
| Locale/`ignore_speech` file selection (`_torrent_skip`, `_torrent_excluded_files`) | decides which files count | keep for verification scope |

## Needs a decision to keep behaviour honest (flagged, not blocking)
- **Repair of a damaged client file.** Today aria2 re-downloads the bad pieces. Re-downloading client files is distribution of the base client → not in 3.0. 3.0 verifies and *reports* exactly which files are bad and offers guidance (re-extract from the user's own ZIP/torrent copy; with a local archive selected, **extract the specific files from it**). MPQ patch re-download (`download_mpq`, separate source, own host allowlist) is unaffected and stays.
- **`prune_stale_client_files`** deletes files the current torrent no longer ships. Deleting user files from a manifest is risky without the sync that justified it; 3.0 will list "extra files" (as `clientscan` already does) and not delete automatically. Will confirm in Phase 3 review.
- **Incremental game updates.** "Supported incremental game updates" in the brief → MPQ patches/known patch files from existing sources keep working; base-client file replacement doesn't.

## Other
`ALLOWED_DOWNLOAD_HOSTS` stays for addons/mods/MPQ. Tests referencing the sync: `test_client_lock_guard.py` (kept: lock guard also protects `patch_exe`), anything importing `run_aria2c`/`torrent_*` to be moved to `clientscan`/manifest tests; I will list and port each rather than delete.
