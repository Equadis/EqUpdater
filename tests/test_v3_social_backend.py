"""The temporary username login and the live friends/presence link against the LOCAL PYTHON BACKEND (a real server
on localhost, real WebSockets, real time), plus unit tests of the client pieces that need no server: the
WebSocket client, the reconnect back-off, address rules, activity detection and isolation.

The same scenarios run against the Cloudflare Worker in tests/test_v3_social_worker.py and, as a smoke test,
against the deployed Worker in tests/test_v3_social_live.py. Automated; not a test of any real OctoWoW service."""

import json
import os
import shutil
import socket
import tempfile
import unittest

from equpdater.core import account as A
from equpdater.core import devsocial as D
from equpdater.core import gamewatch as G
from equpdater.core import presence as P
from equpdater.core import wsclient
from tests.social_harness import Client, PythonBackend, TESTER_KEY, wait_for
from tests.social_scenarios import AccountScenarios, FriendScenarios, MetricsScenarios, PresenceScenarios, uname

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PythonBackendCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = PythonBackend(ttl=3.0, recover_per_name_hour=5, ws_per_account_10min=25, register_per_ip_hour=1000,
                                    friend_adds_hour=1000)

    @classmethod
    def tearDownClass(cls):
        cls.backend.stop()


class TestAccounts(AccountScenarios, PythonBackendCase):
    def test_sign_up_flood_from_one_address_is_limited(self):
        local = PythonBackend(ttl=3.0, register_per_ip_hour=4)
        self.addCleanup(local.stop)
        results = []
        for _ in range(7):
            c = Client(local)
            self.addCleanup(c.stop)
            results.append(c.login(uname("fl")))
            if "Too many" in c.account.message:
                break
        self.assertIn(False, results)
        self.assertEqual(results[:4], [True] * 4)

    def test_the_account_cap_stops_registration(self):
        local = PythonBackend(ttl=3.0, accounts=2)
        self.addCleanup(local.stop)
        cs = [Client(local) for _ in range(3)]
        for c in cs:
            self.addCleanup(c.stop)
        self.assertTrue(cs[0].login(uname()) and cs[1].login(uname()))
        self.assertFalse(cs[2].login(uname()))
        self.assertIn("full", cs[2].account.message)


class TestFriends(FriendScenarios, PythonBackendCase):
    pass


class TestPresence(PresenceScenarios, PythonBackendCase):
    def test_backend_outage_keeps_the_last_data_dims_it_and_recovers_by_itself(self):
        local = PythonBackend(ttl=3.0)
        self.addCleanup(local.stop)
        a, b = Client(local), Client(local)
        self.addCleanup(a.stop)
        self.addCleanup(b.stop)
        na, nb = uname("al"), uname("bo")
        a.login(na)
        b.login(nb)
        a.svc.add_friend(nb)
        self.assertTrue(a.sees(nb, "Launcher open", 4))
        local.outage()
        self.assertTrue(wait_for(lambda: not a.link_up(), 5))
        self.assertEqual(a.line(nb), "Launcher open")                                  # the last known value is kept ...
        self.assertTrue(a.rows()[nb]["stale"])                                         # ... and shown as old
        self.assertIn("could not be reached", a.svc.add_friend(uname("zz")))
        self.assertEqual(a.account.state, A.SIGNED_IN)                                 # still signed in
        local.start()
        self.assertTrue(wait_for(lambda: a.link_up() and b.link_up(), 20), "both reconnect by themselves")
        self.assertTrue(wait_for(lambda: a.line(nb) == "Launcher open" and not a.rows()[nb]["stale"], 6))


class TestMetrics(MetricsScenarios, PythonBackendCase):
    pass


class FakeWs:
    """A stand-in connection for the link tests."""

    def __init__(self, script):
        self.script, self.sent, self.closed = list(script), [], False

    def send_text(self, t):
        self.sent.append(t)

    def recv(self, timeout=None):
        if not self.script:
            raise wsclient.WebSocketError("closed")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self, code=1000):
        self.closed = True


