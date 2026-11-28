"""Test harness for the social test backend: a backend (the local Python twin, the Worker under workerd, or the
deployed Worker) and several independent "launchers" (each with its own session, friends cache and live link).

Used by tests/test_v3_social_backend.py, tests/test_v3_social_worker.py and tests/test_v3_social_qt.py.
Nothing here is production code. Everything is real network I/O on real threads and real time.
"""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_DIR = os.path.join(ROOT, "cloudflare", "social")
TESTER_KEY = "test-tester-key"
ADMIN_KEY = "test-admin-key"


def sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


class Settings(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def set(self, k, v):
        self[k] = v


def wait_for(cond, timeout=6.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


# ── backends ─────────────────────────────────────────────────────────────────────────────

class PythonBackend:
    """The reference server on a real localhost port. ``ttl`` is short so expiry can be tested in seconds."""
    kind = "python"
    name = "the local Python backend"
    key, admin = TESTER_KEY, ADMIN_KEY
    has_outage = True

    def __init__(self, ttl=3.0, **caps):
        self.ttl, self.caps = ttl, caps
        self.hub = self.srv = self.thread = None
        self.port = 0
        self.start()

    def start(self):
        if self.hub is None:
            self.srv, self.hub = S.make_server("127.0.0.1", self.port, ":memory:", self.ttl,
                                               tester_keys=[TESTER_KEY], admin_key=ADMIN_KEY, caps=self.caps)
        else:
            self.srv = S.QuietServer(("127.0.0.1", self.port), S.make_handler(self.hub))
            self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def outage(self):
        """The backend goes away: the listener closes and every live connection is cut."""
        self.srv.shutdown()
        self.srv.server_close()
        for c in list(self.hub.conns.values()):
            try:
                c.sock.shutdown(2)
            except OSError:
                pass

    def stop(self):
        try:
            self.outage()
        except Exception:
            pass
        self.hub.stop_reaper.set()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class WorkerBackend:
    """The Cloudflare Worker under workerd (``wrangler dev``), with a short TTL, started once and shared."""
    kind = "worker"
    name = "the Worker under workerd"
    key, admin = TESTER_KEY, ADMIN_KEY
    has_outage = False

    def __init__(self, port=8811, ttl=4, alarm=1):
        self.port = port
        self.tmp = tempfile.mkdtemp(prefix="equ-worker-")
        wrangler = os.path.join(WORKER_DIR, "node_modules", ".bin", "wrangler.cmd" if os.name == "nt" else "wrangler")
        self.log = open(os.path.join(self.tmp, "dev.log"), "w")
        env = dict(os.environ)
        cmd = [wrangler, "dev", "--local", "--ip", "127.0.0.1", "--port", str(port), "--persist-to", os.path.join(self.tmp, "state"),
               "--var", f"TESTER_KEYS:{sha(TESTER_KEY)}", "--var", f"ADMIN_KEY_HASH:{sha(ADMIN_KEY)}",
               "--var", f"PRESENCE_TTL_S:{ttl}", "--var", f"ALARM_S:{alarm}", "--var", "EDGE_LIMITS_OFF:1",
               "--var", "REGISTER_PER_IP_HOUR:1000", "--var", "RECOVER_PER_NAME_HOUR:5", "--var", "WS_PER_ACCOUNT_10MIN:25",
               "--var", "FRIEND_ADDS_HOUR:1000"]
        self.proc = subprocess.Popen(cmd, cwd=WORKER_DIR, stdout=self.log, stderr=subprocess.STDOUT, env=env)
        ok = wait_for(self._ready, 90, 0.5)
        if not ok:
            self.stop()
            raise RuntimeError("wrangler dev did not start: " + open(os.path.join(self.tmp, "dev.log")).read()[-800:])

    def _ready(self):
        try:
            return D.default_http("GET", self.url + "/v1/info")[0] == 200
        except D.BackendDown:
            return False

    def stop(self):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True) if os.name == "nt" \
                else self.proc.terminate()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


class LiveBackend:
    """The deployed Worker, for a small smoke test. Needs EQU3_TEST_LIVE_URL and EQU3_TEST_LIVE_KEY."""
    kind = "live"
    name = "the deployed Worker"
    has_outage = False

    def __init__(self, url, key):
        self.url, self.key, self.admin = url.rstrip("/"), key, os.environ.get("EQU3_TEST_LIVE_ADMIN", "")

    def stop(self):
        pass


# ── a launcher ───────────────────────────────────────────────────────────────────────────

class Recorder:
    """The HTTP function, recording every request: the proof that presence is not polled."""

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


class Client:
    """One launcher on its own: credential store, settings, friends cache, link."""

    def __init__(self, backend, cred=None, settings=None, activity=None, ping_every=1.5, clock=time.time, tmp=None):
        self.backend, self.clock = backend, clock
        self.tmp = tmp or tempfile.mkdtemp(prefix="equ-sc-")
        self.settings = settings if settings is not None else Settings()
        self.cred = cred or C.MemoryStore()
        self.http = Recorder()
        self.messages = []                                     # every raw message the server pushed (for assertions)
        self.rejected = []
        self.social_store = JsonStore(os.path.join(self.tmp, "social.json"), {"friends": [], "realms": [], "realms_at": 0}, delay=0)
        self.book = F.FriendBook(self.social_store, now=clock)
        self.be = D.Backend(backend.url, self.http)
        self.vault = D.Vault(self.cred)
        self.auth = D.DevAuthProvider(self.be, self.vault)
        self.provider = D.DevPresenceProvider(self.be, self.book, now=clock,
                                              link_factory=lambda *a, **k: D.SocialLink(*a, ping_every=ping_every, **k))
        self.account = A.AccountService(self.auth, self.cred, self.settings, now=clock)
        self.presence = P.PresenceService(self.book, self.provider, self.account, now=clock)
        self.provider.attach(self.presence, self.account.valid_access)
        self.provider.on_friends = self.book.sync_from
        self.provider.on_rejected = self._rejected
        self.activity = activity
        self.svc = SocialService(self.settings, self.book, self.presence, self.account, R.RealmList(self.social_store),
                                 now=clock, activity=activity)
        self.presence.before_poll = self.svc.refresh_activity
        self.changes = 0
        self.presence.on_change = self._changed
        self.provider.tap = self.messages.append

    def _changed(self):
        self.changes += 1

    def _rejected(self, message):
        self.rejected.append(message)
        self.account.sign_out()
        self.account.message = message

    # -- actions ------------------------------------------------------------------------------
    def login(self, name, key=None, recovery=""):
        self.auth.username = name
        self.auth.key = self.backend.key if key is None else key
        self.auth.recovery = recovery
        ok = self.account.sign_in(lambda url: None)
        if ok:
            self.svc.sync_link()
            self.svc.publish()
            wait_for(lambda: self.link_up(), 6)
        return ok

    def link_up(self):
        return bool(self.provider.link and self.provider.link.connected)

    def logout(self):
        self.account.sign_out()
        self.svc.sync_link()
        self.presence.cache.clear()

    def restore(self):
        self.account.restore()
        self.svc.sync_link()
        return self.account.state == A.SIGNED_IN

    def stop(self):
        try:
            self.provider.ensure_link(False)
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- reading --------------------------------------------------------------------------------
    def rows(self):
        return {r["name"]: r for r in self.svc.rows() if r["kind"] == "friend"}

    def line(self, name):
        r = self.rows().get(name)
        return r["statusLine"] if r else None

    def dot(self, name):
        r = self.rows().get(name)
        return r["dot"].lower() if r else None

    def names(self):
        return sorted(self.rows())

    def sees(self, name, line, timeout=6.0):
        return wait_for(lambda: self.line(name) == line, timeout)

    def raw_ws(self, token=None, timeout=5.0):
        """A bare WebSocket as this account, with no heartbeat of its own (for silence and abuse tests)."""
        from equpdater.core import wsclient
        return wsclient.connect(D.ws_url(self.backend.url), {"Authorization": "Bearer " + (token or self.account.tokens.access)})


class SimActivity(G.SimulatedActivity):
    """A simulated game, driven by a test (stands in for a running client)."""

    def __init__(self, tmp, realms=None):
        super().__init__(os.path.join(tmp, "activity.json"), realms)
        self.set("launcher")

    def set(self, activity, realm=""):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"activity": activity, "realm": realm}, f)
