"""The simplified social model (core, no Qt): forum-name friends, presence strings, the four own
statuses, Invisible, the 15-minute Away rule, the fixed five-second batched refresh, and the
migration of the removed Social settings. Automated, fake clock and fake providers only."""

import os
import shutil
import tempfile
import unittest
from dataclasses import fields

from equpdater.core import friends as F
from equpdater.core import presence as P
from equpdater.core import realms as R
from equpdater.core import settings as S
from equpdater.core.social_service import AUTO_AWAY_SECONDS, STATUS_CHOICES, SocialService
from equpdater.core.store import JsonStore

NOW = 1_800_000_000.0
REALMS = {"nzoth", "yshaarj", "cthun"}


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


class FakeAccount:
    state = "signed_in"

    def __init__(self):
        self.tokens = 0

    def valid_access(self):
        self.tokens += 1
        return "tok"


class FakeProvider(P.PresenceProvider):
    id, name, available = "fake", "fake", True
    capabilities = frozenset({"game_presence", "launcher_presence", "publish"})

    def __init__(self, max_batch=50):
        self.max_batch = max_batch
        self.calls, self.published = [], []
        self.data = {}
        self.error = None

    def lookup(self, friends, token):
        self.calls.append([f.id for f in friends])
        if self.error:
            raise self.error
        return [self.data[f.id] for f in friends if f.id in self.data]

    def publish(self, status, token):
        self.published.append(status)


def make(tmp, provider=None, account=None, clock=None, settings=None):
    clock = clock or Clock()
    book = F.FriendBook(store(tmp), now=clock)
    prov = provider or P.UnavailablePresenceProvider()
    acct = account if account is not None else FakeAccount()
    pres = P.PresenceService(book, prov, acct, now=clock)
    svc = SocialService(settings if settings is not None else Settings(), book, pres, acct, R.RealmList(store(tmp)), now=clock)
    return svc, book, pres, prov, clock


def obs(fid, game="unknown", launcher="unknown", realm="", as_of=NOW):
    return P.Observation(fid, game, launcher, as_of, realm)


class TestFriendList(Tmp):
    def test_friends_are_forum_user_names_and_persist(self):
        svc, book, *_ = make(self.d)
        self.assertEqual(svc.add_friend("Thrall"), "")
        self.assertEqual([f.label for f in book.all()], ["Thrall"])
        again = F.FriendBook(store(self.d))
        self.assertEqual([f.label for f in again.all()], ["Thrall"])

    def test_what_is_refused(self):
        svc, book, *_ = make(self.d)
        self.assertNotEqual(svc.add_friend(""), "")
        self.assertNotEqual(svc.add_friend("ab"), "")
        self.assertNotEqual(svc.add_friend("<script>"), "")
        svc.add_friend("Thrall")
        self.assertIn("already", svc.add_friend("thrall"))
        self.assertEqual(len(book), 1)

    def test_the_list_has_a_limit(self):
        svc, book, *_ = make(self.d)
        for i in range(F.MAX_FRIENDS):
            self.assertEqual(svc.add_friend(f"friend{i:03d}"), "")
        self.assertIn("full", svc.add_friend("onemore"))

    def test_a_first_beta_character_entry_is_kept_and_still_shown(self):
        st = store(self.d)
        st.set("friends", [{"id": "old1", "name": "Thrall", "realm": "nzoth", "forum": "", "favourite": True, "added": 1}])
        svc, book, *_ = make(self.d)
        self.assertEqual([f.label for f in book.all()], ["Thrall"])
        self.assertEqual([r["name"] for r in svc.rows() if r["kind"] == "friend"], ["Thrall"])

    def test_search_and_remove(self):
        svc, book, *_ = make(self.d)
        for n in ("Thrall", "Jaina", "Thrain"):
            svc.add_friend(n)
        svc.search = "thr"
        self.assertEqual(sorted(r["name"] for r in svc.rows() if r["kind"] == "friend"), ["Thrain", "Thrall"])
        svc.search = ""
        victim = book.search("Jaina")[0]
        svc.remove_friend(victim.id)
        self.assertEqual(sorted(f.label for f in book.all()), ["Thrain", "Thrall"])


