"""
EqUpdater - a standalone updater for the OctoWoW client: game files, mods,
tweaks, texture packs and addons. Standard library, optional 'certifi' for
TLS; Python 3.10+.

Derived from Octo Updater by rebasedkon (see NOTICE), and reworked around one
rule: **EqUpdater manages what EqUpdater installed, or what the user asked it
to manage. It may inspect everything else, but it must not silently replace
it.**

The machinery that enforces that rule does not live in this file. Every
decision about whether a component may be touched comes from
`equpdater.planner.plan()`, which is a pure function over facts gathered
here; the version ordering behind it is `equpdater.versions`, the commit
ancestry is `equpdater.gitcompare`, and the local-modification check is
`equpdater.hashing`. This file gathers facts, draws them, and carries out
whatever the planner authorised - it does not decide.

That separation is the point. In the updater this grew from, the mods list,
the addons list, the apply worker and Update All each decided independently
what was "out of date", and four implementations of one judgement drifted
apart exactly as you would expect.
"""

import json
import hashlib
import os
import sys
import ssl
import re
import subprocess
import urllib.request
from urllib.parse import urlsplit
import shutil
import stat
import struct
import time
import math
import threading
import queue
from functools import cache
import tkinter as tk
from tkinter import filedialog

from . import (branding, diagnostics, gamepaths, gitlocal, i18n, logindoctor,
               lootgate, mpq, octolauncher, platforms)
from .i18n import N_, tr
from .config import (APP_DATA_DIR, BACKUP_DIR, CONFIG_FILE, bootstrap_config,
                     ensure_dir, load_config, new_addon_record,
                     new_mod_record, update_config)
from .gitcompare import (GIT_HOSTS, Ancestry, ancestry, canonical_repo_url,
                         forge_named, repo_ref, same_repo,
                         short as short_sha, split_repo_url)
from .hashing import files_hash, folder_hash
from .planner import (CAUSE_LOOKUP, Component, plan, skipped_notably,
                      updatable)
from .states import Action, Plan, Status
from .news import (ANNOUNCEMENTS_FORUM_ID, CHANGELOG_FORUM_ID,
                   ForumBlockedError, ForumError, fetch_first_post,
                   fetch_forum_page, fetch_latest_post, fetch_news_feed,
                   fetch_topic_list, is_challenge_page, newest_first)
from .ui import (AnimatedBackground, FontManager, GradientButton, prepare_fonts,
                 GradientPalette, apply_edge_fades, cover_background,
                 BackdropCanvas, blend, edge_fade_layers, photo_image,
                 style_title_bar)

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

APP_NAME         = branding.APP_NAME
UPDATER_VERSION  = branding.APP_VERSION
SERVER           = "https://octowow.st"
UA               = branding.USER_AGENT
DOWNLOAD_TIMEOUT = 10    # seconds without any data before a transfer aborts

# Where the app lives, where its settings live, and where a fresh install
# puts the game. All three now come from equpdater.branding, which knows the
# platform conventions - the inherited version wrote settings beside the
# executable on Linux, which breaks the moment the app is installed anywhere
# read-only.
APP_DIR = branding.app_dir()

# First-run default game folder: <home>/Games/OctoWoW, on every platform.
# Never inside the app's own folder: since 2.0.6 that folder is
# %LOCALAPPDATA%\Programs\EqUpdater and an update replaces it, and on Linux
# it is the AppImage's read-only mount, gone when the app closes.
DEFAULT_GAME_DIR = os.path.join(os.path.expanduser("~"), "Games", "OctoWoW")

# News is read straight from the public OctoWoW forum by this PC
# (equpdater/news.py): forum 2 is Announcements, forum 4 is Patch Notes and
# Changelog. Fetched once at launch and whenever a refresh button is pressed;
# never polled.
NEWS_TIMEOUT      = 8
NEWS_CHANGELOG_COUNT = 8

# Design dimensions, authored at 96 DPI (100% scaling). At startup the app
# multiplies these — and every other hard-coded pixel value, via self._px() —
# by a single DPI scale factor, so the whole fixed-pixel layout scales as a
# unit instead of point-fonts overflowing fixed geometry on high-DPI displays.
WIN_W, WIN_H = 1000, 700
FOOT_H       = 130
#: The footer's link icons (Discord, Ko-fi): height in design
#: pixels, the gap between them, and how opaque they rest. Under the cursor
#: they are fully white, which is the whole hover effect.
FOOTER_ICON_H = 22
FOOTER_ICON_GAP = 10
FOOTER_ICON_REST = 0.86
#: The update-all arrow beside PLAY, and its colour under the cursor.
UPDALL_COLOR = "#cda348"
UPDALL_HOVER = "#dfb95f"
UPDALL_ICON_H = 32
#: The arrow is coloured like the UPDATE ALL button it replaced: that
#: button's gradient (top, bottom), resting and under the cursor, its 1 px
#: border, and the 2 px glow around it.
UPDALL_FILL = (("#cda348", "#ad7f2d"), ("#dfb95f", "#c29036"))
UPDALL_BORDER = ("#d6b160", "#eed185")
UPDALL_GLOW = "#6d5524"
#: How opaque the arrow rests; under the cursor it is solid.
UPDALL_REST = 0.80

# Dark underwater palette tuned against the bubble artwork.  The artwork is the
# visual identity; panels stay deliberately restrained so text remains legible.
C_BG         = "#06132d"
C_PANEL      = "#081a35"
C_PANEL_ALT  = "#0b2142"
C_PANEL_BDR  = "#18385f"
C_HDR        = "#06142c"
C_DIVIDER    = "#19385f"
C_GOLD       = "#c99a3d"
C_GOLD_LT    = "#e4bd67"
C_PURPLE     = "#d7b56c"
C_TEXT       = "#eee9dc"
C_TEXT_DIM   = "#91a3bd"
C_LOG_BG     = "#041027"
C_OK         = "#79bf73"
C_ERR        = "#d17a7a"
C_MOD_HL     = "#a8b83c"
C_NEW        = "#ff3b3b"     # NEW! on a News post

PLAY_GRADIENT = GradientPalette(
    top="#4d8049", bottom="#396637",
    hover_top="#5b9155", hover_bottom="#427640",
    disabled_top="#24344a", disabled_bottom="#1a293e",
    border="#6f9d69", hover_border="#8aba84")
UPDATE_GRADIENT = GradientPalette(
    top="#d2a84e", bottom="#b8872f",
    hover_top="#e0ba62", hover_bottom="#c7963b",
    disabled_top="#28354a", disabled_bottom="#1c293d",
    border="#e0bd6d", hover_border="#f0d18a")
BUSY_GRADIENT = GradientPalette(
    top="#293a53", bottom="#1b2a40",
    hover_top="#293a53", hover_bottom="#1b2a40",
    disabled_top="#293a53", disabled_bottom="#1b2a40",
    border="#304966", disabled_fg=C_TEXT_DIM)

FONT_MONO   = ("Consolas", 9) if platforms.WINDOWS else ("Monospace", 9)


# Tweaks (WoW.exe patches, Config.wtf, stored values) live in core/tweaks.py;
# every name is re-exported so call sites and tests are unchanged.
from .core import tweaks as core_tweaks, logsink  # noqa: E402
from .core.tweaks import (  # noqa: E402,F401
    PRISTINE_WOW_PATH, _LOCALE_ASSERT_OFFSET, _LOCALE_TAG_OFFSET,
    _LOCALE_INDEX_OFFSET, _LOCALE_NAME_OFFSET, LOCALE_NAMES, LOCALES,
    DEFAULT_LOCALE, locale_patches, client_exe_locked, CLOSE_THE_GAME,
    read_pristine_wow, refresh_pristine_wow, write_config_wtf, sound_channels,
    update_config_wtf, TWEAKS_DEFAULTS, TWEAKS_ITEMS, TWEAKS_LIMITS, _FOV_REFS,
    fov_default_for_display, _DISPLAY_FALLBACK, _get_display_info_safe,
    _query_display_info, load_tweaks_config, save_tweaks_config)

# core.errors (lifted by tools/lift.py)
from .core.errors import (  # noqa: E402,F401
    _api_json,
    _describe_net_error,
    _error_chain,
    describe_install_error)

# core.mods (lifted by tools/lift.py)
from .core.mods import (  # noqa: E402,F401
    CUSTOM_MOD_DIR,
    CUSTOM_MOD_EXTS,
    CUSTOM_MOD_HOSTS,
    CUSTOM_ZIP_MAX_BYTES,
    DLL_LOAD_ORDER,
    DXVK_CONF_CONTENT,
    GAME_TOP_FOLDERS,
    GITHUB_API,
    MODS_REGISTRY,
    MOD_LOOKUPS,
    RELEASE_KINDS,
    UPDATER_CHECK_TTL,
    UPDATER_REPO,
    _MOD_VERSION_CACHE_TTL,
    _MOD_VERSION_STARTUP_MAX_AGE,
    _check_digest,
    _codeberg_latest,
    _custom_asset_entry,
    _custom_mod_host,
    _custom_mod_id,
    _custom_rec_id,
    _dll_key,
    _dlls_txt_path,
    _fetch_release_cached,
    _gitea_latest,
    _github_latest,
    _install_mod_zip,
    _invalidate_dll_cache,
    _note_lookup,
    _parse_version,
    _pick_asset,
    _read_lines,
    _release_latest,
    _release_version,
    _restore_mod_files,
    _same_source,
    _sha256_file,
    _slim_release,
    _sorted_dll_lines,
    _stash_mod_files,
    _write_dlls_txt,
    _write_dxvk_conf,
    _write_whole,
    add_custom_asset_mod,
    add_custom_mod,
    add_dll,
    backup_component,
    custom_asset_pattern,
    custom_files_conflict,
    custom_mod_conflict,
    custom_mod_entry,
    ensure_dll,
    fetch_mod_latest_version_cached,
    fetch_updater_latest_tag,
    forget_custom_mod,
    identify_installed_release,
    install_mod,
    load_custom_mods,
    mod_component,
    mod_dll_name,
    mod_files_on_disk,
    mod_found_files,
    mod_has_files,
    mod_installed_files_present,
    mod_lookup_line,
    mod_plan,
    mod_source_label,
    mod_supports_update_check,
    mod_twins,
    mod_zip_dll,
    mod_zip_plan,
    parse_mod_release_link,
    reconcile_dlls_txt,
    release_asset,
    release_by_tag,
    release_dlls,
    release_mod_files,
    remove_dll,
    uninstall_mod,
    updater_update_available)

# core.errors (lifted, part 2)
from .core.errors import (  # noqa: E402,F401
    DDOS_CHECK,
    DdosCheckError,
    _refuse_challenge)

# core.addons (lifted by tools/lift.py)
from .core.addons import (  # noqa: E402,F401
    ADDONS_CATALOG_TTL,
    ADDONS_URL,
    ADDONS_VERIFY_TTL,
    ADDON_ANCESTRY_CACHE_MAX,
    ADDON_ANCESTRY_RETRY,
    ADDON_FAILURES,
    ADDON_GIT_HOSTS,
    ADDON_LOOKUP_FAILURES,
    ADDON_SHA_CACHE_TTL,
    ADDON_STATUS_NAMES,
    ADDON_ZIP_HOSTS,
    BLOCKED_ADDONS,
    CURATED_ADDONS,
    LISTED_ADDONS,
    PFUI_DEFAULT_PROFILE,
    RECOMMENDED_ADDONS,
    _ADDON_CONFLICT_LOGGED,
    _ARCHIVES,
    _ARCHIVES_MAX,
    _ARCHIVE_KEEP,
    _GIT_REFS_MAX,
    _MARK,
    _PFUI_CHAT_BEGIN,
    _PFUI_CHAT_END,
    _PFUI_MARK_BEGIN,
    _PFUI_MARK_END,
    _PFUI_PREFIXES,
    _PFUI_STRIP_RE,
    _SHA,
    _commit_from_archive,
    _download_archive,
    _git_parts,
    _git_refs_sha,
    _log_addon_dir_state,
    _lua_value,
    _rmtree_force,
    _same_git_repo,
    _slim_addon_catalog,
    addon_ancestry,
    addon_cached_sha,
    addon_component,
    addon_failure,
    addon_install_source,
    addon_is_managed,
    addon_lookup_reason,
    addon_offers_replace,
    addon_remote_sha,
    addon_zip_url,
    addons_path,
    archive_commit,
    check_custom_addon_url,
    fetch_addons_catalog,
    install_addon_files,
    is_allowed_git_url,
    parse_wow_colored,
    patch_pfui_default_profile,
    read_toc_file,
    strip_wow_colors,
    writable_addons_path)

from .core.mods_ops import ModsOps  # noqa: E402
from .core.mods_rows import ModsRows  # noqa: E402

from .core.addons_ops import AddonsOps  # noqa: E402

from .core.mods_rows import ModsRows  # noqa: E402

# Secure networking lives in core/net.py (shared with the Qt front end);
# re-exported so every existing call site and test keeps its name.
from .core.net import (  # noqa: E402,F401
    SYSTEM_CA_BUNDLES, ca_bundles, make_ssl_context, SSL_CTX, CA_SOURCES,
    ALLOWED_DOWNLOAD_HOSTS, _check_url, _HttpsOnlyRedirectHandler,
    _SECURE_OPENER, secure_urlopen)


# Serializes all config read-modify-write cycles across the many worker
# threads (mods, addons, caches) and the main thread, so concurrent updates
# can't clobber each other. Reentrant so update_config() can call save.
# load_config / save_config / update_config / ensure_dir are imported from
# equpdater.config. Same names, same contract - atomic writes and one lock
# shared by the UI thread and every worker - so the several hundred call
# sites below are unchanged. What is new is underneath them: records carry
# provenance (who installed this, from where, and what did it look like) and
# a first launch migrates Octo Updater's settings across.


# ── logging ─────────────────────────────────────────────────────────────────
# One thread-safe log sink for the whole app. Any function — worker thread or
# main — calls log(); the GUI drains _LOG_Q on the main thread (see
# EqUpdaterApp._poll) and renders each line. This keeps all Tk access on the
# main thread without threading a log_fn argument through every function.
_LOG_Q: queue.Queue = queue.Queue()


def log(msg: str, tag: str = ""):
    """Append a line to the app log. Thread-safe; safe to call before the GUI
    exists (the queue just buffers until it's drained)."""
    _LOG_Q.put((msg, tag))


# Core modules log through logsink; in the Tk app that is this queue.
logsink.set_sink(log)


def remove_wdb(client_dir: str):
    """Delete the client's WDB folder (server-data cache, safe to drop)."""
    wdb = gamepaths.game_path(client_dir, "WDB")
    if not os.path.isdir(wdb):
        return
    try:
        shutil.rmtree(wdb)
        log("WDB cache cleared.", "dim")
    except Exception as e:
        log(f"Could not clear WDB: {e}", "err")


def get_client_version(out_dir: str) -> str:
    """Read version + build from fixed offsets in the client's WoW.exe."""
    exe_path = os.path.join(out_dir, "WoW.exe")
    if not os.path.exists(exe_path):
        return ""
    try:
        # Read only the two small fields, not the whole ~5 MB binary.
        with open(exe_path, "rb") as f:
            f.seek(0x00437bfc)
            build   = f.read(4).decode("utf-8", errors="replace").rstrip("\x00")
            f.seek(0x00437c04)
            version = f.read(6).decode("utf-8", errors="replace").rstrip("\x00")
    except Exception:
        return ""
    # Unexpected build, or an exe half-written mid-update, reads junk there -
    # show nothing rather than garbage. A real read looks like version "1.12.1",
    # build "5875".
    if not re.fullmatch(r"\d+(?:\.\d+)+", version) or not build.isdigit():
        return ""
    return f"{version} ({build})"


def fmt_size(num_bytes: float) -> str:
    """Human-readable size: KB, MB, GB. Uses binary (1024) steps but the
    familiar KB/MB/GB labels. Truncated, not rounded, so 8.7998 GiB reads as
    '8.79 GB' instead of rounding up to 8.80 and overstating the size."""
    if num_bytes < 1024 ** 2:
        return f"{int(num_bytes / 1024)} KB"
    if num_bytes < 1024 ** 3:
        return f"{int(num_bytes / 1024 ** 2 * 10) / 10:.1f} MB"
    return f"{int(num_bytes / 1024 ** 3 * 100) / 100:.2f} GB"


def fmt_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    return f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"


# ──────────────────────────────────────────────────────────────────────────────
#  Torrent-based client sync (aria2c)
# ──────────────────────────────────────────────────────────────────────────────
# Client files are synced over BitTorrent with aria2c, fetched on first use.
# The download is differential: only files that are missing or the wrong size
# are pulled.

CLIENT_TORRENT_URL = "https://dl.octowow.st/download/client.torrent"

# aria2 is GPLv2+, run unmodified. Windows: the pinned official build below,
# fetched on first use and checked against its sha256 (of the release .zip).
# Linux: a native aria2c -- the AppImage carries a pinned static build (see
# tools/build_appimage.py), otherwise the system's (platforms.find_aria2c).
ARIA2_ZIP_URL    = ("https://github.com/aria2/aria2/releases/download/"
                    "release-1.37.0/aria2-1.37.0-win-32bit-build1.zip")
ARIA2_ZIP_SHA256 = "35f6514cc5dd7e98a87b3c4c2d25a0754b9b063dbe59bc0f22d483464f61e5b6"
ARIA2C_PATH      = os.path.join(APP_DATA_DIR, "aria2c.exe")

# The torrent's top-level folder name: aria2 writes files under <dir>/<name>/…,
# so a junction <staging>/client → the real client dir lands them in place.
TORRENT_NAME       = "client"
TORRENT_STAGING_DIR = os.path.join(APP_DATA_DIR, "torrent-root")






_NO_WINDOW = platforms.no_window_flags()


def ensure_aria2c(log_fn=log) -> str:
    """Return the aria2c to run. Windows: aria2c.exe, downloaded and
    checksum-verified into APP_DATA_DIR on first use. Linux: a native
    aria2c, the AppImage's own or the system's -- never the Windows build
    under Wine. Raises on failure."""
    found = platforms.find_aria2c(APP_DATA_DIR)
    if found:
        return found
    if not platforms.WINDOWS:
        raise RuntimeError(
            "aria2c was not found. The EqUpdater AppImage includes it; "
            "running from source, install aria2 (for example "
            "'sudo apt install aria2' or 'sudo dnf install aria2').")
    log_fn("Fetching aria2c (one-time, ~2.5 MB)…", "acct")
    req = urllib.request.Request(ARIA2_ZIP_URL, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=60, allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != ARIA2_ZIP_SHA256:
        raise RuntimeError(
            f"aria2 checksum mismatch (got {digest[:12]}…); refusing to run it")
    import zipfile, io
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next((n for n in zf.namelist()
                     if n.lower().endswith("aria2c.exe")), None)
        if not name:
            raise RuntimeError("aria2c.exe not found in the aria2 archive")
        exe = zf.read(name)
    ensure_dir(APP_DATA_DIR)
    tmp = ARIA2C_PATH + ".part"
    with open(tmp, "wb") as f:
        f.write(exe)
    os.replace(tmp, ARIA2C_PATH)
    log_fn("aria2c ready.", "ok")
    return ARIA2C_PATH


def _bdecode(buf: bytes, pos: int = 0):
    """Minimal bencode decoder → (value, next_pos). Strings stay bytes."""
    ch = buf[pos]
    if ch == 0x69:                                   # i<int>e
        end = buf.index(b"e", pos)
        return int(buf[pos + 1:end]), end + 1
    if ch == 0x6c:                                   # l<items>e
        lst, p = [], pos + 1
        while buf[p] != 0x65:
            v, p = _bdecode(buf, p)
            lst.append(v)
        return lst, p + 1
    if ch == 0x64:                                   # d<pairs>e
        d, p = {}, pos + 1
        while buf[p] != 0x65:
            k, p = _bdecode(buf, p)
            v, p = _bdecode(buf, p)
            d[k] = v
        return d, p + 1
    colon = buf.index(b":", pos)                     # <len>:<bytes>
    n = int(buf[pos:colon])
    start = colon + 1
    return buf[start:start + n], start + n


def fetch_torrent(url: str = CLIENT_TORRENT_URL) -> tuple:
    """Download the .torrent → (raw_bytes, files). `files` is a list of
    (path_parts, length). The raw bytes' SHA-1 is the client 'version'."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=DOWNLOAD_TIMEOUT,
                        allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
        raw = r.read()
    decoded, _ = _bdecode(raw)
    info = decoded.get(b"info", {})
    files = [([p.decode("latin1") for p in f.get(b"path", [])],
              int(f.get(b"length", 0)))
             for f in info.get(b"files", [])]
    return raw, files


def torrent_version(raw: bytes) -> str:
    """SHA-1 of the whole .torrent file – the client 'version'. Changes whenever
    the torrent is re-rolled, so it's what we compare to detect a new build and
    decide whether aria2's resume state is stale. An occasional re-check when
    only the announce/date changed (content unchanged) is harmless."""
    return hashlib.sha1(raw).hexdigest()


@cache
def _torrent_excluded_files() -> frozenset:
    """Lower-cased basenames of every file the Mods tab installs, across all
    mods – the client-root files the torrent sync leaves to the mod system.

    The torrent ships several of them (VfPatcher.dll, nampower.dll, d3d9.dll,
    …), but the Mods tab is the single source of truth for installing /
    disabling / versioning them, so the sync must never fetch, re-add, or flag
    them. Computed once and memoized (MODS_REGISTRY is static at import)."""
    return frozenset(
        os.path.basename(f).lower()
        for mod in MODS_REGISTRY
        for f in mod.get("installed_files", [])
    )


def _is_torrent_excluded(parts) -> bool:
    return len(parts) == 1 and parts[0].lower() in _torrent_excluded_files()


def _torrent_skip(parts, ignore_speech: bool, client_dir: str) -> bool:
    """Files the sync must leave alone: mod-owned client-root files, plus a
    speech.MPQ the user opted to keep (Settings) – but only when it already
    exists on disk. A missing speech.MPQ is still downloaded so a clean client
    can launch; 'ignore' protects a custom copy, it doesn't skip a required file
    that isn't there."""
    if _is_torrent_excluded(parts):
        return True
    if ignore_speech and parts and parts[-1].lower() == "speech.mpq":
        return os.path.exists(os.path.join(client_dir, *parts))
    return False


# Files the sync must not rewrite (mod-owned client-root files, plus a kept
# custom speech.MPQ). Excluding them from --select-file stops aria2 fetching
# them on their own, but a torrent piece can straddle a file boundary, so
# repairing a selected neighbour re-downloads the shared piece and rewrites the
# excluded file's bytes too. We move them aside (rename) for the sync and put
# them back — instant and RAM-free, unlike copying a large speech.MPQ.
_SHIELD_SUFFIX = ".octobak"


def shield_protected_files(client_dir: str, files, ignore_speech: bool) -> list:
    """Prepare each sync-protected file (see _torrent_skip) for the sync. An
    existing one is moved aside via a same-dir rename; an absent one is recorded
    with backup=None, because the sync must never create it (only the Mods tab
    installs these) yet aria2 may write a partial stub for it through a shared
    piece. Returns [(orig, backup_or_None)] for unshielding."""
    shielded = []
    for parts, _length in files:
        if not _torrent_skip(parts, ignore_speech, client_dir):
            continue
        p = os.path.join(client_dir, *parts)
        if os.path.exists(p):
            bak = p + _SHIELD_SUFFIX
            try:
                os.replace(p, bak)      # same-fs, instant; aria2 sees p missing
                shielded.append((p, bak))
            except OSError:
                pass
        else:
            shielded.append((p, None))  # must stay absent afterwards
    return shielded


def unshield_protected_files(shielded) -> list:
    """Move each shielded file back, or delete the stub the sync created for a
    file that was absent before. Returns the basenames restored."""
    restored = []
    for p, bak in shielded:
        if bak is None:
            try:
                os.remove(p)            # drop aria2's partial stub; stay absent
            except OSError:
                pass
            continue
        try:
            os.replace(bak, p)          # our version wins over aria2's partial
            restored.append(os.path.basename(p))
        except OSError:
            pass
    return restored


def recover_protected_files(client_dir: str, files):
    """Undo a shield interrupted by a crash: an orphaned '.octobak' beside a
    torrent file is the real file – move it back into place."""
    for parts, _length in files:
        p   = os.path.join(client_dir, *parts)
        bak = p + _SHIELD_SUFFIX
        if os.path.exists(bak):
            try:
                os.replace(bak, p)
            except OSError:
                pass


def torrent_selection(client_dir: str, files, drop_mismatched=False,
                      ignore_speech=False):
    """1-indexed list of torrent files that are missing or the wrong size on
    disk (aria2 --select-file), plus whether any were entirely missing."""
    need, missing = [], False
    for i, (parts, length) in enumerate(files):
        if _torrent_skip(parts, ignore_speech, client_dir):
            continue
        dest = os.path.join(client_dir, *parts)
        try:
            size = os.path.getsize(dest)
        except OSError:
            missing = True
            need.append(i + 1)
            continue
        if size != length:
            # oversized/corrupt file poisons resume — drop it; a short file is
            # kept so aria2 can resume it
            if drop_mismatched or size > length:
                try:
                    os.remove(dest)
                except OSError:
                    pass
            need.append(i + 1)
    return need, missing


def torrent_all_selection(client_dir: str, files, ignore_speech=False) -> list:
    """1-indexed list of every non-mod file in the torrent. Used for an
    integrity pass: aria2 --check-integrity verifies each selected file's piece
    hashes and re-downloads only the bad/missing pieces – catching same-size but
    corrupted files that the size-based torrent_selection can't."""
    return [i + 1 for i, (parts, _) in enumerate(files)
            if not _torrent_skip(parts, ignore_speech, client_dir)]


def torrent_tree_intact(client_dir: str, files, ignore_speech=False) -> bool:
    for parts, length in files:
        if _torrent_skip(parts, ignore_speech, client_dir):
            continue
        try:
            if os.path.getsize(os.path.join(client_dir, *parts)) != length:
                return False
        except OSError:
            return False
    return True


# Legacy leftovers the current client no longer ships. Locale data folders are
# matched by name; the old patch archives by name AND exact size, so a player
# mod that reused one of these names is never deleted.
_LOCALE_DATA_DIRS = {
    "enus", "engb", "encn", "entw", "kokr", "frfr", "dede", "zhcn",
    "zhtw", "eses", "esmx", "ruru", "ptbr", "ptpt", "itit",
}
_LEGACY_ARCHIVES = {
    "patch-6.mpq": 451195806,
    "patch-7.mpq": 175256564,
    "patch-8.mpq": 484649870,
    "patch-9.mpq": 506808141,
    "patch-a.mpq": 241751337,
}


def prune_stale_client_files(client_dir: str, files) -> list:
    """Remove legacy Data/<locale>/ folders and known old-client patch MPQs the
    current torrent no longer ships. Folders are removed by name (unless the torrent
    still uses them); archives only when the name AND the exact size match a known
    legacy one. Returns removed names."""
    data_dir = gamepaths.game_path(client_dir, "Data")
    if not os.path.isdir(data_dir):
        return []
    # what the current torrent puts directly in Data/ (.mpq files and subdirs)
    expected, used_dirs = set(), set()
    for parts, _length in files:
        if not parts or parts[0] != "Data":
            continue
        if len(parts) == 2 and parts[1].lower().endswith(".mpq"):
            expected.add(parts[1].lower())
        elif len(parts) >= 3:
            used_dirs.add(parts[1].lower())

    removed = []
    for name in os.listdir(data_dir):
        lc, full = name.lower(), os.path.join(data_dir, name)
        if os.path.isdir(full):
            if lc in _LOCALE_DATA_DIRS and lc not in used_dirs:
                try:
                    shutil.rmtree(full)
                    removed.append(name + "/")
                except OSError:
                    pass
            continue
        if not lc.endswith(".mpq") or lc in expected:
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if _LEGACY_ARCHIVES.get(lc) == size:
            try:
                os.remove(full)
                removed.append(name)
            except OSError:
                pass
    return removed


# Downloadable MPQ content patches (not shipped with the client). Each is
# a single HTTP file with a `<url>.sha256` sidecar; an update is available when
# that published sha differs from the on-disk file's.
MPQ_PATCHES = [
    {
        "file": "patch-O.mpq",
        "name": "Octo Raid Visuals",
        "description": N_("Adds ground markers and sounds for boss abilities in raids."),
        "url":  "https://dl.octowow.st/client/latest/Data/patch-O.mpq",
        # Where the pack is made. Shown as a credit and a link for the user's
        # browser; downloads still come from OctoWoW's own copy above, which
        # publishes a checksum and is reachable by an app (octowow.st/git,
        # like the forum, answers apps with its DDoS check).
        "author":   "Rook",
        "homepage": "https://octowow.st/git/Rook/RaidVisuals-OctoWoW",
    },
]


def mpq_patch_for(filename: str):
    """The registry entry whose file matches `filename` (case-insensitive)."""
    lc = filename.lower()
    return next((e for e in MPQ_PATCHES if e["file"].lower() == lc), None)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def fetch_mpq_sha256(url: str) -> str:
    """The published SHA256 of an MPQ patch, from its `<url>.sha256` sidecar."""
    req = urllib.request.Request(url + ".sha256", headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=NEWS_TIMEOUT,
                        allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
        return r.read().decode("ascii", "ignore").strip().split()[0].lower()


#: Latest-release lookups for linked texture packs, per repository, so that
#: switching to the MPQ tab does not spend GitHub's unauthenticated rate
#: limit (60 an hour) on every visit.
_MPQ_RELEASE_CACHE: dict = {}
_MPQ_RELEASE_TTL = 600


def resolve_mpq_source(src: dict, local_file: str) -> mpq.Remote:
    """Ask a texture-pack source what it publishes now. Raises on any
    failure -- the caller treats that as "unable to verify", never as news."""
    kind = src.get("kind")
    if kind == "catalogue":
        entry = mpq_patch_for(src.get("file") or "")
        if entry is None:
            raise RuntimeError("no longer in the %s list" % branding.APP_NAME)
        return mpq.Remote(sha=fetch_mpq_sha256(entry["url"]), marker=None,
                          url=entry["url"])
    if kind == "url":
        return mpq.Remote(sha=fetch_mpq_sha256(src["url"]), marker=None,
                          url=src["url"])
    if kind in ("github_release", "codeberg_release", "gitea_release"):
        key = (kind, src.get("forge"), src["owner"].lower(), src["repo"].lower())
        hit = _MPQ_RELEASE_CACHE.get(key)
        if hit and time.time() - hit[0] < _MPQ_RELEASE_TTL:
            release = hit[1]
        else:
            if kind == "gitea_release":
                forge = forge_named(src.get("forge") or "")
                if forge is None or forge.kind != "gitea":
                    raise RuntimeError("unknown source kind %r" % kind)
                release = _gitea_latest(forge.api, src["owner"], src["repo"],
                                        raise_errors=True)
            else:
                latest = (_github_latest if kind == "github_release"
                          else _codeberg_latest)
                release = latest(src["owner"], src["repo"], raise_errors=True)
            if not release:
                raise RuntimeError("the repository has no releases")
            _MPQ_RELEASE_CACHE[key] = (time.time(), release)
        try:
            asset = mpq.pick_asset(release.get("assets") or [],
                                   src.get("asset"), local_file)
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        return mpq.asset_remote(asset)
    raise RuntimeError("unknown source kind %r" % kind)


def download_mpq(remote: mpq.Remote, data_dir: str, filename: str,
                 keep_existing: bool = False, on_progress=None) -> str:
    """Fetch `remote` into <data_dir>/<filename> and return its SHA-256.

    Verified against the published checksum whenever the source publishes
    one; a release asset without a digest is protected by TLS alone, which
    is exactly what a browser download of it would have been.

    `keep_existing` renames the current file to `<filename>.<stamp>.bak`
    in the same folder before the new one goes in -- a rename, not a copy,
    because packs run to hundreds of megabytes. The game only loads
    patch-?.mpq, so the kept copy is inert until renamed back."""
    if not remote.url:
        raise RuntimeError("the source gave no download link")
    ensure_dir(data_dir)
    tmp = os.path.join(data_dir, filename + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(remote.url, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=DOWNLOAD_TIMEOUT,
                        allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done  = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    got = h.hexdigest().lower()
    if remote.sha and got != remote.sha:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("checksum verification failed")
    dest = os.path.join(data_dir, filename)
    if keep_existing and os.path.exists(dest):
        kept = "%s.%s.bak" % (dest, time.strftime("%Y%m%d-%H%M%S"))
        os.replace(dest, kept)
        log("  Kept your previous copy as %s" % os.path.basename(kept), "dim")
    os.replace(tmp, dest)
    return got


def clear_torrent_resume_state():
    """Delete aria2's saved control (.aria2) and metadata (.torrent) files in
    the staging dir. They pin a specific torrent revision (info-hash) and record
    which pieces are 'done', so a stale one causes an 'info hash mismatch' error
    after the client torrent is re-rolled, or blocks re-downloading a file the
    user deleted. Safe to call when nothing is there."""
    try:
        for name in os.listdir(TORRENT_STAGING_DIR):
            if name.endswith((".aria2", ".torrent")):
                try:
                    os.remove(os.path.join(TORRENT_STAGING_DIR, name))
                except OSError:
                    pass
    except OSError:
        pass






def _ensure_torrent_junction(client_dir: str) -> str:
    """Point <staging>/client at client_dir through a directory link (an NTFS
    junction on Windows, no admin needed; a symlink on Linux) so aria2 writes
    the torrent's files straight into the real client dir. Returns the
    staging dir to pass as aria2 --dir."""
    staging = TORRENT_STAGING_DIR
    ensure_dir(staging)
    link   = os.path.join(staging, TORRENT_NAME)
    target = os.path.abspath(client_dir)
    try:
        if os.path.isdir(link) and \
                os.path.abspath(os.path.realpath(link)) == target:
            return staging
    except OSError:
        pass
    # remove a stale junction/link (rmdir drops the reparse point, not its
    # target's contents) then recreate it
    try:
        os.rmdir(link)
    except OSError:
        try:
            os.remove(link)
        except OSError:
            pass
    platforms.link_dir(link, target)
    return staging


_SIZE_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2,
               "GiB": 1024 ** 3, "TiB": 1024 ** 4}
_UNIT = "|".join(_SIZE_UNITS)              # B|KiB|MiB|GiB|TiB
_SIZE = rf"[\d.]+(?:{_UNIT})"              # e.g. 8.8GiB
_FRAC = rf"({_SIZE})/({_SIZE})\((\d+)%\)"  # done/total(percent%)
_ARIA_FRAC = re.compile(_FRAC)
_ARIA_DL   = re.compile(rf"DL:({_SIZE})")
# During --check-integrity aria2 prints the hash-check progress in a separate
# field, e.g. '… [Checksum:#f66a3e 236MiB/1.5GiB(15%)]', while the leading
# completed/total stays at 0%/0B (a complete client downloads nothing). Read the
# Checksum fraction so the bar tracks the check instead of freezing at 0.
_ARIA_CHK  = re.compile(rf"Checksum:#\w+\s+{_FRAC}")


def _to_bytes(s: str) -> float:
    m = re.match(rf"^([\d.]+)({_UNIT})$", s.strip())
    return float(m.group(1)) * _SIZE_UNITS[m.group(2)] if m else 0.0


def parse_aria_progress(line: str):
    """Parse an aria2 summary line like '1.2GiB/8.8GiB(13%) … DL:5.0MiB' →
    {progress, done, total, bps, checking}, or None for non-progress lines.
    A checksum-check line is preferred over the (idle) download fraction."""
    chk = _ARIA_CHK.search(line)
    m   = chk or _ARIA_FRAC.search(line)
    if not m:
        return None
    dl = _ARIA_DL.search(line)
    return {"progress": int(m.group(3)) / 100.0,
            "done":  _to_bytes(m.group(1)),
            "total": _to_bytes(m.group(2)),
            "bps":   _to_bytes(dl.group(1)) if dl else 0.0,
            "checking": chk is not None}


# The currently-running aria2c child, so it can be killed when the app quits
# (a daemon worker thread dying would otherwise orphan it, still downloading
# headless). --stop-with-process is aria2's own belt-and-suspenders for this,
# but it's unreliable on Windows — hence the explicit kill too.
_active_aria2: "subprocess.Popen | None" = None
_active_aria2_lock = threading.Lock()


def stop_aria2c():
    """Terminate the running aria2c child, if any. Safe to call from any thread
    (e.g. the app's close handler)."""
    global _active_aria2
    with _active_aria2_lock:
        proc, _active_aria2 = _active_aria2, None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def run_aria2c(client_dir, select_files=None, check_integrity=False,
               on_progress=None, should_cancel=None, log_fn=log):
    """Sync the client torrent into client_dir with aria2c (leech-only). Blocks
    until aria2c exits; raises on a non-zero exit or cancellation. Calls
    on_progress(dict) per update and should_cancel()->bool to abort.

    aria2 is handed the .torrent URL (not a local copy), so it always fetches
    the server's current torrent at download time – no chance of running a stale
    local .torrent if the user starts the update long after the verify."""
    global _active_aria2
    exe     = ensure_aria2c(log_fn)
    staging = _ensure_torrent_junction(client_dir)
    args = [
        exe,
        f"--dir={staging}",
        # aria2 exits when this PID (the updater) does — stops an orphaned
        # download if we're killed before the explicit stop_aria2c() runs.
        f"--stop-with-process={os.getpid()}",
        "--seed-time=0",
        f"--check-integrity={'true' if check_integrity else 'false'}",
        "--bt-remove-unselected-file=false",
        "--continue=true",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--disk-cache=128M",
        "--stream-piece-selector=inorder",
        "--max-tries=0",
        "--retry-wait=5",
        "--bt-stop-timeout=120",
        "--auto-save-interval=15",
        "--summary-interval=1",
        "--human-readable=false",
        "--truncate-console-readout=false",
        "--console-log-level=warn",
        "--enable-dht=true",
        "--bt-enable-lpd=true",
        "--max-connection-per-server=8",
        "--split=16",
        "--min-split-size=1M",
    ]
    if select_files:
        args.append("--select-file=" + ",".join(str(i) for i in select_files))
    args.append(CLIENT_TORRENT_URL)

    proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            bufsize=1, creationflags=_NO_WINDOW)
    with _active_aria2_lock:
        _active_aria2 = proc
    try:
        for line in proc.stdout:
            if should_cancel and should_cancel():
                proc.terminate()
                raise RuntimeError("Cancelled")
            line = line.strip()
            if not line:
                continue
            p = parse_aria_progress(line)
            if p:
                # aria2's console readout floods idle '0B/total(0%)' lines (no
                # bytes, no checksum) between the once-a-second summary blocks
                # that carry the real figure. Drop the idle ones so they can't
                # stomp progress back to 0 under the UI's latest-wins draining.
                informative = (p["done"] or p["progress"]
                               or p["bps"] or p["checking"])
                if on_progress and informative:
                    on_progress(p)
            else:
                log_fn(f"[aria2] {line}", "dim")
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        with _active_aria2_lock:
            if _active_aria2 is proc:
                _active_aria2 = None
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"aria2c exited with code {code}")






class VerifyWorker:
    def __init__(self, out_dir: str, log_q: queue.Queue, prog_q: queue.Queue):
        self.out_dir = out_dir
        self.log_q   = log_q
        self.prog_q  = prog_q
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def log(self, msg, tag=""):
        self.log_q.put((msg, tag))

    def progress(self, value, label=""):
        self.prog_q.put((value, label))

    def run(self):
        try:
            self.log("Checking for updates…", "acct")
            raw, files = fetch_torrent()
            self.log("Checking game files…", "acct")

            # aria2 selects by size (a patched WoW.exe keeps the torrent's size,
            # so it's never flagged); need == files missing or wrong-sized.
            ignore_speech = bool(load_config().get("ignore_speech", False))
            need, _missing = torrent_selection(self.out_dir, files,
                                               ignore_speech=ignore_speech)
            have_exe = os.path.exists(os.path.join(self.out_dir, "WoW.exe"))

            if have_exe and not need:
                self.log("Everything is up to date!", "ok")
                self.log_q.put(("__UP_TO_DATE__", ""))
            else:
                self.log("Update available.", "acct")
                self.log_q.put(("__UPDATE_NEEDED__", ""))
        except Exception as e:
            self.log(f"Verification failed: {e}", "err")
            self.log_q.put(("__UPDATE_NEEDED__", ""))


class UpdateWorker:
    def __init__(self, out_dir: str, log_q: queue.Queue, prog_q: queue.Queue,
                 check_integrity: bool = False, overwrite_config: bool = False):
        self.out_dir = out_dir
        self.log_q   = log_q
        self.prog_q  = prog_q
        self._cancel = False
        # Integrity mode: aria2 hash-checks every file's pieces and repairs
        # them (catches same-size corruption size-based selection misses).
        self.check_integrity  = check_integrity
        # Write a fresh Config.wtf on a reconcile.
        self.overwrite_config = overwrite_config

    def cancel(self):
        self._cancel = True

    def log(self, msg: str, tag: str = ""):
        self.log_q.put((msg, tag))

    def progress(self, value: float, label: str = "", status: str | None = None):
        # status (when given) updates the big status line; None leaves it as-is.
        self.prog_q.put((value, label, status))

    def build_tweaks(self, buf, tweaks: dict | None = None):
        return core_tweaks.build_tweaks(buf, tweaks)
        # fmt: on

    def patch_exe(self, tweaks: dict | None = None):
        core_tweaks.patch_exe(self.out_dir, tweaks, self.log)

    def run(self):
        # The selection is recomputed here from the live torrent so an
        # interrupted sync always resumes against the current file set.
        try:
            self.log("\nStarting client sync…\n", "acct")

            # Before anything is fetched or written. A sync that discovers this
            # halfway through has already replaced some of the client and left
            # the rest, which is a worse state than not having started.
            if client_exe_locked(self.out_dir):
                self.log(CLOSE_THE_GAME, "err")
                self.progress(0.0, N_("Close the game first"))
                # The window leaves its "updating" state only on __DONE__ or
                # __ERROR__; returning without one left it stuck.
                self.log_q.put(("__ERROR__", ""))
                return

            self.progress(0.0, N_("Preparing…"))
            raw, files = fetch_torrent()
            version = torrent_version(raw)

            # aria2's saved control state pins a torrent revision and its
            # completed pieces. When the torrent was re-rolled (new identity) or
            # the folder changed, that state is stale — an 'info hash mismatch'
            # error, or skipped re-downloads. Clear it and (for a new revision)
            # drop wrong-sized files so they re-fetch clean.
            cfg = load_config()
            stale = (cfg.get("active_torrent_hash") != version or
                     cfg.get("active_client_dir") != os.path.abspath(self.out_dir))
            if stale:
                clear_torrent_resume_state()
                update_config(lambda c: c.update({
                    "active_torrent_hash": version,
                    "active_client_dir": os.path.abspath(self.out_dir)}))

            # Config.wtf is user game config, not in the torrent — (re)write it
            # on a reconcile (overwrite_config), or when missing.
            cfg_wtf = gamepaths.game_path(self.out_dir, "WTF", "Config.wtf")
            if self.overwrite_config or not os.path.exists(cfg_wtf):
                write_config_wtf(self.out_dir)

            # WoW.exe on disk is patched, so an integrity check always flags it
            # and re-fetches its pieces over the network. Restore the cached
            # pristine base first: the check then passes with no re-download when
            # the client is unchanged (aria2 still repairs it if the torrent's
            # WoW.exe genuinely changed). It's re-patched after the check.
            wow_path = os.path.join(self.out_dir, "WoW.exe")
            if (self.check_integrity and os.path.exists(PRISTINE_WOW_PATH)
                    and os.path.exists(wow_path)):
                shutil.copyfile(PRISTINE_WOW_PATH, wow_path)

            # Put back any file a prior run shielded but couldn't restore (crash
            # mid-sync), so its version isn't stranded as a .octobak.
            recover_protected_files(self.out_dir, files)

            # speech.MPQ is left unverified/un-updated when the user keeps a
            # custom one (Settings → Ignore speech.mpq).
            ignore_speech = bool(load_config().get("ignore_speech", False))
            if self.check_integrity:
                # Full piece-hash verify + repair of every non-mod file — aria2
                # re-hashes them and re-fetches only the bad/missing pieces.
                need, missing = torrent_all_selection(
                    self.out_dir, files, ignore_speech=ignore_speech), False
            else:
                need, missing = torrent_selection(
                    self.out_dir, files, drop_mismatched=stale,
                    ignore_speech=ignore_speech)
            # A file the user deleted leaves its pieces marked done in the
            # control file, so aria2 skips it forever — clear resume state.
            if missing:
                clear_torrent_resume_state()
            wow_downloaded = any(files[i - 1][0] == ["WoW.exe"] for i in need)

            if need:
                self.log(
                    (f"Verifying {len(need)} file(s) via torrent…"
                     if self.check_integrity
                     else f"Syncing {len(need)} file(s) via torrent…"), "acct")

                # A reconcile runs two aria2 phases: it hash-checks every file
                # (p["checking"]), then fetches the bad/missing pieces. Flip the
                # status from "Verifying" to "Updating" between them so it doesn't
                # read "Verifying" while files are actually being updated. Only
                # sent on a phase change.
                _phase = {"status": None}

                def _prog(p):
                    label = f"{fmt_size(p['done'])} / {fmt_size(p['total'])}"
                    if p["bps"]:
                        label += "   •   " + fmt_speed(p["bps"])
                    frac = p["done"] / p["total"] if p["total"] else 0.0
                    status = None
                    if self.check_integrity:
                        want = (N_("Verifying game files…") if p["checking"]
                                else N_("Updating game files…"))
                        if want != _phase["status"]:
                            _phase["status"] = status = want
                    self.progress(min(frac, 1.0), label, status)

                # Excluded files (mod-owned + kept speech.MPQ) can still be
                # rewritten by aria2 through a shared torrent piece. Move them
                # aside for the sync and put them back after, so the Mods tab /
                # custom speech stays authoritative.
                shielded = shield_protected_files(self.out_dir, files,
                                                  ignore_speech)
                try:
                    run_aria2c(self.out_dir, select_files=need,
                               check_integrity=self.check_integrity,
                               on_progress=_prog,
                               should_cancel=lambda: self._cancel,
                               log_fn=self.log)
                finally:
                    for name in unshield_protected_files(shielded):
                        self.log(f"  kept mod file: {name}", "dim")
            else:
                self.log("All game files already present.", "dim")

            if self._cancel:
                self.log("\nUpdate cancelled.", "err")
                self.progress(0.0, N_("Cancelled"))
                self.log_q.put(("__ERROR__", ""))
                return

            self.progress(1.0, N_("Verifying…"))
            if not torrent_tree_intact(self.out_dir, files,
                                       ignore_speech=ignore_speech):
                self.log("\n✗  Download incomplete – click Update to finish.", "err")
                self.log_q.put(("__ERROR__", ""))
                return

            self.log("\nDownload complete.", "ok")
            remove_wdb(self.out_dir)

            # Drop legacy leftovers the current torrent no longer ships
            for gone in prune_stale_client_files(self.out_dir, files):
                self.log(f"Removed legacy file: {gone}", "dim")

            # Cache the fresh pristine exe, then patch WoW.exe from that
            # clean base — but only when the sync actually (re)downloaded it.
            refresh_pristine_wow(self.out_dir)
            if wow_downloaded:
                self.progress(1.0, N_("Patching…"))
                self.patch_exe()
            else:
                self.log("\nWoW.exe unchanged – skipping patch.", "dim")

            self.progress(1.0, "")
            self.log("\n✓  Everything is up to date!", "ok")
            client_ver = get_client_version(self.out_dir)
            if client_ver:
                self.log(f"Client version: {client_ver}", "dim")
                self.log_q.put((f"__VERSION__{client_ver}", ""))
            else:
                self.log("Could not read client version from WoW.exe", "dim")
            self.log_q.put(("__DONE__", ""))

        except Exception as e:
            self.log(f"\n✗  {e}", "err")
            self.progress(0.0, "")
            self.log_q.put(("__ERROR__", ""))








# ──────────────────────────────────────────────────────────────────────────────
#  Mods definition & engine
# ──────────────────────────────────────────────────────────────────────────────





















































































# ── Custom mods ─────────────────────────────────────────────────────────────
#
# A player may add a mod EqUpdater does not list, from the releases of a
# GitHub, Codeberg or OctoWoW Git repository. One file of the latest release:
#
#   * a .dll is installed the way OctoLogin is -- to mods/<file>.dll,
#     registered in dlls.txt;
#   * a .zip is unpacked into the game folder (its own top folder dropped
#     when it has one), and a single loadable .dll in it is registered --
#     a mod needs no DLL at all;
#   * an .mpq goes to Data/.
#
# From then on it is an ordinary entry in MODS_REGISTRY: the same install,
# update, disable and removal code, the same planner. The records live in
# the config ("custom_mods"); load_custom_mods() puts them into the registry.
#
# A mod may run code inside the game, so nothing is installed without the
# player confirming a warning that says so.































































# ──────────────────────────────────────────────────────────────────────────────
#  Addons definition & engine  (installs via git-archive zips pinned to a
#  commit sha, so no full git client is needed)
# ──────────────────────────────────────────────────────────────────────────────























#: How a mouse wheel reaches Tk. Windows (and macOS) send <MouseWheel> with a
#: delta; X11 -- Linux -- sends no <MouseWheel> at all, but a press of button
#: 4 (up) or 5 (down) per notch. Binding only the first left every list
#: unscrollable by wheel on Linux.
WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def wheel_steps(event) -> int:
    """Units to scroll for one wheel event: negative is up."""
    num = getattr(event, "num", None)
    if num == 4:
        return -1
    if num == 5:
        return 1
    delta = getattr(event, "delta", 0) or 0
    if not delta:
        return 0
    steps = int(-delta / 120)
    # A fine-grained wheel or touchpad sends less than one 120 notch at a
    # time; that is still a scroll, not nothing.
    return steps or (-1 if delta > 0 else 1)








#: What is wrong with a line of the Game launcher's environment variables,
#: by platforms.parse_env_lines reason.
ENV_PROBLEMS = {
    "no_equals": N_("Line {line}: write it as NAME=value."),
    "bad_name":  N_("Line {line}: a name starts with a letter or _ and has "
                    "only letters, digits and _."),
    "nul":       N_("Line {line}: the value contains a character that "
                    "cannot be used."),
}


def env_problem_text(problems) -> str:
    """One translated line per unusable environment variable line."""
    return "\n".join(tr(ENV_PROBLEMS[reason], line=number)
                     for number, reason in problems)


#: Why a Linux launch would fail at once, by platforms.launch_problems reason.
LAUNCH_PROBLEMS = {
    "runner_missing":     N_("'{value}' was not found in PATH."),
    "runner_not_file":    N_("The launcher program does not exist or cannot "
                             "be run:\n{value}"),
    "prefix_relative":    N_("The Wine prefix is not a full path:\n{value}"),
    "prefix_not_dir":     N_("The Wine prefix is a file, not a folder:\n{value}"),
    "prefix_parent":      N_("The Wine prefix's parent folder does not "
                             "exist:\n{value}"),
    "protonpath_missing": N_("PROTONPATH does not exist:\n{value}"),
}


def launch_problem_text(problems) -> str:
    """One translated paragraph per launch problem."""
    return "\n\n".join(tr(LAUNCH_PROBLEMS[reason], value=value)
                       for reason, value in problems)


#: How long after PLAY a runner that quits with an error counts as one that
#: could not start the game, and where its output goes (Linux).
LAUNCH_WATCH_MS = 8000
LAUNCH_OUTPUT = os.path.join(APP_DATA_DIR, "last-launch.log")
























# ── Commits from archives, while a Gitea's API is behind its DDoS check ────
#
# octowow.st answers apps' API calls with its DDoS page but serves repository
# archives, and Gitea stamps the commit an archive was made from into the
# zip's comment (it is `git archive`). So the latest commit can be read from
# the archive itself: <repo>/archive/HEAD.zip for the default branch, or the
# pinned branch's. That is enough to install and to notice that the source
# moved on. It is not enough to say which way it moved -- that is the
# compare API, still blocked -- so a changed addon reads "Unable to verify"
# and is never updated automatically; Replace is offered instead. Once the
# API answers again none of this runs.





















# ── Why an addon install/update failed, for the person who asked for it ────
#
# Each failure is put in one of a few plain categories, and the one sentence
# shown names the host that failed -- a remote that is down or behind its
# DDoS check is not the player's internet. The English text and the full
# exception chain go to the log; the dialog shows the translated sentence.
























# ── pfUI "Default" profile patch ─────────────────────────────────────────────
# pfUI ships a set of built-in design profiles. After every pfUI install/update
# we add a curated "Default" profile and make it the firstrun default. Because
# an update overwrites pfUI's files, the patch is re-applied each time and is
# idempotent (marked blocks are replaced, not duplicated).










# ──────────────────────────────────────────────────────────────────────────────
#  Tweaks definition
# ──────────────────────────────────────────────────────────────────────────────


















def enforce_autoloot(client_dir: str) -> list:
    """Make WoW.exe say what the Auto Loot checkbox says,
    just before the game starts. OctoLauncher patches WoW.exe to its own
    setting whenever it plays, so the bytes can change behind the checkbox.
    Returns what it did, for the log. Never prevents a launch."""
    notes = []
    wanted = load_tweaks_config().get("alwaysAutoLoot")
    exe = os.path.join(client_dir, "WoW.exe")
    state = lootgate.exe_state(exe)
    target = state if wanted is None else bool(wanted)
    if state is not None and (state != target
                              or not lootgate.exe_complete(exe, target)):
        if client_exe_locked(client_dir):
            notes.append("Auto Loot: WoW.exe does not match the setting, and "
                         "it is in use so it was left as it is.")
        else:
            lootgate.set_exe_state(exe, target)
            notes.append(
                f"Auto Loot: WoW.exe did not match the setting (another "
                f"launcher, or an older EqUpdater, patched it); set to "
                f"{'on' if target else 'off'}." if state != target else
                f"Auto Loot: WoW.exe now reads Shift when you loot "
                f"(set to {'on' if target else 'off'}).")
    try:
        if lootgate.remove_gate(writable_addons_path(client_dir)):
            notes.append("Auto Loot: removed the !EqLootGate addon; "
                         "WoW.exe does its job now.")
    except Exception:
        pass
    return notes



# ──────────────────────────────────────────────────────────────────────────────
#  News feed
# ──────────────────────────────────────────────────────────────────────────────


def _strip_html(raw: str) -> str:
    """Reduce forum HTML to readable plain text for a Tk widget."""
    import re
    import html as html_mod
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", raw)
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)<li[^>]*>", "\n• ", txt)
    txt = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr|blockquote)>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html_mod.unescape(txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" ?\n ?", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


_MONTHS = (N_("Jan"), N_("Feb"), N_("Mar"), N_("Apr"), N_("May"), N_("Jun"),
           N_("Jul"), N_("Aug"), N_("Sep"), N_("Oct"), N_("Nov"), N_("Dec"))


def _format_news_date(iso: str) -> str:
    from datetime import datetime
    try:
        d = datetime.fromisoformat(iso)
    except Exception:
        return iso
    return f"{d.day:02d} {tr(_MONTHS[d.month - 1])} {d.year}"


#: A News post this recent is NEW! whenever the launcher was last opened.
NEWS_NEW_DAYS = 3


def _news_timestamp(iso: str) -> float | None:
    """A post's date as a Unix time, or None. A date with no offset (the
    news.json fallback) is taken as UTC, as the forum's are."""
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def news_is_new(iso: str, last_seen: float | None, now: float) -> bool:
    """Whether a post is NEW!: posted after the player last saw that list
    (``last_seen``, a Unix time; None before the first time), *or* within
    the last NEWS_NEW_DAYS days. Either is enough: a post four days old is
    still new to a player who has not opened the launcher for a week."""
    ts = _news_timestamp(iso)
    if ts is None:
        return False
    return ((last_seen is not None and ts > last_seen)
            or ts >= now - NEWS_NEW_DAYS * 86400)


def news_marks(items: list, last_seen: float | None, now: float) -> set:
    """The ids of the posts in a list that are NEW!."""
    return {str(i.get("id", "")) for i in items
            if news_is_new(i.get("date", ""), last_seen, now)}


def _news_open(req, timeout):
    return secure_urlopen(req, timeout=timeout,
                          allowed_hosts={"octowow.st"})


#: How long a Changelog topic's opening post is kept before it is read
#: again (a patch note may be corrected after it is posted).
NEWS_FIRST_POST_TTL = 24 * 3600


def _changelog_rows(limit: int) -> tuple:
    """The ``limit`` newest-started topics of the Changelog forum, reading
    further listing pages while there are fewer, and whether there are more
    beyond them. A topic pinned to every page is counted once."""
    rows, seen, start = [], set(), 0
    while start is not None and len(rows) <= limit:
        page, start = fetch_forum_page(CHANGELOG_FORUM_ID, start,
                                       opener=_news_open, user_agent=UA,
                                       timeout=NEWS_TIMEOUT)
        for row in page:
            if row.topic_id not in seen:
                seen.add(row.topic_id)
                rows.append(row)
    return rows[:limit], len(rows) > limit or start is not None


#: Whether the Changelog forum has topics past the ones last read.
CHANGELOG_MORE = {"more": False}


def fetch_patch_notes(limit: int = NEWS_CHANGELOG_COUNT) -> list:
    """The newest topics of OctoWoW's Patch Notes and Changelog forum (f=4),
    newest-started first: title, start date, author and link from the
    listing, and each topic's *opening* post as its preview.

    The listing is one request. An opening post is read once and kept for
    a day (config "news_first_posts", by topic id), so a refresh is normally
    the listing alone. A topic page that cannot be read leaves that entry
    with its last known preview, or none; a DDoS check on one stops the
    rest from being asked. Raises news.ForumError when the listing itself
    cannot be read."""
    rows, CHANGELOG_MORE["more"] = _changelog_rows(limit)
    cache = load_config().get("news_first_posts", {})
    now = time.time()
    fresh, items, blocked = {}, [], False
    for row in rows:
        key = str(row.topic_id)
        entry = cache.get(key) or {}
        body, links = entry.get("body"), entry.get("links") or []
        if body is None or now - entry.get("timestamp", 0) >= NEWS_FIRST_POST_TTL:
            if not blocked:
                try:
                    post = fetch_first_post(row, opener=_news_open,
                                            user_agent=UA,
                                            timeout=NEWS_TIMEOUT)
                    body = post.content
                    links = [list(link) for link in post.links]
                    fresh[key] = {"body": body, "links": links,
                                  "timestamp": now}
                except ForumBlockedError as exc:
                    blocked = True
                    log(f"Changelog: {exc}", "dim")
                except ForumError as exc:
                    log(f"Changelog: {exc}", "dim")
        item = row.to_item()
        if body:
            item["body"] = item["html"] = body
            item["links"] = links
        items.append(item)

    shown = {str(r.topic_id) for r in rows}

    def remember(c):
        kept = {k: v for k, v in (c.get("news_first_posts") or {}).items()
                if k in shown}
        kept.update(fresh)
        c["news_first_posts"] = kept
    if fresh or set(cache) - shown:
        update_config(remember)
    return items


def fetch_featured_post() -> dict | None:
    """The newest-started topic of OctoWoW's Announcements forum (f=2) with
    its opening post -- not a pinned topic, and never a reply. When the
    forum cannot be read -- above all while it shows apps its DDoS check --
    the newest item of the public news.json instead (as OctoLauncher reads
    it; fetched directly, no launcher needed), or None when that feed is
    validly empty. If both fail, the forum's error is raised: it is the
    reason the panel cannot update."""
    try:
        return fetch_latest_post(
            ANNOUNCEMENTS_FORUM_ID, opener=_news_open, user_agent=UA,
            timeout=NEWS_TIMEOUT).to_item()
    except ForumError as forum_exc:
        log(f"Announcements: {forum_exc}; trying news.json", "dim")
        try:
            items = fetch_news_feed(opener=_news_open, user_agent=UA,
                                    timeout=NEWS_TIMEOUT)
        except ForumError as feed_exc:
            log(f"Announcements: {feed_exc}", "dim")
            raise forum_exc
        if not items:
            return None
        # The newest by date: a pinned older post may lead the feed.
        item = dict(max(items, key=lambda i: i["date"]))
        item["_note"] = getattr(forum_exc, "short", "") or str(forum_exc)
        return item


def _news_error(section: str, exc: Exception) -> str:
    """The panel's line for a failed refresh; the full reason goes to the
    session log."""
    log(f"{section}: {exc}", "err")
    return tr("{section} unavailable: {reason}", section=tr(section),
              reason=tr(getattr(exc, "short", None) or str(exc)))

# ──────────────────────────────────────────────────────────────────────────────
#  GUI
# ──────────────────────────────────────────────────────────────────────────────


class SlimScrollbar(tk.Canvas):
    """Flat minimal scrollbar (the native tk.Scrollbar can't be themed on
    Windows). Speaks the standard set()/command scrollbar protocol."""

    def __init__(self, parent, command=None, width=10,
                 bg=C_PANEL, thumb="#3a2f55", thumb_hover=C_GOLD, **kw):
        super().__init__(parent, width=width, bg=bg,
                         highlightthickness=0, bd=0, **kw)
        self.command      = command
        self._thumb       = thumb
        self._thumb_hover = thumb_hover
        self._first       = 0.0
        self._last        = 1.0
        self._drag_off    = None
        self._hover       = False
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>",  self._click)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Enter>",     lambda e: self._set_hover(True))
        self.bind("<Leave>",     lambda e: self._set_hover(False))

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._redraw()

    def _set_hover(self, on):
        self._hover = on
        self._redraw()

    def _redraw(self):
        self.delete("all")
        if self._last - self._first >= 1.0:
            return
        h  = self.winfo_height()
        w  = self.winfo_width()
        y0 = int(self._first * h)
        y1 = max(int(self._last * h), y0 + 24)
        self.create_rectangle(
            2, y0, w - 2, y1,
            fill=self._thumb_hover if self._hover else self._thumb,
            outline="")

    def _click(self, e):
        h  = self.winfo_height() or 1
        y0 = self._first * h
        y1 = self._last * h
        self._drag_off = (e.y - y0) if y0 <= e.y <= y1 else (y1 - y0) / 2
        self._drag(e)

    def _drag(self, e):
        h     = self.winfo_height() or 1
        span  = self._last - self._first
        first = max(0.0, min((e.y - (self._drag_off or 0)) / h, 1.0 - span))
        if self.command:
            self.command("moveto", first)

class EqUpdaterApp(ModsRows, AddonsOps, ModsOps, tk.Tk):
    def __init__(self):
        # Give EqUpdater its own Windows taskbar identity before Tk creates
        # the native window. This prevents Windows from grouping it under an
        # older Octo/EqUpdater shortcut and reusing that cached taskbar icon.
        # Fonts first: on Linux fontconfig reads its configuration when the
        # first Tk window opens, which is the next line but one.
        prepare_fonts()
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    branding.WINDOWS_APP_ID)
            except Exception:
                pass
        super().__init__()
        # Tk ``after`` callbacks are Tcl commands.  If the root is destroyed
        # while one is still queued, Tcl later tries to invoke a command that
        # no longer exists and writes ``invalid command name ...`` to stderr.
        # Keep an explicit lifecycle flag and cancel pending callbacks in
        # destroy(); this matters both for clean shutdown and for GUI smoke
        # tests that construct/destroy several roots in one Python process.
        self._destroying = False

        # ── DPI scaling ────────────────────────────────────────────────────
        # With awareness declared (see _enable_dpi_awareness) Windows reports
        # the real DPI. Derive one scale factor from it and drive everything
        # off it: fonts via Tk's own point→pixel scaling, and every hard-coded
        # pixel value via self._px(). At 100% the factor is 1.0 and _px() is an
        # exact identity, so nothing changes; only high-DPI diverges.
        self._sc = max(1.0, self.winfo_fpixels("1i") / 96.0)
        # tk scaling is pixels-per-point; DPI/72 == _sc * 96/72. Setting it
        # explicitly keeps point-fonts growing by exactly _sc regardless of
        # what Tk defaulted to.
        self.tk.call("tk", "scaling", self._sc * 96.0 / 72.0)
        # Bake the factor into the shared design constants once, up front, so
        # the ~40 WIN_W/WIN_H/FOOT_H references downstream need no changes.
        global WIN_W, WIN_H, FOOT_H
        WIN_W  = self._px(WIN_W)
        WIN_H  = self._px(WIN_H)
        FOOT_H = self._px(FOOT_H)

        # Keep the window hidden until it's positioned and fully built, so it
        # never flashes at the default top-left corner before centering.
        self.withdraw()

        # Detect first run before anything writes the config.
        self._first_run  = not os.path.exists(CONFIG_FILE)

        # **The inheritance step**, run before anything reads a setting.
        # Finds Octo Updater's config if this machine has one, copies it
        # aside, carries the compatible settings across and stamps the new
        # schema so it never runs twice. A fresh machine gets an empty
        # stamped config and nothing else happens.
        #
        # Records come across as *provenance*: what the old updater recorded
        # as installed becomes managed-and-inherited, and anything it only
        # ever saw becomes unmanaged. An old record is evidence that updater
        # installed something; it is not evidence that the files on disk are
        # still what it installed, and the migration is careful not to
        # confuse the two. See equpdater.config.migrate_records.
        try:
            self._cfg = bootstrap_config(
                client_dir=(load_config().get("out_dir") or ""),
                addon_dir_for=lambda folder: os.path.join(
                    addons_path(load_config().get("out_dir") or ""), folder))
            if self._cfg.get("migrated_from"):
                log("")
                log("Imported settings from %s."
                    % self._cfg["migrated_from"]["product"])
                if self._cfg["migrated_from"].get("backup"):
                    log("  A copy of the old configuration is kept at %s."
                        % self._cfg["migrated_from"]["backup"])
        except Exception as e:
            # A failed migration must not stop the app starting. No stamp is
            # written, so the next launch tries again rather than leaving the
            # user half-migrated with no way back.
            log(f"Could not import previous settings: {e}", "err")
        # Set when the user adds a Defender exclusion via Settings; checked at
        # the next reconcile to skip the auto-prompt (so a manual add isn't
        # double-prompted), then reset — so each folder change offers one unless
        # the user just added it themselves.
        self._av_excluded = False
        self._cfg        = load_config()
        # First launch: the player picks a language before anything is drawn
        # in one, so the window opens in it -- no restart. "ask_language" in
        # the config asks once more on an existing install (support, testing)
        # and is cleared as soon as it has been asked.
        ask_again = bool(self._cfg.get("ask_language"))
        if ((self._first_run or ask_again)
                and not os.environ.get("EQUPDATER_NO_LANGUAGE_PROMPT")):
            self._ask_first_language()
        if ask_again:
            self._cfg = update_config(lambda c: c.pop("ask_language", None))
        # EqUpdater speaks the game's language (Tweaks -> Language), or
        # English where there is no translation. Fixed for this window's
        # life: everything below draws its text once.
        i18n.set_language(load_tweaks_config().get("locale", DEFAULT_LOCALE))

        # One font policy for the whole application. Friz Quadrata is the
        # default presentation, with Arial and OpenDyslexic available live
        # from Settings. ``dyslexic_font`` is still honoured for migrated
        # configs and maps to the explicit font selector.
        accessibility = self._cfg.get("accessibility", {})
        font_choice = accessibility.get("font_choice")
        if not font_choice:
            font_choice = "opendyslexic" if accessibility.get("dyslexic_font") else "arial"
        self._fonts = FontManager(self, font_choice)
        self._font_choice_var = tk.StringVar(value=self._fonts.choice)
        self._gradient_buttons = []
        # Pending reconcile mode (None = none): _offer_reconcile surfaces the
        # Update button and the Update the user clicks runs an integrity pass
        # instead of a routine size-based sync. "full" also writes a fresh
        # Config.wtf (folder change / first run); "keep-config" leaves Config.wtf.
        self._pending_reconcile = self._cfg.get("pending_reconcile") or None
        self._running    = False
        # True only after a verify/update confirmed the client files are up
        # to date. PLAY is gated on this AND on no mod being in an error state.
        self._client_ready = False
        self._worker: UpdateWorker | None = None
        self._log_q:  queue.Queue = queue.Queue()
        self._prog_q: queue.Queue = queue.Queue()
        # Session log lives in memory; the "Show Session Log" window renders it.
        self._log_buffer: list = []
        self._logwin = None
        self._logwin_text = None
        self._settings_overlay = None
        # Guards against triggering the default-mods / recommended-addons
        # auto-install more than once per app session (e.g. verify firing
        # twice in quick succession).
        self._default_mods_install_started = False
        self._default_addons_install_started = False

        # Game folder path — shared by the Settings modal. A change takes
        # effect only once Settings is closed (see _close_settings), so no
        # live trace fires mid-edit.
        # Settings copied from a Wine install can name the game folder as
        # Wine sees it (Z:\\home\\...); where that folder exists here, use it.
        native = platforms.native_path_for(self._cfg.get("out_dir") or "")
        if native:
            log(f"Game folder from Wine settings: {self._cfg['out_dir']} is "
                f"{native} here.", "acct")
            self._cfg = update_config(lambda c: c.__setitem__("out_dir", native))
        self._game_path = tk.StringVar(
            value=os.path.normpath(self._cfg.get("out_dir", DEFAULT_GAME_DIR)))

        # Count of mods with an update available — shown as a badge on the
        # MODS nav tab.
        self._mod_updates_count = 0

        # Addons state
        self._addons_status = {"state": "idle", "addons": {}, "available": []}
        self._addons_busy = False
        # True only while addons are actually downloading/installing (not
        # during a verify) — gates the PLAY button, like mods installs do.
        self._addons_installing = False
        self._addons_verified_ts = 0.0
        self._addon_updates_count = 0
        self._mpq_updates_count = 0
        self._mpq_busy = None
        # {folder: {"error": msg, "git": url}} for failed installs/updates —
        # survives the post-operation rescan so rows can show what went wrong.
        self._addon_errors = {}
        self._addon_sections_open = {"INSTALLED": True, "AVAILABLE": True}

        # Persistent News cache: render the last successful content immediately
        # and refresh in the background. A temporary forum outage should never
        # turn the front page into an empty error panel.
        news_cache = self._cfg.get("news_cache", {})
        feat_cache = news_cache.get("announcements", {})
        patch_cache = news_cache.get("changelog", {})
        self._featured = feat_cache.get("item")
        self._patch_items = patch_cache.get("items")
        # When each list was last read, as of this launch: a post after it
        # is NEW! all session long. The time is moved on (_news_seen) once
        # the list is read, for the next launch.
        seen = self._cfg.get("news_seen") or {}
        self._news_seen_before = {key: seen.get(section) for key, section in
                                  (("ann", "announcements"),
                                   ("patch", "changelog"))}

        # Scrollable list canvases that respond to the mouse wheel whenever
        # the pointer is anywhere over them (not just over the scrollbar).
        self._wheel_canvases: list = []
        for sequence in WHEEL_EVENTS:
            self.bind_all(sequence, self._on_mousewheel)

        self.title(branding.APP_TITLE)
        # Resizable. The smallest size is set once the header is drawn
        # (_update_minsize): the logo and tabs side by side above the
        # footer's buttons, with the content panels hidden when there is no
        # room for them. From here on WIN_W/WIN_H are the window's *current*
        # size: _apply_resize updates them.
        self._min_w, self._min_h = self._px(400), self._px(260)
        self._panels_hidden = False
        self.resizable(True, True)
        self.configure(bg=C_BG)
        self._apply_window_icon()

        # The size it had last time, if it still fits this screen; centred
        # either way, in a single geometry call.
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        saved = self._cfg.get("window") or {}
        try:
            w = int(saved.get("width") or 0)
            h = int(saved.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
        if self._min_w <= w <= sw and self._min_h <= h <= sh:
            WIN_W, WIN_H = w, h     # minsize() may still enlarge it a little
        x  = max(0, (sw - WIN_W) // 2)
        y  = max(0, (sh - WIN_H) // 2)
        self.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        self._resize_job = None
        self._live_job = None
        self._resizing = False
        self._stretch_pending = {}
        self.bind("<Configure>", self._on_window_configure, add="+")

        self._build()

        out_dir = self._cfg.get("out_dir", DEFAULT_GAME_DIR)
        if not os.path.exists(out_dir):
            def _wipe(c):
                c.pop("mods", None)
                c.pop("addons", None)
            self._cfg = update_config(_wipe)

        live_ver = get_client_version(out_dir)
        if live_ver:
            self._client_ver_var.set(live_ver)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()
        # Run any one-time migrations for a freshly-installed updater version
        # (and stamp app_version) before verifying. It runs
        # synchronously, so a v1.3 re-patch is settled before verify reads
        # WoW.exe, and the normal verify below still runs — an upgrading user
        # never misses a pending client update.
        self._new_version_installed()
        # On first run, defer verification until Settings is closed (see
        # _close_settings). If a reconcile was offered on a previous run but
        # never applied, restore the Update button instead of a routine verify.
        # Otherwise run the routine launch verify.
        if self._first_run:
            pass
        elif self._pending_reconcile:
            self.after(300, self._offer_reconcile)
        else:
            self.after(300, self._start_verify)
        self.after(600, self._load_news)
        # Check mod updates at launch too — but only once mods have actually
        # been used (mod_release_cache exists). On a first run or right after a
        # game-folder change nothing is installed yet, so there's nothing to
        # check; the MODS tab will do it when opened.
        # Attempts the last EqUpdater left open: no Tk in there, so a thread.
        threading.Thread(target=self._diag_recover, daemon=True).start()
        if self._cfg.get("mod_release_cache"):
            self.after(900, lambda: threading.Thread(
                target=self._load_mods_state,
                kwargs={"max_age": _MOD_VERSION_STARTUP_MAX_AGE},
                daemon=True).start())
        # Same parity for addons: background verify at launch (feeds the
        # ADDONS tab badge), but only once addons were initialized for this
        # folder — never on a first run / fresh folder.
        if self._cfg.get("addons") is not None:
            self.after(1500, self._addons_verify)
        # MPQ patches: check installed known patches for updates at launch so
        # the tab badge shows without opening the tab (no-op if none installed).
        if not self._first_run and self._game_path.get().strip():
            self.after(1700, self._mpq_check)
        # Daily self-update check (cached), last so it never delays the rest.
        self.after(2000, lambda: threading.Thread(
            target=self._check_updater_update, daemon=True).start())
        # First launch: open Settings so the user sets the game folder etc.
        if self._first_run:
            self.after(500, self._open_settings)
        else:
            # Installed or updated with a game folder already chosen: offer
            # the Defender exclusion once for it.
            self.after(1200, self._offer_av_exclusion)

        # Dark title bar on Windows 10; the app's own navy on Windows 11.
        # Set before the window is first shown: Windows 10 only paints the
        # whole bar dark if it is dark from the start.
        style_title_bar(self, caption=C_BG, text=C_TEXT)
        # Everything is positioned and built — reveal the centered window.
        self.deiconify()

    def _px(self, *vals):
        """Scale design pixels (authored at 96 DPI) to the current DPI. Returns
        an int for one arg, a tuple for several – handy for canvas coordinate
        lists. Exact identity when the scale factor is 1.0 (100% scaling)."""
        if len(vals) == 1:
            return round(vals[0] * self._sc)
        return tuple(round(v * self._sc) for v in vals)

    # ── build ─────────────────────────────────────────────────────────────────

    def _cancel_pending_after_callbacks(self):
        """Cancel every callback queued on this Tcl interpreter.

        ``after`` jobs outlive the Python method that created them.  Destroying
        the root without cancelling them produces noisy Tcl errors such as
        ``invalid command name \"..._poll\"`` on some Tk builds.  At final
        window teardown none of these jobs should survive, so cancelling the
        interpreter's complete queue is the safest cleanup.
        """
        try:
            jobs = self.tk.call("after", "info")
            if isinstance(jobs, str):
                jobs = self.tk.splitlist(jobs)
            for job in jobs:
                try:
                    self.after_cancel(job)
                except (tk.TclError, RuntimeError):
                    pass
        except (tk.TclError, RuntimeError):
            pass

    def after(self, ms, func=None, *args):
        """Tk's ``after``, except that once the window is being destroyed it
        schedules nothing.

        Background workers hand their results back with ``self.after(0, ...)``
        from their own threads. A worker that finishes after the window has
        closed -- a slow network call, say -- must not reach into a dead
        interpreter; its result simply has nowhere to go."""
        if getattr(self, "_destroying", False):
            return None
        return super().after(ms, func, *args)

    def destroy(self):
        """Destroy EqUpdater without leaving scheduled Tcl callbacks.

        Order matters: mark the window as closing (so workers' ``after``
        hand-backs are dropped), stop the background animation and wait for
        its worker, cancel every pending ``after`` job, and only then destroy
        the Tk widgets."""
        if getattr(self, "_destroying", False):
            return
        self._destroying = True
        self._stop_bg_animation(restore_static=False, wait=2.0)
        self._cancel_pending_after_callbacks()
        try:
            for sequence in WHEEL_EVENTS:
                self.unbind_all(sequence)
        except (tk.TclError, RuntimeError):
            pass
        try:
            super().destroy()
        except tk.TclError:
            # A second/late destroy during interpreter teardown is harmless.
            pass

    def _on_close(self):
        """Hide immediately, stop external work, then tear Tk down cleanly."""
        try:
            self.withdraw()
        except Exception:
            pass
        # A download's aria2c child isn't a daemon thread — kill it explicitly
        # so it can't keep syncing headless after the window is gone.
        stop_aria2c()
        self.destroy()

    def _add_tooltip(self, widget, text: str):
        """Attach a small hover tooltip to a widget."""
        state = {"win": None}

        def show(_e=None):
            if state["win"] is not None:
                return
            x = widget.winfo_rootx() + self._px(12)
            y = widget.winfo_rooty() + widget.winfo_height() + self._px(4)
            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            # Wrapped: a long description in one line ran off the window.
            tk.Label(tw, text=text, font=self._font(9),
                     fg=C_TEXT, bg="#0f0b16", justify="left",
                     wraplength=self._px(420),
                     highlightthickness=1, highlightbackground=C_PANEL_BDR,
                     padx=self._px(6), pady=self._px(2)).pack()
            state["win"] = tw

        def hide(_e=None):
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    #: The row action icons, drawn in a 20 x 18 box at 2 px strokes so they
    #: line up and weigh the same wherever they appear: (resting colour,
    #: colour under the cursor).
    ACTION_ICONS = {
        "link":     ("#8a96ad", "#c99a3d"),
        "release":  ("#8a96ad", "#eee9dc"),
        "remove":   ("#8a4a4a", "#ff5555"),
        "download": ("#58b85a", "#8fdf8e"),
    }

    def _action_icon(self, parent, kind: str, tip: str, command,
                     padx=None):
        """One row action, the same everywhere: ↗ opens a page, a grey ×
        stops managing (the files stay), the red bin uninstalls, the arrow
        into a tray downloads. Packed on the right of ``parent``."""
        rest, lit = self.ACTION_ICONS[kind]
        if kind == "remove":
            rest, lit = "#8a4a4a", C_ERR
        if kind == "download":
            rest = C_OK
        w = self._px(2)
        c = tk.Canvas(parent, width=self._px(20), height=self._px(18),
                      bg=parent.cget("bg"), highlightthickness=0,
                      cursor="hand2")
        c.pack(side="right", padx=padx if padx is not None
               else (self._px(4), self._px(2)))
        if kind == "link":
            c.create_line(*self._px(5, 14, 14, 5), fill=rest, width=w,
                          capstyle="round", tags="ink")
            c.create_line(*self._px(8, 5, 14, 5, 14, 11), fill=rest, width=w,
                          capstyle="round", joinstyle="round", tags="ink")
        elif kind == "release":
            c.create_line(*self._px(6, 5, 14, 13), fill=rest, width=w,
                          capstyle="round", tags="ink")
            c.create_line(*self._px(14, 5, 6, 13), fill=rest, width=w,
                          capstyle="round", tags="ink")
        elif kind == "remove":
            # The bin as it always was: handle, lid, tapered body, slats.
            c.create_rectangle(*self._px(8, 2, 12, 4), fill=rest, outline="",
                               tags="fill")
            c.create_rectangle(*self._px(4, 4, 16, 6), fill=rest, outline="",
                               tags="fill")
            c.create_polygon(*self._px(5, 8, 15, 8, 14, 16, 6, 16), fill=rest,
                             outline="", tags="fill")
            for x in (8, 10, 12):
                c.create_line(*self._px(x, 10, x, 14), fill=parent.cget("bg"))
        elif kind == "download":
            c.create_polygon(*self._px(8, 1, 12, 1, 12, 6, 15, 6, 10, 11,
                                       5, 6, 8, 6), fill=rest, outline="",
                             tags="fill")
            c.create_line(*self._px(3, 11, 3, 15, 17, 15, 17, 11), fill=rest,
                          width=w, capstyle="round", joinstyle="round",
                          tags="ink")

        def paint(colour):
            c.itemconfigure("ink", fill=colour)
            c.itemconfigure("fill", fill=colour)
        c.bind("<Button-1>", lambda _e: command(), add="+")
        c.bind("<Enter>", lambda _e: paint(lit), add="+")
        c.bind("<Leave>", lambda _e: paint(rest), add="+")
        c._action_kind = kind
        self._set_tooltip(c, tip)
        return c

    def _set_tooltip(self, widget, text: str):
        """A tooltip whose text can change without stacking another handler.

        `_add_tooltip` binds with add="+", which is right for a label built
        once and never touched again. Rows are different: they are restyled
        on every refresh, and calling _add_tooltip each time leaves one live
        handler per refresh, each showing the text that was current when it
        was bound. Here the text lives on the widget and the binding happens
        once."""
        widget._tip_text = text
        if getattr(widget, "_tip_bound", False):
            return
        widget._tip_bound = True
        self._add_tooltip_dynamic(widget)

    def _add_tooltip_dynamic(self, widget):
        state = {"win": None}

        def show(_e=None):
            text = getattr(widget, "_tip_text", "")
            if state["win"] is not None or not text:
                return
            x = widget.winfo_rootx() + self._px(12)
            y = widget.winfo_rooty() + widget.winfo_height() + self._px(4)
            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, font=self._font(9),
                     fg=C_TEXT, bg="#0f0b16", justify="left",
                     wraplength=self._px(320),
                     highlightthickness=1, highlightbackground=C_PANEL_BDR,
                     padx=self._px(6), pady=self._px(2)).pack()
            state["win"] = tw

        def hide(_e=None):
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _on_mousewheel(self, event):
        # Panels are stacked, so several list canvases share the same screen
        # region — only the one inside the active tab's panel should scroll.
        active = getattr(self, "_active_panel", None)
        active_prefix = (str(active) + ".") if active is not None else None
        for cv in list(self._wheel_canvases):
            try:
                if not cv.winfo_ismapped():
                    continue
                if active_prefix and not str(cv).startswith(active_prefix):
                    continue
                wx, wy = cv.winfo_rootx(), cv.winfo_rooty()
                if (wx <= event.x_root <= wx + cv.winfo_width()
                        and wy <= event.y_root <= wy + cv.winfo_height()):
                    # When the content fits entirely in view, Tk would still
                    # happily shift it around — ignore the wheel instead.
                    first, last = cv.yview()
                    steps = wheel_steps(event)
                    if last - first < 1.0 and steps:
                        cv.yview_scroll(steps, "units")
                    return
            except tk.TclError:
                self._wheel_canvases.remove(cv)

    def _draw_nav_tab(self, tab: str, hover: bool = False):
        """Render a nav tab on the header canvas; the active tab's text gets
        a soft glow (dim gold halo layers behind the bright text)."""
        cv = self._hdr_canvas
        tag = f"nav_{tab}"
        cv.delete(tag)
        cx, cy = self._nav_pos[tab], self._px(54)
        font = self._font(11, bold=True)
        label = tr(tab)
        if tab == self._active_tab:
            for r, col in ((self._px(2), "#42340f"), (self._px(1), "#7a5c1d")):
                for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r),
                               (-r, -r), (r, -r), (-r, r), (r, r)):
                    cv.create_text(cx + dx, cy + dy, text=label,
                                   font=font, fill=col, tags=tag)
            cv.create_text(cx, cy, text=label, font=font, fill=C_GOLD_LT,
                           tags=tag)
        else:
            cv.create_text(cx, cy, text=label, font=font,
                           fill=C_GOLD_LT if hover else C_TEXT, tags=tag)

        # Update-counter badges
        count = 0
        if tab == "MODS":
            count = self._mod_updates_count
        elif tab == "ADDONS":
            count = self._addon_updates_count
        elif tab == "MPQ":
            count = self._mpq_updates_count
        if count:
            bx = cx + self._nav_text_w[tab] // 2 + self._px(11)
            by = cy - self._px(11)
            # Canvas oval, not a ● glyph: the oval gives exact geometric
            # control so the number always centers, consistently across OSes.
            # (A filled-circle glyph antialiases nicely but its disk isn't
            # centered in the glyph box — by a font-specific amount — so the
            # number drifts off-centre and can't be corrected reliably.)
            r8 = self._px(8)
            cv.create_oval(bx - r8, by - r8, bx + r8, by + r8,
                           fill=C_GOLD, outline="", tags=tag)
            cv.create_text(bx, by, text=str(count),
                           font=self._font(8, bold=True),
                           fill="#1a1408", tags=tag)

    def _switch_tab(self, tab: str):
        if tab == self._active_tab:
            return
        prev = self._active_tab
        self._active_tab = tab
        self._draw_nav_tab(prev)
        self._draw_nav_tab(tab)

        # Panels stay mapped and stacked; switching tabs only raises the
        # active one. place_forget()/place() would unmap and remap the whole
        # widget tree of a populated panel (hundreds of widgets) every
        # switch — a visible synchronous stall.
        panels = {"NEWS":   self._news_panel,
                  "TWEAKS": self._tweaks_panel_frame,
                  "ADDONS": self._addons_panel_frame,
                  "MODS":   self._mods_panel_frame,
                  "MPQ":    self._mpq_panel_frame}
        target = panels.get(tab, self._news_panel)
        # The panel left behind keeps the size it has now, fixed: a hidden
        # panel that followed the window would be laid out again at every
        # step of a resize (the Addons list alone is hundreds of widgets).
        old = self._active_panel
        if old is not target and old.winfo_ismapped():
            old.place_configure(relwidth=0, relheight=0,
                                width=old.winfo_width(),
                                height=old.winfo_height())
        if not self._panels_hidden:
            self._place_panel(target)     # follows the window again
        target.tkraise()
        self._active_panel = target

        if tab == "MODS":
            threading.Thread(target=self._load_mods_state, daemon=True).start()
        elif tab == "TWEAKS":
            self._refresh_tweaks_panel()
        elif tab == "ADDONS":
            self._addons_verify()
        elif tab == "MPQ":
            self._mpq_check()
        # NEWS: nothing. The forum is read at launch and on refresh only.

    def _font(self, size: int, *, bold=False, italic=False, underline=False):
        return self._fonts.spec(size, bold=bold, italic=italic, underline=underline)

    def _apply_app_fonts(self):
        """Apply the selected family immediately to the live widget tree."""
        self._fonts.apply_tree(self)
        self._layout_footer()
        if hasattr(self, "_hdr_canvas"):
            self._draw_logo()
            if getattr(self, "_nav_pos", None):
                self._layout_nav()
            self._draw_update_label()
            self._draw_gear()
        for button in getattr(self, "_gradient_buttons", []):
            try:
                button.refresh()
            except tk.TclError:
                pass

    def _background_path(self):
        for path in branding.background_candidates():
            if os.path.exists(path):
                return path
        return None

    def _animated_background_path(self):
        for path in branding.animated_background_candidates():
            if os.path.exists(path):
                return path
        return None

    def _build(self):
        self._bg_canvas = tk.Canvas(self, width=WIN_W, height=WIN_H,
                                    bg=C_BG, highlightthickness=0)
        self._bg_canvas.place(x=0, y=0)
        self._draw_bg()

        self._build_header()
        self._build_panel()
        self._build_footer()
        self._apply_app_fonts()

    def _draw_bg(self):
        """The static background, always drawn first: it is what shows while
        the animation's first frame is being made, when animation is off,
        and if the animation cannot be played."""
        c = self._bg_canvas
        # Only its own items: the header and footer are drawn on this canvas
        # too, and a resize redraws the background under them.
        c.delete("bg")
        self._bg_item = None
        # The top eases into the window edge behind the header, and the
        # bottom fades to the footer's dark so the controls sit on a soft
        # dark gradient rather than a solid block with a hard top edge.
        self._bg_fades = edge_fade_layers(
            WIN_W, WIN_H, C_BG,
            top=(self._px(120), 0.45),
            bottom=(FOOT_H + self._px(70), 0.9))
        self._bg_pil = cover_background(
            self._background_path(), WIN_W, WIN_H, darken=0.93)
        if self._bg_pil is not None:
            apply_edge_fades(self._bg_pil, self._bg_fades)
        self._bg_photo = photo_image(self._bg_pil, master=self)
        self._bg_current = self._bg_photo
        if self._bg_photo is not None:
            self._bg_item = c.create_image(0, 0, image=self._bg_photo,
                                           anchor="nw", tags=("bg", "bg_image"))
        else:
            c.create_rectangle(0, 0, WIN_W, WIN_H, fill=C_BG, outline="",
                               tags="bg")
        # A tiny dark veil keeps the bright bubble highlights from fighting
        # with edge text while leaving the supplied artwork clearly visible.
        self._bg_frame = c.create_rectangle(0, 0, WIN_W - 1, WIN_H - 1, fill="",
                                            outline=C_PANEL_BDR, tags="bg")
        c.tag_lower("bg")
        self._bg_size = (WIN_W, WIN_H)
        if self._cfg.get("animated_background", True) \
                and not getattr(self, "_resizing", False):
            self._start_bg_animation()

    def _start_bg_animation(self):
        """Play the looping GIF over the static background. Any failure --
        file missing, unreadable, not animated -- leaves the static image in
        place and says why in the session log."""
        self._stop_bg_animation()
        path = self._animated_background_path()
        if not path:
            log("Animated background unavailable: %s not found; using the "
                "static background." % branding.BACKGROUND_ANIMATED, "dim")
            return
        if self._bg_item is None:
            self._bg_item = self._bg_canvas.create_image(
                0, 0, anchor="nw", tags=("bg", "bg_image"))
            self._bg_canvas.tag_lower("bg")
        self._bg_anim = AnimatedBackground(
            self, self._bg_canvas, self._bg_item, path, WIN_W, WIN_H,
            darken=0.93, fades=self._bg_fades,
            on_fail=self._bg_animation_failed,
            on_image=self._show_bg_image)
        self._bg_anim.start()

    def _stop_bg_animation(self, restore_static: bool = True,
                           wait: float = 0.0):
        anim = getattr(self, "_bg_anim", None)
        self._bg_anim = None
        if anim is not None:
            anim.stop(wait=wait)
        if restore_static and getattr(self, "_bg_item", None) is not None:
            try:
                self._bg_canvas.itemconfigure(
                    self._bg_item, image=self._bg_photo or "")
            except tk.TclError:
                pass
            self._show_bg_image(self._bg_photo)

    def _show_bg_image(self, photo):
        """Point every see-through panel at the background image now on
        screen -- the still, or the animation's PhotoImage, whose frames
        then appear in those panels too."""
        self._bg_current = photo
        for backdrop in getattr(self, "_bg_backdrops", ()):
            try:
                backdrop.set_image(photo)
            except tk.TclError:
                pass

    def _bg_animation_failed(self, why: str):
        log("Animated background could not be played (%s); using the static "
            "background." % why, "dim")
        self._stop_bg_animation()

    def _toggle_animated_background(self):
        val = bool(self._animated_bg_var.get())
        self._cfg = update_config(
            lambda c: c.__setitem__("animated_background", val))
        if val:
            self._start_bg_animation()
        else:
            self._stop_bg_animation()

    def _ask_first_language(self) -> None:
        """The first-launch language picker: one button per language, each
        in its own words, since the player may read only one of them. The
        choice is the Language tweak -- EqUpdater's text and, at the next
        patch, the game's. Closing the window keeps what is saved."""
        current = load_tweaks_config().get("locale", DEFAULT_LOCALE)
        self._apply_window_icon()
        win = tk.Toplevel(self, bg=C_PANEL)
        win.title(branding.APP_TITLE)
        win.resizable(False, False)
        style_title_bar(win, caption=C_BG, text=C_TEXT)
        chosen = {}

        body = tk.Frame(win, bg=C_PANEL)
        body.pack(padx=self._px(28), pady=(self._px(20), self._px(24)))
        tk.Label(body, text="Language · Sprache · Язык · 语言 · Idioma",
                 font=("Arial", 12, "bold"), fg=C_GOLD,
                 bg=C_PANEL).pack(pady=(0, self._px(14)))

        def pick(code):
            chosen["code"] = code
            win.destroy()

        for code, (_i, label) in LOCALES.items():
            base = C_GOLD if code == current else C_PANEL_BDR
            fg = "#000" if code == current else C_TEXT
            btn = tk.Label(body, text=label, font=("Arial", 11),
                           fg=fg, bg=base, cursor="hand2",
                           pady=self._px(6))
            btn.pack(fill="x", pady=(0, self._px(6)))
            btn.language = code
            btn.bind("<Button-1>", lambda e, c=code: pick(c))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=C_GOLD_LT, fg="#000"))
            btn.bind("<Leave>", lambda e, b=btn, bg=base, f=fg: b.configure(bg=bg, fg=f))

        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        win.geometry("+%d+%d" % ((win.winfo_screenwidth() - w) // 2,
                                 (win.winfo_screenheight() - h) // 2))
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.lift()
        win.focus_force()
        win.grab_set()
        self._language_prompt = win
        self.wait_window(win)
        self._language_prompt = None

        code = chosen.get("code")
        if code and code != current:
            tweaks = load_tweaks_config()
            tweaks["locale"] = code
            save_tweaks_config(tweaks)

    def _apply_window_icon(self):
        """Put EqUpdater's icon on the title bar/taskbar cross-platform.

        Prefer the canonical PNG through Tk's ``iconphoto`` (works on Windows
        and Linux and preserves alpha). Fall back to the generated ICO / the
        frozen executable resource for Windows builds. Keep the PhotoImage on
        ``self`` so Tk does not garbage-collect it after this method returns.
        """
        for path in branding.icon_candidates():
            try:
                if not os.path.exists(path):
                    continue
                if path.lower().endswith(".png"):
                    photo = tk.PhotoImage(file=path)
                    self.iconphoto(True, photo)
                    self._window_icon_photo = photo
                    return
                self.iconbitmap(default=path)
                return
            except Exception:
                continue

    def _build_header(self):
        HDR_H = self._px(108)

        # The header is drawn straight onto the background canvas. It used to
        # be its own canvas holding a still crop of the background, which
        # left a hard line at its bottom edge once the background moved.
        hdr = self._bg_canvas
        self._hdr_canvas = hdr

        self._logo_y = HDR_H // 2 - self._px(6)
        self._draw_logo()

        self._active_tab  = "NEWS"
        self._nav_pos     = {}
        self._nav_text_w  = {}
        self._hdr_regions = {}
        self._hdr_h       = HDR_H
        self._layout_nav()

        self._hdr_regions["gear"] = (WIN_W - self._px(36), self._px(2),
                                     WIN_W - self._px(2), self._px(34))
        self._draw_gear()

        # The wordmark is a clickable header element too (opens the repo).
        lb = hdr.bbox("logo")
        tb = hdr.bbox("logo_text") or lb
        if lb:
            self._hdr_regions["logo"] = (lb[0] - self._px(4), lb[1] - self._px(4),
                                         lb[2] + self._px(6), lb[3] + self._px(4))
            self._logo_cx = (tb[0] + tb[2]) // 2
        else:
            self._logo_cx = self._px(127)
        # "Update available!" label under the wordmark, shown once the daily
        # self-update check finds a newer release.
        self._update_available = False
        self._draw_update_label()

        self._hdr_hover = None
        hdr.bind("<Button-1>", self._on_hdr_click)
        hdr.bind("<Motion>",   self._on_hdr_motion)
        hdr.bind("<Leave>",    lambda e: self._on_hdr_motion(None))

        self._clear_wdb_var = tk.BooleanVar(
            value=bool(self._cfg.get("clear_wdb_on_launch", False)))
        self._close_on_launch_var = tk.BooleanVar(
            value=bool(self._cfg.get("close_on_launch", False)))
        self._auto_mods_var = tk.BooleanVar(
            value=bool(self._cfg.get("auto_install_mods", False)))
        # Off by default. Recommended addons are a taste call, not a fix, and
        # silently dropping a dozen of them into a hand-curated AddOns folder
        # on first run is not something to do without being asked. The ADDONS
        # tab still lists every one of them for one-click install.
        self._auto_addons_var = tk.BooleanVar(
            value=bool(self._cfg.get("auto_install_addons", False)))
        self._ignore_speech_var = tk.BooleanVar(
            value=bool(self._cfg.get("ignore_speech", False)))
        self._animated_bg_var = tk.BooleanVar(
            value=bool(self._cfg.get("animated_background", True)))
        # Deferred "install missing" pending from turning an auto-install
        # option on in Settings — applied on close (see _close_settings).
        self._auto_mods_retrigger = False
        self._auto_addons_retrigger = False

    def _logo_icon(self):
        """The app icon at wordmark height, made once. None if unavailable."""
        if not hasattr(self, "_logo_icon_photo"):
            self._logo_icon_photo = None
            path = next((p for p in branding.icon_candidates()
                         if p.lower().endswith(".png") and os.path.exists(p)),
                        None)
            try:
                from PIL import Image, ImageTk
                with Image.open(path) as im:
                    im = im.convert("RGBA")
                    h = self._px(40)
                    w = max(1, round(im.width * h / im.height))
                    self._logo_icon_photo = ImageTk.PhotoImage(
                        im.resize((w, h), Image.Resampling.LANCZOS),
                        master=self)
            except Exception:
                pass
        return self._logo_icon_photo

    def _draw_logo(self, hover: bool = False):
        cv = self._hdr_canvas
        cv.delete("logo")
        x = self._px(24)
        icon = self._logo_icon()
        if icon is not None:
            cv.create_image(x, self._logo_y, image=icon, anchor="w",
                            tags="logo")
            x += icon.width() + self._px(10)
        cv.create_text(x, self._logo_y, text=branding.APP_TITLE,
                       font=self._font(24, bold=True),
                       fill=C_GOLD_LT if hover else C_TEXT,
                       anchor="w", tags=("logo", "logo_text"))

    def _layout_nav(self):
        """Place the tabs from the right: the last tab's text ends exactly
        on the right edge of the panels below (the Changelog border).
        Re-run on font changes, since every face measures differently."""
        import tkinter.font as tkfont
        tabs = [N_("NEWS"), N_("TWEAKS"), N_("ADDONS"), N_("MODS"), N_("MPQ")]
        font = tkfont.Font(font=self._font(11, bold=True))
        pad = self._px(18)                       # clickable margin each side
        widths = [font.measure(tr(t)) for t in tabs]
        right = WIN_W - self._px(40)             # panels' right edge
        x = right + pad - sum(w + 2 * pad for w in widths)
        for tab, text_w in zip(tabs, widths):
            w = text_w + 2 * pad
            self._nav_pos[tab] = x + w // 2
            self._nav_text_w[tab] = text_w
            # Keep the last tab's hit box clear of the gear in the corner.
            self._hdr_regions[tab] = (x, 0, min(x + w, right + self._px(4)),
                                      self._hdr_h)
            x += w
            self._draw_nav_tab(tab)
        self._update_minsize()

    def _draw_update_label(self, hover: bool = False):
        cv = self._hdr_canvas
        cv.delete("upd_label")
        self._hdr_regions.pop("update", None)
        if not self._update_available:
            return
        cv.create_text(self._logo_cx, self._logo_y + self._px(26),
                       text=tr("Update available!"),
                       font=self._font(10, bold=True),
                       fill=C_GOLD_LT if hover else C_GOLD,
                       anchor="n", tags="upd_label")
        lb = cv.bbox("upd_label")
        if lb:
            self._hdr_regions["update"] = (lb[0] - self._px(4), lb[1] - self._px(2),
                                           lb[2] + self._px(4), lb[3] + self._px(2))

    def _draw_gear(self, hover: bool = False):
        cv = self._hdr_canvas
        cv.delete("gear_icon")
        cv.create_text(WIN_W - self._px(10), self._px(8), text="⚙",
                       font=self._font(13),
                       fill=C_GOLD if hover else C_TEXT_DIM,
                       anchor="ne", tags="gear_icon")

    def _hdr_hit(self, x, y):
        for name, (x0, y0, x1, y1) in self._hdr_regions.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _on_hdr_motion(self, event):
        name = self._hdr_hit(event.x, event.y) if event is not None else None
        if name == self._hdr_hover:
            return
        prev = self._hdr_hover
        self._hdr_hover = name
        if prev in self._nav_pos:
            self._draw_nav_tab(prev)
        if name in self._nav_pos:
            self._draw_nav_tab(name, hover=True)
        if "gear" in (prev, name):
            self._draw_gear(hover=(name == "gear"))
        if "logo" in (prev, name):
            self._draw_logo(hover=(name == "logo"))
        if "update" in (prev, name):
            self._draw_update_label(hover=(name == "update"))
        self._hdr_canvas.configure(cursor="hand2" if name else "")

    def _on_hdr_click(self, event):
        name = self._hdr_hit(event.x, event.y)
        if name == "gear":
            self._open_settings(event)
        elif name in ("logo", "update"):
            self._open_url(branding.PROJECT_URL)
        elif name in self._nav_pos:
            self._switch_tab(name)

    def _check_updater_update(self):
        """Background daily check of the updater's own GitHub releases; shows
        the 'Update available!' header label if a newer version exists."""
        try:
            tag = fetch_updater_latest_tag()
        except Exception:
            tag = None
        if updater_update_available(tag):
            def show():
                self._update_available = True
                self._draw_update_label()
            self.after(0, show)

    def _build_panel(self):
        # A backdrop, not a plain frame: the gap between the two news panels
        # shows the (animated) window background instead of a flat colour.
        panel = BackdropCanvas(self, bg=C_BG)
        self._bg_backdrops = [panel]
        self._place_panel(panel)
        panel.set_image(getattr(self, "_bg_current", None))
        self._news_panel = panel
        self._active_panel = panel

        # A small gap between the two panels; the backdrop shows the
        # bubbles through it.
        self._news_gap = self._px(10)
        self._news_column_widths()

        feat = tk.Frame(panel, bg=C_PANEL,
                        highlightthickness=1,
                        highlightbackground=C_PANEL_BDR)
        self._feat_frame = feat
        changelog = tk.Frame(panel, bg=C_PANEL,
                             highlightthickness=1,
                             highlightbackground=C_PANEL_BDR)
        self._patch_frame = changelog
        self._place_news_columns()

        self._render_featured(self._featured, loading=self._featured is None)
        self._render_patch_notes(self._patch_items,
                                 loading=self._patch_items is None)

        self._log_line(f"{branding.APP_TITLE}  v{UPDATER_VERSION}\n", "acct")
        self._log_line("─" * 60 + "\n", "dim")
        self._build_mods_panel()
        self._build_tweaks_panel()
        self._build_addons_panel()
        self._build_mpq_panel()

    def _place_panel(self, panel):
        """A tab's panel between the header and the footer, relative to the
        window so it follows every resize."""
        top = self._px(119)
        panel.place(x=self._px(40), y=top, relwidth=1.0, width=-self._px(80),
                    relheight=1.0, height=-(top + FOOT_H + self._px(10)))

    def _all_panels(self) -> list:
        return [p for p in (getattr(self, n, None) for n in (
            "_news_panel", "_tweaks_panel_frame", "_addons_panel_frame",
            "_mods_panel_frame", "_mpq_panel_frame")) if p is not None]

    def _update_panel_visibility(self):
        """Too little height between the header and the footer: hide the
        panels rather than squeeze them; put the active one back when there
        is room again (the others come back as their tabs are opened)."""
        room = WIN_H - (self._px(119) + FOOT_H + self._px(10))
        hide = room < self._px(120)
        if hide == self._panels_hidden:
            return
        self._panels_hidden = hide
        if hide:
            for panel in self._all_panels():
                panel.place_forget()
        else:
            self._place_panel(self._active_panel)
            self._active_panel.tkraise()

    def _update_minsize(self):
        """The smallest window: the logo and the tabs side by side (they are
        laid out from the right edge, so their width is fixed), and the
        footer's buttons with a short progress bar; as tall as the header
        and footer alone. Again whenever the font or language changes."""
        c = getattr(self, "_bg_canvas", None)
        tabs = [r for k, r in getattr(self, "_hdr_regions", {}).items()
                if k in getattr(self, "_nav_pos", {})]
        if c is None or not tabs:
            return
        logo = c.bbox("logo")
        logo_right = logo[2] if logo else self._px(260)
        tabs_w = WIN_W - min(r[0] for r in tabs)
        self._min_w = max(logo_right + self._px(24) + tabs_w,
                          self._px(400) + self._px(120) + self._px(40))
        self._min_h = self._px(119) + FOOT_H + self._px(10)
        self.minsize(self._min_w, self._min_h)

    def _news_column_widths(self):
        """The two News columns' widths for the window's current width:
        Announcements 60 %, the Changelog the rest, a gap between."""
        inner_w = WIN_W - self._px(40) * 2
        self._news_left_w = int(inner_w * 0.60)
        self._news_right_w = inner_w - self._news_left_w - self._news_gap

    def _place_news_columns(self):
        self._feat_frame.place(x=0, y=0, width=self._news_left_w,
                               relheight=1.0)
        self._patch_frame.place(x=self._news_left_w + self._news_gap, y=0,
                                width=self._news_right_w, relheight=1.0)

    # ── window size ──────────────────────────────────────────────────────────

    #: While a drag goes on, the live layout runs at most this often (ms).
    RESIZE_LIVE_MS = 16
    #: The size has settled when no resize came for this long (ms).
    RESIZE_SETTLE_MS = 180

    def _on_window_configure(self, event):
        """The window was moved or resized. A resize is laid out in two
        parts: what is cheap follows the drag live (_live_resize: the
        header, footer, panels and News text, a few milliseconds), and what
        is costly waits until the size settles (_apply_resize: the
        background image and its animation). A move changes nothing."""
        if event.widget is not self:
            return
        if (event.width, event.height) == (WIN_W, WIN_H) \
                and self._resize_job is None:
            return
        if self._resize_job is None:
            self._begin_resize()
        else:
            try:
                self.after_cancel(self._resize_job)
            except tk.TclError:
                pass
        self._resize_job = self.after(self.RESIZE_SETTLE_MS,
                                      self._apply_resize)
        if getattr(self, "_live_job", None) is None:
            self._live_job = self.after(self.RESIZE_LIVE_MS, self._live_resize)

    def _begin_resize(self):
        """A drag starts: the animation pauses (its frames are made for one
        size, and making them competes with the drag); the still image stays
        on screen until the size settles."""
        self._resizing = True
        if not hasattr(self, "_stretch_pending"):
            self._stretch_pending = {}
        if getattr(self, "_bg_anim", None) is not None:
            self._stop_bg_animation()
        # The picture is not drawn while the window is dragged: repainting a
        # window-sized image (twice, with the News backdrop) at every step
        # was most of a step's cost. The plain background colour stands in
        # until the size settles and the picture is drawn for it.
        self._bg_canvas.itemconfigure("bg_image", state="hidden")
        for backdrop in getattr(self, "_bg_backdrops", ()):
            try:
                backdrop.set_image(None)
            except tk.TclError:
                pass

    def _live_resize(self):
        """The cheap part of a resize, run while the drag goes on: the
        window size, the header's tabs and gear, the footer, the News
        columns and their wrapping. The panels follow on their own (they are
        placed relative to the window). No image work, no widget rebuilt."""
        global WIN_W, WIN_H
        self._live_job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        w, h = max(w, self._min_w), max(h, self._min_h)
        if (w, h) == (WIN_W, WIN_H):
            return
        WIN_W, WIN_H = w, h
        self._update_panel_visibility()
        self._bg_canvas.configure(width=WIN_W, height=WIN_H)
        self._bg_canvas.coords(self._bg_frame, 0, 0, WIN_W - 1, WIN_H - 1)
        self._layout_nav()
        self._hdr_regions["gear"] = (WIN_W - self._px(36), self._px(2),
                                     WIN_W - self._px(2), self._px(34))
        self._draw_gear()
        self._pb_width = max(self._px(60), WIN_W - self._px(400) - self._px(40))
        self._layout_footer()
        self._news_column_widths()
        self._place_news_columns()

    def _apply_resize(self):
        """The size has settled: finish the layout, then the costly part --
        the background drawn for this size (its animation restarted) -- and
        remember the size for the next start."""
        if getattr(self, "_live_job", None) is not None:
            try:
                self.after_cancel(self._live_job)
            except tk.TclError:
                pass
            self._live_job = None
        self._resize_job = None
        self._resizing = False
        self._live_resize()
        self._stretch_lists()
        self._rewrap_news()
        if getattr(self, "_bg_size", None) != (WIN_W, WIN_H):
            self._draw_bg()
        else:
            self._bg_canvas.itemconfigure("bg_image", state="normal")
            if self._cfg.get("animated_background", True) \
                    and getattr(self, "_bg_anim", None) is None:
                self._start_bg_animation()
        self._show_bg_image(self._bg_current)

        size = {"width": WIN_W, "height": WIN_H}
        if (self._cfg.get("window") or {}) != size:
            self._cfg = update_config(lambda c: c.__setitem__("window", size))

    def _list_stretch(self, canvas, item):
        """A scrolling list's <Configure> handler: its rows are as wide as
        the list. While the window is being dragged the rows keep their width
        -- stretching hundreds of rows at every step of a drag is what made
        it lag -- and they reflow once when the size settles (_apply_resize).
        ``item`` returns the canvas window holding the rows, or None."""
        def on_configure(event):
            window = item()
            if window is None:
                return
            if getattr(self, "_resizing", False):
                self._stretch_pending[canvas] = item
                return
            canvas.itemconfigure(window, width=event.width)
        return on_configure

    def _stretch_lists(self):
        """After a drag: every list that waited takes its final width."""
        pending, self._stretch_pending = self._stretch_pending, {}
        for canvas, item in pending.items():
            try:
                window = item()
                if window is not None and canvas.winfo_exists():
                    canvas.itemconfigure(window, width=canvas.winfo_width())
            except tk.TclError:
                pass

    def _rewrap_news(self):
        """The News text wrapped to the columns' current widths, in place:
        cheaper than drawing the lists again, and the scroll position stays.
        (A post's text box wraps by itself and fits its own height.)"""
        widths = {"ann": self._news_wrap("ann"), "patch": self._news_wrap("patch")}
        for key, width in widths.items():
            for label in getattr(self, "_acc_wraps", {}).get(key, ()):
                try:
                    cut = getattr(label, "_wrap_cut", 0)
                    label.configure(wraplength=max(self._px(80), width - cut)
                                    if cut else width)
                except tk.TclError:
                    pass

    def _news_wrap(self, key) -> int:
        if key == "ann":
            return max(self._px(200), self._news_left_w - self._px(70))
        return max(self._px(160), self._news_right_w - self._px(60))

    # ── news panel ───────────────────────────────────────────────────────────

    def _save_news_cache(self, section: str, payload_key: str, payload):
        stamp = time.time()
        def mutate(c):
            entry = c.setdefault("news_cache", {}).setdefault(section, {})
            entry["timestamp"] = stamp
            entry[payload_key] = payload
        self._cfg = update_config(mutate)
        return stamp

    def _news_seen(self, section: str):
        """Remember that the player saw ``section``'s list now: what is
        posted later is NEW! at the next launch."""
        stamp = time.time()
        self._cfg = update_config(
            lambda c: c.setdefault("news_seen", {}).__setitem__(section, stamp))

    def _news_marks(self, key: str, entries: list) -> dict:
        seen = getattr(self, "_news_seen_before", {}).get(key)
        try:
            seen = float(seen) if seen is not None else None
        except (TypeError, ValueError):
            seen = None
        return news_marks(entries, seen, time.time())

    def _load_news(self):
        """The launch fetch: one request cycle per panel."""
        self._load_featured()
        self._load_patch_notes()

    def _load_featured(self):
        """Read the Announcements once, in the background: forum 2's newest
        topic, else news.json (fetch_featured_post); if both fail, the last
        good one stays on screen. Then the list of every announcement is
        read (forum 2's first page), unless the forum could not be. A click
        on refresh while a read is already running does not start a second;
        a refresh starts the list over, newest open."""
        if getattr(self, "_feat_loading", False):
            return
        self._feat_loading = True
        self._ann_reset()
        self._render_featured(self._featured, loading=True)

        def worker():
            feat, err = None, ""
            try:
                feat = fetch_featured_post()
            except Exception as exc:
                err = _news_error(N_("Announcements"), exc)

            def apply():
                self._feat_loading = False
                if feat is not None:
                    # Set only when the forum failed and news.json stood in.
                    note = feat.pop("_note", "")
                    self._featured = feat
                    self._save_news_cache("announcements", "item", feat)
                    self._news_seen("announcements")
                    self._render_featured(feat, note=note)
                    if not note:
                        self._ann_load_page()
                else:
                    self._render_featured(self._featured, error=err)
            self.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _load_patch_notes(self):
        """Read the patch notes thread once, in the background."""
        if getattr(self, "_patch_loading", False):
            return
        self._patch_loading = True
        self._render_patch_notes(self._patch_items, loading=True)

        def worker():
            items, err = None, ""
            try:
                items = fetch_patch_notes(
                    getattr(self, "_patch_limit", NEWS_CHANGELOG_COUNT))
            except Exception as exc:
                err = _news_error(N_("Changelog"), exc)

            def apply():
                self._patch_loading = False
                if items is not None:
                    self._patch_items = items
                    self._save_news_cache("changelog", "items", items)
                    self._news_seen("changelog")
                    self._render_patch_notes(items)
                else:
                    self._render_patch_notes(self._patch_items, error=err)
            self.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _news_header(self, parent, title, refresh_command, loading=False):
        hdr = tk.Frame(parent, bg=C_PANEL_ALT)
        hdr.pack(fill="x")
        inner = tk.Frame(hdr, bg=C_PANEL_ALT)
        inner.pack(fill="x", padx=self._px(16),
                   pady=(self._px(13), self._px(11)))
        tk.Label(inner, text=title,
                 font=self._font(12, bold=True),
                 fg=C_GOLD_LT, bg=C_PANEL_ALT).pack(side="left")
        if loading:
            tk.Label(inner, text=tr("Refreshing…"), font=self._font(8),
                     fg=C_TEXT_DIM, bg=C_PANEL_ALT).pack(
                         side="right", padx=(0, self._px(8)))
        rf = tk.Label(inner, text="⟳", font=("Segoe UI Symbol", 14),
                      fg=C_TEXT_DIM, bg=C_PANEL_ALT, cursor="hand2")
        rf.pack(side="right")
        rf.bind("<Button-1>", lambda e: refresh_command())
        rf.bind("<Enter>", lambda e: rf.configure(fg=C_GOLD_LT))
        rf.bind("<Leave>", lambda e: rf.configure(fg=C_TEXT_DIM))
        tk.Frame(parent, bg=C_DIVIDER, height=self._px(1)).pack(fill="x")

    # ── open/close lists: Announcements and the Changelog ────────────────────

    def _render_accordion(self, f, key, entries, open_ids, toggle, *,
                          wrap_w, title_size=11, footer=None):
        """A scrolling list filling ``f`` below its header: one entry per
        item, its title and byline a single control that opens or closes
        the entry. An open entry shows its text (``body``; None while it is
        being read) and a link to the forum.

        This builds the list; afterwards _acc_sync changes it in place --
        an entry opened or closed, a post arrived, entries added by Load
        more -- so a click redraws one entry, not the panel. Building it
        again (a refresh) keeps the scroll position.

        ``footer(inner)`` adds what goes under the entries."""
        # Where the old list was scrolled to, in pixels: taken by
        # _acc_remember before the panel was cleared.
        y_px = getattr(self, "_acc_keep", {}).pop(key, 0)

        list_frame = tk.Frame(f, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True,
                        padx=(self._px(14), self._px(4)),
                        pady=(0, self._px(8)))
        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL,
                           width=self._px(10), thumb="#31547e")

        def scrolled(first, last):
            sb.set(first, last)
            self._acc_pin(key)
        canvas.configure(yscrollcommand=scrolled)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._wheel_canvases.append(canvas)
        inner = tk.Frame(canvas, bg=C_PANEL)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", self._list_stretch(canvas, lambda: win))
        self._acc_canvas = {**getattr(self, "_acc_canvas", {}), key: canvas}
        self._acc_wraps.setdefault(key, [])
        lst = {"inner": inner, "order": [], "rows": {}, "toggle": toggle,
               "title_size": title_size, "footer": footer, "tail": [],
               "marks": self._news_marks(key, entries)}
        self._acc_lists = {**getattr(self, "_acc_lists", {}), key: lst}

        for item in entries:
            self._acc_add(key, item, str(item.get("id", "")) in open_ids)
        self._acc_footer(key)
        if y_px:
            # The same pixel offset, not the same fraction: the list may be
            # longer than before, and a fraction would then move the view.
            inner.update_idletasks()
            total = max(1, inner.winfo_reqheight())
            canvas.configure(scrollregion=(0, 0, inner.winfo_reqwidth(), total))
            canvas.yview_moveto(y_px / total)

    @staticmethod
    def _acc_signature(item, is_open, mark=False):
        """What an entry shows: when this changes, the entry is redone."""
        body = item.get("body")
        return (is_open, item.get("title"), body is None, body or "",
                item.get("_error") or "", item.get("url") or "", mark)

    def _acc_add(self, key, item, is_open):
        """One entry at the end of a list, with the divider under it."""
        lst = self._acc_lists[key]
        iid = str(item.get("id", ""))
        entry = tk.Frame(lst["inner"], bg=C_PANEL)
        entry.pack(fill="x", padx=(0, self._px(8)))
        head = tk.Frame(entry, bg=C_PANEL, cursor="hand2")
        head.pack(fill="x", pady=(self._px(10), 0))
        title_row = tk.Frame(head, bg=C_PANEL, cursor="hand2")
        title_row.pack(fill="x")
        title = tk.Label(title_row, text=item.get("title", ""),
                         font=self._font(lst["title_size"], bold=True),
                         fg=C_TEXT, bg=C_PANEL,
                         wraplength=self._news_wrap(key), justify="left",
                         anchor="w", cursor="hand2")
        title.pack(side="left")
        self._acc_wraps.setdefault(key, []).append(title)
        # Shown by _acc_show on a new post, just after its title.
        badge = tk.Label(title_row, text=tr("NEW!"),
                         font=self._font(lst["title_size"] + 1, bold=True,
                                         italic=True),
                         fg=C_NEW, bg=C_PANEL, cursor="hand2")
        byline = tk.Label(head, text="", font=self._font(8), fg=C_TEXT_DIM,
                          bg=C_PANEL, anchor="w", cursor="hand2")
        byline.pack(fill="x", pady=(self._px(2), 0))
        body = tk.Frame(entry, bg=C_PANEL)
        body.pack(fill="x")
        divider = tk.Frame(lst["inner"], bg=C_DIVIDER, height=self._px(1))
        divider.pack(fill="x", padx=(0, self._px(8)), pady=(self._px(10), 0))
        row = {"entry": entry, "head": head, "title": title, "byline": byline,
               "body": body, "divider": divider, "badge": badge, "sig": None}
        for w in (head, title_row, title, badge, byline):
            w.bind("<Button-1>", lambda e, i=iid: lst["toggle"](i))
            w.bind("<Enter>", lambda e, l=title: l.configure(fg=C_GOLD))
            w.bind("<Leave>", lambda e, r=row: r["title"].configure(
                fg=C_GOLD_LT if r["sig"] and r["sig"][0] else C_TEXT))
        lst["order"].append(iid)
        lst["rows"][iid] = row
        self._acc_show(key, iid, item, is_open)

    def _acc_show(self, key, iid, item, is_open):
        """Bring one entry up to date: its arrow, its title's colour and, if
        what it shows changed, its text -- nothing else in the list."""
        lst = self._acc_lists[key]
        row = lst["rows"][iid]
        is_new = iid in lst.get("marks", ())
        sig = self._acc_signature(item, is_open, is_new)
        if sig == row["sig"]:
            return
        row["sig"] = sig
        # NEW! after the title, the title wrapping short of it.
        badge, title = row["badge"], row["title"]
        if is_new:
            title._wrap_cut = badge.winfo_reqwidth() + self._px(8)
            badge.pack(side="left", anchor="n", padx=(self._px(8), 0))
        else:
            title._wrap_cut = 0
            badge.pack_forget()
        title.configure(wraplength=max(self._px(80), self._news_wrap(key)
                                       - title._wrap_cut))
        meta = []
        if item.get("author"):
            meta.append(tr("by {author}", author=item["author"]))
        date = _format_news_date(item.get("date", ""))
        if date:
            meta.append(date)
        row["byline"].configure(text=" · ".join(meta) + "  "
                                + ("▴" if is_open else "▾"))
        row["title"].configure(text=item.get("title", ""),
                               fg=C_GOLD_LT if is_open else C_TEXT)
        body = row["body"]
        for w in body.winfo_children():
            w.destroy()
        if not is_open:
            body.configure(height=1)
            if lst.get("pinned") == iid:
                self.after_idle(lambda: self._acc_pin(key))
            return
        text = item.get("body")
        if text is None:
            tk.Label(body, text=tr("Loading…"), font=self._font(9),
                     fg=C_TEXT_DIM, bg=C_PANEL, anchor="w").pack(
                         fill="x", pady=(self._px(6), 0))
        elif item.get("_error"):
            err = tk.Label(body, text=item["_error"], font=self._font(9),
                           fg=C_ERR, bg=C_PANEL,
                           wraplength=self._news_wrap(key),
                           justify="left", anchor="w")
            err.pack(fill="x", pady=(self._px(6), 0))
            self._acc_wraps.setdefault(key, []).append(err)
        elif text.strip():
            txt = self._news_text(body, text, item.get("links") or [])
            if key == "patch":
                self._acc_clamp(key, iid, item, txt)
        if item.get("url"):
            lnk = tk.Label(body, text=tr("Open in Browser") + "  ↗",
                           font=self._font(9, bold=True), fg=C_GOLD,
                           bg=C_PANEL, cursor="hand2", anchor="w")
            lnk.pack(fill="x", pady=(self._px(8), 0))
            lnk.bind("<Button-1>", lambda e, u=item["url"]: self._open_url(u))
            lnk.bind("<Enter>", lambda e, w=lnk: w.configure(fg=C_GOLD_LT))
            lnk.bind("<Leave>", lambda e, w=lnk: w.configure(fg=C_GOLD))

    #: How many lines of a changelog entry show before "Read more".
    CHANGELOG_PREVIEW_LINES = 14

    def _acc_clamp(self, key, iid, item, txt):
        """A long changelog entry opens to its first lines and a "Read more"
        under them; "Show less" puts it back. A short one shows whole, with
        neither. Follows the text's own height as the column is resized."""
        lst = self._acc_lists[key]
        full = lst.setdefault("full", set())
        limit = self.CHANGELOG_PREVIEW_LINES
        toggle = tk.Label(txt.master, text="", font=self._font(9, bold=True),
                          fg=C_GOLD, bg=C_PANEL, cursor="hand2", anchor="w")

        def lines():
            try:
                n = txt.count("1.0", "end-1c", "displaylines")
                return (n[0] if isinstance(n, tuple) else n) or 0
            except (tk.TclError, TypeError):
                return 0

        def fit(_event=None):
            n = lines() + 1
            long = n > limit
            want = n if (iid in full or not long) else limit
            try:
                if int(txt.cget("height")) != want:
                    txt.configure(height=want)
                if long:
                    toggle.configure(text=tr("Show less") if iid in full
                                     else tr("Read more"))
                    if not toggle.winfo_manager():
                        toggle.pack(fill="x", after=txt,
                                    pady=(self._px(4), 0))
                elif toggle.winfo_manager():
                    toggle.pack_forget()
            except tk.TclError:
                pass

        def flip(_e=None):
            full.symmetric_difference_update({iid})
            fit()
            if iid not in full:
                # Back at the top of the entry, not lost below it.
                self._acc_scroll_to(key, iid)
        toggle.bind("<Button-1>", flip)
        toggle.bind("<Enter>", lambda e: toggle.configure(fg=C_GOLD_LT))
        toggle.bind("<Leave>", lambda e: toggle.configure(fg=C_GOLD))
        txt.bind("<Configure>", fit, add="+")
        fit()

    def _acc_scroll_to(self, key, iid):
        """Scroll a list so an entry's header is at the top, if it is above."""
        lst = self._acc_lists.get(key)
        canvas = getattr(self, "_acc_canvas", {}).get(key)
        if not lst or canvas is None or iid not in lst["rows"]:
            return
        try:
            lst["inner"].update_idletasks()
            y = lst["rows"][iid]["entry"].winfo_y()
            if y < canvas.canvasy(0):
                total = max(1, lst["inner"].winfo_reqheight())
                canvas.configure(scrollregion=(0, 0, lst["inner"].winfo_reqwidth(),
                                               total))
                canvas.yview_moveto(y / total)
            self._acc_pin(key)
        except tk.TclError:
            pass

    def _acc_pin(self, key):
        """Keep an open entry's header in view while its text scrolls under
        it: when the header has scrolled off the top of the list, a copy of it
        sits there, with the divider and gap under it, until the entry's text
        has gone too. Clicking it closes the entry, wherever the list is."""
        lst = getattr(self, "_acc_lists", {}).get(key)
        canvas = getattr(self, "_acc_canvas", {}).get(key)
        if not lst or canvas is None:
            return
        try:
            if not canvas.winfo_exists():
                return
            top = canvas.canvasy(0)
        except tk.TclError:
            return
        pin = lst.get("pin")
        found = None
        for iid in lst["order"]:
            row = lst["rows"][iid]
            if not (row["sig"] and row["sig"][0]):
                continue
            try:
                y = row["entry"].winfo_y()
                h = row["entry"].winfo_height()
                head = row["head"].winfo_height()
            except tk.TclError:
                continue
            pin_h = pin.winfo_reqheight() if pin is not None else head
            if y + self._px(10) < top and y + h > top + pin_h:
                found = iid
                break
        if found is None:
            if pin is not None:
                pin.place_forget()
            lst["pinned"] = None
            return
        if pin is None:
            pin = tk.Frame(canvas.master, bg=C_PANEL, cursor="hand2")
            title = tk.Label(pin, font=self._font(lst["title_size"], bold=True),
                             fg=C_GOLD_LT, bg=C_PANEL, justify="left",
                             anchor="w", cursor="hand2")
            title.pack(fill="x", pady=(self._px(10), 0))
            byline = tk.Label(pin, font=self._font(8), fg=C_TEXT_DIM,
                              bg=C_PANEL, anchor="w", cursor="hand2")
            byline.pack(fill="x", pady=(self._px(2), 0))
            tk.Frame(pin, bg=C_DIVIDER, height=self._px(1)).pack(
                fill="x", pady=(self._px(8), 0))
            tk.Frame(pin, bg=C_PANEL, height=self._px(6)).pack(fill="x")
            pin._title, pin._byline = title, byline

            def close(_e=None):
                iid = lst.get("pinned")
                if iid is None:
                    return
                lst["toggle"](iid)
                self._acc_scroll_to(key, iid)
            for w in (pin, title, byline):
                w.bind("<Button-1>", close)
                w.bind("<Enter>", lambda e: pin._title.configure(fg=C_GOLD))
                w.bind("<Leave>", lambda e: pin._title.configure(fg=C_GOLD_LT))
            lst["pin"] = pin
        row = lst["rows"][found]
        lst["pinned"] = found
        pin._title.configure(text=row["title"].cget("text"),
                             wraplength=row["title"].cget("wraplength"))
        pin._byline.configure(text=row["byline"].cget("text"))
        # Over the list, not past it: the scrollbar stays uncovered.
        pin.place(in_=canvas, x=0, y=0,
                  width=max(1, canvas.winfo_width() - self._px(8)))
        pin.lift()

    def _acc_footer(self, key):
        """What goes under a list's entries, made again."""
        lst = self._acc_lists[key]
        for w in lst["tail"]:
            try:
                w.destroy()
            except tk.TclError:
                pass
        before = set(lst["inner"].winfo_children())
        if lst["footer"] is not None:
            lst["footer"](lst["inner"])
        lst["tail"] = [w for w in lst["inner"].winfo_children()
                       if w not in before]

    def _acc_sync(self, key, entries, open_ids) -> bool:
        """Change a built list in place to show ``entries``: each entry
        brought up to date, new ones added at the end, the footer made
        again. False when that cannot be done (no list on screen, or the
        entries are not the shown ones plus some at the end): build it."""
        lst = getattr(self, "_acc_lists", {}).get(key)
        canvas = getattr(self, "_acc_canvas", {}).get(key)
        try:
            if lst is None or canvas is None or not canvas.winfo_exists():
                return False
        except tk.TclError:
            return False
        ids = [str(item.get("id", "")) for item in entries]
        if ids[:len(lst["order"])] != lst["order"]:
            return False
        lst["marks"] = self._news_marks(key, entries)
        for item, iid in zip(entries, ids):
            if iid in lst["rows"]:
                self._acc_show(key, iid, item, iid in open_ids)
        new = [(item, iid) for item, iid in zip(entries, ids)
               if iid not in lst["rows"]]
        if new:
            for w in lst["tail"]:
                try:
                    w.destroy()
                except tk.TclError:
                    pass
            lst["tail"] = []
            for item, iid in new:
                self._acc_add(key, item, iid in open_ids)
        self._acc_footer(key)
        return True

    def _news_text(self, parent, body, links):
        """A post's text, its links clickable (opened in the browser), sized
        to its wrapped lines -- and resized with the column. Read-only, but
        selectable for copying."""
        txt = tk.Text(parent, bg=C_PANEL, fg=C_TEXT, font=self._font(10),
                      wrap="word", height=1, relief="flat", bd=0,
                      highlightthickness=0, padx=0, pady=0,
                      spacing2=self._px(2), cursor="arrow")
        txt.insert("1.0", body)
        for n, link in enumerate(links):
            try:
                start, end, url = int(link[0]), int(link[1]), str(link[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not (0 <= start < end <= len(body)) or \
                    urlsplit(url).scheme not in ("http", "https"):
                continue
            tag = f"link{n}"
            txt.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
            txt.tag_configure(tag, foreground=C_GOLD, underline=True)
            txt.tag_bind(tag, "<Button-1>", lambda e, u=url: self._open_url(u))
            txt.tag_bind(tag, "<Enter>", lambda e: txt.configure(cursor="hand2"))
            txt.tag_bind(tag, "<Leave>", lambda e: txt.configure(cursor="arrow"))
        txt.configure(state="disabled")
        txt.pack(fill="x", pady=(self._px(8), 0))

        def fit(_event=None):
            try:
                n = txt.count("1.0", "end-1c", "displaylines")
                n = (n[0] if isinstance(n, tuple) else n) or 0
                if int(txt.cget("height")) != n + 1:
                    txt.configure(height=n + 1)
            except (tk.TclError, TypeError, ValueError):
                pass
        txt.bind("<Configure>", fit, add="+")
        # Laid out and fitted now, so it is painted at its full height
        # instead of one line that grows a moment later.
        try:
            txt.update_idletasks()
        except tk.TclError:
            pass
        fit()
        return txt

    def _acc_remember(self, key):
        """Note how far a list is scrolled, before its panel is redrawn."""
        canvas = getattr(self, "_acc_canvas", {}).get(key)
        try:
            if canvas is not None and canvas.winfo_exists():
                self._acc_keep = {**getattr(self, "_acc_keep", {}),
                                  key: canvas.canvasy(0)}
        except tk.TclError:
            pass

    def _news_message(self, f, text, wrap_w, *, error=False, bottom=False):
        label = tk.Label(f, text=text, font=self._font(8 if bottom else 10),
                         fg=C_ERR if error else C_TEXT_DIM, bg=C_PANEL,
                         wraplength=wrap_w, justify="left", anchor="w")
        label.pack(side="bottom" if bottom else "top", fill="x",
                   padx=self._px(16),
                   pady=(0, self._px(4)) if bottom else self._px(16))
        key = "ann" if f is self._feat_frame else "patch"
        self._acc_wraps.setdefault(key, []).append(label)

    # ── Announcements ────────────────────────────────────────────────────────

    #: Announcements listed at first, and added by each Load more.
    ANN_PAGE = 10

    def _render_featured(self, post, loading=False, error="", note=""):
        """The Announcements panel: every announcement in forum 2, newest
        first, ten at a time, each opening to its post; the newest is open
        by default. ``post`` is the newest announcement as read at launch
        (or news.json's, while the forum cannot be read): until the list
        itself is read, it is shown alone."""
        self._feat_render_args = (post, loading, error, note)
        self._acc_remember("ann")
        self._acc_wraps = {**getattr(self, "_acc_wraps", {}), "ann": []}
        f = self._feat_frame
        for w in f.winfo_children():
            w.destroy()
        f.configure(bg=C_PANEL, highlightthickness=1,
                    highlightbackground=C_PANEL_BDR)
        st = self._ann()
        self._news_header(f, tr("ANNOUNCEMENTS"), self._load_featured,
                          loading=loading or st["loading"])
        wrap_w = self._news_wrap("ann")
        if error or note:
            text = (tr("{error} · showing cached content", error=error) if error
                    else tr("{error} · showing the news feed instead",
                            error=tr(note)))
            self._news_message(f, text, wrap_w, bottom=True)

        entries = self._ann_entries(post)
        if not entries:
            self._news_message(f, error or (tr("Loading announcements…")
                                            if loading else
                                            tr("No announcements available.")),
                               wrap_w)
            return
        if st["open"] is None:
            st["open"] = {str(entries[0].get("id", ""))}
        self._render_accordion(f, "ann", entries, st["open"],
                               self._ann_toggle, wrap_w=wrap_w,
                               title_size=12, footer=self._ann_footer)

    def _ann(self) -> dict:
        """The Announcements list: the topics read so far (by id), the offset
        of the next forum page (None after the last), how many are listed,
        which are open, and the posts already read (by id)."""
        st = getattr(self, "_ann_state", None)
        if st is None:
            st = self._ann_state = {"rows": {}, "next": 0, "loaded": False,
                                    "shown": self.ANN_PAGE, "loading": False,
                                    "error": "", "posts": {}, "open": None}
        return st

    def _ann_reset(self):
        """Forget the list (refresh): read again, newest open again."""
        self._ann_state = None

    def _ann_entries(self, post) -> list:
        """The items listed: the forum's topics once read, each with its
        post where read (None while not); before that, ``post`` alone."""
        st = self._ann()
        if post and post.get("id") is not None:
            st["posts"].setdefault(str(post["id"]), post)
        if not st["loaded"]:
            return [post] if post else []
        out = []
        for row in self._ann_visible()[:st["shown"]]:
            item = row.to_item()
            known = st["posts"].get(str(row.topic_id))
            item["body"] = known.get("body") if known else None
            item["links"] = (known or {}).get("links") or []
            if known and known.get("_error"):
                item["_error"] = known["_error"]
            out.append(item)
        return out

    def _ann_visible(self) -> list:
        """The topics read so far, newest started first. Pinned topics sit
        on every page of a phpBB listing whatever their age, so one older
        than the oldest ordinary topic read so far is held back until the
        pages around its date are read, or the forum's last page is."""
        st = self._ann()
        rows = newest_first(list(st["rows"].values()))
        if st["next"] is not None:
            dated = [r.created_at for r in rows
                     if r.kind == "normal" and r.created_at]
            if dated:
                floor = min(dated)
                rows = [r for r in rows if r.kind == "normal"
                        or (r.created_at and r.created_at >= floor)]
        return rows

    def _ann_redraw(self):
        """Show the list's new state: in place when only entries changed or
        were added; built again otherwise."""
        st = self._ann()
        post = self._feat_render_args[0]
        entries = self._ann_entries(post)
        if st["open"] is None or not entries \
                or not self._acc_sync("ann", entries, st["open"]):
            self._render_featured(*self._feat_render_args)

    def _ann_toggle(self, iid: str):
        """Open or close one announcement; its post is read the first time."""
        st = self._ann()
        if iid in st["open"]:
            st["open"].discard(iid)
        else:
            st["open"].add(iid)
            known = st["posts"].get(iid)
            if known is None or known.get("_error"):
                st["posts"].pop(iid, None)      # a failed read is tried again
                self._ann_read_post(iid)
        self._ann_redraw()

    def _ann_read_post(self, iid: str):
        st = self._ann()
        row = st["rows"].get(int(iid)) if iid.isdigit() else None
        if row is None:
            return

        def worker():
            try:
                item = fetch_first_post(row, opener=_news_open, user_agent=UA,
                                        timeout=NEWS_TIMEOUT).to_item()
            except Exception as exc:
                item = {"_error": _news_error(N_("Announcements"), exc),
                        "body": ""}

            def apply():
                if self._ann_state is not st:
                    return                      # refreshed meanwhile
                st["posts"][iid] = item         # an error is shown in place
                self._ann_redraw()
            self.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _ann_load_page(self):
        """Read the next page of forum 2's listing, in the background."""
        st = self._ann()
        if st["loading"] or st["next"] is None:
            return
        st["loading"], st["error"] = True, ""
        start = st["next"]

        def worker():
            rows, nxt, err = [], start, ""
            try:
                rows, nxt = fetch_forum_page(
                    ANNOUNCEMENTS_FORUM_ID, start, opener=_news_open,
                    user_agent=UA, timeout=NEWS_TIMEOUT)
            except Exception as exc:
                log(f"Announcements list: {exc}", "dim")
                err = getattr(exc, "short", None) or str(exc)

            def apply():
                if self._ann_state is not st:
                    return                      # refreshed meanwhile
                st["loading"] = False
                if err:
                    st["error"] = err
                else:
                    for row in rows:
                        st["rows"].setdefault(row.topic_id, row)
                    st["next"], st["loaded"] = nxt, True
                self._ann_redraw()
            self.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _ann_footer(self, inner):
        """Under the list: Load more, or why the list could not be read."""
        st = self._ann()
        if st["loading"]:
            tk.Label(inner, text=tr("Loading announcements…"),
                     font=self._font(9), fg=C_TEXT_DIM,
                     bg=C_PANEL).pack(pady=self._px(10))
            return
        if st["error"]:
            tk.Label(inner, text=tr("{section} unavailable: {reason}",
                                    section=tr("Announcements"),
                                    reason=tr(st["error"])),
                     font=self._font(9), fg=C_ERR, bg=C_PANEL,
                     wraplength=max(self._px(200),
                                    self._news_left_w - self._px(70)),
                     justify="left").pack(pady=(self._px(10), 0), anchor="w")
        more = (not st["loaded"] or len(self._ann_visible()) > st["shown"]
                or st["next"] is not None)
        if not (more or st["error"]):
            return
        btn = tk.Label(inner, text=tr("Load more"),
                       font=self._font(10, bold=True), fg=C_TEXT,
                       bg=C_PANEL_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(4))
        btn.pack(pady=self._px(12))
        btn.bind("<Button-1>", lambda e: self._ann_more())
        btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD, fg="#000"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=C_PANEL_BDR, fg=C_TEXT))

    def _ann_more(self):
        """Ten more: from the topics already read, reading the next forum
        page when those run out."""
        st = self._ann()
        if st["error"]:
            st["error"] = ""
        elif st["loaded"]:
            st["shown"] += self.ANN_PAGE
        if not st["loaded"] or (len(self._ann_visible()) < st["shown"]
                                and st["next"] is not None):
            self._ann_load_page()
        self._ann_redraw()

    # ── Changelog ────────────────────────────────────────────────────────────

    def _render_patch_notes(self, items, loading=False, error=""):
        """The Changelog: forum 4's newest topics, each opening to its patch
        notes; the newest is open by default."""
        self._patch_render_args = (items, loading, error)
        self._acc_remember("patch")
        self._acc_wraps = {**getattr(self, "_acc_wraps", {}), "patch": []}
        f = self._patch_frame
        for w in f.winfo_children():
            w.destroy()
        f.configure(bg=C_PANEL, highlightthickness=1,
                    highlightbackground=C_PANEL_BDR)
        self._news_header(
            f, tr("CHANGELOG"), self._load_patch_notes,
            loading=loading)
        wrap_w = self._news_wrap("patch")
        if error:
            self._news_message(f, tr("{error} · showing cached content",
                                     error=error), wrap_w, bottom=True)
        if not items:
            self._news_message(f, error or (tr("Loading changelog…") if loading
                                            else tr("No changelog entries "
                                                    "available.")), wrap_w)
            return
        opened = getattr(self, "_patch_open", None)
        if opened is None:                      # the newest, until changed
            opened = self._patch_open = {str(items[0].get("id", ""))}
        self._render_accordion(f, "patch", items, opened, self._patch_toggle,
                               wrap_w=wrap_w, title_size=10,
                               footer=self._patch_footer)

    def _patch_footer(self, inner):
        """Under the Changelog: Show more while the forum has older posts."""
        if getattr(self, "_patch_loading", False) and \
                getattr(self, "_patch_limit", 0) > len(self._patch_items or []):
            tk.Label(inner, text=tr("Loading changelog…"), font=self._font(9),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(pady=self._px(10))
            return
        if not CHANGELOG_MORE["more"]:
            return
        btn = tk.Label(inner, text=tr("Show more"),
                       font=self._font(10, bold=True), fg=C_TEXT,
                       bg=C_PANEL_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(4))
        btn.pack(pady=self._px(12))
        btn.bind("<Button-1>", lambda e: self._patch_more())
        btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD, fg="#000"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=C_PANEL_BDR, fg=C_TEXT))

    def _patch_more(self):
        """Eight older Changelog posts, under the ones shown."""
        self._patch_limit = max(getattr(self, "_patch_limit", 0),
                                len(self._patch_items or [])) + NEWS_CHANGELOG_COUNT
        self._load_patch_notes()

    def _patch_toggle(self, iid: str):
        opened = getattr(self, "_patch_open", None) or set()
        opened ^= {iid}
        self._patch_open = opened
        items = self._patch_render_args[0] or []
        if not self._acc_sync("patch", items, opened):
            self._render_patch_notes(*self._patch_render_args)


    # ── tweaks panel ─────────────────────────────────────────────────────────────

    def _build_tweaks_panel(self):
        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR,
                         highlightcolor=C_PANEL_BDR)
        self._tweaks_panel_frame = outer

        # The rows scroll. They outgrew the panel: OpenDyslexic already hid
        # the SOUND section, and every new tweak pushes the bottom further
        # out. The scrollbar draws nothing while everything fits.
        canvas = tk.Canvas(outer, bg=C_PANEL, highlightthickness=0, bd=0,
                           yscrollincrement=self._px(12))
        sb = SlimScrollbar(outer, command=canvas.yview, bg=C_PANEL,
                           width=self._px(10), thumb="#31547e")
        canvas.configure(yscrollcommand=sb.set)
        # Below the Apply/Reset bar, which sits over the top right corner.
        sb.pack(side="right", fill="y", pady=(self._px(40), self._px(4)))
        canvas.pack(side="left", fill="both", expand=True)
        self._tweaks_canvas = canvas
        self._wheel_canvases.append(canvas)

        self._tweaks_inner = tk.Frame(canvas, bg=C_PANEL)
        window = canvas.create_window((0, 0), window=self._tweaks_inner,
                                      anchor="nw")
        self._tweaks_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", self._list_stretch(canvas, lambda: window))

        bar = tk.Frame(outer, bg=C_PANEL)
        bar.place(relx=1.0, x=-self._px(16), y=self._px(3), anchor="ne")

        apl = tk.Label(bar, text=tr("Apply"), font=self._font(11),
                       fg=C_TEXT, bg=C_PANEL_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(4))
        apl.bind("<Button-1>", lambda e: self._apply_tweaks())
        apl.bind("<Enter>",    lambda e: apl.configure(bg=C_GOLD, fg="#000"))
        apl.bind("<Leave>",    lambda e: apl.configure(bg=C_PANEL_BDR, fg=C_TEXT))
        self._tweaks_apply_btn = apl

        rst = tk.Label(bar, text=tr("Reset"), font=self._font(11),
                       fg=C_TEXT, bg=C_PANEL_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(4))
        rst.bind("<Button-1>", lambda e: self._reset_tweaks())
        rst.bind("<Enter>",    lambda e: rst.configure(bg=C_GOLD, fg="#000"))
        rst.bind("<Leave>",    lambda e: rst.configure(bg=C_PANEL_BDR, fg=C_TEXT))
        self._tweaks_reset_btn = rst

        self._tweak_widgets: dict = {}
        self._tweak_vars:    dict = {}

        self._build_tweaks_rows()

    def _build_tweaks_rows(self):
        for w in self._tweaks_inner.winfo_children():
            w.destroy()
        self._tweak_widgets = {}
        self._tweak_vars    = {}

        values = load_tweaks_config()
        PAD_X  = self._px(16)
        # The name column fits the longest name in this language: "Nameplate
        # Range" in German or Spanish is half as long again as in English.
        # Measured in the widths of "0", the unit a Label's width is in.
        import tkinter.font as tkfont
        name_font = tkfont.Font(font=self._font(10, bold=True))
        zero = max(1, name_font.measure("0"))
        name_chars = max([22] + [-(-name_font.measure(tr(item[1])) // zero) + 1
                                 for item in TWEAKS_ITEMS if item[2] != "section"])

        for (tid, label, kind, _, _, desc, mn, mx, _) in TWEAKS_ITEMS:
            label = tr(label)
            desc = tr(desc) if desc else desc
            if tid == "fieldOfView":
                # Shown, never applied on its own - see load_tweaks_config().
                try:
                    desc = tr("{description} Suggested for your display: {value}.",
                              description=desc,
                              value=fov_default_for_display())
                except Exception:
                    pass
            if kind == "section":
                tk.Label(self._tweaks_inner, text=label,
                         font=self._font(11, bold=True),
                         fg=C_GOLD, bg=C_PANEL,
                         anchor="w").pack(fill="x", padx=PAD_X,
                                          pady=(self._px(10), self._px(2)))
                tk.Frame(self._tweaks_inner, bg=C_DIVIDER, height=self._px(1)).pack(
                    fill="x", padx=PAD_X, pady=(0, self._px(4)))
                continue

            row = tk.Frame(self._tweaks_inner, bg=C_PANEL)
            row.pack(fill="x", padx=PAD_X, pady=self._px(3))

            name_lbl = tk.Label(row, text=label,
                                font=self._font(10, bold=True),
                                fg=C_TEXT, bg=C_PANEL,
                                width=name_chars, anchor="w")
            name_lbl.pack(side="left")
            if tid == "locale":
                self._add_tooltip(name_lbl, tr(
                    "The language of the game and of EqUpdater."))

            if kind == "checkbox":
                var = tk.BooleanVar(value=values.get(tid, False))
                var.trace_add("write", self._refresh_tweaks_buttons)
                tk.Checkbutton(row, variable=var,
                               bg=C_PANEL, activebackground=C_PANEL,
                               fg=C_TEXT, selectcolor=C_PANEL,
                               highlightthickness=0, bd=0,
                               relief="flat", cursor="hand2"
                               ).pack(side="left", padx=(self._px(4), self._px(12)))
                self._tweak_vars[tid] = var

            elif kind == "number":
                val = values.get(tid, mn or 0)
                var = tk.StringVar(value=str(int(val)))
                var.trace_add("write", self._refresh_tweaks_buttons)
                entry = tk.Entry(row, textvariable=var,
                                 bg="#18181e", fg=C_TEXT,
                                 insertbackground=C_GOLD,
                                 relief="flat", font=FONT_MONO,
                                 width=7,
                                 highlightthickness=1,
                                 highlightbackground=C_PANEL_BDR,
                                 highlightcolor=C_GOLD,
                                 justify="center")
                entry.pack(side="left", padx=(self._px(4), self._px(12)),
                           ipady=self._px(3))
                self._tweak_vars[tid]   = var
                self._tweak_widgets[tid] = entry

                def _clamp(e, t=tid, lo=mn, hi=mx):
                    try:
                        v = int(float(self._tweak_vars[t].get()))
                        if lo is not None: v = max(lo, v)
                        if hi is not None: v = min(hi, v)
                        self._tweak_vars[t].set(str(v))
                    except ValueError:
                        self._tweak_vars[t].set(str(TWEAKS_DEFAULTS.get(t, lo or 0)))
                entry.bind("<FocusOut>", _clamp)
                entry.bind("<Return>",   _clamp)

            elif kind == "dropdown":
                cur = values.get(tid, TWEAKS_DEFAULTS.get(tid))
                if cur not in LOCALES:
                    cur = DEFAULT_LOCALE
                var = tk.StringVar(value=cur)           # holds the locale code
                var.trace_add("write", self._refresh_tweaks_buttons)
                mb = tk.Menubutton(
                    row, text=LOCALES[cur][1], font=self._font(10),
                    fg=C_TEXT, bg="#18181e", activebackground=C_PANEL_BDR,
                    activeforeground=C_TEXT, relief="flat", cursor="hand2",
                    width=15, anchor="w", padx=self._px(8), pady=self._px(2),
                    highlightthickness=1, highlightbackground=C_PANEL_BDR)
                menu = tk.Menu(mb, tearoff=0, bg=C_PANEL, fg=C_TEXT,
                               activebackground=C_GOLD, activeforeground="#000")
                for code, (_i, label) in LOCALES.items():
                    menu.add_command(
                        label=label,
                        command=lambda c=code, v=var: v.set(c))
                mb["menu"] = menu
                mb.pack(side="left", padx=(self._px(4), self._px(12)))
                var.trace_add(
                    "write",
                    lambda *_a, m=mb, v=var: m.configure(
                        text=LOCALES.get(v.get(), (0, v.get()))[1]))
                self._tweak_vars[tid] = var

            if desc:
                tk.Label(row, text=desc,
                         font=self._font(10), fg=C_TEXT_DIM, bg=C_PANEL,
                         wraplength=self._px(520), justify="left", anchor="w"
                         ).pack(side="left", fill="x", expand=True)

        self._refresh_tweaks_buttons()

    def _refresh_tweaks_buttons(self, *args):
        """Show Apply only when the UI differs from the saved config and
        Reset only when values are custom (differ from the defaults); paint
        out-of-range number entries red."""
        if not getattr(self, "_tweak_vars", None):
            return

        any_bad = False
        for tid, entry in self._tweak_widgets.items():
            lo, hi = TWEAKS_LIMITS.get(tid, (None, None))
            try:
                v = int(float(self._tweak_vars[tid].get()))
                bad = ((lo is not None and v < lo) or
                       (hi is not None and v > hi))
            except ValueError:
                bad = True
            any_bad = any_bad or bad
            entry.configure(fg=C_ERR if bad else C_TEXT)

        ui       = self._get_tweaks_from_ui()
        saved    = load_tweaks_config()
        defaults = dict(TWEAKS_DEFAULTS)

        def norm(d):
            def one(k):
                default = TWEAKS_DEFAULTS.get(k)
                if isinstance(default, bool):
                    return bool(d.get(k))
                if isinstance(default, str):
                    return str(d.get(k, default))
                return int(d.get(k, 0))
            return {k: one(k) for k in ui}

        # An out-of-range entry always counts as a change: _get_tweaks_from_ui
        # clamps it, and the clamped value can coincide with the saved one
        # (e.g. saved 180, typed 192 → clamps to 180), which would otherwise
        # hide the buttons while the entry still shows an invalid number.
        # Reset is offered while either side is custom. Judging the screen
        # alone hid it whenever the screen happened to show the defaults --
        # Russian saved, English picked -- although Reset still had a saved
        # custom value to put back.
        ui_n   = norm(ui)
        dirty  = any_bad or ui_n != norm(saved)
        custom = (any_bad or ui_n != norm(defaults)
                  or norm(saved) != norm(defaults))

        self._tweaks_apply_btn.pack_forget()
        self._tweaks_reset_btn.pack_forget()
        if dirty:
            self._tweaks_apply_btn.pack(side="left")
        if custom:
            self._tweaks_reset_btn.pack(
                side="left", padx=(self._px(8), 0) if dirty else (0, 0))

    def _refresh_tweaks_panel(self):
        values = load_tweaks_config()
        for tid, var in self._tweak_vars.items():
            v = values.get(tid, TWEAKS_DEFAULTS.get(tid))
            if isinstance(var, tk.BooleanVar):
                var.set(bool(v))
            elif isinstance(TWEAKS_DEFAULTS.get(tid), str):
                var.set(v if v in LOCALES else DEFAULT_LOCALE)
            else:
                var.set(str(int(v)) if v is not None else "")

    def _get_tweaks_from_ui(self) -> dict:
        """Read tweak values from the UI, always clamped to their limits –
        an out-of-range entry can never reach the config or the exe patch."""
        result = {}
        for tid, var in self._tweak_vars.items():
            if isinstance(var, tk.BooleanVar):
                result[tid] = var.get()
            elif isinstance(TWEAKS_DEFAULTS.get(tid), str):
                code = var.get()
                result[tid] = code if code in LOCALES else DEFAULT_LOCALE
            else:
                try:
                    v = int(float(var.get()))
                except ValueError:
                    v = TWEAKS_DEFAULTS.get(tid, 0)
                lo, hi = TWEAKS_LIMITS.get(tid, (None, None))
                if lo is not None:
                    v = max(lo, v)
                if hi is not None:
                    v = min(hi, v)
                result[tid] = v
        return result

    def _reset_tweaks(self):
        """Put every tweak back to its default -- after asking. It throws
        away the user's values and, with a client installed, patches
        WoW.exe straight away, so one stray click must not do it."""
        from tkinter import messagebox
        out = self._game_path.get().strip()
        patch_now = bool(out and os.path.exists(os.path.join(out, "WoW.exe")))
        if not messagebox.askyesno(
                tr("Reset tweaks?"),
                (tr("Every tweak goes back to its default value, and WoW.exe "
                    "is patched with the defaults now.") if patch_now else
                 tr("Every tweak goes back to its default value."))
                + "\n\n" + tr("Your current values are not kept.")
                + "\n\n" + tr("Reset tweaks?"),
                icon="warning", default="no", parent=self):
            return
        defaults = dict(TWEAKS_DEFAULTS)
        save_tweaks_config(defaults)
        self._refresh_tweaks_panel()
        if not patch_now:
            self._offer_language_restart()
        if patch_now:
            self._set_btn_busy(tr("Patching…"))
            self._status_var.set(tr("Applying tweaks…"))
            # Pass the same defaults that were saved
            threading.Thread(target=self._apply_tweaks_worker,
                             args=(out, defaults), daemon=True).start()

    def _apply_tweaks(self):
        values = self._get_tweaks_from_ui()
        save_tweaks_config(values)
        # Write the (possibly clamped) saved values back into the entries so
        # the UI never keeps showing an out-of-range number after Apply.
        self._refresh_tweaks_panel()

        out = self._game_path.get().strip()
        if not out:
            self._log_line("Game folder not set.\n", "err")
            self._offer_language_restart()
            return

        exe = os.path.join(out, "WoW.exe")
        if not os.path.exists(exe):
            self._log_line("WoW.exe not found – run Update first.\n", "err")
            self._offer_language_restart()
            return

        self._log_line("\nApplying tweaks to WoW.exe...\n", "acct")
        self._set_btn_busy(tr("Patching…"))
        self._status_var.set(tr("Applying tweaks…"))
        threading.Thread(target=self._apply_tweaks_worker,
                         args=(out, values), daemon=True).start()

    def _apply_tweaks_worker(self, client_dir: str, tweaks: dict):
        log_q  = queue.Queue()
        prog_q = queue.Queue()
        worker = UpdateWorker(client_dir, log_q, prog_q)

        def drain():
            try:
                while True:
                    msg, tag = log_q.get_nowait()
                    if msg not in ("__DONE__", "__ERROR__") and not msg.startswith("__"):
                        self.after(0, lambda m=msg, t=tag: self._log_line(
                            (m if m.endswith("\n") else m + "\n"), t))
            except queue.Empty:
                pass

        try:
            worker.patch_exe(tweaks)
            drain()
            update_config_wtf(client_dir, tweaks)

            self._log_line("\nTweaks applied.\n", "ok")
            self.after(0, self._refresh_ready_state)
        except Exception as e:
            drain()
            self._log_line(f"\n✗ Tweak patch failed: {e}\n", "err")

            def _fail_state():
                self._status_error(tr("Tweaks failed – check the log"),
                                   self._show_logs)
                self._set_btn_update()
            self.after(0, _fail_state)
        # Only now, with WoW.exe written: a restart mid-patch would leave it
        # half-written.
        self.after(0, self._offer_language_restart)

    def _offer_language_restart(self):
        """EqUpdater's text is drawn once, so a new Language shows in
        EqUpdater after a restart. Offered, never forced, and asked in the
        language just chosen -- the one the player can read."""
        wanted = i18n.ui_language(
            load_tweaks_config().get("locale", DEFAULT_LOCALE))
        current = i18n.language()
        if wanted == current:
            return
        from tkinter import messagebox
        i18n.set_language(wanted)
        title = tr("Restart {app}?", app=branding.APP_NAME)
        body = tr("{app} restarts to show its text in the language you "
                  "chose.\n\nRestart now?", app=branding.APP_NAME)
        i18n.set_language(current)
        if messagebox.askyesno(title, body, parent=self):
            self._restart()

    def _restart(self):
        """Start a fresh EqUpdater, then close this one."""
        cmd = platforms.self_command(
            os.path.join(branding.app_dir(), "EqUpdater.py"))
        try:
            platforms.spawn_detached(cmd, cwd=os.path.expanduser("~"))
        except OSError as e:
            self._log_line(f"Could not restart: {e}\n", "err")
            return
        self._on_close()

    # ── version migrations ─────────────────────────────────────────────────────────

    def _new_version_installed(self):
        """One-time, version-gated migrations run at launch.

        The config key ``app_version`` records the updater version
        whose migrations have already been applied to this install (absent on
        installs predating this mechanism, < 1.3). When it already matches the
        running ``UPDATER_VERSION`` there's nothing to do.

        Otherwise replay every migration step newer than the stored version,
        then stamp the current one so each runs exactly once. Runs
        synchronously (before the normal startup verify is scheduled), so any
        WoW.exe re-patch is settled before verify reads the exe – no read/write
        race, and the standard verify still runs and can surface a pending
        client update. Future versions add their own ``if from_ver < (x, y): …``
        block below."""
        cfg = load_config()
        if cfg.get("app_version") == UPDATER_VERSION:
            return

        if not self._first_run:
            from_ver = _parse_version(cfg.get("app_version", ""))
            if from_ver < (1, 3):
                self._migrate_1_3()

        self._cfg = update_config(
            lambda c: c.__setitem__("app_version", UPDATER_VERSION))

    def _migrate_1_3(self):
        """1.3: raise the ground-clutter defaults for existing installs.

        frillDistance is a VanillaTweak baked into WoW.exe, so bumping the old
        70 → 120 default needs a re-patch (skipped for a user who already set a
        higher value). frillDensity is a plain Config.wtf value; if it's still
        below 128 we lift it to the new default in place."""
        out = self._game_path.get().strip()

        # frillDistance (VanillaTweak / WoW.exe). Raise to 120 if it's lower
        stored = dict(load_config().get("tweaks", {}))
        if int(stored.get("frillDistance", 70)) < 120:
            stored["frillDistance"] = 120
            save_tweaks_config(stored)
            self._log_line(
                "Ground clutter distance raised to 120 (new default).\n", "acct")
            if out and os.path.exists(os.path.join(out, "WoW.exe")):
                self._log_line("Re-patching WoW.exe for v1.3…\n", "acct")
                self._apply_tweaks_worker(out, load_tweaks_config())

        # frillDensity (Config.wtf value). Raise to 128 if it's lower
        cfg_path = gamepaths.game_path(out, "WTF", "Config.wtf") if out else ""
        if cfg_path and os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                changed = False
                for i, line in enumerate(lines):
                    if not line.strip().lower().startswith("set frilldensity "):
                        continue
                    parts = line.split('"')
                    cur = (int(parts[1]) if len(parts) >= 2
                           and parts[1].strip().lstrip("-").isdigit() else None)
                    if cur is not None and cur < 128:
                        lines[i] = 'SET frillDensity "128"\n'
                        changed = True
                    break
                if changed:
                    with open(cfg_path, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    self._log_line(
                        "Ground clutter density raised to 128 (new default).\n",
                        "acct")
            except Exception as e:
                self._log_line(
                    f"Could not update frillDensity in Config.wtf: {e}\n", "err")

        # dlls.txt cleanup: earlier versions wrongly registered the loader
        # (VfPatcher.dll) and dxvk here — neither is loaded via dlls.txt, so a
        # loader that walks the list would try to load itself / a missing file.
        dlls_path = _dlls_txt_path(out) if out else ""
        if dlls_path and os.path.exists(dlls_path):
            try:
                present = {l.strip().lower()
                           for l in _read_lines(dlls_path)}
                stale = [n for n in ("VfPatcher.dll", "dxvk")
                         if n.lower() in present]
                for n in stale:
                    remove_dll(out, n)
                if stale:
                    self._log_line(
                        f"Removed stale dlls.txt entries: {', '.join(stale)}.\n",
                        "acct")
            except Exception as e:
                self._log_line(f"Could not clean dlls.txt: {e}\n", "err")

        # dxvk.conf: refresh an existing config to the current template
        # (d3d9.presentInterval, no cursor / numBackBuffers). Only when DXVK is
        # actually present — a missing dxvk.conf is left alone.
        if out and os.path.exists(os.path.join(out, "dxvk.conf")):
            _write_dxvk_conf(out)
            self._log_line("dxvk.conf updated to the new defaults.\n", "acct")

        # Legacy game-hash cache: the torrent-based download no longer keeps a
        # per-file SHA-1 cache. Delete the pre-1.3 cache file beside the app.
        stale_cache = os.path.join(APP_DIR, "octo_updater_hash_cache.json")
        try:
            if os.path.exists(stale_cache):
                os.remove(stale_cache)
        except OSError:
            pass

        # Drop the vestigial WoW.exe hash keys from config — unused since the
        # download revamp (verify is size-based, patching reads the pristine
        # base), so they'd otherwise linger in every migrated config.
        self._cfg = update_config(
            lambda c: (c.pop("expected_patched_wow_hash", None),
                       c.pop("original_server_wow_hash", None)))

        self._set_pending_reconcile("keep-config")

    # ── mods panel ───────────────────────────────────────────────────────────────

    def _build_mods_panel(self):
        load_custom_mods()
        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR)
        self._mods_panel_frame = outer

        note = tk.Frame(outer, bg=C_PANEL)
        note.pack(fill="x", padx=self._px(16), pady=(self._px(14), self._px(8)),
                  anchor="w")
        # One sentence for the translators, split around the star.
        before, after = tr("Mods marked with {star} are essential").split("{star}")
        tk.Label(note, text=before,
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")
        tk.Label(note, text="★", font=self._font(10),
                 fg=C_GOLD, bg=C_PANEL).pack(side="left")
        tk.Label(note, text=after,
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")

        tk.Frame(outer, bg=C_DIVIDER, height=self._px(1)).pack(
            fill="x", padx=self._px(16), pady=(0, self._px(4)))

        list_frame = tk.Frame(outer, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True, padx=self._px(16))

        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL,
                           width=self._px(10))
        self._wheel_canvases.append(canvas)
        self._mods_inner = tk.Frame(canvas, bg=C_PANEL)
        self._mods_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        mods_win = canvas.create_window((0, 0), window=self._mods_inner,
                                        anchor="nw")
        # Stretch rows to the full canvas width so right-side controls
        # (Ignore updates) sit flush against the scrollbar.
        canvas.bind("<Configure>", self._list_stretch(canvas, lambda: mods_win))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        tk.Frame(outer, bg=C_DIVIDER, height=self._px(1)).pack(
            fill="x", padx=self._px(16), pady=(self._px(4), 0))
        foot = tk.Frame(outer, bg=C_PANEL)
        foot.pack(fill="x", padx=self._px(16), pady=(self._px(6), self._px(10)))

        # Packed on demand by _refresh_apply_btn_visibility(): shown only
        # when there are unapplied checkbox changes or a mod is in error.
        self._apply_btn = tk.Label(foot, text=tr("Apply"),
                                   font=self._font(11),
                                   fg=C_TEXT, bg=C_PANEL_BDR,
                                   cursor="hand2", padx=self._px(16),
                                   pady=self._px(4))
        self._apply_btn.bind("<Button-1>", lambda e: self._apply_mods())
        self._apply_btn.bind("<Enter>",    lambda e: self._apply_btn.configure(bg=C_GOLD, fg="#000"))
        self._apply_btn.bind("<Leave>",    lambda e: self._apply_btn.configure(bg=C_PANEL_BDR, fg=C_TEXT))

        # "Manage all" is on the INSTALLED LOCALLY header, as on Addons.
        self._mods_manage_all = None

        add = tk.Label(foot, text="+  " + tr("Add custom git mod"),
                       font=self._font(10, bold=True), fg="#d76f9e",
                       bg=C_PANEL, cursor="hand2")
        add.pack(side="right")
        add.bind("<Button-1>", lambda e: self._open_custom_mod_dialog())
        add.bind("<Enter>", lambda e: add.configure(fg="#eb96ba"))
        add.bind("<Leave>", lambda e: add.configure(fg="#d76f9e"))

        self._mod_row_vars:     dict = {}
        self._mods_state:       list = []
        self._mod_pending_state: dict = {}
        self._render_mod_rows()
        self._refresh_apply_btn_visibility()

    def _render_mod_rows(self):
        cfg      = load_config()
        mods_cfg = cfg.get("mods", {})

        # Essential mods first, then alphabetical within each group.
        mods_sorted = sorted(
            MODS_REGISTRY,
            key=lambda m: (not m.get("essential", False), m["name"].lower()))

        if self._mod_row_vars and self._mod_icons_stale(mods_cfg):
            self._rebuild_mod_rows()
            return
        if self._mod_row_vars:
            for mod in mods_sorted:
                mid   = mod["id"]
                state = mods_cfg.get(mid, {})
                live  = next((m for m in self._mods_state if m["id"] == mid), None)
                refs  = self._mod_row_vars.get(mid, {})
                if not refs:
                    continue

                if live is not None and "ver_label" in refs:
                    refs["ver_label"].configure(
                        text=f"  {self._mod_version_text(state, live)}")

                # Checkbox always reflects config only — never a registry default.
                # A mod only shows checked if it's actually recorded as installed.
                if mid not in self._mod_pending_state:
                    if "enabled" in refs:
                        refs["enabled"].set(state.get("enabled", False))
                    if "ignore" in refs:
                        refs["ignore"].set(state.get("ignore_updates", False))

                has_error = bool(state.get("error"))
                installed = mod_has_files(state)
                if "name_label" in refs:
                    refs["name_label"].configure(
                        fg=C_ERR if has_error else (C_MOD_HL if installed else C_TEXT))
                if "desc_label" in refs:
                    refs["desc_label"].configure(
                        fg=C_TEXT if state.get("enabled", False) else C_TEXT_DIM)
                if "error_label" in refs:
                    self._show_mod_problem(refs["error_label"], state)

                if "update_label" in refs:
                    self._style_mod_action_label(refs["update_label"],
                                                 mod, state, live)
            return

        for w in self._mods_inner.winfo_children():
            w.destroy()
        self._mod_row_vars = {}

        # Wide enough for the longest name beside the longest version word:
        # "unknown" in Spanish or Portuguese clipped at a fixed 210 px.
        import tkinter.font as tkfont
        name_font = tkfont.Font(font=self._font(10, bold=True))
        small = tkfont.Font(font=self._font(9))
        name_w = max(self._px(210),
                     max(name_font.measure(m["name"]) for m in MODS_REGISTRY)
                     + small.measure("★ " + "  " + tr("unknown")) + self._px(10))
        self._mod_built_sections = {}

        for mod in self._mod_build_list(mods_sorted, mods_cfg):
            if isinstance(mod, tuple):
                self._mod_section_header(*mod)
                continue
            mid   = mod["id"]
            state = mods_cfg.get(mid, {})
            live  = next((m for m in self._mods_state if m["id"] == mid), {})

            latest_ver  = self._mod_version_text(state, live)
            # Checkbox reflects only what's actually recorded in config — never
            # a registry default. Pending (not-yet-applied) UI changes still
            # win so an in-progress toggle survives a background re-render.
            enabled     = self._mod_pending_state.get(mid, {}).get("enabled", state.get("enabled", False))
            ignore_upd  = state.get("ignore_updates", False)
            essential   = mod.get("essential", False)
            installed   = mod_has_files(state)
            # Installed mods are highlighted green; the name is neutral text
            # otherwise (error state overrides to red in the refresh paths).
            name_col    = C_MOD_HL if installed else C_TEXT

            container = tk.Frame(self._mods_inner, bg=C_PANEL)
            container.pack(fill="x")
            self._mod_built_sections[mid] = self._mod_section(mod, state)

            row = tk.Frame(container, bg=C_PANEL)
            row.pack(fill="x", pady=self._px(5))

            name_f = tk.Frame(row, bg=C_PANEL, width=name_w)
            name_f.pack(side="left", fill="y")
            name_f.pack_propagate(False)
            # Essential mods get a gold star badge; a fixed-width slot keeps
            # the names aligned whether or not the star is present.
            star = tk.Label(name_f, text="★" if essential else "",
                            font=self._font(9), fg=C_GOLD, bg=C_PANEL,
                            width=2, anchor="w")
            star.pack(side="left")
            if essential:
                self._add_tooltip(star, tr("Essential mod"))
            name_label = tk.Label(name_f, text=mod["name"],
                                  font=self._font(10, bold=True),
                                  fg=name_col, bg=C_PANEL, anchor="w")
            name_label.pack(side="left")
            ver_label = tk.Label(name_f, text=f"  {latest_ver}",
                                 font=self._font(9), fg=C_TEXT_DIM, bg=C_PANEL)
            ver_label.pack(side="left")

            # No on/off box: the section says what a mod is, and its icons do
            # the rest -- download to install, the bin to uninstall. The
            # variable stays for the code that still reads a row's state.
            enabled_var = tk.BooleanVar(value=enabled)

            # Right-side widgets are packed first so they stay pinned to the
            # panel's right edge; the description then fills the middle.
            ignore_var = tk.BooleanVar(value=ignore_upd)
            ig_f = tk.Frame(row, bg=C_PANEL)
            ig_f.pack(side="right", padx=(self._px(8), 0))
            tk.Checkbutton(ig_f, variable=ignore_var,
                           bg=C_PANEL, activebackground=C_PANEL,
                           fg=C_TEXT, selectcolor=C_PANEL,
                           highlightthickness=0, bd=0,
                           relief="flat", cursor="hand2",
                           command=lambda m=mid, v=ignore_var: self._set_ignore(m, v)
                           ).pack(side="left")
            tk.Label(ig_f, text=tr("Ignore updates"),
                     font=self._font(9), fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")

            # Uninstall, stop managing, then the repository: in that order
            # from the right edge, as on an addon row.
            client = self._game_path.get().strip()
            here = bool(client) and bool(mod_found_files(mod, client))
            if mod.get("custom"):
                self._action_icon(row, "remove", tr("Uninstall"),
                                  lambda m=mid: self._remove_custom_mod(m))
            elif here:
                self._action_icon(row, "remove", tr("Uninstall"),
                                  lambda m=mid: self._mod_uninstall(m))
            if here and state.get("managed") and not mod.get("custom"):
                self._action_icon(row, "release", tr("Stop managing. Your "
                                                     "files are kept."),
                                  lambda m=mid: self._mod_release(m))
            if not here:
                self._action_icon(row, "download", tr("Download from repository"),
                                  lambda m=mid: self._mod_install(m))
            self._action_icon(row, "link", tr("Open repository in browser"),
                              lambda u=mod["repo_url"]: self._open_url(u))

            update_label = tk.Label(row, text=tr("update"),
                                    font=self._font(10, bold=True),
                                    fg=C_GOLD, bg=C_PANEL, cursor="hand2")
            update_label.bind("<Button-1>",
                              lambda e, m=mid: self._mod_action_click(m))
            # Right-click is the deliberate, out-of-the-way gesture for the
            # deliberate, destructive action: install the source's version
            # over whatever is here, downgrade included. Never reachable by
            # a stray left-click on a label that reads "local newer".
            update_label.bind("<Button-3>",
                              lambda e, m=mid: self._mod_force_replace(m))
            update_label.bind("<Enter>", lambda e, l=update_label:
                              l.configure(fg=getattr(l, "_hover", C_GOLD_LT)))
            update_label.bind("<Leave>", lambda e, l=update_label:
                              l.configure(fg=getattr(l, "_base", C_GOLD)))
            self._style_mod_action_label(update_label, mod, state, live)

            desc_label = tk.Label(row, text=tr(mod["description"]),
                                  font=self._font(10),
                                  fg=(C_TEXT if enabled else C_TEXT_DIM),
                                  bg=C_PANEL, wraplength=self._px(400),
                                  justify="left", anchor="w")
            desc_label.pack(side="left", fill="x", expand=True)
            # Wrapped to the room the row leaves it, not a fixed width: a
            # status word on the right ("update to v1.20.4") would otherwise
            # sit on top of the text's last words. Measured from the row and
            # the widgets beside the text -- never from the text itself, whose
            # size follows its wrap, which would feed back into itself.
            def fit_desc(_e=None, w=desc_label, r=row):
                try:
                    others = sum(c.winfo_reqwidth() for c in r.pack_slaves()
                                 if c is not w)
                    want = max(self._px(120), min(
                        self._px(400), r.winfo_width() - others - self._px(30)))
                    if abs(int(w.cget("wraplength")) - want) > self._px(2):
                        w.configure(wraplength=want)
                except tk.TclError:
                    pass
            row.bind("<Configure>", fit_desc, add="+")

            error_label = tk.Label(container, text="",
                                   font=self._font(9), fg=C_ERR,
                                   bg=C_PANEL, anchor="w", padx=self._px(16))
            if state.get("error"):
                name_label.configure(fg=C_ERR)
            self._show_mod_problem(error_label, state)

            divider = tk.Frame(self._mods_inner, bg=C_DIVIDER, height=self._px(1))
            divider.pack(fill="x", pady=(self._px(2), 0))

            self._mod_row_vars[mid] = {
                "enabled":      enabled_var,
                "ignore":       ignore_var,
                "ver_label":    ver_label,
                "name_label":   name_label,
                "desc_label":   desc_label,
                "error_label":  error_label,
                "update_label": update_label,
                "icons":        self._mod_icon_state(mod, state),
            }





    def _show_mod_problem(self, label, state: dict):
        """The line under a mod row: an install that failed (red; PLAY waits
        for it), or an update that failed with the old files kept (it does
        not hold PLAY back -- the mod still works)."""
        if state.get("error"):
            label.configure(text=f"  \u26a0  {tr(state['error'])}", fg=C_ERR)
        elif state.get("update_error"):
            label.configure(
                text="  \u26a0  " + tr("Update failed: {reason}. Your current "
                                       "files were kept.",
                                       reason=tr(state["update_error"])),
                fg=C_GOLD)
        else:
            label.pack_forget()
            return
        label.pack(fill="x", pady=(0, self._px(4)))



    def _refresh_mods_badge(self):
        try:
            count = self._count_mod_updates()
        except Exception:
            count = 0
        if count != self._mod_updates_count:
            self._mod_updates_count = count
            self._draw_nav_tab("MODS")
        self._refresh_update_all_btn()







    def _refresh_apply_btn_visibility(self):
        """Apply is offered only when there is something to apply: pending
        checkbox changes, or a failed mod the user may want to retry."""
        self._refresh_manage_all()
        has_error = any(bool(s.get("error"))
                        for s in load_config().get("mods", {}).values())
        if self._mod_pending_state or has_error:
            if not self._apply_btn.winfo_ismapped():
                self._apply_btn.pack(side="left")
        else:
            self._apply_btn.pack_forget()

    def _open_url(self, url: str):
        try:
            platforms.open_url(url)
        except OSError as e:
            self._log_line(f"Could not open {url}: {e}\n", "err")


    def _style_mod_action_label(self, lbl, mod, state, live, plan_=None):
        """Drive the per-row action label from the mod's plan.

        Every state the planner can reach has an entry here or is hidden.
        The important half is what is *not* offered: local-newer, modified
        and unmanaged mods get a word and a tooltip, not a button, because
        there is nothing safe for a click to do."""
        if state.get("error"):
            lbl._base, lbl._hover = C_GOLD, C_GOLD_LT
            lbl._mode = "act"
            lbl.configure(text=tr("retry"), fg=C_GOLD)
            # The error itself is on the line under the row.
            self._set_tooltip(lbl, tr("Try again"))
            lbl.pack(side="right", padx=(self._px(2), self._px(8)))
            return

        client = self._game_path.get().strip()
        p = plan_ or mod_plan(mod, state, live, client)
        entry = self._MOD_ACTIONS.get(p.status)
        if not entry:
            lbl.pack_forget()
            return

        text, mode, tip = entry
        text, tip = tr(text), tr(tip)
        if p.status is Status.UPDATE_AVAILABLE and p.remote:
            text = tr("update to {version}", version=p.remote)
            tip = f"{p.local} \u2192 {p.remote}"
        elif p.status is Status.LOCAL_NEWER:
            # Not the generic tooltip as well: it says the same thing.
            tip = (tr("Installed {local}; the configured source offers "
                      "{remote}.", local=p.local, remote=p.remote)
                   + " " + tr("EqUpdater will not replace it.") + "\n\n"
                   + tr("Right-click to install {version} anyway.",
                        version=p.remote))
        elif p.status is Status.MODIFIED:
            tip = tip + "\n\n" + tr("Right-click to replace them anyway.")
        elif p.status is Status.UNVERIFIABLE and \
                p.detail.get("cause") == CAUSE_LOOKUP:
            # The source did not answer. Not the files' fault, and not the
            # same thing as an unknown version: say why, and offer to ask
            # again.
            look = MOD_LOOKUPS.get(mod["id"]) or {}
            text, mode = tr("can't check"), "retry"
            tip = (tr("Could not check the latest release: {reason}.",
                      reason=look.get("reason")
                      or tr("the source did not answer"))
                   + "\n\n" + tr("Nothing is changed. Click to check again."))
        elif p.status is Status.UNVERIFIABLE:
            tip = (tr("Installed {local}; the configured source offers "
                      "{remote}.", local=p.local, remote=p.remote)
                   + " " + tip + "\n\n"
                   + tr("Right-click to install {version} anyway.",
                        version=p.remote))
        elif p.status is Status.VERSION_UNKNOWN:
            tip = (tip + "\n\n"
                   + tr("Click to replace them with {version}. You are asked first.",
                        version=p.remote))
        elif p.status is Status.MISSING_FILES and not p.local:
            # Some files are gone and nothing says which build the rest are:
            # putting the set back means writing over them, so it asks first.
            mode = "recover"

        colour = {"act": C_GOLD, "adopt": C_MOD_HL, "recover": C_GOLD,
                  "retry": C_GOLD}.get(mode, C_TEXT_DIM)
        lbl._base = colour
        # An informational label does not brighten under the cursor. Looking
        # interactive is a promise, and there is nothing here to click.
        lbl._hover = {"act": C_GOLD_LT, "adopt": C_GOLD_LT,
                      "recover": C_GOLD_LT, "retry": C_GOLD_LT}.get(mode, colour)
        lbl._mode = mode
        lbl.configure(text=text, fg=colour,
                      cursor="hand2" if mode in ("act", "adopt", "recover",
                                                 "retry") else "arrow")
        self._set_tooltip(lbl, tip)
        lbl.pack(side="right", padx=(self._px(2), self._px(8)))

    def _mod_action_click(self, mod_id: str):
        """What the row's label does, which depends on what it says.

        An informational label does nothing at all when clicked. That is
        deliberate: a label that reads "local newer" and then downgrades the
        mod on click would be the original bug wearing a warning label."""
        refs = self._mod_row_vars.get(mod_id, {})
        lbl = refs.get("update_label")
        mode = getattr(lbl, "_mode", "act") if lbl else "act"
        if mode == "adopt":
            self._adopt_mod(mod_id)
        elif mode == "act":
            self._update_mod(mod_id)
        elif mode == "recover":
            self._mod_force_replace(mod_id)
        elif mode == "retry":
            self._mod_recheck(mod_id)




    # ── bringing in what another tool installed ──────────────────────────────




    def _after_identify(self):
        self._render_mod_rows()
        self._refresh_mods_badge()
        self._addons_verified_ts = 0.0
        self._addons_verify(force=False)

    def _offer_manage_found(self):
        """Ask once per game folder, when the addons have been looked at and
        there is something to ask about. The rows and the "Manage all" links
        stay for anybody who says no."""
        if os.environ.get("EQUPDATER_NO_MIGRATION_PROMPT"):
            return
        client = self._game_path.get().strip()
        if not client or load_config().get("found_offered_for") == client:
            return
        if (getattr(self, "_settings_overlay", None) is not None or self._running
                or getattr(self, "_asking", False)):
            self.after(3000, self._offer_manage_found)
            return
        if not self._found_mods() and not self._found_addons():
            return
        update_config(lambda c: c.__setitem__("found_offered_for", client))
        self._asking = True
        try:
            self._manage_all_found()
        finally:
            self._asking = False

    def _refresh_manage_all(self):
        """The MODS tab's "Manage all" link: there while there is something
        to manage."""
        lbl = getattr(self, "_mods_manage_all", None)
        if lbl is None:
            return
        count = len(self._found_mods())
        if count:
            lbl.configure(text=tr("Manage all ({count})", count=count))
            lbl.pack(side="left", padx=(self._px(10), 0))
        else:
            lbl.pack_forget()


    def _addon_is_commit(self, client_dir: str, folder: str, git: str,
                         sha: str, fp: str) -> bool | None:
        """Whether an addon's files are exactly what commit ``sha`` unpacks
        to. Unpacked into a scratch folder; the game folder is not touched.
        None when it could not be downloaded."""
        import tempfile
        staging = os.path.join(APP_DATA_DIR, "staging")
        ensure_dir(staging)
        scratch = tempfile.mkdtemp(prefix="identify-addon-", dir=staging)
        try:
            install_addon_files(scratch, folder, git, sha)
            return folder_hash(os.path.join(addons_path(scratch), folder)) == fp
        except Exception as e:
            log(f"  Could not compare {folder}: {describe_install_error(e)}")
            return None
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


    def _apply_mods(self):
        out = self._game_path.get().strip()
        if not out:
            return
        self._apply_btn.configure(text=tr("Applying…"), bg="#2a2a32", fg=C_TEXT_DIM)
        self._set_btn_busy(tr("Installing…"))
        self._status_var.set(tr("Downloading mods…"))
        threading.Thread(target=self._apply_mods_worker,
                         args=(out,), daemon=True).start()



    def _ask_essential_mods(self, mods: list) -> bool:
        """The one-time first-run question. Records that it was asked, and
        the answer, so it is never asked again; Settings changes it later."""
        from tkinter import messagebox
        names = "\n".join("    • %s" % m["name"] for m in mods)
        yes = messagebox.askyesno(
            tr("Install essential mods?"),
            tr("{app} can install these client mods:\n\n{mods}\n\n"
               "They are DLLs the game loads at start-up, downloaded from each "
               "mod's own release page. Nothing is installed unless you say "
               "yes.\n\nYou can change this later in Settings → Install "
               "essential mods, or pick mods one at a time on the MODS "
               "tab.\n\nInstall them now?", app=branding.APP_NAME, mods=names),
            parent=self)

        def _merge(c):
            c["essential_mods_asked"] = True
            c["auto_install_mods"] = bool(yes)
        self._cfg = update_config(_merge)
        self._auto_mods_var.set(bool(yes))
        return bool(yes)



    def _mods_finished(self, fresh_mods):
        """Everything the Tk window does when an Apply run ends: repaint the
        rows in place, the Apply button, badge, chained steps. The logic-only
        part is in core.mods_ops; a Qt host implements its own."""
        if self._mod_icons_stale(fresh_mods):
            self._rebuild_mod_rows()
        for mod in MODS_REGISTRY:
            mid         = mod["id"]
            state       = fresh_mods.get(mid, {})
            refs        = self._mod_row_vars.get(mid, {})
            if not refs:
                continue
            has_error   = bool(state.get("error"))
            installed   = mod_has_files(state)

            if mid not in self._mod_pending_state:
                refs["enabled"].set(state.get("enabled", False))
                refs["ignore"].set(state.get("ignore_updates", False))

            live = next((m for m in self._mods_state if m["id"] == mid), {})
            if "ver_label" in refs:
                refs["ver_label"].configure(
                    text=f"  {self._mod_version_text(state, live)}")

            if "name_label" in refs:
                refs["name_label"].configure(
                    fg=C_ERR if has_error else (C_MOD_HL if installed else C_TEXT))

            if "desc_label" in refs:
                refs["desc_label"].configure(
                    fg=C_TEXT if state.get("enabled", False) else C_TEXT_DIM)

            if "error_label" in refs:
                self._show_mod_problem(refs["error_label"], state)

            if "update_label" in refs:
                self._style_mod_action_label(refs["update_label"],
                                             mod, state, live)

        self._apply_btn.configure(text="Apply", bg=C_PANEL_BDR, fg=C_TEXT)
        self._refresh_apply_btn_visibility()
        self._refresh_mods_badge()
        # Fresh setup chain: once the default mods finished installing,
        # the recommended addons follow (no-op if already initialized).
        self._maybe_install_default_addons()
        # Update all: the mods half is done; the addons half follows.
        if self._update_all_chain:
            self.after(0, self._chain_addon_update_all)

        # Any mod in an error state (download blocked, API limit, AV
        # deleted the archive, …) — bring the MODS tab up so the error
        # is visible. PLAY stays disabled via _refresh_ready_state.
        if any(bool(s.get("error")) for s in fresh_mods.values()):
            self._switch_tab("MODS")
        self._refresh_ready_state()


    def _new_bool_var(self, value=False):
        return tk.BooleanVar(master=self, value=value)

    # ── dialog hooks used by the lifted logic (core/mods_ops, addons_ops) ──
    def _ask_yes_no(self, title, body, **kw):
        from tkinter import messagebox
        return messagebox.askyesno(title, body, parent=self, **kw)

    def _ask_ok_cancel(self, title, body, **kw):
        from tkinter import messagebox
        return messagebox.askokcancel(title, body, parent=self, **kw)

    def _show_info(self, title, body, **kw):
        from tkinter import messagebox
        return messagebox.showinfo(title, body, parent=self, **kw)

    def _show_error(self, title, body, **kw):
        from tkinter import messagebox
        return messagebox.showerror(title, body, parent=self, **kw)

    # ── addons panel ─────────────────────────────────────────────────────────

    def _build_addons_panel(self):
        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR,
                         highlightcolor=C_PANEL_BDR)
        self._addons_panel_frame = outer

        top = tk.Frame(outer, bg=C_PANEL)
        top.pack(fill="x", padx=self._px(16), pady=(self._px(4), 0))
        self._addon_filter_var = tk.StringVar()
        self._addon_filter_job = None
        self._addon_filter_var.trace_add(
            "write", self._on_addon_filter_changed)
        ent = tk.Entry(top, textvariable=self._addon_filter_var,
                       bg="#070a17", fg=C_TEXT, insertbackground=C_GOLD,
                       relief="flat", font=self._font(10), width=24,
                       highlightthickness=1,
                       highlightbackground=C_PANEL_BDR,
                       highlightcolor=C_GOLD)
        tk.Label(top, text="⌕", font=self._font(18),
                 fg=C_TEXT, bg=C_PANEL).pack(side="right")
        ent.pack(side="right", ipady=self._px(4), padx=(0, self._px(6)))

        legend = tk.Frame(top, bg=C_PANEL)
        legend.pack(side="left")
        before, after = tr("Addons marked with {star} are recommended").split("{star}")
        tk.Label(legend, text=before,
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")
        tk.Label(legend, text="★", font=self._font(10),
                 fg=C_GOLD, bg=C_PANEL).pack(side="left")
        tk.Label(legend, text=after,
                 font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL).pack(side="left")

        list_frame = tk.Frame(outer, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True, padx=(self._px(16), self._px(4)))
        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL,
                           width=self._px(10))
        self._wheel_canvases.append(canvas)
        self._addons_canvas = canvas
        self._addons_win    = None
        canvas.bind("<Configure>",
                    self._list_stretch(canvas, lambda: self._addons_win))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._reset_addons_inner()

        tk.Frame(outer, bg=C_DIVIDER, height=self._px(1)).pack(
            fill="x", padx=self._px(16), pady=(self._px(4), 0))
        foot = tk.Frame(outer, bg=C_PANEL)
        foot.pack(fill="x", padx=self._px(16), pady=(self._px(8), self._px(12)))
        foot.columnconfigure(0, weight=1)
        foot.columnconfigure(1, weight=1)
        foot.columnconfigure(2, weight=1)

        chk = tk.Label(foot, text="⟳  " + tr("Check for updates"),
                       font=self._font(10), fg=C_TEXT_DIM, bg=C_PANEL,
                       cursor="hand2")
        chk.grid(row=0, column=0, sticky="w")
        chk.bind("<Button-1>", lambda e: self._addons_verify(force=True))
        chk.bind("<Enter>", lambda e: chk.configure(fg=C_GOLD))
        chk.bind("<Leave>", lambda e: chk.configure(fg=C_TEXT_DIM))

        add = tk.Label(foot, text="+  " + tr("Add custom git addon"),
                       font=self._font(10, bold=True), fg="#d76f9e",
                       bg=C_PANEL, cursor="hand2")
        add.grid(row=0, column=1)
        add.bind("<Button-1>", lambda e: self._open_custom_addon_dialog())
        add.bind("<Enter>", lambda e: add.configure(fg="#eb96ba"))
        add.bind("<Leave>", lambda e: add.configure(fg="#d76f9e"))

        self._addons_right_lbl = tk.Label(foot, text="",
                                          font=self._font(10, bold=True),
                                          bg=C_PANEL, cursor="hand2")
        self._addons_right_lbl.grid(row=0, column=2, sticky="e")
        self._addons_right_lbl.bind("<Button-1>",
                                    lambda e: self._addon_update_all())

        self._render_addons()

    # ── MPQ panel ────────────────────────────────────────────────────────────

    def _build_mpq_panel(self):
        outer = tk.Frame(self, bg=C_PANEL,
                         highlightthickness=1,
                         highlightbackground=C_PANEL_BDR,
                         highlightcolor=C_PANEL_BDR)
        self._mpq_panel_frame = outer

        # speech.MPQ is an MPQ too, so its setting lives on this tab rather
        # than in Settings -> General.
        foot = tk.Frame(outer, bg=C_PANEL)
        foot.pack(side="bottom", fill="x", padx=self._px(16),
                  pady=(0, self._px(10)))
        tk.Frame(foot, bg=C_DIVIDER, height=self._px(1)).pack(
            fill="x", pady=(0, self._px(8)))
        cb_ignore_speech = tk.Checkbutton(
            foot, text=" " + tr("Ignore speech.mpq"),
            variable=self._ignore_speech_var,
            command=self._toggle_ignore_speech,
            font=self._font(10), fg=C_TEXT, bg=C_PANEL,
            activebackground=C_PANEL, activeforeground=C_TEXT,
            selectcolor="#0f0b16", highlightthickness=0, bd=0,
            cursor="hand2")
        cb_ignore_speech.pack(anchor="w")
        self._add_tooltip(
            cb_ignore_speech,
            tr("Ignores verification and updates for speech.mpq, allowing custom speech sounds."))

        list_frame = tk.Frame(outer, bg=C_PANEL)
        list_frame.pack(fill="both", expand=True,
                        padx=(self._px(16), self._px(4)),
                        pady=(self._px(8), self._px(8)))
        canvas = tk.Canvas(list_frame, bg=C_PANEL, highlightthickness=0)
        sb = SlimScrollbar(list_frame, command=canvas.yview, bg=C_PANEL,
                           width=self._px(10))
        self._wheel_canvases.append(canvas)
        self._mpq_canvas = canvas
        self._mpq_win    = None
        canvas.bind("<Configure>",
                    self._list_stretch(canvas, lambda: self._mpq_win))
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._mpq_sections_open = {"INSTALLED": True, "AVAILABLE": True}
        self._mpq_installed_rows = []
        self._mpq_available_rows = list(MPQ_PATCHES)
        self._reset_mpq_inner()
        self._render_mpq()

    def _reset_mpq_inner(self):
        cv  = self._mpq_canvas
        old = getattr(self, "_mpq_inner", None)
        if old is not None:
            old.destroy()
        inner = tk.Frame(cv, bg=C_PANEL)
        inner.bind("<Configure>",
                   lambda e: cv.configure(scrollregion=cv.bbox("all")))
        if self._mpq_win is None:
            self._mpq_win = cv.create_window((0, 0), window=inner, anchor="nw",
                                             width=cv.winfo_width() or 1)
        else:
            cv.itemconfigure(self._mpq_win, window=inner)
        cv.yview_moveto(0)
        self._mpq_inner = inner

    def _scan_mpq_patches(self) -> list:
        """Lettered patch archives (patch-<letter>.mpq, case-insensitive) present in the
        game's Data folder, e.g. Patch-A.mpq / patch-b.MPQ. Sorted by letter."""
        out = self._game_path.get().strip()
        data_dir = gamepaths.game_path(out, "Data") if out else ""
        found = []
        if data_dir and os.path.isdir(data_dir):
            for name in os.listdir(data_dir):
                if re.fullmatch(r"patch-[a-z]\.mpq", name, re.IGNORECASE):
                    found.append(name)
        found.sort(key=str.lower)
        return found

    @staticmethod
    def _mpq_record(records: dict, filename: str):
        """A pack's record, matched case-insensitively: Windows does not care
        whether the file is patch-O.mpq or Patch-O.MPQ, so neither may this."""
        lc = filename.lower()
        return next((r for k, r in (records or {}).items()
                     if k.lower() == lc and isinstance(r, dict)), None)

    @staticmethod
    def _mpq_save_record(filename: str, record):
        """Write (or, with None, drop) one pack's record. Safe from a worker
        thread: update_config holds the config lock."""
        def _merge(c):
            recs = c.setdefault("mpq", {})
            for k in [k for k in recs if k.lower() == filename.lower()]:
                del recs[k]
            if record is not None:
                recs[filename] = record
        update_config(_merge)

    def _mpq_check(self):
        """Scan Data/ for lettered patches and ask each *linked* pack's source
        what it publishes now. Feeds the INSTALLED/AVAILABLE lists and the
        tab badge; the source checks run in the background so the list
        appears immediately.

        A pack nobody linked is not checked at all. Its file name matching
        one of ours is not evidence of where it came from, and comparing it
        with a source it may have nothing to do with would produce exactly
        the confident wrong answer this updater exists to avoid."""
        installed_files = self._scan_mpq_patches()
        installed_lc    = {f.lower() for f in installed_files}
        records = load_config().get("mpq", {}) or {}
        rows = []
        for f in installed_files:
            rec = self._mpq_record(records, f)
            src = mpq.source_of(rec, f)
            rows.append({"file": f, "entry": mpq_patch_for(f), "record": rec,
                         "source": src,
                         "state": None if src else "unmanaged",
                         "update": False})
        self._mpq_installed_rows = rows
        self._mpq_available_rows = [
            e for e in MPQ_PATCHES if e["file"].lower() not in installed_lc]
        self._render_mpq()

        out = self._game_path.get().strip()
        data_dir = gamepaths.game_path(out, "Data") if out else ""
        linked = [r for r in rows if r["source"]]
        if not (linked and data_dir):
            self._mpq_updates_count = 0
            self._draw_nav_tab("MPQ")
            return

        def worker():
            results = {}
            for row in linked:
                name, rec = row["file"], row["record"]
                try:
                    local = sha256_file(os.path.join(data_dir, name))
                except OSError:
                    results[name] = ("unverifiable", False)
                    continue
                try:
                    remote, failed = resolve_mpq_source(row["source"], name), False
                except Exception:
                    # Could not ask. Not an update, not an error the user has
                    # to clear - just an unknown, and unknowns are left alone.
                    remote, failed = None, True
                results[name] = mpq.judge(rec, local, remote, failed)
                settled = mpq.settle(rec, local, remote)
                if settled:
                    self._mpq_save_record(name, settled)

            def apply():
                count = 0
                for row in self._mpq_installed_rows:
                    state = results.get(row["file"])
                    if state:
                        row["state"], row["update"] = state
                        count += bool(row["update"])
                self._mpq_updates_count = count
                self._draw_nav_tab("MPQ")
                self._render_mpq()
            self.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    def _render_mpq(self):
        if not hasattr(self, "_mpq_inner"):
            return
        self._reset_mpq_inner()
        installed = getattr(self, "_mpq_installed_rows", [])
        available = getattr(self, "_mpq_available_rows", [])
        busy = getattr(self, "_mpq_busy", None)
        busy_text = getattr(self, "_mpq_busy_text", tr("Downloading…"))

        self._mpq_section_header(N_("INSTALLED"), installed)
        if self._mpq_sections_open.get("INSTALLED", True):
            for row in installed:
                entry  = row["entry"]
                src    = row["source"]
                fname  = row["file"]
                name   = entry["name"] if entry else fname
                desc   = tr(entry["description"]) if entry else ""
                state  = row.get("state")
                button = None
                if row["update"] and src:
                    button = (tr("Update"), lambda r=row, n=name: self._mpq_fetch(
                        r["file"], r["source"], n))
                if src:
                    source_line = (tr("Source: {source}",
                                      source=mpq.describe_source(src)),
                                   None, None)
                else:
                    source_line = (tr("Source: not linked"), tr("Link source…"),
                                   lambda f=fname: self._open_mpq_link_dialog(f))
                # Credit only a pack known to be the listed one; a file that
                # merely shares its name is not evidence of who made it.
                listed = bool(entry and src and src.get("kind") == "catalogue")
                self._mpq_row(name, fname, desc,
                              busy=busy_text if busy == fname else None,
                              button=button, state=state,
                              replace=(lambda r=row: self._mpq_replace(r))
                              if src and state in mpq.REPLACEABLE else None,
                              source_line=source_line,
                              credit=entry if listed else None,
                              uninstall=lambda f=fname: self._mpq_uninstall(f),
                              release=(lambda f=fname: self._mpq_unlink(f))
                              if src else None)

        self._mpq_section_header(N_("AVAILABLE"), available)
        if self._mpq_sections_open.get("AVAILABLE", True):
            for entry in available:
                self._mpq_row(entry["name"], entry["file"],
                              tr(entry["description"]), credit=entry,
                              busy=busy_text if busy == entry["file"] else None,
                              button=(tr("Install"), lambda en=entry: self._mpq_fetch(
                                  en["file"], mpq.catalogue_source(en["file"]),
                                  en["name"])))

    def _mpq_section_header(self, title: str, rows: list):
        f = self._mpq_inner
        is_open = self._mpq_sections_open.get(title, True)
        hdr = tk.Frame(f, bg=C_PANEL)
        hdr.pack(fill="x", pady=(self._px(10), self._px(2)))
        arrow = tk.Label(hdr, text="▾" if is_open else "▸",
                         font=self._font(14, bold=True),
                         fg=C_GOLD, bg=C_PANEL, cursor="hand2", width=2)
        arrow.pack(side="left")
        lbl = tk.Label(hdr, text=tr(title), font=self._font(12, bold=True),
                       fg=C_GOLD, bg=C_PANEL, cursor="hand2")
        lbl.pack(side="left")
        tk.Label(hdr, text=f"  {len(rows)}", font=self._font(10),
                 fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")

        def toggle(_e=None, t=title):
            self._mpq_sections_open[t] = \
                not self._mpq_sections_open.get(t, True)
            self._render_mpq()
        arrow.bind("<Button-1>", toggle)
        lbl.bind("<Button-1>", toggle)

        if is_open and not rows:
            tk.Label(f, text=tr("Nothing here."), font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(anchor="w",
                                                     padx=self._px(8))

    #: MPQ state -> (label, colour, tooltip). A patch MPQ carries no version,
    #: so "differs from what the source ships today" is the only comparison
    #: available -- and it is equally true of an older pack, a newer one, and
    #: one somebody made themselves. These say which is which instead of
    #: calling all three out of date and offering to replace them.
    _MPQ_STATES = {
        "modified":      (N_("Modified locally"), "#d4b43c",
                          N_("This file has changed since EqUpdater installed "
                          "or linked it. Automatic updates are held back.")),
        "unverifiable":  (N_("Unable to verify"), None,
                          N_("The source could not be checked, so nothing is "
                          "replaced.")),
        "sourceDiffers": (N_("Differs from source"), "#d4b43c",
                          N_("This is not the file its source publishes now. An MPQ carries no version, so it could be older, newer or edited – it is left alone. Replace installs the source's copy and keeps yours as a .bak file.")),
        "unconfirmed":   (N_("Source not confirmed"), None,
                          N_("The source publishes no checksum, so EqUpdater "
                          "cannot prove this file is its copy. Replace "
                          "installs the source's copy, keeps yours as a "
                          ".bak file, and tracks updates from then on.")),
    }

    def _mpq_row(self, name, filename, description, busy=None, button=None,
                 state=None, replace=None, source_line=None, credit=None,
                 uninstall=None, release=None):
        """One patch row: name (and filename), then on the right either a
        busy label, a button, or the state with its explanation; the
        description underneath; and for installed packs, where the pack
        comes from and the way to change that.

        `button` is (label, callback). `replace` is a callback offered as a
        quiet "Replace…" beside a held-back state. `source_line` is (text,
        link label, callback); the link may be None. `uninstall` and
        `release` add the bin and the grey × at the right edge."""
        f = self._mpq_inner
        box = tk.Frame(f, bg=C_PANEL)
        box.pack(fill="x", pady=(self._px(4), 0))
        # The same divider as an Addons row, under everything this pack says.
        tk.Frame(f, bg=C_DIVIDER, height=self._px(1)).pack(
            fill="x", pady=(self._px(3), 0))
        top = tk.Frame(box, bg=C_PANEL)
        top.pack(fill="x")
        tk.Label(top, text=name, font=self._font(11, bold=True),
                 fg=C_TEXT, bg=C_PANEL, anchor="w").pack(side="left",
                                                         padx=self._px(8))
        if filename and filename.lower() != name.lower():
            tk.Label(top, text=f"  {filename}", font=self._font(9),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")
        if not busy:
            if uninstall:
                self._action_icon(top, "remove", tr("Uninstall"), uninstall,
                                  padx=(self._px(8), self._px(8)))
            if release:
                self._action_icon(top, "release", tr("Stop managing. Your "
                                                     "files are kept."),
                                  release, padx=(self._px(2), 0))
        if busy:
            tk.Label(top, text=busy, font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(side="right",
                                                     padx=self._px(8))
        elif button:
            label, callback = button
            btn = tk.Label(top, text=label, font=self._font(10, bold=True),
                           fg="#000", bg=C_GOLD, cursor="hand2",
                           padx=self._px(12), pady=self._px(2))
            btn.pack(side="right", padx=self._px(8))
            btn.bind("<Button-1>", lambda e, cb=callback: cb())
            btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD_LT))
            btn.bind("<Leave>", lambda e: btn.configure(bg=C_GOLD))
        elif state in self._MPQ_STATES:
            text, colour, tip = self._MPQ_STATES[state]
            text, tip = tr(text), tr(tip)
            if replace:
                rep = tk.Label(top, text=tr("Replace…"), font=self._font(10),
                               fg=C_TEXT_DIM, bg=C_PANEL, cursor="hand2")
                rep.pack(side="right", padx=self._px(8))
                rep.bind("<Button-1>", lambda e, cb=replace: cb())
                rep.bind("<Enter>", lambda e, w=rep: w.configure(fg=C_ERR))
                rep.bind("<Leave>", lambda e, w=rep: w.configure(fg=C_TEXT_DIM))
                self._set_tooltip(
                    rep, tr("Install the source's copy over this one. You "
                            "are asked first, and your file is kept."))
            lbl = tk.Label(top, text=text, font=self._font(10),
                           fg=colour or C_TEXT_DIM, bg=C_PANEL)
            lbl.pack(side="right", padx=self._px(8))
            self._set_tooltip(lbl, tip)
        if description:
            tk.Label(box, text=description, font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL, anchor="w",
                     wraplength=self._px(620), justify="left").pack(
                     fill="x", padx=self._px(8), pady=(self._px(2), 0))
        if credit and (credit.get("author") or credit.get("homepage")):
            # "By <author> · Project page": who makes the pack, and where.
            line = tk.Frame(box, bg=C_PANEL)
            line.pack(fill="x", padx=self._px(8), pady=(self._px(2), 0))
            if credit.get("author"):
                tk.Label(line, text=tr("By {author}", author=credit["author"]),
                         font=self._font(9), fg=C_TEXT_DIM,
                         bg=C_PANEL).pack(side="left")
            if credit.get("homepage"):
                sep = "  ·  " if credit.get("author") else ""
                page = tk.Label(line, text=sep + tr("Project page"),
                                font=self._font(9, bold=True), fg=C_MOD_HL,
                                bg=C_PANEL, cursor="hand2")
                page.pack(side="left")
                page.bind("<Button-1>",
                          lambda e, u=credit["homepage"]: self._open_url(u))
                page.bind("<Enter>", lambda e, w=page: w.configure(fg=C_GOLD_LT))
                page.bind("<Leave>", lambda e, w=page: w.configure(fg=C_MOD_HL))
                self._set_tooltip(page, credit["homepage"])
        if source_line:
            text, link_text, callback = source_line
            line = tk.Frame(box, bg=C_PANEL)
            line.pack(fill="x", padx=self._px(8), pady=(self._px(2), 0))
            tk.Label(line, text=text, font=self._font(9), fg=C_TEXT_DIM,
                     bg=C_PANEL, anchor="w").pack(side="left")
            if not busy and link_text:
                link = tk.Label(line, text="  ·  " + link_text,
                                font=self._font(9, bold=True), fg=C_MOD_HL,
                                bg=C_PANEL, cursor="hand2")
                link.pack(side="left")
                link.bind("<Button-1>", lambda e, cb=callback: cb())
                link.bind("<Enter>", lambda e, w=link: w.configure(fg=C_GOLD_LT))
                link.bind("<Leave>", lambda e, w=link: w.configure(fg=C_MOD_HL))
                if state == "unmanaged":
                    # "Source: not linked" says it; this says why it matters.
                    self._set_tooltip(link, tr(
                        "EqUpdater did not install this pack and does not "
                        "know where it came from, so it cannot check it for "
                        "updates. Linking its source changes no files."))

    def _mpq_start(self, filename: str, text: str) -> str | None:
        """Mark one pack busy and return the Data folder, or None if another
        pack is busy or there is no game folder."""
        if getattr(self, "_mpq_busy", None):
            return None                              # one at a time
        out = self._game_path.get().strip()
        if not out:
            self._log_line("Set the game folder first.\n", "err")
            return None
        self._mpq_busy, self._mpq_busy_text = filename, text
        self._render_mpq()
        return gamepaths.game_path(out, "Data")

    def _mpq_finish(self):
        self._mpq_busy = None
        self._mpq_check()                            # re-scan + re-check + badge

    def _mpq_fetch(self, filename: str, source: dict, label: str,
                   replace: bool = False):
        """Install, update or replace one pack from `source`.

        An ordinary update only goes ahead if the file is still exactly what
        was recorded -- it may have changed since the row was drawn -- and
        then overwrites it, because it is the source's own older copy. A
        replace is the user overruling a held-back state after a warning,
        and keeps their file beside the new one."""
        data_dir = self._mpq_start(filename, tr("Downloading…"))
        if data_dir is None:
            return
        self._log_line(f"\nDownloading {label}…\n", "acct")
        path = os.path.join(data_dir, filename)

        def worker():
            err = None
            try:
                if not replace and os.path.exists(path):
                    rec = self._mpq_record(load_config().get("mpq", {}), filename)
                    if not (rec and rec.get("sha")
                            and sha256_file(path) == rec["sha"]):
                        raise RuntimeError(
                            "the file changed since it was checked; "
                            "nothing was replaced")
                remote = resolve_mpq_source(source, filename)
                sha = download_mpq(remote, data_dir, filename,
                                   keep_existing=replace)
                # Ours now, and this is the fingerprint of what we wrote -
                # which is what lets a later run tell an edited pack from an
                # untouched one, instead of calling both "out of date".
                self._mpq_save_record(filename, mpq.installed_record(
                    sha=sha, source=source, remote=remote,
                    installed_by=branding.APP_NAME))
            except Exception as e:
                err = str(e)

            def done():
                if err:
                    self._log_line(f"✗  {label} failed: {err}\n", "err")
                else:
                    self._log_line(f"✓  {label} installed.\n", "ok")
                self._mpq_finish()
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _mpq_replace(self, row):
        from tkinter import messagebox
        fname = row["file"]
        if not messagebox.askyesno(
                tr("Replace {name}?", name=fname),
                tr("{file} will be replaced with the copy from:\n    {source}\n\n"
                   "Your current file is not deleted. It is renamed to\n"
                   "    {file}.<date>.bak\n"
                   "in the Data folder, where the game ignores it. Rename it "
                   "back to undo.\n\nReplace it?",
                   file=fname, source=mpq.describe_source(row["source"])),
                parent=self):
            return
        entry = row.get("entry")
        self._mpq_fetch(fname, row["source"],
                        entry["name"] if entry else fname, replace=True)

    def _mpq_uninstall(self, filename: str):
        """Delete a pack from Data, after asking, and forget its record."""
        from tkinter import messagebox
        out = self._game_path.get().strip()
        if not out or getattr(self, "_mpq_busy", None):
            return
        if not messagebox.askyesno(
                tr("Uninstall {name}?", name=filename),
                tr("{file} is deleted from the game's Data folder.",
                   file=filename), parent=self):
            return
        try:
            os.remove(os.path.join(gamepaths.game_path(out, "Data"), filename))
        except FileNotFoundError:
            pass
        except OSError as e:
            self._log_line(f"Could not uninstall {filename}: {e}\n", "err")
            return
        self._mpq_save_record(filename, None)
        self._log_line(f"\n{filename} uninstalled.\n", "acct")
        self._mpq_check()

    def _mpq_unlink(self, filename: str):
        """Forget where a pack came from. The file stays exactly where it is;
        it simply stops being checked, as if it had been installed by hand."""
        self._mpq_save_record(filename, None)
        self._log_line("\n%s is no longer linked to a source; the file is "
                       "untouched.\n" % filename, "acct")
        self._mpq_check()

    def _mpq_link(self, filename: str, source: dict):
        """Record that `filename` comes from `source`. **Changes no file.**

        The source is asked what it publishes now and the pack is
        fingerprinted as it is. If the two agree, the pack is tracked from
        here like one EqUpdater installed; if they do not, the link is kept
        and the pack is held as differing from its source. If the source
        cannot be reached, nothing is recorded: a link that was never
        checked would read as one that was."""
        data_dir = self._mpq_start(filename, tr("Checking…"))
        if data_dir is None:
            return
        where = mpq.describe_source(source)

        def worker():
            err, rec = None, None
            try:
                local = sha256_file(os.path.join(data_dir, filename))
                remote = resolve_mpq_source(source, filename)
                rec = mpq.linked_record(sha=local, source=source, remote=remote)
                self._mpq_save_record(filename, rec)
            except Exception as e:
                err = str(e)

            def done():
                if err:
                    self._log_line("\n✗  Could not link %s to %s: %s\n"
                                   "Nothing was changed.\n"
                                   % (filename, where, err), "err")
                else:
                    self._log_line("\n%s is now linked to %s. Its file was "
                                   "not changed.\n%s\n"
                                   % (filename, where, mpq.link_outcome(rec)),
                                   "acct")
                self._mpq_finish()
            self.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _open_mpq_link_dialog(self, filename: str):
        """Ask where a manually installed pack comes from: one of the packs
        EqUpdater lists, or a link the user pastes."""
        if self._settings_overlay is not None:
            return
        ov = tk.Frame(self, bg="#0a0a0e")
        ov.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        ov.bind("<Button-1>", lambda e: self._close_settings())
        self._settings_overlay = ov
        self.bind("<Escape>", lambda e: self._close_settings())

        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        MW = self._px(600)
        MH = self._px(260 + 28 * len(MPQ_PATCHES))
        panel = tk.Frame(ov, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        panel.place(relx=0.5, rely=0.5, anchor="center", y=-self._px(20),
                    width=MW, height=MH)

        hdr = tk.Frame(panel, bg=P_HDR, height=self._px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("LINK TEXTURE PACK SOURCE"),
                 font=self._font(13, bold=True),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=self._px(18))
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=self._px(16))
        x_btn.bind("<Button-1>", lambda e: self._close_settings())
        x_btn.bind("<Enter>",    lambda e: x_btn.configure(fg=C_TEXT))
        x_btn.bind("<Leave>",    lambda e: x_btn.configure(fg=C_TEXT_DIM))
        tk.Frame(panel, bg=P_BDR, height=self._px(1)).pack(fill="x")

        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=self._px(22),
                  pady=(self._px(14), self._px(12)))
        tk.Label(body, text=tr("Where does {file} come from?", file=filename),
                 font=self._font(10, bold=True), fg=C_GOLD,
                 bg=P_BG).pack(anchor="w")

        match = mpq_patch_for(filename)
        choice = tk.StringVar(value=("cat:" + match["file"]) if match else "url")

        def radio(text, value):
            tk.Radiobutton(body, text=" " + text, variable=choice, value=value,
                           font=self._font(10), fg=C_TEXT, bg=P_BG,
                           activebackground=P_BG, activeforeground=C_TEXT,
                           selectcolor=P_INP, highlightthickness=0, bd=0,
                           cursor="hand2").pack(anchor="w",
                                                pady=(self._px(6), 0))
        for entry in MPQ_PATCHES:
            radio(tr("{name} ({file}) from the {app} list",
                     name=entry["name"], file=entry["file"],
                     app=branding.APP_NAME),
                  "cat:" + entry["file"])
        radio(tr("Somewhere else:"), "url")

        url_var = tk.StringVar()
        ent = tk.Entry(body, textvariable=url_var, bg=P_INP, fg=C_TEXT,
                       insertbackground=C_GOLD, relief="flat", font=FONT_MONO,
                       highlightthickness=1, highlightbackground=P_BDR,
                       highlightcolor=C_GOLD)
        ent.pack(fill="x", ipady=self._px(6), pady=(self._px(4), self._px(4)))
        ent.bind("<FocusIn>", lambda e: choice.set("url"))
        tk.Label(body,
                 text=tr("A GitHub, Codeberg or OctoWoW Git repository or "
                         "release download, or a dl.octowow.st link. Linking "
                         "changes no files."),
                 font=self._font(9), fg=C_TEXT_DIM, bg=P_BG,
                 wraplength=MW - self._px(50), justify="left").pack(anchor="w")
        err = tk.Label(body, text="", font=self._font(9), fg=C_ERR, bg=P_BG,
                       wraplength=MW - self._px(50), justify="left")
        err.pack(anchor="w")

        def submit():
            value = choice.get()
            if value.startswith("cat:"):
                source = mpq.catalogue_source(value[4:])
            else:
                try:
                    source = mpq.parse_source(url_var.get())
                except ValueError as e:
                    err.configure(text=str(e))
                    return
            self._close_settings()
            self._mpq_link(filename, source)

        btn = tk.Label(body, text=tr("Link"), font=self._font(11, bold=True),
                       fg=C_TEXT, bg=P_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(7))
        btn.pack(anchor="e", pady=(self._px(6), 0))
        btn.bind("<Button-1>", lambda e: submit())
        btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD, fg="#000"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=P_BDR, fg=C_TEXT))

    # ── addons engine (app side) ─────────────────────────────────────────────

    def _addon_dir_conflict_prompt(self, client: str) -> bool:
        """When Interface/AddOns and another case of it (Interface/Addons)
        both hold different copies of the same addon: ask, once per
        session, which copies to keep. The others are moved aside, never
        deleted. Returns True when the scan should wait (the player chose
        to decide later), False to go on."""
        try:
            state = gamepaths.prepare_addons_dir(client)
        except OSError as e:
            self._log_line(f"Could not check the addons folder: {e}\n", "err")
            return False
        _log_addon_dir_state(state)
        if not state.conflicts or getattr(self, "_addon_conflict_asked", False):
            return False
        self._addon_conflict_asked = True
        from tkinter import messagebox
        other = os.path.dirname(state.conflicts[0][1])
        names = "\n".join("    " + c[0] for c in state.conflicts)
        choice = messagebox.askyesnocancel(
            tr("Two addon folders"),
            tr("This game folder has two addon folders whose names differ only "
               "in letter case:\n    {other}\n    {addons}\n\nEverything that "
               "was in only one of them is now in {addons}. These addons are "
               "in both, with different files:\n{names}\n\nYes: keep the "
               "copies from {other}.\nNo: keep the copies in {addons}.\n"
               "Cancel: decide later. Until then {app} installs, updates and "
               "removes no addons.\n\nThe copies you do not keep are moved "
               "into an AddOns-conflicts folder next to them, not deleted.",
               other=other, addons=state.path, names=names,
               app=branding.APP_NAME),
            icon="warning", parent=self)
        if choice is None:
            self._log_line("Addon changes are paused until the two addon "
                           "folders are resolved (ADDONS tab, Check for "
                           "updates, asks again after a restart).\n", "err")
            return False
        try:
            aside, moved = gamepaths.resolve_conflicts(
                client, "other" if choice else "addons")
        except OSError as e:
            self._log_line(f"Could not resolve the addon folders: {e}\n", "err")
            return False
        _ADDON_CONFLICT_LOGGED[:] = []
        for entry in moved:
            self._log_line(f"Moved {entry} aside to {aside}\n", "ok")
        return False






    # ── custom mods ──────────────────────────────────────────────────────────

    def _rebuild_mod_rows(self):
        """Rows from scratch: a mod was added to or taken out of the list."""
        self._mod_row_vars = {}
        self._render_mod_rows()
        self._refresh_mods_badge()
        self._refresh_apply_btn_visibility()

    def _open_custom_mod_dialog(self):
        """Add a mod EqUpdater does not list: one .dll, .zip or .mpq from a
        repository's releases. The link is checked, the release's files are
        offered, and nothing is installed until the player confirms the
        warning. A ZIP is downloaded and looked through first, so the
        warning can say what it puts where."""
        if self._settings_overlay is not None:
            return
        ov = tk.Frame(self, bg="#0a0a0e")
        ov.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        ov.bind("<Button-1>", lambda e: self._close_settings())
        self._settings_overlay = ov
        self.bind("<Escape>", lambda e: self._close_settings())

        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        MW, MH = self._px(580), self._px(380)
        panel = tk.Frame(ov, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        panel.place(relx=0.5, rely=0.5, anchor="center", y=-self._px(20),
                    width=MW, height=MH)
        panel.bind("<Button-1>", lambda e: "break")

        hdr = tk.Frame(panel, bg=P_HDR, height=self._px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("ADD CUSTOM GIT MOD"),
                 font=self._font(13, bold=True),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=self._px(18))
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=self._px(16))
        x_btn.bind("<Button-1>", lambda e: self._close_settings())
        tk.Frame(panel, bg=P_BDR, height=self._px(1)).pack(fill="x")

        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=self._px(22),
                  pady=(self._px(14), self._px(12)))
        tk.Label(body, text=tr("RELEASE LINK"), font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w")
        url_var = tk.StringVar()
        url_entry = tk.Entry(body, textvariable=url_var, bg=P_INP, fg=C_TEXT,
                             insertbackground=C_GOLD, relief="flat",
                             font=FONT_MONO, highlightthickness=1,
                             highlightbackground=P_BDR, highlightcolor=C_GOLD)
        url_entry.pack(fill="x", ipady=self._px(7), pady=(self._px(6), self._px(4)))
        tk.Label(body, text=tr("A GitHub, Codeberg or OctoWoW Git repository, "
                               "or one of its release downloads."),
                 font=self._font(9), fg=C_TEXT_DIM, bg=P_BG,
                 wraplength=MW - self._px(44), justify="left").pack(anchor="w")
        tk.Label(body, text="⚠  " + tr("A mod runs code inside the game. "
                                       "Only add mods you trust."),
                 font=self._font(9, bold=True), fg=C_GOLD, bg=P_BG,
                 wraplength=MW - self._px(44), justify="left"
                 ).pack(anchor="w", pady=(self._px(6), 0))
        status = tk.Label(body, text="", font=self._font(9), fg=C_ERR, bg=P_BG,
                          wraplength=MW - self._px(44), justify="left")
        status.pack(anchor="w", pady=(self._px(4), 0))
        choices = tk.Frame(body, bg=P_BG)
        choices.pack(fill="x", anchor="w")
        btn = tk.Label(body, text=tr("Find mod files"),
                       font=self._font(11, bold=True), fg=C_TEXT, bg=P_BDR,
                       cursor="hand2", padx=self._px(16), pady=self._px(7))
        btn.pack(side="bottom", anchor="e")
        btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD, fg="#000"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=P_BDR, fg=C_TEXT))

        st = {"src": None, "dlls": [], "release": None, "busy": False}
        choice = tk.StringVar()
        self._custom_mod_ui = {"url": url_var, "status": status,
                               "choice": choice, "state": st,
                               "find": lambda: find(), "install": lambda: install()}

        def reset(*_a):
            st["src"], st["dlls"], st["release"] = None, [], None
            choice.set("")
            for w in choices.winfo_children():
                w.destroy()
            btn.configure(text=tr("Find mod files"))
            status.configure(text="")
        url_var.trace_add("write", reset)

        def show(src, release, error):
            st["busy"] = False
            if not ov.winfo_exists():
                return
            if error:
                status.configure(text=tr("Could not read the releases: {error}",
                                         error=tr(error)), fg=C_ERR)
                return
            dlls = release_mod_files(release)
            if src.get("asset"):
                dlls = [d for d in dlls if d.lower() == src["asset"].lower()]
            if not dlls:
                status.configure(text=tr("The latest release has no .dll, "
                                         ".zip or .mpq file to install. An "
                                         "addon with no releases is added "
                                         "in Addons: Add custom git addon."),
                                 fg=C_ERR)
                return
            st["src"], st["dlls"], st["release"] = src, dlls, release
            choice.set(dlls[0])
            status.configure(text=tr("Latest release: {tag}. Choose the "
                                     "file to install:",
                                     tag=(release or {}).get("tag_name") or "?"),
                             fg=C_OK)
            for d in dlls:
                tk.Radiobutton(choices, text=d, value=d, variable=choice,
                               font=self._font(10), fg=C_TEXT, bg=P_BG,
                               activebackground=P_BG, activeforeground=C_TEXT,
                               selectcolor=P_INP, highlightthickness=0,
                               anchor="w").pack(anchor="w")
            btn.configure(text=tr("Install"))

        def find():
            if st["busy"]:
                return
            try:
                src = parse_mod_release_link(url_var.get())
            except ValueError as e:
                status.configure(text=tr(str(e)), fg=C_ERR)
                return
            st["busy"] = True
            status.configure(text=tr("Looking up the latest release…"),
                             fg=C_TEXT_DIM)

            def worker():
                try:
                    rel, err = _release_latest(src, raise_errors=True), None
                except Exception as e:
                    rel, err = None, str(e)
                self.after(0, lambda: show(src, rel, err))
            threading.Thread(target=worker, daemon=True).start()

        def install():
            name = choice.get()
            if not st["src"] or not name:
                return find()
            if name.lower().endswith(".zip"):
                return inspect_zip(name)
            if name.lower().endswith(".mpq"):
                dest = f"Data/{name}"
                client = self._game_path.get().strip()
                if client and os.path.exists(os.path.join(client, dest)):
                    status.configure(text=tr("Data already has a file named "
                                             "{name}.", name=name), fg=C_ERR)
                    return
                return finish(name, [dest], None)
            problem = custom_mod_conflict(name, st["src"])
            if problem:
                status.configure(text=tr(problem), fg=C_ERR)
                return
            if not self._confirm_custom_mod(st["src"], name):
                return
            add(name, lambda: add_custom_mod(st["src"], name), name)

        def inspect_zip(name):
            """Download the ZIP and read where its files go, off the UI
            thread; finish() then asks."""
            if st["busy"]:
                return
            asset = next((a for a in (st["release"] or {}).get("assets") or []
                          if a.get("name") == name), None)
            if asset is None:
                return find()
            st["busy"] = True
            status.configure(text=tr("Looking inside {name}…", name=name),
                             fg=C_TEXT_DIM)

            def worker():
                import io
                import zipfile
                files, err = None, None
                try:
                    req = urllib.request.Request(
                        asset["browser_download_url"],
                        headers={"User-Agent": UA})
                    with secure_urlopen(req, timeout=120,
                                        allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
                        data = r.read()
                    with zipfile.ZipFile(io.BytesIO(data)) as zf:
                        files = [d for _, d in mod_zip_plan(zf.namelist())]
                except ValueError as e:
                    err = tr(str(e))
                except Exception as e:
                    err = tr("Could not read {name}: {error}", name=name,
                             error=e)

                def done():
                    st["busy"] = False
                    if not ov.winfo_exists():
                        return
                    if err:
                        status.configure(text=err, fg=C_ERR)
                        return
                    status.configure(text="", fg=C_OK)
                    finish(name, files, mod_zip_dll(files))
                self.after(0, done)
            threading.Thread(target=worker, daemon=True).start()

        def finish(name, files, dll):
            """A ZIP or MPQ mod: checked, confirmed, added."""
            problem = custom_files_conflict(files, st["src"])
            if problem:
                status.configure(text=tr(problem), fg=C_ERR)
                return
            if not self._confirm_custom_asset_mod(st["src"], name, files, dll):
                return
            add(os.path.basename(dll) if dll else None,
                lambda: add_custom_asset_mod(st["src"], name, files, dll), name)

        def add(dll, record, name):
            """Add the mod (``record()``) and install it. ``dll`` is the
            file it loads, if any."""
            # Another build of the same DLL that is on: it goes off in the
            # same Apply that puts this one in, or this one is not added.
            twins_on = [m for m in MODS_REGISTRY
                        if dll and mod_dll_name(m) == dll.lower()
                        and self._mod_is_on(m["id"])]
            if twins_on and not self._confirm_replace_twins(
                    f"{_custom_mod_host(st['src'])}/{st['src']['owner']}/"
                    f"{st['src']['repo']}", dll, twins_on):
                return
            try:
                mid = record()
            except ValueError as e:
                status.configure(text=tr(str(e)), fg=C_ERR)
                return
            self._close_settings()
            self._log_line(f"\nAdded custom mod {name} from "
                           f"{_custom_mod_host(st['src'])}/{st['src']['owner']}/"
                           f"{st['src']['repo']}.\n", "acct")
            self._rebuild_mod_rows()
            if twins_on:
                for m in twins_on:
                    self._mod_pending_state.setdefault(
                        m["id"], {})["enabled"] = False
                self._mod_pending_state.setdefault(mid, {})["enabled"] = True
                if self._game_path.get().strip():
                    self._apply_mods()
            elif self._game_path.get().strip():
                self._update_mod(mid)

        def click():
            install() if st["src"] else find()
        btn.bind("<Button-1>", lambda e: click())
        url_entry.bind("<Return>", lambda e: click())
        url_entry.focus_set()


    def _confirm_custom_mod(self, src: dict, dll: str) -> bool:
        from tkinter import messagebox
        where = f"{_custom_mod_host(src)}/{src['owner']}/{src['repo']}"
        return messagebox.askyesno(
            tr("Install {name}?", name=dll),
            tr("{dll} from {source} will be installed to the mods folder and "
               "loaded into the game every time it starts.\n\n"
               "A mod runs code inside the game, with the same access to "
               "this computer as the game itself. {app} does not check what "
               "it does. Only install mods you trust.\n\n"
               "Install it?", dll=dll, source=where, app=branding.APP_NAME),
            icon="warning", parent=self)

    def _confirm_custom_asset_mod(self, src: dict, name: str, files: list,
                                  dll: str | None) -> bool:
        """The warning for a ZIP or MPQ mod: what it puts where, and what it
        loads into the game, if anything."""
        from tkinter import messagebox
        where = f"{_custom_mod_host(src)}/{src['owner']}/{src['repo']}"
        def top(f):
            # An addon by its folder; anything else by its top folder.
            parts = f.split("/")
            depth = 3 if [p.lower() for p in parts[:2]] == ["interface",
                                                            "addons"] else 1
            return "/".join(parts[:depth]) + ("/" if len(parts) > depth else "")
        tops = sorted({top(f) for f in files}, key=str.lower)
        shown = "\n".join("  " + t for t in tops[:8])
        if len(tops) > 8:
            shown += "\n  " + tr("and {count} more", count=len(tops) - 8)
        loads = (tr("It loads {dll} into the game every time it starts.",
                    dll=dll) if dll else
                 tr("It loads no .dll: the game reads its files as it would "
                    "an addon's or a patch's."))
        return messagebox.askyesno(
            tr("Install {name}?", name=name),
            tr("{name} from {source} puts {count} files into the game "
               "folder:\n\n{where}\n\n{loads}\n\n"
               "A mod can change how the game behaves, and {app} does not "
               "check what it does. Only install mods you trust.\n\n"
               "Install it?", name=name, source=where, count=len(files),
               where=shown, loads=loads, app=branding.APP_NAME),
            icon="warning", parent=self)




    def _mod_section_header(self, title: str, count: int):
        """A section's title and count, opening and closing it -- drawn as
        the Addons tab draws its own."""
        f = self._mods_inner
        self._mod_sections_open = getattr(self, "_mod_sections_open", {})
        is_open = self._mod_sections_open.get(title, True)
        hdr = tk.Frame(f, bg=C_PANEL)
        hdr.pack(fill="x", pady=(self._px(10), self._px(2)))
        arrow = tk.Label(hdr, text="▾" if is_open else "▸",
                         font=self._font(14, bold=True),
                         fg=C_GOLD, bg=C_PANEL, cursor="hand2", width=2)
        arrow.pack(side="left")
        lbl = tk.Label(hdr, text=tr(title), font=self._font(12, bold=True),
                       fg=C_GOLD, bg=C_PANEL, cursor="hand2")
        lbl.pack(side="left")
        tk.Label(hdr, text=f"  {count}", font=self._font(10),
                 fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")
        if title == "INSTALLED LOCALLY" and count and self._found_mods():
            every = tk.Label(hdr, text=tr("Manage all ({count})",
                                          count=len(self._found_mods())),
                             font=self._font(10, bold=True), fg=C_MOD_HL,
                             bg=C_PANEL, cursor="hand2")
            every.pack(side="right", padx=self._px(4))
            every.bind("<Button-1>", lambda _e: self._manage_all_found())
            every.bind("<Enter>", lambda _e, w=every: w.configure(fg=C_GOLD_LT))
            every.bind("<Leave>", lambda _e, w=every: w.configure(fg=C_MOD_HL))

        def toggle(_e=None, t=title):
            self._mod_sections_open[t] = not self._mod_sections_open.get(t, True)
            self._rebuild_mod_rows()
        arrow.bind("<Button-1>", toggle)
        lbl.bind("<Button-1>", toggle)
        if is_open and not count:
            tk.Label(f, text=tr("Nothing here."), font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(anchor="w",
                                                     padx=self._px(8))






    def _remove_custom_mod(self, mod_id: str):
        """Take a custom mod out of the list: its file is deleted, its
        dlls.txt line removed, and its record forgotten."""
        from tkinter import messagebox
        mod = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
        if mod is None or not mod.get("custom"):
            return
        if not messagebox.askyesno(
                tr("Remove {name}?", name=mod["name"]),
                tr("{name} is deleted from the mods folder, taken out of "
                   "dlls.txt and removed from this list.", name=mod["name"])
                if not mod["source"].get("asset_pattern", "").lower().endswith(
                    (".zip", ".mpq")) else
                tr("The files {name} put into the game folder are deleted, "
                   "and it is removed from this list.", name=mod["name"]),
                parent=self):
            return
        client = self._game_path.get().strip()
        if client:
            try:
                uninstall_mod(mod, client)
                if mod.get("register_dll"):
                    remove_dll(client, mod["register_dll"])
            except OSError as e:
                self._log_line(f"Could not remove {mod['name']}: {e}\n", "err")
                return
        forget_custom_mod(mod_id)
        self._mod_pending_state.pop(mod_id, None)
        self._log_line(f"\nRemoved custom mod {mod['name']}.\n", "dim")
        self._rebuild_mod_rows()

    def _open_custom_addon_dialog(self):
        if self._settings_overlay is not None:
            return
        ov = tk.Frame(self, bg="#0a0a0e")
        ov.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        ov.bind("<Button-1>", lambda e: self._close_settings())
        self._settings_overlay = ov
        self.bind("<Escape>", lambda e: self._close_settings())

        # Same purple-dark theme as the Settings modal.
        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        MW, MH = self._px(560), self._px(230)
        panel = tk.Frame(ov, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        panel.place(relx=0.5, rely=0.5, anchor="center", y=-self._px(20),
                    width=MW, height=MH)

        hdr = tk.Frame(panel, bg=P_HDR, height=self._px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("ADD CUSTOM GIT ADDON"),
                 font=self._font(13, bold=True),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=self._px(18))
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=self._px(16))
        x_btn.bind("<Button-1>", lambda e: self._close_settings())
        x_btn.bind("<Enter>",    lambda e: x_btn.configure(fg=C_TEXT))
        x_btn.bind("<Leave>",    lambda e: x_btn.configure(fg=C_TEXT_DIM))
        tk.Frame(panel, bg=P_BDR, height=self._px(1)).pack(fill="x")

        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=self._px(22),
                  pady=(self._px(16), self._px(12)))
        tk.Label(body, text=tr("REPOSITORY URL"),
                 font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w")
        url_var = tk.StringVar()
        url_entry = tk.Entry(body, textvariable=url_var, bg=P_INP, fg=C_TEXT,
                             insertbackground=C_GOLD, relief="flat",
                             font=FONT_MONO, highlightthickness=1,
                             highlightbackground=P_BDR, highlightcolor=C_GOLD)
        url_entry.pack(fill="x", ipady=self._px(7),
                       pady=(self._px(6), self._px(6)))
        tk.Label(body,
                 text=tr("Allowed hosts: {hosts}",
                         hosts=", ".join(ADDON_GIT_HOSTS)),
                 font=self._font(9), fg=C_TEXT_DIM, bg=P_BG).pack(anchor="w")
        err = tk.Label(body, text="", font=self._font(9),
                       fg=C_ERR, bg=P_BG)
        err.pack(anchor="w")
        self._custom_addon_status = err

        # Checked as it is typed: a link that will work says so in green,
        # and a message from an earlier attempt never outlives the text it
        # was about. Half-typed links are not scolded; Install says why.
        def live(*_a):
            _url, _folder, problem = check_custom_addon_url(url_var.get())
            if problem is None:
                err.configure(text=tr("Valid URL. Click Install to continue."),
                              fg=C_OK)
            else:
                err.configure(text="", fg=C_ERR)
        url_var.trace_add("write", live)

        def submit():
            url, folder, problem = check_custom_addon_url(url_var.get())
            if problem is not None:
                err.configure(text=tr(problem), fg=C_ERR)
                return
            self._close_settings()
            self._log_line(f"\nInstalling custom addon {folder}…\n", "acct")
            self._addon_apply([{"folder": folder, "status": "available",
                                "git": url, "branch": None, "ref": None,
                                "toc": {}, "description": None,
                                "error": None, "custom": True}])

        btn = tk.Label(body, text=tr("Install"), font=self._font(11, bold=True),
                       fg=C_TEXT, bg=P_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(7))
        btn.pack(anchor="e", pady=(self._px(8), 0))
        btn.bind("<Button-1>", lambda e: submit())
        btn.bind("<Enter>", lambda e: btn.configure(bg=C_GOLD, fg="#000"))
        btn.bind("<Leave>", lambda e: btn.configure(bg=P_BDR, fg=C_TEXT))
        url_entry.bind("<Return>", lambda e: submit())
        self._custom_addon_url = url_var
        url_entry.focus_set()

    # ── addons rendering ─────────────────────────────────────────────────────

    def _reset_addons_inner(self):
        """Start a fresh rows container. A single destroy() tears an old
        subtree down inside Tk (C code) -- far faster than destroying
        hundreds of row widgets one by one from Python.

        Once a list is on screen, the new container is built off-screen:
        the old one stays visible until the batched build has finished and
        _addons_show_inner swaps the finished list in, in one step. Built in
        place, every batch was drawn as it came, so expanding or collapsing
        a section showed an emptied list filling back in from the top."""
        cv  = self._addons_canvas
        pending = getattr(self, "_addons_inner", None)
        shown = getattr(self, "_addons_shown", None)
        if pending is not None and pending is not shown:
            pending.destroy()          # an abandoned, never-shown build
        inner = tk.Frame(cv, bg=C_PANEL)
        self._addons_inner = inner
        if self._addons_win is None or shown is None:
            self._addons_show_inner(top=True)

    def _addons_show_inner(self, top: bool = True):
        """Put the built rows container on screen in place of the old one,
        with its scroll region set once, from its finished size."""
        cv = self._addons_canvas
        inner = self._addons_inner
        old = getattr(self, "_addons_shown", None)
        if old is inner:
            return
        first = top or old is None
        y = 0.0 if first else cv.yview()[0]
        inner.update_idletasks()       # lay out before it is ever drawn
        if self._addons_win is None:
            self._addons_win = cv.create_window((0, 0), window=inner,
                                                anchor="nw",
                                                width=cv.winfo_width() or 1)
        else:
            cv.itemconfigure(self._addons_win, window=inner)
        inner.bind("<Configure>",
                   lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.configure(scrollregion=(0, 0, cv.winfo_width() or 1,
                                   inner.winfo_reqheight()))
        cv.yview_moveto(y)
        self._addons_shown = inner
        if old is not None:
            old.destroy()

    def _on_addon_filter_changed(self, *_args):
        """Debounce search input – re-render once typing pauses, not on
        every keystroke."""
        if self._addon_filter_job is not None:
            self.after_cancel(self._addon_filter_job)
        self._addon_filter_job = self.after(250, self._apply_addon_filter)

    def _apply_addon_filter(self):
        self._addon_filter_job = None
        self._render_addons()

    def _render_addons(self, keep_scroll: bool = False):
        """Rebuild the addons list. Rows are created in small batches on the
        Tk event loop so a large catalog doesn't freeze the UI; the finished
        list replaces the shown one in one step (see _reset_addons_inner).
        ``keep_scroll`` keeps the scroll position instead of returning to the
        top: expanding or collapsing a section, and the redraws during and
        after an install, update or removal -- installing several addons from
        a long list must not send the player back to its start each time."""
        if not hasattr(self, "_addons_inner"):
            return
        self._addons_keep_scroll = keep_scroll
        self._addons_render_gen = getattr(self, "_addons_render_gen", 0) + 1
        gen = self._addons_render_gen
        self._reset_addons_inner()

        st  = self._addons_status
        flt = self._addon_filter_var.get().strip().lower()
        flt_compact = flt.replace(" ", "")

        def matches(rec):
            if not flt:
                return True
            title = strip_wow_colors((rec.get("toc") or {}).get("Title") or "")
            hay = f"{rec['folder']} {title}".lower()
            # Space-insensitive both ways: "sell value" finds SellValue,
            # "sellvalue" finds "Sell Value".
            return flt in hay or flt_compact in hay.replace(" ", "")

        def keep(lst):
            lst = sorted(lst, key=lambda r: r["folder"].lower())
            return [r for r in lst if matches(r)]

        # **Grouped by ownership, because ownership is the thing to know.**
        # One "INSTALLED" list mixed addons EqUpdater looks after with addons
        # it merely found, and the rows gave no clue which was which - so an
        # Update button on one and an Update button on the other looked like
        # the same button doing the same thing. They were not.
        every = keep(st["addons"].values())
        managed = [r for r in every if r.get("status") != "unmanaged"]
        unmanaged = [r for r in every if r.get("status") == "unmanaged"]
        # Recommended addons are mixed into Available and marked with a ★
        # badge. Sort recommended first so they surface at the top of the list.
        available = keep(a for a in st["available"]
                         if a["folder"] not in st["addons"])
        available.sort(key=lambda a: (a["folder"] not in RECOMMENDED_ADDONS,
                                      a["folder"].lower()))

        work = []
        for title, rows in ((N_("MANAGED"), managed),
                            (N_("INSTALLED LOCALLY"), unmanaged),
                            (N_("AVAILABLE"), available)):
            work.append(("header", title, rows))
            if self._addon_sections_open.get(title, True):
                work.extend(("row", rec) for rec in rows)
        self._addons_build_queue = work
        self._refresh_addons_footer()
        self._addons_build_step(gen)

    def _addons_build_step(self, gen: int):
        """Create up to one batch of queued headers/rows, then yield to the
        event loop; abandons the queue if a newer render has started."""
        if gen != self._addons_render_gen:
            return
        queue = self._addons_build_queue
        built = 0
        while queue and built < 14:
            item = queue.pop(0)
            if item[0] == "header":
                self._addon_section_header(item[1], item[2])
            else:
                self._addon_row(item[1])
            built += 1
        if queue:
            self.after(1, lambda: self._addons_build_step(gen))
        else:
            self._addons_show_inner(
                top=not getattr(self, "_addons_keep_scroll", False))

    def _addon_section_header(self, title: str, rows: list):
        f = self._addons_inner
        is_open = self._addon_sections_open.get(title, True)

        hdr = tk.Frame(f, bg=C_PANEL)
        hdr.pack(fill="x", pady=(self._px(10), self._px(2)))
        arrow = tk.Label(hdr, text="▾" if is_open else "▸",
                         font=self._font(14, bold=True),
                         fg=C_GOLD, bg=C_PANEL, cursor="hand2", width=2)
        arrow.pack(side="left")
        lbl = tk.Label(hdr, text=tr(title),
                       font=self._font(12, bold=True),
                       fg=C_GOLD, bg=C_PANEL, cursor="hand2")
        lbl.pack(side="left")
        tk.Label(hdr, text=f"  {len(rows)}", font=self._font(10),
                 fg=C_TEXT_DIM, bg=C_PANEL).pack(side="left")
        if title == "INSTALLED LOCALLY" and self._found_addons():
            every = tk.Label(hdr, text=tr("Manage all ({count})",
                                          count=len(self._found_addons())),
                             font=self._font(10, bold=True), fg=C_MOD_HL,
                             bg=C_PANEL, cursor="hand2")
            every.pack(side="right", padx=self._px(4))
            every.bind("<Button-1>", lambda _e: self._manage_all_found())
            every.bind("<Enter>", lambda _e, w=every: w.configure(fg=C_GOLD_LT))
            every.bind("<Leave>", lambda _e, w=every: w.configure(fg=C_MOD_HL))

        def toggle(_e=None, t=title):
            self._addon_sections_open[t] = \
                not self._addon_sections_open.get(t, True)
            self._render_addons(keep_scroll=True)
        arrow.bind("<Button-1>", toggle)
        lbl.bind("<Button-1>", toggle)

        if is_open and not rows:
            msg = (tr("Verifying…") if self._addons_status["state"] == "verifying"
                   else tr("Nothing here."))
            tk.Label(f, text=msg, font=self._font(10), fg=C_TEXT_DIM,
                     bg=C_PANEL).pack(anchor="w", padx=self._px(8))

    def _addon_row(self, rec: dict):
        f = self._addons_inner
        installed = rec["folder"] in self._addons_status["addons"]
        toc = rec.get("toc") or {}

        warnings = []
        if toc.get("Interface") and toc["Interface"] != "11200":
            warnings.append(tr("Made for client {version}",
                               version=toc["Interface"]))
        # pfUI bundles its own modules, so its .toc dependencies aren't real
        # missing addons — never warn about them.
        if installed and rec["folder"] != "pfUI":
            deps = [d.strip() for d in
                    (toc.get("Dependencies") or "").replace(";", ",").split(",")
                    if d.strip()]
            missing = [d for d in deps
                       if d not in self._addons_status["addons"]]
            if missing:
                warnings.append(tr("Missing dependencies: {names}",
                                   names=", ".join(missing)))

        row = tk.Frame(f, bg=C_PANEL)
        row.pack(fill="x", pady=self._px(3))


        # right side first so it stays pinned to the edge
        if installed:
            self._action_icon(row, "remove", tr("Uninstall"),
                              lambda n=rec["folder"]: self._addon_remove(n),
                              padx=(self._px(8), self._px(2)))

            # **Stop managing**, next to Uninstall and deliberately unlike
            # it: one gives up ownership, the other deletes files. Offered
            # only on addons EqUpdater actually owns - there is nothing to
            # release on one it never claimed.
            if rec.get("status") not in ("unmanaged", "available", None):
                self._action_icon(row, "release", tr("Stop managing. Your "
                                                     "files are kept."),
                                  lambda n=rec["folder"]: self._addon_release(n),
                                  padx=(self._px(2), 0))

            # A recommended source that is not the one this was installed
            # from. Offered as a switch, never taken as an update - see
            # _addon_switch_source.
            suggested = rec.get("suggested_git")
            if (suggested and rec.get("git")
                    and not rec.get("custom")
                    and not same_repo(suggested, rec["git"])):
                sw = tk.Label(row, text="⇄", font=self._font(11),
                              fg="#d4b43c", bg=C_PANEL, cursor="hand2")
                sw.pack(side="right", padx=(self._px(2), 0))
                sw.bind("<Button-1>",
                        lambda e, n=rec["folder"], g=suggested:
                        self._addon_switch_source(n, g))
                sw.bind("<Enter>", lambda e, w=sw: w.configure(fg=C_GOLD_LT))
                sw.bind("<Leave>", lambda e, w=sw: w.configure(fg="#d4b43c"))
                self._set_tooltip(
                    sw, tr("A different source is recommended for this "
                           "addon:\n{source}\n\nSwitching forks is a choice, "
                           "not an update, so nothing happens until you ask.",
                           source=suggested))
        else:
            self._action_icon(row, "download", tr("Download from repository"),
                              lambda r=rec: self._addon_apply([dict(r)]),
                              padx=(self._px(8), self._px(2)))

        # Repo link on the right (like the Mods tab), between the status text
        # and the install/remove icon.
        if rec.get("git"):
            repo_url = rec["git"][:-4] if rec["git"].endswith(".git") \
                else rec["git"]
            self._action_icon(row, "link", tr("Open repository in browser"),
                              lambda u=repo_url: self._open_url(u))

        self._addon_row_status(row, rec, installed, warnings)

        # name (WoW colour codes honoured) + repo link
        name_f = tk.Frame(row, bg=C_PANEL, width=self._px(250))
        name_f.pack(side="left", fill="y")
        name_f.pack_propagate(False)
        # Gold ★ badge for recommended addons; fixed-width slot keeps the
        # titles aligned whether or not the star is present.
        is_recommended = rec["folder"] in RECOMMENDED_ADDONS
        star = tk.Label(name_f, text="★" if is_recommended else "",
                        font=self._font(9), fg=C_GOLD, bg=C_PANEL,
                        width=2, anchor="w")
        star.pack(side="left")
        if is_recommended:
            self._add_tooltip(star, tr("Recommended addon"))
        title = toc.get("Title") or rec["folder"]
        for seg, col in parse_wow_colored(title)[:6]:
            tk.Label(name_f, text=seg, font=self._font(10, bold=True),
                     fg=col or C_TEXT, bg=C_PANEL).pack(side="left")

        desc = strip_wow_colors(toc.get("Notes")
                                or rec.get("description") or "")
        tk.Label(row, text=desc, font=self._font(10), fg=C_TEXT_DIM,
                 bg=C_PANEL, wraplength=self._px(430), justify="left",
                 anchor="w").pack(side="left", fill="x", expand=True)

        if rec.get("error"):
            tk.Label(f, text=f"  ⚠  {tr(rec['error'])}",
                     font=self._font(9), fg=C_ERR, bg=C_PANEL,
                     wraplength=self._px(840), justify="left",
                     anchor="w").pack(fill="x", pady=(0, self._px(3)))
        elif rec.get("check_failed"):
            tk.Label(f, text="  ⚠  " + tr(
                         "Could not check the addon's source: {reason}. "
                         "Your installed files are not changed.",
                         reason=tr(rec["check_failed"])),
                     font=self._font(9), fg=C_GOLD, bg=C_PANEL,
                     wraplength=self._px(840), justify="left",
                     anchor="w").pack(fill="x", pady=(0, self._px(3)))

        tk.Frame(f, bg=C_DIVIDER, height=self._px(1)).pack(
            fill="x", pady=(self._px(3), 0))

    #: Addon status -> (label, colour, tooltip). Anything that is not an
    #: offer to act reads as plain text in a muted colour: a state the user
    #: cannot act on is not drawn to look like a button.
    _ADDON_STATES = {
        "localNewer":      (N_("Local version newer"), "#d4b43c",
                            N_("The installed commit is ahead of the source's. "
                            "EqUpdater will not roll it back.")),
        "modified":        (N_("Modified locally"), "#d4b43c",
                            N_("These files differ from the ones EqUpdater "
                            "installed. Automatic updates are held back so "
                            "your changes survive.")),
        "differentSource": (N_("Different source"), "#d4b43c",
                            N_("Installed from a different repository than the "
                            "one now recommended. Switching forks is a "
                            "choice, not an update.")),
        "diverged":        (N_("Different revision"), "#d4b43c",
                            N_("The installed commit and the remote one do not contain each other – another branch, a fork, or rebased history.")),
        "unverifiable":    (N_("Unable to verify"), C_TEXT_DIM,
                            N_("Which side is newer could not be established, "
                            "so nothing is replaced.")),
        "ignored":         (N_("Ignored"), C_TEXT_DIM,
                            N_("Excluded from updates at your request.")),
        "unknown":         (N_("Not versioned"), C_TEXT_DIM, ""),
    }

    def _addon_row_status(self, row, rec, installed, warnings):
        """The right-hand end of an addon row: what it is, and what may be
        done about it.

        Two things here are buttons - Update on a proven newer commit, and
        Manage on something EqUpdater found but does not own. Everything else
        is a statement of fact with a tooltip explaining it, because there is
        nothing safe for a click to do."""
        status = rec.get("status")

        if status == "downloading":
            tk.Label(row, text=tr("downloading…"), font=self._font(10),
                     fg=C_TEXT_DIM, bg=C_PANEL).pack(side="right",
                                                     padx=self._px(4))
            return

        if rec.get("check_failed") and not rec.get("error"):
            btn = tk.Label(row, text=tr("can't check"),
                           font=self._font(10, bold=True), fg=C_GOLD,
                           bg=C_PANEL, cursor="hand2")
            btn.pack(side="right", padx=self._px(4))
            btn.bind("<Button-1>", lambda _e: self._addons_verify(force=True))
            btn.bind("<Enter>", lambda _e, w=btn: w.configure(fg=C_GOLD_LT))
            btn.bind("<Leave>", lambda _e, w=btn: w.configure(fg=C_GOLD))
            self._set_tooltip(btn, tr("Nothing is changed. Click to check again."))
            return

        if status == "invalid" or rec.get("error"):
            # Short marker on the right; the full reason gets its own line
            # under the row (long messages would squeeze the description).
            tk.Label(row, text="⛔ " + tr("Addon error"), font=self._font(10),
                     fg=C_ERR, bg=C_PANEL).pack(side="right", padx=self._px(4))
            return

        if status == "unmanaged":
            # Found on disk. The offer is to *manage* it - which changes
            # nothing now - and never to "update" it, because nothing here
            # establishes that the catalogue copy and this copy are even the
            # same addon.
            if rec.get("suggested_git") or rec.get("git"):
                btn = tk.Label(row, text=tr("Manage"),
                               font=self._font(10, bold=True),
                               fg=C_MOD_HL, bg=C_PANEL, cursor="hand2")
                btn.pack(side="right", padx=self._px(4))
                btn.bind("<Button-1>", lambda e, r=rec: self._addon_adopt(r))
                btn.bind("<Enter>", lambda e, w=btn: w.configure(fg=C_GOLD_LT))
                btn.bind("<Leave>", lambda e, w=btn: w.configure(fg=C_MOD_HL))
                self._set_tooltip(
                    btn, tr("Let {app} update this addon from {source}. Your "
                            "current files are not changed now.",
                            app=branding.APP_NAME,
                            source=rec.get("suggested_git") or rec.get("git")))
            return

        if status == "updateAvailable" and installed:
            upd = tk.Label(row, text=tr("Update"), font=self._font(10, bold=True),
                           fg=C_GOLD, bg=C_PANEL, cursor="hand2")
            upd.pack(side="right", padx=self._px(4))
            upd.bind("<Button-1>", lambda e, r=rec: self._addon_apply([r]))
            upd.bind("<Enter>", lambda e, w=upd: w.configure(fg=C_GOLD_LT))
            upd.bind("<Leave>", lambda e, w=upd: w.configure(fg=C_GOLD))
            if rec.get("installed_sha") and rec.get("remote_sha"):
                self._set_tooltip(upd, "%s → %s"
                                  % (short_sha(rec["installed_sha"]),
                                     short_sha(rec["remote_sha"])))
            return

        if warnings:
            tk.Label(row, text="⚠ %s" % warnings[0],
                     font=self._font(10), fg="#d4b43c",
                     bg=C_PANEL).pack(side="right", padx=self._px(4))
            return

        entry = self._ADDON_STATES.get(status)
        if entry:
            text, colour, tip = entry
            text, tip = tr(text), (tr(tip) if tip else tip)
            # The way out of a held-back state, for somebody who has read
            # why it is held back and wants the remote copy anyway. Offered
            # beside the explanation rather than instead of it, and it warns
            # again before it does anything.
            if addon_offers_replace(rec):
                rep = tk.Label(row, text=tr("Replace…"),
                               font=self._font(10), fg=C_TEXT_DIM,
                               bg=C_PANEL, cursor="hand2")
                rep.pack(side="right", padx=self._px(4))
                rep.bind("<Button-1>",
                         lambda e, r=rec: self._addon_force_replace(r))
                rep.bind("<Enter>", lambda e, w=rep: w.configure(fg=C_ERR))
                rep.bind("<Leave>", lambda e, w=rep: w.configure(fg=C_TEXT_DIM))
                self._set_tooltip(
                    rep, tr("Overwrite this addon with the latest from its "
                            "source. A backup is taken first, and you are "
                            "told exactly what will be lost."))
            lbl = tk.Label(row, text=text, font=self._font(10),
                           fg=colour, bg=C_PANEL)
            lbl.pack(side="right", padx=self._px(4))
            # The planner's reason, only where it adds something: the
            # sources' addresses. Elsewhere it restates the tooltip.
            if rec.get("reason") and status == "differentSource":
                tip = (tip + "\n\n" + tr(str(rec["reason"]))).strip()
            if tip:
                self._set_tooltip(lbl, tip)






    def _refresh_addons_badge(self):
        """Addons that can be safely updated. An unmanaged, modified or
        diverged addon is not a pending task; counting it as one is how a
        badge teaches people to press Update All without reading it."""
        count = sum(1 for r in self._addons_status["addons"].values()
                    if r["status"] == "updateAvailable")
        if count != self._addon_updates_count:
            self._addon_updates_count = count
            self._draw_nav_tab("ADDONS")
        self._refresh_update_all_btn()

    def _refresh_addons_footer(self):
        lbl = self._addons_right_lbl
        st  = self._addons_status
        if st["state"] == "verifying" or self._addons_busy:
            lbl.configure(text=tr("Checking…"), fg=C_TEXT_DIM, cursor="arrow")
        elif any(r["status"] == "updateAvailable"
                 for r in st["addons"].values()):
            lbl.configure(text=tr("Update all"), fg=C_OK, cursor="hand2")
        else:
            # "Nothing to update" rather than "everything up to date": with
            # unmanaged or held-back addons on the page the second is not
            # true, and saying it anyway is how a warning gets ignored.
            held = sum(1 for r in st["addons"].values()
                       if r["status"] in ("localNewer", "modified",
                                          "diverged", "differentSource",
                                          "unverifiable"))
            if held:
                lbl.configure(text=tr("Nothing to update · {count} held back",
                                      count=held),
                              fg=C_TEXT_DIM, cursor="arrow")
            else:
                lbl.configure(text=tr("Everything up to date"), fg=C_TEXT_DIM,
                              cursor="arrow")

    # ── footer ────────────────────────────────────────────────────────────────

    def _build_footer(self):
        """PLAY/UPDATE, the status line, the progress bar and the
        version -- drawn over the bottom of the background, which fades to
        dark there (see _draw_bg), instead of on a solid block. Text is
        canvas text so the gradient shows through around it."""
        c = self._bg_canvas
        self._foot_items = {}

        def text(key, var=None, **kw):
            self._foot_items[key] = c.create_text(0, 0, **kw)
            if var is not None:
                var.trace_add("write", lambda *_a, k=key, v=var:
                              c.itemconfigure(self._foot_items[k], text=v.get()))

        self._status_var = tk.StringVar(value=tr("Ready to update"))
        text("status", self._status_var, text=self._status_var.get(),
             anchor="nw", fill=C_TEXT)
        # An error in the status line is a link (see _status_error): any
        # other text written over it takes the link away again.
        self._status_action = None
        self._status_action_text = None
        self._status_var.trace_add("write", lambda *_a: self._status_changed())
        sid = self._foot_items["status"]
        c.tag_bind(sid, "<Enter>", lambda _e: self._status_hover(True))
        c.tag_bind(sid, "<Leave>", lambda _e: self._status_hover(False))
        c.tag_bind(sid, "<Button-1>", lambda _e: self._status_click())

        # Thin halo frame around the button gives a soft glow that follows
        # the button state (gold for UPDATE, green for PLAY).
        self._btn_mode = "update"
        self._btn_glow = tk.Frame(c, bg="#5a4828")
        self._upd_btn = GradientButton(
            self._btn_glow, width=self._px(132), height=self._px(38),
            text=tr("UPDATE"), palette=UPDATE_GRADIENT,
            command=self._btn_click, bg=C_BG,
            font_factory=lambda: self._font(11, bold=True))
        self._upd_btn.pack(padx=self._px(2), pady=self._px(2))
        self._gradient_buttons.append(self._upd_btn)
        self._foot_items["btn"] = c.create_window(
            0, 0, window=self._btn_glow, anchor="nw")

        self._client_ver_var = tk.StringVar(value="")
        text("client", self._client_ver_var, text="", anchor="nw",
             fill=C_TEXT_DIM)

        self._pb_x0 = self._px(400)
        self._pb_width = WIN_W - self._px(400) - self._px(40)
        self._pb_val = 0.0
        self._pb_y = 0
        self._prog_label_var = tk.StringVar(value="")
        text("progress", self._prog_label_var, text="", anchor="s",
             fill=C_TEXT)

        text("version", text=f"v{UPDATER_VERSION}", anchor="se",
             fill="#6a7690", font=("Courier New", 8))
        self._build_footer_links()
        self._build_update_all_button()
        self._layout_footer()

    def _status_error(self, text: str, action):
        """Show an error in the status line as a link: an arrow after it,
        and a click runs ``action`` -- the log, the message, the tab the
        text points at."""
        shown = f"{text} \u2197"
        self._status_action = action
        self._status_action_text = shown
        self._status_var.set(shown)

    def _status_changed(self):
        if self._status_var.get() != self._status_action_text:
            self._status_action = None
            self._status_action_text = None
            self._status_hover(False)

    def _status_hover(self, inside: bool):
        lit = inside and self._status_action is not None
        try:
            self._bg_canvas.itemconfigure(self._foot_items["status"],
                                          fill=C_GOLD if lit else C_TEXT)
            self._bg_canvas.configure(cursor="hand2" if lit else "")
        except tk.TclError:
            pass

    def _status_click(self):
        if self._status_action is not None:
            self._status_action()

    def _footer_icon_photos(self):
        """The footer's link icons at this scale, {key: (resting, under the
        cursor)}. Each supplied image is trimmed to what it draws, so the two
        logos are the same height whatever transparent margin they came with,
        and keeps its proportions. Rebuilt only when the scale changes."""
        height = self._px(FOOTER_ICON_H)
        if getattr(self, "_footer_icon_height", None) == height:
            return
        from PIL import Image, ImageTk
        photos = {}
        for key, candidates in (("discord", branding.discord_icon_candidates()),
                                ("coffee", branding.coffee_icon_candidates())):
            path = next(p for p in candidates if os.path.exists(p))
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im = im.crop(im.getbbox() or (0, 0, im.width, im.height))
                width = max(1, round(im.width * height / im.height))
                full = im.resize((width, height), Image.Resampling.LANCZOS)
            resting = full.copy()
            resting.putalpha(full.getchannel("A").point(
                lambda a: round(a * FOOTER_ICON_REST)))
            photos[key] = (ImageTk.PhotoImage(resting, master=self),
                           ImageTk.PhotoImage(full, master=self))
        self._footer_icons = photos
        self._footer_icon_height = height

    def _canvas_link(self, key: str, images, tip, on_click):
        """An image on the footer canvas that behaves as a link: the hover
        image and a hand under the cursor, a tooltip above it, ``on_click``
        on a click. ``images`` and ``tip`` are callables, read each time, so a
        rescale or a new count shows at once."""
        c = self._bg_canvas
        item = c.create_image(0, 0, anchor="se", image=images()[0])
        self._foot_items[key] = item
        self._foot_tips = getattr(self, "_foot_tips", {})

        def hide_tip():
            win = self._foot_tips.pop(key, None)
            if win is not None:
                win.destroy()

        def enter(_e=None):
            c.itemconfigure(item, image=images()[1])
            c.configure(cursor="hand2")
            hide_tip()
            win = tk.Toplevel(self)
            win.wm_overrideredirect(True)
            tk.Label(win, text=tip(), font=self._font(9),
                     fg=C_TEXT, bg="#0f0b16", highlightthickness=1,
                     highlightbackground=C_PANEL_BDR, padx=self._px(6),
                     pady=self._px(2)).pack()
            # Above the icon, kept inside the window: the right edge is near.
            win.update_idletasks()
            x0, y0, x1, _y1 = c.bbox(item)
            x = min(x0, c.winfo_width() - win.winfo_reqwidth() - self._px(4))
            win.wm_geometry("+%d+%d" % (
                c.winfo_rootx() + max(0, x),
                c.winfo_rooty() + y0 - win.winfo_reqheight() - self._px(4)))
            self._foot_tips[key] = win

        def leave(_e=None):
            c.itemconfigure(item, image=images()[0])
            c.configure(cursor="")
            hide_tip()

        c.tag_bind(item, "<Enter>", enter)
        c.tag_bind(item, "<Leave>", leave)
        c.tag_bind(item, "<Button-1>", lambda _e: on_click())
        return item, leave

    def _build_footer_links(self):
        """Discord and the Ko-fi cup, side by side in the bottom-right
        corner, above the version. A missing image or Pillow leaves the footer
        as it was."""
        try:
            self._footer_icon_photos()
        except Exception as e:
            log(f"Footer icons not shown: {e}")
            return
        self._canvas_link("discord", lambda: self._footer_icons["discord"],
                          lambda: tr("Discord"),
                          lambda: self._open_url(branding.DISCORD_URL))
        self._canvas_link("coffee", lambda: self._footer_icons["coffee"],
                          lambda: tr("Donate through Ko-Fi!"),
                          lambda: self._open_url(branding.KOFI_URL))

    # ── update everything ────────────────────────────────────────────────────

    def _update_all_photos(self):
        """Two curved arrows chasing each other round a circle -- the usual
        "update" sign -- in the old UPDATE ALL button's colours: its gold
        gradient filling them, and its 1 px border and 2 px glow following
        their outline. Drawn at four times the size and scaled down so the
        edges are smooth: (resting, under the cursor)."""
        h = self._px(UPDALL_ICON_H)
        if getattr(self, "_updall_height", None) == h:
            return
        from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk
        k = 4
        line, glow = max(1, self._px(1)) * k, self._px(2) * k
        pad = line + glow
        import math
        ah = h * k
        W = H = ah + 2 * pad
        arrow = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(arrow)
        c = W / 2
        # A thin band: the 3 px of border and glow round it must not fill
        # the middle of the circle or the gaps between the two arrows.
        thick = ah * 0.11                    # the band's width
        head_w, head_l = 1.7, 1.9            # arrowhead, in band widths
        outer = ah * 0.5 - thick * (head_w - 0.5)   # room for the heads
        mid = outer - thick / 2
        # Angles run clockwise from three o'clock (y grows downwards). One
        # band over the top, left to right; one under, right to left; each
        # ends in a head pointing on round the circle.
        for start, end in ((220, 320), (40, 140)):
            d.arc([c - outer, c - outer, c + outer, c + outer], start, end,
                  fill=255, width=round(thick))
            a = math.radians(end)
            px, py = c + mid * math.cos(a), c + mid * math.sin(a)
            nx, ny = math.cos(a), math.sin(a)          # out from the centre
            tx, ty = -math.sin(a), math.cos(a)         # along the circle
            half, length = thick * head_w, thick * head_l
            d.polygon([(px + nx * half - tx * k, py + ny * half - ty * k),
                       (px - nx * half - tx * k, py - ny * half - ty * k),
                       (px + tx * length, py + ty * length)], fill=255)
        # Rounded, not cut: the arrow's corners softened, and each layer
        # around it grown with round corners. Blurring a mask and cutting it
        # at the middle rounds its corners in place; cutting it low grows it
        # by a set distance (1.5 blur radii at 17/255) with rounded corners.
        def soften(mask, r):
            return mask.filter(ImageFilter.GaussianBlur(r)).point(
                lambda v: 255 if v >= 128 else 0)

        def grow(mask, by):
            return mask.filter(ImageFilter.GaussianBlur(by / 1.5)).point(
                lambda v: 255 if v > 17 else 0)
        arrow = soften(arrow, 0.3 * k)
        bordered = grow(arrow, line)
        glowed = grow(bordered, glow)
        ring = ImageChops.subtract(bordered, arrow)
        halo = ImageChops.subtract(glowed, bordered)

        def gradient(top, bottom):
            a = [int(top[i:i + 2], 16) for i in (1, 3, 5)]
            b = [int(bottom[i:i + 2], 16) for i in (1, 3, 5)]
            col = Image.new("RGB", (1, H))
            for y in range(H):
                t = max(0.0, min(1.0, (y - pad) / max(1, ah)))
                col.putpixel((0, y), tuple(round(a[i] + (b[i] - a[i]) * t)
                                           for i in range(3)))
            return col.resize((W, H))

        out = []
        for (top, bottom), border, opacity in (
                (UPDALL_FILL[0], UPDALL_BORDER[0], UPDALL_REST),
                (UPDALL_FILL[1], UPDALL_BORDER[1], 1.0)):
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            im.paste(Image.new("RGB", (W, H), UPDALL_GLOW), (0, 0), halo)
            im.paste(Image.new("RGB", (W, H), border), (0, 0), ring)
            im.paste(gradient(top, bottom), (0, 0),
                     arrow.point(lambda v, o=opacity: round(v * o)))
            small = im.resize((W // k, H // k), Image.Resampling.LANCZOS)
            out.append(ImageTk.PhotoImage(small, master=self))
        self._updall_images = tuple(out)
        self._updall_height = h

    def _build_update_all_button(self):
        """The arrow right of PLAY: there only while something can be
        updated, a spinner while it is updating."""
        self._update_all_chain = False
        self._update_all_running = False
        self._updall_spin = 0
        try:
            self._update_all_photos()
        except Exception as e:
            log(f"Update-all arrow not shown: {e}")
            return
        _item, self._updall_leave = self._canvas_link(
            "updall", lambda: self._updall_images, self._update_all_tip,
            self._update_all)
        c = self._bg_canvas
        self._foot_items["updall_spin"] = c.create_arc(
            0, 0, 1, 1, start=90, extent=270, style="arc",
            outline=UPDALL_COLOR, width=max(2, self._px(3)), state="hidden")
        c.itemconfigure(self._foot_items["updall"], state="hidden")

    def _update_all_tip(self) -> str:
        count = self._update_all_count()
        if count == 1:
            return tr("Update all addons and mods (1 update available)")
        return tr("Update all addons and mods ({count} updates available)",
                  count=count)

    def _update_all_count(self) -> int:
        return int(self._mod_updates_count) + int(self._addon_updates_count)

    def _refresh_update_all_btn(self):
        """Show the arrow while there is something EqUpdater can prove is
        safe to update, and nothing else is running; a spinner in its place
        while an update-all runs. Called whenever a count or the busy state
        changes, so it follows them without a restart. A failed update leaves
        its count where it was, and with it the arrow, to try again."""
        items = getattr(self, "_foot_items", {})
        if "updall" not in items:
            return
        c = self._bg_canvas
        busy = (self._btn_mode == "busy" or self._addons_busy
                or getattr(self, "_running", False))
        if self._update_all_running and not busy and not self._update_all_chain:
            self._update_all_running = False
        spinning = self._update_all_running
        show = not spinning and not busy and self._update_all_count() > 0
        if not show and c.itemcget(items["updall"], "state") != "hidden":
            self._updall_leave()            # no tooltip left over a hidden icon
        c.itemconfigure(items["updall"], state="normal" if show else "hidden")
        c.itemconfigure(items["updall_spin"],
                        state="normal" if spinning else "hidden")
        if spinning and not self._updall_spin:
            self._spin_update_all()

    def _spin_update_all(self):
        if not self._update_all_running or getattr(self, "_destroying", False):
            self._updall_spin = 0
            return
        self._updall_spin = (self._updall_spin + 30) % 360 or 360
        try:
            self._bg_canvas.itemconfigure(self._foot_items["updall_spin"],
                                          start=90 - self._updall_spin)
            self.after(60, self._spin_update_all)
        except (tk.TclError, RuntimeError):
            self._updall_spin = 0


    def _update_all_preview(self, plans) -> bool:
        """Show what the update will and will not do; True to go ahead.

        Shown only when something is being held back. A run where everything
        is ordinary is one click, which is the point of the button -- and a
        confirmation nobody ever needs is a confirmation everybody learns to
        dismiss, which is how the one that mattered gets dismissed too."""
        from tkinter import messagebox
        doing = updatable(plans)
        held = skipped_notably(plans)
        if not held:
            return True

        lines = []
        if doing:
            lines.append(tr("Updates to install: {count}", count=len(doing)))
            for p in doing:
                if p.local and p.remote:
                    lines.append("    %s   %s \u2192 %s"
                                 % (p.component_id, p.local, p.remote))
                else:
                    lines.append("    %s" % p.component_id)
            lines.append("")
        else:
            lines.append(tr("Nothing can be safely updated right now."))
            lines.append("")

        lines.append(tr("Not being changed:"))
        for p in held:
            lines.append("    %s" % p.component_id)
            lines.append("        %s" % tr(str(p.reason)))
        lines.append("")
        lines.append(tr("Anything listed under \u201cnot being changed\u201d "
                        "stays exactly as it is. Each one can still be dealt "
                        "with individually from its own tab."))

        if not doing:
            messagebox.showinfo(tr("Update all"), "\n".join(lines))
            return False
        return messagebox.askokcancel(tr("Update all"), "\n".join(lines))



    def _layout_footer(self):
        """Place the footer from the current font's line height, so a larger
        face (OpenDyslexic) pushes the buttons down instead of under the
        status line. Called again whenever the font changes."""
        items = getattr(self, "_foot_items", None)
        if not items:
            return
        import tkinter.font as tkfont
        c = self._bg_canvas
        status_font = self._font(10, bold=True)
        line = tkfont.Font(font=status_font).metrics("linespace")
        y0 = WIN_H - FOOT_H
        x = self._px(40)
        status_y = y0 + self._px(6)
        btn_y = status_y + line + self._px(6)
        # As wide as the longest word it can show, in this language and face,
        # and never so wide that it reaches the progress bar.
        btn_font = tkfont.Font(font=self._font(11, bold=True))
        widest = max(btn_font.measure(tr(t)) for t in (
            "UPDATE", "PLAY", "Installing…", "Patching…",
            "Checking…", "Verifying…", "Updating…"))
        btn_w = min(self._px(170), max(self._px(132), widest + self._px(20)))
        if getattr(self, "_upd_btn", None) is not None:
            self._upd_btn.set_width(btn_w)
        glow_w = btn_w + 2 * self._px(2)
        glow_h = self._px(38) + 2 * self._px(2)
        btn_bottom = btn_y + glow_h

        c.coords(items["status"], x, status_y)
        c.itemconfigure(items["status"], font=status_font)
        c.coords(items["btn"], x, btn_y)
        if "updall" in items:
            # Right of PLAY, centred on it. PLAY itself does not move.
            self._update_all_photos()
            c.itemconfigure(items["updall"], image=self._updall_images[0])
            aw = self._updall_images[0].width()
            ah = self._updall_images[0].height()
            ax = x + glow_w + self._px(10)     # where UPDATE ALL sat
            ay = btn_y + (glow_h - ah) // 2
            c.coords(items["updall"], ax + aw, ay + ah)
            r = self._px(11)                    # inside the box
            cx, cy = ax + aw // 2, btn_y + glow_h // 2
            c.coords(items["updall_spin"], cx - r, cy - r, cx + r, cy + r)
            c.itemconfigure(items["updall_spin"], width=max(2, self._px(3)))
        c.coords(items["client"], x, btn_bottom + self._px(6))
        c.itemconfigure(items["client"], font=self._font(8))

        # The progress bar's bottom edge lines up with the buttons'.
        self._pb_y = btn_bottom - self._px(6)
        c.coords(items["progress"], self._pb_x0 + self._pb_width // 2,
                 self._pb_y - self._px(6))
        c.itemconfigure(items["progress"], font=self._font(10))
        c.coords(items["version"], WIN_W - self._px(10), WIN_H - self._px(6))
        if "coffee" in items:
            # Above the version, side by side, the right one's edge on the
            # version's: Discord, then the Ko-fi cup.
            self._footer_icon_photos()
            bottom = c.bbox(items["version"])[1] - self._px(6)
            right = WIN_W - self._px(10)
            for key in ("coffee", "discord"):
                photo = self._footer_icons[key][0]
                c.itemconfigure(items[key], image=photo)
                c.coords(items[key], right, bottom)
                right -= photo.width() + self._px(FOOTER_ICON_GAP)
        self._draw_progress(self._pb_val)

    def _draw_progress(self, value: float):
        """The progress bar, drawn on the background canvas (tag "pb") and
        only while something is running."""
        self._pb_val = max(0.0, min(1.0, value))
        c = self._bg_canvas
        c.delete("pb")
        if not getattr(self, "_running", False):
            return
        w, x0, y0, h = self._pb_width, self._pb_x0, self._pb_y, self._px(6)
        c.create_rectangle(x0, y0, x0 + w, y0 + h, fill="#1e1e26",
                           outline="", tags="pb")
        filled = int(w * self._pb_val)
        if filled > 0:
            for x in range(filled):
                t     = x / max(filled - 1, 1)
                r_val = int(0xc8 + t * (0xe8 - 0xc8))
                g_val = int(0x92 + t * (0xb8 - 0x92))
                b_val = int(0x2a + t * (0x4b - 0x2a))
                col   = f"#{r_val:02x}{g_val:02x}{b_val:02x}"
                c.create_line(x0 + x, y0, x0 + x, y0 + h, fill=col, tags="pb")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _game_folder_changed(self, new_val: str):
        """Wipe all state tied to the previous game folder – the folder-scoped
        config (mods/addons install records) and session TTLs/badges – and
        point everything at new_val. Called from _close_settings when the user
        has switched to a different folder."""
        if os.path.exists(os.path.join(new_val, "WoW.exe")):
            remove_wdb(new_val)

        # Wipe folder-scoped config and set the new path in one atomic merge.
        # This also re-arms the default-mods / recommended-addons auto-install.
        # Settings copied from a Windows or Wine install name the old folder
        # as a Windows path, which is no folder here at all: choosing the
        # game folder then is moving the settings across, not switching to
        # another game, so the mod and addon records are kept. Each is
        # checked against the files in the new folder as usual -- a record
        # whose files differ shows as changed, never as up to date.
        old_dir = self._cfg.get("out_dir") or ""
        keep_records = platforms.foreign_windows_path(old_dir)

        def _wipe(c):
            c["out_dir"] = new_val
            if not keep_records:
                for k in ("mods", "addons"):
                    c.pop(k, None)
        self._cfg = update_config(_wipe)
        if keep_records:
            self._log_line(f"Settings came from a Windows/Wine install "
                           f"({old_dir}); mod and addon records kept and "
                           f"checked against the new folder.\n", "acct")

        self._mod_pending_state = {}
        self._default_mods_install_started = False
        self._default_addons_install_started = False
        self._client_ready = False

        # Reset session-level state so nothing from the previous folder is
        # served from memory: addons verify + rendered list, news timers, badges.
        self._addons_verified_ts = 0.0
        self._addons_status = {"state": "idle", "addons": {}, "available": []}
        self._addon_errors = {}
        self._mod_updates_count = 0
        self._addon_updates_count = 0
        self._mpq_updates_count = 0
        self._mpq_installed_rows = []
        self._mpq_available_rows = list(MPQ_PATCHES)
        self._draw_nav_tab("MODS")
        self._draw_nav_tab("ADDONS")
        self._draw_nav_tab("MPQ")
        self._update_all_chain = False
        self._update_all_running = False
        self._refresh_update_all_btn()
        self._render_addons()
        self._render_mpq()

        self._log_line("\nGame folder changed – state reset.\n", "acct")

    def _set_pending_reconcile(self, mode):
        """Set the pending-reconcile mode (None / 'full' / 'keep-config') and
        persist it, so a reconcile offered but not applied before quitting is
        still pending next launch."""
        self._pending_reconcile = mode or None
        self._cfg = update_config(
            lambda c: c.__setitem__("pending_reconcile", self._pending_reconcile))

    def _offer_reconcile(self):
        """Surface the Update button for the pending reconcile (mode already
        set) WITHOUT running a verify first. Clicking Update then reconciles:
        an integrity check + WoW.exe re-patch (+ a fresh Config.wtf when the
        mode is 'full') – meaningful even when the game files are already
        intact."""
        self._running = False
        self._client_ready = False
        self._status_var.set(tr("Update available!"))
        self._set_btn_update()

    def _prompt_av_exclusion(self, title: str | None = None):
        """Ask whether to add the current game folder to Windows Defender
        exclusions (some mods can be mistakenly flagged by antivirus). The
        answer, yes or no, is recorded for this folder: it is asked once."""
        from tkinter import messagebox
        client = os.path.normpath(self._game_path.get().strip())
        update_config(lambda c: c.__setitem__("defender_offered_for", client))
        self._asking = True
        try:
            yes = messagebox.askyesno(
                title or tr("Game folder changed"),
                tr("It is highly recommended to add the game folder to your "
                   "antivirus exclusions. Antivirus software may incorrectly "
                   "detect some mods as threats and prevent them from being "
                   "downloaded or installed properly.\n\n"
                   "Do you want to add the game folder to Defender exclusions?"),
                parent=self)
        finally:
            self._asking = False
        if yes:
            self._allow_through_antivirus()

    def _offer_av_exclusion(self):
        """After installing or updating EqUpdater: offer the Defender
        exclusion once for this game folder, the way the "manage what is
        already installed" question is offered. A first run asks when
        Settings closes instead (see _close_settings), and a folder already
        asked about -- or excluded from Settings -- is not asked again."""
        if not platforms.WINDOWS or os.environ.get("EQUPDATER_NO_DEFENDER_PROMPT"):
            return
        client = self._game_path.get().strip()
        if (not client or self._first_run or self._av_excluded
                or not os.path.isdir(client)):
            return
        if load_config().get("defender_offered_for") == os.path.normpath(client):
            return
        if (getattr(self, "_settings_overlay", None) is not None
                or getattr(self, "_asking", False)):
            self.after(3000, self._offer_av_exclusion)    # one question at a time
            return
        self._prompt_av_exclusion(tr("Add the game folder to Defender exclusions?"))

    def _render_log(self, msg: str, tag: str = ""):
        """Normalize a raw log message (ensure a trailing newline, auto-tag
        when untagged) and append it to the log panel. Main thread only."""
        line = msg if msg.endswith("\n") else msg + "\n"
        if not tag:
            ml = line.lower()
            if "✓" in line or "success" in ml or "complete" in ml or "up to date" in ml:
                tag = "ok"
            elif "✗" in line or "error" in ml or "fail" in ml or "mismatch" in ml:
                tag = "err"
            elif line.strip().startswith("["):
                tag = "acct"
        self._log_line(line, tag)

    def _log_line(self, text: str, tag: str = ""):
        self._log_buffer.append((text, tag))
        txt = self._logwin_text
        if txt is not None:
            try:
                txt.configure(state="normal")
                if tag:
                    txt.insert("end", text, tag)
                else:
                    txt.insert("end", text)
                txt.see("end")
                txt.configure(state="disabled")
            except tk.TclError:
                self._logwin_text = None

    # ── settings ─────────────────────────────────────────────────────────────

    def _open_settings(self, event=None):
        if self._settings_overlay is not None:
            return

        ov = tk.Frame(self, bg="#0a0a0e")
        ov.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        ov.bind("<Button-1>", lambda e: self._close_settings())
        self._settings_overlay = ov

        self.bind("<Escape>", lambda e: self._close_settings())

        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        MW, MH = self._px(800), self._px(500)
        panel = tk.Frame(ov, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        panel.place(relx=0.5, rely=0.5, anchor="center", y=-self._px(20),
                    width=MW, height=MH)

        hdr = tk.Frame(panel, bg=P_HDR, height=self._px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("SETTINGS"), font=self._font(13, bold=True),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=self._px(18))
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=self._px(16))
        x_btn.bind("<Button-1>", lambda e: self._close_settings())
        x_btn.bind("<Enter>",    lambda e: x_btn.configure(fg=C_TEXT))
        x_btn.bind("<Leave>",    lambda e: x_btn.configure(fg=C_TEXT_DIM))
        tk.Frame(panel, bg=P_BDR, height=self._px(1)).pack(fill="x")

        PADX = self._px(22)
        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=PADX,
                  pady=(self._px(16), self._px(12)))

        loc_row = tk.Frame(body, bg=P_BG)
        loc_row.pack(fill="x")
        tk.Label(loc_row, text=tr("GAME FOLDER"),
                 font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(side="left")
        opn = tk.Label(loc_row, text=tr("Open folder"), font=self._font(9),
                       fg=C_TEXT_DIM, bg=P_BG, cursor="hand2")
        opn.pack(side="left", padx=(self._px(16), 0))
        opn.bind("<Button-1>", lambda e: self._open_client_folder())
        opn.bind("<Enter>",    lambda e: opn.configure(fg=C_GOLD))
        opn.bind("<Leave>",    lambda e: opn.configure(fg=C_TEXT_DIM))

        # Same StringVar as the Update tab's Game folder entry — changing it
        # here fires the exact same folder-change mechanics immediately.
        path_row = tk.Frame(body, bg=P_BG)
        path_row.pack(fill="x", pady=(self._px(8), 0))
        ent = tk.Entry(path_row, textvariable=self._game_path,
                       bg=P_INP, fg=C_TEXT, relief="flat", font=FONT_MONO,
                       state="readonly", readonlybackground=P_INP,
                       highlightthickness=1, highlightbackground=P_BDR,
                       highlightcolor=P_BDR)
        ent.pack(side="left", fill="x", expand=True, ipady=self._px(7))
        chg = tk.Label(path_row, text=tr("Change"),
                       font=self._font(10, bold=True),
                       fg=C_TEXT, bg=P_BDR, cursor="hand2",
                       padx=self._px(16), pady=self._px(7))
        chg.pack(side="left", padx=(self._px(8), 0))
        chg.bind("<Button-1>", lambda e: self._settings_change_dir())
        chg.bind("<Enter>",    lambda e: chg.configure(bg=C_GOLD, fg="#000"))
        chg.bind("<Leave>",    lambda e: chg.configure(bg=P_BDR, fg=C_TEXT))

        # Two equal-width columns via grid, so the right column keeps a fixed
        # position and reaches toward the right edge — regardless of how wide
        # the left column's text is.
        cols = tk.Frame(body, bg=P_BG)
        cols.pack(fill="x", pady=(self._px(20), 0))
        cols.columnconfigure(0, weight=3, uniform="s")
        cols.columnconfigure(1, weight=2, uniform="s")
        lcol = tk.Frame(cols, bg=P_BG)
        lcol.grid(row=0, column=0, sticky="nw")
        rcol = tk.Frame(cols, bg=P_BG)
        rcol.grid(row=0, column=1, sticky="nw")

        tk.Label(lcol, text=tr("DOWNLOAD MIRROR"),
                 font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w", pady=(0, self._px(4)))
        mir = tk.Frame(lcol, bg=P_BG)
        mir.pack(anchor="w")
        tk.Label(mir, text="●", font=self._font(9),
                 fg=C_OK, bg=P_BG).pack(side="left")
        tk.Label(mir, text=" " + tr("Iceland"), font=self._font(10, bold=True),
                 fg=C_TEXT, bg=P_BG).pack(side="left")
        self._mirror_status_lbl = tk.Label(mir, text=tr("checking…"),
                                           font=self._font(9),
                                           fg=C_TEXT_DIM, bg=P_BG)
        self._mirror_status_lbl.pack(side="left", padx=(self._px(8), 0))
        rf = tk.Label(mir, text="⟳", font=self._font(11),
                      fg=C_TEXT_DIM, bg=P_BG, cursor="hand2")
        rf.pack(side="left", padx=(self._px(6), 0))
        rf.bind("<Button-1>", lambda e: self._check_mirror_status())
        rf.bind("<Enter>",    lambda e: rf.configure(fg=C_GOLD))
        rf.bind("<Leave>",    lambda e: rf.configure(fg=C_TEXT_DIM))
        self._check_mirror_status()

        tk.Label(lcol, text=tr("TROUBLESHOOTING"),
                 font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w", pady=(self._px(22), 0))

        def _titem(icon, text, cmd, icon_color=C_GOLD, tip=None):
            r = tk.Frame(lcol, bg=P_BG, cursor="hand2")
            r.pack(anchor="w", pady=(self._px(12), 0))
            # Monochrome glyphs in a fixed-width slot so all icons line up
            # and read at the same size (color emoji would render larger).
            ic = tk.Label(r, text=icon, font=("Segoe UI Symbol", 11),
                          fg=icon_color, bg=P_BG, width=2, anchor="w")
            ic.pack(side="left")
            tl = tk.Label(r, text=text, font=self._font(10),
                          fg=C_TEXT, bg=P_BG)
            tl.pack(side="left")
            for w in (r, ic, tl):
                w.bind("<Button-1>", lambda e: cmd())
                w.bind("<Enter>", lambda e: tl.configure(fg=C_GOLD))
                w.bind("<Leave>", lambda e: tl.configure(fg=C_TEXT))
            if tip:
                for w in (ic, tl):
                    self._add_tooltip(w, tip)

        _titem("✓", tr("Verify game files"), self._settings_verify,
               tip=tr("Checks every game file against the official client and "
                      "repairs any that are damaged or missing. Installed mods "
                      "are left alone."))
        _titem("✚", tr("Login Doctor"), self._open_login_doctor,
               tip=tr("Checks this computer for common login problems: the game client, its login settings, OctoLogin and the connection to OctoWoW. It can repair the login settings."))
        _titem("⚑", tr("Game Diagnostics"), self._open_game_diagnostics,
               tip=tr("What happened after you pressed PLAY, a check of the "
                      "game folder, and a report you can send for help."))
        _titem("☰", tr("Show Session Log"), self._show_logs,
               tip=tr("Everything EqUpdater has done since it started: "
                      "downloads, installs, updates and errors."))
        if platforms.WINDOWS:
            _titem("⛊", tr("Exclude from Defender"),
                   self._allow_through_antivirus,
                   tip=tr("Adds the game folder to Windows Defender's exclusions, "
                          "so mods are not wrongly flagged or removed. Windows asks "
                          "for administrator rights."))
        else:
            _titem("⚙", tr("Game launcher…"), self._open_game_launcher,
                   tip=tr("How the game is started on Linux: the launch command, "
                          "the Wine prefix and environment variables."))

        tk.Label(rcol, text=tr("GENERAL"),
                 font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w")
        cb_wdb = tk.Checkbutton(rcol, text=" " + tr("Clear WDB on game launch"),
                       variable=self._clear_wdb_var,
                       command=self._toggle_clear_wdb,
                       font=self._font(10), fg=C_TEXT, bg=P_BG,
                       activebackground=P_BG, activeforeground=C_TEXT,
                       selectcolor=P_INP, highlightthickness=0, bd=0,
                       cursor="hand2")
        cb_wdb.pack(anchor="w", pady=(self._px(10), 0))
        self._add_tooltip(
            cb_wdb,
            tr("Deletes the game's WDB cache before each launch: the item and "
               "creature data the server sent. Clearing it fixes stale names "
               "and tooltips; the game builds it again as you play."))
        cb_minimize = tk.Checkbutton(rcol, text=" " + tr("Minimize {app} on game launch",
                                                 app=branding.APP_TITLE),
                       variable=self._close_on_launch_var,
                       command=self._toggle_close_on_launch,
                       font=self._font(10), fg=C_TEXT, bg=P_BG,
                       activebackground=P_BG, activeforeground=C_TEXT,
                       selectcolor=P_INP, highlightthickness=0, bd=0,
                       cursor="hand2")
        cb_minimize.pack(anchor="w", pady=(self._px(10), 0))
        self._add_tooltip(
            cb_minimize,
            tr("Minimizes {app} to the taskbar a moment after the game starts.",
               app=branding.APP_TITLE))
        cb_auto_mods = tk.Checkbutton(
            rcol, text=" " + tr("Install essential mods"),
            variable=self._auto_mods_var, command=self._toggle_auto_mods,
            font=self._font(10), fg=C_TEXT, bg=P_BG,
            activebackground=P_BG, activeforeground=C_TEXT,
            selectcolor=P_INP, highlightthickness=0, bd=0, cursor="hand2")
        cb_auto_mods.pack(anchor="w", pady=(self._px(10), 0))
        self._add_tooltip(
            cb_auto_mods,
            tr("Off by default. When on, the essential mods (VanillaFixes "
               "and the DLLs it loads) are installed wherever they are "
               "missing. Mods already in the client are never replaced."))
        cb_auto_addons = tk.Checkbutton(rcol, text=" " + tr("Install recommended addons"),
                       variable=self._auto_addons_var,
                       command=self._toggle_auto_addons,
                       font=self._font(10), fg=C_TEXT, bg=P_BG,
                       activebackground=P_BG, activeforeground=C_TEXT,
                       selectcolor=P_INP, highlightthickness=0, bd=0,
                       cursor="hand2")
        cb_auto_addons.pack(anchor="w", pady=(self._px(10), 0))
        self._add_tooltip(
            cb_auto_addons,
            tr("When on, the recommended addons (marked ★ on the Addons tab) "
               "are installed wherever they are missing. Addons already in "
               "the client are never replaced."))
        cb_animated_bg = tk.Checkbutton(
            rcol, text=" " + tr("Animated background"),
            variable=self._animated_bg_var,
            command=self._toggle_animated_background,
            font=self._font(10), fg=C_TEXT, bg=P_BG,
            activebackground=P_BG, activeforeground=C_TEXT,
            selectcolor=P_INP, highlightthickness=0, bd=0, cursor="hand2")
        cb_animated_bg.pack(anchor="w", pady=(self._px(10), 0))
        self._add_tooltip(
            cb_animated_bg,
            tr("Moving bubbles behind the window. Off shows a still image "
               "and uses less CPU."))

        # FONT: its own section under GENERAL. The panel grows to fit its
        # content (_fit_settings_panel), so a larger face cannot push these
        # rows out of reach.
        # Chinese is drawn in Windows' own Chinese font whichever face is
        # chosen here, so in Chinese the section is shown greyed out.
        fonts_matter = i18n.font_choice_matters()
        font_lbl = tk.Label(rcol, text=tr("FONT"),
                            font=self._font(10, bold=True),
                            fg=C_GOLD if fonts_matter else C_TEXT_DIM, bg=P_BG)
        font_lbl.pack(anchor="w", pady=(self._px(20), 0))

        # Each name is drawn in its own face, so the list previews what
        # choosing it looks like, whichever is selected now. Dot and name are
        # separate widgets placed by the letters' measured height: Tk would
        # centre the dot on the line box, and OpenDyslexic's line box is far
        # taller than its letters, so its dot sat high. Every row is the same
        # height, so the rows are evenly spaced too.
        font_choices = ("arial", "friz", "opendyslexic")
        ink = {c: self._fonts.ink_metrics(c, 10) for c in font_choices}
        row_h = 2 + 2 * max(-(-cap // 2) + desc for _a, cap, desc in ink.values())

        def _font_row(value, label, note=None, available=True):
            shown = label if available else tr("{font} (not installed)", font=label)
            available = available and fonts_matter
            row = tk.Frame(rcol, bg=P_BG, height=row_h)
            rb = tk.Radiobutton(
                row, text="", value=value,
                variable=self._font_choice_var,
                command=self._change_font_choice,
                fg=C_TEXT, bg=P_BG,
                activebackground=P_BG, activeforeground=C_TEXT,
                selectcolor=P_INP, highlightthickness=0, bd=0,
                padx=0, pady=0,
                state="normal" if available else "disabled",
                cursor="hand2" if available else "arrow")
            name = tk.Label(
                row, text=shown, font=self._fonts.preview_font(value, 10),
                fg=C_TEXT if available else C_TEXT_DIM, bg=P_BG,
                bd=0, padx=0, pady=0, highlightthickness=0,
                cursor="hand2" if available else "arrow")
            rb.keeps_own_font = name.keeps_own_font = True
            name.font_choice = value
            ascent, cap, _desc = ink[value]
            dot_w = rb.winfo_reqwidth()
            gap = self._px(5)
            rb.place(x=0, y=row_h // 2, anchor="w")
            name.place(x=dot_w + gap, y=row_h // 2 - (ascent - cap // 2))
            row.configure(width=dot_w + gap + name.winfo_reqwidth())
            row.pack(anchor="w", pady=(self._px(5), 0))
            if available:
                name.bind("<Button-1>", lambda _e: rb.invoke())
            if note:
                self._add_tooltip(rb, tr(note))
                self._add_tooltip(name, tr(note))
            return rb

        _font_row("arial", "Arial",
                  N_("Simple sans-serif."),
                  self._fonts.arial_available)
        _font_row("friz", "Friz Quadrata",
                  N_("Warcraft-style font."),
                  self._fonts.friz_available)
        _font_row(
            "opendyslexic", "OpenDyslexic",
            N_("Dyslexic-friendly font."),
            self._fonts.dyslexic_available)

        # Settings is created after the main-window font pass, so apply the
        # current family to the newly-created overlay immediately.
        self._fonts.apply_tree(ov)
        self._settings_panel = (panel, MW, MH)
        self._fit_settings_panel(panel, MW, MH)

    def _open_game_launcher(self):
        """Linux: Settings -> Game launcher, in a panel of its own over
        Settings. Its fields do not fit in Settings' left column: with
        OpenDyslexic they pushed the bottom of that column out of the panel."""
        if self._settings_overlay is None:
            return
        old = getattr(self, "_launcher_overlay", None)
        if old is not None and old.winfo_exists():
            return
        P_BG, P_HDR, P_BDR, P_INP = C_PANEL, C_HDR, C_PANEL_BDR, "#0f0b16"
        shade = tk.Frame(self._settings_overlay, bg="#0a0a0e")
        shade.place(x=0, y=0, relwidth=1, relheight=1)
        self._launcher_overlay = shade

        def close():
            self._launcher_overlay = None
            try:
                shade.destroy()
            except tk.TclError:
                pass
            if self._settings_overlay is not None:
                self.bind("<Escape>", lambda e: self._close_settings())
        shade.bind("<Button-1>", lambda e: close())
        self.bind("<Escape>", lambda e: close())

        panel = tk.Frame(shade, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        hdr = tk.Frame(panel, bg=P_HDR, height=self._px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("GAME LAUNCHER"), font=self._font(13, bold=True),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=self._px(18))
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=self._px(16))
        x_btn.bind("<Button-1>", lambda e: close())
        x_btn.bind("<Enter>",    lambda e: x_btn.configure(fg=C_TEXT))
        x_btn.bind("<Leave>",    lambda e: x_btn.configure(fg=C_TEXT_DIM))
        tk.Frame(panel, bg=P_BDR, height=self._px(1)).pack(fill="x")
        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=self._px(22),
                  pady=(self._px(8), self._px(18)))
        self._build_game_launcher_settings(body, P_BG, P_INP, P_BDR)

        # Centred at its natural height, so it grows when an error line
        # appears under the environment variables instead of clipping it.
        panel.place(relx=0.5, rely=0.5, anchor="center",
                    width=min(self._px(560), WIN_W - 2 * self._px(10)))

    # ── Login Doctor ────────────────────────────────────────────────────────

    _DOCTOR_MARKS = {logindoctor.PASS: ("✓", C_OK),
                     logindoctor.WARN: ("⚠", "#d4b43c"),
                     logindoctor.FAIL: ("✗", C_ERR)}

    def _doctor_fetch(self, url: str, timeout: float) -> int:
        """One HTTPS request for Login Doctor. Any HTTP answer -- an error
        status or the DDoS-protection page included -- means the host was
        reached; only a failure to connect raises."""
        import urllib.error
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with secure_urlopen(req, timeout=timeout,
                                allowed_hosts={"octowow.st", "dl.octowow.st"}) as r:
                r.read(4096)
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _open_login_doctor(self):
        """Settings -> Login Doctor: local login checks, safe connectivity
        checks and an explicit repair of the login configuration. See
        equpdater/logindoctor.py for what it does and does not touch."""
        if self._settings_overlay is None:
            return
        old = getattr(self, "_doctor_overlay", None)
        if old is not None and old.winfo_exists():
            return
        P_BG, P_HDR, P_BDR = C_PANEL, C_HDR, C_PANEL_BDR
        shade = tk.Frame(self._settings_overlay, bg="#0a0a0e")
        shade.place(x=0, y=0, relwidth=1, relheight=1)
        self._doctor_overlay = shade

        def close():
            self._doctor_overlay = None
            self._doctor_gen = getattr(self, "_doctor_gen", 0) + 1
            try:
                shade.destroy()
            except tk.TclError:
                pass
            if self._settings_overlay is not None:
                self.bind("<Escape>", lambda e: self._close_settings())
        self.bind("<Escape>", lambda e: close())

        panel = tk.Frame(shade, bg=P_BG, highlightthickness=1,
                         highlightbackground=P_BDR, highlightcolor=P_BDR)
        hdr = tk.Frame(panel, bg=P_HDR, height=self._px(46))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=tr("LOGIN DOCTOR"), font=self._font(13, bold=True),
                 fg=C_PURPLE, bg=P_HDR).pack(side="left", padx=self._px(18))
        x_btn = tk.Label(hdr, text="✕", font=self._font(12),
                         fg=C_TEXT_DIM, bg=P_HDR, cursor="hand2")
        x_btn.pack(side="right", padx=self._px(16))
        x_btn.bind("<Button-1>", lambda e: close())
        tk.Frame(panel, bg=P_BDR, height=self._px(1)).pack(fill="x")
        body = tk.Frame(panel, bg=P_BG)
        body.pack(fill="both", expand=True, padx=self._px(22),
                  pady=(self._px(10), self._px(16)))

        box = tk.Frame(body, bg=C_LOG_BG)
        box.pack(fill="both", expand=True)
        txt = tk.Text(box, height=12, wrap="word", bg=C_LOG_BG, fg=C_TEXT,
                      font=self._font(10), relief="flat", bd=0,
                      padx=self._px(10), pady=self._px(8),
                      highlightthickness=0, cursor="arrow")
        sb = SlimScrollbar(box, command=txt.yview, bg=C_LOG_BG,
                           width=self._px(8))
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        for state, (_mark, colour) in self._DOCTOR_MARKS.items():
            txt.tag_configure(state, foreground=colour)
        txt.tag_configure("head", foreground=C_GOLD,
                          font=self._font(10, bold=True))
        txt.tag_configure("dim", foreground=C_TEXT_DIM)
        self._doctor_text = txt

        row = tk.Frame(body, bg=P_BG)
        row.pack(fill="x", pady=(self._px(10), 0))
        self._doctor_repair_btn = tk.Label(
            row, text=tr("Repair login configuration"), font=self._font(10),
            fg=C_TEXT_DIM, bg=C_PANEL_BDR, padx=self._px(12), pady=self._px(5))
        self._doctor_repair_btn.pack(side="left")
        again = tk.Label(row, text="⟳  " + tr("Run again"), font=self._font(10),
                         fg=C_TEXT, bg=C_PANEL_BDR, cursor="hand2",
                         padx=self._px(12), pady=self._px(5))
        again.pack(side="right")
        again.bind("<Button-1>", lambda e: self._run_login_doctor())

        tk.Label(body, text=tr("Priority Sign In"), font=self._font(10, bold=True),
                 fg=C_GOLD, bg=P_BG).pack(anchor="w", pady=(self._px(14), 0))
        tk.Label(body, text=tr(logindoctor.PRIORITY_NOTE), font=self._font(9),
                 fg=C_TEXT_DIM, bg=P_BG, justify="left", anchor="w",
                 wraplength=min(self._px(600), WIN_W - 2 * self._px(60))
                 ).pack(anchor="w", fill="x", pady=(self._px(4), 0))

        panel.place(relx=0.5, rely=0.5, anchor="center",
                    width=min(self._px(660), WIN_W - 2 * self._px(10)))
        self._run_login_doctor()

    def _run_login_doctor(self):
        """Local checks now (file reads), network checks on a worker; the
        window polls for them so Tk is only touched on this thread."""
        if getattr(self, "_doctor_overlay", None) is None:
            return
        self._doctor_gen = gen = getattr(self, "_doctor_gen", 0) + 1
        client = self._game_path.get().strip()
        if not client or not os.path.isdir(client):
            local = [logindoctor.Check(logindoctor.FAIL,
                                       N_("No game folder is set"), {})]
            running = False
        else:
            running = platforms.client_in_use(client)
            local = logindoctor.local_checks(client, get_client_version(client),
                                             running)
        self._doctor_state = {"client": client, "local": local,
                              "running": running, "network": None}
        result = []

        def work():
            try:
                result.append(logindoctor.network_checks(self._doctor_fetch))
            except Exception as e:           # never leave the window waiting
                result.append([logindoctor.Check(
                    logindoctor.FAIL, N_("Network checks failed: {error}"),
                    {"error": str(e)}, "network")])

        threading.Thread(target=work, daemon=True, name="login-doctor").start()

        def poll():
            if gen != self._doctor_gen or self._doctor_overlay is None:
                return
            if result:
                self._doctor_state["network"] = result[0]
                self._render_login_doctor()
            else:
                self.after(100, poll)
        self._render_login_doctor()
        self.after(100, poll)

    def _render_login_doctor(self):
        st, txt = self._doctor_state, getattr(self, "_doctor_text", None)
        if txt is None or not txt.winfo_exists():
            return
        txt.configure(state="normal")
        txt.delete("1.0", "end")

        def section(title, checks):
            txt.insert("end", tr(title) + "\n", "head")
            for c in checks:
                mark, _colour = self._DOCTOR_MARKS[c.state]
                txt.insert("end", "  " + mark + "  ", c.state)
                txt.insert("end", tr(c.message, **c.values) + "\n")
            txt.insert("end", "\n")

        local = st["local"]
        section(N_("Game client"), [c for c in local if c.group == "client"])
        realm = [c for c in local if c.group in ("realmlist", "config")]
        if realm:
            section(N_("Login configuration"), realm)
        octologin = [c for c in local if c.group == "octologin"]
        if octologin:
            section(N_("OctoLogin"), octologin)
        net = st["network"]
        if net is None:
            txt.insert("end", tr("Checking the connection…") + "\n", "dim")
        else:
            section(N_("Connection"), net)
            done = logindoctor.summary(local + net)
            if done:
                txt.insert("end", tr(done) + "\n")
        txt.configure(state="disabled")

        btn = self._doctor_repair_btn
        plan = (logindoctor.repair_plan(st["client"])
                if st["client"] and os.path.isdir(st["client"]) else [])
        ready = bool(plan) and not st["running"]
        btn.configure(fg=C_TEXT if ready else C_TEXT_DIM,
                      cursor="hand2" if ready else "arrow")
        btn.unbind("<Button-1>")
        if ready:
            btn.bind("<Button-1>", lambda e: self._repair_login_config())

    def _repair_login_config(self):
        """Show exactly what changes, back up, repair, run the checks again."""
        from tkinter import messagebox
        client = self._doctor_state["client"]
        if platforms.client_in_use(client):
            messagebox.showerror(tr("Repair login configuration"),
                                 tr("The game is running. Close it first: it "
                                    "rewrites these files when it exits."),
                                 parent=self)
            self._run_login_doctor()
            return
        plan = logindoctor.repair_plan(client)
        if not plan:
            self._run_login_doctor()
            return
        files = "\n".join("    " + os.path.relpath(p, client) for p, _t in plan)
        if not messagebox.askyesno(
                tr("Repair login configuration"),
                tr("These files will be changed:\n\n{files}\n\nOnly their login "
                   "lines change: the realm address is set to {host}. A copy of "
                   "each is kept beside it as {ext} (an existing copy is never "
                   "replaced).\n\nRepair now?", files=files,
                   host=logindoctor.LOGIN_HOST, ext=logindoctor.BACKUP_EXT),
                parent=self):
            return
        try:
            changed = logindoctor.repair(client, platforms.client_in_use)
            for path in changed:
                log(f"Login Doctor repaired {path}", "ok")
        except logindoctor.GameRunning:
            messagebox.showerror(tr("Repair login configuration"),
                                 tr("The game is running. Close it first: it "
                                    "rewrites these files when it exits."),
                                 parent=self)
        except OSError as e:
            messagebox.showerror(tr("Repair login configuration"),
                                 tr("Could not repair: {error}", error=e),
                                 parent=self)
        self._run_login_doctor()

    def _build_game_launcher_settings(self, parent, bg, inp, bdr):
        """Linux: how PLAY starts the Windows game client. Empty fields mean
        automatic (Wine, else UMU); a launch command covers Proton, Lutris,
        Faugus or anything else. Saved as typed."""
        saved = load_config().get("game_launcher") or {}

        def field(key, label, tip):
            tk.Label(parent, text=label, font=self._font(9), fg=C_TEXT,
                     bg=bg).pack(anchor="w", pady=(self._px(10), 0))
            var = tk.StringVar(value=saved.get(key, ""))
            ent = tk.Entry(parent, textvariable=var, bg=inp, fg=C_TEXT,
                           insertbackground=C_GOLD, relief="flat",
                           font=FONT_MONO, width=36, highlightthickness=1,
                           highlightbackground=bdr, highlightcolor=C_GOLD)
            ent.pack(anchor="w", fill="x", ipady=self._px(3))
            self._add_tooltip(ent, tip)

            def store(*_a, k=key, v=var):
                value = v.get().strip()

                def merge(c):
                    launcher = c.setdefault("game_launcher", {})
                    launcher[k] = value
                self._cfg = update_config(merge)
                show_runner()
            var.trace_add("write", store)
            return var

        field("launch_command", tr("Launch command"),
              tr("Leave empty to use Wine, or UMU if Wine is not installed. "
                 "{exe} stands for the game's path; without it, the path is "
                 "added at the end."))
        field("wine_prefix", tr("Wine prefix"),
              tr("Optional. The Wine prefix the game runs in; passed as "
                 "WINEPREFIX."))
        runner = tk.Label(parent, text="", font=self._font(9), fg=C_TEXT_DIM,
                          bg=bg, justify="left", wraplength=self._px(500))
        runner.pack(anchor="w", pady=(self._px(4), 0))

        # Environment variables for the game only: one NAME=value per line,
        # saved as typed. A line that cannot be used is named here at once,
        # and PLAY refuses to start without it rather than dropping it.
        tk.Label(parent, text=tr("Environment variables"), font=self._font(9),
                 fg=C_TEXT, bg=bg).pack(anchor="w", pady=(self._px(8), 0))
        env_box = tk.Text(parent, height=3, width=36, wrap="none", bg=inp,
                          fg=C_TEXT, insertbackground=C_GOLD, relief="flat",
                          font=FONT_MONO, highlightthickness=1,
                          highlightbackground=bdr, highlightcolor=C_GOLD,
                          undo=True)
        env_box.insert("1.0", saved.get("env_vars", ""))
        env_box.edit_modified(False)
        env_box.pack(anchor="w", fill="x")
        self._add_tooltip(env_box, tr(
            "Optional. One NAME=value per line, given only to the game when PLAY starts it – for example DXVK_HUD=fps."))
        env_error = tk.Label(parent, text="", font=self._font(9), fg=C_ERR,
                             bg=bg, justify="left", wraplength=self._px(500))
        self._env_vars_box, self._env_vars_error = env_box, env_error

        def show_env_problems(text):
            _vars, problems = platforms.parse_env_lines(text)
            try:
                if problems:
                    env_error.configure(text=env_problem_text(problems))
                    env_error.pack(anchor="w", after=env_box,
                                   pady=(self._px(2), 0))
                else:
                    env_error.configure(text="")
                    env_error.pack_forget()
            except tk.TclError:
                pass

        def store_env(_event=None):
            if not env_box.edit_modified():
                return
            env_box.edit_modified(False)
            text = env_box.get("1.0", "end-1c")

            def merge(c):
                c.setdefault("game_launcher", {})["env_vars"] = text
            self._cfg = update_config(merge)
            show_env_problems(text)
        env_box.bind("<<Modified>>", store_env)
        show_env_problems(saved.get("env_vars", ""))

        def show_runner():
            launcher = load_config().get("game_launcher") or {}
            if (launcher.get("launch_command") or "").strip():
                text = tr("Using your launch command.")
            else:
                name, path = platforms.detect_runner()
                text = (tr("Automatic: {runner}", runner=f"{name} ({path})")
                        if name else
                        tr("Automatic: nothing found. Install Wine, or set "
                           "a launch command."))
            try:
                runner.configure(text=text)
            except tk.TclError:
                pass
        show_runner()

    def _fit_settings_panel(self, panel, min_w: int, min_h: int):
        """Grow the Settings panel to what its content needs, up to the
        window. OpenDyslexic sets far wider and taller than Friz at the same
        point size, and a fixed panel clipped its bottom rows."""
        panel.update_idletasks()
        margin = self._px(10)
        w = min(max(min_w, panel.winfo_reqwidth()), WIN_W - 2 * margin)
        h = min(max(min_h, panel.winfo_reqheight()), WIN_H - 2 * margin)
        # Centred on the window, so it stays centred when the window is
        # resized; never higher than the margin.
        lift = self._px(20) if h == min_h else 0
        lift = min(lift, max(0, (WIN_H - h) // 2 - margin))
        panel.place_configure(relx=0.5, rely=0.5, anchor="center",
                              x=0, y=-lift, width=w, height=h)

    def _close_settings(self):
        self.unbind("<Escape>")
        self._launcher_overlay = None         # destroyed with the overlay
        self._doctor_overlay = None
        if self._settings_overlay is not None:
            self._settings_overlay.destroy()
            self._settings_overlay = None

        # Closing Settings is the "Confirm" action. It needs a reconcile when
        # this is the first run, or the chosen folder now differs from the saved
        # one — the two cases are handled identically from here on.
        new_path  = os.path.normpath(self._game_path.get().strip())
        saved     = os.path.normpath(self._cfg.get("out_dir", ""))
        folder_changed = bool(new_path) and new_path != saved
        needs_reconcile = self._first_run or folder_changed
        self._first_run = False

        if folder_changed:
            self._game_folder_changed(new_path)
        if needs_reconcile:
            self._set_pending_reconcile("full")
            self._offer_reconcile()
            # The reconcile installs mods/addons itself — drop any pending
            # auto-install retriggers so they don't race it.
            self._auto_mods_retrigger = self._auto_addons_retrigger = False
        else:
            # A settings change with no reconcile: apply an auto-install option
            # the user turned on this session (idempotent — no-op if nothing
            # missing).
            if self._auto_mods_retrigger:
                self._auto_mods_retrigger = False
                self._install_missing_essential_mods()
            if self._auto_addons_retrigger:
                self._auto_addons_retrigger = False
                self._install_missing_recommended_addons()

        # Offer a Defender exclusion at each reconcile, unless the user already
        # added one via Settings since the last reconcile. Reset after, so a
        # later reconcile (e.g. another folder change) offers it again.
        if needs_reconcile:
            if not self._av_excluded and platforms.WINDOWS:
                self._prompt_av_exclusion()
            self._av_excluded = False

    def _open_client_folder(self):
        path = os.path.normpath(self._game_path.get().strip())
        if not os.path.isdir(path):
            self._log_line(f"Folder not found: {path}\n", "err")
            return
        try:
            platforms.open_folder(path)
            self._log_line(f"Opened folder: {path}\n", "dim")
        except OSError as e:
            self._log_line(f"Could not open {path}: {e}\n", "err")

    def _settings_change_dir(self):
        cur     = self._game_path.get()
        initial = cur if os.path.isdir(cur) else os.path.expanduser("~")
        chosen  = filedialog.askdirectory(
            title=tr("Select game client folder"),
            initialdir=initial, mustexist=False)
        if chosen:
            # normpath → backslashes; fires the folder-change reset
            self._game_path.set(os.path.normpath(chosen))

    def _settings_verify(self):
        self._close_settings()
        self._verify_game_files()

    def _allow_through_antivirus(self):
        """Add a Windows Defender exclusion for the game folder (asks for
        admin elevation via UAC)."""
        client_dir = os.path.normpath(self._game_path.get().strip())
        if not client_dir or client_dir == ".":
            return
        import ctypes
        cmd = f"Add-MpPreference -ExclusionPath '{client_dir}'"
        r = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe",
            f'-NoProfile -WindowStyle Hidden -Command "{cmd}"', None, 0)
        if r > 32:
            self._av_excluded = True
            update_config(lambda c: c.__setitem__("defender_offered_for",
                                                  client_dir))
            self._log_line(
                f"Requested Defender exclusion for: {client_dir}\n", "ok")
        else:
            self._log_line("Antivirus exclusion cancelled.\n", "err")

    def _maybe_show_aria2_firewall_notice(self):
        """One-time heads-up, shown before the first download, that the bundled
        aria2c downloader may trigger a Windows Firewall prompt – so it doesn't
        look sketchy to a non-technical user. Purely informational; tracked in
        the config so it appears only once."""
        if self._cfg.get("aria2_firewall_notice_shown") or not platforms.WINDOWS:
            return
        from tkinter import messagebox
        messagebox.showinfo(
            tr("Downloading the game"),
            tr("{app} downloads the game with aria2c, the same tool the "
               "official launcher uses.\n\n"
               "You may be asked to allow aria2c through the firewall. You can "
               "safely reject the request, but download speeds may be slightly "
               "lower.\n\n"
               "This message is shown only once.", app=branding.APP_TITLE),
            parent=self)
        self._cfg = update_config(
            lambda c: c.__setitem__("aria2_firewall_notice_shown", True))

    def _check_mirror_status(self):
        lbl = self._mirror_status_lbl
        lbl.configure(text=tr("checking…"), fg=C_TEXT_DIM)

        def worker():
            ok = False
            try:
                req = urllib.request.Request(
                    CLIENT_TORRENT_URL, headers={"User-Agent": UA})
                with secure_urlopen(req, timeout=6,
                                    allowed_hosts=ALLOWED_DOWNLOAD_HOSTS):
                    ok = True
            except Exception:
                ok = False

            def upd():
                try:
                    lbl.configure(text=tr("online") if ok else tr("offline"),
                                  fg=C_OK if ok else C_ERR)
                except tk.TclError:
                    pass
            self.after(0, upd)
        threading.Thread(target=worker, daemon=True).start()

    def _change_font_choice(self):
        choice = (self._font_choice_var.get() or "arial").strip().lower()

        def mutate(c):
            acc = c.setdefault("accessibility", {})
            acc["font_choice"] = choice
            # Legacy compatibility for any code/config that still looks at the
            # old boolean flag.
            acc["dyslexic_font"] = (choice == "opendyslexic")

        self._cfg = update_config(mutate)
        self._fonts.set_choice(choice)
        self._apply_app_fonts()
        # Switched from inside Settings: the panel's content just changed size.
        sp = getattr(self, "_settings_panel", None)
        if sp and self._settings_overlay is not None:
            try:
                self._fit_settings_panel(*sp)
            except tk.TclError:
                pass

    def _toggle_clear_wdb(self):
        val = self._clear_wdb_var.get()
        self._cfg = update_config(
            lambda c: c.__setitem__("clear_wdb_on_launch", val))

    def _toggle_close_on_launch(self):
        val = self._close_on_launch_var.get()
        self._cfg = update_config(
            lambda c: c.__setitem__("close_on_launch", val))

    def _toggle_auto_mods(self):
        val = self._auto_mods_var.get()

        def _merge(c):
            c["auto_install_mods"] = val
            c["essential_mods_asked"] = True   # a choice made here is an answer
        self._cfg = update_config(_merge)
        # Install the missing essential mods only when Settings is closed;
        # turning it back off cancels the pending install.
        self._auto_mods_retrigger = val

    def _toggle_auto_addons(self):
        val = self._auto_addons_var.get()
        self._cfg = update_config(
            lambda c: c.__setitem__("auto_install_addons", val))
        self._auto_addons_retrigger = val

    def _toggle_ignore_speech(self):
        self._cfg = update_config(
            lambda c: c.__setitem__("ignore_speech",
                                    self._ignore_speech_var.get()))

    def _install_missing_essential_mods(self):
        """Install every essential mod not already present. Used when the user
        turns 'Install essential mods' on after the fact."""
        if self._running:
            return
        out = self._game_path.get().strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return  # no client yet — the fresh-folder auto-install handles it
        mods_cfg = load_config().get("mods", {})
        pending = False
        for mod in MODS_REGISTRY:
            if not mod.get("essential", False):
                continue
            state = mods_cfg.get(mod["id"], {})
            if (state.get("installed_version")
                    and mod_installed_files_present(mod, out)):
                continue  # already installed
            if mod_files_on_disk(mod, out):
                continue  # somebody else's copy: shown for adoption, not replaced
            if any(mods_cfg.get(t, {}).get("enabled")
                   for t in mod_twins(mod["id"])):
                continue  # another build of it is on (a custom fork)
            self._mod_pending_state.setdefault(mod["id"], {})["enabled"] = True
            pending = True
        if not pending:
            return
        self._log_line("\nInstalling essential mods...\n", "acct")
        self._set_btn_busy(tr("Installing…"))
        self._status_var.set(tr("Downloading mods…"))
        threading.Thread(target=self._apply_mods_worker,
                         args=(out,), daemon=True).start()

    def _install_missing_recommended_addons(self):
        """Install every recommended addon not already present. Used when the
        user turns 'Install recommended addons' on afterwards."""
        if self._addons_busy:
            return
        out = self._game_path.get().strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return
        ap = addons_path(out)
        recs = [{"folder": name, "status": "available", "git": url,
                 "branch": None, "ref": None, "toc": {},
                 "description": None, "error": None}
                for name, url in RECOMMENDED_ADDONS.items()
                if not os.path.isdir(os.path.join(ap, name))]
        if not recs:
            return
        self._log_line("\nInstalling recommended addons...\n", "acct")
        self._addon_apply(recs)

    def _verify_game_files(self):
        """Full integrity pass: aria2 hash-checks every game file's pieces
        against the torrent and re-downloads only the bad/missing ones, then
        re-patches WoW.exe. Catches same-size corruption the routine size-based
        sync can't. Installed mods are left alone."""
        if self._running:
            return
        self._client_ready = False
        self._log_line(
            "\nVerify game files – hash-checking every file against the torrent.\n",
            "acct")
        self._start_update(check_integrity=True)

    def _show_logs(self):
        if self._logwin is not None:
            try:
                self._logwin.deiconify()
                self._logwin.lift()
                self._logwin.focus_force()
                return
            except tk.TclError:
                self._logwin = None
                self._logwin_text = None

        win = tk.Toplevel(self)
        win.title(tr("{app} – Logs", app=branding.APP_TITLE))
        LW, LH = self._px(760), self._px(420)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{LW}x{LH}+{(sw - LW) // 2}+{(sh - LH) // 2}")
        win.configure(bg=C_BG)

        top = tk.Frame(win, bg=C_BG)
        top.pack(fill="x", padx=self._px(12), pady=(self._px(10), self._px(4)))
        tk.Label(top, text=tr("SESSION LOG"), font=self._font(9, bold=True),
                 fg=C_GOLD, bg=C_BG).pack(side="left")

        outer = tk.Frame(win, bg=C_BG)
        outer.pack(fill="both", expand=True, padx=self._px(12),
                   pady=(0, self._px(12)))
        sb = SlimScrollbar(outer, bg=C_BG, width=self._px(10))
        sb.pack(side="right", fill="y")
        txt = tk.Text(outer, bg=C_LOG_BG, fg=C_TEXT,
                      insertbackground=C_TEXT, relief="flat",
                      font=FONT_MONO, wrap="word", state="disabled",
                      padx=self._px(10), pady=self._px(8), yscrollcommand=sb.set,
                      cursor="arrow", selectbackground=C_PANEL_BDR)
        txt.pack(side="left", fill="both", expand=True)
        sb.command = txt.yview
        for t, c in (("ok", C_OK), ("err", C_ERR),
                     ("dim", C_TEXT_DIM), ("acct", C_GOLD)):
            txt.tag_config(t, foreground=c)

        txt.configure(state="normal")
        for text, tag in self._log_buffer:
            if tag:
                txt.insert("end", text, tag)
            else:
                txt.insert("end", text)
        txt.see("end")
        txt.configure(state="disabled")

        def _close():
            self._logwin = None
            self._logwin_text = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _close)

        self._logwin = win
        self._logwin_text = txt

    # ── button helpers ───────────────────────────────────────────────────────────





    def _set_btn_play(self):
        self._btn_mode = "play"
        self._upd_btn.set_text(tr("PLAY"))
        self._upd_btn.set_palette(PLAY_GRADIENT)
        self._upd_btn.set_enabled(True)
        self._upd_btn.set_split(self._px(16), self._toggle_launch_menu)
        self._refresh_update_all_btn()
        self._btn_glow.configure(bg="#29472c")

    def _set_btn_update(self):
        self._btn_mode = "update"
        self._upd_btn.set_split()
        self._close_launch_menu()
        self._upd_btn.set_text(tr("UPDATE"))
        self._upd_btn.set_palette(UPDATE_GRADIENT)
        self._upd_btn.set_enabled(True)
        self._btn_glow.configure(bg="#5a4828")
        self._refresh_update_all_btn()

    def _set_btn_busy(self, label="…"):
        self._btn_mode = "busy"
        self._upd_btn.set_split()
        self._close_launch_menu()
        self._upd_btn.set_text(label)
        self._upd_btn.set_palette(BUSY_GRADIENT)
        self._upd_btn.set_enabled(False)
        self._btn_glow.configure(bg="#1b2c43")
        self._refresh_update_all_btn()

    def _mods_have_errors(self) -> bool:
        return any(bool(s.get("error"))
                   for s in load_config().get("mods", {}).values())

    def _refresh_ready_state(self):
        """Recompute the footer status/button after an operation finishes.
        PLAY is only offered when the client files are up to date AND no mod
        is in an error state – otherwise the button stays grey and inactive."""
        self._refresh_launch_menu()
        # Never re-enable PLAY while addons are actively installing — this
        # guards against a stray call during the mods→addons setup chain or a
        # post-install verify flipping the button back on mid-download.
        if self._addons_installing:
            self._set_btn_busy(tr("Installing…"))
            self._status_var.set(tr("Downloading addons…"))
            return
        if not self._client_ready:
            self._status_var.set(tr("Update available!"))
            self._set_btn_update()
            return
        if self._mods_have_errors():
            self._set_btn_busy(tr("PLAY"))
            self._status_error(tr("Mod errors – check the MODS tab"),
                               lambda: self._switch_tab("MODS"))
        else:
            self._set_btn_play()
            self._status_var.set(tr("Everything up to date!"))

    # ── what PLAY starts ─────────────────────────────────────────────────────

    def _launch_target(self, client_dir: str) -> str | None:
        """The file PLAY starts in this folder, looked up on disk each time:
        a game-folder change or a mod install needs nothing else to notice."""
        return platforms.resolve_launch_target(
            platforms.launch_options(client_dir),
            load_config().get("launch_default"))

    def _toggle_launch_menu(self):
        if getattr(self, "_launch_menu", None) is not None:
            self._close_launch_menu()
        else:
            self._open_launch_menu()

    def _close_launch_menu(self):
        menu = getattr(self, "_launch_menu", None)
        self._launch_menu = None
        if menu is not None:
            try:
                menu.destroy()
            except tk.TclError:
                pass

    def _open_launch_menu(self):
        """The arrow's list: which file is the default, and a checkbox for
        each file PLAY could start. Drawn in the window rather than a popup
        window, so it scales with the rest and needs no focus from the
        window manager."""
        self._close_launch_menu()
        client = self._game_path.get().strip()
        options = platforms.launch_options(client)
        target = platforms.resolve_launch_target(
            options, load_config().get("launch_default"))

        bd = tk.Frame(self, bg=C_PANEL_BDR)
        self._launch_menu = bd
        bd.vars = []
        inner = tk.Frame(bd, bg=C_PANEL)
        inner.pack(padx=self._px(1), pady=self._px(1))
        tk.Label(inner, font=self._font(10, bold=True), fg=C_GOLD, bg=C_PANEL,
                 anchor="w",
                 text=(tr("Default – {file}", file=target) if target
                       else tr("No launch files found"))
                 ).pack(fill="x", padx=self._px(10),
                        pady=(self._px(7), self._px(5)))
        if options:
            tk.Frame(inner, bg=C_DIVIDER, height=self._px(1)).pack(fill="x")
        for name in options:
            var = tk.BooleanVar(value=name == target)
            bd.vars.append(var)     # dropped, the box forgets its state
            tk.Checkbutton(inner, variable=var, text=" " + name, anchor="w",
                           font=self._font(10), bg=C_PANEL,
                           activebackground=C_PANEL, fg=C_TEXT,
                           activeforeground=C_GOLD_LT, selectcolor=C_PANEL,
                           highlightthickness=0, bd=0, relief="flat",
                           cursor="hand2",
                           command=lambda n=name: self._choose_launch_default(n)
                           ).pack(fill="x", padx=self._px(8), pady=self._px(2))
        tk.Frame(inner, bg=C_PANEL, height=self._px(5)).pack()

        # Above the button and the status line over it, left edges in line.
        self.update_idletasks()
        btn = self._upd_btn
        x = btn.winfo_rootx() - self.winfo_rootx()
        status = self._bg_canvas.bbox(self._foot_items["status"])
        top = (self._bg_canvas.winfo_rooty() - self.winfo_rooty() + status[1]
               if status else btn.winfo_rooty() - self.winfo_rooty())
        y = top - bd.winfo_reqheight() - self._px(4)
        bd.place(x=x, y=max(0, y))
        bd.lift()
        if not getattr(self, "_launch_menu_bound", False):
            self._launch_menu_bound = True
            self.bind("<Button-1>", self._launch_menu_outside, add="+")
            self.bind("<Escape>", lambda _e: self._close_launch_menu(), add="+")
            self.bind("<Configure>", self._launch_menu_resized, add="+")

    def _launch_menu_outside(self, event):
        """A click anywhere else closes the list. The button decides for
        itself: its arrow toggles the list, and PLAY closes it on release."""
        menu = getattr(self, "_launch_menu", None)
        if menu is None or event.widget is self._upd_btn:
            return
        if not str(event.widget).startswith(str(menu)):
            self._close_launch_menu()

    def _launch_menu_resized(self, event):
        if event.widget is self:
            size = (event.width, event.height)
            if size != getattr(self, "_launch_menu_size", size):
                self._close_launch_menu()
            self._launch_menu_size = size

    def _choose_launch_default(self, name: str):
        """Check one and it is the default; the one before it unchecks. The
        checked one stays checked: there is always a default."""
        self._cfg = update_config(
            lambda c: c.__setitem__("launch_default", name))
        self._log_line(f"PLAY now starts {name}.\n", "acct")
        self._open_launch_menu()

    def _refresh_launch_menu(self):
        """Files may have come or gone while the list was open."""
        if getattr(self, "_launch_menu", None) is not None:
            self._open_launch_menu()

    def _btn_click(self):
        self._close_launch_menu()
        if self._btn_mode == "play":
            self._launch_game()
        elif self._btn_mode == "update":
            self._start_update()

    def _diag_store(self):
        store = getattr(self, "_diag_store_obj", None)
        if store is None:
            store = self._diag_store_obj = diagnostics.Store(
                os.path.join(APP_DATA_DIR, "diagnostics"))
        return store

    def _diag_probe(self):
        if not hasattr(self, "_diag_probe_obj"):
            self._diag_probe_obj = diagnostics.default_probe()
        return self._diag_probe_obj

    def _diag_begin(self, client_dir: str, diagnostic: bool):
        """The record of one PLAY, on disk before anything else happens. If
        it cannot be made the game still starts: the view reports the error."""
        try:
            attempt = diagnostics.Attempt(
                self._diag_store(), client_dir, UPDATER_VERSION,
                sys.platform, diagnostic)
            if client_dir and os.path.isdir(client_dir):
                attempt.data["client_build"] = get_client_version(client_dir) or None
            diagnostics.begin_baseline(attempt, self._diag_probe())
            return attempt
        except Exception as e:
            self._log_line(f"Game Diagnostics could not record this launch: {e}\n",
                           "err")
            return diagnostics.NullAttempt()

    def _diag_watch(self, attempt, proc, exe_lbl: str, output):
        """Watch this attempt on its own thread. It outlives nothing it
        needs: what it sees is on disk, for whichever EqUpdater looks next."""
        if isinstance(attempt, diagnostics.NullAttempt):
            return
        try:
            direct = exe_lbl.lower() == platforms.GAME_EXE.lower() \
                and attempt.data["route"].get("runner") == "windows"
            role = "game" if direct else "launcher"
            attempt.process(role, proc.pid, exe_lbl)
            attempt.stage("process_created")
            attempt.event("spawned", role=role, pid=proc.pid, name=exe_lbl)
            observer = diagnostics.Observer(
                attempt, self._diag_probe(), proc, role, exe_lbl, output,
                on_change=self._diag_changed,
                output_copy=LAUNCH_OUTPUT if output else None)
            self._diag_observers = [o for o in getattr(self, "_diag_observers", [])
                                    if not o.done] + [observer]
            observer.start()
        except Exception as e:
            self._log_line(f"Game Diagnostics could not watch this launch: {e}\n",
                           "err")

    def _diag_changed(self, _attempt=None):
        """From an observer's thread: redraw the view if it is open."""
        view = getattr(self, "_diag_view", None)
        if view is not None and not getattr(self, "_destroying", False):
            try:
                self.after(0, view.refresh_from_disk)
            except (tk.TclError, RuntimeError):
                pass

    def _diag_recover(self):
        """Attempts the last EqUpdater left open: settle them with whatever
        evidence is there now, and carry on watching a game still running."""
        try:
            probe = self._diag_probe()
            store = self._diag_store()
            for data in diagnostics.recover(store, probe):
                diagnostics.watch_again(store, data, probe,
                                        on_change=self._diag_changed).start()
        except Exception as e:
            log(f"Game Diagnostics could not read its earlier attempts: {e}")

    def _open_game_diagnostics(self):
        from . import diagnostics_ui
        diagnostics_ui.open_view(self)

    def _launch_game(self, diagnostic: bool = False):
        """Launch the game detached.
        If VanillaFixes is installed use VanillaFixes.exe (it injects dlls then
        starts WoW.exe itself). Otherwise fall back to WoW.exe directly. On
        Linux either one is started through the game runner (see
        platforms.launch_plan)."""
        client_dir = self._game_path.get().strip()
        cfg        = load_config()
        attempt    = self._diag_begin(client_dir, diagnostic)

        # The loader reads dlls.txt in a moment. Anything that edited that file
        # since the last Apply - the official launcher, a restore, a hand edit
        # - can have dropped an enabled mod, and a mod dropped here is a mod
        # that silently does not exist in the game. Last chance to put it back.
        try:
            for name in reconcile_dlls_txt(client_dir, cfg.get("mods", {})):
                self._log_line(f"{name} was missing from dlls.txt - "
                               f"re-registered before launch.\n", "acct")
        except Exception as e:
            self._log_line(f"Could not check dlls.txt before launch: {e}\n", "err")
        try:
            for note in enforce_autoloot(client_dir):
                self._log_line(note + "\n", "acct")
        except Exception as e:
            self._log_line(f"Could not check Auto Loot before launch: {e}\n", "err")

        # Trust VanillaFixes.exe on disk, not the config record. That record
        # is absent after a game-folder switch, after a config reset, and on
        # any install this updater did not perform itself - and falling back
        # to WoW.exe in those cases launches the game with *none* of the
        # dlls.txt mods loaded, silently. Whether the loader is actually there
        # is the thing that matters. Unless the player chose otherwise, it is
        # what starts.
        exe_lbl = self._launch_target(client_dir) or platforms.GAME_EXE
        exe     = os.path.join(client_dir, exe_lbl)
        vf_installed = os.path.exists(os.path.join(client_dir,
                                                   platforms.LOADER_EXE))

        if os.path.exists(_dlls_txt_path(client_dir)):
            if not vf_installed:
                self._log_line(
                    "VanillaFixes.exe is missing, so the mods listed in "
                    "dlls.txt will NOT load. Install VanillaFixes from the "
                    "Mods tab.\n", "err")
            elif exe_lbl.lower() == platforms.GAME_EXE.lower():
                self._log_line(
                    "Starting WoW.exe directly, as chosen: the mods listed "
                    "in dlls.txt will not load.\n", "acct")

        if not os.path.exists(exe):
            self._log_line(f"{exe_lbl} not found at: {exe}\n", "err")
            attempt.block("executable", f"The launch target {exe_lbl} was not "
                          f"found in the game folder.")
            return
        if exe_lbl.lower().endswith(".exe") and self._launch_target_damaged(
                exe, exe_lbl, attempt):
            return

        # One-time DXVK first-launch notice (armed when DXVK was installed).
        if cfg.get("dxvk_notice_pending"):
            self._cfg = update_config(
                lambda c: c.pop("dxvk_notice_pending", None))
            from tkinter import messagebox
            messagebox.showinfo(
                tr("DXVK mod first launch"),
                tr("Initial shader compilation may cause temporary in-game stuttering during the first launch. This is a normal process while the game builds its shader cache.\n\nUsers with AMD GPUs experiencing stability issues can switch to DXVK 2.5.3."),
                parent=self)

        # How to start it: the executable itself on Windows; on Linux the
        # player's launch command, else Wine, else UMU (platforms.launch_plan).
        plan = platforms.launch_plan(client_dir, exe,
                                     load_config().get("game_launcher"))
        if plan.env_problems:
            from tkinter import messagebox
            attempt.block("launch_settings",
                          "The launcher's environment variables have lines "
                          "that cannot be used: " + env_problem_text(plan.env_problems))
            self._log_line("Not launched: the game launcher's environment "
                           "variables have lines that cannot be used.\n",
                           "err")
            messagebox.showerror(
                tr("Cannot start the game"),
                tr("Fix the environment variables in Settings → Game "
                   "launcher:\n\n{problems}",
                   problems=env_problem_text(plan.env_problems)),
                parent=self)
            return
        if not plan.ok:
            from tkinter import messagebox
            attempt.block("launch_settings",
                          "No way to run the game was found: no usable launch "
                          "command, and no wine or umu-run on PATH.")
            self._log_line("No way to run the Windows game client was found "
                           "(no launch command set, no wine or umu-run on "
                           "PATH).\n", "err")
            messagebox.showerror(
                tr("Cannot start the game"),
                tr("{app} found no way to run the Windows game client. "
                   "Install Wine, or set a launch command in Settings \u2192 "
                   "Game launcher.", app=branding.APP_NAME),
                parent=self)
            return

        attempt.route(plan, exe, exe_lbl)
        diagnostics.preflight(attempt, client_dir, exe, plan)
        problems = platforms.launch_problems(plan)
        if problems:
            attempt.block("launch_settings", "; ".join(
                f"{reason}: {value}" for reason, value in problems))
            self._launch_failed(
                launch_problem_text(problems),
                "Not launched: " + "; ".join(f"{r}: {v}" for r, v in problems)
                + f"\n  command: {plan.argv}\n")
            return

        if self._cfg.get("clear_wdb_on_launch", False):
            remove_wdb(client_dir)

        output = None
        if plan.runner != "windows":
            try:
                ensure_dir(os.path.dirname(LAUNCH_OUTPUT))
                output = attempt.output_path() or LAUNCH_OUTPUT
            except OSError:
                pass
        if diagnostic:
            # This launch only: in its own environment, never saved anywhere.
            extra = diagnostics.diagnostic_env(plan.runner,
                                               attempt.folder() or APP_DATA_DIR)
            plan.env = {**(plan.env or platforms.child_env()), **extra}
            attempt.route(plan, exe, exe_lbl)
        try:
            proc = platforms.spawn_detached(plan.argv, cwd=plan.cwd,
                                            env=plan.env, output=output,
                                            console=plan.console)
        except OSError as e:
            attempt.block("process_creation",
                          f"{exe_lbl} could not be started: {e.strerror or e}", e)
            self._launch_failed(
                tr("{program} could not be started: {error}",
                   program=plan.argv[0], error=e.strerror or e),
                f"Failed to launch {exe_lbl}: {e}\n  command: {plan.argv}\n")
            return
        try:
            how = "" if plan.runner == "windows" else f" with {plan.runner}"
            self._log_line(f"Launched {exe_lbl}{how}!\n", "ok")
            self._diag_watch(attempt, proc, exe_lbl, output)
            if output:
                self._watch_launch(proc, plan.argv, output, 0)
            # Briefly disable PLAY so a double-click can't spawn two clients.
            self._set_btn_busy(tr("PLAY"))
            self._status_var.set(tr("Launching…"))
            # Optionally minimize (close) the updater to the taskbar shortly after launch
            if self._cfg.get("close_on_launch", False):
                self.after(1000, self.iconify) # self._on_close and return
            self.after(5000, self._refresh_ready_state)
        except Exception as e:
            self._log_line(f"Failed to launch {exe_lbl}: {e}\n", "err")

    def _launch_target_damaged(self, exe: str, exe_lbl: str, attempt) -> bool:
        """Stop before starting a file that is not a whole Windows program,
        and say what to do. Started anyway, Windows answers only "WinError
        193: %1 is not a valid Win32 application" -- which is what players saw
        when VanillaFixes.exe was empty or cut short. True when stopped."""
        from tkinter import messagebox
        try:
            problem = platforms.program_problem(exe)
        except OSError as e:
            problem, size = None, None
            self._log_line(f"Could not read {exe_lbl} before launch: {e}\n", "err")
        if problem is None:
            return False
        size = os.path.getsize(exe)
        attempt.block("executable", f"{exe_lbl} is {problem} ({size} bytes): "
                      f"not a complete Windows program.")
        self._log_line(f"Not launched: {exe_lbl} is {problem} ({size} bytes), "
                       f"not a complete Windows program.\n", "err")
        what = (tr("{program} is empty.", program=exe_lbl) if problem == "empty"
                else tr("{program} is damaged: it is not a complete Windows "
                        "program.", program=exe_lbl))
        loader = exe_lbl.lower() == platforms.LOADER_EXE.lower()
        if loader:
            if messagebox.askyesno(
                    tr("Cannot start the game"),
                    what + " " + tr("An interrupted download or an antivirus "
                                    "can leave it like this.") + "\n\n"
                    + tr("Reinstall VanillaFixes now?"), parent=self):
                self._switch_tab("MODS")
                self._mod_force_replace("VanillaFixes")
        else:
            messagebox.showerror(
                tr("Cannot start the game"),
                what + " " + tr("Run Settings → Verify game files to repair "
                                "it."), parent=self)
        self._refresh_ready_state()
        return True

    def _launch_failed(self, message: str, details: str):
        """PLAY did not start the game: the details to the log, a short
        reason to the player, PLAY ready again."""
        from tkinter import messagebox
        self._log_line(details, "err")
        messagebox.showerror(tr("Cannot start the game"),
                             tr("Could not launch the game.") + "\n\n" + message,
                             parent=self)
        self._refresh_ready_state()

    def _watch_launch(self, proc, argv, output, waited: int):
        """Linux: a runner that quits with an error within LAUNCH_WATCH_MS
        of PLAY did not start the game -- a bad prefix, a Proton that is not
        there. Say so, with the end of what it printed, rather than let
        PLAY come back as if nothing happened. Quitting cleanly is fine:
        VanillaFixes.exe exits once it has started WoW.exe."""
        code = proc.poll()
        if code is None:
            if waited < LAUNCH_WATCH_MS:
                self.after(250, lambda: self._watch_launch(
                    proc, argv, output, waited + 250))
            return
        if code == 0:
            return
        try:
            with open(output, encoding="utf-8", errors="replace") as f:
                lines = [l.rstrip() for l in f.read().splitlines() if l.strip()]
        except OSError:
            lines = []
        tail = "\n".join(lines[-40:])
        self._launch_failed(
            tr("{program} stopped at once (exit code {code}).",
               program=os.path.basename(argv[0]), code=code)
            + ("\n\n" + "\n".join(lines[-6:]) if lines else ""),
            f"The game runner exited with code {code} right after launch.\n"
            f"  command: {argv}\n  output ({output}):\n{tail}\n")

    # ── verify lifecycle ──────────────────────────────────────────────────────────

    def _start_verify(self):
        out = self._game_path.get().strip()
        if not out:
            self._set_btn_update()
            return
        # Cancel any verify already in flight before swapping the queues, so
        # a stale worker can't keep writing to a queue we no longer poll.
        prev = getattr(self, "_verify_worker", None)
        if prev is not None:
            prev.cancel()
        self._running = True
        self._set_btn_busy(tr("Checking…"))
        self._status_var.set(tr("Checking for updates…"))
        self._log_q  = queue.Queue()
        self._prog_q = queue.Queue()
        worker = VerifyWorker(out, self._log_q, self._prog_q)
        self._verify_worker = worker
        threading.Thread(target=worker.run, daemon=True).start()

    # ── update lifecycle ──────────────────────────────────────────────────────────

    def _start_update(self, check_integrity: bool = False,
                      overwrite_config: bool = False):
        if self._running:
            return
        if self._pending_reconcile:
            check_integrity  = True
            overwrite_config = (self._pending_reconcile == "full")
            self._set_pending_reconcile(None)
        out = self._game_path.get().strip()
        if not out:
            self._log_line("✗  Please set the game folder first.\n", "err")
            return

        self._cfg = update_config(lambda c: c.__setitem__("out_dir", out))

        self._maybe_show_aria2_firewall_notice()

        self._log_line(f"\nGame folder: {out}\n", "dim")

        self._running = True
        self._set_btn_busy(tr("Verifying…") if check_integrity else tr("Updating…"))
        self._status_var.set(tr("Verifying game files…") if check_integrity
                             else tr("Updating game files…"))
        self._draw_progress(0.0)
        self._prog_label_var.set("")

        self._log_q  = queue.Queue()
        self._prog_q = queue.Queue()

        self._worker   = UpdateWorker(out, self._log_q, self._prog_q,
                                      check_integrity=check_integrity,
                                      overwrite_config=overwrite_config)

        t = threading.Thread(target=self._worker.run, daemon=True)
        t.start()

    def _finish(self, success: bool):
        self._running = False
        if success:
            self._client_ready = True
            self._draw_progress(1.0)
            self._refresh_ready_state()
            # Game files are confirmed present now — install essential mods
            # if this is a fresh folder (first launch or folder just changed).
            self._maybe_install_essential_mods()
            # When mods were already initialized, the addons chain from
            # _do_inplace_update never runs — trigger it directly.
            if load_config().get("mods"):
                self._maybe_install_default_addons()
        else:
            self._client_ready = False
            self._status_error(tr("Update failed – check the log"),
                               self._show_logs)
            self._draw_progress(0.0)
            self._set_btn_update()

    # ── queue polling ─────────────────────────────────────────────────────────

    def _poll(self):
        try:
            while True:
                msg, tag = self._log_q.get_nowait()
                if msg == "__DONE__":
                    self._finish(True)
                elif msg == "__ERROR__":
                    self._finish(False)
                elif msg == "__UP_TO_DATE__":
                    self._running = False
                    self._client_ready = True
                    self._draw_progress(1.0)
                    self._refresh_ready_state()
                    # Files were already fine (no download needed) — still need
                    # to check whether essential mods should be auto-installed,
                    # e.g. right after switching to a folder that already has
                    # the client but has never had mods installed via this
                    # updater.
                    self._maybe_install_essential_mods()
                    if load_config().get("mods"):
                        self._maybe_install_default_addons()
                elif msg == "__UPDATE_NEEDED__":
                    self._running = False
                    self._client_ready = False
                    self._status_var.set(tr("Update available!"))
                    self._draw_progress(0.0)
                    self._set_btn_update()
                elif msg.startswith("__VERSION__"):
                    ver = msg[len("__VERSION__"):]
                    self._client_ver_var.set(ver)
                else:
                    self._render_log(msg, tag)
        except queue.Empty:
            pass

        # Drain the global app-log queue that helper functions write to.
        try:
            while True:
                msg, tag = _LOG_Q.get_nowait()
                self._render_log(msg, tag)
        except queue.Empty:
            pass

        latest = None
        try:
            while True:
                latest = self._prog_q.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            val, lbl = latest[0], latest[1]
            status = latest[2] if len(latest) > 2 else None
            self._draw_progress(val)
            self._prog_label_var.set(tr(lbl) if lbl else lbl)
            if status:
                self._status_var.set(tr(status))
                self._set_btn_busy(tr("Verifying…") if status.startswith("Verifying")
                                   else tr("Updating…"))

        if not getattr(self, "_destroying", False):
            try:
                self.after(80, self._poll)
            except (tk.TclError, RuntimeError):
                pass


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────

def _enable_dpi_awareness():
    """Declare DPI awareness before any Tk window is created, so Windows
    reports the true DPI (letting us scale crisply) instead of bitmap-scaling a
    96-DPI render. Prefers Per-Monitor-V2, falling back through older contexts.
    Must run before the Tk root exists; a no-op off Windows / on old Windows."""
    try:
        import ctypes
    except Exception:
        return
    user32 = getattr(ctypes, "windll", None) and ctypes.windll.user32
    if user32 is not None:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4, PER_MONITOR = -3.
        for ctx in (-4, -3):
            try:
                if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(ctx)):
                    return
            except Exception:
                pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor (8.1+)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()         # system-aware (Vista+)
    except Exception:
        pass


if __name__ == "__main__":
    _enable_dpi_awareness()
    app = EqUpdaterApp()
    app.mainloop()
