"""The TEMPORARY username login and shared friends/presence client, for testing the social features
before OctoWoW offers real authentication. Qt-free.

**This is a development and testing system, not secure production authentication.** A player picks a
unique username: no password, no email, no OctoWoW account, no external provider. The server it talks to
(``tools/socialserver/server.py``) hands back a token that only proves "this client claimed this name".

It is isolated on purpose, so the production sign-in can replace it without touching anything else:
* it plugs in through the same two interfaces production will use, ``account.AuthProvider`` (sign-in,
  profile, sign-out) and ``presence.PresenceProvider`` (friends and presence);
* it is chosen only when a backend address is configured (``EQU3_SOCIAL_URL`` or ``DEFAULT_URL``) and
  ``account.PROVIDER_CONFIG`` (OctoWoW's real sign-in) is not;
* nothing else in EqUpdater imports this module except the one place that picks the providers.
The protocol is documented in ``tools/socialserver/server.py``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..i18n import tr
from . import account as A
from . import presence as P

#: Where the shared test backend runs. Empty until one is hosted; EQU3_SOCIAL_URL overrides it.
DEFAULT_URL = ""

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
_PRIVATE = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$")


def backend_url(env=None) -> str:
    env = os.environ if env is None else env
    url = (env.get("EQU3_SOCIAL_URL") or DEFAULT_URL or "").strip().rstrip("/")
    return url if url_ok(url) else ""


def url_ok(url: str) -> bool:
    """https anywhere; plain http only to this machine or a private network (a test server on a LAN)."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and bool(parts.hostname and _PRIVATE.match(parts.hostname))


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


class BackendDown(Exception):
    """The backend could not be reached or answered nonsense."""


