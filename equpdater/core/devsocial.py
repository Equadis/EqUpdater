"""The TEMPORARY username sign-in and the live friends/presence link, for testing the social features before
OctoWoW offers real authentication. Qt-free.

**A development and testing system, not production authentication.** The experience stays "choose a username":
no password, no email, no OctoWoW account. Behind it each username is a real account on the test backend
(``tools/socialserver`` locally, ``cloudflare/social`` hosted): registering a name makes an account with a random
session token and a random recovery code; nobody else can take the name afterwards, online or not; the recovery
code moves the account to another computer. Secrets live only in the operating system's credential manager
(``core/credstore``) and never in settings, source or the package. The hosted test environment also asks for a
tester key at registration (given to authorised testers, never shipped).

Presence is event-driven: one WebSocket per signed-in player (``SocialLink``). Status changes are sent as they
happen, the server pushes a friend's change only to that friend's watchers, the connection is kept alive by a
tiny ping, and a lost connection is retried with exponential back-off and then restores the friends list and
everyone's presence.

It is isolated on purpose, so the production sign-in can replace it without touching anything else:
* it plugs in through ``account.AuthProvider`` and ``presence.PresenceProvider``, the interfaces production uses;
* it is chosen only when a backend address is configured (``EQU3_SOCIAL_URL`` or ``DEFAULT_URL``) and
  ``account.PROVIDER_CONFIG`` (OctoWoW's real sign-in) is not;
* nothing else in EqUpdater imports this module except the one place that picks the providers.
The protocol is documented in ``tools/socialserver/server.py``.
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..i18n import tr
from . import account as A
from . import presence as P
from . import wsclient

#: The hosted test backend (https): the Worker in cloudflare/social. It is a public address, not a secret: using it
#: needs a tester key to register. Clear it (or set EQU3_SOCIAL_URL=off) to run without any test backend. A release
#: build must not carry it (tools/beta_check.py warns while it is set).
DEFAULT_URL = "https://equpdater-social.eqvoice.workers.dev"

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
_PRIVATE = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$")

PING_EVERY = 45.0              # seconds of quiet before a heartbeat (the server drops a connection silent for 120)
STALE_AFTER = 150.0            # presence is "old" this long after the last sign of life from the server
BACKOFF_FIRST, BACKOFF_MAX = 1.0, 60.0


def backend_url(env=None) -> str:
    env = os.environ if env is None else env
    given = (env.get("EQU3_SOCIAL_URL") or "").strip()
    if given.lower() in ("off", "none", "0"):
        return ""
    if not given and (env.get("EQU3_OFFLINE") == "1" or env.get("EQUPDATER_OFFLINE")):
        return ""                                          # an offline run makes no network requests, including this one
    url = (given or DEFAULT_URL or "").strip().rstrip("/")
    return url if url_ok(url) else ""


def url_ok(url: str) -> bool:
    """https anywhere; plain http only to this machine or a private network (a test server on a LAN)."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and bool(parts.hostname and _PRIVATE.match(parts.hostname))


def ws_url(base: str) -> str:
    return ("wss" if base.startswith("https") else "ws") + base[base.index("://"):] + "/v1/ws"


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


class BackendDown(Exception):
    """The backend could not be reached or answered nonsense."""