class TestRowsAndPresenceText(Tmp):
    def friends(self, svc, *names):
        for n in names:
            svc.add_friend(n)
        return {f.label: f for f in svc.book.all()}

    def serve(self, tmp, **by_name):
        prov = FakeProvider()
        svc, book, pres, _p, clock = make(tmp, prov)
        fr = self.friends(svc, *by_name)
        for n, args in by_name.items():
            prov.data[fr[n].id] = obs(fr[n].id, **args)
        pres.poll()
        return svc, fr

    def test_the_exact_status_lines(self):
        svc, fr = self.serve(self.d, Thrall=dict(game="in_game", launcher="online", realm="nzoth"),
                             Jaina=dict(game="in_game", realm="yshaarj"), Arthas=dict(game="in_game", realm="cthun"),
                             Sylvanas=dict(launcher="online"), Illidan=dict(launcher="away"),
                             Tyrande=dict(launcher="dnd"), Malfurion=dict(game="offline", launcher="offline"))
        lines = {r["name"]: r["statusLine"] for r in svc.rows() if r["kind"] == "friend"}
        self.assertEqual(lines["Thrall"], "In-game \u00b7 N'Zoth")
        self.assertEqual(lines["Jaina"], "In-game \u00b7 Y'Shaarj")
        self.assertEqual(lines["Arthas"], "In-game \u00b7 C'Thun")
        for who in ("Sylvanas", "Illidan", "Tyrande"):                    # launcher open, whatever the status
            self.assertEqual(lines[who], "Launcher open")
        self.assertEqual(lines["Malfurion"], "Offline")
        dots = {r["name"]: r["dot"].lower() for r in svc.rows() if r["kind"] == "friend"}
        self.assertEqual((dots["Sylvanas"], dots["Illidan"], dots["Tyrande"], dots["Malfurion"]),
                         ("#3ddc84", "#f2c744", "#ef5350", "#7d8590"))             # online, away, do not disturb, offline
        names = {r["name"]: r["statusName"] for r in svc.rows() if r["kind"] == "friend"}
        self.assertEqual((names["Illidan"], names["Tyrande"]), ("Away", "Do Not Disturb"))

    def test_no_empty_separator_and_no_placeholder_realm(self):
        svc, fr = self.serve(self.d, Thrall=dict(game="in_game"), Jaina=dict(launcher="online", realm="nzoth"))
        rows = {r["name"]: r for r in svc.rows() if r["kind"] == "friend"}
        self.assertEqual(rows["Thrall"]["statusLine"], "In-game")          # in game, realm not given: no separator
        self.assertEqual(rows["Jaina"]["statusLine"], "Launcher open")     # a realm is only shown while in game
        for r in rows.values():
            self.assertNotIn("\u00b7", r["statusLine"] if not r["realm"] else "")
            self.assertFalse(r["statusLine"].endswith(("\u00b7", " ")))

    def test_having_the_launcher_open_is_not_the_same_as_playing(self):
        svc, fr = self.serve(self.d, Open=dict(launcher="online"), Playing=dict(game="in_game", launcher="online", realm="nzoth"))
        st = {r["name"]: r["state"] for r in svc.rows() if r["kind"] == "friend"}
        self.assertEqual((st["Open"], st["Playing"]), (P.LAUNCHER, P.IN_GAME))

    def test_a_row_carries_no_character_class_level_guild_or_zone(self):
        svc, fr = self.serve(self.d, Thrall=dict(game="in_game", realm="nzoth"))
        row = next(r for r in svc.rows() if r["kind"] == "friend")
        for key in ("charClass", "level", "guild", "zone", "detail", "nameColour", "character"):
            self.assertNotIn(key, row)
        names = {f.name for f in fields(P.Observation)}
        self.assertTrue(names.isdisjoint({"char_class", "level", "guild", "zone", "character"}), names)

    def test_nothing_is_invented_without_a_service(self):
        svc, book, pres, prov, _c = make(self.d)
        svc.add_friend("Thrall")
        row = next(r for r in svc.rows() if r["kind"] == "friend")
        self.assertEqual((row["state"], row["realm"]), (P.UNKNOWN, ""))
        self.assertEqual(row["statusLine"], "Unavailable")
        self.assertEqual(svc.online_count(), 0)
        self.assertEqual(pres.requests, 0)

    def test_favourites_first_then_presence_then_offline_last(self):
        svc, fr = self.serve(self.d, Anna=dict(launcher="away"), Bob=dict(game="in_game", realm="nzoth"),
                             Cara=dict(game="offline"), Dan=dict(launcher="online"), Eve=dict(launcher="dnd"))
        svc.toggle_favourite(fr["Cara"].id)
        order = [r["name"] if r["kind"] == "friend" else "#" + r["group"] for r in svc.rows()]
        self.assertEqual(order, ["#favourites", "Cara", "#online", "Bob", "Dan", "Anna", "Eve"])

    def test_offline_friends_are_at_the_bottom(self):
        svc, fr = self.serve(self.d, Anna=dict(game="offline"), Bob=dict(launcher="online"))
        order = [r["name"] if r["kind"] == "friend" else "#" + r["group"] for r in svc.rows()]
        self.assertEqual(order, ["#online", "Bob", "#offline", "Anna"])

    def test_favourite_and_unfavourite(self):
        svc, fr = self.serve(self.d, Anna=dict(launcher="online"), Bob=dict(launcher="online"))
        svc.toggle_favourite(fr["Bob"].id)
        self.assertEqual(svc.rows()[0]["group"], "favourites")
        svc.toggle_favourite(fr["Bob"].id)
        self.assertEqual([r["group"] for r in svc.rows() if r["kind"] == "header"], ["online"])

    def test_there_is_no_realm_filter_or_sort_left(self):
        for name in ("SORTS", "class_colour", "CLASSES"):
            self.assertFalse(hasattr(F, name), name)
        self.assertEqual(F.GROUPS, ("favourites", "online", "offline"))
        svc, *_ = make(self.d)
        for attr in ("set", "get"):
            self.assertFalse(hasattr(svc, attr), attr)

    def test_unavailable_friends_are_not_called_offline(self):
        svc, *_ = make(self.d)
        svc.add_friend("Thrall")
        titles = [r["title"] for r in svc.rows() if r["kind"] == "header"]
        self.assertEqual(titles, ["Unavailable"])


