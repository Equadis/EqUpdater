"""Addons: catalogue, ownership/state, git lookups and ancestry, archive
install, failure classification and the pfUI default-profile patch.

Lifted verbatim from ``equpdater/app.py`` (``tools/lift.py``); ``app.py``
re-exports every name. No Tk, no Qt.
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
import ssl
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
from . import atomic as _atomic
from .logsink import log
from .net import ALLOWED_DOWNLOAD_HOSTS, secure_urlopen
from .errors import (DDOS_CHECK, DdosCheckError, _api_json,
                     _describe_net_error, _error_chain, _refuse_challenge,
                     describe_install_error)
from .mods import _same_source, _write_whole
from . import oplock
from .tweaks import client_exe_locked

APP_NAME = branding.APP_NAME
UPDATER_VERSION = branding.APP_VERSION
SERVER = "https://octowow.st"
UA = branding.USER_AGENT
DOWNLOAD_TIMEOUT = 10    # seconds without any data before a transfer aborts


ADDONS_URL          = f"{SERVER}/api/addons.json"


ADDONS_CATALOG_TTL  = 86400   # 1 day, persisted in the config file


ADDON_SHA_CACHE_TTL = 3600


ADDONS_VERIFY_TTL   = 300     # skip re-verify on tab switches within this


# Curated recommended addons: {folder_name: git_url}. Every entry carries
# its git link explicitly, so a recommended addon always appears — even if
# addons.json renames or drops it. Where the link differs from the catalog,
# it's a deliberate fork preference.
#: The curated sources: for each addon folder, the repository EqUpdater
#: recommends when *it* installs that addon.
#:
#: **A recommendation, and nothing more.** It is not a claim over a folder of
#: that name: an addon already on disk is never re-pointed at the entry here,
#: and one installed from a different repository is offered a switch rather
#: than given one. A user-chosen source outranks this list permanently.
#:
#: Audited 2026-09-24 against the GitHub API for existence and activity.
#: Community forks are the maintained versions on a 1.12 server and are
#: preferred deliberately over upstreams that stopped at Vanilla. Removed in
#: that pass: LevelRange-Octo (repository deleted, no replacement exists).
#: Re-pointed: NampowerSettings, whose repository was deleted, to
#: brues-code's maintained mirror.
#:
#: Known-stale but kept, because no better source exists and the addons still
#: work: ShaguDPS (upstream archived), TurtleMail (sica42's fork, quiet since
#: 2025-09).
RECOMMENDED_ADDONS = {
    "AtlasLoot":            "https://github.com/Otari98/AtlasLoot",
    "aux-addon":            "https://github.com/OldManAlpha/aux-addon",
    "BetterCharacterStats": "https://github.com/pepopo978/BetterCharacterStats",
    "DoiteAuras":           "https://github.com/deceius/DoiteAuras",
    "FlightTracker":        "https://github.com/Lexxoi/FlightTracker",
    "InstanceJournal":      "https://github.com/Arthur-Helias/InstanceJournal",
    "ModernMapMarkers":     "https://github.com/tilare/ModernMapMarkers",
    "NampowerSettings":     "https://github.com/brues-code/NampowerSettings",
    # Folder name from the repository's Questie-Octo.toc.
    "Questie-Octo":         "https://github.com/SandreaSub/Questie-Octo",
    "ShaguDPS":             "https://github.com/shagu/ShaguDPS",
    "SuperAPI":             "https://github.com/balakethelock/SuperAPI",
    "SuperCleveRoidMacros": "https://github.com/brues-code/SuperCleveRoidMacros",
    "Tmog":                 "https://github.com/Otari98/Tmog",
    "TrinketMenu":          "https://github.com/jrc13245/TrinketMenu",
    "TurtleCalendar":       "https://github.com/Wayoff333/TurtleCalendar",
    "TurtleMail":           "https://github.com/sica42/TurtleMail",
    "UnitXP_SP3_Addon":     "https://github.com/rebasedkon/UnitXP_SP3_Addon",
    "WhatsTraining_Turtle": "https://github.com/rebasedkon/WhatsTraining_Turtle",
}


#: Offered on the ADDONS page from these sources, but **not recommended**:
#: no star, not sorted first, never installed by "Install recommended
#: addons". Several are not in addons.json at all, so without an entry here
#: they would not be offered; the others are pinned to the maintained forks
#: rather than the catalogue's original upstreams.
#:
#: Taken off the recommended list 2026-09-24 at the maintainer's request:
#: ItemRack, Magnify, PallyPowerTW, pfQuest, pfQuest-turtle, pfUI, SUCC-bag.
#: Questie-Octo was added here the same day, then promoted to recommended.
LISTED_ADDONS = {
    "ItemRack":             "https://github.com/Otari98/ItemRack",
    "Magnify":              "https://github.com/paokkerkir/Magnify",
    "PallyPowerTW":         "https://github.com/ShikawaLePaladin/PallyPowerTW",
    "pfQuest":              "https://github.com/The-Kludge-Bureau/pfQuest",
    "pfQuest-turtle":       "https://github.com/KameleonUK/pfQuest-turtle",
    "pfUI":                 "https://github.com/brues-code/pfUI",
    "SUCC-bag":             "https://github.com/Otari98/SUCC-bag",
}


#: Every folder EqUpdater has a preferred source for: listed or recommended.
CURATED_ADDONS = {**LISTED_ADDONS, **RECOMMENDED_ADDONS}


# Never *offered* by the updater, even when present in addons.json. Blocking
# hides an entry from the AVAILABLE list; it has no effect on one already
# installed, which is shown and left alone like everything else.
#
# Each entry names what replaces it, because a block whose replacement has
# since disappeared is how somebody ends up with neither -- which is exactly
# what happened to LevelRange below.
BLOCKED_ADDONS = {
    "SuperMacro",        # SuperMacro - SuperWoW Support
    "Rested",            # Rested XP (hazlema)
    "CleveRoidMacros",   # SuperCleveRoidMacros replaces it
    "BlizzPlates",       # Blizzard Plates
    "PallyPower",        # CosminPOP PallyPower — PallyPowerTW replaces it
    # "LevelRange-Turtle" was blocked in favour of LevelRange-Octo, whose
    # repository has since been deleted with nothing replacing it (audit,
    # 2026-09-24). The block is lifted rather than leaving no LevelRange at
    # all. Unblocking only makes it offerable again; no install changes.
}


def _same_git_repo(a, b) -> bool:
    """Compare git URLs ignoring a trailing '.git' / slash and case.

    Delegates to gitcompare.same_repo so the UI, the planner and this file
    agree on what "the same repository" means. They did not before: a
    difference the planner would call a source change could read here as the
    same repo, or the other way about, and the two answers drove different
    buttons."""
    return same_repo(a, b)


def addon_is_managed(saved: dict | None) -> bool:
    """Whether EqUpdater owns this addon.

    The v2 schema says so outright. A record inherited from Octo Updater does
    not have the field, and for those the evidence is a recorded commit sha
    *and* the repository it came from -- which together mean that updater
    installed it. A folder with neither is on disk because somebody put it
    there, and it stays theirs until they say otherwise."""
    if not saved:
        return False
    if "managed" in saved:
        return bool(saved["managed"])
    return bool(saved.get("sha") and saved.get("git"))


def addon_offers_replace(rec: dict) -> bool:
    """Whether an installed addon's row offers Replace: the held-back
    states, and "unable to verify" when the source does have a different
    commit -- something to replace with, just not provably newer."""
    status = rec.get("status")
    if status in ("modified", "localNewer", "diverged"):
        return True
    return (status == "unverifiable" and bool(rec.get("remote_sha"))
            and rec.get("remote_sha") != rec.get("installed_sha"))


def addon_component(folder: str, saved: dict | None, *,
                    installed: bool, remote_sha=None,
                    configured_source=None, current_hash=None,
                    ancestry_result=None, lookup_failed: bool = False,
                    error=None, installed_version=None) -> Component:
    """Gather one addon's facts for the planner.

    The one thing worth spelling out: `installed_source` is the repository
    the files *came from*, and `configured_source` is the one the catalogue
    currently recommends. Keeping them apart is what turns "this addon is out
    of date" back into the two different questions it always was -- is there
    a newer commit on the source you installed from, and separately, would
    you like to switch to a different fork."""
    saved = saved or {}
    managed = addon_is_managed(saved)
    return Component(
        id=folder, kind="addon", name=folder,
        installed=installed, managed=managed, enabled=True,
        ignore_updates=bool(saved.get("ignore_updates", False)),
        files_present=installed,
        installed_sha=saved.get("sha"), remote_sha=remote_sha,
        ancestry=ancestry_result,
        installed_source=saved.get("git"),
        configured_source=configured_source,
        custom_source=bool(saved.get("custom_source",
                                     saved.get("custom", False))),
        recorded_hash=saved.get("content_hash"), current_hash=current_hash,
        installed_version=installed_version,
        lookup_failed=lookup_failed, error=error,
    )


#: Planner status -> the short machine string the addon rows and badges use.
#: One mapping, so a status cannot mean one thing to the badge and another to
#: the row -- which is what "outOfDate" managed to do for four situations at
#: once.
ADDON_STATUS_NAMES = {
    Status.UPDATE_AVAILABLE: "updateAvailable",
    Status.UP_TO_DATE:       "upToDate",
    Status.LOCAL_NEWER:      "localNewer",
    Status.UNMANAGED:        "unmanaged",
    Status.MODIFIED:         "modified",
    Status.DIFFERENT_SOURCE: "differentSource",
    Status.DIVERGED:         "diverged",
    Status.UNVERIFIABLE:     "unverifiable",
    Status.VERSION_UNKNOWN:  "unverifiable",
    Status.IGNORED:          "ignored",
    Status.ERROR:            "invalid",
    Status.MISSING_FILES:    "invalid",
    Status.NOT_INSTALLED:    "available",
    Status.DISABLED:         "upToDate",
}


#: Where addon sources may live, as people type them -- the git host
#: registry in gitcompare, so a new host (prefixed Gitea included) is added
#: there once.
ADDON_GIT_HOSTS = tuple(h.label for h in GIT_HOSTS)


#: Where an addon archive may be downloaded from.
ADDON_ZIP_HOSTS = frozenset().union(*(h.downloads_from for h in GIT_HOSTS))


#: The last conflict set logged, so a standing conflict is logged once.
_ADDON_CONFLICT_LOGGED: list = []


def _log_addon_dir_state(state) -> None:
    if state.renamed:
        log(f"Addons folder: renamed Interface/{state.renamed} to "
            f"Interface/{gamepaths.ADDONS} (letter case only; no addon changed).", "ok")
    for entry in state.moved:
        log(f"Addons folder: moved Interface/{entry} into Interface/{gamepaths.ADDONS}.", "ok")
    for entry in state.collapsed:
        log(f"Addons folder: Interface/{entry} was identical to the copy in "
            f"Interface/{gamepaths.ADDONS}; kept one.", "dim")
    for note in state.notes:
        log(f"Addons folder: {note}.", "dim")
    names = sorted(c[0] for c in state.conflicts)
    if names != _ADDON_CONFLICT_LOGGED:
        _ADDON_CONFLICT_LOGGED[:] = names
        for entry, other, mine in state.conflicts:
            log(f"Addons folder: {entry} exists in both {other} and {mine} with "
                f"different contents; both kept. Addon changes are paused until "
                f"you choose which to keep.", "err")


def addons_path(client_dir: str) -> str:
    """Interface/AddOns of ``client_dir``, the one place the addons folder is
    named. On a case-sensitive filesystem an existing Interface/Addons (or
    any other case) is found and settled first -- see equpdater.gamepaths."""
    state = gamepaths.prepare_addons_dir(client_dir)
    _log_addon_dir_state(state)
    return state.path


def writable_addons_path(client_dir: str) -> str:
    """addons_path for installing, updating or removing: raises
    gamepaths.AddonDirConflict while two case variants of the folder hold
    different copies of the same addon."""
    state = gamepaths.prepare_addons_dir(client_dir)
    _log_addon_dir_state(state)
    if state.conflicts:
        raise gamepaths.AddonDirConflict(state)
    return state.path


def check_custom_addon_url(text: str):
    """(repository URL, folder, None) for a link the custom-addon dialog
    can install, or (None, None, message) saying why not."""
    url = canonical_repo_url((text or "").strip()).rstrip("/")
    if url.lower().endswith(".git"):
        url = url[:-4]
    if not is_allowed_git_url(url):
        return None, None, N_("URL must be https from an allowed host.")
    folder = repo_ref(url).repo
    if not folder or folder in (".", "..") or "\\" in folder:
        return None, None, N_("Could not derive addon folder name.")
    return url, folder, None


def addon_install_source(git):
    """The URL an addon is installed from, and recorded as its source: the
    canonical form of ``git``. Raises when it is not a repository on an
    allowed host -- the check is the same one, made on the canonical URL."""
    url = canonical_repo_url(git) if git else git
    if not url or not is_allowed_git_url(url):
        raise RuntimeError(N_("Addon URL is not from an allowed git host"))
    return url


def is_allowed_git_url(url: str) -> bool:
    """An https link to a repository on one of the known git hosts."""
    if not (url or "").strip().lower().startswith("https://"):
        return False
    ref = repo_ref(url)
    return ref is not None and ref.forge is not None


def _slim_addon_catalog(catalog: list) -> list:
    """Keep only the catalog fields the updater actually uses, so the
    persisted cache stays small."""
    slim = []
    for a in catalog:
        toc = a.get("toc") or {}
        slim.append({
            "name":        a.get("name"),
            "git":         a.get("git"),
            "branch":      a.get("branch"),
            "ref":         a.get("ref"),
            "description": a.get("description"),
            "toc": {k: toc[k] for k in ("Title", "Notes", "Interface")
                    if k in toc},
        })
    return slim


def fetch_addons_catalog(force=False) -> list:
    """Addon catalog, cached in the config file for a day
    ({"addons_catalog_cache": {"timestamp": epoch, "catalog": […]}})."""
    now = time.time()
    if not force:
        entry = load_config().get("addons_catalog_cache", {})
        if (entry.get("catalog") is not None
                and (now - entry.get("timestamp", 0)) < ADDONS_CATALOG_TTL):
            return entry["catalog"]
    req = urllib.request.Request(ADDONS_URL, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=10) as r:
        catalog = _slim_addon_catalog(json.load(r))
    update_config(lambda c: c.__setitem__(
        "addons_catalog_cache", {"timestamp": now, "catalog": catalog}))
    return catalog


def read_toc_file(path: str) -> dict:
    """Parse '## Key: Value' metadata lines from a WoW addon .toc file."""
    toc = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return toc
    if content.startswith("\ufeff"):   # strip UTF-8 BOM
        content = content[1:]
    for line in content.splitlines():
        if not line.startswith("## "):
            continue
        key, sep, value = line[3:].partition(":")
        if sep:
            toc[key.strip()] = value.strip()
    return toc


def parse_wow_colored(text: str):
    """Split a string containing WoW colour escapes (|cAARRGGBB … |r) into
    [(segment, "#rrggbb" | None), …] for rendering."""
    import re
    segments = []
    color = None
    pos = 0
    for m in re.finditer(r"\|c[0-9a-fA-F]{8}|\|r", text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], color))
        tok = m.group(0)
        color = f"#{tok[4:]}" if tok.startswith("|c") else None
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], color))
    return [(t, c) for t, c in segments if t]


def strip_wow_colors(text: str) -> str:
    return "".join(t for t, _c in parse_wow_colored(text))


def _git_parts(git_url: str):
    """→ (kind, repo_url, owner, repo, api_base); kind ∈ github/gitlab/gitea.
    From the git host registry, so a forge mounted under a path prefix
    (octowow.st/git/<owner>/<repo>, API at /git/api/v1) needs no special
    case here. An unknown host is refused rather than guessed at."""
    ref = repo_ref(git_url)
    if ref is None or ref.forge is None:
        raise ValueError(f"Unsupported git URL: {git_url}")
    return ref.forge.kind, ref.url, ref.owner, ref.repo, ref.forge.api


#: Archives fetched to read their commit, kept briefly so installing that
#: commit does not download it a second time. {sha: (time, bytes)}
_ARCHIVES: dict = {}


_ARCHIVE_KEEP = 600


_ARCHIVES_MAX = 4


_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def archive_commit(data: bytes) -> str | None:
    """The commit a git-archive zip was made from (its comment), or None."""
    import zipfile
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            comment = zf.comment.decode("ascii", "replace").strip().lower()
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    return comment if _SHA.fullmatch(comment) else None


def _download_archive(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=120, allowed_hosts=ADDON_ZIP_HOSTS) as r:
        data = r.read()
        _refuse_challenge(data, r.headers)
    return data


def _commit_from_archive(git_url: str, pin=None) -> str:
    """The latest commit of the default branch (or ``pin``), read from the
    repository's archive. Raises when the archive does not say."""
    from urllib.parse import quote
    _kind, repo_url, _owner, _repo, _api = _git_parts(git_url)
    data = _download_archive(f"{repo_url}/archive/{quote(pin or 'HEAD', safe='')}.zip")
    sha = archive_commit(data)
    if not sha:
        raise DdosCheckError(DDOS_CHECK)
    now = time.time()
    for old in [k for k, (t, _d) in _ARCHIVES.items() if now - t > _ARCHIVE_KEEP]:
        _ARCHIVES.pop(old, None)
    while len(_ARCHIVES) >= _ARCHIVES_MAX:
        _ARCHIVES.pop(next(iter(_ARCHIVES)))
    _ARCHIVES[sha] = (now, data)
    return sha