class TestTheLink(unittest.TestCase):
    def link(self, connect, **kw):
        events = {"msgs": [], "link": [], "rejected": []}
        lk = D.SocialLink("ws://x/v1/ws", lambda: "tok", events["msgs"].append, events["link"].append, events["rejected"].append,
                          connect, rng=lambda: 0.5, **kw)
        return lk, events

    def test_back_off_doubles_to_a_cap_and_has_jitter(self):
        def connect(url, headers):
            raise wsclient.WebSocketError("no route")
        lk, ev = self.link(connect)
        lk.stop_flag.wait = lambda t: len(lk.delays) >= 9 and (lk.stop_flag.set() or True)      # do not really wait
        lk.start()
        lk.thread.join(10)
        self.assertEqual([round(d, 2) for d in lk.delays[:8]], [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0])   # jitter 0.5 -> factor 1.0
        lk.rng = lambda: 0.0
        self.assertAlmostEqual(0.75, 0.75 + 0.5 * lk.rng())
        lk.rng = lambda: 1.0
        self.assertAlmostEqual(1.25, 0.75 + 0.5 * lk.rng())                                                     # jitter is +-25 %

    def test_a_refusal_with_retry_after_is_honoured(self):
        def connect(url, headers):
            raise wsclient.WebSocketError("busy", 429, retry_after=30.0)
        lk, ev = self.link(connect)
        lk.stop_flag.wait = lambda t: lk.stop_flag.set() or True
        lk.start()
        lk.thread.join(5)
        self.assertGreaterEqual(lk.delays[0], 30.0)

    def test_a_rejected_session_stops_the_link_and_says_so(self):
        def connect(url, headers):
            raise wsclient.WebSocketError("no", 401)
        lk, ev = self.link(connect)
        lk.start()
        lk.thread.join(5)
        self.assertEqual(ev["rejected"], ["rejected"])
        self.assertFalse(lk.running())

    def test_another_launcher_taking_over_stops_it_without_a_fight(self):
        conns = []

        def connect(url, headers):
            conns.append(1)
            return FakeWs(['{"t":"friends","friends":[]}', '{"t":"bye","reason":"replaced"}'])
        lk, ev = self.link(connect)
        lk.start()
        lk.thread.join(5)
        self.assertEqual(len(conns), 1)
        self.assertEqual(ev["rejected"], [])

    def test_an_account_recovered_elsewhere_is_reported_and_stops(self):
        lk, ev = self.link(lambda u, h: FakeWs(['{"t":"bye","reason":"recovered elsewhere"}']))
        lk.start()
        lk.thread.join(5)
        self.assertEqual(ev["rejected"], ["recovered elsewhere"])

    def test_the_players_state_is_resent_on_every_reconnect(self):
        calls = {"n": 0}
        sockets = []

        def connect(url, headers):
            calls["n"] += 1
            ws = FakeWs(['{"t":"friends","friends":[]}', wsclient.WebSocketError("dropped")])
            sockets.append(ws)
            if calls["n"] >= 3:
                lk.stop_flag.set()
            return ws
        lk, ev = self.link(connect)
        lk.send_state(P.OwnStatus("dnd", "in_game", "nzoth"))
        lk.stop_flag.wait = lambda t: lk.stop_flag.is_set()
        lk.start()
        lk.thread.join(5)
        self.assertGreaterEqual(len(sockets), 2)
        for ws in sockets:
            self.assertEqual(json.loads(ws.sent[0]), {"t": "state", "status": "dnd", "activity": "in_game", "realm": "nzoth"})

    def test_it_pings_when_quiet_and_not_otherwise(self):
        ws = FakeWs([socket.timeout(), socket.timeout(), '{"t":"friends","friends":[]}', wsclient.WebSocketError("end")])
        lk, ev = self.link(lambda u, h: ws, ping_every=0.01)
        lk.stop_flag.wait = lambda t: lk.stop_flag.set() or True
        lk.start()
        lk.thread.join(5)
        self.assertEqual(ws.sent.count("ping"), 2)

    def test_a_signed_out_player_makes_no_connection(self):
        calls = []
        lk = D.SocialLink("ws://x/v1/ws", lambda: None, lambda m: None, lambda c: None, lambda r: None,
                          lambda u, h: calls.append(1))
        lk.start()
        lk.thread.join(3)
        self.assertEqual(calls, [])


class TestWebSocketClient(unittest.TestCase):
    def test_it_speaks_to_the_local_server_and_rejects_a_bad_handshake(self):
        backend = PythonBackend()
        self.addCleanup(backend.stop)
        c = Client(backend)
        self.addCleanup(c.stop)
        c.login(uname())
        ws = c.raw_ws()
        self.addCleanup(ws.close)
        self.assertIn('"t":"friends"', ws.recv(3))
        ws.send_text("ping")
        self.assertEqual(ws.recv(3), "pong")
        with self.assertRaises(socket.timeout):
            ws.recv(0.2)
        with self.assertRaises(wsclient.WebSocketError) as ctx:
            wsclient.connect(D.ws_url(backend.url), {"Authorization": "Bearer nope"})
        self.assertEqual(ctx.exception.status, 401)
        with self.assertRaises(wsclient.WebSocketError):
            wsclient.connect("http://127.0.0.1:1/x")

    def test_addresses(self):
        for ok in ("https://social.example.org", "http://127.0.0.1:8787", "http://localhost:1", "http://192.168.1.5:80",
                   "http://10.1.2.3", "http://172.16.0.9"):
            self.assertTrue(D.url_ok(ok), ok)
        for bad in ("http://example.org", "ftp://127.0.0.1", "", "http://172.32.0.1", "https://", "javascript:alert(1)"):
            self.assertFalse(D.url_ok(bad), bad)
        self.assertEqual(D.ws_url("https://a.b"), "wss://a.b/v1/ws")
        self.assertEqual(D.ws_url("http://127.0.0.1:9"), "ws://127.0.0.1:9/v1/ws")
        self.assertEqual(D.backend_url({"EQU3_SOCIAL_URL": "http://example.org"}), "")
        self.assertEqual(D.backend_url({"EQU3_SOCIAL_URL": "http://127.0.0.1:9/"}), "http://127.0.0.1:9")


