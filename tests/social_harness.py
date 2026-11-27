"""Test harness for the social test backend: a real server on localhost and several independent
"launchers" (each with its own settings, friends cache, session and clock-driven polling).

Used by tests/test_v3_social_backend.py and tests/test_v3_social_qt.py. Nothing here is production code.
"""

import json
import os
import shutil
import tempfile
import threading

from equpdater.core import account as A
from equpdater.core import credstore as C
from equpdater.core import devsocial as D
from equpdater.core import friends as F
from equpdater.core import gamewatch as G
from equpdater.core import presence as P
from equpdater.core import realms as R
from equpdater.core.social_service import SocialService
from equpdater.core.store import JsonStore
from tools.socialserver import server as S

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


class Settings(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def set(self, k, v):
        self[k] = v


class Server:
    """A real HTTP server on 127.0.0.1 with a controllable clock. ``stop()`` and ``start()`` simulate an outage."""

    def __init__(self, clock, ttl=S.TTL, db=":memory:"):
        self.clock, self.ttl, self.db = clock, ttl, db
        self.srv = self.store = self.thread = None
        self.port = 0
        self.start()

    def start(self):
        self.srv, self.store = S.make_server("127.0.0.1", self.port, self.db, self.ttl, self.clock) if self.store is None else \
            self._rebind()
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def _rebind(self):
        srv = S.ThreadingHTTPServer(("127.0.0.1", self.port), S.make_handler(self.store))
        srv.daemon_threads = True
        return srv, self.store

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class Recorder:
    """Wraps the HTTP function so a test can see every request and its status."""

    def __init__(self):
        self.log = []

    def __call__(self, method, url, body=None, headers=None, timeout=8.0):
        try:
            status, obj, hdr = D.default_http(method, url, body, headers, timeout)
        except D.BackendDown:
            self.log.append((method, url.split("/v1", 1)[-1], "down"))
            raise
        self.log.append((method, url.split("/v1", 1)[-1], status))
        return status, obj, hdr

    def statuses(self, path):
        return [s for m, p, s in self.log if p == path]


class Client:
    """One launcher: signs in under a username, keeps a friends cache, polls like the real one."""

    def __init__(self, server, clock, tmp=None, settings=None, store=None, activity=None, data=None):
        self.server, self.clock = server, clock
        self.tmp = tmp or tempfile.mkdtemp(prefix="equ-sc-")
        self.settings = settings if settings is not None else Settings()
        self.cred = store or C.MemoryStore()
        self.http = Recorder()
        self.social_store = JsonStore(os.path.join(self.tmp, "social.json"), {"friends": [], "realms": [], "realms_at": 0}, delay=0)
        self.book = F.FriendBook(self.social_store, now=clock)
        self.backend = D.Backend(server.url, self.http)
        self.auth = D.DevAuthProvider(self.backend, self.settings.get("dev_tokens") or {},
                                      remember=lambda t: self.settings.set("dev_tokens", t))
        self.provider = D.DevPresenceProvider(self.backend, self.book, now=clock)
        self.provider.on_friends = self.book.sync_from
        self.account = A.AccountService(self.auth, self.cred, self.settings, now=clock)
        self.presence = P.PresenceService(self.book, self.provider, self.account, now=clock)
        self.activity = activity
        self.svc = SocialService(self.settings, self.book, self.presence, self.account, R.RealmList(self.social_store),
                                 now=clock, activity=activity)
        self.presence.before_poll = self.svc.refresh_activity

    # -- actions ------------------------------------------------------------------------------
    def login(self, name) -> bool:
        self.auth.username = name
        ok = self.account.sign_in(lambda url: None)
        if ok:
            self.svc.publish()
        return ok

    def logout(self):
        self.account.sign_out()
        self.presence.cache.clear()

    def restore(self):
        self.account.restore()
        return self.account.state == A.SIGNED_IN

    def sync(self, advance=2.0):
        """One heartbeat-and-read round (the clock moves a little first, as real time does)."""
        self.clock.tick(advance)
        self.presence.poll()
        return self.lines()

    def lines(self):
        return {r["name"]: r for r in self.svc.rows() if r["kind"] == "friend"}

    def line(self, name):
        r = self.lines().get(name)
        return r["statusLine"] if r else None

    def dot(self, name):
        return self.lines()[name]["dot"].lower()

    def names(self):
        return sorted(self.lines())


class SimActivity(G.SimulatedActivity):
    """A simulated game, driven by a test (stands in for a running client)."""

    def __init__(self, tmp, realms=None):
        super().__init__(os.path.join(tmp, "activity.json"), realms)
        self.set("launcher")

    def set(self, activity, realm=""):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"activity": activity, "realm": realm}, f)