#: How long "could not tell" is remembered for one commit pair before the
#: compare endpoint is asked again. A definite answer is kept for good: which
#: of two commits came first cannot change.
ADDON_ANCESTRY_RETRY = 3600


#: Pairs remembered at most; the oldest go first.
ADDON_ANCESTRY_CACHE_MAX = 200


def addon_ancestry(git_url: str, installed_sha: str, remote_sha: str,
                   force: bool = False) -> Ancestry:
    """ancestry(), asking the host's compare endpoint only for a commit pair
    it has not answered before.

    OctoWoW asked that the compare endpoint be called only when the latest
    commit has actually changed. The verify already skips it when the latest
    commit is the installed one; this also skips it while an update stays
    pending, when every verify (each launch, each Addons tab visit after a
    few minutes) would otherwise ask the same question again. ``force``
    (a refresh the player asked for) retries an "unknown", never a
    definite answer."""
    if not installed_sha or not remote_sha:
        return Ancestry.UNKNOWN
    if installed_sha == remote_sha:
        return Ancestry.IDENTICAL
    ref = repo_ref(git_url)
    where = ref.url.lower() if ref else (git_url or "").strip().lower()
    key = f"{where}|{installed_sha}|{remote_sha}"
    now = time.time()
    entry = load_config().get("addon_ancestry_cache", {}).get(key)
    if entry:
        try:
            known = Ancestry(entry.get("result"))
        except ValueError:
            known = None
        if known is not None and known is not Ancestry.UNKNOWN:
            return known
        if (known is Ancestry.UNKNOWN and not force
                and now - entry.get("timestamp", 0) < ADDON_ANCESTRY_RETRY):
            return known
    result = ancestry(git_url, installed_sha, remote_sha, fetch=_api_json)

    def remember(c):
        cache = c.setdefault("addon_ancestry_cache", {})
        cache[key] = {"result": result.value, "timestamp": now}
        for old in sorted(cache, key=lambda k: cache[k].get("timestamp", 0)
                          )[:max(0, len(cache) - ADDON_ANCESTRY_CACHE_MAX)]:
            cache.pop(old, None)
    update_config(remember)
    return result


