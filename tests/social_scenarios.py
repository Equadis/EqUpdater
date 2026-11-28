"""The scenarios every social test backend must pass. A subclass supplies ``backend`` (see social_harness):
the local Python twin (tests/test_v3_social_backend.py), the Worker under workerd (tests/test_v3_social_worker.py)
and, for a smaller smoke set, the deployed Worker (tests/test_v3_social_live.py).

Real sockets, real threads, real time: presence arrives by push, and the tests wait for it instead of polling.
"""

import itertools
import os
import shutil
import tempfile
import time
import unittest

from equpdater.core import account as A
from equpdater.core import devsocial as D
from tests.social_harness import SimActivity, Client, wait_for

GREEN, YELLOW, RED, GREY = "#3ddc84", "#f2c744", "#ef5350", "#7d8590"
_ids = itertools.count(int(time.time()) % 100000)


def uname(stem="u"):
    """A fresh valid username (the hosted backends keep names for good)."""
    return f"{stem}{next(_ids):x}"[:20]


class Scenarios:
    backend = None                    # set by the subclass

    def make(self, **kw):
        c = Client(self.backend, **kw)
        self.addCleanup(c.stop)
        return c

    def pair(self):
        """Two signed-in launchers that are friends of each other."""
        a, b = self.make(), self.make()
        na, nb = uname("al"), uname("bo")
        self.assertTrue(a.login(na))
        self.assertTrue(b.login(nb))
        self.assertEqual(a.svc.add_friend(nb), "")
        self.assertEqual(b.svc.add_friend(na), "")
        self.assertTrue(wait_for(lambda: a.names() == [nb] and b.names() == [na], 6))
        self.assertTrue(a.sees(nb, "Launcher open") and b.sees(na, "Launcher open"))
        return a, b, na, nb