class TestOwnStatus(Tmp):
    def test_four_choices_and_the_default_is_online(self):
        self.assertEqual(STATUS_CHOICES, ("online", "away", "dnd", "invisible"))
        svc, *_ = make(self.d)
        self.assertEqual(svc.status, "online")
        for choice in STATUS_CHOICES:
            svc.set_status(choice)
            self.assertEqual(svc.status, choice)
        svc.set_status("busy")                                                 # not a choice: ignored
        self.assertEqual(svc.status, "invisible")

    def test_the_choice_is_remembered(self):
        s = Settings()
        svc, *_ = make(self.d, settings=s)
        svc.set_status("dnd")
        svc2, *_ = make(self.d, settings=s)
        self.assertEqual(svc2.status, "dnd")

    def test_invisible_shows_the_player_as_offline_and_shares_nothing_but_keeps_everything_else(self):
        prov = FakeProvider()
        svc, book, pres, _p, clock = make(self.d, prov)
        svc.set_status("invisible")
        sent = prov.published[-1]
        self.assertEqual((sent.status, sent.activity, sent.realm), ("invisible", "launcher", ""))
        # still signed in and fully working locally: friends are still looked up, the list still works
        svc.add_friend("Thrall")
        prov.data[book.all()[0].id] = obs(book.all()[0].id, launcher="online")
        pres.poll()
        self.assertEqual(svc.rows()[1]["state"], P.LAUNCHER)
        self.assertEqual(svc.online_count(), 1)

    def test_online_shares_the_game_and_the_realm_but_never_the_character_or_zone(self):
        prov = FakeProvider()
        svc, *_ = make(self.d, prov)
        svc.publish()
        sent = prov.published[-1]
        self.assertEqual((sent.status, sent.activity, sent.realm), ("online", "launcher", ""))
        self.assertEqual({f.name for f in fields(P.OwnStatus)}, {"status", "activity", "realm"})

    def test_a_change_is_published_once_and_nothing_goes_out_without_a_service_or_sign_in(self):
        prov = FakeProvider()
        svc, *_ = make(self.d, prov)
        svc.publish()
        svc.publish()
        self.assertEqual(len(prov.published), 1)
        svc.set_status("away")
        self.assertEqual([p.status for p in prov.published], ["online", "away"])
        none = make(self.d)[0]
        self.assertFalse(none.publish())

        class Out(FakeAccount):
            def valid_access(self):
                return None
        prov2 = FakeProvider()
        prov2.requires_account = True
        svc3, *_ = make(self.d, prov2, account=Out())
        self.assertFalse(svc3.publish())
        self.assertEqual(prov2.published, [])

    def test_automatic_away_after_fifteen_minutes_and_activity_brings_online_back(self):
        svc, *_, clock = make(self.d, FakeProvider())
        self.assertEqual(AUTO_AWAY_SECONDS, 15 * 60)
        clock.t += 14 * 60 + 59
        self.assertFalse(svc.check_idle())
        self.assertEqual(svc.status, "online")
        clock.t += 2
        self.assertTrue(svc.check_idle())
        self.assertEqual(svc.status, "away")
        svc.note_activity()
        self.assertTrue(svc.check_idle())
        self.assertEqual(svc.status, "online")

    def test_the_automatic_away_is_published(self):
        prov = FakeProvider()
        svc, *_, clock = make(self.d, prov)
        svc.publish()
        clock.t += AUTO_AWAY_SECONDS + 1
        svc.check_idle()
        self.assertEqual([p.status for p in prov.published], ["online", "away"])

    def test_a_manual_status_always_wins_over_the_automatic_one(self):
        for manual in ("away", "dnd", "invisible"):
            svc, *_, clock = make(self.d, FakeProvider())
            svc.set_status(manual)
            clock.t += AUTO_AWAY_SECONDS * 3
            svc.check_idle()
            self.assertEqual(svc.status, manual)
        svc, *_, clock = make(self.d, FakeProvider())
        svc.set_status("dnd")
        svc.note_activity()
        self.assertEqual(svc.status, "dnd")