ADDON_FAILURES = {
    "challenge": N_("{host} is showing its DDoS-protection check instead of "
                    "the download. Try again later."),
    "forbidden": N_("{host} refused the download (HTTP 403). It may be "
                    "limiting automated downloads. Try again later."),
    "rate_limit": N_("{host} is limiting requests (HTTP 429). Try again "
                     "later."),
    "not_found": N_("{host} says the repository or version does not exist "
                    "(HTTP 404)."),
    "server": N_("{host} is having problems (HTTP {code}). Try again later."),
    "http": N_("{host} refused the download (HTTP {code})."),
    "timeout": N_("{host} did not answer in time. Try again later."),
    "dns": N_("Could not look up {host}. Check your internet connection or "
              "DNS."),
    "tls": N_("The secure connection to {host} failed (TLS/certificate "
              "error)."),
    "connection": N_("{host} refused or dropped the connection. Try again "
                     "later."),
    "network": N_("Could not connect to {host}."),
    "archive": N_("The file from {host} is not a valid addon archive. Try "
                  "again later."),
    "lookup": N_("Could not find the latest version of the addon at "
                 "{host}."),
    "conflict": N_("The AddOns folder exists twice with different copies of "
                   "this addon. Choose which to keep, then try again."),
    "source": N_("The addon's address is not on an allowed git host."),
    "no_space": N_("Not enough disk space to write the addon files."),
    "permission": N_("No permission to write to the AddOns folder."),
    "antivirus": N_("The files were blocked or removed while being written – usually antivirus. Open Settings (⚙) → 'Exclude from Defender', then retry."),
    "write": N_("Could not write the addon files ({error})."),
    "unknown": N_("Unexpected error ({error})."),
}


