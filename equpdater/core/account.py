"""Optional OctoWoW account sign-in, Qt-free.

**Status (2026-11-24): there is nothing to sign in to.** OctoWoW publishes no
OAuth or OpenID Connect service, no API and no token endpoint (investigated in
``docs/v3/SOCIAL.md``): its website signs people in with a password form, and
its forum is phpBB. EqUpdater will not collect a password, scrape a session or
bypass anything, so ``PROVIDER_CONFIG`` is ``None`` and the provider in use is
``UnavailableProvider``, which says so and never reports a sign-in.

What *is* built, ready for the day OctoWoW publishes a service:

* ``AuthProvider``: the interface the rest of EqUpdater uses.
* ``OAuthProvider``: a standards-based public client (RFC 6749 authorization
  code with PKCE, RFC 7636, a loopback redirect, RFC 8252: the sign-in happens
  in the player's own browser on the official site; EqUpdater only receives a
  one-time code). Token refresh and revocation (RFC 7009). It is configured by
  endpoints, so no code changes when they exist. It is tested against a fake
  server; it has **not** been run against a real one and is not "operational".
* ``AccountService``: session restore, refresh before expiry, sign-out, and
  secure persistence: tokens go to the operating system's credential manager
  (``core/credstore.py``), never to a file; only the user name and avatar
  address (not secrets) are kept in the settings.

Forum sign-in is separate from any game account; nothing here touches a game
login, and nothing here is needed to update, configure or play.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..i18n import tr
from . import credstore

SERVICE = "EqUpdater3"
ACCOUNT = "octowow-session"

#: The sign-in service OctoWoW would have to provide. None until it exists.
#: Shape: {"authorize_url", "token_url", "userinfo_url", "revoke_url", "client_id", "scopes": [...],
#:         "avatar_hosts": [...], "profile_url", "capabilities": [...]}
PROVIDER_CONFIG: dict | None = None

SIGN_UP_URL = "https://octowow.st/login"          # the official site (its account creation is in the same page)
EARLY_REFRESH_S = 90.0


class AuthError(Exception):
    """``code``: cancelled | denied | network | rejected | invalid | unavailable; ``message`` is for the player."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message or code


@dataclass
class Tokens:
    access: str
    refresh: str = ""
    expires_at: float = 0.0
    scope: str = ""

    def expired(self, now: float, margin: float = 0.0) -> bool:
        return bool(self.expires_at) and now >= self.expires_at - margin

    def dumps(self) -> str:
        return json.dumps({"v": 1, "a": self.access, "r": self.refresh, "e": self.expires_at, "s": self.scope})

    @classmethod
    def loads(cls, text: str | None) -> "Tokens | None":
        try:
            d = json.loads(text or "")
            if d.get("v") == 1 and isinstance(d.get("a"), str) and d["a"]:
                return cls(d["a"], str(d.get("r") or ""), float(d.get("e") or 0), str(d.get("s") or ""))
        except (ValueError, TypeError, AttributeError):
            pass
        return None


@dataclass
class Profile:
    username: str
    user_id: str = ""
    avatar_url: str = ""
    profile_url: str = ""


# ── providers ─────────────────────────────────────────────────────────────────────────

class AuthProvider:
    id = "none"
    name = ""
    available = False
    unavailable_reason = ""
    capabilities: frozenset = frozenset()
    avatar_hosts: tuple = ()
    profile_url = ""

    def start(self, open_browser, cancel: threading.Event, timeout: float = 180.0) -> Tokens:
        raise AuthError("unavailable", self.unavailable_reason)

    def refresh(self, tokens: Tokens) -> Tokens:
        raise AuthError("unavailable", self.unavailable_reason)

    def revoke(self, tokens: Tokens) -> None:
        return None

    def profile(self, tokens: Tokens) -> Profile:
        raise AuthError("unavailable", self.unavailable_reason)