class AccountScenarios(Scenarios):
    def test_registering_a_username_makes_a_real_account(self):
        c = self.make()
        name = uname()
        self.assertTrue(c.login(name))
        self.assertEqual((c.account.state, c.account.username), (A.SIGNED_IN, name))
        self.assertTrue(c.link_up())
        self.assertTrue(c.vault.get("session", name) and c.vault.get("recovery", name))       # the secrets are in the credential store
        self.assertNotIn(c.account.tokens.access, repr(c.settings))                           # ... and not in the settings

    def test_bad_names_are_refused_with_a_reason(self):
        for bad in ("", "ab", "x" * 21, "bad name", "<b>", "emoji☺"):
            c = self.make()
            self.assertFalse(c.login(bad), bad)
            self.assertEqual(c.account.state, A.SIGNED_OUT)
            self.assertIn("username", c.account.message.lower())

    def test_the_tester_key_is_required_and_a_wrong_one_is_refused(self):
        if not self.backend.key:
            self.skipTest("this backend is open")
        c = self.make()
        self.assertTrue(c.auth.requires_key())
        self.assertFalse(c.login(uname(), key="not-the-key"))
        self.assertIn("tester key", c.account.message)
        self.assertFalse(c.login(uname(), key=""))
        self.assertTrue(c.login(uname()))                                                    # the right key (the default)
        self.assertEqual(c.vault.get("tester-key"), self.backend.key)                       # remembered, in the credential store
        self.assertFalse(c.auth.requires_key())

    def test_a_username_belongs_to_its_owner_even_when_the_owner_is_offline(self):
        owner, thief = self.make(), self.make()
        name = uname()
        owner.login(name)
        owner.logout()                                                                       # offline, and signed out
        for variant in (name, name.upper(), name.lower()):
            self.assertFalse(thief.login(variant), variant)
            self.assertIn("already registered", thief.account.message)
        self.assertFalse(thief.login(name, recovery="guess-guess-guess"))
        self.assertIn("recovery", thief.account.message.lower())
        self.assertEqual(thief.account.state, A.SIGNED_OUT)

    def test_a_stolen_username_cannot_be_used_with_another_accounts_credentials(self):
        a, b = self.make(), self.make()
        na, nb = uname("al"), uname("bo")
        a.login(na)
        b.login(nb)
        # B's token does not act as A, and A's recovery code does not open B
        status, _o, _h = a.be.call("GET", "/v1/me", token=b.account.tokens.access)
        self.assertEqual(status, 200)
        self.assertEqual(a.be.call("GET", "/v1/me", token=b.account.tokens.access)[1]["username"], nb)
        victim_code = a.vault.get("recovery", na)
        c = self.make()
        self.assertFalse(c.login(nb, recovery=victim_code))
        self.assertEqual(c.account.state, A.SIGNED_OUT)

    def test_recovery_attempts_are_limited(self):
        c = self.make()
        name = uname()
        c.login(name)
        c.logout()
        codes = []
        for i in range(10):
            d = self.make()
            d.login(name, recovery=f"wrong-{i}-{i}-{i}-{i}")
            codes.append(d.account.message)
        self.assertTrue(any("Too many" in m for m in codes), codes)                          # locked out after a few wrong guesses

    def test_the_session_survives_a_restart(self):
        a = self.make()
        name = uname()
        a.login(name)
        again = self.make(cred=a.cred, settings=a.settings)                                   # a new launcher process, same machine
        self.assertTrue(again.restore())
        self.assertEqual(again.account.username, name)
        self.assertTrue(again.account.verified)
        self.assertTrue(wait_for(again.link_up, 6))

    def test_signing_out_and_switching_accounts_on_one_machine(self):
        a = self.make()
        n1, n2 = uname("one"), uname("two")
        a.login(n1)
        a.logout()
        self.assertEqual(a.account.state, A.SIGNED_OUT)
        self.assertIsNone(a.cred.get(A.SERVICE, a.account.key))                              # the session is gone from the store
        self.assertTrue(a.login(n2, key=""))                                                  # the tester key was remembered
        self.assertEqual(a.account.username, n2)
        a.logout()
        self.assertTrue(a.login(n1.upper()))                                                  # back: the saved recovery code is used
        self.assertEqual(a.account.username, n1)

    def test_moving_an_account_to_another_computer_with_the_recovery_code(self):
        old, new = self.make(), self.make()
        name = uname()
        old.login(name)
        code = old.vault.get("recovery", name)
        self.assertTrue(new.login(name, recovery=code))
        self.assertEqual(new.account.username, name)
        self.assertTrue(wait_for(new.link_up, 6))
        self.assertTrue(wait_for(lambda: old.account.state == A.SIGNED_OUT, 6))              # the old computer is signed out
        self.assertTrue(old.rejected and "another computer" in old.rejected[0])

    def test_two_launchers_on_one_account_do_not_fight(self):
        a = self.make()
        name = uname()
        a.login(name)
        second = self.make(cred=a.cred, settings=a.settings)
        self.assertTrue(second.restore())
        self.assertTrue(wait_for(second.link_up, 6))
        self.assertTrue(wait_for(lambda: not a.link_up() or a.provider.link is None or not a.provider.link.running(), 6))
        time.sleep(1.5)
        self.assertFalse(a.link_up())                                                         # the first gave way and stays away
        self.assertTrue(second.link_up())


