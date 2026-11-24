"""The client manifest: what a correct client contains.

Only the **metadata** of the client torrent is read (a small ``.torrent``
file: file names, sizes, piece hashes) - never any client file. Used by the
read-only client scan and by the update check. Nothing here downloads, seeds or
writes game files; the downloader the manifest used to feed was removed
(ARIA2_AUDIT.md).

Lifted verbatim from ``equpdater/app.py``.
"""

import hashlib
import os
import urllib.request
from functools import cache

from .. import branding
from . import net
from .logsink import log
from .mods import MODS_REGISTRY
from .net import ALLOWED_DOWNLOAD_HOSTS, secure_urlopen

UA = branding.USER_AGENT
DOWNLOAD_TIMEOUT = 10


CLIENT_TORRENT_URL = "https://dl.octowow.st/download/client.torrent"


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
