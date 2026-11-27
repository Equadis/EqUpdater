"""The EqUpdater social TEST backend: a small shared server so several launchers (on one machine
or many) can sign in under a username, keep a friends list and see each other's presence.

    python tools/socialserver/server.py --port 8787 [--host 0.0.0.0] [--db social-test.sqlite]

**This is a development and testing system, not production authentication.** A username is claimed
with no password, no email and no OctoWoW account; the token it hands back only proves "this client
claimed this name". Anyone who can reach the server can claim any free name. It exposes no real
account data and no privileged function: it stores usernames, friend links, favourites, and a
heartbeat (status, launcher-or-game, realm) that expires. The production path will be OctoWoW's own
sign-in (docs/v3/OCTOWOW_INTEGRATION_PROPOSAL.md); the client keeps the two separate
(equpdater/core/devsocial.py).

Standard library only (sqlite3 + http.server): nothing to install, nothing to pay for. The same
protocol is small enough for a Cloudflare Worker with D1 (PROTOCOL.md) should a shared deployment
be wanted; this file is the reference and the thing the tests run against.

Protocol v1 (JSON; ``Authorization: Bearer <token>`` except login):
    POST   /v1/login   {"username", "token"?}  -> 200 {"username","token"} | 400 | 409 in use
    POST   /v1/logout                          -> 204 (the player is offline at once)
    GET    /v1/me                              -> 200 {"username"}
    POST   /v1/sync    {"status","activity","realm"}  [If-None-Match]
                                               -> 200 {"friends":[{"username","favourite","state","activity","realm"}]}
                                                  + ETag | 304 (nothing changed) | 429 Retry-After
    POST   /v1/friends {"username"}            -> 200 | 400 self | 404 no such user | 409 already
    PUT    /v1/friends/<name> {"favourite"}    -> 204 | 404
    DELETE /v1/friends/<name>                  -> 204 | 404
``sync`` is both the heartbeat (own state) and the read (friends with presence), one request about
every five seconds. A player is offline once no heartbeat has arrived for ``ttl`` seconds (30), so a
closed launcher, a crash or a lost connection never stays "online". An invisible player is reported
to others as offline; their own sync still works. ``state`` is online | away | dnd | offline;
``activity`` is launcher | in_game (only when not offline); ``realm`` only while in_game.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
STATUSES = ("online", "away", "dnd", "invisible")
ACTIVITIES = ("launcher", "in_game")
REALMS = ("nzoth", "yshaarj", "cthun")
TTL = 30.0                       # seconds without a heartbeat before a player is offline
MIN_SYNC_GAP = 0.8               # a token may sync about once a second at most
MAX_BODY = 4096
MAX_FRIENDS = 200


class Store:
    """Users, friend links and live presence. Presence is memory only: it is a heartbeat."""

    def __init__(self, path=":memory:", ttl=TTL, now=time.time):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.ttl, self.now = ttl, now
        self.presence: dict = {}          # key -> {status, activity, realm, seen}
        self.last_sync: dict = {}         # token hash -> time
        self.version = 0                  # bumps on every change anyone could see (for ETag)
        with self.lock:
            self.db.executescript("""
                create table if not exists users (key text primary key, name text not null,
                                                  token_hash text not null, created real not null);
                create table if not exists friends (owner text not null, friend text not null,
                                                    favourite integer not null default 0,
                                                    primary key (owner, friend));
            """)
            self.db.commit()

    # -- identity ---------------------------------------------------------------------------
    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def alive(self, key: str) -> bool:
        p = self.presence.get(key)
        return bool(p) and self.now() - p["seen"] <= self.ttl and p["status"] is not None

    def login(self, name: str, token: str = "") -> tuple:
        """(status, body). 400 bad name; 409 the name is in use by a live session of someone else."""
        if not isinstance(name, str) or not NAME_RE.match(name):
            return 400, {"error": "A username is 3 to 20 letters, numbers, dashes or underscores."}
        key = name.lower()
        with self.lock:
            row = self.db.execute("select name, token_hash from users where key=?", (key,)).fetchone()
            if row is None:
                tok = secrets.token_urlsafe(24)
                self.db.execute("insert into users values (?,?,?,?)", (key, name, self._hash(tok), self.now()))
                self.db.commit()
                self.version += 1
                return 200, {"username": name, "token": tok}
            if token and hmac.compare_digest(self._hash(token), row[1]):
                return 200, {"username": row[0], "token": token}            # resuming the same claim
            if self.alive(key):
                return 409, {"error": "That username is in use right now."}
            tok = secrets.token_urlsafe(24)                                   # free again: claim it (test system)
            self.db.execute("update users set token_hash=? where key=?", (self._hash(tok), key))
            self.db.commit()
            return 200, {"username": row[0], "token": tok}

    def who(self, token: str):
        if not token:
            return None
        h = self._hash(token)
        with self.lock:
            row = self.db.execute("select key, name from users where token_hash=?", (h,)).fetchone()
        return (row[0], row[1], h) if row else None

    def logout(self, key: str):
        with self.lock:
            if self.presence.pop(key, None) is not None:
                self.version += 1

    # -- friends -----------------------------------------------------------------------------
    def add_friend(self, owner: str, name: str) -> tuple:
        if not isinstance(name, str) or not NAME_RE.match(name):
            return 400, {"error": "That is not a valid username."}
        key = name.lower()
        with self.lock:
            if key == owner:
                return 400, {"error": "You cannot add yourself."}
            if not self.db.execute("select 1 from users where key=?", (key,)).fetchone():
                return 404, {"error": "No user has that name."}
            if self.db.execute("select 1 from friends where owner=? and friend=?", (owner, key)).fetchone():
                return 409, {"error": "Already in your friends list."}
            if self.db.execute("select count(*) from friends where owner=?", (owner,)).fetchone()[0] >= MAX_FRIENDS:
                return 400, {"error": "The friends list is full."}
            self.db.execute("insert into friends(owner, friend) values (?,?)", (owner, key))
            self.db.commit()
            self.version += 1
            display = self.db.execute("select name from users where key=?", (key,)).fetchone()[0]
        return 200, {"username": display}

    def set_favourite(self, owner: str, name: str, value: bool) -> int:
        with self.lock:
            cur = self.db.execute("update friends set favourite=? where owner=? and friend=?",
                                  (1 if value else 0, owner, (name or "").lower()))
            self.db.commit()
            if cur.rowcount:
                self.version += 1
            return 204 if cur.rowcount else 404

    def remove_friend(self, owner: str, name: str) -> int:
        with self.lock:
            cur = self.db.execute("delete from friends where owner=? and friend=?", (owner, (name or "").lower()))
            self.db.commit()
            if cur.rowcount:
                self.version += 1
            return 204 if cur.rowcount else 404

    # -- presence ----------------------------------------------------------------------------
    def heartbeat(self, key: str, body: dict):
        status = body.get("status") if body.get("status") in STATUSES else "online"
        activity = body.get("activity") if body.get("activity") in ACTIVITIES else "launcher"
        realm = body.get("realm") if body.get("realm") in REALMS and activity == "in_game" else ""
        with self.lock:
            old = self.presence.get(key)
            was_alive = self.alive(key)
            self.presence[key] = {"status": status, "activity": activity, "realm": realm, "seen": self.now()}
            if not was_alive or (old and (old["status"], old["activity"], old["realm"]) != (status, activity, realm)):
                self.version += 1

    def shown(self, key: str) -> dict:
        """How others see ``key``. Invisible and expired both read as offline."""
        p = self.presence.get(key)
        if not p or not self.alive(key) or p["status"] == "invisible":
            return {"state": "offline", "activity": "", "realm": ""}
        return {"state": p["status"], "activity": p["activity"], "realm": p["realm"]}

    def friends_view(self, owner: str) -> list:
        with self.lock:
            rows = self.db.execute("select u.key, u.name, f.favourite from friends f join users u on u.key=f.friend "
                                   "where f.owner=? order by u.name", (owner,)).fetchall()
            return [{"username": name, "favourite": bool(fav), **self.shown(key)} for key, name, fav in rows]

    def expire(self):
        """Called before reading: a player whose heartbeat lapsed changes the version (so ETags move)."""
        with self.lock:
            lapsed = [k for k, p in self.presence.items() if p["status"] is not None and self.now() - p["seen"] > self.ttl]
            for k in lapsed:
                self.presence[k]["status"] = None
                self.version += 1

    def changes_state(self, key: str, body: dict) -> bool:
        """Does this heartbeat say something new about the player? (Then it is let through at once.)"""
        status = body.get("status") if body.get("status") in STATUSES else "online"
        activity = body.get("activity") if body.get("activity") in ACTIVITIES else "launcher"
        realm = body.get("realm") if body.get("realm") in REALMS and activity == "in_game" else ""
        with self.lock:
            p = self.presence.get(key)
            return not (p and self.alive(key) and (p["status"], p["activity"], p["realm"]) == (status, activity, realm))

    def throttle(self, token_hash: str) -> float:
        """0 if allowed, else seconds to wait."""
        with self.lock:
            now = self.now()
            last = self.last_sync.get(token_hash, 0.0)
            if now - last < MIN_SYNC_GAP:
                return MIN_SYNC_GAP - (now - last)
            self.last_sync[token_hash] = now
            return 0.0


def make_handler(store: Store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EqUpdaterSocialTest/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body=None, headers=None):
            raw = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if code != 304:
                self.wfile.write(raw)

        def _json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            data = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data

        def _auth(self):
            h = self.headers.get("Authorization") or ""
            return store.who(h[7:] if h.startswith("Bearer ") else "")

        def _route(self, method):
            path = urlsplit(self.path).path
            try:
                body = self._json() if method in ("POST", "PUT") else {}
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "bad request"})
            if method == "POST" and path == "/v1/login":
                code, out = store.login(body.get("username"), str(body.get("token") or ""))
                return self._send(code, out)
            me = self._auth()
            if me is None:
                return self._send(401, {"error": "sign in again"})
            key, name, thash = me
            if method == "GET" and path == "/v1/me":
                return self._send(200, {"username": name})
            if method == "POST" and path == "/v1/logout":
                store.logout(key)
                return self._send(204)
            if method == "POST" and path == "/v1/sync":
                wait = 0.0 if store.changes_state(key, body) else store.throttle(thash)     # a new status is never held back
                if wait:
                    return self._send(429, {"error": "slow down"}, {"Retry-After": str(max(1, int(wait + 0.99)))})
                store.heartbeat(key, body)
                store.expire()
                friends = store.friends_view(key)
                etag = '"%s"' % hashlib.sha1(json.dumps(friends, sort_keys=True).encode()).hexdigest()[:16]
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, None, {"ETag": etag})
                return self._send(200, {"friends": friends}, {"ETag": etag})
            if method == "POST" and path == "/v1/friends":
                code, out = store.add_friend(key, body.get("username"))
                return self._send(code, out)
            if path.startswith("/v1/friends/"):
                target = unquote(path[len("/v1/friends/"):])
                if method == "DELETE":
                    return self._send(store.remove_friend(key, target))
                if method == "PUT":
                    return self._send(store.set_favourite(key, target, bool(body.get("favourite"))))
            return self._send(404, {"error": "not found"})

        def do_GET(self):      # noqa: N802
            self._route("GET")

        def do_POST(self):     # noqa: N802
            self._route("POST")

        def do_PUT(self):      # noqa: N802
            self._route("PUT")

        def do_DELETE(self):   # noqa: N802
            self._route("DELETE")
    return Handler


def make_server(host="127.0.0.1", port=0, db=":memory:", ttl=TTL, now=time.time):
    """(server, store). ``server.serve_forever()`` in a thread; ``server.server_address`` has the port."""
    store = Store(db, ttl, now)
    srv = ThreadingHTTPServer((host, port), make_handler(store))
    srv.daemon_threads = True
    return srv, store


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--db", default="social-test.sqlite")
    a = ap.parse_args()
    srv, _ = make_server(a.host, a.port, a.db)
    print(f"EqUpdater social test backend on http://{a.host}:{srv.server_address[1]}  (db: {a.db})", flush=True)
    print("Development/testing only: usernames are claimed with no password.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