def default_http(method, url, body=None, headers=None, timeout=8.0):
    """(status, parsed json or None, response headers). HTTP answers are returned; a failed connection raises."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", "User-Agent": "EqUpdater3",
                                          **({"Content-Type": "application/json"} if data else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context(url)) as r:
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


def _context(url):
    if url.startswith("https"):
        from .net import make_ssl_context
        return make_ssl_context()[0]
    return None


class Backend:
    def __init__(self, base_url: str, http=default_http):
        if not url_ok(base_url):
            raise ValueError("the test backend address must be https (or http on a private network)")
        self.base, self.http = base_url.rstrip("/"), http

    def call(self, method, path, body=None, token=""):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.http(method, self.base + path, body, headers)


# ── secrets ──────────────────────────────────────────────────────────────────────────────

class Vault:
    """The account's secrets in the credential manager: a session token and a recovery code per username, and
    the tester key. Nothing here is ever written to a file by EqUpdater."""
    SERVICE = "EqUpdater3-test"

    def __init__(self, store, profile=""):
        self.store, self.suffix = store, ("-" + profile if profile else "")

    def _k(self, kind, name=""):
        return f"{kind}:{name.lower()}{self.suffix}" if name else f"{kind}{self.suffix}"

    def get(self, kind, name=""):
        try:
            return self.store.get(self.SERVICE, self._k(kind, name))
        except Exception:                                              # noqa: BLE001 - an optional convenience
            return None

    def set(self, kind, value, name=""):
        try:
            return bool(self.store.set(self.SERVICE, self._k(kind, name), value))
        except Exception:                                              # noqa: BLE001
            return False

    def delete(self, kind, name=""):
        try:
            self.store.delete(self.SERVICE, self._k(kind, name))
        except Exception:                                              # noqa: BLE001
            pass


# ── sign-in ──────────────────────────────────────────────────────────────────────────────

class DevAuthProvider(A.AuthProvider):
    """Choosing a username is the whole experience. ``username`` (and, when asked for, ``key`` / ``recovery``)
    are set by the interface before ``start``."""
    id = "dev-username"
    name = "Test login"
    available = True
    capabilities = frozenset({"publish", "dev"})
    username_login = True                     # the interface asks for a name instead of opening a browser

    def __init__(self, backend: Backend, vault: Vault):
        self.backend, self.vault = backend, vault
        self.username = self.key = self.recovery = ""
        self.new_recovery = ""                 # set when this sign-in created an account: shown once to the player
        self._info = None

    def requires_key(self) -> bool:
        """Does the backend want a tester key to register? (asked once, cached; unreachable counts as no)"""
        if self._info is None:
            try:
                status, obj, _h = self.backend.call("GET", "/v1/info")
                self._info = obj if status == 200 and isinstance(obj, dict) else {}
            except BackendDown:
                return False
        return bool(self._info.get("requires_key")) and not self.vault.get("tester-key")

    def _fail(self, status, obj, default=""):
        msg = obj.get("error") if isinstance(obj, dict) else ""
        text = {403: tr("A tester key is needed to create an account, and that one was not accepted."),
                409: tr("That username is already registered. If it is yours, enter its recovery code."),
                429: tr("Too many attempts. Please wait a while and try again."),
                503: tr("The test service is full or paused.")}.get(status)
        raise A.AuthError("denied" if status in (401, 403, 409, 429) else "invalid",
                          text or (tr("That recovery code does not match.") if status == 401 else
                                   msg or default or tr("The test service answered unexpectedly ({status}).", status=status)))

    def _call(self, method, path, body=None, token=""):
        try:
            return self.backend.call(method, path, body, token)
        except BackendDown:
            raise A.AuthError("network", tr("The test service could not be reached.")) from None

    def start(self, open_browser, cancel: threading.Event, timeout: float = 180.0) -> A.Tokens:
        name = (self.username or "").strip()
        if not valid_name(name):
            raise A.AuthError("invalid", tr("A username is 3 to 20 letters, numbers, dashes or underscores."))
        self.new_recovery = ""
        recovery = (self.recovery or "").strip()
        self.recovery = ""
        if not recovery:
            saved = self.vault.get("session", name)                     # this computer already has the account
            if saved:
                status, obj, _h = self._call("GET", "/v1/me", token=saved)
                if status == 200:
                    return A.Tokens(saved)
            recovery = self.vault.get("recovery", name) or ""            # ... or at least its recovery code
        if recovery:
            status, obj, _h = self._call("POST", "/v1/recover", {"username": name, "recovery": recovery})
            if status == 200 and isinstance(obj, dict) and obj.get("token"):
                self.vault.set("session", obj["token"], name)
                self.vault.set("recovery", recovery, name)
                return A.Tokens(obj["token"])
            if (self.vault.get("recovery", name) or "") != recovery or status != 401:
                self._fail(status, obj)
            self.vault.delete("recovery", name)                          # a stale saved code: fall through to register
        key = (self.key or "").strip() or self.vault.get("tester-key") or ""
        status, obj, _h = self._call("POST", "/v1/register", {"username": name, "key": key})
        if status != 200 or not isinstance(obj, dict) or not obj.get("token"):
            self._fail(status, obj)
        self.vault.set("session", obj["token"], name)
        self.vault.set("recovery", obj["recovery"], name)
        if key:
            self.vault.set("tester-key", key)                            # accepted: remembered for later accounts
        self.new_recovery = obj["recovery"]
        return A.Tokens(obj["token"])

    def refresh(self, tokens: A.Tokens) -> A.Tokens:
        return tokens

    def revoke(self, tokens: A.Tokens) -> None:
        try:
            self.backend.call("POST", "/v1/logout", {}, tokens.access)
        except BackendDown:
            pass

    def profile(self, tokens: A.Tokens) -> A.Profile:
        status, obj, _h = self._call("GET", "/v1/me", token=tokens.access)
        if status in (401, 403):
            raise A.AuthError("rejected", tr("Your sign-in has expired. Please sign in again."))
        if status != 200 or not isinstance(obj, dict) or not obj.get("username"):
            raise A.AuthError("invalid", tr("The test service answered unexpectedly ({status}).", status=status))
        return A.Profile(str(obj["username"]), str(obj["username"]).lower(), "")


# ── the live link ────────────────────────────────────────────────────────────────────────

class SocialLink:
    """One WebSocket, kept up. Runs on its own thread. Sends the player's state as it changes, pings when quiet,
    and reconnects with exponential back-off and jitter. A refusal that retrying cannot fix (the session was
    revoked, or another computer took over) stops it."""

    def __init__(self, url, token_fn, on_message, on_link, on_rejected, connect=wsclient.connect,
                 ping_every=PING_EVERY, rng=random.random):
        self.url, self.token_fn, self.connect_fn = url, token_fn, connect
        self.on_message, self.on_link, self.on_rejected = on_message, on_link, on_rejected
        self.ping_every, self.rng = ping_every, rng
        self.ws = None
        self.state = None                     # the last own-state message, resent on every reconnect
        self.stop_flag = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.connected = False
        self.attempts = 0                     # connection attempts since the last healthy connection
        self.delays: list = []                # the back-off delays used (for tests and diagnostics)
        self.messages_sent = 0

    # -- control -------------------------------------------------------------------------------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, name="social-link", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        with self.lock:
            ws, self.ws = self.ws, None
        if ws:
            ws.close(1000)
        self._set(False)

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and not self.stop_flag.is_set())

    def _set(self, connected: bool):
        if connected != self.connected:
            self.connected = connected
            self.on_link(connected)

    # -- sending -----------------------------------------------------------------------------------
    def send_state(self, own: P.OwnStatus):
        msg = json.dumps({"t": "state", "status": own.status, "activity": own.activity, "realm": own.realm})
        with self.lock:
            self.state = msg
            ws = self.ws
        if ws and self.connected:
            self._send(ws, msg)

    def _send(self, ws, text):
        try:
            with self.lock:
                ws.send_text(text)
            self.messages_sent += 1
            return True
        except OSError:
            return False

    # -- the loop ---------------------------------------------------------------------------------
    def _run(self):
        delay = BACKOFF_FIRST
        while not self.stop_flag.is_set():
            token = self.token_fn()
            if not token:
                return                                               # signed out: nothing to connect as
            started = time.monotonic()
            self.attempts += 1
            retry_after = 0.0
            try:
                ws = self.connect_fn(self.url, {"Authorization": "Bearer " + token, "User-Agent": "EqUpdater3"})
            except wsclient.WebSocketError as e:
                if e.status in (401, 403):
                    self.on_rejected("rejected")
                    return
                retry_after = e.retry_after
            else:
                with self.lock:
                    self.ws = ws
                self._set(True)
                if self.state:
                    self._send(ws, self.state)                       # restore: say who and what we are again
                end = self._serve(ws)
                with self.lock:
                    self.ws = None
                self._set(False)
                if end == "recovered elsewhere":
                    self.on_rejected(end)                            # the account moved to another computer
                    return
                if end in ("signed out", "replaced"):
                    return                                           # our own sign-out, or another launcher took over: do not fight
                if time.monotonic() - started >= 10.0:
                    delay, self.attempts = BACKOFF_FIRST, 0          # it was healthy: start the back-off again
            if self.stop_flag.is_set():
                return
            wait = max(delay, retry_after) * (0.75 + 0.5 * self.rng())
            self.delays.append(wait)
            if self.stop_flag.wait(wait):
                return
            delay = min(delay * 2, BACKOFF_MAX)

    def _serve(self, ws) -> str:
        """Read until the connection ends; returns why ('' for a plain loss)."""
        quiet_since = time.monotonic()
        while not self.stop_flag.is_set():
            try:
                text = ws.recv(timeout=max(1.0, self.ping_every - (time.monotonic() - quiet_since)))
            except socket.timeout:
                if not self._send(ws, "ping"):
                    return ""
                quiet_since = time.monotonic()
                continue
            except (OSError, ValueError):
                return ""
            if text is None:
                return ""
            quiet_since = time.monotonic()
            if text == "pong":
                self.on_message({"t": "pong"})
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("t") == "bye":
                return str(msg.get("reason") or "")
            if isinstance(msg, dict):
                self.on_message(msg)
        return ""


# ── friends and presence ─────────────────────────────────────────────────────────────────

class DevPresenceProvider(P.PresenceProvider):
    """Presence by push. ``PresenceService`` no longer asks anything: this provider keeps a ``SocialLink`` up while
    someone is signed in and writes what the server pushes into the presence cache. The friends list lives on the
    server (it follows the player from machine to machine); the server's snapshot is mirrored into the local
    ``FriendBook``."""
    id = "dev-ws"
    name = "Test service"
    available = True
    requires_account = True
    syncs_friends = True
    push = True
    stale_after = STALE_AFTER
    capabilities = frozenset({"game_presence", "launcher_presence", "publish"})
    max_batch = 1 << 20

    def __init__(self, backend: Backend, book, now=time.time, connect=wsclient.connect, link_factory=None):
        self.backend, self.book, self.now, self.connect = backend, book, now, connect
        self.own = P.OwnStatus()
        self.sink = None                       # the PresenceService whose cache we fill
        self.on_friends = lambda entries: None
        self.on_rejected = lambda why: None
        self.tap = lambda msg: None            # sees every message the server pushes (diagnostics and tests)
        self.link: SocialLink | None = None
        self.link_factory = link_factory or SocialLink
        self.token_fn = lambda: None

    # -- the link ---------------------------------------------------------------------------------
    def attach(self, presence_service, token_fn):
        self.sink, self.token_fn = presence_service, token_fn

    def ensure_link(self, signed_in: bool):
        """Keep the connection up exactly while someone is signed in."""
        if signed_in:
            if self.link is None or not self.link.running():
                self.link = self.link_factory(ws_url(self.backend.base), self.token_fn, self._on_message, self._on_link,
                                              self._rejected, self.connect)
                self.link.send_state(self.own)
                self.link.start()
        elif self.link is not None:
            self.link.stop()
            self.link = None

    def _rejected(self, why):
        self.link = None
        self.on_rejected(tr("You were signed out: this account was opened on another computer.")
                         if "recovered" in why else tr("Your sign-in has expired. Please sign in again."))

    def _on_link(self, connected: bool):
        if self.sink is None:
            return
        if not connected:
            for obs in self.sink.cache.values():
                obs.as_of = 0.0                # what we know is now old (kept, shown dim) until the server speaks again
        else:
            self.sink.error, self.sink.failures = "", 0
        self.sink.on_change()

    def _observation(self, fid, state, activity, realm):
        if state == "offline":
            return P.Observation(fid, "offline", "offline", self.now(), "", "test-service")
        in_game = activity == "in_game"
        return P.Observation(fid, "in_game" if in_game else "offline", state if state in ("online", "away", "dnd") else "online",
                             self.now(), realm if in_game else "", "test-service")

    def _on_message(self, msg: dict):
        if msg.get("t") != "pong":
            self.tap(msg)
        if self.sink is None:
            return
        t = msg.get("t")
        if t == "friends" and isinstance(msg.get("friends"), list):
            entries = [e for e in msg["friends"] if isinstance(e, dict) and valid_name(str(e.get("username") or ""))]
            self.on_friends(entries)
            by_name = {f.label.casefold(): f.id for f in self.book.all()}
            fresh = {}
            for e in entries:
                fid = by_name.get(str(e["username"]).casefold())
                if fid:
                    fresh[fid] = self._observation(fid, str(e.get("state") or "offline"), str(e.get("activity") or ""),
                                                   str(e.get("realm") or "")).clean()
            self.sink.cache.clear()
            self.sink.cache.update(fresh)
            self.sink.on_change()
        elif t == "presence" and valid_name(str(msg.get("u") or "")):
            fid = next((f.id for f in self.book.all() if f.label.casefold() == str(msg["u"]).casefold()), None)
            if fid:
                self.sink.cache[fid] = self._observation(fid, str(msg.get("state") or "offline"), str(msg.get("activity") or ""),
                                                         str(msg.get("realm") or "")).clean()
                self.sink.on_change()
        elif t == "pong":
            for obs in self.sink.cache.values():
                if obs.as_of:                  # a sign of life: nothing about the friends changed, and nothing is old
                    obs.as_of = self.now()

    # -- the interface PresenceService and SocialService use -----------------------------------------
    def lookup(self, friends, token):                                     # nothing is polled
        return []

    def publish(self, status, token) -> None:
        self.own = status
        if self.link is not None:
            self.link.send_state(status)

    # -- friend editing: ordinary requests; the server answers by pushing the new list -----------------
    def _edit(self, method, path, token, body=None) -> str:
        try:
            status, obj, _h = self.backend.call(method, path, body, token)
        except BackendDown:
            return tr("The test service could not be reached.")
        if status in (200, 204):
            return ""
        if status in (401, 403):
            return tr("Your sign-in has expired. Please sign in again.")
        msg = obj.get("error") if isinstance(obj, dict) else ""
        return {404: tr("No user has that name."), 409: tr("{name} is already in your friends list.", name=path.rsplit("/", 1)[-1]),
                429: tr("Too many attempts. Please wait a while and try again.")}.get(
            status, msg or tr("The test service answered unexpectedly ({status}).", status=status))

    def add_friend(self, token, name) -> str:
        return self._edit("POST", "/v1/friends", token, {"username": name})

    def remove_friend(self, token, name) -> str:
        return self._edit("DELETE", "/v1/friends/" + urllib.parse.quote(name), token)

    def set_favourite(self, token, name, value) -> str:
        return self._edit("PUT", "/v1/friends/" + urllib.parse.quote(name), token, {"favourite": bool(value)})
