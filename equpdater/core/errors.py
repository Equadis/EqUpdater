"""Plain-language descriptions of network and install failures, and the small
JSON-API helper the release/addon lookups share. Lifted verbatim from
``equpdater/app.py``."""

import json
import time
import os
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlsplit

from .. import branding, platforms
from ..i18n import N_, tr
from ..news import is_challenge_page
from .net import secure_urlopen

UA = branding.USER_AGENT


def _api_json(url: str, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=timeout) as r:
        body = r.read()
        _refuse_challenge(body, r.headers)
    return json.loads(body)


def _describe_net_error(e: Exception) -> str:
    """Human-readable cause for a failed API request."""
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        headers = e.headers or {}
        limited = (headers.get("X-RateLimit-Remaining") == "0"
                   or bool(headers.get("Retry-After")))
        if e.code == 429 or (e.code == 403 and limited):
            reset = headers.get("X-RateLimit-Reset")
            if str(reset or "").isdigit():
                return tr("API rate limit reached until {time}",
                          time=time.strftime("%H:%M", time.localtime(int(reset))))
            return N_("API rate limit exceeded – try again in 1 hour")
        if e.code == 403 and "X-RateLimit-Remaining" not in headers \
                and "api.github.com" in (e.url or ""):
            return N_("API rate limit exceeded – try again in 1 hour")
        if e.code == 404:
            return N_("repository or branch not found")
        host = (e.url or "").split("/")[2] if (e.url or "").count("/") >= 2 \
            else "server"
        return f"HTTP {e.code} from {host}"
    if isinstance(e, urllib.error.URLError):
        return f"network error ({e.reason})"
    return str(e)


def describe_install_error(e: Exception) -> str:
    """Map an install/update failure to a message the user can act on."""
    import zipfile
    import urllib.error
    if isinstance(e, (urllib.error.HTTPError, urllib.error.URLError)):
        return _describe_net_error(e)
    if (platforms.WINDOWS and isinstance(e, OSError)
            and getattr(e, "errno", None) in (2, 13, 22)):
        # The archive/file vanished or got locked mid-operation — on Windows
        # that's almost always the antivirus quarantining the download.
        return N_("Blocked by antivirus – open Settings (⚙) → 'Exclude from Defender', then retry")
    if isinstance(e, zipfile.BadZipFile):
        return N_("Downloaded archive is corrupted (possibly blocked by antivirus) – retry, or use Settings (⚙) → 'Exclude from Defender'")
    return str(e)


def _error_chain(e: BaseException) -> list:
    """``e`` and every exception it was raised from, outermost first."""
    chain, seen = [], set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        chain.append(e)
        e = e.__cause__ or e.__context__
    return chain


#: What a host behind DDoS protection sends an app instead of the answer
#: (octowow.st today). Said plainly, rather than as "not JSON" or "corrupted
#: archive".
DDOS_CHECK = N_("octowow.st is showing its DDoS-protection check to apps "
                "right now")


class DdosCheckError(RuntimeError):
    """A host answered with its DDoS-protection page instead of the data."""


def _refuse_challenge(body: bytes, headers) -> None:
    if body[:2] == b"PK" or body[:1] in (b"{", b"["):
        return
    if is_challenge_page(body[:4096].decode("utf-8", "replace"), headers):
        raise DdosCheckError(DDOS_CHECK)