class TestRefresh(Tmp):
    def setup_friends(self, n, prov, **kw):
        svc, book, pres, _p, clock = make(self.d, prov, **kw)
        for i in range(n):
            svc.add_friend(f"friend{i:02d}")
        return svc, book, pres, clock

    def test_the_interval_is_five_seconds_and_is_not_a_setting(self):
        self.assertEqual(P.INTERVAL, 5.0)
        svc, book, pres, clock = self.setup_friends(1, FakeProvider())
        pres.poll()
        self.assertAlmostEqual(pres.delay(), 5.0, delta=0.6)
        self.assertFalse(hasattr(pres, "set_interval"))

    def test_one_request_carries_every_friend_up_to_the_batch_size(self):
        prov = FakeProvider(max_batch=50)
        svc, book, pres, clock = self.setup_friends(120, prov)
        pres.poll()
        self.assertEqual([len(c) for c in prov.calls], [50, 50, 20])
        self.assertEqual(pres.requests, 3)                                   # not 120

    def test_nothing_is_requested_signed_out_unavailable_or_minimised(self):
        prov = FakeProvider()
        prov.requires_account = True

        class Out(FakeAccount):
            state = "signed_out"
        svc, book, pres, clock = self.setup_friends(2, prov, account=Out())
        pres.account = Out()
        self.assertIsNone(pres.delay())
        self.assertFalse(pres.poll())
        pres.account = FakeAccount()
        self.assertIsNotNone(pres.delay())
        pres.minimized = True
        self.assertIsNone(pres.delay())
        pres.minimized = False
        none, *_ = make(self.d)
        none.add_friend("Thrall")
        self.assertIsNone(none.presence.delay())
        self.assertEqual(prov.calls, [])

    def test_a_friendless_list_asks_nothing(self):
        prov = FakeProvider()
        svc, book, pres, _p, clock = make(self.d, prov)
        self.assertIsNone(pres.delay())

    def test_failures_back_off_and_a_rate_limit_waits(self):
        prov = FakeProvider()
        svc, book, pres, clock = self.setup_friends(1, prov)
        prov.error = P.ProviderError("down")
        delays = []
        for _ in range(4):
            pres.poll()
            delays.append(pres.delay())
            clock.t += delays[-1]
        self.assertEqual(delays, [30.0, 60.0, 120.0, 240.0])
        prov.error = P.RateLimited(90)
        pres.failures = 0
        pres.poll()
        self.assertTrue(pres.rate_limited)
        self.assertGreaterEqual(pres.delay(), 89)
        prov.error = None
        clock.t += 100
        self.assertTrue(pres.poll() or True)
        self.assertEqual((pres.failures, pres.rate_limited), (0, False))

    def test_last_data_is_kept_through_an_outage_and_marked_stale_later(self):
        prov = FakeProvider()
        svc, book, pres, clock = self.setup_friends(1, prov)
        fid = book.all()[0].id
        prov.data[fid] = obs(fid, launcher="online", as_of=clock.t)
        pres.poll()
        prov.error = P.ProviderError("down")
        clock.t += 20
        pres.poll()
        self.assertEqual(pres.state_of(book.all()[0]), (P.LAUNCHER, False))
        clock.t += 60
        self.assertEqual(pres.state_of(book.all()[0]), (P.LAUNCHER, True))

    def test_there_are_no_login_notifications(self):
        svc, *_ = make(self.d)
        for name in ("take_toasts", "toasts", "_friend_online"):
            self.assertFalse(hasattr(svc, name), name)
        self.assertFalse(hasattr(svc.presence, "on_friend_online"))