class UnavailableProvider(AuthProvider):
    """The honest default: OctoWoW has published no sign-in service."""
    id = "octowow-unavailable"
    name = "OctoWoW"

    @property
    def unavailable_reason(self) -> str:
        return tr("OctoWoW has not published a sign-in service that EqUpdater can use yet, and EqUpdater "
                  "will not ask for your forum password. Everything else works without an account.")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple:
    verifier = _b64url(secrets.token_bytes(48))
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def default_http(method: str, url: str, data: dict | None = None, headers: dict | None = None,
                 timeout: float = 15.0) -> tuple:
    """(status, body bytes) for one HTTPS request; any HTTP answer is returned, a connection failure raises."""
    import urllib.error
    import urllib.request

    from .net import secure_urlopen
    host = urllib.parse.urlsplit(url).hostname or ""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Accept": "application/json", **(headers or {}),
                                          **({"Content-Type": "application/x-www-form-urlencoded"} if body else {})})
    try:
        with secure_urlopen(req, timeout=timeout, allowed_hosts={host}) as r:
            return r.status, r.read(256 * 1024)
    except urllib.error.HTTPError as e:
        return e.code, e.read(64 * 1024)


class OAuthProvider(AuthProvider):
    """Authorization code + PKCE with a loopback redirect, for a public client."""
    id = "octowow-oauth"
    name = "OctoWoW"
    available = True

    def __init__(self, config: dict, http=default_http, now=time.time):
        for key in ("authorize_url", "token_url", "userinfo_url", "client_id"):
            if not config.get(key):
                raise ValueError(f"the sign-in configuration has no {key}")
        for key in ("authorize_url", "token_url", "userinfo_url", "revoke_url"):
            if config.get(key) and not config[key].startswith("https://"):
                raise ValueError(f"{key} must be an https address")
        self.cfg, self.http, self.now = config, http, now
        self.capabilities = frozenset(config.get("capabilities") or ())
        self.avatar_hosts = tuple(config.get("avatar_hosts") or ())
        self.profile_url = config.get("profile_url") or ""

    # -- the browser step -------------------------------------------------------------------
    def start(self, open_browser, cancel, timeout: float = 180.0) -> Tokens:
        verifier, challenge = pkce_pair()
        state = _b64url(secrets.token_bytes(24))
        box: dict = {}
        done = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                                              # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if urllib.parse.urlsplit(self.path).path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                box.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(("<html><body style='font-family:sans-serif'><h3>EqUpdater</h3>"
                                  "<p>You can close this tab and return to EqUpdater.</p></body></html>").encode())
                done.set()

            def log_message(self, *a):                                     # noqa: D401 - silence
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 0.25
        redirect = f"http://127.0.0.1:{server.server_port}/callback"
        url = self.cfg["authorize_url"] + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": self.cfg["client_id"], "redirect_uri": redirect,
            "scope": " ".join(self.cfg.get("scopes") or ()), "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        try:
            open_browser(url)
            end = time.monotonic() + timeout
            while not done.is_set():
                if cancel.is_set():
                    raise AuthError("cancelled", tr("Sign-in was cancelled."))
                if time.monotonic() > end:
                    raise AuthError("cancelled", tr("Sign-in took too long and was stopped."))
                server.handle_request()
        finally:
            server.server_close()
        if box.get("error"):
            raise AuthError("denied", tr("The sign-in was not approved."))
        if box.get("state") != state or not box.get("code"):
            raise AuthError("invalid", tr("The sign-in answer could not be verified."))
        return self._token({"grant_type": "authorization_code", "code": box["code"], "redirect_uri": redirect,
                            "client_id": self.cfg["client_id"], "code_verifier": verifier})

    # -- tokens ------------------------------------------------------------------------------------
    def _token(self, form: dict) -> Tokens:
        try:
            status, body = self.http("POST", self.cfg["token_url"], form)
        except Exception as e:                                           # noqa: BLE001
            raise AuthError("network", tr("OctoWoW could not be reached: {error}", error=e)) from None
        try:
            obj = json.loads(body.decode("utf-8"))
        except ValueError:
            obj = {}
        if status in (400, 401) and obj.get("error") in ("invalid_grant", "invalid_token", "invalid_client"):
            raise AuthError("rejected", tr("Your sign-in has expired. Please sign in again."))
        if status != 200 or not obj.get("access_token"):
            raise AuthError("network" if status >= 500 or status == 429 else "invalid",
                            tr("The sign-in service answered unexpectedly ({status}).", status=status))
        ttl = obj.get("expires_in")
        return Tokens(str(obj["access_token"]), str(obj.get("refresh_token") or ""),
                      self.now() + float(ttl) if isinstance(ttl, (int, float)) else 0.0, str(obj.get("scope") or ""))

    def refresh(self, tokens: Tokens) -> Tokens:
        if not tokens.refresh:
            raise AuthError("rejected", tr("Your sign-in has expired. Please sign in again."))
        new = self._token({"grant_type": "refresh_token", "refresh_token": tokens.refresh,
                           "client_id": self.cfg["client_id"]})
        if not new.refresh:
            new.refresh = tokens.refresh                                   # the server may not rotate it
        return new

    def revoke(self, tokens: Tokens) -> None:
        if not self.cfg.get("revoke_url"):
            return
        for tok, hint in ((tokens.refresh, "refresh_token"), (tokens.access, "access_token")):
            if tok:
                try:
                    self.http("POST", self.cfg["revoke_url"],
                              {"token": tok, "token_type_hint": hint, "client_id": self.cfg["client_id"]})
                except Exception:                                          # noqa: BLE001 - best effort
                    pass

    def profile(self, tokens: Tokens) -> Profile:
        try:
            status, body = self.http("GET", self.cfg["userinfo_url"], None,
                                     {"Authorization": "Bearer " + tokens.access})
        except Exception as e:                                           # noqa: BLE001
            raise AuthError("network", tr("OctoWoW could not be reached: {error}", error=e)) from None
        if status in (401, 403):
            raise AuthError("rejected", tr("Your sign-in has expired. Please sign in again."))
        try:
            obj = json.loads(body.decode("utf-8"))
        except ValueError:
            obj = {}
        name = str(obj.get("preferred_username") or obj.get("username") or obj.get("name") or "").strip()
        if status != 200 or not name or len(name) > 64:
            raise AuthError("invalid", tr("The sign-in service did not say who you are."))
        return Profile(name, str(obj.get("sub") or obj.get("id") or ""),
                       str(obj.get("picture") or obj.get("avatar_url") or ""),
                       str(obj.get("profile") or self.profile_url or ""))


