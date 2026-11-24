"""Mods: registry, release lookups, install/uninstall/recovery, custom mods,
dlls.txt, and the planner glue (``mod_component`` / ``mod_plan``).

Lifted verbatim from ``equpdater/app.py`` (``tools/lift.py``); ``app.py``
re-exports every name. No Tk, no Qt. Logging via ``logsink``; the network via
``net``.
"""

import glob
import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import threading
import time
import urllib.request
from functools import cache
from urllib.parse import urlsplit

from .. import (branding, gamepaths, gitlocal, i18n, lootgate, mpq,
                platforms)
from ..i18n import N_, tr
from ..config import (APP_DATA_DIR, BACKUP_DIR, CONFIG_FILE, ensure_dir,
                      load_config, new_addon_record, new_mod_record,
                      update_config)
from ..gitcompare import (GIT_HOSTS, Ancestry, ancestry, canonical_repo_url,
                          forge_named, repo_ref, same_repo,
                          short as short_sha, split_repo_url)
from ..hashing import files_hash, folder_hash
from ..planner import CAUSE_LOOKUP, Component, plan, skipped_notably, updatable
from ..states import Action, Plan, Status
from . import atomic
from . import oplock
from .logsink import log
from .net import ALLOWED_DOWNLOAD_HOSTS, secure_urlopen
from .errors import _api_json, _describe_net_error, describe_install_error
from .tweaks import client_exe_locked

APP_NAME = branding.APP_NAME
UPDATER_VERSION = branding.APP_VERSION
SERVER = "https://octowow.st"
UA = branding.USER_AGENT
DOWNLOAD_TIMEOUT = 10    # seconds without any data before a transfer aborts


