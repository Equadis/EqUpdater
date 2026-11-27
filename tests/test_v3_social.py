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
        o = obs(game="hacked", launcher="<b>", realm="r" * 500).clean()
        self.assertEqual((o.game, o.launcher), ("unknown", "unknown"))
        self.assertLessEqual(len(o.realm), 48)
        self.assertEqual(obs(launcher="invisible").clean().launcher, "offline")     # a service must mask it itself

    def test_every_state_has_a_label_and_a_distinct_dot_for_the_live_ones(self):
        for s in P.STATES:
            self.assertTrue(P.label(s))
        self.assertEqual(P.COLOURS[P.LAUNCHER].lower(), "#3ddc84")                 # Online: green
        self.assertEqual(P.COLOURS[P.AWAY].lower(), "#f2c744")                     # Away: yellow
        self.assertEqual(P.COLOURS[P.DND].lower(), "#ef5350")                      # Do Not Disturb: red
        self.assertEqual(P.COLOURS[P.OFFLINE].lower(), P.COLOURS[P.UNKNOWN].lower())   # grey


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