def addon_failure(e: BaseException, url: str | None = None) -> tuple:
    """(category, ADDON_FAILURES template, values) for a failed addon
    install or update. ``url`` is the addon's source, for the host name when
    the exception does not carry one."""
    import errno as _errno
    import socket
    import urllib.error
    import zipfile
    from urllib.parse import urlsplit

    def host_of(u):
        try:
            return urlsplit(u or "").hostname or ""
        except ValueError:
            return ""

    chain = _error_chain(e)
    host = ""
    for x in chain:
        if isinstance(x, urllib.error.HTTPError):
            host = host_of(getattr(x, "url", None) or x.geturl())
            break
    host = host or host_of(url) or "?"

    def out(category, **values):
        return category, ADDON_FAILURES[category], dict(host=host, **values)

    for x in chain:
        if isinstance(x, gamepaths.AddonDirConflict):
            return out("conflict")
        if isinstance(x, DdosCheckError):
            return out("challenge")
        if isinstance(x, urllib.error.HTTPError):
            if x.code == 403:
                return out("forbidden", code=x.code)
            if x.code == 429:
                return out("rate_limit", code=x.code)
            if x.code == 404:
                return out("not_found", code=x.code)
            if x.code >= 500:
                return out("server", code=x.code)
            return out("http", code=x.code)
        if isinstance(x, zipfile.BadZipFile):
            return out("archive")
    for x in chain:
        reason = getattr(x, "reason", None) if isinstance(
            x, urllib.error.URLError) else x
        for r in (x, reason):
            if isinstance(r, (socket.timeout, TimeoutError)) or (
                    isinstance(r, str) and "timed out" in r.lower()):
                return out("timeout")
            if isinstance(r, socket.gaierror):
                return out("dns")
            if isinstance(r, ssl.SSLError) or isinstance(
                    r, getattr(ssl, "CertificateError", ssl.SSLError)):
                return out("tls")
            if isinstance(r, (ConnectionRefusedError, ConnectionResetError,
                              ConnectionAbortedError, BrokenPipeError)):
                return out("connection")
        if isinstance(x, urllib.error.URLError):
            return out("network")
    first = chain[0]
    text = str(first)
    if text == "Addon URL is not from an allowed git host":
        return out("source")
    if text == "Could not resolve remote commit":
        return out("lookup")
    if isinstance(first, OSError):
        code = getattr(first, "errno", None)
        if code == _errno.ENOSPC:
            return out("no_space")
        if platforms.WINDOWS and code in (2, 13, 22):
            return out("antivirus")
        if code in (_errno.EACCES, _errno.EPERM, _errno.EROFS):
            return out("permission")
        return out("write", error=first.strerror or text)
    if any(isinstance(x, (ValueError, KeyError, IndexError, TypeError))
           for x in chain[1:]) and len(chain) > 1:
        return out("lookup")
    return out("unknown", error=text or type(first).__name__)


#: Why the last look at an addon's source failed: {cache key: reason}.
#: Cleared by the next look that succeeds. A failure is never cached, so the
#: next look -- any verify, or the row's "can't check" -- asks again.
ADDON_LOOKUP_FAILURES: dict = {}


#: Most of a ref advertisement read while looking for a pinned branch or tag.
_GIT_REFS_MAX = 4 * 1024 * 1024