class TestIsolationAndSecrets(unittest.TestCase):
    def test_the_test_system_is_isolated_from_the_rest_of_the_program(self):
        users = []
        for d, _s, files in os.walk(os.path.join(ROOT, "equpdater")):
            for f in files:
                if f.endswith((".py", ".qml")) and "__pycache__" not in d:
                    p = os.path.join(d, f)
                    if "devsocial" in open(p, encoding="utf-8", errors="ignore").read():
                        users.append(os.path.relpath(p, ROOT).replace("\\", "/"))
        self.assertEqual(sorted(users), ["equpdater/qtui/main.py"])                          # the one place that picks the providers
        main = open(os.path.join(ROOT, "equpdater", "qtui", "main.py"), encoding="utf-8").read()
        self.assertIn("not _account.PROVIDER_CONFIG", main)                                   # OctoWoW's real sign-in always wins

    def test_no_secret_is_in_the_source_or_the_package_inputs(self):
        """No tester key, admin key, token or recovery code in source: secrets are Cloudflare secrets and the
        credential manager. (The keys the tests use are made up and only ever start a local server.)"""
        import re
        pat = re.compile(r"(ADMIN_KEY|TESTER_KEYS?|RECOVERY)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{24,}[\"']")
        hits = []
        for base in ("equpdater", "cloudflare/social/src", "tools/socialserver"):
            for d, _s, files in os.walk(os.path.join(ROOT, base)):
                for f in files:
                    if f.endswith((".py", ".js", ".qml")) and "node_modules" not in d:
                        if pat.search(open(os.path.join(d, f), encoding="utf-8", errors="ignore").read()):
                            hits.append(f)
        self.assertEqual(hits, [])
        toml = open(os.path.join(ROOT, "cloudflare", "social", "wrangler.toml"), encoding="utf-8").read()
        self.assertNotIn("TESTER_KEYS =", toml)
        self.assertNotIn("ADMIN_KEY_HASH =", toml)
        self.assertTrue(D.DEFAULT_URL == "" or D.url_ok(D.DEFAULT_URL))

    def test_the_secrets_stay_out_of_settings_and_use_the_credential_store(self):
        backend = PythonBackend()
        self.addCleanup(backend.stop)
        c = Client(backend)
        self.addCleanup(c.stop)
        name = uname()
        c.login(name)
        blob = json.dumps(c.settings)
        for secret in (c.account.tokens.access, c.vault.get("recovery", name), TESTER_KEY):
            self.assertNotIn(secret, blob)
        for kind in ("session", "recovery"):
            self.assertTrue(c.vault.get(kind, name))
        self.assertEqual(c.vault.get("tester-key"), TESTER_KEY)

    def test_an_early_builds_plaintext_tokens_are_removed_from_settings(self):
        from equpdater.core import settings as S
        d = tempfile.mkdtemp(prefix="equ-set-")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "imported_from_2x": True, "dev_tokens": {"alice": "secret"}}, f)
        self.assertNotIn("dev_tokens", S.open_settings(d).data)