def default_http(method, url, body=None, headers=None, timeout=8.0):
    """(status, parsed json or None, response headers). HTTP answers are returned; a failed connection raises."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {}),
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, hdrs = r.read(256 * 1024), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read(64 * 1024), e.code, dict(e.headers)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise BackendDown(str(e)) from None
    try:
        obj = json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        obj = None
    return status, obj, {k.lower(): v for k, v in hdrs.items()}


class Backend:
    def __init__(self, base_url: str, http=default_http):
        if not url_ok(base_url):
            raise ValueError("the test backend address must be https (or http on a private network)")
        self.base, self.http = base_url.rstrip("/"), http

    def call(self, method, path, body=None, token="", etag=""):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + token
        if etag:
            headers["If-None-Match"] = etag
        return self.http(method, self.base + path, body, headers)


# ── sign-in ──────────────────────────────────────────────────────────────────────────────

class DevAuthProvider(A.AuthProvider):
    """Choosing a username is the whole sign-in. ``username`` is set by the interface before ``start``."""
    id = "dev-username"
    name = "Test login"
    available = True
    capabilities = frozenset({"publish", "dev"})
    username_login = True                     # the interface asks for a name instead of opening a browser

    def __init__(self, backend: Backend, known_tokens=None, remember=None):
        self.backend = backend
        self.username = ""
        self.known = dict(known_tokens or {})             # lower-case name -> token (to switch back to an account)
        self.remember = remember or (lambda tokens: None)

    def start(self, open_browser, cancel: threading.Event, timeout: float = 180.0) -> A.Tokens:
        name = (self.username or "").strip()
        if not valid_name(name):
            raise A.AuthError("invalid", tr("A username is 3 to 20 letters, numbers, dashes or underscores."))
        try:
            status, obj, _h = self.backend.call("POST", "/v1/login", {"username": name, "token": self.known.get(name.lower(), "")})
        except BackendDown:
            raise A.AuthError("network", tr("The test service could not be reached.")) from None
        if status == 409:
            raise A.AuthError("denied", tr("That username is in use right now."))
        if status != 200 or not isinstance(obj, dict) or not obj.get("token"):
            raise A.AuthError("invalid", (obj or {}).get("error") if isinstance(obj, dict) and obj.get("error")
                              else tr("The test service answered unexpectedly ({status}).", status=status))
        self.known[str(obj["username"]).lower()] = obj["token"]
        self.remember(dict(self.known))
        return A.Tokens(obj["token"])

    def refresh(self, tokens: A.Tokens) -> A.Tokens:
        return tokens

    def revoke(self, tokens: A.Tokens) -> None:
        try:
            self.backend.call("POST", "/v1/logout", {}, tokens.access)
        except BackendDown:
            pass

    def profile(self, tokens: A.Tokens) -> A.Profile:
        try:
            status, obj, _h = self.backend.call("GET", "/v1/me", token=tokens.access)
        except BackendDown:
            raise A.AuthError("network", tr("The test service could not be reached.")) from None
        if status in (401, 403):
            raise A.AuthError("rejected", tr("Your sign-in has expired. Please sign in again."))
        if status != 200 or not isinstance(obj, dict) or not obj.get("username"):
            raise A.AuthError("invalid", tr("The test service answered unexpectedly ({status}).", status=status))
        return A.Profile(str(obj["username"]), str(obj["username"]).lower(), "")


# ── friends and presence ─────────────────────────────────────────────────────────────────

class DevPresenceProvider(P.PresenceProvider):
    """One request about every five seconds does both jobs: it is the heartbeat (your state) and it returns
    your friends with their presence (a 304 when nothing changed). The friends list lives on the server, so it
    follows the player from machine to machine; ``on_friends`` mirrors it into the local ``FriendBook``."""
    id = "dev-sync"
    name = "Test service"
    available = True
    requires_account = True
    syncs_friends = True
    capabilities = frozenset({"game_presence", "launcher_presence", "publish"})
    max_batch = 1 << 20

    def __init__(self, backend: Backend, book, now=time.time):
        self.backend, self.book, self.now = backend, book, now
        self.own = P.OwnStatus()
        self.on_friends = lambda entries: None
        self._etag = ""
        self._last: list = []              # the last answer, so a 304 can re-stamp it
        self._lock = threading.Lock()

    # -- the sync -----------------------------------------------------------------------------
    def _sync(self, token, use_etag=True):
        o = self.own
        body = {"status": o.status, "activity": o.activity, "realm": o.realm}
        try:
            status, obj, hdr = self.backend.call("POST", "/v1/sync", body, token, self._etag if use_etag else "")
        except BackendDown:
            raise P.ProviderError(tr("The test service could not be reached.")) from None
        if status == 429:
            try:
                wait = float(hdr.get("retry-after", 5))
            except ValueError:
                wait = 5.0
            raise P.RateLimited(wait)
        if status in (401, 403):
            raise P.ProviderError(tr("Your sign-in has expired. Please sign in again."))
        if status == 304:
            return None
        if status != 200 or not isinstance(obj, dict) or not isinstance(obj.get("friends"), list):
            raise P.ProviderError(tr("The test service answered unexpectedly ({status}).", status=status))
        self._etag = hdr.get("etag", "")
        entries = [e for e in obj["friends"] if isinstance(e, dict) and valid_name(str(e.get("username") or ""))]
        self._last = entries                           # whoever asked (heartbeat or an immediate publish), the answer is kept
        self.on_friends(entries)
        return entries

    def lookup(self, friends: list, token):
        with self._lock:
            entries = self._sync(token)
            if entries is None:
                entries = self._last                   # unchanged on the server: the same answer, newly confirmed
            by_name = {f.label.casefold(): f.id for f in self.book.all()}
            out = []
            for e in entries:
                fid = by_name.get(str(e["username"]).casefold())
                if not fid:
                    continue
                state, activity = str(e.get("state") or "offline"), str(e.get("activity") or "")
                if state == "offline":
                    obs = P.Observation(fid, "offline", "offline", self.now(), "", "test-service")
                else:
                    in_game = activity == "in_game"
                    obs = P.Observation(fid, "in_game" if in_game else "offline",
                                        state if state in ("online", "away", "dnd") else "online",
                                        self.now(), str(e.get("realm") or "") if in_game else "", "test-service")
                out.append(obs)
            return out

    def publish(self, status, token) -> None:
        """Push the own state now (a status change should not wait for the next heartbeat)."""
        with self._lock:
            self.own = status
            try:
                self._sync(token, use_etag=False)
            except (P.ProviderError, P.RateLimited):
                pass                                   # the next heartbeat carries it

    # -- friend editing (the list lives on the server) -------------------------------------------------
    def _edit(self, method, path, token, body=None) -> str:
        try:
            status, obj, _h = self.backend.call(method, path, body, token)
        except BackendDown:
            return tr("The test service could not be reached.")
        if status in (200, 204):
            self._etag = ""                             # the list changed: the next sync must return it
            return ""
        if status in (401, 403):
            return tr("Your sign-in has expired. Please sign in again.")
        msg = obj.get("error") if isinstance(obj, dict) else ""
        return {404: tr("No user has that name."), 409: tr("{name} is already in your friends list.", name=path.rsplit("/", 1)[-1])}.get(
            status, msg or tr("The test service answered unexpectedly ({status}).", status=status))

    def add_friend(self, token, name) -> str:
        err = self._edit("POST", "/v1/friends", token, {"username": name})
        return err

    def remove_friend(self, token, name) -> str:
        return self._edit("DELETE", "/v1/friends/" + urllib.parse.quote(name), token)

    def set_favourite(self, token, name, value) -> str:
        return self._edit("PUT", "/v1/friends/" + urllib.parse.quote(name), token, {"favourite": bool(value)})
