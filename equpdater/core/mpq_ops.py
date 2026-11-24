"""MPQ packs (Data/patch-<letter>.mpq): the listed catalogue, source
resolution, checksum-verified download with a kept backup, and the logic of
the page (scan, check, install/update/replace/uninstall/link).

Lifted verbatim from ``equpdater/app.py``. No Tk, no Qt. Writes go through
temp + replace; the page's operations run on ``OpsHost`` hooks.
"""

import hashlib
import os
import re
import threading
import time
import urllib.request

from .. import branding, gamepaths, mpq
from ..i18n import N_, tr
from ..config import ensure_dir, load_config, update_config
from .logsink import log
from .net import ALLOWED_DOWNLOAD_HOSTS, secure_urlopen
from . import oplock
from ..gitcompare import forge_named
from .mods import _codeberg_latest, _gitea_latest, _github_latest

UA = branding.USER_AGENT
DOWNLOAD_TIMEOUT = 10
NEWS_TIMEOUT = 8


# Downloadable MPQ content patches (not shipped with the client). Each is
# a single HTTP file with a `<url>.sha256` sidecar; an update is available when
# that published sha differs from the on-disk file's.
#: Latest-release lookups for linked texture packs, per repository, so that
#: switching to the MPQ tab does not spend GitHub's unauthenticated rate
#: limit (60 an hour) on every visit.
_MPQ_RELEASE_CACHE: dict = {}


_MPQ_RELEASE_TTL = 600

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