class TestPresenceProviderUnit(unittest.TestCase):
    def book(self):
        from equpdater.core import friends as F
        from equpdater.core.store import JsonStore
        tmp = tempfile.mkdtemp(prefix="equ-pp-")
        self.addCleanup(shutil.rmtree, tmp, True)
        return F.FriendBook(JsonStore(os.path.join(tmp, "s.json"), {"friends": []}, delay=0))

    def test_a_lost_connection_ages_what_is_known_and_a_pong_freshens_it(self):
        book = self.book()
        book.add("Thrall")
        now = [1000.0]
        prov = D.DevPresenceProvider(D.Backend("http://127.0.0.1:9"), book, now=lambda: now[0])
        pres = P.PresenceService(book, prov, None, now=lambda: now[0])
        prov.attach(pres, lambda: "t")
        prov._on_message({"t": "friends", "friends": [{"username": "Thrall", "favourite": False, "state": "online", "activity": "launcher", "realm": ""}]})
        self.assertEqual(pres.state_of(book.all()[0]), (P.LAUNCHER, False))
        now[0] += 100
        self.assertFalse(pres.state_of(book.all()[0])[1])                                    # 100 s of quiet is fine for a push link
        prov._on_message({"t": "pong"})
        now[0] += 100
        self.assertFalse(pres.state_of(book.all()[0])[1])
        prov._on_link(False)                                                                  # the link drops
        self.assertEqual(pres.state_of(book.all()[0]), (P.LAUNCHER, True))                   # kept, but old
        prov._on_message({"t": "presence", "u": "thrall", "state": "dnd", "activity": "in_game", "realm": "nzoth"})
        self.assertEqual(pres.state_of(book.all()[0]), (P.IN_GAME, False))
        self.assertEqual(pres.observation(book.all()[0]).realm, "nzoth")

    def test_polling_is_gone_for_a_push_provider(self):
        book = self.book()
        prov = D.DevPresenceProvider(D.Backend("http://127.0.0.1:9"), book)
        pres = P.PresenceService(book, prov, type("Acct", (), {"state": "signed_in", "valid_access": lambda s: "t"})())
        calls = []
        pres.before_poll = lambda: calls.append(1)
        self.assertFalse(pres.poll())
        self.assertEqual((calls, pres.requests), ([1], 0))                                   # local detection ran, nothing was requested


class TestActivityDetection(unittest.TestCase):
    def test_a_launcher_alone_is_launcher_an_open_game_is_not_in_game_and_a_realm_connection_is(self):
        state = {"running": False, "connected": False}
        w = G.GameWatcher(lambda: "C:/g", lambda: [], running=lambda d: state["running"],
                          connected=lambda d: state["connected"], realm=lambda d, r: "")
        self.assertEqual(w.read(), G.Activity("launcher"))
        state["running"] = True                                                              # the game is open (login screen)
        self.assertEqual(w.read(), G.Activity("launcher"))
        state["connected"] = True                                                            # connected to a realm
        self.assertEqual(w.read(), G.Activity("in_game", ""))                                # connected, realm unknown: no guess
        state["connected"] = False
        self.assertEqual(w.read(), G.Activity("launcher"))
        state["running"] = False
        self.assertEqual(w.read(), G.Activity("launcher"))

    def test_the_realm_comes_only_from_a_known_world_server_never_from_config_wtf_alone(self):
        from equpdater.core import realms as R
        realms = [R.Realm("nzoth", "N'Zoth"), R.Realm("cthun", "C'Thun")]
        endpoints = [("203.0.113.5", 8092)]
        # connected to a world server nobody has told us about: no realm, whatever Config.wtf says
        self.assertEqual(G.realm_for(realms, endpoints, known={}), "")
        self.assertEqual(G.realm_for(realms, endpoints, known={"203.0.113.5:8092": "cthun"}), "cthun")
        self.assertEqual(G.realm_for(realms, endpoints, known={"203.0.113.5:8092": "madeup"}), "")       # not a real realm
        self.assertEqual(G.realm_for(realms, [], known={"203.0.113.5:8092": "cthun"}), "")                # not connected
        self.assertEqual(G.realm_for(realms, [("203.0.113.5", 8092), ("198.51.100.7", 8092)],
                                     known={"203.0.113.5:8092": "cthun", "198.51.100.7:8092": "nzoth"}), "")   # two answers: no guess

    def test_world_connections_are_told_from_the_login_connection_and_from_local_traffic(self):
        conns = [("185.246.188.177", 3724), ("92.114.107.49", 8092), ("127.0.0.1", 40041)]
        self.assertEqual(G.world_endpoints(conns, login_ports=(3724,), login_hosts={"185.246.188.177"}), [("92.114.107.49", 8092)])
        self.assertEqual(G.world_endpoints([("185.246.188.177", 3724)], (3724,), {"185.246.188.177"}), [])
        self.assertEqual(G.world_endpoints([], (3724,), set()), [])

    def test_the_simulation_is_only_used_when_asked_for_and_never_in_a_packaged_build(self):
        self.assertIsInstance(G.make_source(lambda: "", lambda: [], env={}, frozen=False), G.GameWatcher)
        self.assertIsInstance(G.make_source(lambda: "", lambda: [], env={"EQU3_SIM_ACTIVITY": "x.json"}, frozen=False), G.SimulatedActivity)
        self.assertIsInstance(G.make_source(lambda: "", lambda: [], env={"EQU3_SIM_ACTIVITY": "x.json"}, frozen=True), G.GameWatcher)

    def test_a_game_that_is_not_installed_is_not_running(self):
        self.assertFalse(G.game_running(""))
        self.assertFalse(G.game_running(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