class FriendScenarios(Scenarios):
    def test_add_remove_and_the_errors(self):
        a, b = self.make(), self.make()
        na, nb = uname("al"), uname("bo")
        a.login(na)
        b.login(nb)
        self.assertEqual(a.svc.add_friend(nb), "")
        self.assertIn("already", a.svc.add_friend(nb.upper()))
        self.assertIn("No user", a.svc.add_friend(uname("nobody")))
        self.assertNotEqual(a.svc.add_friend(na), "")
        self.assertNotEqual(a.svc.add_friend("x"), "")
        self.assertTrue(wait_for(lambda: a.names() == [nb], 6))
        self.assertEqual(a.svc.remove_friend(a.book.all()[0].id), "")
        self.assertTrue(wait_for(lambda: a.names() == [], 6))
        self.assertEqual(a.svc.add_friend(nb), "")

    def test_favourites_search_and_ordering(self):
        a = self.make()
        a.login(uname("al"))
        friends = []
        for _ in range(3):
            c = self.make()
            n = uname("fr")
            c.login(n)
            friends.append(n)
            self.assertEqual(a.svc.add_friend(n), "")
        self.assertTrue(wait_for(lambda: len(a.names()) == 3, 6))
        target = a.book.all()[1]
        self.assertEqual(a.svc.toggle_favourite(target.id), "")
        self.assertTrue(wait_for(lambda: a.rows()[target.label]["favourite"], 6))
        rows = [r["name"] if r["kind"] == "friend" else "#" + r["group"] for r in a.svc.rows()]
        self.assertEqual(rows[:2], ["#favourites", target.label])
        a.svc.search = target.label[:4].lower()
        self.assertIn(target.label, [r["name"] for r in a.svc.rows() if r["kind"] == "friend"])
        a.svc.search = ""
        self.assertEqual(a.svc.toggle_favourite(target.id), "")
        self.assertTrue(wait_for(lambda: not a.rows()[target.label]["favourite"], 6))

    def test_the_list_follows_the_player_to_another_machine(self):
        a, b = self.make(), self.make()
        na, nb = uname("al"), uname("bo")
        a.login(na)
        b.login(nb)
        a.svc.add_friend(nb)
        self.assertTrue(wait_for(lambda: a.names() == [nb], 6))
        a.svc.toggle_favourite(a.book.all()[0].id)
        code = a.vault.get("recovery", na)
        other = self.make()
        self.assertTrue(other.login(na, recovery=code))
        self.assertTrue(wait_for(lambda: other.names() == [nb] and other.rows()[nb]["favourite"], 6))

    def test_nothing_is_shown_or_editable_when_signed_out(self):
        a, b = self.pair()[:2]
        a.logout()
        self.assertEqual(a.svc.rows(), [])
        self.assertIn("Sign in", a.svc.add_friend("someone"))