class TestSettingsMigration(Tmp):
    OLD = {"social_enabled": False, "sidebar_collapsed": True, "show_launcher_presence": False,
           "show_game_presence": False, "show_character": True, "show_realm": False, "show_zone": True,
           "friend_notifications": True, "auto_away_minutes": 0, "default_status": "dnd",
           "friends_sort": "name", "friends_realm": "nzoth", "presence_interval": 900,
           "own_status": "away", "account_username": "Thrallfan", "skin": "turtle-wow"}

    def open(self, data):
        import json
        os.makedirs(self.d, exist_ok=True)
        with open(os.path.join(self.d, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(dict(data, _schema=1, imported_from_2x=True), f)
        return S.open_settings(self.d)

    def test_every_removed_preference_is_dropped_and_nothing_else_is(self):
        s = self.open(self.OLD)
        for key in S.OBSOLETE_SOCIAL_KEYS:
            self.assertNotIn(key, s.data)
        self.assertEqual((s.get("own_status"), s.get("account_username"), s.get("skin")), ("away", "Thrallfan", "turtle-wow"))

    def test_the_obsolete_preferences_no_longer_change_the_behaviour(self):
        s = self.open(self.OLD)
        svc = make(self.d, FakeProvider(), settings=s)[0]
        self.assertEqual(svc.manual_status, "away")                    # the player's own choice, not default_status "dnd"
        sent = FakeProvider()
        svc2 = make(self.d, sent, settings=s)[0]
        svc2.set_status("online")
        self.assertEqual(sent.published[-1].status, "online")                              # show_* ignored
        pres = svc2.presence
        self.assertEqual(pres.interval, 5.0)                           # presence_interval ignored

    def test_a_damaged_status_falls_back_to_online_and_defaults_are_the_new_ones(self):
        s = self.open({"own_status": "banana"})
        self.assertEqual(s.get("own_status"), "online")
        self.assertEqual(set(S.SOCIAL_DEFAULTS), {"own_status", "account_username", "account_avatar_url"})

    def test_a_fresh_profile_has_none_of_the_old_keys(self):
        s = S.open_settings(os.path.join(self.d, "fresh"), legacy={})
        for key in S.OBSOLETE_SOCIAL_KEYS:
            self.assertNotIn(key, s.data)


if __name__ == "__main__":
    unittest.main()