# Registry order == install order: VanillaFixes first (it provides the
# loader the other mods rely on). The UI sorts alphabetically for display.
MODS_REGISTRY = [
    {
        "id":          "VanillaFixes",
        "essential": True,
        "name":        "VanillaFixes",
        "description": N_("Eliminates stuttering and animation lag and enables mod loading. Required by other mods."),
        "repo_url":    "https://github.com/hannesmann/vanillafixes",
        "source": {
            "kind":          "github_release",
            "owner":         "hannesmann",
            "repo":          "vanillafixes",
            "asset_pattern": "vanillafixes-*.zip",
            "prefer_no":     "-dxvk",
            "extract_map":   {"VfPatcher.dll": "VfPatcher.dll",
                              "VanillaFixes.exe": "VanillaFixes.exe"},
        },
        "register_dll":    None,   # the DLL loader itself
        "installed_files": ["VfPatcher.dll", "VanillaFixes.exe"],
    },
    {
        "id":          "ClassicAPI",
        "essential": True,
        "name":        "ClassicAPI",
        "description": N_("Expands the addon API with functions from later WoW versions."),
        "repo_url":    "https://github.com/brues-code/ClassicAPI",
        "source": {
            "kind":          "github_release",
            "owner":         "brues-code",
            "repo":          "ClassicAPI",
            "asset_pattern": "ClassicAPI.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "ClassicAPI.dll",
        "installed_files": ["ClassicAPI.dll"],
    },
    {
        "id":          "AuctionQueryThrottle",
        "essential": True,
        "name":        "AuctionQueryThrottle",
        "description": N_("Removes the fixed 5-second wait between auction house searches."),
        "repo_url":    "https://github.com/brues-code/AuctionQueryThrottle",
        "source": {
            "kind":          "github_release",
            "owner":         "brues-code",
            "repo":          "AuctionQueryThrottle",
            "asset_pattern": "AuctionQueryThrottle.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "AuctionQueryThrottle.dll",
        "installed_files": ["AuctionQueryThrottle.dll"],
    },
    {
        "id":          "dxvk",
        "essential": True,
        "name":        "DXVK",
        "description": N_("Enables Vulkan-based rendering for improved performance."),
        "repo_url":    "https://github.com/doitsujin/dxvk",
        "source": {
            "kind":          "github_release",
            "owner":         "doitsujin",
            "repo":          "dxvk",
            "asset_pattern": "dxvk-[0-9]*.tar.gz",
            "prefer_no":     "-native",
            "extract_map":   {"dxvk-*/x32/d3d9.dll": "d3d9.dll"},
            "post_install":  ["write_dxvk_conf"],
        },
        "register_dll":    None,   # d3d9.dll auto-loads as a DirectX proxy
        "installed_files": ["d3d9.dll", "dxvk.conf"],
    },
    {
        "id":          "nampower",
        "essential": True,
        "name":        "Nampower",
        "description": N_("Reduces input lag and expands the addon API."),
        "repo_url":    "https://github.com/Emyrk/nampower",
        "source": {
            "kind":          "github_release",
            "owner":         "Emyrk",
            "repo":          "nampower",
            "asset_pattern": "nampower.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "nampower.dll",
        "installed_files": ["nampower.dll"],
    },
    {
        "id":          "SuperWoW",
        "essential": True,
        "name":        "SuperWoW",
        "description": N_("Adds new features, including healing combat text, chat links of spells and recipes, and expands the addon API."),
        "repo_url":    "https://github.com/balakethelock/SuperWoW",
        "source": {
            "kind":          "github_release",
            "owner":         "balakethelock",
            "repo":          "SuperWoW",
            "asset_pattern": "SuperWoW*.zip",
            "prefer_no":     None,
            # SuperWoW keeps a static "Release" tag and edits it in place, so
            # the version comes from the asset filename (e.g. …2.2.zip), not
            # the tag.
            "version_from":  "asset",
            "extract_map":   {"SuperWoWhook.dll": "SuperWoWhook.dll"},
        },
        "register_dll":    "SuperWoWhook.dll",
        "installed_files": ["SuperWoWhook.dll"],
    },
    {
        "id":          "transmogfix",
        "essential": True,
        "name":        "TransmogFix",
        "description": N_("Prevents frame drops on character death caused by the server-side transmogrification durability recalculation."),
        "repo_url":    "https://codeberg.org/MarcelineVQ/WeirdUtils",
        "source": {
            "kind":          "direct_file",
            "url":           "https://codeberg.org/MarcelineVQ/WeirdUtils/releases/download/v0.7.0/transmogfix.dll",
            "dest":          "transmogfix.dll",
            "pinned_version": "v0.7.0",
        },
        "register_dll":    "transmogfix.dll",
        "installed_files": ["transmogfix.dll"],
    },
    {
        "id":          "UnitXP_SP3",
        "essential": True,
        "name":        "UnitXP_SP3",
        "description": N_("Adds new features, including frame limiter, improved targeting, and anti-aliased combat text, and expands the addon API."),
        "repo_url":    "https://codeberg.org/konaka/UnitXP_SP3",
        "source": {
            "kind":          "codeberg_release",
            "owner":         "konaka",
            "repo":          "UnitXP_SP3",
            "asset_pattern": "UnitXP_SP3 v*.zip",
            "prefer_no":     "-debug",
            "extract_map":   {"UnitXP_SP3.dll": "UnitXP_SP3.dll"},
        },
        "register_dll":    "UnitXP_SP3.dll",
        "installed_files": ["UnitXP_SP3.dll"],
    },
    {
        "id":          "VanillaHelpers",
        "essential": True,
        "name":        "VanillaHelpers",
        "description": N_("Increases the maximum supported texture resolution and improves memory allocation."),
        "repo_url":    "https://github.com/isfir/VanillaHelpers",
        "source": {
            "kind":          "github_release",
            "owner":         "isfir",
            "repo":          "VanillaHelpers",
            "asset_pattern": "VanillaHelpers.dll",
            "prefer_no":     None,
            "extract_map":   None,
        },
        "register_dll":    "VanillaHelpers.dll",
        "installed_files": ["VanillaHelpers.dll"],
    },
    {
        "id":          "VanillaMultiMonitorFix",
        "essential": False,
        "name":        "VanillaMultiMonitorFix",
        "description": N_("Fixes incorrect game resolution on multi-monitor setups with different resolutions. Set the desired monitor's index in VMMFix_preferred_monitor.txt; use ShowAllDisplayDevices.exe to find the index."),
        "repo_url":    "https://github.com/Mates1500/VanillaMultiMonitorFix",
        "source": {
            "kind":          "github_release",
            "owner":         "Mates1500",
            "repo":          "VanillaMultiMonitorFix",
            "asset_pattern": "release.zip",
            "prefer_no":     None,
            "extract_map":   {"VanillaMultiMonitorFix.dll":
                              "VanillaMultiMonitorFix.dll",
                              "VMMFix_preferred_monitor.txt":
                              "VMMFix_preferred_monitor.txt",
                              "ShowAllDisplayDevices.exe":
                              "ShowAllDisplayDevices.exe"},
        },
        "register_dll":    "VanillaMultiMonitorFix.dll",
        "installed_files": ["VanillaMultiMonitorFix.dll",
                            "VMMFix_preferred_monitor.txt",
                            "ShowAllDisplayDevices.exe"],
        # The DLL is the mod. OctoLauncher installs only that, and the text
        # file is the player's to edit.
        "core_files":      ["VanillaMultiMonitorFix.dll"],
    },
    {
        "id":          "no1600x1200",
        "essential": False,
        "name":        "No1600x1200",
        "description": N_("Fixes incorrect game resolution when your monitor native resolution isn't detected or 1600x1200 is listed as the maximum."),
        "repo_url":    "https://github.com/RetroCro/TurtleWoW-Mods",
        "source": {
            "kind":          "direct_file",
            "url":           "https://raw.githubusercontent.com/RetroCro/TurtleWoW-Mods/refs/heads/main/Archive/DLL%20BACKUP/no1600x1200.dll",
            "dest":          "no1600x1200.dll",
            "pinned_version": "1.0",
        },
        "register_dll":    "no1600x1200.dll",
        "installed_files": ["no1600x1200.dll"],
    },
    {
        "id":          "OctoLogin",
        "essential": True,
        "name":        "OctoLogin",
        "description": N_("Picks the OctoWoW login server that answers first and the world server with the lowest ping. Its settings and log are in the mods folder."),
        # By Mustafa Yaman (fmustafayaman), MIT. Its own repository is gone
        # (404 since October 2026), so its last release, v1.1.0, is hosted
        # unchanged at Equadis/OctoLogin; same tag, so an installed copy
        # reads as up to date. Point this back if the author republishes.
        "repo_url":    "https://github.com/Equadis/OctoLogin",
        "source": {
            "kind":          "github_release",
            "owner":         "Equadis",
            "repo":          "OctoLogin",
            # The bare DLL, not the zip: the zip nests it under a folder
            # named after the version, which changes every release.
            "asset_pattern": "OctoLogin.dll",
            "prefer_no":     None,
            "extract_map":   None,
            # Where its own installer puts it; it reads OctoLogin.ini and
            # writes OctoLogin.log beside the DLL.
            "dest":          "mods/OctoLogin.dll",
        },
        "register_dll":    "mods/OctoLogin.dll",
        "installed_files": ["mods/OctoLogin.dll"],
    },
]


GITHUB_API = "https://api.github.com"


# Self-update: the updater checks its own GitHub releases once a day.
# This product's own releases. Octo Updater's version line stopped being
# comparable with EqUpdater's the moment the schema and the safety model
# changed, so the daily check watches this repository; upstream is credited
# in the About box and in NOTICE rather than polled.
UPDATER_REPO      = branding.PROJECT_REPO


UPDATER_CHECK_TTL = 86400   # 1 day, cached in the config file


def _parse_version(v: str) -> tuple:
    """'v1.2.0' → (1, 2, 0); non-numeric parts become 0."""
    parts = []
    for p in (v or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def fetch_updater_latest_tag(force: bool = False) -> str | None:
    """Latest release tag of the updater's own repo, cached for a day. Returns
    None when there are no releases yet (GitHub 404) or on any error."""
    now = time.time()
    if not force:
        entry = load_config().get("updater_release_cache", {})
        if (entry.get("tag") is not None
                and (now - entry.get("timestamp", 0)) < UPDATER_CHECK_TTL):
            return entry["tag"]
    try:
        req = urllib.request.Request(
            f"{GITHUB_API}/repos/{UPDATER_REPO}/releases/latest",
            headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            tag = json.load(r).get("tag_name")
    except Exception:
        return None
    if tag:
        update_config(lambda c: c.__setitem__(
            "updater_release_cache", {"timestamp": now, "tag": tag}))
    return tag


def updater_update_available(latest_tag: str) -> bool:
    if not latest_tag:
        return False
    a, b = _parse_version(latest_tag), _parse_version(UPDATER_VERSION)
    n = max(len(a), len(b))               # zero-pad so 1.1 == 1.1.0
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def _codeberg_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    return _gitea_latest("https://codeberg.org/api/v1", owner, repo,
                         raise_errors=raise_errors)


def _gitea_latest(api: str, owner: str, repo: str,
                  raise_errors=False) -> dict | None:
    """The newest full release of a repository on a Gitea (Codeberg,
    OctoWoW Git), from its API root."""
    url = f"{api}/repos/{owner}/{repo}/releases?limit=10&pre-release=false"
    try:
        releases = _api_json(url)
        for rel in releases:
            if not rel.get("prerelease", False) and not rel.get("draft", False):
                return rel
        return releases[0] if releases else None
    except Exception as e:
        if raise_errors:
            raise RuntimeError(_describe_net_error(e)) from e
        return None


DXVK_CONF_CONTENT = """# Low latency - limit queued frames - helps input lag
d3d9.maxFrameLatency = 1
# Forces clamp for AF through DXVK - if you see grass textures shimmering or shaking try false
d3d9.clampNegativeLodBias = True
# Disable logging for performance
dxvk.logLevel = none
# Overrides synchronization interval (Vsync) for presentation.
# Setting this to 0 disables vertical synchronization entirely.
# A positive value 'n' will enable Vsync and repeat the same
# image n times, and a negative value will have no effect.
#
# Value -1 uses the application's setting
d3d9.presentInterval = -1
# VanillaFix handles DPI awareness; avoid double-scaling
d3d9.dpiAware = False
# Enable GPL if supported to reduce stuttering (NVIDIA 473.33+, AMD 24.6.1+)
dxvk.enableGraphicsPipelineLibrary = Auto
# Track pipeline lifetimes to reduce memory usage
dxvk.trackPipelineLifetime = True
# Limit compiler threads to reduce memory usage
dxvk.numCompilerThreads = 2
"""


def _write_dxvk_conf(client_dir: str):
    path = os.path.join(client_dir, "dxvk.conf")
    with atomic.replacing(path, "w", encoding="utf-8") as f:
        f.write(DXVK_CONF_CONTENT)
    log("  Wrote dxvk.conf")


def _github_latest(owner: str, repo: str, raise_errors=False) -> dict | None:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            return json.load(r)
    except Exception as e:
        if raise_errors:
            raise RuntimeError(_describe_net_error(e)) from e
        return None


def _pick_asset(assets: list, pattern: str, prefer_no) -> dict | None:
    import fnmatch
    candidates = [a for a in assets if fnmatch.fnmatch(a["name"], pattern)]
    if prefer_no:
        preferred = [a for a in candidates if prefer_no not in a["name"]]
        if preferred:
            candidates = preferred
    return candidates[0] if candidates else None


#: Release-based mod sources, by kind.
RELEASE_KINDS = ("github_release", "codeberg_release", "gitea_release")


def _release_latest(src: dict, raise_errors: bool = False) -> dict | None:
    """The newest release a release-based mod source publishes."""
    kind = src["kind"]
    if kind == "github_release":
        return _github_latest(src["owner"], src["repo"], raise_errors)
    if kind == "codeberg_release":
        return _codeberg_latest(src["owner"], src["repo"], raise_errors)
    if kind == "gitea_release":
        forge = forge_named(src.get("forge") or "")
        if forge is None or forge.kind != "gitea":
            if raise_errors:
                raise RuntimeError("unknown git host %r" % src.get("forge"))
            return None
        return _gitea_latest(forge.api, src["owner"], src["repo"],
                             raise_errors=raise_errors)
    return None


def _release_version(mod: dict, rel: dict) -> str | None:
    """Version string for a github/codeberg release. Normally the tag name –
    but some mods (e.g. SuperWoW) keep a static tag and edit the release in
    place, so their tag never changes. For those, derive the version from the
    matched asset instead: its filename embeds the real version."""
    src = mod["source"]
    if src.get("version_from") == "asset":
        asset = _pick_asset(rel.get("assets", []), src["asset_pattern"],
                            src.get("prefer_no"))
        if asset and asset.get("name"):
            import re
            m = re.search(r"\d+(?:[._]\d+)+", asset["name"])
            return m.group(0) if m else asset["name"]
    return rel.get("tag_name")


_MOD_VERSION_CACHE_TTL = 3600


#: How old a cached release may be at launch. Each launch looks again: with
#: only the hour above, a release published minutes after a check stayed
#: invisible through every restart in that hour (a tester's ClassicAPI sat
#: on 1.15.16 after 1.16.0 was out). The floor keeps a burst of restarts
#: from spending GitHub's 60-requests-an-hour allowance for apps.
_MOD_VERSION_STARTUP_MAX_AGE = 300


def _slim_release(rel: dict) -> dict:
    """Reduce an API release object to the fields the updater actually uses,
    so the persisted cache stays small."""
    return {
        "tag_name": rel.get("tag_name"),
        # `digest` is GitHub's published SHA-256 of the file ("sha256:..."),
        # absent elsewhere and on older releases. It lets a download be
        # checked, and a file on disk be matched to a release unread.
        "assets": [{"name": a.get("name"),
                    "size": a.get("size", 0),
                    "browser_download_url": a.get("browser_download_url"),
                    "digest": a.get("digest")}
                   for a in rel.get("assets", [])],
    }


#: What the last latest-release lookup of each mod came to, for the row's
#: tooltip and the log: {mod_id: {"outcome", "reason", "tag", "at"}}.
#: Outcomes: "cached" (a recent enough answer was reused), "fetched",
#: "failed" (the source could not be asked; `reason` says why), "no-asset" (a
#: release exists but carries no file this mod installs) and "pinned" (a
#: fixed download, no release feed to ask).
MOD_LOOKUPS: dict = {}


def _note_lookup(mid: str, outcome: str, reason: str | None = None,
                 tag: str | None = None):
    MOD_LOOKUPS[mid] = {"outcome": outcome, "reason": reason, "tag": tag,
                        "at": time.time()}


def release_asset(mod: dict, rel: dict | None) -> dict | None:
    """The file in ``rel`` this mod installs, or None."""
    if not rel:
        return None
    src = mod["source"]
    return _pick_asset(rel.get("assets", []), src.get("asset_pattern") or "*",
                       src.get("prefer_no"))


def _fetch_release_cached(mod: dict, force: bool = False,
                          max_age: float = _MOD_VERSION_CACHE_TTL) -> dict | None:
    """Latest-release lookup backed by a persistent cache in the config file
    ({"mod_release_cache": {mod_id: {"timestamp": epoch, "release": {…}}}}):
    an entry younger than ``max_age`` seconds is used as it is.

    A failed lookup returns None, never the old entry, so the row says it
    could not check rather than presenting a stale release as current. Nor
    is a failure cached: the next look asks again. Either way the outcome
    and its reason land in MOD_LOOKUPS.

    A release without a file this mod installs is no answer either: its tag
    would read as an update that cannot then be downloaded."""
    src  = mod["source"]
    kind = src["kind"]
    if kind not in RELEASE_KINDS:
        return None
    mid = mod["id"]
    now = time.time()
    rel, outcome = None, "fetched"
    if not force:
        entry = load_config().get("mod_release_cache", {}).get(mid)
        if entry and (now - entry.get("timestamp", 0)) < max_age:
            rel, outcome = entry.get("release"), "cached"
    if rel is None:
        try:
            rel = _release_latest(src, raise_errors=True)
        except Exception as e:
            _note_lookup(mid, "failed", tr(str(e)))
            return None
        if rel is None:
            _note_lookup(mid, "failed", tr("the source has no published release"))
            return None
        rel = _slim_release(rel)
        update_config(lambda c: c.setdefault("mod_release_cache", {}).__setitem__(
            mid, {"timestamp": now, "release": rel}))
    tag = _release_version(mod, rel)
    if release_asset(mod, rel) is None:
        _note_lookup(mid, "no-asset",
                     tr("its latest release ({tag}) has no file matching "
                        "{pattern}", tag=tag, pattern=src.get("asset_pattern")),
                     tag)
        return None
    _note_lookup(mid, outcome, tag=tag)
    return rel


def mod_source_label(mod: dict) -> str:
    """Where a mod comes from, for the log: host and owner/repository, or
    the pinned file. Never a URL with anything in it but the path."""
    src = mod["source"]
    kind = src["kind"]
    if kind in RELEASE_KINDS:
        host = {"github_release": "GitHub", "codeberg_release": "Codeberg"}.get(
            kind, src.get("forge") or "git")
        return f"{host} {src.get('owner')}/{src.get('repo')}"
    name = urlsplit(src.get("url") or "").path.rsplit("/", 1)[-1]
    return f"pinned download {name} ({src.get('pinned_version') or '?'})"


def mod_lookup_line(mod: dict, state: dict | None = None) -> str:
    """One diagnostic line: which mod, from where, what the latest-release
    lookup came to and why, and what is known about the installed copy."""
    look = MOD_LOOKUPS.get(mod["id"]) or {}
    line = (f"{mod['name']} [{mod['id']}] from {mod_source_label(mod)}: "
            f"latest release {look.get('outcome') or 'not checked'}")
    if look.get("tag"):
        line += f" ({look['tag']})"
    if look.get("reason"):
        line += f" - {look['reason']}"
    if state is not None:
        ver = state.get("installed_version")
        line += "; installed version " + (
            f"{ver}" + (f" ({state['version_evidence']})"
                        if state.get("version_evidence") else "")
            if ver else "unknown")
    return line


def fetch_mod_latest_version_cached(mod: dict, force: bool = False,
                                    max_age: float = _MOD_VERSION_CACHE_TTL
                                    ) -> str | None:
    src  = mod["source"]
    kind = src["kind"]
    if kind in ("direct_file", "direct_tar"):
        _note_lookup(mod["id"], "pinned", tag=src.get("pinned_version"))
        return src.get("pinned_version")
    rel = _fetch_release_cached(mod, force=force, max_age=max_age)
    if rel:
        return _release_version(mod, rel)
    return None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_digest(asset: dict, data: bytes):
    """Refuse a download that is not the file its release publishes."""
    digest = (asset or {}).get("digest") or ""
    if digest.startswith("sha256:") and \
            hashlib.sha256(data).hexdigest() != digest[7:].lower():
        raise RuntimeError(N_("the download does not match the checksum its "
                              "release publishes"))


def release_by_tag(src: dict, tag: str) -> dict | None:
    """One named release of a release-based source, slimmed; None when it
    does not exist or the source cannot be asked."""
    from urllib.parse import quote
    t = quote(tag, safe="")
    owner, repo = src.get("owner"), src.get("repo")
    kind = src["kind"]
    if kind == "github_release":
        url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{t}"
    elif kind == "codeberg_release":
        url = f"https://codeberg.org/api/v1/repos/{owner}/{repo}/releases/tags/{t}"
    elif kind == "gitea_release":
        forge = forge_named(src.get("forge") or "")
        if forge is None or forge.kind != "gitea":
            return None
        url = f"{forge.api}/repos/{owner}/{repo}/releases/tags/{t}"
    else:
        return None
    try:
        return _slim_release(_api_json(url))
    except Exception:
        return None


def identify_installed_release(mod: dict, files: list, client_dir: str,
                               rel: dict | None = None) -> bool:
    """Whether the files on disk are exactly what installing this mod from
    ``rel`` (or from its pinned download) writes: same names, same bytes.

    The only evidence that names an installed release. A fingerprint taken
    at adoption says the files have not changed since; it says nothing about
    which release they were. A version some other tool wrote down is a hint
    of which release to compare with, not an answer (see octolauncher.py).

    A single file whose release publishes its SHA-256 is compared without a
    download. Anything else is installed into a scratch folder, compared
    and thrown away -- the game folder is not touched."""
    files = [f for f in files or [] if f]
    src = mod["source"]
    if not files or (src["kind"] in RELEASE_KINDS and not rel):
        return False
    try:
        local = {f.lower(): _sha256_file(os.path.join(client_dir, f))
                 for f in files}
    except OSError:
        return False
    asset = release_asset(mod, rel) if rel else None
    if src["kind"] in RELEASE_KINDS and asset is None:
        return False
    digest = (asset or {}).get("digest") or ""
    if (asset and len(files) == 1 and src.get("extract_map") is None
            and not src.get("extract_all") and digest.startswith("sha256:")
            and (src.get("dest") or asset["name"]).lower() == files[0].lower()):
        return digest[7:].lower() == local[files[0].lower()]
    import tempfile
    staging = os.path.join(APP_DATA_DIR, "staging")
    ensure_dir(staging)
    scratch = tempfile.mkdtemp(prefix="identify-", dir=staging)
    try:
        log(f"  Comparing {mod['name']} with its published files...")
        written = install_mod(dict(mod), scratch, release=rel)
        theirs = {w.lower(): _sha256_file(os.path.join(scratch, w))
                  for w in written}
        core = {f.lower() for f in mod.get("core_files") or []}
        if core:
            return all(k in local and theirs.get(k) == local[k] for k in core)
        return bool(theirs) and theirs == local
    except Exception as e:
        log(f"  Could not compare {mod['name']}: {describe_install_error(e)}")
        return False
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _stash_mod_files(client_dir: str, rels: list) -> str:
    """Copy a mod's files aside before they are replaced, so a download or
    install that fails part-way can put them back."""
    import tempfile
    staging = os.path.join(APP_DATA_DIR, "staging")
    ensure_dir(staging)
    stash = tempfile.mkdtemp(prefix="replace-", dir=staging)
    for rel in rels:
        dest = os.path.join(stash, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(client_dir, rel), dest)
    return stash


def _restore_mod_files(client_dir: str, stash: str, rels: list) -> bool:
    try:
        for rel in rels:
            dest = os.path.join(client_dir, rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            shutil.copy2(os.path.join(stash, rel), dest)
        return True
    except OSError as e:
        log(f"  Could not put the previous files back: {e}. "
            f"A copy is in {stash}")
        return False


def _write_whole(dest: str, data: bytes):
    """Write a mod file whole or not at all: a temporary file beside it,
    moved over it once complete. A download or an antivirus interrupting the
    write otherwise leaves a cut-off program, which Windows will not start
    ("WinError 193")."""
    tmp = dest + ".equpdater-new"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


@oplock.guarded(1, "installing a mod")
def install_mod(mod: dict, client_dir: str, release: dict | None = None) -> list:
    src     = mod["source"]
    written = []

    if src["kind"] in ("codeberg_release", "gitea_release"):
        rel = release if release is not None else \
            _release_latest(src, raise_errors=True)
        if not rel:
            raise RuntimeError(N_("no release found on Codeberg")
                               if src["kind"] == "codeberg_release"
                               else N_("no release found on OctoWoW Git"))
        import fnmatch
        assets = rel.get("assets", [])
        asset  = next(
            (a for a in assets if fnmatch.fnmatch(a["name"], src["asset_pattern"])
             and (not src.get("prefer_no") or src["prefer_no"] not in a["name"])),
            None
        )
        if not asset:
            raise RuntimeError(
                f"No matching asset '{src['asset_pattern']}' in {mod['id']} release")
        log(f"  Downloading {asset['name']} ({asset['size']//1024} KB)...")
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
            data = r.read()
        _check_digest(asset, data)
        if src.get("extract_all"):
            written.extend(_install_mod_zip(mod, data, client_dir))
        elif src.get("extract_map") is None:
            dest_rel = src.get("dest") or asset["name"]
            dest     = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            _write_whole(dest, data)
            written.append(dest_rel)
            log(f"  Installed {dest_rel}")
        else:
            import zipfile
            tmp_path = os.path.join(client_dir, f"_mod_tmp_{mod['id']}.zip")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(data)
                with zipfile.ZipFile(tmp_path) as zf:
                    for zip_path, dest_rel in src["extract_map"].items():
                        try:
                            zip_data = zf.read(zip_path)
                        except KeyError:
                            log(f"  Warning: {zip_path} not in zip, skipping")
                            continue
                        dest = os.path.join(client_dir, dest_rel)
                        os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                        _write_whole(dest, zip_data)
                        written.append(dest_rel)
                        log(f"  Installed {dest_rel}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    elif src["kind"] == "github_release":
        rel = release if release is not None else \
            _github_latest(src["owner"], src["repo"], raise_errors=True)
        if not rel:
            raise RuntimeError(N_("no release found on GitHub"))
        asset = _pick_asset(rel.get("assets", []), src["asset_pattern"], src["prefer_no"])
        if not asset:
            raise RuntimeError(
                f"No matching asset '{src['asset_pattern']}' in {mod['id']} release")
        log(f"  Downloading {asset['name']} ({asset['size']//1024} KB)...")
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
            data = r.read()
        _check_digest(asset, data)

        if src.get("extract_all"):
            written.extend(_install_mod_zip(mod, data, client_dir))
        elif src.get("extract_map") is None:
            dest_rel = src.get("dest") or asset["name"]
            dest     = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            _write_whole(dest, data)
            written.append(dest_rel)
            log(f"  Installed {dest_rel}")
        elif asset["name"].endswith(".tar.gz") or asset["name"].endswith(".tgz"):
            import tarfile, io, fnmatch as _fnmatch
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                all_names = tf.getnames()
                for pattern, dest_rel in src["extract_map"].items():
                    matched = (
                        pattern if pattern in all_names
                        else next(
                            (n for n in all_names if _fnmatch.fnmatch(n, pattern)),
                            None
                        )
                    )
                    if matched is None:
                        log(f"  Warning: no file matching '{pattern}' in tar, skipping")
                        continue
                    f_obj    = tf.extractfile(tf.getmember(matched))
                    tar_data = f_obj.read()
                    dest = os.path.join(client_dir, dest_rel)
                    os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                    _write_whole(dest, tar_data)
                    written.append(dest_rel)
                    log(f"  Installed {dest_rel}")
        else:
            import zipfile
            tmp_path = os.path.join(client_dir, f"_mod_tmp_{mod['id']}.zip")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(data)
                with zipfile.ZipFile(tmp_path) as zf:
                    for zip_path, dest_rel in src["extract_map"].items():
                        try:
                            zip_data = zf.read(zip_path)
                        except KeyError:
                            log(f"  Warning: {zip_path} not found in zip, skipping")
                            continue
                        dest = os.path.join(client_dir, dest_rel)
                        os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                        _write_whole(dest, zip_data)
                        written.append(dest_rel)
                        log(f"  Installed {dest_rel}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    elif src["kind"] == "direct_tar":
        log(f"  Downloading {src['url'].split('/')[-1]}...")
        req = urllib.request.Request(src["url"], headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
            data = r.read()
        import tarfile, io as _io, fnmatch as _fnmatch
        with tarfile.open(fileobj=_io.BytesIO(data), mode="r:gz") as tf:
            all_names = tf.getnames()
            for pattern, dest_rel in src["extract_map"].items():
                matched = pattern if pattern in all_names else next(
                    (n for n in all_names if _fnmatch.fnmatch(n, pattern)), None)
                if matched is None:
                    log(f"  Warning: no file matching '{pattern}' in tar, skipping")
                    continue
                tar_data = tf.extractfile(tf.getmember(matched)).read()
                dest = os.path.join(client_dir, dest_rel)
                os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                _write_whole(dest, tar_data)
                written.append(dest_rel)
                log(f"  Installed {dest_rel}")
        if src.get("pinned_version"):
            mod["_resolved_version"] = src["pinned_version"]

    elif src["kind"] == "direct_file":
        url = src["url"]
        log(f"  Downloading {url.rsplit('/', 1)[-1]}...")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=120,
                            allowed_hosts=ALLOWED_DOWNLOAD_HOSTS) as r:
            data = r.read()

        if src.get("extract_map") is None:
            dest_rel = src["dest"]
            dest     = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
            _write_whole(dest, data)
            written.append(dest_rel)
            log(f"  Installed {dest_rel}")
        elif url.endswith(".tar.gz") or url.endswith(".tgz"):
            import tarfile, io, fnmatch as _fnmatch
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
                all_names = tf.getnames()
                for pattern, dest_rel in src["extract_map"].items():
                    matched = (
                        pattern if pattern in all_names
                        else next(
                            (n for n in all_names if _fnmatch.fnmatch(n, pattern)),
                            None
                        )
                    )
                    if matched is None:
                        log(f"  Warning: no file matching '{pattern}' in tar, skipping")
                        continue
                    f_obj    = tf.extractfile(tf.getmember(matched))
                    tar_data = f_obj.read()
                    dest = os.path.join(client_dir, dest_rel)
                    os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                    _write_whole(dest, tar_data)
                    written.append(dest_rel)
                    log(f"  Installed {dest_rel}")
        else:
            import zipfile
            tmp_path = os.path.join(client_dir, f"_mod_tmp_{mod['id']}.zip")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(data)
                with zipfile.ZipFile(tmp_path) as zf:
                    for zip_path, dest_rel in src["extract_map"].items():
                        try:
                            zip_data = zf.read(zip_path)
                        except KeyError:
                            log(f"  Warning: {zip_path} not in zip, skipping")
                            continue
                        dest = os.path.join(client_dir, dest_rel)
                        os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
                        _write_whole(dest, zip_data)
                        written.append(dest_rel)
                        log(f"  Installed {dest_rel}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        if src.get("pinned_version"):
            mod["_resolved_version"] = src["pinned_version"]

    for hook in src.get("post_install", []):
        if hook == "write_dxvk_conf":
            _write_dxvk_conf(client_dir)
            written.append("dxvk.conf")

    return written


@oplock.guarded(1, "removing a mod")
def uninstall_mod(mod: dict, client_dir: str):
    cfg   = load_config()
    state = cfg.get("mods", {}).get(mod["id"], {})
    files = state.get("installed_files", mod.get("installed_files", []))
    for rel in files:
        full = os.path.join(client_dir, rel)
        if os.path.exists(full):
            os.remove(full)
            log(f"  Removed {rel}")
    if mod.get("source", {}).get("extract_all"):
        # A ZIP mod's folders, emptied: gone too, deepest first. Never the
        # game folder's own top folders (Interface, Data, mods...).
        dirs = {os.path.dirname(rel.replace("\\", "/")) for rel in files}
        parents = set()
        for d in dirs:
            while "/" in d:
                parents.add(d)
                d = d.rsplit("/", 1)[0]
        for d in sorted(parents, key=lambda p: -p.count("/")):
            if d.lower() in ("interface/addons",):
                continue
            try:
                os.rmdir(os.path.join(client_dir, d))
            except OSError:
                pass            # not empty, or not there


def _read_lines(path: str) -> list:
    """A text file's lines, with the file closed again."""
    with open(path) as f:
        return f.read().splitlines()


def _dlls_txt_path(client_dir: str) -> str:
    return os.path.join(client_dir, "dlls.txt")


# VanillaFixes injects the DLLs listed in dlls.txt from top to bottom, and
# several of them hook the same client functions. The display and memory
# patches have to land before SuperWoW installs its hooks, and everything that
# builds on SuperWoW's API (nampower, ClassicAPI, AuctionQueryThrottle,
# UnitXP_SP3) has to come after it. Appending each mod as it installed produced
# roughly the reverse of that, which is its own source of instability.
DLL_LOAD_ORDER = [
    "VanillaMultiMonitorFix.dll",
    "no1600x1200.dll",
    "transmogfix.dll",
    "VanillaHelpers.dll",
    "SuperWoWhook.dll",
    "nampower.dll",
    "ClassicAPI.dll",
    "AuctionQueryThrottle.dll",
    "UnitXP_SP3.dll",
    # Hooks only the client's connect import, and leaves it alone if anything
    # else got there first: last, so it never displaces another mod's hook.
    "mods/OctoLogin.dll",
]


def _dll_key(entry: str) -> str:
    """What a dlls.txt entry names, for comparing: case and the direction of
    the slash do not matter (``mods/OctoLogin.dll`` is ``MODS\\OctoLogin.dll``)."""
    return entry.strip().replace("\\", "/").lower()


def _sorted_dll_lines(lines: list) -> list:
    """dlls.txt entries in load order. Entries we do not know about (added by
    hand by the player) keep their relative order and go last - sorted is
    stable."""
    rank = {_dll_key(n): i for i, n in enumerate(DLL_LOAD_ORDER)}
    return sorted((l for l in lines if l.strip()),
                  key=lambda l: rank.get(_dll_key(l), len(rank)))


def _invalidate_dll_cache(client_dir: str):
    """VanillaFixes caches dlls.txt as *absolute* paths in dlls.txt.cache.
    Those paths go stale the moment the game folder is moved or renamed, and a
    stale cache means the mods silently never load. Drop it whenever dlls.txt
    is rewritten so the loader rebuilds it against the current folder."""
    cache = _dlls_txt_path(client_dir) + ".cache"
    try:
        if os.path.exists(cache):
            os.remove(cache)
    except OSError:
        pass


def _write_dlls_txt(client_dir: str, lines: list):
    path = _dlls_txt_path(client_dir)
    with atomic.replacing(path, "w") as f:
        f.write("\n".join(_sorted_dll_lines(lines)) + "\n")
    _invalidate_dll_cache(client_dir)


def add_dll(client_dir: str, name: str):
    path  = _dlls_txt_path(client_dir)
    lines = _read_lines(path) if os.path.exists(path) else []
    if not any(_dll_key(l) == _dll_key(name) for l in lines):
        lines = [l for l in lines if l.strip()] + [name]
    # Rewrite even when the entry was already present: a dlls.txt left behind
    # by an older build can still be in the wrong order.
    _write_dlls_txt(client_dir, lines)


def ensure_dll(client_dir: str, name: str) -> bool:
    """add_dll, but only when the entry is genuinely absent.

    Every write drops dlls.txt.cache and makes VanillaFixes rebuild it on the
    next launch, so a mod that is already registered must not be rewritten just
    to confirm it. Returns True when something was actually repaired."""
    path  = _dlls_txt_path(client_dir)
    lines = _read_lines(path) if os.path.exists(path) else []
    if any(_dll_key(l) == _dll_key(name) for l in lines):
        return False
    add_dll(client_dir, name)
    return True


def reconcile_dlls_txt(client_dir: str, mods_cfg: dict) -> list:
    """Put every enabled, installed mod's DLL back into dlls.txt if it has
    gone missing. Returns the names that were re-registered.

    ensure_dll repairs one mod when the Apply loop visits it - and a targeted
    single-mod update visits only that one mod, so the loop skips the others
    and their entries never get checked. That is exactly how UnitXP_SP3 was
    lost a second time: something outside this updater (the official launcher
    rewrites dlls.txt with its own list) dropped it, and the next thing to run
    was a one-click ClassicAPI update, which wrote the file back without it.

    So the sweep is its own step, run on every Apply regardless of scope, and
    on PLAY - the last thing between the file and the loader. Cheap: one read
    of dlls.txt per mod, a write only when something is actually absent."""
    repaired = []
    for mod in MODS_REGISTRY:
        name = mod.get("register_dll")
        if not name:
            continue
        state = mods_cfg.get(mod["id"], {})
        if not state.get("enabled") or not state.get("installed_version"):
            continue
        files = state.get("installed_files") or mod.get("installed_files") or []
        if not all(os.path.exists(os.path.join(client_dir, f)) for f in files):
            continue   # the DLL itself is gone; registering it would only break the loader
        if ensure_dll(client_dir, name):
            repaired.append(name)
    return repaired


CUSTOM_MOD_HOSTS = {"github.com": "github_release",
                    "codeberg.org": "codeberg_release",
                    "octowow.st/git": "gitea_release"}


CUSTOM_MOD_DIR = "mods"


#: The release files a custom mod can be: a DLL, a ZIP unpacked into the
#: game folder, or an MPQ patch for Data/.
CUSTOM_MOD_EXTS = (".dll", ".zip", ".mpq")


#: Top folders a mod's ZIP may put files in as they are. Any other single
#: top folder (``ModName-1.2/``) is the archive's own and is dropped.
GAME_TOP_FOLDERS = {"interface", "data", "wtf", "fonts", "mods"}


#: Unpacked size past which a mod's ZIP is refused.
CUSTOM_ZIP_MAX_BYTES = 512 * 1024 * 1024


def parse_mod_release_link(text: str) -> dict:
    """A custom mod's source from a pasted link, or ValueError with a
    sentence the player can act on (an N_ message).

    Accepted: a repository, its /releases page, or one of its release
    downloads (which also names the file), on GitHub, Codeberg or OctoWoW Git.
    """
    url = (text or "").strip()
    if not url:
        raise ValueError(N_("Paste a link to the mod's repository or release."))
    if not url.lower().startswith("https://"):
        raise ValueError(N_("The link must start with https://"))
    split = split_repo_url(url)
    if split is None or split[0].label not in CUSTOM_MOD_HOSTS:
        raise ValueError(N_("Mods can be added from GitHub, Codeberg or "
                            "OctoWoW Git releases."))
    forge, owner, repo, rest = split
    asset = None
    if len(rest) >= 4 and rest[0] == "releases" and rest[1] == "download":
        from urllib.parse import unquote
        asset = unquote(rest[-1])
        if not asset.lower().endswith(CUSTOM_MOD_EXTS):
            raise ValueError(N_("That download is not a .dll, .zip or .mpq "
                                "file."))
    elif rest and rest[0] != "releases":
        raise ValueError(N_("Link the repository or one of its release "
                            "downloads, not a page inside it."))
    src = {"kind": CUSTOM_MOD_HOSTS[forge.label], "owner": owner,
           "repo": repo, "asset": asset}
    if src["kind"] == "gitea_release":
        src["forge"] = forge.label
    return src


def _custom_mod_host(src: dict) -> str:
    return {"github_release": "github.com",
            "codeberg_release": "codeberg.org"}.get(src["kind"]) \
        or src.get("forge") or ""


def release_dlls(release: dict | None) -> list:
    """The .dll files a release offers, by name."""
    return sorted((a.get("name") for a in (release or {}).get("assets") or []
                   if (a.get("name") or "").lower().endswith(".dll")),
                  key=str.lower)


def release_mod_files(release: dict | None) -> list:
    """The files a release offers that can be a custom mod: its .dll files
    first (as before), then .zip and .mpq, each by name."""
    names = [a.get("name") or "" for a in (release or {}).get("assets") or []]
    return sorted((n for n in names if n.lower().endswith(CUSTOM_MOD_EXTS)),
                  key=lambda n: (CUSTOM_MOD_EXTS.index(
                      os.path.splitext(n.lower())[1]), n.lower()))


def custom_asset_pattern(name: str) -> str:
    """The pattern that finds ``name`` again in a later release: each run
    of digits matches any (``Mod-1.2.zip`` finds ``Mod-1.3.zip``)."""
    import glob
    return re.sub(r"\d+", "*", glob.escape(name))


def _custom_mod_id(dll: str) -> str:
    return "custom:" + dll.lower()


def _custom_rec_id(rec: dict) -> str:
    """A custom-mod record's registry id. A DLL mod is known by its file
    name (as it always was); a ZIP or MPQ mod, whose file name changes with
    each release, by its repository."""
    if not rec.get("asset"):
        return _custom_mod_id(rec.get("dll") or "")
    where = "/".join(p for p in (rec.get("forge") or rec.get("kind"),
                                 rec.get("owner"), rec.get("repo")) if p)
    return "custom:" + where.lower()


def mod_zip_plan(names: list) -> list:
    """Where each file of a mod's ZIP goes, as (member, path in the game
    folder) pairs. Folders are skipped; one top folder that is not a game
    folder (``ComfyAtmosphere-1.2/``) is dropped. ValueError (an N_
    message) for a ZIP that would write outside the game folder, or that
    carries a program."""
    files = []
    for name in names:
        rel = name.replace("\\", "/")
        if rel.endswith("/"):
            continue
        parts = rel.split("/")
        if (rel.startswith("/") or ":" in rel
                or any(p in ("", ".", "..") for p in parts)):
            raise ValueError(N_("The archive has a file that would land "
                                "outside the game folder."))
        files.append((name, parts))
    if not files:
        raise ValueError(N_("The archive is empty."))
    tops = {parts[0].lower() for _, parts in files}
    if (len(tops) == 1 and all(len(parts) > 1 for _, parts in files)
            and next(iter(tops)) not in GAME_TOP_FOLDERS):
        files = [(name, parts[1:]) for name, parts in files]
    plan = [(name, "/".join(parts)) for name, parts in files]
    if any(dest.lower().endswith((".exe", ".bat", ".cmd", ".ps1", ".scr"))
           for _, dest in plan):
        raise ValueError(N_("The archive has a program in it. EqUpdater "
                            "installs mods, not programs."))
    return plan


def mod_zip_dll(files: list) -> str | None:
    """The DLL a mod's ZIP has loaded through dlls.txt: its one .dll in the
    game folder itself or in mods/, or None (none, or more than one -- which
    one to load is then not EqUpdater's guess)."""
    dlls = [f for f in files if f.lower().endswith(".dll")
            and (f.count("/") == 0
                 or (f.count("/") == 1 and f.lower().startswith("mods/")))]
    return dlls[0] if len(dlls) == 1 else None


def _install_mod_zip(mod: dict, data: bytes, client_dir: str) -> list:
    """Unpack a custom mod's ZIP into the game folder (mod_zip_plan).
    Refused: a file another mod installs, a file of the game's own in Data/,
    and an archive past CUSTOM_ZIP_MAX_BYTES unpacked."""
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = {i.filename: i for i in zf.infolist()}
        plan = mod_zip_plan(list(infos))
        if sum(infos[m].file_size for m, _ in plan) > CUSTOM_ZIP_MAX_BYTES:
            raise RuntimeError(N_("The archive is too large to be a mod."))
        problem = custom_files_conflict([d for _, d in plan], mod_id=mod["id"])
        if problem:
            raise RuntimeError(problem)
        for _, dest_rel in plan:
            full = os.path.join(client_dir, dest_rel)
            if (dest_rel.lower().startswith("data/") and dest_rel.count("/") == 1
                    and os.path.exists(full)):
                raise RuntimeError(N_("The archive would replace one of the "
                                      "game's own files in Data."))
        written = []
        for member, dest_rel in plan:
            full = os.path.join(client_dir, dest_rel)
            os.makedirs(os.path.dirname(full) or client_dir, exist_ok=True)
            with zf.open(member) as src_f, atomic.replacing(full, "wb") as out:
                shutil.copyfileobj(src_f, out)
            written.append(dest_rel)
        log(f"  Installed {len(written)} files")
        return written


def _same_source(mod: dict, src: dict) -> bool:
    """Whether a registry entry installs from the repository ``src`` names."""
    a = mod.get("source") or {}
    return (a.get("kind") == src.get("kind")
            and (a.get("forge") or "") == (src.get("forge") or "")
            and (a.get("owner") or "").lower() == (src.get("owner") or "").lower()
            and (a.get("repo") or "").lower() == (src.get("repo") or "").lower())


def custom_mod_conflict(dll: str, src: dict | None = None) -> str | None:
    """Why ``dll`` cannot be added (an N_ message), or None.

    It must be a plain .dll file name, and no listed or custom mod may
    already install to mods/<dll> -- the same file on disk, which two
    entries cannot share. A mod elsewhere with the same file name is *not*
    a conflict: UnitXP_SP3.dll from a GitHub fork goes to mods/ beside the
    listed Codeberg build in the game folder. Those are two builds of one
    mod, and mod_twins() keeps them from being loaded together."""
    if (not dll or not dll.lower().endswith(".dll")
            or os.path.basename(dll) != dll or dll.startswith(".")
            or any(c in dll for c in '\\/:*?"<>|')):
        return N_("That is not a .dll file name EqUpdater can use.")
    dest = _dll_key(f"{CUSTOM_MOD_DIR}/{dll}")
    name = dll.lower()
    for m in MODS_REGISTRY:
        files = [_dll_key(f) for f in m.get("installed_files", [])]
        if dest in files:
            if src is not None and m.get("custom") and _same_source(m, src):
                return N_("That mod is already in the Mods list.")
            return N_("A mod with that file name is already in the Mods list.")
        # The same name elsewhere is only another build of a mod loaded
        # through dlls.txt. VanillaFixes' VfPatcher.dll or DXVK's d3d9.dll
        # are not loaded that way, and listing one would break the game.
        if (any(os.path.basename(f) == name for f in files)
                and mod_dll_name(m) != name):
            return N_("A mod with that file name is already in the Mods list.")
    return None


def custom_files_conflict(files: list, src: dict | None = None,
                          mod_id: str | None = None) -> str | None:
    """Why a mod installing ``files`` (paths in the game folder) cannot be
    added (an N_ message), or None: no other listed or custom mod may
    install one of the same files. ``mod_id`` is the mod itself, on an
    update."""
    want = {_dll_key(f) for f in files}
    for m in MODS_REGISTRY:
        if m["id"] == mod_id:
            continue
        if want & {_dll_key(f) for f in m.get("installed_files", [])}:
            if src is not None and m.get("custom") and _same_source(m, src):
                return N_("That mod is already in the Mods list.")
            return N_("Another mod in the Mods list installs the same files.")
    return None


def mod_dll_name(mod: dict) -> str:
    """The file name of the DLL a mod has loaded through dlls.txt
    (lower case), or "" for one that is not loaded that way."""
    reg = mod.get("register_dll") or ""
    return os.path.basename(reg.replace("\\", "/")).lower()


def mod_twins(mod_id: str) -> list:
    """Other mods that load a DLL of the same file name: two builds of one
    mod (UnitXP_SP3 from Codeberg and from a GitHub fork). The loader would
    inject both, hooking the same functions twice, so only one may be on."""
    me = next((m for m in MODS_REGISTRY if m["id"] == mod_id), None)
    name = mod_dll_name(me) if me else ""
    if not name:
        return []
    return [m["id"] for m in MODS_REGISTRY
            if m["id"] != mod_id and mod_dll_name(m) == name]


def custom_mod_entry(rec: dict) -> dict:
    """A MODS_REGISTRY entry for a custom-mod record."""
    import glob
    if rec.get("asset"):
        return _custom_asset_entry(rec)
    dll = rec["dll"]
    dest = f"{CUSTOM_MOD_DIR}/{dll}"
    src = {"kind": rec["kind"], "owner": rec["owner"], "repo": rec["repo"],
           # An exact file name, matched with fnmatch: escape its wildcards.
           "asset_pattern": glob.escape(dll), "prefer_no": None,
           "extract_map": None, "dest": dest}
    if rec.get("forge"):
        src["forge"] = rec["forge"]
    host = _custom_mod_host(rec)
    return {
        "id": _custom_mod_id(dll),
        "custom": True,
        "essential": False,
        "name": os.path.splitext(dll)[0],
        "description": tr("Custom mod from {source}.",
                          source=f"{host}/{rec['owner']}/{rec['repo']}"),
        "repo_url": f"https://{host}/{rec['owner']}/{rec['repo']}",
        "source": src,
        "register_dll": dest,
        "installed_files": [dest],
    }


def _custom_asset_entry(rec: dict) -> dict:
    """The entry for a ZIP or MPQ mod: ``asset`` the pattern of its release
    file, ``files`` where it goes in the game folder (as of when it was
    added; each install records what it wrote), ``dll`` the one it has
    loaded through dlls.txt, if any."""
    files = [str(f) for f in rec["files"]]
    if not files:
        raise KeyError("files")
    is_zip = rec["asset"].lower().endswith(".zip")
    src = {"kind": rec["kind"], "owner": rec["owner"], "repo": rec["repo"],
           "asset_pattern": rec["asset"], "prefer_no": None,
           "extract_map": None}
    if is_zip:
        src["extract_all"] = True
    else:
        src["dest"] = files[0]
    if rec.get("forge"):
        src["forge"] = rec["forge"]
    host = _custom_mod_host(rec)
    return {
        "id": _custom_rec_id(rec),
        "custom": True,
        "essential": False,
        "name": rec.get("name") or rec["repo"],
        "description": tr("Custom mod from {source}.",
                          source=f"{host}/{rec['owner']}/{rec['repo']}"),
        "repo_url": f"https://{host}/{rec['owner']}/{rec['repo']}",
        "source": src,
        "register_dll": rec.get("dll") or None,
        "installed_files": files,
    }


def load_custom_mods(cfg: dict | None = None) -> list:
    """Put the configured custom mods into MODS_REGISTRY (in place, so every
    reader sees them), replacing any put there before. Returns their ids."""
    recs = (cfg if cfg is not None else load_config()).get("custom_mods") or []
    MODS_REGISTRY[:] = [m for m in MODS_REGISTRY if not m.get("custom")]
    ids = []
    for rec in recs:
        try:
            entry = custom_mod_entry(rec)
        except (KeyError, TypeError):
            continue
        if entry["id"] in ids or (
                custom_files_conflict(entry["installed_files"]) if rec.get("asset")
                else custom_mod_conflict(rec["dll"])):
            continue
        MODS_REGISTRY.append(entry)
        ids.append(entry["id"])
    return ids


def add_custom_asset_mod(src: dict, asset: str, files: list,
                         dll: str | None = None) -> str:
    """Record a ZIP or MPQ custom mod (``asset`` the release file's name,
    ``files`` where it goes, ``dll`` the one to load, if any) and add it to
    the registry; returns its id."""
    rec = {"kind": src["kind"], "owner": src["owner"], "repo": src["repo"],
           "asset": custom_asset_pattern(asset),
           "name": src["repo"], "files": list(files)}
    if dll:
        rec["dll"] = dll
    if src.get("forge"):
        rec["forge"] = src["forge"]
    mid = _custom_rec_id(rec)
    if any(m["id"] == mid for m in MODS_REGISTRY):
        raise ValueError(N_("That mod is already in the Mods list."))
    problem = custom_files_conflict(files, src)
    if problem:
        raise ValueError(problem)
    cfg = update_config(lambda c: c.setdefault("custom_mods", []).append(rec))
    load_custom_mods(cfg)
    return mid


def add_custom_mod(src: dict, dll: str) -> str:
    """Record a custom mod and add it to the registry; returns its id."""
    problem = custom_mod_conflict(dll, src)
    if problem:
        raise ValueError(problem)
    rec = {"kind": src["kind"], "owner": src["owner"], "repo": src["repo"],
           "dll": dll}
    if src.get("forge"):
        rec["forge"] = src["forge"]
    cfg = update_config(lambda c: c.setdefault("custom_mods", []).append(rec))
    load_custom_mods(cfg)
    return _custom_mod_id(dll)


def forget_custom_mod(mod_id: str) -> None:
    """Drop a custom mod's record, its mods entry and its release cache, and
    take it out of the registry. Its files are the caller's to remove."""
    def _drop(c):
        c["custom_mods"] = [r for r in c.get("custom_mods") or []
                            if _custom_rec_id(r) != mod_id]
        c.get("mods", {}).pop(mod_id, None)
        c.get("mod_release_cache", {}).pop(mod_id, None)
    load_custom_mods(update_config(_drop))


def remove_dll(client_dir: str, name: str):
    path = _dlls_txt_path(client_dir)
    if not os.path.exists(path):
        return
    lines = [l for l in _read_lines(path)
             if l.strip() and _dll_key(l) != _dll_key(name)]
    if not lines:
        os.remove(path)
        _invalidate_dll_cache(client_dir)
    else:
        _write_dlls_txt(client_dir, lines)


def mod_has_files(state: dict) -> bool:
    """Whether a record says EqUpdater looks after files for this mod --
    installed or adopted, version known or not. Version knowledge decides
    updates; it never decides whether the mod is there."""
    return bool(state.get("installed_version")
                or (state.get("managed") and state.get("installed_files")))


def mod_installed_files_present(mod: dict, client_dir: str) -> bool:
    cfg   = load_config()
    state = cfg.get("mods", {}).get(mod["id"], {})
    files = state.get("installed_files", [])
    return bool(files) and all(
        os.path.exists(os.path.join(client_dir, f)) for f in files)


def backup_component(path: str, label: str) -> str | None:
    """Copy a file or folder aside before something destructive happens to it.

    Only ever called from an explicit force/replace path. Routine updates do
    not back anything up: an updater that copied every addon on every update
    would fill the disk with noise nobody reads, and the noise is what makes
    the one useful backup impossible to find.

    Returns where it went, or None. **A failed backup is not fatal** - it is
    reported and the caller decides. Refusing to proceed because a copy could
    not be made would leave somebody unable to fix a broken addon on a full
    disk."""
    if not path or not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(BACKUP_DIR, label, stamp)
    try:
        ensure_dir(dest_dir)
        dest = os.path.join(dest_dir, os.path.basename(path.rstrip("/\\")))
        if os.path.isdir(path):
            shutil.copytree(path, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(path, dest)
        return dest
    except Exception as e:
        log(f"  Could not back up {os.path.basename(path)}: {e}")
        return None


def mod_supports_update_check(mod: dict) -> bool:
    """Whether this mod's source can be asked what the latest version is.

    A pinned direct file has no release feed, so there is nothing to compare
    against and nothing to update to. That is a fact about the source, not a
    judgement about the files."""
    return mod["source"]["kind"] not in ("direct_file", "direct_tar")


def mod_files_on_disk(mod: dict, client_dir: str) -> bool:
    """Whether the files that identify this mod are all present.

    Distinct from `mod_installed_files_present`, which asks about the files a
    previous install recorded. This one answers "is something that looks like
    this mod already here" for a client EqUpdater has never touched -- the
    first-run discovery question. `core_files` is for a mod whose other files
    are optional: without it, VanillaMultiMonitorFix as OctoLauncher installs
    it read as not installed, and ticking it wrote over the DLL unasked."""
    files = mod.get("core_files") or mod.get("installed_files") or []
    return bool(files) and all(
        os.path.exists(os.path.join(client_dir, f)) for f in files)


def mod_found_files(mod: dict, client_dir: str) -> list:
    """The files of this mod actually here: what a record of files found on
    disk should name. A file it never had is not one it has lost."""
    return [f for f in mod.get("installed_files") or []
            if os.path.exists(os.path.join(client_dir, f))]


def mod_component(mod: dict, state: dict, live: dict | None,
                  client_dir: str = "") -> Component:
    """Gather everything the planner needs about one DLL mod.

    **This function decides nothing.** It reads config, disk and the cached
    release lookup, and hands the facts to `plan()`. Every consumer -- the
    row, the badge, Apply, Update All -- goes through here, so they cannot
    disagree about what a mod's state is.

    Two subtleties worth naming:

    `managed` defaults to whether a version was ever recorded, because a
    record written by the inherited schema has no `managed` key and a
    recorded version is the only evidence that updater installed it. Files
    found on disk with no record at all are *not* managed: that is somebody
    else's DLL, or one from another updater, and EqUpdater's job there is to
    show it, not to replace it with its own preferred build.

    `lookup_failed` is true whenever a version check was wanted and did not
    produce an answer. It has to be distinguishable from "no update", because
    an unreachable GitHub must never be the reason files get replaced."""
    installed_ver = state.get("installed_version")
    recorded = bool(installed_ver) or bool(state.get("installed_files"))
    managed = bool(state.get("managed", recorded))
    files = state.get("installed_files") or mod.get("installed_files") or []

    present = bool(files) and all(
        os.path.exists(os.path.join(client_dir, f)) for f in files) \
        if client_dir else True

    # Something on disk that no record claims: discovered, never owned.
    installed = recorded or (bool(client_dir) and mod_files_on_disk(mod, client_dir))

    remote_ver = (live or {}).get("latest_version")
    checkable = mod_supports_update_check(mod)
    lookup_failed = bool(checkable and managed and installed
                         and not remote_ver)

    recorded_hash = state.get("content_hash")
    current_hash = (files_hash(client_dir, files)
                    if recorded_hash and client_dir and present else None)

    return Component(
        id=mod["id"], kind="mod", name=mod.get("name") or mod["id"],
        installed=installed, managed=managed,
        # Unticked means "EqUpdater, leave this out". It cannot describe files
        # EqUpdater never owned: those read "disabled" with nothing to click,
        # when first-run discovery had not recorded them. Not managed is the
        # truth, and it comes with the way in.
        enabled=bool(state.get("enabled", False)) or (installed and not managed),
        ignore_updates=bool(state.get("ignore_updates", False)),
        files_present=present,
        installed_version=installed_ver,
        # A pinned source has no newer release to offer: a known copy is
        # compared with itself. An unknown one is compared with the pinned
        # version, which is what a reinstall would put there.
        remote_version=(remote_ver if checkable else
                        installed_ver or mod["source"].get("pinned_version")),
        recorded_hash=recorded_hash, current_hash=current_hash,
        lookup_failed=lookup_failed,
        error=state.get("error"),
    )


def mod_plan(mod: dict, state: dict, live: dict | None,
             client_dir: str = "") -> Plan:
    return plan(mod_component(mod, state, live, client_dir))