def _git_refs_sha(repo_url: str, pin=None) -> str | None:
    """The latest commit of the default branch (or ``pin``), from git's own
    ref advertisement: what ``git ls-remote`` reads.

    GitHub's REST API allows an address 60 requests an hour, shared by every
    addon, every mod release check and anyone else behind that address.
    Twenty addons and a few refreshes use it up, and then every addon fails
    at once, whatever its repository. This endpoint does not count against
    that allowance, and the default branch's commit is its first line. Returns
    None when the source does not list the commit; raises when it cannot be
    asked."""
    req = urllib.request.Request(
        f"{repo_url}.git/info/refs?service=git-upload-pack",
        headers={"User-Agent": UA})
    wanted = {f"refs/heads/{pin}", f"refs/tags/{pin}", f"refs/tags/{pin}^{{}}",
              str(pin)} if pin else {"HEAD"}
    found = {}
    with secure_urlopen(req, timeout=15) as r:
        if not (r.headers.get("Content-Type") or "").startswith(
                "application/x-git-upload-pack"):
            raise ValueError("the source did not answer as a git repository")
        seen = 0
        while seen < _GIT_REFS_MAX:
            size = r.read(4)
            if len(size) < 4:
                break
            n = int(size, 16)
            if n < 4:           # a flush between the service line and the refs
                seen += 4
                continue
            line = r.read(n - 4)
            seen += n
            head = line.split(b"\0", 1)[0].rstrip(b"\n").decode("utf-8", "replace")
            sha, _, name = head.partition(" ")
            if name in wanted and _SHA.fullmatch(sha):
                found[name] = sha
                if not pin:
                    return sha
                if not name.startswith("refs/tags/") or name.endswith("^{}"):
                    return sha
    # A pinned annotated tag: the commit it peels to, if it was listed.
    return (found.get(f"refs/tags/{pin}^{{}}") or found.get(f"refs/tags/{pin}")
            if pin else None)


def addon_lookup_reason(git_url: str, branch=None, ref=None) -> str | None:
    return ADDON_LOOKUP_FAILURES.get(f"{git_url}#{ref or branch or ''}")


def addon_remote_sha(git_url: str, branch=None, ref=None,
                     force=False, raise_errors=False) -> str | None:
    """Latest commit sha of a repo's branch (or pinned ref), cached in the
    config file so repeated verifies don't burn API quota. Returns None on
    failure, with the cause in ADDON_LOOKUP_FAILURES -- or raises with that
    cause when raise_errors is set."""
    key = f"{git_url}#{ref or branch or ''}"
    now = time.time()
    if not force:
        entry = load_config().get("addon_sha_cache", {}).get(key)
        if entry and (now - entry.get("timestamp", 0)) < ADDON_SHA_CACHE_TTL:
            ADDON_LOOKUP_FAILURES.pop(key, None)
            return entry.get("sha")

    pin = ref or branch          # explicit branch/ref when the caller has one
    sha = None
    try:
        kind, _repo_url, owner, repo, api = _git_parts(git_url)
        if kind == "github":
            # A full commit id is not a ref the advertisement lists.
            if not (pin and _SHA.fullmatch(pin)):
                try:
                    sha = _git_refs_sha(_repo_url, pin)
                except Exception as e:
                    log(f"{owner}/{repo}: git refs not available "
                        f"({_describe_net_error(e)}), asking the API")
            if sha:
                pass
            elif pin:
                sha = _api_json(
                    f"{api}/repos/{owner}/{repo}/commits/{pin}").get("sha")
            else:
                lst = _api_json(
                    f"{api}/repos/{owner}/{repo}/commits?per_page=1")
                sha = lst[0].get("sha") if lst else None
        elif kind == "gitlab":
            from urllib.parse import quote
            proj = quote(f"{owner}/{repo}", safe="")
            if pin:
                sha = _api_json(
                    f"{api}/projects/{proj}/repository/commits/"
                    f"{quote(pin, safe='')}").get("id")
            else:
                lst = _api_json(
                    f"{api}/projects/{proj}/repository/commits?per_page=1")
                sha = lst[0].get("id") if lst else None
        else:  # gitea / codeberg / OctoWoW Git
            q = f"?sha={pin}&limit=1" if pin else "?limit=1"
            try:
                lst = _api_json(f"{api}/repos/{owner}/{repo}/commits{q}")
                sha = lst[0].get("sha") if lst else None
            except DdosCheckError:
                # The API is behind the host's DDoS check; its archives are
                # not, and say which commit they are. See _commit_from_archive.
                sha = _commit_from_archive(git_url, pin)
    except Exception as e:
        reason = _describe_net_error(e)
        ADDON_LOOKUP_FAILURES[key] = reason
        if raise_errors:
            raise RuntimeError(reason) from e
        return None
    if sha:
        ADDON_LOOKUP_FAILURES.pop(key, None)
        update_config(lambda c: c.setdefault("addon_sha_cache", {}).__setitem__(
            key, {"timestamp": now, "sha": sha}))
    else:
        ADDON_LOOKUP_FAILURES[key] = N_("the source lists no commits")
    return sha


def addon_cached_sha(git_url: str, branch=None, ref=None):
    """Cached remote sha regardless of age – never touches the network."""
    key = f"{git_url}#{ref or branch or ''}"
    entry = load_config().get("addon_sha_cache", {}).get(key)
    return entry.get("sha") if entry else None


def addon_zip_url(git_url: str, sha: str) -> str:
    kind, repo_url, _owner, repo, _api = _git_parts(git_url)
    if kind == "gitlab":
        return f"{repo_url}/-/archive/{sha}/{repo}-{sha}.zip"
    return f"{repo_url}/archive/{sha}.zip"


def _rmtree_force(path):
    """Like shutil.rmtree, but also removes read-only files. Plain rmtree
    raises PermissionError on Windows when it meets a read-only file (e.g. a
    .git object store from a manual clone, or a read-only addon shipped in an
    old zip); this clears the read-only bit and retries."""
    def handler(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handler)      # onerror deprecated in 3.12
    else:
        shutil.rmtree(path, onerror=handler)


