"""The temporary username login and the shared social test backend, end to end: a REAL server on localhost
and several independent launchers (core level, no Qt). This is the multi-launcher test environment:
nothing here is mocked at the network layer except where a test breaks the connection on purpose.

Automated tests against the reference server in tools/socialserver. They are not tests of a real OctoWoW
service (there is none) and the reference server has not been exercised across machines or over https."""

import os
import shutil
import tempfile
import unittest

from equpdater.core import account as A
from equpdater.core import devsocial as D
from equpdater.core import gamewatch as G
from equpdater.core import presence as P
from tests.social_harness import NOW, Client, Clock, Server, Settings, SimActivity
from tools.socialserver import server as S

GREEN, YELLOW, RED, GREY = "#3ddc84", "#f2c744", "#ef5350", "#7d8590"


class Env(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.server = Server(self.clock)
        self.addCleanup(self.server.stop)
        self.dirs = []

    def client(self, **kw):
        c = Client(self.server, self.clock, **kw)
        self.addCleanup(shutil.rmtree, c.tmp, True)
        return c

    def pair(self, a="Alice", b="Bobby"):
        """Two signed-in launchers that are friends of each other."""
        x, y = self.client(), self.client()
        self.assertTrue(x.login(a))
        self.assertTrue(y.login(b))
        self.assertEqual(x.svc.add_friend(b), "")
        self.assertEqual(y.svc.add_friend(a), "")
        x.sync(), y.sync()
        return x, y


class TestUsernameLogin(Env):
    def test_a_username_is_all_it_takes(self):
        c = self.client()
        self.assertTrue(c.login("Thrall"))
        self.assertEqual((c.account.state, c.account.username), (A.SIGNED_IN, "Thrall"))

    def test_bad_names_are_refused_with_a_reason(self):
        for bad in ("", "ab", "x" * 21, "bad name", "<b>", "emoji\u263a"):
            c = self.client()
            self.assertFalse(c.login(bad), bad)
            self.assertEqual(c.account.state, A.SIGNED_OUT)
            self.assertIn("username", c.account.message.lower())

    def test_names_are_unique_whatever_the_case_while_in_use(self):
        a, b = self.client(), self.client()
        self.assertTrue(a.login("Thrall"))
        self.assertFalse(b.login("thrall"))
        self.assertFalse(b.login("THRALL"))
        self.assertIn("in use", b.account.message)
        self.assertEqual(S.NAME_RE.match("Thrall") is not None, True)

    def test_the_name_keeps_the_case_it_was_claimed_with(self):
        a, b = self.client(), self.client()
        a.login("ThrallFan")
        b.login("Other")
        self.assertEqual(b.svc.add_friend("thrallfan"), "")          # any case finds the user
        b.sync()
        self.assertEqual(b.names(), ["ThrallFan"])

    def test_a_name_is_free_again_after_logging_out_or_when_the_session_lapses(self):
        a, b = self.client(), self.client()
        a.login("Thrall")
        a.sync()
        self.assertFalse(b.login("Thrall"))
        a.logout()
        self.assertTrue(b.login("thrall"))                           # free again
        self.assertEqual(b.account.username, "Thrall")
        c = self.client()
        b.sync()
        self.clock.tick(S.TTL + 1)                                   # b vanished without logging out
        self.assertTrue(c.login("THRALL"))

    def test_the_session_survives_a_restart(self):
        a = self.client()
        a.login("Thrall")
        again = self.client(settings=a.settings, store=a.cred)       # a new launcher process: same settings and credentials
        self.assertTrue(again.restore())
        self.assertEqual(again.account.username, "Thrall")
        self.assertTrue(again.account.verified)

    def test_logging_out_and_switching_accounts(self):
        a = self.client()
        a.login("Thrall")
        a.logout()
        self.assertEqual(a.account.state, A.SIGNED_OUT)
        self.assertIsNone(a.cred.get(A.SERVICE, A.ACCOUNT))
        self.assertTrue(a.login("Jaina"))
        self.assertEqual(a.account.username, "Jaina")
        a.logout()
        self.assertTrue(a.login("thrall"))                           # back to the first one: its token was remembered
        self.assertEqual(a.account.username, "Thrall")

    def test_a_restart_with_a_server_that_forgot_the_session_signs_out_cleanly(self):
        a = self.client()
        a.login("Thrall")
        fresh_server = Server(self.clock)
        self.addCleanup(fresh_server.stop)
        b = Client(fresh_server, self.clock, settings=a.settings, store=a.cred)
        self.addCleanup(shutil.rmtree, b.tmp, True)
        self.assertFalse(b.restore())
        self.assertEqual(b.account.state, A.SIGNED_OUT)

    def test_backend_unreachable_at_sign_in_says_so(self):
        a = self.client()
        self.server.stop()
        self.assertFalse(a.login("Thrall"))
        self.assertIn("could not be reached", a.account.message)
        self.server.start()
        self.assertTrue(a.login("Thrall"))


class TestFriends(Env):
    def test_add_remove_and_the_errors(self):
        a, b = self.client(), self.client()
        a.login("Alice")
        b.login("Bobby")
        self.assertEqual(a.svc.add_friend("Bobby"), "")
        self.assertIn("already", a.svc.add_friend("bobby"))
        self.assertIn("No user", a.svc.add_friend("Nobody"))             # a nonexistent username
        self.assertNotEqual(a.svc.add_friend("Alice"), "")                # yourself
        self.assertNotEqual(a.svc.add_friend("x"), "")
        a.sync()
        self.assertEqual(a.names(), ["Bobby"])
        fid = a.book.all()[0].id
        self.assertEqual(a.svc.remove_friend(fid), "")
        a.sync()
        self.assertEqual(a.names(), [])
        self.assertEqual(a.svc.add_friend("Bobby"), "")                   # and can be added again

    def test_favourites_search_and_ordering(self):
        a = self.client()
        a.login("Alice")
        others = [self.client() for _ in range(3)]
        for c, n in zip(others, ("Bobby", "Carol", "Dave")):
            c.login(n)
            a.svc.add_friend(n)
        a.sync()
        carol = next(f for f in a.book.all() if f.label == "Carol")
        self.assertEqual(a.svc.toggle_favourite(carol.id), "")
        a.sync()
        rows = [r["name"] if r["kind"] == "friend" else "#" + r["group"] for r in a.svc.rows()]
        self.assertEqual(rows[:2], ["#favourites", "Carol"])
        a.svc.search = "bo"
        self.assertEqual([r["name"] for r in a.svc.rows() if r["kind"] == "friend"], ["Bobby"])
        a.svc.search = ""
        self.assertEqual(a.svc.toggle_favourite(carol.id), "")
        self.assertNotIn("Favourites", [r["title"] for r in a.svc.rows() if r["kind"] == "header"])

    def test_the_list_follows_the_player_to_another_machine(self):
        a, b = self.client(), self.client()
        a.login("Alice")
        b.login("Bobby")
        a.svc.add_friend("Bobby")
        fid = a.book.all()[0].id if a.book.all() else None
        a.sync()
        a.svc.toggle_favourite(a.book.all()[0].id)
        a.logout()
        other_machine = self.client()
        self.assertTrue(other_machine.login("Alice"))
        other_machine.sync()
        self.assertEqual(other_machine.names(), ["Bobby"])
        self.assertTrue(other_machine.lines()["Bobby"]["favourite"])

    def test_when_signed_out_there_is_no_list_and_no_editing(self):
        a = self.client()
        a.login("Alice")
        b = self.client()
        b.login("Bobby")
        a.svc.add_friend("Bobby")
        a.sync()
        a.logout()
        self.assertEqual(a.svc.rows(), [])
        self.assertEqual(a.svc.friend_count(), 0)
        self.assertIn("Sign in", a.svc.add_friend("Bobby"))
        self.assertIsNone(a.presence.delay())                               # and nothing is requested

    def test_the_friend_cache_is_made_equal_to_the_server(self):
        a, b = self.client(), self.client()
        a.login("Alice")
        b.login("Bobby")
        a.social_store.set("friends", [{"id": "old", "name": "Thrall", "realm": "nzoth", "forum": "", "favourite": True, "added": 1}])
        a.book.__init__(a.social_store)
        a.svc.add_friend("Bobby")
        a.sync()
        self.assertEqual(a.names(), ["Bobby"])                              # the first-beta character entry is gone


class TestPresence(Env):
    def test_all_five_states_as_a_friend_sees_them(self):
        x, y = self.pair()
        seen = {}
        for status in ("online", "away", "dnd", "invisible", "online"):
            y.svc.set_status(status)
            x.sync()
            seen.setdefault(status, []).append((x.line("Bobby"), x.dot("Bobby")))
        self.assertEqual(seen["online"][0], ("Launcher open", GREEN))
        self.assertEqual(seen["away"][0], ("Launcher open", YELLOW))
        self.assertEqual(seen["dnd"][0], ("Launcher open", RED))
        self.assertEqual(seen["invisible"][0], ("Offline", GREY))             # appears offline
        self.assertEqual(seen["online"][1], ("Launcher open", GREEN))

    def test_invisible_users_still_see_everything(self):
        x, y = self.pair()
        y.svc.set_status("invisible")
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Offline")
        view = y.sync()
        self.assertEqual(view["Alice"]["statusLine"], "Launcher open")          # y keeps full social functionality
        self.assertIn("No user", y.svc.add_friend("Carol"))                       # editing works while invisible
        self.assertEqual(y.account.state, A.SIGNED_IN)
        self.assertEqual(y.svc.status, "invisible")

    def test_status_changes_are_pushed_at_once_not_after_the_next_heartbeat(self):
        x, y = self.pair()
        y.svc.set_status("dnd")                                                 # publish() is immediate
        self.clock.tick(1.0)
        x.presence.poll()
        self.assertEqual(x.dot("Bobby"), RED)

    def test_automatic_away_after_fifteen_minutes_without_overriding_dnd_or_invisible(self):
        x, y = self.pair()
        self.clock.tick(15 * 60 + 1)
        y.svc.check_idle()
        self.assertEqual(x.sync()["Bobby"]["dot"].lower(), YELLOW)
        y.svc.note_activity()
        y.svc.check_idle()
        self.assertEqual(x.sync()["Bobby"]["dot"].lower(), GREEN)
        for manual, expect in (("dnd", RED), ("invisible", GREY)):
            y.svc.set_status(manual)
            y.svc.last_activity -= 15 * 60 + 1                         # fifteen quiet minutes (the launcher kept its heartbeat going)
            y.svc.check_idle()
            x.sync()
            self.assertEqual(x.dot("Bobby"), expect, manual)
            y.svc.note_activity()

    def test_launcher_open_versus_in_game_and_the_realm(self):
        x = self.client()
        sim = SimActivity(tempfile.mkdtemp(prefix="equ-sim-"))
        self.addCleanup(shutil.rmtree, os.path.dirname(sim.path), True)
        y = self.client(activity=sim)
        x.login("Alice")
        y.login("Bobby")
        x.svc.add_friend("Bobby")
        y.svc.add_friend("Alice")
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Launcher open")
        for realm, name in (("nzoth", "N'Zoth"), ("yshaarj", "Y'Shaarj"), ("cthun", "C'Thun")):
            sim.set("in_game", realm)
            y.sync()
            self.assertEqual(x.sync()["Bobby"]["statusLine"], f"In-game \u00b7 {name}", realm)    # realm switching
        sim.set("in_game", "")                                                  # in game, realm not known
        y.sync()
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "In-game")
        sim.set("launcher")                                                     # the game closes, the launcher stays
        y.sync()
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Launcher open")

    def test_invisible_hides_the_game_and_the_realm_too(self):
        x = self.client()
        sim = SimActivity(tempfile.mkdtemp(prefix="equ-sim-"))
        self.addCleanup(shutil.rmtree, os.path.dirname(sim.path), True)
        y = self.client(activity=sim)
        x.login("Alice")
        y.login("Bobby")
        x.svc.add_friend("Bobby")
        sim.set("in_game", "nzoth")
        y.svc.set_status("invisible")
        y.sync()
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Offline")
        self.assertEqual(y.provider.own.realm, "")                                # nothing about the game is even sent

    def test_closing_the_launcher_is_offline_at_once_and_a_crash_after_the_heartbeat_expires(self):
        x, y = self.pair()
        y.logout()                                                               # a clean close
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Offline")
        self.assertTrue(y.login("Bobby"))
        y.sync()
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Launcher open")
        # a crash: Bobby never syncs again
        for _ in range(5):
            self.clock.tick(5)
            x.presence.poll()
        self.assertEqual(x.line("Bobby"), "Launcher open")                       # 25 s: still inside the lease
        self.clock.tick(10)
        x.presence.poll()
        self.assertEqual(x.line("Bobby"), "Offline")                              # lapsed: not online for ever

    def test_a_stale_session_never_shows_as_online_indefinitely(self):
        x, y = self.pair()
        self.clock.tick(S.TTL + 5)
        self.assertEqual(x.sync(advance=0)["Bobby"]["statusLine"], "Offline")

    def test_reconnection(self):
        x, y = self.pair()
        self.clock.tick(S.TTL + 5)
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Offline")
        y.sync()                                                                   # the same launcher comes back
        self.assertEqual(x.sync()["Bobby"]["statusLine"], "Launcher open")

    def test_backend_unavailable_keeps_the_last_data_backs_off_and_recovers(self):
        x, y = self.pair()
        self.server.stop()
        x.sync()
        self.assertEqual(x.presence.failures, 1)
        self.assertEqual(x.line("Bobby"), "Launcher open")                         # the last known value stays
        self.assertIn("could not be reached", x.svc.add_friend("Carol"))            # editing says why it failed
        d1 = x.presence.delay()
        x.clock.tick(d1)
        x.presence.poll()
        self.assertGreater(x.presence.delay(), d1 - 0.5)                            # exponential back-off
        self.assertEqual(x.presence.failures, 2)
        self.server.start()
        self.clock.tick(x.presence.delay())
        y.sync()
        x.presence.poll()
        self.assertEqual((x.presence.failures, x.line("Bobby")), (0, "Launcher open"))

    def test_the_heartbeat_is_one_request_and_a_304_when_nothing_changed(self):
        x, y = self.pair()
        before = len(x.http.statuses("/sync"))
        for _ in range(3):
            x.sync(advance=5)
            y.sync(advance=0)
        statuses = x.http.statuses("/sync")[before:]
        self.assertEqual(len(statuses), 3)                                          # one request per round, not per friend
        self.assertEqual(statuses[1:], [304, 304])                                   # nothing changed: nothing sent back
        y.svc.set_status("away")
        x.sync(advance=5)
        self.assertEqual(x.http.statuses("/sync")[-1], 200)                          # a change comes through
        self.assertEqual(x.dot("Bobby"), YELLOW)

    def test_a_304_still_counts_as_fresh_so_a_quiet_friend_is_not_marked_old(self):
        x, y = self.pair()
        for _ in range(20):
            x.sync(advance=5)
            y.sync(advance=0)
        self.assertFalse(x.lines()["Bobby"]["stale"])

    def test_the_server_slows_a_client_that_asks_too_fast(self):
        x, y = self.pair()
        x.clock.tick(2)
        x.presence.poll()
        x.presence.poll()                                                           # no time passed
        self.assertTrue(x.presence.rate_limited)
        self.assertEqual(x.presence.failures, 0)                                    # not counted as a failure
        self.assertGreaterEqual(x.presence.delay(), 1)

    def test_nothing_is_asked_when_signed_out(self):
        a = self.client()
        self.assertIsNone(a.presence.delay())
        self.assertFalse(a.presence.poll())
        self.assertEqual(a.http.log, [])

    def test_the_interval_is_five_seconds_and_not_a_setting(self):
        x, y = self.pair()
        x.presence.poll()
        self.assertAlmostEqual(x.presence.delay(), 5.0, delta=1.0)
        self.assertFalse(hasattr(x.presence, "set_interval"))


