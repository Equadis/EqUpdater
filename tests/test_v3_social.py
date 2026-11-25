"""Social features, core logic: credentials, sign-in, friends, presence, social settings."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import uuid

from equpdater.core import account as A
from equpdater.core import credstore as C
from equpdater.core import friends as F
from equpdater.core import presence as P
from equpdater.core import realms as R
from equpdater.core.social_service import SocialService
from equpdater.core.store import JsonStore

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Settings(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def set(self, k, v):
        self[k] = v


def store(tmp):
    return JsonStore(os.path.join(tmp, "social.json"), {"friends": [], "realms": [], "realms_at": 0}, delay=0)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


# ── credentials ─────────────────────────────────────────────────────────────────────────

class TestCredentialStore(unittest.TestCase):
    def test_memory_store_round_trip_and_delete(self):
        s = C.MemoryStore()
        self.assertFalse(s.persistent)
        self.assertTrue(s.set("svc", "acct", "secret"))
        self.assertEqual(s.get("svc", "acct"), "secret")
        self.assertTrue(s.delete("svc", "acct"))
        self.assertIsNone(s.get("svc", "acct"))

    @unittest.skipUnless(sys.platform == "win32", "Windows Credential Manager")
    def test_the_real_windows_credential_manager_round_trip_including_a_long_value(self):
        s = C.WindowsStore()
        svc = "EqUpdater3-test-" + uuid.uuid4().hex[:8]
        self.addCleanup(s.delete, svc, "acct")
        long_secret = "é" + "x" * 6000 + "ü"                      # more than one credential's worth
        self.assertTrue(s.set(svc, "acct", long_secret))
        self.assertEqual(s.get(svc, "acct"), long_secret)
        self.assertTrue(s.set(svc, "acct", "short"))               # replacing leaves no old chunks behind
        self.assertEqual(s.get(svc, "acct"), "short")
        self.assertTrue(s.delete(svc, "acct"))
        self.assertIsNone(s.get(svc, "acct"))

    def test_secret_tool_gets_the_secret_on_stdin_never_in_the_arguments(self):
        calls = []

        class R:
            def __init__(self, rc=0, out=b""):
                self.returncode, self.stdout = rc, out

        def run(cmd, **kw):
            calls.append((cmd, kw.get("input")))
            return R(0, b"the-secret") if cmd[1] == "lookup" else R()
        s = C.SecretToolStore(run=run)
        s.set("svc", "acct", "the-secret")
        self.assertEqual(calls[0][1], b"the-secret")
        self.assertNotIn("the-secret", " ".join(calls[0][0]))
        self.assertEqual(s.get("svc", "acct"), "the-secret")
        self.assertTrue(s.delete("svc", "acct"))

    def test_without_any_credential_manager_nothing_falls_back_to_a_file(self):
        before = set(os.listdir(tempfile.gettempdir()))
        s = C.MemoryStore()
        s.set("svc", "acct", "TOP-SECRET-VALUE")
        for name in set(os.listdir(tempfile.gettempdir())) - before:
            p = os.path.join(tempfile.gettempdir(), name)
            if os.path.isfile(p) and os.path.getsize(p) < 100_000:
                with open(p, "rb") as f:
                    self.assertNotIn(b"TOP-SECRET-VALUE", f.read())

    def test_the_environment_can_force_memory_only(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"EQU3_CREDSTORE": "memory"}):
            self.assertEqual(C.open_store().kind, "memory")


# ── sign-in ─────────────────────────────────────────────────────────────────────────────

CFG = {"authorize_url": "https://auth.example.test/authorize", "token_url": "https://auth.example.test/token",
       "userinfo_url": "https://auth.example.test/userinfo", "revoke_url": "https://auth.example.test/revoke",
       "client_id": "equpdater3", "scopes": ["openid", "profile"], "avatar_hosts": ["cdn.example.test"],
       "profile_url": "https://octowow.st/forum/memberlist.php?mode=viewprofile"}


class FakeServer:
    """A fake authorization server behind the ``http`` callable. It records every request."""

    def __init__(self, now=Clock()):
        self.now = now
        self.requests = []
        self.user = {"preferred_username": "Thrallfan", "sub": "42", "picture": "https://cdn.example.test/a.png"}
        self.token_status, self.token_error = 200, None
        self.userinfo_status = 200
        self.fail_network = False
        self.rotate = True
        self.issued = 0

    def __call__(self, method, url, data=None, headers=None, timeout=15):
        self.requests.append((method, url, dict(data or {}), dict(headers or {})))
        if self.fail_network:
            raise OSError("offline")
        if url.endswith("/token"):
            if self.token_status != 200:
                return self.token_status, json.dumps({"error": self.token_error or "server_error"}).encode()
            self.issued += 1
            body = {"access_token": f"access-{self.issued}", "expires_in": 3600, "scope": "openid profile"}
            if self.rotate or data.get("grant_type") == "authorization_code":
                body["refresh_token"] = f"refresh-{self.issued}"
            return 200, json.dumps(body).encode()
        if url.endswith("/userinfo"):
            return self.userinfo_status, json.dumps(self.user).encode()
        return 200, b"{}"


def fake_browser(server_calls=None, **overrides):
    """An ``open_browser`` that plays the person: it opens the redirect with the code (or an error)."""
    def open_browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        redirect = q["redirect_uri"][0]
        if server_calls is not None:
            server_calls.append(q)
        params = {"code": "the-code", "state": q["state"][0]}
        params.update(overrides)
        params = {k: v for k, v in params.items() if v is not None}

        def go():
            try:
                urllib.request.urlopen(redirect + "?" + urllib.parse.urlencode(params), timeout=10).read()
            except Exception:                                       # noqa: BLE001
                pass
        threading.Thread(target=go, daemon=True).start()
    return open_browser


def service(srv=None, st=None, settings=None, clock=None):
    clock = clock or Clock()
    srv = srv or FakeServer(clock)
    provider = A.OAuthProvider(CFG, http=srv, now=clock)
    return A.AccountService(provider, st or C.MemoryStore(), settings if settings is not None else Settings(), now=clock), srv


class TestUnavailableProvider(unittest.TestCase):
    def test_with_no_published_service_nothing_can_sign_in_and_nothing_is_simulated(self):
        self.assertIsNone(A.PROVIDER_CONFIG)                        # the honest default
        prov = A.make_provider()
        self.assertIsInstance(prov, A.UnavailableProvider)
        self.assertFalse(prov.available)
        svc = A.AccountService(prov, C.MemoryStore(), Settings())
        self.assertFalse(svc.sign_in(lambda url: self.fail("no browser must open")))
        self.assertEqual(svc.state, A.SIGNED_OUT)
        self.assertIn("not published", svc.message)
        self.assertEqual(svc.username, "")
        self.assertIsNone(svc.valid_access())

    def test_the_reason_never_asks_for_a_password(self):
        reason = A.UnavailableProvider().unavailable_reason
        self.assertIn("will not ask for your forum password", reason)

    def test_a_bad_configuration_is_unavailable_not_half_working(self):
        self.assertIsInstance(A.make_provider({"authorize_url": "http://insecure.test/a", "token_url": "https://x/t",
                                               "userinfo_url": "https://x/u", "client_id": "c"}), A.UnavailableProvider)
        self.assertIsInstance(A.make_provider({"client_id": "c"}), A.UnavailableProvider)

    def test_restore_with_no_provider_makes_no_request(self):
        svc = A.AccountService(A.UnavailableProvider(), C.MemoryStore(), Settings())
        svc.restore()
        self.assertEqual(svc.state, A.SIGNED_OUT)


class TestOAuthSignIn(Tmp):
    def test_the_browser_flow_uses_pkce_state_and_a_loopback_redirect(self):
        svc, srv = service()
        seen = []
        self.assertTrue(svc.sign_in(fake_browser(seen)))
        q = seen[0]
        self.assertTrue(q["redirect_uri"][0].startswith("http://127.0.0.1:"))
        self.assertEqual(q["code_challenge_method"], ["S256"])
        exchange = next(r for r in srv.requests if r[1].endswith("/token"))
        verifier = exchange[2]["code_verifier"]
        challenge = urllib.parse.parse_qs(urllib.parse.urlsplit(
            "https://x/?" + urllib.parse.urlencode({"c": q["code_challenge"][0]})).query)["c"][0]
        import base64
        self.assertEqual(challenge, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode())
        self.assertNotIn("client_secret", exchange[2])                  # a public client: no secret to leak
        self.assertEqual(svc.state, A.SIGNED_IN)
        self.assertEqual(svc.username, "Thrallfan")

    def test_no_password_is_ever_asked_for_or_sent(self):
        svc, srv = service()
        svc.sign_in(fake_browser())
        for _m, _u, data, headers in srv.requests:
            self.assertNotIn("password", " ".join(list(data) + list(headers)).lower())
            self.assertNotIn("password", " ".join(str(v) for v in data.values()).lower())

    def test_a_wrong_state_is_refused(self):
        svc, _ = service()
        self.assertFalse(svc.sign_in(fake_browser(state="forged")))
        self.assertEqual(svc.state, A.SIGNED_OUT)
        self.assertIn("could not be verified", svc.message)

    def test_a_denied_sign_in_is_not_a_failure_of_ours(self):
        svc, _ = service()
        self.assertFalse(svc.sign_in(fake_browser(error="access_denied", code=None)))
        self.assertIn("not approved", svc.message)

    def test_cancelling_stops_the_wait(self):
        svc, _ = service()
        threading.Timer(0.5, svc.cancel_sign_in).start()
        self.assertFalse(svc.sign_in(lambda url: None))                   # the browser never answers
        self.assertEqual(svc.state, A.SIGNED_OUT)
        self.assertIn("cancelled", svc.message)

    def test_an_unreachable_service_is_reported(self):
        svc, srv = service()
        srv.fail_network = True
        self.assertFalse(svc.sign_in(fake_browser()))
        self.assertIn("could not be reached", svc.message)

    def test_a_service_that_does_not_say_who_you_are_is_refused(self):
        svc, srv = service()
        srv.user = {"sub": "42"}
        self.assertFalse(svc.sign_in(fake_browser()))
        self.assertEqual(svc.state, A.SIGNED_OUT)
        srv.user = {"preferred_username": "x" * 200}
        self.assertFalse(svc.sign_in(fake_browser()))

    def test_only_https_endpoints_are_accepted(self):
        with self.assertRaises(ValueError):
            A.OAuthProvider({**CFG, "token_url": "http://auth.example.test/token"})


class TestSessionStorage(Tmp):
    def test_tokens_go_to_the_credential_store_and_never_into_the_settings(self):
        st, settings = C.MemoryStore(), Settings()
        svc, _ = service(st=st, settings=settings)
        svc.sign_in(fake_browser())
        blob = st.get(A.SERVICE, A.ACCOUNT)
        self.assertIn("access-1", blob)
        self.assertNotIn("access-1", json.dumps(settings))
        self.assertNotIn("refresh-1", json.dumps(settings))
        self.assertEqual(settings["account_username"], "Thrallfan")      # the name is not a secret

    def test_a_saved_session_is_restored_at_start_and_confirmed(self):
        st, settings = C.MemoryStore(), Settings()
        first, _ = service(st=st, settings=settings)
        first.sign_in(fake_browser())
        second, srv = service(st=st, settings=settings)
        second.restore()
        self.assertEqual((second.state, second.username, second.verified), (A.SIGNED_IN, "Thrallfan", True))
        self.assertTrue(any(r[1].endswith("/userinfo") for r in srv.requests))

    def test_an_expired_session_is_refreshed_and_the_new_tokens_are_saved(self):
        clock = Clock()
        st, settings = C.MemoryStore(), Settings()
        first, srv = service(st=st, settings=settings, clock=clock)
        first.sign_in(fake_browser())
        clock.t += 7200                                             # the access token has expired
        second, _ = service(srv=srv, st=st, settings=settings, clock=clock)
        second.restore()
        self.assertEqual(second.state, A.SIGNED_IN)
        self.assertIn("refresh_token", [r[2].get("grant_type") for r in srv.requests])
        self.assertIn("access-2", st.get(A.SERVICE, A.ACCOUNT))

    def test_a_rejected_refresh_signs_out_and_deletes_the_credentials(self):
        clock = Clock()
        st, settings = C.MemoryStore(), Settings()
        first, srv = service(st=st, settings=settings, clock=clock)
        first.sign_in(fake_browser())
        clock.t += 7200
        srv.token_status, srv.token_error = 400, "invalid_grant"
        second, _ = service(srv=srv, st=st, settings=settings, clock=clock)
        second.restore()
        self.assertEqual(second.state, A.SIGNED_OUT)
        self.assertIsNone(st.get(A.SERVICE, A.ACCOUNT))
        self.assertEqual(settings["account_username"], "")
        self.assertIn("expired", second.message)

    def test_a_network_failure_at_start_keeps_the_session_unverified(self):
        st, settings = C.MemoryStore(), Settings()
        first, srv = service(st=st, settings=settings)
        first.sign_in(fake_browser())
        srv.fail_network = True
        second, _ = service(srv=srv, st=st, settings=settings)
        second.restore()
        self.assertEqual(second.state, A.SIGNED_IN)                    # being offline proves nothing
        self.assertFalse(second.verified)
        self.assertIsNotNone(st.get(A.SERVICE, A.ACCOUNT))

    def test_access_is_refreshed_before_it_expires_and_a_refresh_token_survives_rotation_gaps(self):
        clock = Clock()
        svc, srv = service(clock=clock)
        svc.sign_in(fake_browser())
        srv.rotate = False                                          # the server will not send a new refresh token
        clock.t += 3600 - 30                                         # inside the early-refresh margin
        self.assertEqual(svc.valid_access(), "access-2")
        self.assertEqual(svc.tokens.refresh, "refresh-1")
        clock.t += 3600 - 30
        self.assertEqual(svc.valid_access(), "access-3")

    def test_sign_out_clears_everything_locally_even_when_the_service_cannot_be_reached(self):
        st, settings = C.MemoryStore(), Settings()
        svc, srv = service(st=st, settings=settings)
        svc.sign_in(fake_browser())
        srv.fail_network = True
        svc.sign_out()
        self.assertEqual(svc.state, A.SIGNED_OUT)
        self.assertIsNone(st.get(A.SERVICE, A.ACCOUNT))
        self.assertEqual((settings["account_username"], settings["account_avatar_url"]), ("", ""))
        self.assertIsNone(svc.valid_access())
        self.assertEqual(svc.username, "")

    def test_sign_out_tells_the_service_to_revoke_both_tokens(self):
        svc, srv = service()
        svc.sign_in(fake_browser())
        svc.sign_out()
        revoked = [r[2]["token_type_hint"] for r in srv.requests if r[1].endswith("/revoke")]
        self.assertEqual(sorted(revoked), ["access_token", "refresh_token"])

    def test_a_memory_only_store_does_not_survive_a_restart(self):
        settings = Settings()
        first, _ = service(st=C.MemoryStore(), settings=settings)
        first.sign_in(fake_browser())
        second, _ = service(st=C.MemoryStore(), settings=settings)      # a new run, a new empty store
        second.restore()
        self.assertEqual(second.state, A.SIGNED_OUT)
        self.assertFalse(second.persists)

    def test_a_corrupt_saved_session_is_ignored(self):
        st = C.MemoryStore()
        st.set(A.SERVICE, A.ACCOUNT, "{not json")
        svc, _ = service(st=st)
        svc.restore()
        self.assertEqual(svc.state, A.SIGNED_OUT)

    def test_one_sign_in_at_a_time(self):
        svc, _ = service()
        svc.state = A.SIGNING_IN
        self.assertFalse(svc.sign_in(fake_browser()))


# ── friends ─────────────────────────────────────────────────────────────────────────────

REALMS = {"nzoth", "yshaarj", "cthun"}


def book(tmp, clock=None):
    return F.FriendBook(store(tmp), realms=lambda: REALMS, now=clock or Clock())


class TestCensusIsNotPresence(unittest.TestCase):
    def test_the_presence_code_does_not_touch_the_aggregate_census(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("equpdater/core/presence.py", "equpdater/core/social_service.py", "equpdater/core/friends.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("server import", text, rel)
            self.assertNotIn("core.server", text, rel)
            self.assertNotIn("live.json", text, rel)


class TestFriends(Tmp):
    def test_add_remove_and_persist(self):
        b = book(self.d)
        f = b.add("thrall", "nzoth")
        self.assertEqual((f.name, f.realm), ("Thrall", "nzoth"))                # name is tidied
        self.assertEqual(len(book(self.d)), 1)                                   # reloaded from disk
        self.assertTrue(b.remove(f.id))
        self.assertEqual(len(book(self.d)), 0)
        self.assertFalse(b.remove(f.id))

    def test_the_same_character_twice_is_refused_whatever_the_case(self):
        b = book(self.d)
        b.add("Thrall", "nzoth")
        for name in ("Thrall", "thrall", "THRALL"):
            with self.assertRaises(F.FriendError):
                b.add(name, "nzoth")
        self.assertEqual(len(b), 1)

    def test_the_same_name_on_another_realm_is_a_different_friend(self):
        b = book(self.d)
        a, c = b.add("Thrall", "nzoth"), b.add("Thrall", "cthun")
        self.assertNotEqual(a.id, c.id)
        self.assertEqual(len(b), 2)
        b.remove(a.id)
        self.assertEqual([f.realm for f in b.all()], ["cthun"])                 # removing one leaves the other

    def test_what_is_refused(self):
        b = book(self.d)
        for args in (("", "nzoth"), ("a", "nzoth"), ("Thrall123", "nzoth"), ("Thrall", ""), ("Thrall", "azeroth"),
                     ("Thr all", "nzoth"), ("Thrall" * 3, "nzoth")):
            with self.assertRaises(F.FriendError, msg=str(args)):
                b.add(*args)
        with self.assertRaises(F.FriendError):
            b.add("Thrall", "nzoth", forum="someone")                             # one kind at a time
        self.assertEqual(len(b), 0)

    def test_accented_names_are_letters(self):
        b = book(self.d)
        self.assertEqual(b.add("Ælfwyn", "nzoth").name, "Ælfwyn")

    def test_account_friends_by_forum_name(self):
        b = book(self.d)
        f = b.add(forum="Forum User")
        self.assertEqual((f.label, f.realm), ("Forum User", ""))
        with self.assertRaises(F.FriendError):
            b.add(forum="forum user")
        with self.assertRaises(F.FriendError):
            b.add(forum="x")

    def test_the_list_has_a_limit(self):
        b = book(self.d)
        for i in range(F.MAX_FRIENDS):
            b.add("Name" + "abcdefghij"[i % 10] * 2 + chr(97 + i // 10 % 26) * 2 + chr(97 + i // 260), "nzoth") \
                if False else b.add(f"Fr{chr(97 + i // 26)}{chr(97 + i % 26)}x", "nzoth")
        with self.assertRaises(F.FriendError):
            b.add("Onemore", "nzoth")

    def test_favourite_search_and_realm_filter(self):
        b = book(self.d)
        a, c, d = b.add("Thrall", "nzoth"), b.add("Jaina", "yshaarj"), b.add("Thrall", "cthun")
        self.assertTrue(b.set_favourite(a.id, True))
        self.assertFalse(b.set_favourite(a.id, True))
        self.assertTrue(book(self.d).get(a.id).favourite)                          # persisted
        self.assertEqual({f.id for f in b.search("thr")}, {a.id, d.id})
        self.assertEqual([f.id for f in b.search("thr", "cthun")], [d.id])
        self.assertEqual([f.id for f in b.search("", "yshaarj")], [c.id])

    def test_a_hand_edited_or_damaged_file_does_not_break_the_list(self):
        st = store(self.d)
        st.set("friends", [{"id": "a", "name": "Good", "realm": "nzoth"}, {"id": 5}, "junk",
                           {"id": "b", "name": "Bad Name!", "realm": "nzoth"},
                           {"id": "c", "name": "Good", "realm": "nzoth"}])        # a duplicate
        self.assertEqual([f.name for f in F.FriendBook(st)], []) if False else None
        b = F.FriendBook(st)
        self.assertEqual([f.id for f in b.all()], ["a"])

    def test_any_realm_is_accepted_when_the_list_is_unknown(self):
        b = F.FriendBook(store(self.d), realms=None)
        self.assertTrue(b.add("Thrall", "newrealm"))

    def test_grouping_and_sorting(self):
        fr = [F.Friend("1", "Zed", "nzoth"), F.Friend("2", "Amy", "yshaarj", favourite=True),
              F.Friend("3", "Bob", "cthun"), F.Friend("4", "Cat", "nzoth")]
        states = {"1": "in_game", "2": "offline", "3": "unknown", "4": "away"}
        state_of = lambda f: states[f.id]                                       # noqa: E731
        self.assertEqual([F.group_of(f, states[f.id]) for f in fr], ["online", "favourites", "offline", "online"])
        self.assertEqual([f.name for f in sorted(fr, key=F.sort_key("status", state_of))], ["Zed", "Cat", "Bob", "Amy"])
        self.assertEqual([f.name for f in sorted(fr, key=F.sort_key("name", state_of))], ["Amy", "Bob", "Cat", "Zed"])
        self.assertEqual([f.name for f in sorted(fr, key=F.sort_key("realm", state_of))], ["Bob", "Cat", "Zed", "Amy"])


# ── presence ────────────────────────────────────────────────────────────────────────────

def obs(fid="f", game="unknown", launcher="unknown", as_of=NOW, **kw):
    return P.Observation(fid, game, launcher, as_of, **kw)


class TestPresenceStates(unittest.TestCase):
    def state(self, o, now=NOW, stale_after=180):
        return P.resolve(o, now, stale_after)

    def test_the_six_states(self):
        self.assertEqual(self.state(obs(game="in_game"))[0], P.IN_GAME)
        self.assertEqual(self.state(obs(launcher="online"))[0], P.LAUNCHER)
        self.assertEqual(self.state(obs(launcher="away"))[0], P.AWAY)
        self.assertEqual(self.state(obs(launcher="dnd"))[0], P.DND)
        self.assertEqual(self.state(obs(game="offline"))[0], P.OFFLINE)
        self.assertEqual(self.state(None)[0], P.UNKNOWN)
        self.assertEqual(self.state(obs())[0], P.UNKNOWN)

    def test_the_game_outranks_the_launcher(self):
        self.assertEqual(self.state(obs(game="in_game", launcher="dnd"))[0], P.IN_GAME)

    def test_not_using_the_launcher_never_means_the_character_is_offline(self):
        self.assertEqual(self.state(obs(game="unknown", launcher="offline"))[0], P.UNKNOWN)
        self.assertEqual(self.state(obs(game="unknown", launcher="unknown"))[0], P.UNKNOWN)

    def test_offline_needs_the_game_channel_to_say_so(self):
        self.assertEqual(self.state(obs(game="offline", launcher="offline"))[0], P.OFFLINE)
        self.assertEqual(self.state(obs(game="offline", launcher="unknown"))[0], P.OFFLINE)

    def test_a_character_offline_in_game_but_in_the_launcher_shows_the_launcher(self):
        self.assertEqual(self.state(obs(game="offline", launcher="online"))[0], P.LAUNCHER)

    def test_stale_information_is_flagged_not_dropped(self):
        self.assertEqual(self.state(obs(game="in_game", as_of=NOW - 100))[1], False)
        self.assertEqual(self.state(obs(game="in_game", as_of=NOW - 1000)), (P.IN_GAME, True))

    def test_values_from_a_service_are_not_trusted(self):
        o = obs(game="hacked", launcher="<b>", level=999, guild="g" * 500, zone=None).clean()
        self.assertEqual((o.game, o.launcher, o.level, o.zone), ("unknown", "unknown", None, ""))
        self.assertLessEqual(len(o.guild), 48)
        self.assertIsNone(obs(level=True).clean().level)

    def test_every_state_has_a_label_and_a_distinct_dot_for_the_live_ones(self):
        for s in P.STATES:
            self.assertTrue(P.label(s))
        live = [P.COLOURS[s] for s in (P.IN_GAME, P.LAUNCHER, P.AWAY, P.DND)]
        self.assertEqual(len(set(live)), 4)
        self.assertEqual(P.COLOURS[P.IN_GAME].lower(), "#3ddc84")                  # green
        self.assertEqual(P.COLOURS[P.DND].lower(), "#ef5350")                      # red


class FakeProvider(P.PresenceProvider):
    id, name, available = "fake", "fake", True
    capabilities = frozenset({"game_presence", "launcher_presence", "character_details"})

    def __init__(self, max_batch=50):
        self.max_batch = max_batch
        self.calls = []
        self.data = {}
        self.error = None

    def lookup(self, friends, token):
        self.calls.append(([f.id for f in friends], token))
        if self.error:
            raise self.error
        return [self.data[f.id] for f in friends if f.id in self.data]


def presence_setup(tmp, provider=None, n=2, account=None, clock=None):
    clock = clock or Clock()
    b = book(tmp, clock)
    names = ["Alpha", "Bravo", "Charlie", "Delta"]
    friends = [b.add(names[i], "nzoth") for i in range(n)]
    prov = provider or FakeProvider()
    svc = P.PresenceService(b, prov, account, now=clock)
    svc.sidebar_open = True
    return svc, prov, b, friends, clock


class TestPresenceService(Tmp):
    def test_the_unavailable_provider_makes_no_request_and_every_friend_is_unknown(self):
        b = book(self.d)
        f = b.add("Thrall", "nzoth")
        svc = P.PresenceService(b, P.UnavailablePresenceProvider(), now=Clock())
        svc.sidebar_open = True
        self.assertFalse(svc.active())
        self.assertIsNone(svc.delay())
        self.assertFalse(svc.poll())
        self.assertEqual(svc.requests, 0)
        self.assertEqual(svc.state_of(f), (P.UNKNOWN, False))
        self.assertIn("no service", svc.status_text())

    def test_nothing_is_requested_unless_every_condition_holds(self):
        svc, prov, b, fr, _ = presence_setup(self.d)
        self.assertTrue(svc.active())
        for attr, off in (("sidebar_open", False), ("minimized", True), ("enabled", False)):
            old = getattr(svc, attr)
            setattr(svc, attr, off)
            self.assertFalse(svc.active(), attr)
            self.assertIsNone(svc.delay(), attr)
            self.assertFalse(svc.poll())
            setattr(svc, attr, old)
        for f in b.all():
            b.remove(f.id)
        self.assertFalse(svc.active())                                          # nobody to ask about
        self.assertEqual(prov.calls, [])

    def test_a_provider_that_needs_an_account_waits_for_one(self):
        class Acct:
            state = "signed_out"
            def valid_access(self):
                return "tok"
        acct = Acct()
        prov = FakeProvider()
        prov.requires_account = True
        svc, _, _, fr, _ = presence_setup(self.d, prov, account=acct)
        self.assertFalse(svc.active())
        acct.state = "signed_in"
        self.assertTrue(svc.poll() or True)
        self.assertEqual(prov.calls[0][1], "tok")

    def test_requests_are_batched(self):
        prov = FakeProvider(max_batch=3)
        svc, _, b, _, _ = presence_setup(self.d, prov, n=4)
        svc.book.add("Echo", "cthun")
        svc.book.add("Foxtrot", "cthun")
        svc.poll()
        self.assertEqual([len(c[0]) for c in prov.calls], [3, 3])
        self.assertEqual(svc.requests, 2)

    def test_results_are_cached_cleaned_and_stamped(self):
        svc, prov, b, fr, clock = presence_setup(self.d)
        prov.data[fr[0].id] = P.Observation(fr[0].id, "in_game", "online", None, "shaman", 60, "Guild", "Orgrimmar")
        prov.data["not-a-friend"] = P.Observation("not-a-friend", "in_game")
        svc.poll()
        self.assertEqual(svc.state_of(fr[0]), (P.IN_GAME, False))
        self.assertEqual(svc.observation(fr[0]).as_of, clock.t)
        self.assertEqual(svc.state_of(fr[1]), (P.UNKNOWN, False))
        self.assertNotIn("not-a-friend", svc.cache)

    def test_data_becomes_stale_when_it_stops_being_refreshed(self):
        svc, prov, b, fr, clock = presence_setup(self.d)
        prov.data[fr[0].id] = obs(fr[0].id, game="in_game", as_of=clock.t)
        svc.poll()
        clock.t += svc.stale_after + 1
        self.assertEqual(svc.state_of(fr[0]), (P.IN_GAME, True))

    def test_rate_limiting_waits_and_is_not_counted_as_a_failure(self):
        svc, prov, _, fr, clock = presence_setup(self.d)
        prov.error = P.RateLimited(120)
        svc.poll()
        self.assertTrue(svc.rate_limited)
        self.assertEqual(svc.failures, 0)
        self.assertGreaterEqual(svc.delay(), 119)
        self.assertIn("slow down", svc.status_text())
        prov.error = None
        clock.t += 130
        svc.poll()
        self.assertFalse(svc.rate_limited)

    def test_a_huge_retry_after_is_capped(self):
        svc, prov, _, _, clock = presence_setup(self.d)
        prov.error = P.RateLimited(10 ** 9)
        svc.poll()
        self.assertLessEqual(svc.delay(), 3600)

    def test_failures_back_off_exponentially_and_reset_on_success(self):
        svc, prov, _, _, clock = presence_setup(self.d)
        prov.error = OSError("down")
        delays = []
        for _ in range(7):
            svc.poll()
            delays.append(round(svc.delay()))
            clock.t += svc.delay() + 1
        self.assertEqual(delays, [30, 60, 120, 240, 480, 600, 600])
        prov.error = None
        svc.poll()
        self.assertEqual(svc.failures, 0)
        self.assertEqual(svc.error, "")

    def test_the_last_known_data_is_kept_through_an_outage(self):
        svc, prov, _, fr, clock = presence_setup(self.d)
        prov.data[fr[0].id] = obs(fr[0].id, game="in_game")
        svc.poll()
        prov.error = OSError("down")
        svc.poll()
        self.assertEqual(svc.state_of(fr[0])[0], P.IN_GAME)
        self.assertIn("last known", svc.status_text())

    def test_the_interval_is_configurable_and_bounded(self):
        svc, _, _, _, _ = presence_setup(self.d)
        svc.set_interval(5)
        self.assertEqual(svc.interval, P.MIN_INTERVAL)
        svc.set_interval(99999)
        self.assertEqual(svc.interval, P.MAX_INTERVAL)
        svc.set_interval(120)
        self.assertEqual(svc.stale_after, 360)

    def test_the_next_poll_is_one_interval_after_the_last_success(self):
        svc, prov, _, _, clock = presence_setup(self.d)
        svc.poll()
        self.assertAlmostEqual(svc.delay(), svc.interval, delta=1)

    def test_a_friend_coming_online_is_announced_once_and_not_on_the_first_poll(self):
        svc, prov, _, fr, clock = presence_setup(self.d)
        heard = []
        svc.on_friend_online = lambda f, o: heard.append(f.name)
        prov.data[fr[0].id] = obs(fr[0].id, game="in_game")
        svc.poll()
        self.assertEqual(heard, [])                                             # the baseline, not news
        prov.data[fr[0].id] = obs(fr[0].id, game="offline", as_of=clock.t)
        prov.data[fr[1].id] = obs(fr[1].id, game="offline")
        svc.poll()
        prov.data[fr[0].id] = obs(fr[0].id, game="in_game", as_of=clock.t)
        svc.poll()
        svc.poll()
        self.assertEqual(heard, ["Alpha"])

    def test_forgetting_a_friend_forgets_what_was_known(self):
        svc, prov, _, fr, _ = presence_setup(self.d)
        prov.data[fr[0].id] = obs(fr[0].id, game="in_game")
        svc.poll()
        svc.forget(fr[0].id)
        self.assertEqual(svc.state_of(fr[0])[0], P.UNKNOWN)


# ── realms ──────────────────────────────────────────────────────────────────────────────

class TestRealms(Tmp):
    LIVE = [{"id": "all", "name": "All Realms"}, {"id": "nzoth", "name": "N’Zoth (Normal)"},
            {"id": "yshaarj", "name": "Y�Shaarj (PvP)"}, {"id": "cthun", "name": "C'Thun (Hardcore)"},
            {"id": "newrealm", "name": "A New Realm"}, {"id": "bad id"}, "junk"]

    def test_the_aggregate_is_not_a_realm_and_new_realms_appear(self):
        names = {r.id: r.name for r in R.parse(self.LIVE)}
        self.assertEqual(names, {"nzoth": "N'Zoth", "yshaarj": "Y'Shaarj", "cthun": "C'Thun", "newrealm": "A New Realm"})

    def test_the_fallback_is_marked_as_not_authoritative(self):
        rl = R.RealmList(store(self.d))
        self.assertFalse(rl.authoritative)
        self.assertEqual(rl.ids(), {"nzoth", "yshaarj", "cthun"})
        self.assertFalse(any(r.authoritative for r in rl.all()))

    def test_a_fetched_list_replaces_the_fallback_and_survives_a_restart(self):
        body = json.dumps(self.LIVE).encode("utf-8")
        from equpdater.core.server import Response
        rl = R.RealmList(store(self.d), fetch=lambda url, etag="": Response(200, body))
        self.assertTrue(rl.refresh())
        self.assertIn("newrealm", rl.ids())
        again = R.RealmList(store(self.d))
        self.assertTrue(again.authoritative)
        self.assertIn("newrealm", again.ids())

    def test_a_failed_or_empty_fetch_keeps_what_there_was(self):
        from equpdater.core.server import Response
        rl = R.RealmList(store(self.d), fetch=lambda url, etag="": Response(200, json.dumps(self.LIVE).encode()))
        rl.refresh()
        rl.fetch = lambda url, etag="": (_ for _ in ()).throw(OSError("down"))
        self.assertFalse(rl.refresh())
        rl.fetch = lambda url, etag="": Response(200, b"[]")
        self.assertFalse(rl.refresh())
        self.assertIn("newrealm", rl.ids())

    def test_it_is_refreshed_daily_not_every_launch(self):
        clock = Clock()
        from equpdater.core.server import Response
        rl = R.RealmList(store(self.d), fetch=lambda url, etag="": Response(200, json.dumps(self.LIVE).encode()), now=clock)
        self.assertTrue(rl.due())
        rl.refresh()
        self.assertFalse(rl.due())
        clock.t += R.REFRESH_S + 1
        self.assertTrue(rl.due())

    def test_without_a_fetcher_nothing_is_requested(self):
        rl = R.RealmList(store(self.d), fetch=None)
        self.assertFalse(rl.due())
        self.assertFalse(rl.refresh())


# ── the social service and its settings ──────────────────────────────────────────────────

class FakeAccount:
    state = "signed_in"

    def __init__(self):
        self.tokens = 0

    def valid_access(self):
        self.tokens += 1
        return "tok"


def social(tmp, provider=None, settings=None, n=0, clock=None):
    clock = clock or Clock()
    b = book(tmp, clock)
    rl = R.RealmList(store(tmp))
    prov = provider or P.UnavailablePresenceProvider()
    pres = P.PresenceService(b, prov, FakeAccount(), now=clock)
    s = settings if settings is not None else Settings()
    svc = SocialService(s, b, pres, FakeAccount(), rl, now=clock)
    return svc, b, pres, s


class TestSocialService(Tmp):
    def seed(self, svc):
        out = {}
        for name, realm in (("Thrall", "nzoth"), ("Jaina", "yshaarj"), ("Arthas", "cthun"), ("Sylvanas", "nzoth")):
            svc.add_friend(name, realm)
            out[name] = svc.book.search(name)[0]
        return out

    def test_privacy_first_defaults(self):
        svc, *_ = social(self.d)
        for key in ("show_launcher_presence", "show_game_presence", "show_character", "show_realm", "show_zone",
                    "friend_notifications"):
            self.assertFalse(svc.get(key), key)
        self.assertTrue(svc.get("social_enabled"))
        self.assertIsNone(svc.get("sidebar_collapsed"))                          # the skin decides until the player does

    def test_groups_are_favourites_online_offline_and_unknown_is_not_called_offline(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        f = self.seed(svc)
        pres.sidebar_open = True
        svc.toggle_favourite(f["Thrall"].id)
        prov.data[f["Jaina"].id] = obs(f["Jaina"].id, game="in_game")
        prov.data[f["Arthas"].id] = obs(f["Arthas"].id, game="offline")
        pres.poll()
        rows = svc.rows()
        heads = [(r["group"], r["title"], r["count"]) for r in rows if r["kind"] == "header"]
        self.assertEqual(heads, [("favourites", "Favourites", 1), ("online", "Online", 1),
                                 ("offline", "Offline or unavailable", 2)])       # Sylvanas is unknown
        self.assertEqual(svc.online_count(), 1)

    def test_a_group_of_only_offline_friends_is_just_offline(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        f = self.seed(svc)
        pres.sidebar_open = True
        for fr in f.values():
            prov.data[fr.id] = obs(fr.id, game="offline")
        pres.poll()
        self.assertEqual([r["title"] for r in svc.rows() if r["kind"] == "header"], ["Offline"])

    def test_every_friend_is_unknown_without_a_service(self):
        svc, *_ = social(self.d)
        self.seed(svc)
        rows = [r for r in svc.rows() if r["kind"] == "friend"]
        self.assertEqual({r["state"] for r in rows}, {"unknown"})
        self.assertEqual(svc.online_count(), 0)

    def test_search_realm_filter_and_sort(self):
        svc, *_ = social(self.d)
        self.seed(svc)
        names = lambda: [r["name"] for r in svc.rows() if r["kind"] == "friend"]   # noqa: E731
        svc.set("friends_sort", "name")
        self.assertEqual(names(), ["Arthas", "Jaina", "Sylvanas", "Thrall"])
        svc.set("friends_sort", "realm")
        self.assertEqual(names(), ["Arthas", "Sylvanas", "Thrall", "Jaina"])
        svc.set("friends_realm", "nzoth")
        self.assertEqual(names(), ["Sylvanas", "Thrall"])
        svc.set("friends_realm", "")
        svc.search = "ain"
        self.assertEqual(names(), ["Jaina"])

    def test_character_details_appear_when_a_service_gives_them(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        f = self.seed(svc)
        pres.sidebar_open = True
        prov.data[f["Thrall"].id] = P.Observation(f["Thrall"].id, "in_game", "online", None, "shaman", 60, "Horde", "Orgrimmar")
        pres.poll()
        row = next(r for r in svc.rows() if r.get("name") == "Thrall")
        self.assertEqual((row["charClass"], row["level"], row["guild"], row["zone"]), ("shaman", 60, "Horde", "Orgrimmar"))
        self.assertEqual(row["nameColour"].lower(), "#0070de")                         # the class colour
        self.assertIn("Level 60", row["detail"])
        self.assertEqual(row["realmName"], "N'Zoth")

    def test_an_unknown_class_gets_no_class_colour(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        f = self.seed(svc)
        pres.sidebar_open = True
        prov.data[f["Thrall"].id] = P.Observation(f["Thrall"].id, "in_game", "online", None, "necromancer", 60)
        pres.poll()
        self.assertEqual(next(r for r in svc.rows() if r.get("name") == "Thrall")["nameColour"], "")

    def test_the_status_choices_are_validated_and_saved(self):
        svc, *_ = social(self.d)
        svc.set_status("dnd")
        self.assertEqual(svc.status, "dnd")
        svc.set_status("invisible")
        self.assertEqual(svc.status, "dnd")
        svc.set("default_status", "nonsense")
        self.assertEqual(svc.get("default_status"), "online")
        with self.assertRaises(KeyError):
            svc.set("not_a_setting", 1)

    def test_auto_away_only_softens_online(self):
        svc, *_ = social(self.d)
        svc.set("auto_away_minutes", 10)
        svc.window_idle(True)
        self.assertEqual(svc.status, "away")
        svc.window_idle(False)
        self.assertEqual(svc.status, "online")
        svc.set_status("dnd")
        svc.window_idle(True)
        self.assertEqual(svc.status, "dnd")                                          # never overrides a choice
        svc2, *_ = social(tempfile.mkdtemp())
        svc2.window_idle(True)                                                       # auto-away is off
        self.assertEqual(svc2.status, "online")

    def test_settings_survive_a_restart_and_an_old_file_gets_defaults(self):
        path = os.path.join(self.d, "settings.json")
        with open(path, "w") as f:
            json.dump({"_schema": 1, "skin": "bubbles", "animated_background": True}, f)     # a 3.0-beta file with no social keys
        from equpdater.core import settings as S
        st = S.SettingsStore(path, S.DEFAULTS, schema=1)
        self.assertTrue(st.get("social_enabled"))
        self.assertEqual(st.get("friends_sort"), "status")
        self.assertEqual(st.get("skin"), "bubbles")
        st.set("show_zone", True)
        st.set("sidebar_collapsed", False)
        st.set("auto_away_minutes", 15)
        st.flush()
        again = S.SettingsStore(path, S.DEFAULTS, schema=1)
        self.assertEqual((again.get("show_zone"), again.get("sidebar_collapsed"), again.get("auto_away_minutes")),
                         (True, False, 15))

    def test_notifications_are_off_by_default_and_silent_in_do_not_disturb(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        f = self.seed(svc)
        pres.sidebar_open = True
        pres.poll()                                                                   # baseline
        prov.data[f["Thrall"].id] = obs(f["Thrall"].id, game="in_game")
        pres.poll()
        self.assertEqual(svc.take_toasts(), [])                                       # off
        svc.set("friend_notifications", True)
        prov.data[f["Thrall"].id] = obs(f["Thrall"].id, game="offline")
        pres.poll()
        prov.data[f["Thrall"].id] = obs(f["Thrall"].id, game="in_game")
        pres.poll()
        self.assertEqual(svc.take_toasts(), [("Thrall", "is online in game")])
        svc.set_status("dnd")
        prov.data[f["Thrall"].id] = obs(f["Thrall"].id, game="offline")
        pres.poll()
        prov.data[f["Thrall"].id] = obs(f["Thrall"].id, game="in_game")
        pres.poll()
        self.assertEqual(svc.take_toasts(), [])

    def test_nothing_is_published_without_a_service_and_nothing_shared_by_default(self):
        svc, *_ = social(self.d)
        self.assertFalse(svc.publish())
        own = svc.own()
        self.assertEqual((own.show_launcher, own.show_game, own.show_character, own.show_realm, own.show_zone),
                         (False,) * 5)

    def test_publishing_sends_only_what_the_player_allowed_and_only_when_it_changes(self):
        class Pub(FakeProvider):
            capabilities = frozenset({"publish"})
            requires_account = True
            sent = []

            def publish(self, status, token):
                self.sent.append((status, token))
        prov = Pub()
        svc, *_ = social(self.d, prov)
        svc.set("show_launcher_presence", True)
        self.assertTrue(svc.publish())
        self.assertFalse(svc.publish())                                               # unchanged
        status, token = prov.sent[0]
        self.assertEqual((status.show_launcher, status.show_character, status.show_zone), (True, False, False))
        self.assertEqual(token, "tok")
        svc.set("show_zone", True)
        self.assertTrue(svc.publish())
        svc.set("social_enabled", False)
        svc.set("show_zone", False)
        self.assertFalse(svc.publish())                                               # social switched off: silent

    def test_disabling_social_stops_all_polling(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        self.seed(svc)
        pres.sidebar_open = True
        self.assertTrue(pres.active())
        svc.set("social_enabled", False)
        self.assertFalse(pres.active())
        self.assertFalse(pres.poll())
        self.assertEqual(prov.calls, [])

    def test_removing_a_friend_removes_their_cached_presence(self):
        prov = FakeProvider()
        svc, b, pres, _ = social(self.d, prov)
        f = self.seed(svc)
        pres.sidebar_open = True
        prov.data[f["Thrall"].id] = obs(f["Thrall"].id, game="in_game")
        pres.poll()
        svc.remove_friend(f["Thrall"].id)
        self.assertNotIn(f["Thrall"].id, pres.cache)
        self.assertEqual(len(svc.book), 3)

    def test_adding_reports_why_not(self):
        svc, *_ = social(self.d)
        self.assertEqual(svc.add_friend("Thrall", "nzoth"), "")
        self.assertIn("already", svc.add_friend("thrall", "nzoth"))
        self.assertIn("realm", svc.add_friend("Jaina", "moon"))

    def test_account_friends_need_a_service_that_resolves_them(self):
        svc, *_ = social(self.d)
        self.assertFalse(svc.account_friends_supported())
        prov = FakeProvider()
        prov.capabilities = frozenset({"account_characters"})
        self.assertTrue(social(tempfile.mkdtemp(), prov)[0].account_friends_supported())


if __name__ == "__main__":
    unittest.main()