class PresenceScenarios(Scenarios):
    def test_presence_is_pushed_not_polled(self):
        a, b, na, nb = self.pair()
        before = len(a.http.log) + len(b.http.log)
        for status in ("away", "dnd", "online"):
            b.svc.set_status(status)
            self.assertTrue(wait_for(lambda s=status: a.dot(nb) == {"away": YELLOW, "dnd": RED, "online": GREEN}[s], 3), status)
        time.sleep(2.5)
        self.assertEqual(len(a.http.log) + len(b.http.log), before)                           # no HTTP request at all in between

    def test_all_five_states_as_a_friend_sees_them(self):
        a, b, na, nb = self.pair()
        expect = {"online": ("Launcher open", GREEN), "away": ("Launcher open", YELLOW), "dnd": ("Launcher open", RED),
                  "invisible": ("Offline", GREY)}
        for status, (line, dot) in expect.items():
            b.svc.set_status(status)
            self.assertTrue(wait_for(lambda l=line, d=dot: (a.line(nb), a.dot(nb)) == (l, d), 3), status)
        b.logout()
        self.assertTrue(a.sees(nb, "Offline", 4))                                              # the fifth: signed out

    def test_a_status_change_reaches_a_friend_within_a_second(self):
        a, b, na, nb = self.pair()
        t0 = time.time()
        b.svc.set_status("dnd")
        self.assertTrue(wait_for(lambda: a.dot(nb) == RED, 3))
        self.assertLess(time.time() - t0, 1.5)

    def test_only_friends_are_told(self):
        a, b, na, nb = self.pair()
        stranger = self.make()
        stranger.login(uname("st"))
        seen_before = len(stranger.messages)
        b.svc.set_status("away")
        self.assertTrue(wait_for(lambda: a.dot(nb) == YELLOW, 3))
        time.sleep(0.5)
        self.assertEqual([m for m in stranger.messages[seen_before:] if m.get("t") == "presence"], [])
        # a friend you added but who did not add you: you see them, they do not see you
        c = self.make()
        nc = uname("one")
        c.login(nc)
        a.svc.add_friend(nc)
        self.assertTrue(a.sees(nc, "Launcher open", 4))
        self.assertEqual(c.names(), [])

    def test_invisible_users_appear_offline_and_leak_nothing_but_still_see_everything(self):
        a = self.make()
        sim = SimActivity(tempfile.mkdtemp(prefix="equ-sim-"))
        self.addCleanup(shutil.rmtree, os.path.dirname(sim.path), True)
        b = self.make(activity=sim)
        na, nb = uname("al"), uname("bo")
        a.login(na)
        b.login(nb)
        a.svc.add_friend(nb)
        b.svc.add_friend(na)
        sim.set("in_game", "nzoth")
        b.svc.refresh_activity()
        self.assertTrue(a.sees(nb, "In-game · N'Zoth", 4))
        mark = len(a.messages)
        b.svc.set_status("invisible")
        self.assertTrue(a.sees(nb, "Offline", 3))
        b.svc.refresh_activity()
        sim.set("in_game", "cthun")                                                            # keeps playing, on another realm
        b.svc.refresh_activity()
        time.sleep(0.8)
        later = " ".join(str(m) for m in a.messages[mark:])
        self.assertNotIn("cthun", later)                                                       # the realm never reached the friend
        self.assertNotIn("in_game", later)
        self.assertEqual(a.line(nb), "Offline")
        self.assertEqual(b.provider.own.realm, "")                                             # not even sent from this side
        self.assertTrue(b.sees(na, "Launcher open", 3))                                        # and b still sees friends
        self.assertEqual(b.svc.add_friend(uname("zz")) != "", True)                           # editing still works
        self.assertEqual(b.account.state, A.SIGNED_IN)
        b.svc.set_status("online")
        self.assertTrue(a.sees(nb, "In-game · C'Thun", 3))                                # visible again, with the realm

    def test_automatic_away_after_fifteen_minutes_without_overriding_dnd_or_invisible(self):
        a, b, na, nb = self.pair()
        b.svc.last_activity -= 15 * 60 + 1
        b.svc.check_idle()
        self.assertTrue(wait_for(lambda: a.dot(nb) == YELLOW, 3))
        b.svc.note_activity()
        b.svc.check_idle()
        self.assertTrue(wait_for(lambda: a.dot(nb) == GREEN, 3))
        for manual, dot in (("dnd", RED), ("invisible", GREY)):
            b.svc.set_status(manual)
            b.svc.last_activity -= 15 * 60 + 1
            b.svc.check_idle()
            self.assertTrue(wait_for(lambda d=dot: a.dot(nb) == d, 3), manual)
            time.sleep(0.3)
            self.assertEqual(a.dot(nb), dot)
            b.svc.note_activity()

    def test_launcher_open_versus_in_game_realm_switching_and_the_game_closing(self):
        a = self.make()
        sim = SimActivity(tempfile.mkdtemp(prefix="equ-sim-"))
        self.addCleanup(shutil.rmtree, os.path.dirname(sim.path), True)
        b = self.make(activity=sim)
        na, nb = uname("al"), uname("bo")
        a.login(na)
        b.login(nb)
        a.svc.add_friend(nb)
        self.assertTrue(a.sees(nb, "Launcher open", 4))
        for realm, label in (("nzoth", "N'Zoth"), ("yshaarj", "Y'Shaarj"), ("cthun", "C'Thun")):
            sim.set("in_game", realm)
            b.svc.refresh_activity()
            self.assertTrue(a.sees(nb, f"In-game · {label}", 3), realm)
        sim.set("in_game", "")                                                                  # connected, realm not known: no guess
        b.svc.refresh_activity()
        self.assertTrue(a.sees(nb, "In-game", 3))
        sim.set("launcher")                                                                     # the game closes, the launcher stays
        b.svc.refresh_activity()
        self.assertTrue(a.sees(nb, "Launcher open", 3))
        self.assertTrue(b.link_up())

    def test_closing_the_launcher_is_offline_at_once(self):
        a, b, na, nb = self.pair()
        b.provider.ensure_link(False)                                                           # the launcher closes cleanly
        self.assertTrue(a.sees(nb, "Offline", 3))

    def test_an_unexpected_disconnection_is_noticed_and_the_launcher_reconnects_and_restores_everything(self):
        a, b, na, nb = self.pair()
        b.svc.set_status("dnd")
        self.assertTrue(wait_for(lambda: a.dot(nb) == RED, 3))
        link = b.provider.link
        link.ws.sock.close()                                                                    # the network drops, no goodbye
        self.assertTrue(a.sees(nb, "Offline", 5))
        self.assertTrue(wait_for(lambda: b.link_up() and b.provider.link is link, 12))          # back by itself, same link object
        self.assertTrue(wait_for(lambda: a.dot(nb) == RED, 4))                                  # and its state was restored
        self.assertEqual(b.names(), [na])                                                       # and its friends
        self.assertEqual(b.line(na), "Launcher open")

    def test_a_silent_connection_expires(self):
        a, b, na, nb = self.pair()
        mute = self.make()
        nm = uname("mu")
        mute.auth.username, mute.auth.key = nm, self.backend.key
        self.assertTrue(mute.account.sign_in(lambda url: None))
        ws = mute.raw_ws()                                                                      # connected, but it never says a word
        self.addCleanup(ws.close)
        self.assertEqual(a.svc.add_friend(nm), "")
        self.assertTrue(a.sees(nm, "Launcher open", 4))
        self.assertTrue(a.sees(nm, "Offline", 15), "a connection that stops pinging must expire")

    def test_the_server_pushes_the_friends_list_on_every_connection(self):
        a, b, na, nb = self.pair()
        lists = [m for m in b.messages if m.get("t") == "friends"]
        self.assertTrue(lists)                                                                  # one on connecting ...
        self.assertTrue(any(f["username"] == na for m in lists for f in m["friends"]))         # ... and one when the friend was added

    def test_reconnecting_too_fast_is_slowed_down(self):
        a = self.make()
        a.login(uname())
        refused = 0
        for _ in range(60):
            try:
                ws = a.raw_ws()
                ws.close()
            except Exception as e:
                refused += 1 if getattr(e, "status", 0) in (429, 503) else 0
                if refused:
                    break
        self.assertGreater(refused, 0)

    def test_a_flood_of_messages_closes_the_connection(self):
        a = self.make()
        a.login(uname())
        ws = a.raw_ws()
        try:
            try:
                for _ in range(80):
                    ws.send_text('{"t":"sync"}')
            except OSError:
                pass                                                                            # cut off in the middle: that is the point
            closed = False
            end = time.time() + 5
            while time.time() < end:
                try:
                    if ws.recv(1.0) is None:
                        closed = True
                        break
                except Exception:
                    closed = True
                    break
            self.assertTrue(closed)
        finally:
            ws.close()