class TestProtocolAndSafety(Env):
    def call(self, method, path, body=None, token=""):
        return D.default_http(method, self.server.url + path, body, {"Authorization": "Bearer " + token} if token else {})

    def test_every_call_but_login_needs_a_token(self):
        for method, path in (("GET", "/v1/me"), ("POST", "/v1/sync"), ("POST", "/v1/friends"), ("DELETE", "/v1/friends/x")):
            self.assertEqual(self.call(method, path, {} if method != "GET" else None)[0], 401, path)
        self.assertEqual(self.call("GET", "/v1/me", token="made-up")[0], 401)

    def test_bad_input_is_refused_not_crashed_on(self):
        status, obj, _ = self.call("POST", "/v1/login", {"username": "<script>"})
        self.assertEqual(status, 400)
        status, obj, _ = self.call("POST", "/v1/login", {"username": ["a"]})
        self.assertEqual(status, 400)
        tok = self.call("POST", "/v1/login", {"username": "Alice"})[1]["token"]
        for body in ({"status": "root", "activity": "x", "realm": "<>"}, {"status": 5}, {}):
            self.clock.tick(2)
            status, obj, _ = self.call("POST", "/v1/sync", body, tok)
            self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", "/v1/friends", {"username": 5}, tok)[0], 400)
        self.assertEqual(self.call("GET", "/v1/nothing", None, tok)[0], 404)

    def test_an_unknown_realm_or_a_realm_outside_the_game_is_dropped(self):
        a = self.call("POST", "/v1/login", {"username": "Alice"})[1]["token"]
        b = self.call("POST", "/v1/login", {"username": "Bobby"})[1]["token"]
        self.call("POST", "/v1/friends", {"username": "Bobby"}, a)
        self.call("POST", "/v1/sync", {"status": "online", "activity": "in_game", "realm": "madeup"}, b)
        row = self.call("POST", "/v1/sync", {}, a)[1]["friends"][0]
        self.assertEqual((row["activity"], row["realm"]), ("in_game", ""))
        self.clock.tick(2)
        self.call("POST", "/v1/sync", {"status": "online", "activity": "launcher", "realm": "nzoth"}, b)
        self.clock.tick(2)
        row = self.call("POST", "/v1/sync", {}, a)[1]["friends"][0]
        self.assertEqual((row["activity"], row["realm"]), ("launcher", ""))

    def test_the_server_never_returns_more_than_a_name_a_favourite_and_a_presence(self):
        x, y = self.pair()
        row = self.call("POST", "/v1/sync", {}, x.account.tokens.access)[1]["friends"][0]
        self.assertEqual(set(row), {"username", "favourite", "state", "activity", "realm"})

    def test_the_address_rules(self):
        for ok in ("https://social.example.org", "http://127.0.0.1:8787", "http://localhost:1", "http://192.168.1.5:80",
                   "http://10.1.2.3", "http://172.16.0.9"):
            self.assertTrue(D.url_ok(ok), ok)
        for bad in ("http://example.org", "ftp://127.0.0.1", "", "http://172.32.0.1", "https://", "javascript:alert(1)"):
            self.assertFalse(D.url_ok(bad), bad)
        self.assertEqual(D.backend_url({"EQU3_SOCIAL_URL": "http://example.org"}), "")
        self.assertEqual(D.backend_url({"EQU3_SOCIAL_URL": "http://127.0.0.1:9/"}), "http://127.0.0.1:9")
        self.assertEqual(D.backend_url({}), D.DEFAULT_URL if D.url_ok(D.DEFAULT_URL) else "")

    def test_the_test_system_is_isolated_from_the_rest_of_the_program(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        users = []
        for d, _s, files in os.walk(os.path.join(root, "equpdater")):
            for f in files:
                if f.endswith((".py", ".qml")) and "__pycache__" not in d:
                    p = os.path.join(d, f)
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                    if "devsocial" in text:
                        users.append(os.path.relpath(p, root).replace("\\", "/"))
        self.assertEqual(sorted(users), ["equpdater/qtui/main.py"])          # the one place that picks the providers
        with open(os.path.join(root, "equpdater", "qtui", "main.py"), encoding="utf-8") as f:
            main = f.read()
        self.assertIn("not _account.PROVIDER_CONFIG", main)                           # OctoWoW's real sign-in always wins


class TestActivityDetection(unittest.TestCase):
    def test_a_launcher_alone_is_launcher_and_a_running_game_is_in_game(self):
        clock = Clock()
        state = {"running": False, "cfg": ("", 0.0)}
        w = G.GameWatcher(lambda: "C:/g", lambda: [], running=lambda d: state["running"], config=lambda d, r: state["cfg"], now=clock)
        self.assertEqual(w.read(), G.Activity("launcher"))
        state["running"] = True
        self.assertEqual(w.read(), G.Activity("in_game", ""))
        state["running"] = False
        self.assertEqual(w.read(), G.Activity("launcher"))                            # the game closed, the launcher stays

    def test_the_realm_counts_only_if_the_game_wrote_it_this_run(self):
        clock = Clock()
        state = {"running": True, "cfg": ("nzoth", NOW - 3600)}                       # written an hour ago: a previous session
        w = G.GameWatcher(lambda: "C:/g", lambda: [], running=lambda d: state["running"], config=lambda d, r: state["cfg"], now=clock)
        self.assertEqual(w.read(), G.Activity("in_game", ""))                        # not inferred from an old file
        clock.tick(10)
        state["cfg"] = ("yshaarj", clock.t)                                           # the client wrote it now
        self.assertEqual(w.read(), G.Activity("in_game", "yshaarj"))
        state["running"] = False
        w.read()
        state["running"] = True
        state["cfg"] = ("cthun", NOW)                                                 # a new run: the old write does not count
        clock.tick(60)
        self.assertEqual(w.read(), G.Activity("in_game", ""))

    def test_config_wtf_is_read_and_matched_to_a_real_realm(self):
        d = tempfile.mkdtemp(prefix="equ-wtf-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "WTF"))
        with open(os.path.join(d, "WTF", "Config.wtf"), "w", encoding="utf-8") as f:
            f.write('SET realmList "play.octowow.st"\nSET realmName "N\'Zoth"\n')
        from equpdater.core import realms as R
        realm, mtime = G.realm_from_config(d, [R.Realm("nzoth", "N'Zoth"), R.Realm("cthun", "C'Thun")])
        self.assertEqual(realm, "nzoth")
        self.assertGreater(mtime, 0)
        with open(os.path.join(d, "WTF", "Config.wtf"), "w", encoding="utf-8") as f:
            f.write('SET realmName "Some Other Server"\n')
        self.assertEqual(G.realm_from_config(d, [R.Realm("nzoth", "N'Zoth")])[0], "")

    def test_the_simulation_is_only_used_when_asked_for_and_never_in_a_packaged_build(self):
        real = G.make_source(lambda: "", lambda: [], env={}, frozen=False)
        self.assertIsInstance(real, G.GameWatcher)
        sim = G.make_source(lambda: "", lambda: [], env={"EQU3_SIM_ACTIVITY": "x.json"}, frozen=False)
        self.assertIsInstance(sim, G.SimulatedActivity)
        packaged = G.make_source(lambda: "", lambda: [], env={"EQU3_SIM_ACTIVITY": "x.json"}, frozen=True)
        self.assertIsInstance(packaged, G.GameWatcher)

    def test_a_game_that_is_not_installed_is_not_running(self):
        self.assertFalse(G.game_running(""))
        self.assertFalse(G.game_running(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()