def make_provider(config=None, http=default_http) -> AuthProvider:
    config = PROVIDER_CONFIG if config is None else config
    if not config:
        return UnavailableProvider()
    try:
        return OAuthProvider(config, http)
    except ValueError:
        return UnavailableProvider()


# ── the session ─────────────────────────────────────────────────────────────────────────

SIGNED_OUT, SIGNING_IN, SIGNED_IN = "signed_out", "signing_in", "signed_in"
STATUSES = ("online", "away", "dnd")


class AccountService:
    """Who is signed in, and the secrets that prove it.

    Blocking calls (``restore``, ``sign_in``, ``sign_out``, ``valid_access``) are for a
    worker; the bridge runs them through the task manager. ``on_change`` is called
    after every state change."""

    def __init__(self, provider: AuthProvider, store: credstore.CredentialStore, settings, now=time.time):
        self.provider, self.store, self.settings, self.now = provider, store, settings, now
        self.on_change = lambda: None
        self.state = SIGNED_OUT
        self.message = ""
        self.tokens: Tokens | None = None
        self.profile: Profile | None = None
        self.verified = False                      # the server has confirmed this session this run
        self._lock = threading.RLock()
        self.cancel = threading.Event()

    # -- metadata (not secret) ------------------------------------------------------------------
    @property
    def username(self) -> str:
        return (self.profile.username if self.profile else self.settings.get("account_username")) or ""

    @property
    def avatar_url(self) -> str:
        return (self.profile.avatar_url if self.profile else self.settings.get("account_avatar_url")) or ""

    @property
    def profile_url(self) -> str:
        return (self.profile.profile_url if self.profile and self.profile.profile_url else "") or self.provider.profile_url

    @property
    def persists(self) -> bool:
        return self.store.persistent

    def _changed(self):
        self.on_change()

    def _save(self):
        if self.tokens:
            self.store.set(SERVICE, ACCOUNT, self.tokens.dumps())
        if self.profile:
            self.settings.set("account_username", self.profile.username)
            self.settings.set("account_avatar_url", self.profile.avatar_url)

    def _clear(self):
        self.store.delete(SERVICE, ACCOUNT)
        for key in ("account_username", "account_avatar_url"):
            self.settings.set(key, "")
        self.tokens = self.profile = None
        self.verified = False

    # -- lifecycle -------------------------------------------------------------------------------
    def restore(self) -> None:
        """At start: a saved session comes back; an expired one is refreshed; a rejected one is dropped.
        A network failure keeps the session (it is not proof of anything) and tries again later."""
        if not self.provider.available:
            self.state = SIGNED_OUT
            self._changed()
            return
        with self._lock:
            tokens = Tokens.loads(self.store.get(SERVICE, ACCOUNT))
            if tokens is None:
                self.state = SIGNED_OUT
                self._changed()
                return
            self.tokens = tokens
            self.state = SIGNED_IN
            self._changed()
            try:
                self._ensure_fresh()
                self.profile = self.provider.profile(self.tokens)
                self.verified = True
                self._save()
            except AuthError as e:
                if e.code == "rejected":
                    self._clear()
                    self.state, self.message = SIGNED_OUT, e.message
                else:
                    self.message = e.message                              # offline: stay signed in, unverified
            self._changed()

    def _ensure_fresh(self) -> None:
        if self.tokens and self.tokens.expired(self.now(), EARLY_REFRESH_S):
            self.tokens = self.provider.refresh(self.tokens)
            self.store.set(SERVICE, ACCOUNT, self.tokens.dumps())

    def valid_access(self) -> str | None:
        """An access token good for the next minute or so, refreshed if need be; None if signed out
        or the session was rejected (and then the player is signed out)."""
        with self._lock:
            if self.state != SIGNED_IN or not self.tokens:
                return None
            try:
                self._ensure_fresh()
            except AuthError as e:
                if e.code == "rejected":
                    self._clear()
                    self.state, self.message = SIGNED_OUT, e.message
                    self._changed()
                return None if e.code == "rejected" else self.tokens.access
            return self.tokens.access

    def sign_in(self, open_browser) -> bool:
        with self._lock:
            if self.state == SIGNING_IN:
                return False
            self.state, self.message = SIGNING_IN, ""
            self.cancel = threading.Event()
        self._changed()
        try:
            tokens = self.provider.start(open_browser, self.cancel)
            profile = self.provider.profile(tokens)
        except AuthError as e:
            with self._lock:
                self.state, self.message = SIGNED_OUT, e.message
            self._changed()
            return False
        with self._lock:
            self.tokens, self.profile, self.verified = tokens, profile, True
            self.state, self.message = SIGNED_IN, ""
            self._save()
        self._changed()
        return True

    def cancel_sign_in(self) -> None:
        self.cancel.set()

    def sign_out(self) -> None:
        """Ends the session here for certain; the server is told if it can be reached."""
        with self._lock:
            tokens = self.tokens or Tokens.loads(self.store.get(SERVICE, ACCOUNT))
            self._clear()
            self.state, self.message = SIGNED_OUT, ""
        if tokens:
            try:
                self.provider.revoke(tokens)
            except Exception:                                              # noqa: BLE001
                pass
        self._changed()