class MetricsScenarios(Scenarios):
    def test_metrics_are_private_and_aggregate(self):
        if not self.backend.admin:
            self.skipTest("no admin key for this backend")
        a, b, na, nb = self.pair()
        status, obj, _h = a.be.call("GET", "/v1/admin/metrics", token="not-the-admin-key")
        self.assertEqual(status, 401)
        status, obj, _h = a.be.call("GET", "/v1/admin/metrics", token=a.account.tokens.access)
        self.assertEqual(status, 401)                                                           # an ordinary session is not admin
        status, obj, _h = a.be.call("GET", "/v1/admin/metrics", token=self.backend.admin)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(obj["accounts"], 2)
        self.assertGreaterEqual(obj["daily_active"], 2)
        self.assertGreaterEqual(obj["connected"], 2)
        text = str(obj)
        for secret in (na, nb, a.account.tokens.access):
            self.assertNotIn(secret, text)                                                      # no names, no tokens, no histories

    def test_the_protocol_refuses_nonsense_without_crashing(self):
        a = self.make()
        a.login(uname())
        tok = a.account.tokens.access
        be = a.be
        self.assertEqual(be.call("POST", "/v1/register", {"username": ["x"]})[0], 400)
        self.assertEqual(be.call("POST", "/v1/friends", {"username": 5}, tok)[0], 400)
        self.assertEqual(be.call("GET", "/v1/nothing", None, tok)[0], 404)
        self.assertEqual(be.call("GET", "/v1/me", token="made-up")[0], 401)
        self.assertIn(be.call("GET", "/v1/ws", None, tok)[0], (400, 426))                      # not a WebSocket request