@oplock.guarded(0, "installing an addon")
def install_addon_files(client_dir: str, folder: str, git_url: str, sha: str):
    """Download the repo archive at `sha` and unpack it into
    Interface/AddOns/<folder>, atomically replacing any existing copy.
    Refused (gamepaths.AddonDirConflict) before anything is downloaded while
    the addons folder has a case conflict."""
    addons_root = writable_addons_path(client_dir)
    kept = _ARCHIVES.pop(sha, None)
    if kept and time.time() - kept[0] <= _ARCHIVE_KEEP:
        data = kept[1]               # fetched moments ago to read its commit
        log(f"  Installing {folder} @ {sha[:10]}…")
    else:
        log(f"  Downloading {folder} @ {sha[:10]}…")
        data = _download_archive(addon_zip_url(git_url, sha))

    import zipfile
    import io
    dest_root = os.path.join(addons_root, folder)
    tmp_root  = dest_root + ".tmp_install"
    tmp_abs   = os.path.abspath(tmp_root)
    if os.path.isdir(tmp_root):
        _rmtree_force(tmp_root)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # Strip the archive's top-level "<repo>-<sha>/" directory and
                # normalise separators (a zip entry may use "/" or "\").
                parts = [p for p in info.filename.replace("\\", "/").split("/")[1:]
                         if p not in ("", ".")]
                if not parts or ".." in parts:
                    continue
                target = os.path.join(tmp_root, *parts)
                # Defence in depth: never write outside the target folder even
                # if the guards above are somehow bypassed.
                if not os.path.abspath(target).startswith(tmp_abs + os.sep):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        # The installed copy is moved aside, not deleted, until the new one
        # is in place: a failed replace puts it back as it was.
        old_root = dest_root + ".old_install"
        if os.path.isdir(old_root):
            _rmtree_force(old_root)
        had_old = os.path.isdir(dest_root)
        if had_old:
            os.replace(dest_root, old_root)
        try:
            os.replace(tmp_root, dest_root)
        except BaseException:
            if had_old:
                os.replace(old_root, dest_root)
            raise
        if had_old:
            try:
                _rmtree_force(old_root)
            except OSError as e:
                log(f"  Could not remove {old_root}: {e}")
    except BaseException:
        # Never leave a half-written ".tmp_install" behind on failure
        if os.path.isdir(tmp_root):
            try:
                _rmtree_force(tmp_root)
            except Exception:
                pass
        raise
    log(f"  Installed addon {folder}")


