"""The EqUpdater social TEST backend, reference implementation (protocol v2): accounts, friends and real-time
presence over WebSockets. Standard library only (sqlite3 + http.server).

    python tools/socialserver/server.py --port 8787 [--host 0.0.0.0] [--db social-test.sqlite] [--tester-key KEY]

This is the local twin of the hosted Cloudflare Worker (cloudflare/social): the same protocol, the same rules,
so one test suite runs against both (tests/test_v3_social_backend.py). It is for development and testing, not
production authentication; see docs/v3/SOCIAL_TEST_BACKEND.md.

Protocol v2 (JSON; ``Authorization: Bearer <session token>`` except info/register/recover)
  GET    /v1/info                       -> {"protocol":2,"requires_key":bool}
  POST   /v1/register {username,key?}   -> 200 {username,token,recovery} | 400 | 403 key | 409 taken | 429
  POST   /v1/recover  {username,recovery} -> 200 {username,token} (the old session is revoked: a transfer) | 401 | 429
  POST   /v1/logout                     -> 204 (this session ends; the account stays yours)
  GET    /v1/me                         -> 200 {username}
  POST   /v1/friends {username}         -> 200 | 400 | 404 | 409
  PUT    /v1/friends/<name> {favourite} -> 204 | 404
  DELETE /v1/friends/<name>             -> 204 | 404
  GET    /v1/ws  (Upgrade: websocket)   -> presence stream, below
  GET    /v1/admin/metrics (admin key)  -> private counters

WebSocket messages (text, JSON; the text "ping" is answered "pong" and is the heartbeat):
  server -> client  {"t":"friends","friends":[{username,favourite,state,activity,realm}]}   (on connect and on edits)
                    {"t":"presence","u":name,"state","activity","realm"}                   (only to friends of the changer)
                    {"t":"bye","reason"}                                                   (replaced / lapsed / signed out)
  client -> server  {"t":"state","status":"online|away|dnd|invisible","activity":"launcher|in_game","realm":""}
A name belongs to its account for good: nobody else can take it, online or not. The account's secrets are
a session token and a recovery token (both random, both stored only as SHA-256 hashes); the recovery token is
shown once and moves the account to another machine. Presence lives in memory: one connection per account (a new
one replaces the old), offline the moment the connection closes or goes quiet for ``ttl`` seconds. An invisible
player is reported to others as offline and their activity and realm are never kept or sent.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import socket
import sqlite3
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
STATUSES = ("online", "away", "dnd", "invisible")
ACTIVITIES = ("launcher", "in_game")
REALMS = ("nzoth", "yshaarj", "cthun")
TTL = 120.0                    # seconds of silence before a connection is dropped (clients ping every 45)
MAX_BODY = 4096
MAX_FRAME = 1024
MAX_FRIENDS = 200
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Limiter:
    """Sliding-window counters: ``hit(key, limit, window)`` is True while under the limit."""

    def __init__(self, now):
        self.now, self.hits, self.lock = now, {}, threading.Lock()

    def hit(self, key, limit, window) -> bool:
        with self.lock:
            t = self.now()
            q = [x for x in self.hits.get(key, []) if t - x < window]
            if len(q) >= limit:
                self.hits[key] = q
                return False
            q.append(t)
            self.hits[key] = q
            return True


class Conn:
    """One WebSocket connection."""

    def __init__(self, sock, account, name):
        self.sock, self.account, self.name = sock, account, name
        self.status, self.activity, self.realm = "online", "launcher", ""
        self.last_rx = time.time()
        self.lock = threading.Lock()
        self.open = True
        self.tokens = 20.0
        self.refilled = time.time()

    def send(self, obj) -> bool:
        data = (obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))).encode()
        head = b"\x81" + (bytes([len(data)]) if len(data) < 126 else b"\x7e" + struct.pack(">H", len(data)))
        try:
            with self.lock:
                self.sock.sendall(head + data)
            return True
        except OSError:
            self.open = False
            return False

    def close(self, code=1000):
        if not self.open:
            return
        self.open = False
        try:
            with self.lock:
                self.sock.sendall(b"\x88\x02" + struct.pack(">H", code))
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class Hub:
    def __init__(self, path=":memory:", ttl=TTL, now=time.time, tester_keys=(), admin_key="", caps=None):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.ttl, self.now = ttl, now
        self.tester_keys = {sha(k) for k in tester_keys if k}
        self.admin_hash = sha(admin_key) if admin_key else ""
        self.caps = {"register_per_ip_hour": 20, "recover_per_name_hour": 8, "ws_per_account_10min": 40,
                     "friend_adds_hour": 120, "accounts": 500, **(caps or {})}
        self.limits = Limiter(now)
        self.conns: dict = {}                       # account id -> Conn
        self.counters = {"http": 0, "errors": 0, "ws_connects": 0, "ws_in": 0, "ws_out": 0}
        self.peak = {"conn": 0, "game": 0}
        with self.lock:
            self.db.executescript("""
                create table if not exists accounts (id text primary key, name text not null, key text not null unique,
                    session_hash text not null, recovery_hash text not null, created real not null, last_day integer not null);
                create index if not exists accounts_session on accounts(session_hash);
                create table if not exists friends (owner text not null, friend text not null,
                    favourite integer not null default 0, primary key (owner, friend));
                create index if not exists friends_friend on friends(friend);
            """)
            self.db.commit()
        self.stop_reaper = threading.Event()
        threading.Thread(target=self._reaper, daemon=True).start()

    # -- helpers ---------------------------------------------------------------------------------
    def day(self) -> int:
        return int(self.now() // 86400)

    def requires_key(self) -> bool:
        return bool(self.tester_keys)

    def account_for_token(self, token: str):
        if not token:
            return None
        with self.lock:
            row = self.db.execute("select id, name, key, last_day from accounts where session_hash=?", (sha(token),)).fetchone()
            if row and row[3] != self.day():
                self.db.execute("update accounts set last_day=? where id=?", (self.day(), row[0]))
                self.db.commit()
        return row[:3] if row else None

    # -- accounts ----------------------------------------------------------------------------------
    def register(self, name, key, ip):
        if not isinstance(name, str) or not NAME_RE.match(name):
            return 400, {"error": "A username is 3 to 20 letters, numbers, dashes or underscores."}
        if not self.limits.hit("reg:" + ip, self.caps["register_per_ip_hour"], 3600):
            return 429, {"error": "Too many sign-ups from here. Try again later."}
        if self.tester_keys and sha(str(key or "")) not in self.tester_keys:
            return 403, {"error": "A tester key is needed to create an account."}
        with self.lock:
            if self.db.execute("select count(*) from accounts").fetchone()[0] >= self.caps["accounts"]:
                return 503, {"error": "The test environment is full."}
            if self.db.execute("select 1 from accounts where key=?", (name.lower(),)).fetchone():
                return 409, {"error": "That username is taken."}
            token, recovery = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self.db.execute("insert into accounts values (?,?,?,?,?,?,?)",
                            (uuid.uuid4().hex, name, name.lower(), sha(token), sha(recovery), self.now(), self.day()))
            self.db.commit()
        return 200, {"username": name, "token": token, "recovery": recovery}

    def recover(self, name, recovery, ip):
        if not isinstance(name, str) or not NAME_RE.match(name) or not isinstance(recovery, str):
            return 400, {"error": "bad request"}
        if not self.limits.hit("rec:" + name.lower(), self.caps["recover_per_name_hour"], 3600):
            return 429, {"error": "Too many attempts. Try again later."}
        with self.lock:
            row = self.db.execute("select id, name, recovery_hash from accounts where key=?", (name.lower(),)).fetchone()
            if not row or not hmac.compare_digest(sha(recovery), row[2]):
                return 401, {"error": "That recovery code does not match."}
            token = secrets.token_urlsafe(32)
            self.db.execute("update accounts set session_hash=? where id=?", (sha(token), row[0]))
            self.db.commit()
        conn = self.conns.get(row[0])
        if conn:                                            # the old machine is signed out
            conn.send({"t": "bye", "reason": "recovered elsewhere"})
            conn.close(4001)
        return 200, {"username": row[1], "token": token}

    def logout(self, account_id):
        with self.lock:
            self.db.execute("update accounts set session_hash=? where id=?", (sha(secrets.token_urlsafe(16)), account_id))
            self.db.commit()
        conn = self.conns.get(account_id)
        if conn:
            conn.send({"t": "bye", "reason": "signed out"})
            conn.close(1000)

    # -- friends -------------------------------------------------------------------------------------
    def add_friend(self, owner, name):
        if not isinstance(name, str) or not NAME_RE.match(name):
            return 400, {"error": "That is not a valid username."}
        if not self.limits.hit("add:" + owner, self.caps["friend_adds_hour"], 3600):
            return 429, {"error": "Too many friend requests. Try again later."}
        with self.lock:
            row = self.db.execute("select id, name from accounts where key=?", (name.lower(),)).fetchone()
            if not row:
                return 404, {"error": "No user has that name."}
            if row[0] == owner:
                return 400, {"error": "You cannot add yourself."}
            if self.db.execute("select 1 from friends where owner=? and friend=?", (owner, row[0])).fetchone():
                return 409, {"error": "Already in your friends list."}
            if self.db.execute("select count(*) from friends where owner=?", (owner,)).fetchone()[0] >= MAX_FRIENDS:
                return 400, {"error": "The friends list is full."}
            self.db.execute("insert into friends(owner, friend) values (?,?)", (owner, row[0]))
            self.db.commit()
        self.push_friends(owner)
        return 200, {"username": row[1]}

    def set_favourite(self, owner, name, value):
        with self.lock:
            row = self.db.execute("select id from accounts where key=?", ((name or "").lower(),)).fetchone()
            cur = self.db.execute("update friends set favourite=? where owner=? and friend=?",
                                  (1 if value else 0, owner, row[0] if row else ""))
            self.db.commit()
        if cur.rowcount:
            self.push_friends(owner)
        return 204 if cur.rowcount else 404

    def remove_friend(self, owner, name):
        with self.lock:
            row = self.db.execute("select id from accounts where key=?", ((name or "").lower(),)).fetchone()
            cur = self.db.execute("delete from friends where owner=? and friend=?", (owner, row[0] if row else ""))
            self.db.commit()
        if cur.rowcount:
            self.push_friends(owner)
        return 204 if cur.rowcount else 404

    # -- presence ---------------------------------------------------------------------------------------
    @staticmethod
    def shown(conn):
        """How others see a connection. Invisible and absent both read as offline, with nothing else."""
        if conn is None or not conn.open or conn.status == "invisible":
            return {"state": "offline", "activity": "", "realm": ""}
        return {"state": conn.status, "activity": conn.activity, "realm": conn.realm if conn.activity == "in_game" else ""}

    def friends_view(self, owner):
        with self.lock:
            rows = self.db.execute("select a.id, a.name, f.favourite from friends f join accounts a on a.id=f.friend "
                                   "where f.owner=? order by a.name", (owner,)).fetchall()
        return [{"username": n, "favourite": bool(fav), **self.shown(self.conns.get(i))} for i, n, fav in rows]

    def push_friends(self, owner):
        c = self.conns.get(owner)
        if c and c.open:
            self.send(c, {"t": "friends", "friends": self.friends_view(owner)})

    def send(self, conn, obj):
        if conn.send(obj):
            self.counters["ws_out"] += 1

    def broadcast_presence(self, conn_or_acct, name):
        """Tell exactly the accounts that have this one as a friend, if they are connected."""
        acct = conn_or_acct.account if isinstance(conn_or_acct, Conn) else conn_or_acct
        with self.lock:
            owners = [r[0] for r in self.db.execute("select owner from friends where friend=?", (acct,)).fetchall()]
        msg = {"t": "presence", "u": name, **self.shown(self.conns.get(acct))}
        for o in owners:
            c = self.conns.get(o)
            if c and c.open:
                self.send(c, msg)

    def connected(self, conn: Conn):
        old = self.conns.get(conn.account)
        self.conns[conn.account] = conn
        if old and old.open:
            old.send({"t": "bye", "reason": "replaced"})
            old.close(4000)
        self.counters["ws_connects"] += 1
        self.peak["conn"] = max(self.peak["conn"], sum(1 for c in self.conns.values() if c.open))
        self.send(conn, {"t": "friends", "friends": self.friends_view(conn.account)})     # restore on every (re)connect
        self.broadcast_presence(conn, conn.name)

    def state(self, conn: Conn, msg: dict):
        status = msg.get("status") if msg.get("status") in STATUSES else "online"
        activity = msg.get("activity") if msg.get("activity") in ACTIVITIES else "launcher"
        realm = msg.get("realm") if msg.get("realm") in REALMS and activity == "in_game" else ""
        if status == "invisible":
            activity, realm = "launcher", ""                    # never kept, never sent
        if (status, activity, realm) == (conn.status, conn.activity, conn.realm):
            return
        conn.status, conn.activity, conn.realm = status, activity, realm
        self.peak["game"] = max(self.peak["game"], sum(1 for c in self.conns.values() if c.open and c.activity == "in_game"))
        self.broadcast_presence(conn, conn.name)

    def disconnected(self, conn: Conn):
        conn.open = False
        if self.conns.get(conn.account) is conn:
            del self.conns[conn.account]
            self.broadcast_presence(conn.account, conn.name)

    def _reaper(self):
        while not self.stop_reaper.wait(max(0.2, self.ttl / 4)):
            t = time.time()
            for c in list(self.conns.values()):
                if c.open and t - c.last_rx > self.ttl:
                    c.send({"t": "bye", "reason": "no heartbeat"})
                    c.close(1001)

    # -- private metrics ----------------------------------------------------------------------------------
    def metrics(self):
        with self.lock:
            accounts = self.db.execute("select count(*) from accounts").fetchone()[0]
            dau = self.db.execute("select count(*) from accounts where last_day=?", (self.day(),)).fetchone()[0]
        live = [c for c in self.conns.values() if c.open]
        return {"accounts": accounts, "daily_active": dau, "connected": len(live),
                "in_game": sum(1 for c in live if c.activity == "in_game" and c.status != "invisible"),
                "peak": dict(self.peak), **self.counters}


def make_handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EqUpdaterSocialTest/2"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def ip(self):
            return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

        def _send(self, code, body=None, headers=None):
            raw = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
            hub.counters["http"] += 1
            if code >= 400:
                hub.counters["errors"] += 1

        def _json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            data = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data

        def _token(self):
            h = self.headers.get("Authorization") or ""
            return h[7:] if h.startswith("Bearer ") else ""

        def _route(self, method):
            path = urlsplit(self.path).path
            try:
                body = self._json() if method in ("POST", "PUT") else {}
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "bad request"})
            if method == "GET" and path == "/v1/info":
                return self._send(200, {"protocol": 2, "requires_key": hub.requires_key()})
            if method == "POST" and path == "/v1/register":
                code, out = hub.register(body.get("username"), body.get("key"), self.ip())
                return self._send(code, out)
            if method == "POST" and path == "/v1/recover":
                code, out = hub.recover(body.get("username"), body.get("recovery"), self.ip())
                return self._send(code, out)
            if method == "GET" and path == "/v1/admin/metrics":
                if not hub.admin_hash or not hmac.compare_digest(sha(self._token()), hub.admin_hash):
                    return self._send(401, {"error": "no"})
                return self._send(200, hub.metrics())
            me = hub.account_for_token(self._token())
            if me is None:
                return self._send(401, {"error": "sign in again"})
            acct, name, _key = me
            if method == "GET" and path == "/v1/ws":
                if (self.headers.get("Upgrade") or "").lower() != "websocket":
                    return self._send(426, {"error": "a WebSocket connection is required"})
                return self._websocket(acct, name)
            if method == "GET" and path == "/v1/me":
                return self._send(200, {"username": name})
            if method == "POST" and path == "/v1/logout":
                hub.logout(acct)
                return self._send(204)
            if method == "POST" and path == "/v1/friends":
                code, out = hub.add_friend(acct, body.get("username"))
                return self._send(code, out)
            if path.startswith("/v1/friends/"):
                target = unquote(path[len("/v1/friends/"):])
                if method == "DELETE":
                    return self._send(hub.remove_friend(acct, target))
                if method == "PUT":
                    return self._send(hub.set_favourite(acct, target, bool(body.get("favourite"))))
            return self._send(404, {"error": "not found"})

        # -- the WebSocket ----------------------------------------------------------------------------
        def _websocket(self, acct, name):
            if not hub.limits.hit("ws:" + acct, hub.caps["ws_per_account_10min"], 600):
                return self._send(429, {"error": "Too many reconnections. Slow down."}, {"Retry-After": "30"})
            key = self.headers.get("Sec-WebSocket-Key") or ""
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.close_connection = True
            conn = Conn(self.connection, acct, name)
            self.connection.settimeout(0.5)
            hub.connected(conn)
            try:
                self._frames(conn)
            except (OSError, ValueError):
                pass
            finally:
                hub.disconnected(conn)
                try:
                    self.connection.close()
                except OSError:
                    pass

        def _read(self, n, conn):
            """Read exactly n bytes; wakes every half second to notice that the connection was closed from elsewhere
            (a blocked recv is not reliably interrupted by shutdown on every platform)."""
            buf = b""
            while len(buf) < n:
                if not conn.open:
                    raise OSError("closed")
                try:
                    chunk = self.connection.recv(n - len(buf))
                except socket.timeout:
                    continue
                if not chunk:
                    raise OSError("closed")
                buf += chunk
            return buf

        def _frames(self, conn):
            while conn.open:
                b1, b2 = self._read(2, conn)
                opcode, masked, ln = b1 & 0x0F, bool(b2 & 0x80), b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack(">H", self._read(2, conn))[0]
                elif ln == 127:
                    ln = struct.unpack(">Q", self._read(8, conn))[0]
                if ln > MAX_FRAME or not masked:
                    conn.close(1009 if ln > MAX_FRAME else 1002)
                    return
                mask = self._read(4, conn)
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._read(ln, conn)))
                conn.last_rx = time.time()
                if opcode == 8:
                    conn.close(1000)
                    return
                if opcode == 9:
                    with conn.lock:
                        conn.sock.sendall(b"\x8a" + bytes([len(data)]) + data)
                    continue
                if opcode != 1:
                    continue
                hub.counters["ws_in"] += 1
                text = data.decode("utf-8", "replace")
                if text == "ping":
                    conn.send("pong")
                    continue
                t = time.time()
                conn.tokens = min(20.0, conn.tokens + (t - conn.refilled) * 2.0)      # 20 messages, refilled at 2 per second
                conn.refilled = t
                if conn.tokens < 1:
                    conn.send({"t": "bye", "reason": "too many messages"})
                    conn.close(1008)
                    return
                conn.tokens -= 1
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("t") == "state":
                    hub.state(conn, msg)
                elif isinstance(msg, dict) and msg.get("t") == "sync":
                    hub.push_friends(conn.account)

        def do_GET(self):      # noqa: N802
            self._route("GET")

        def do_POST(self):     # noqa: N802
            self._route("POST")

        def do_PUT(self):      # noqa: N802
            self._route("PUT")

        def do_DELETE(self):   # noqa: N802
            self._route("DELETE")
    return Handler


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                                      # a client that vanishes mid-request is normal, not news


def make_server(host="127.0.0.1", port=0, db=":memory:", ttl=TTL, now=time.time, **kw):
    """(server, hub). ``server.serve_forever()`` in a thread; ``server.server_address`` has the port."""
    hub = Hub(db, ttl, now, **kw)
    srv = QuietServer((host, port), make_handler(hub))
    srv.daemon_threads = True
    return srv, hub


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--db", default="social-test.sqlite")
    ap.add_argument("--tester-key", default="", help="require this key to register (default: open)")
    ap.add_argument("--admin-key", default="")
    a = ap.parse_args()
    srv, _ = make_server(a.host, a.port, a.db, tester_keys=[a.tester_key], admin_key=a.admin_key)
    print(f"EqUpdater social test backend on http://{a.host}:{srv.server_address[1]}  (db: {a.db})", flush=True)
    print("Development/testing only.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