# The curated profile (JSON captured from a configured pfUI, profile renamed to
# "Default"). Loaded as a Python dict and emitted as a Lua table at patch time.
PFUI_DEFAULT_PROFILE = json.loads(r'''
{"appearance":{"border":{"default":"-1"},"castbar":{"castbarcolor":"1,0.796,0.251,0.8"},"cd":{"debuffs":"1","font":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","milliseconds":"0"},"infight":{"health":"0"},"minimap":{"arrowscale":"2"}},"buffs":{"hidelist":"","showoverflow":"1","showspillover":"1"},"castbar":{"focus":{"showicon":"1","showtimer":"0"},"player":{"hide_blizz":"0","hide_pfui":"1","showtimer":"0"},"target":{"showicon":"1","showtimer":"0"}},"character":{"inventory":{"durability":"0"},"reputation":{"repRequired":"0"}},"disabled":{"actionbar":"1","addonbuttons":"0","addoncompat":"0","addons":"0","afkcam":"0","autoshift":"0","autovendor":"0","bags":"1","bgscore":"0","bubbles":"1","buff":"1","buffwatch":"0","castbar":"0","chat":"1","chatcopy":"0","combopoints":"0","cooldown":"0","custom":"0","easteregg":"0","energytick":"0","eqcompare":"0","equipmentmanager":"0","farmmode":"0","feigndeath":"0","firstrun":"0","focus":"0","gm":"0","group":"0","gryphons":"0","hdgraphic":"0","hoverbind":"0","hunterbar":"0","infight":"0","innervatecall":"0","itemclick":"0","itemcount":"1","loot":"1","macrotweak":"0","map":"0","mapcolors":"0","mapreveal":"0","marktracking":"0","minimap":"0","mirrortimers":"0","mouseover":"0","nameplates":"0","nampower":"0","panel":"0","pet":"0","pettarget":"0","pixelperfect":"0","player":"0","questitem":"0","raid":"0","roll":"1","screenshot":"0","sellvalue":"0","share":"0","skin":"0","skin_Auctionhouse":"0","skin_Barbershop":"0","skin_Battlefield":"0","skin_Battlefield Minimap":"0","skin_Battlefield Score":"0","skin_Books":"0","skin_Character":"0","skin_Coin Pickup":"0","skin_Color Picker":"0","skin_Dress Up Frame":"0","skin_Everlook Broadcasting":"0","skin_Flightmaster":"1","skin_Friends":"1","skin_GM Survey":"0","skin_Game Menu":"0","skin_Gossip and Quest":"1","skin_Guild Registrar":"0","skin_Guild Tabard":"0","skin_Help":"0","skin_Inspect":"0","skin_KeyBindings":"0","skin_Macro":"0","skin_Mailbox":"1","skin_Merchant":"1","skin_Opacity":"0","skin_Options - Interface":"0","skin_Options - New":"0","skin_Options - Sound":"0","skin_Options - Video":"0","skin_Pet Stable Master":"0","skin_Petition":"0","skin_Popup Dialogs":"0","skin_Profession":"1","skin_Quest Log":"1","skin_Quest Timer":"0","skin_RaidUI":"0","skin_Readycheck":"0","skin_Spellbook":"1","skin_Stack Split":"0","skin_Talents":"1","skin_Tooltips":"0","skin_Trade":"1","skin_Trainer":"1","skin_Transmog":"0","skin_Turtle LFT":"0","skin_Turtle Shop":"0","skin_TurtleCalendar":"0","skin_TurtleMail":"0","skin_Tutorial":"0","socialmod":"0","superwow":"0","swingtimer":"0","target":"0","targettarget":"0","targettargettarget":"0","thirdparty":"0","thirdparty-vanilla":"0","tooltip":"0","totems":"0","tracking":"0","turtle-wow":"0","uf_tukui":"0","unitxp":"0","unlock":"0","unusable":"0","updatenotify":"0","whisperproxy":"0","xpbar":"1"},"global":{"autosell":"1","font_blizzard":"1","font_unit":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","font_unit_name":"Interface\\AddOns\\pfUI\\fonts\\Continuum.ttf","profile":"Default"},"gui":{"showdisabled":"0"},"nameplates":{"barcombatstate":"0","ccombatnothreat":"0","ccombatofftank":"0","ccombatstun":"0","ccombatthreat":"0","debuffanim":"1","debuffs":{"blacklist":"#Mana Attuned#Mercenary","filter":"blacklist","showstacks":"1"},"debuffsize":"20","guessdebuffs":"1","outcombatstate":"0","overlap":"1","spellname":"1"},"panel":{"left":{"center":"none","left":"none","right":"none"},"other":{"minimap":"time"},"right":{"center":"none","left":"none","right":"none"},"seconds":"0"},"position":{"TicketStatusFrame":{"anchor":"CENTER","parent":"UIParent","xpos":1100,"ypos":300},"WorldMapFrame":{"alpha":1,"anchor":"TOPLEFT","scale":0.69999998807907104,"xpos":1434,"ypos":-150},"pfFocus":{"anchor":"CENTER","parent":"UIParent","xpos":-200,"ypos":220},"pfFocusCastbar":{"anchor":"CENTER","parent":"UIParent","xpos":-200,"ypos":162},"pfGroup1":{"anchor":"TOP","parent":"UIParent","scale":1,"xpos":-468,"ypos":-135},"pfGroup2":{"anchor":"TOP","parent":"UIParent","xpos":-468,"ypos":-210},"pfGroup3":{"anchor":"TOP","parent":"UIParent","xpos":-468,"ypos":-285},"pfGroup4":{"anchor":"TOP","parent":"UIParent","xpos":-468,"ypos":-360},"pfMarkTracking":{"anchor":"CENTER","parent":"UIParent","xpos":270,"ypos":270},"pfPartyPet1":{"scale":0.90000000000000002},"pfPartyPet2":{"scale":0.90000000000000002},"pfPartyPet3":{"scale":0.90000000000000002},"pfPartyPet4":{"scale":0.90000000000000002},"pfPet":{"anchor":"CENTER","parent":"UIParent","scale":1,"xpos":-490,"ypos":338},"pfPlayer":{"anchor":"CENTER","parent":"UIParent","xpos":-445,"ypos":380},"pfRaidCluster":{"anchor":"CENTER","parent":"UIParent","xpos":-1420,"ypos":-120},"pfSwingTimerMainhand":{"anchor":"CENTER","parent":"UIParent","xpos":0,"ypos":-252},"pfSwingTimerRanged":{"anchor":"CENTER","parent":"UIParent","xpos":0,"ypos":-228},"pfTarget":{"anchor":"CENTER","parent":"UIParent","xpos":-215,"ypos":380},"pfTargetCastbar":{"anchor":"CENTER","parent":"UIParent","xpos":-215,"ypos":290},"pfTargetTarget":{"anchor":"CENTER","parent":"UIParent","scale":0.90000000000000002,"xpos":-116,"ypos":385},"pfTargetTargetTarget":{"anchor":"CENTER","parent":"UIParent","scale":0.90000000000000002,"xpos":-8,"ypos":385},"pfTooltipAnchor":{"anchor":"CENTER","parent":"UIParent","xpos":720,"ypos":-386},"pfTotems":{"anchor":"CENTER","parent":"UIParent","xpos":-482,"ypos":334}},"thirdparty":{"statcompare":{"enable":"1"},"wim":{"enable":"0"}},"tooltip":{"position":"free","vendor":{"showalways":"0"}},"unitframes":{"abbrevnum":"0","always2dportrait":"1","animation_speed":"1","castbardecimals":"1","custombgcolor":"0.502,0.2,0.2,1","customcolor":"0.055,0.749,0,1","druidmanaheight":"2","druidmanatext":"0","fallback":{"debuff_ind_range":"0"},"focus":{"buffs":"BOTTOMLEFT","buffsize":"18","cooldown_anim":"1","debuff_ind_range":"0","debuffs":"BOTTOMLEFT","debuffsize":"20","hitindicatorfont":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","hitindicatorsize":"16","portrait":"right","powercolor":"0","txthpright":"health"},"focustarget":{"debuff_ind_range":"0"},"group":{"buffs":"off","debuff_ind_range":"0","height":"22","txthpright":"none","width":"144"},"grouppet":{"debuff_ind_range":"0"},"grouptarget":{"debuff_ind_range":"0","visible":"0"},"nampower_buffs":"1","pet":{"debuff_ind_range":"0"},"player":{"buffs":"off","debuff_ind_range":"0","debuffs":"off","display_spellpower":"0","height":"26","hitindicator":"1","hitindicatorfont":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","hitindicatorsize":"16","portrait":"left","powercolor":"0","txthpright":"health","txtpowerright":"power","width":"150"},"ptarget":{"debuff_ind_range":"0"},"raid":{"debuff_ind_range":"0"},"rangecheck_mode":"unitxp","rangechecki":"4","spellqueue":"0","swingtimerattackspeed":"1","swingtimerfontsize":"14","swingtimerlabel":"0","swingtimermhcolor":"0.31,0.596,1,1","swingtimerrangedcolor":"0.686,0.447,1,1","swingtimerwidth":"192","target":{"buffperrow":"7","buffs":"BOTTOMLEFT","buffsize":"18","cooldown_anim":"1","debuff_ind_range":"0","debuffperrow":"6","debuffs":"BOTTOMLEFT","debuffsize":"22","height":"26","hitindicator":"1","hitindicatorfont":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","hitindicatorsize":"16","portrait":"right","powercolor":"0","showPVP":"1","txthpright":"health","txtpowerright":"power","width":"150"},"track_group":"1","ttarget":{"debuff_ind_range":"0","portrait":"off","powercolor":"0"},"tttarget":{"debuff_ind_range":"0","portrait":"off"}},"version":"999.999.999"}
''')


def _lua_value(v, indent: int = 0) -> str:
    """Serialize a JSON-derived value to a pfUI-style Lua literal."""
    if isinstance(v, dict):
        pad, cpad = " " * (indent + 2), " " * indent
        items = "".join(f'{pad}["{k}"] = {_lua_value(val, indent + 2)},\n'
                        for k, val in v.items())
        return "{\n" + items + cpad + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


_MARK = branding.PFUI_MARK_PREFIX


_PFUI_MARK_BEGIN = f"-- {_MARK}_DEFAULT_PROFILE_BEGIN"


_PFUI_MARK_END   = f"-- {_MARK}_DEFAULT_PROFILE_END"


_PFUI_CHAT_BEGIN = f"-- {_MARK}_CHAT_SKIP_BEGIN"


_PFUI_CHAT_END   = f"-- {_MARK}_CHAT_SKIP_END"


# Strips an injected block whichever marker pair it carries -- **including
# the previous product's**. A profile written by Octo Updater has
# OCTO_UPDATER_ markers in it, and a stripper that only knew the new spelling
# would leave that block behind and add a second one beside it, doubling the
# injected settings every launch.
_PFUI_PREFIXES = "|".join([_MARK, *branding.PFUI_LEGACY_MARK_PREFIXES])


_PFUI_STRIP_RE = (r"[ \t]*-- (?:" + _PFUI_PREFIXES +
                  r")_[A-Z_]+?_BEGIN.*?-- (?:" + _PFUI_PREFIXES +
                  r")_[A-Z_]+?_END\n?")


def patch_pfui_default_profile(client_dir: str):
    """Add the curated 'Default' profile to a freshly installed/updated pfUI
    and make it the firstrun default. Idempotent; degrades gracefully if
    pfUI's file layout has changed."""
    import re

    base = os.path.join(addons_path(client_dir), "pfUI")
    profiles_lua = os.path.join(base, "env", "profiles.lua")
    firstrun_lua = os.path.join(base, "modules", "firstrun.lua")
    if not os.path.exists(profiles_lua):
        return

    # 1) profiles.lua — append (or replace) a marked block defining Default.
    block = (f"{_PFUI_MARK_BEGIN}\n"
             f"local octo_default = {_lua_value(PFUI_DEFAULT_PROFILE)}\n"
             f'pfUI_profiles["Default"] = octo_default\n'
             f"{_PFUI_MARK_END}\n")
    try:
        with open(profiles_lua, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        txt = re.sub(re.escape(_PFUI_MARK_BEGIN) + r".*?"
                     + re.escape(_PFUI_MARK_END) + r"\n?", "", txt, flags=re.S)
        _atomic.write_text(profiles_lua, txt.rstrip() + "\n\n" + block, encoding="utf-8")
        log("  pfUI: 'Default' profile installed.")
    except OSError as e:
        log(f"  pfUI: could not patch profiles.lua ({e})")
        return

    # 2) pfUI.lua — use 'Default' (not 'Modern') as the fresh-install config,
    #    so the very first login already lands in the Default profile.
    pfui_lua = os.path.join(base, "pfUI.lua")
    if os.path.exists(pfui_lua):
        try:
            with open(pfui_lua, encoding="utf-8", errors="replace") as f:
                pf = f.read()
            old = 'CopyTable(pfUI_profiles["Modern"]) or {}'
            if old in pf:
                pf = pf.replace(
                    old, 'CopyTable(pfUI_profiles["Default"]) or {}', 1)
                _atomic.write_text(pfui_lua, pf, encoding="utf-8")
                log("  pfUI: 'Default' set as the fresh-install profile.")
        except OSError as e:
            log(f"  pfUI: could not patch pfUI.lua ({e})")

    # 3) firstrun.lua — add a 'Default' button, make it the fallback profile,
    #    and skip the chat wizard steps whenever the chat module is disabled.
    if not os.path.exists(firstrun_lua):
        return
    try:
        with open(firstrun_lua, encoding="utf-8", errors="replace") as f:
            fr = f.read()

        # Remove any previous injections (idempotent re-apply after updates).
        fr = re.sub(_PFUI_STRIP_RE, "", fr, flags=re.S)

        # When the chat module is disabled (e.g. the "Default" profile), the
        # chat firstrun steps can't apply anything, so pre-mark them done to
        # keep them from showing. Injected right after the step table is made.
        chat_skip = (
            f"  {_PFUI_CHAT_BEGIN}\n"
            "  if pfUI_config and pfUI_config.disabled"
            ' and pfUI_config.disabled.chat == "1" then\n'
            "    pfUI_init = pfUI_init or {}\n"
            '    pfUI_init["chat_right"] = true\n'
            '    pfUI_init["chat_position"] = true\n'
            '    pfUI_init["chat_channels"] = true\n'
            "  end\n"
            f"  {_PFUI_CHAT_END}\n")
        chat_anchor = "  pfUI.firstrun.steps = {}\n"
        if chat_anchor in fr:
            fr = fr.replace(chat_anchor, chat_anchor + chat_skip, 1)

        # Insert a Default button just before the built-in "Modern" button.
        button = (
            f"    {_PFUI_MARK_BEGIN}\n"
            '    f.Default = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")\n'
            "    f.Default:SetWidth(250)\n"
            "    f.Default:SetHeight(20)\n"
            '    f.Default:SetPoint("BOTTOM", 0, 120)\n'
            "    f.Default:SetTextColor(1,1,1)\n"
            '    f.Default:SetText("Default (recommended)")\n'
            "    f.Default:SetScript(\"OnClick\", function()\n"
            '      _G["pfUI_config"] = CopyTable(pfUI_profiles["Default"])\n'
            '      pfUI_init.selected_profile = "Default"\n'
            "      pfUI:LoadConfig()\n"
            "      ReloadUI()\n"
            "    end)\n"
            "    SkinButton(f.Default)\n"
            f"    {_PFUI_MARK_END}\n\n")
        anchor = '    f.Modern = CreateFrame("Button"'
        if anchor in fr:
            fr = fr.replace(anchor, button + anchor, 1)

        # Make Default the profile used when the user doesn't pick one.
        fr = fr.replace('pfUI_init.selected_profile or "Modern"',
                        'pfUI_init.selected_profile or "Default"')

        _atomic.write_text(firstrun_lua, fr, encoding="utf-8")
        log("  pfUI: 'Default' added to the firstrun profile picker.")
    except OSError as e:
        log(f"  pfUI: could not patch firstrun.lua ({e})")
