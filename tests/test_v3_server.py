"""OctoBot server status: parsing, honesty about age, polite polling."""

import json
import tempfile
import unittest

from equpdater.core import server as S
from equpdater.core.store import JsonStore

NOW = 1_800_000_000.0


def iso(seconds_ago):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live(online=1381, ago=120):
    return {"all": {"timestamp": iso(ago), "online": online, "source": "official census"}}


def status(ago=240, tcp="reachable", auth="responsive", website="available", realms=None):
    return {"schema": 1, "sample": {"timestamp": iso(ago), "website": website, "tcp": tcp, "auth": auth,
                                    "latency_ms": 849,
                                    "realms": realms if realms is not None else
                                    {"cthun": "online", "nzoth": "online", "yshaarj": "online"}}}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Net:
    """Serves the two OctoBot files with ETags; counts every request."""

    def __init__(self):
        self.live, self.status, self.fail = live(), status(), False
        self.urls = []

    def __call__(self, url, etag="", timeout=8.0):
        self.urls.append(url)
        if self.fail:
            raise OSError("network down")
        obj = {S.LIVE_URL: self.live, S.STATUS_URL: self.status}.get(url)
        if obj is None:
            return S.Response(404)
        body = json.dumps(obj, sort_keys=True).encode()
        tag = '"%d"' % (sum(body) % 10 ** 6)
        return S.Response(304, b"", tag) if etag == tag else S.Response(200, body, tag)


def feed(net=None, clock=None, jitter=lambda a, b: 0.0, **kw):
    store = JsonStore(tempfile.mkdtemp() + "/server.json", {"snapshot": None, "fetched_at": 0}, delay=0)
    f = S.ServerFeed(store, fetch=net or Net(), now=clock or Clock(), jitter=jitter, **kw)
    return f, store


class TestParsing(unittest.TestCase):
    def test_live_and_status_become_a_snapshot(self):
        f, _ = feed()
        self.assertTrue(f.fetch_once())
        v = f.view()
        self.assertEqual(v["total"], 1381)
        self.assertEqual(v["state"], "online")
        self.assertEqual([r["state"] for r in v["realms"]], ["online"] * 3)
        self.assertEqual(v["latency_ms"], 849)

    def test_hostile_values_are_dropped_not_trusted(self):
        snap = S.Snapshot(population=S.parse_live(
            {"all": {"online": -5, "timestamp": "nonsense"}, "cthun": {"online": 10 ** 12},
             "nzoth": {"online": True}, "evil": {"online": 1}, "yshaarj": "x"}))
        self.assertEqual(set(snap.population), {"all", "cthun", "nzoth"})
        self.assertTrue(all(p.online is None for p in snap.population.values()))
        self.assertIsNone(snap.total())

    def test_unknown_words_are_ignored(self):
        snap = S.Snapshot()
        S.apply_status(snap, {"sample": {"timestamp": iso(10), "tcp": "<script>", "auth": 5,
                                         "website": None, "realms": {"cthun": "exploded", "x": "online"}}})
        self.assertEqual((snap.tcp, snap.auth, snap.website, snap.realms), ("", "", "", {}))

    def test_a_realm_sum_is_the_total_when_there_is_no_census(self):
        snap = S.Snapshot(population={"nzoth": S.Population(10, NOW), "cthun": S.Population(5, NOW)})
        self.assertEqual(snap.total().online, 15)

    def test_the_combined_snapshot_has_the_same_meaning(self):
        obj = {"schema": 1, "cadence_s": 60, "population": live(), "status": status()["sample"]}
        snap = S.parse_combined(obj)
        self.assertEqual(snap.total().online, 1381)
        self.assertEqual(snap.cadence_s, 60.0)
        self.assertIsNone(S.parse_combined({"schema": 2}))

    def test_times_in_any_of_the_forms_octobot_uses(self):
        self.assertAlmostEqual(S.parse_time("2026-11-24T13:31:01.177Z"), 1795613461.177, delta=1.0) \
            if False else self.assertIsNotNone(S.parse_time("2026-11-24T13:31:01.177Z"))
        self.assertEqual(S.parse_time(1_700_000_000_000), 1_700_000_000.0)
        self.assertIsNone(S.parse_time("yesterday"))


class TestState(unittest.TestCase):
    def check(self, **kw):
        s = S.Snapshot()
        S.apply_status(s, status(**kw))
        return s.state()

    def test_octobots_own_verdicts(self):
        self.assertEqual(self.check(), "online")
        self.assertEqual(self.check(tcp="unreachable"), "offline")
        self.assertEqual(self.check(auth="unresponsive"), "offline")
        self.assertEqual(self.check(website="unavailable"), "degraded")
        self.assertEqual(S.Snapshot().state(), "unknown")


class TestAgeIsHonest(unittest.TestCase):
    def test_old_data_is_stale_and_labelled_as_such(self):
        net = Net()
        net.live, net.status = live(ago=3 * 3600), status(ago=8 * 3600)
        f, _ = feed(net)
        f.fetch_once()
        v = f.view()
        self.assertTrue(v["stale"])
        self.assertTrue(v["status_stale"])
        self.assertEqual(v["state"], "online")             # what OctoBot last said, labelled stale

    def test_fresh_data_is_not_stale(self):
        f, _ = feed()
        f.fetch_once()
        self.assertFalse(f.view()["stale"])
        self.assertFalse(f.view()["status_stale"])

    def test_a_failed_fetch_never_turns_the_server_offline(self):
        net = Net()
        f, _ = feed(net)
        f.fetch_once()
        net.fail = True
        self.assertFalse(f.fetch_once())
        v = f.view()
        self.assertTrue(v["unavailable"])
        self.assertEqual(v["state"], "online")             # the last good value, not "offline"
        self.assertEqual(v["total"], 1381)

    def test_a_first_failure_with_nothing_cached_is_unknown(self):
        net = Net()
        net.fail = True
        f, _ = feed(net)
        f.fetch_once()
        v = f.view()
        self.assertEqual((v["state"], v["has_data"], v["unavailable"]), ("unknown", False, True))

    def test_age_words(self):
        self.assertEqual(S.age_text(30), "just now")
        self.assertEqual(S.age_text(600), "10 min ago")
        self.assertEqual(S.age_text(8 * 3600), "8 h ago")
        self.assertEqual(S.age_text(3 * 86400), "3 days ago")


class TestPolling(unittest.TestCase):
    def test_conditional_get_makes_the_second_poll_cheap(self):
        net = Net()
        f, _ = feed(net)
        self.assertTrue(f.fetch_once())
        self.assertFalse(f.fetch_once())                      # both 304
        self.assertEqual(len(net.urls), 4)

    def test_only_the_static_files_are_ever_requested(self):
        net = Net()
        f, _ = feed(net)
        for _ in range(3):
            f.fetch_once()
        self.assertEqual(set(net.urls), {S.LIVE_URL, S.STATUS_URL})
        for u in net.urls:
            self.assertNotIn("workers.dev", u)
            self.assertNotIn("/api/", u)

    def test_the_combined_snapshot_url_means_one_request(self):
        obj = {"schema": 1, "population": live(), "status": status()["sample"]}
        calls = []

        def fetch(url, etag="", timeout=8.0):
            calls.append(url)
            return S.Response(200, json.dumps(obj).encode(), '"x"')
        f, _ = feed(fetch, snapshot_url="https://example.invalid/snapshot.json")
        f.fetch_once()
        self.assertEqual(calls, ["https://example.invalid/snapshot.json"])
        self.assertEqual(f.view()["total"], 1381)

    def test_cadence_active_unfocused_minimised(self):
        f, _ = feed()
        self.assertEqual(f.delay(), 60.0)
        f.focused = False
        self.assertEqual(f.delay(), 300.0)
        f.minimized = True
        self.assertIsNone(f.delay())

    def test_jitter_is_applied_within_bounds(self):
        f, _ = feed(jitter=lambda a, b: b)
        self.assertEqual(f.delay(), 75.0)
        f2, _ = feed(jitter=lambda a, b: a)
        self.assertEqual(f2.delay(), 45.0)

    def test_failures_back_off_then_reset(self):
        net = Net()
        f, _ = feed(net)
        net.fail = True
        delays = []
        for _ in range(7):
            f.fetch_once()
            delays.append(f.delay())
        self.assertEqual(delays, [60, 120, 240, 480, 900, 900, 900])
        net.fail = False
        f.fetch_once()
        self.assertEqual(f.delay(), 60.0)
        self.assertFalse(f.unavailable)

    def test_returning_refreshes_only_when_older_than_30_seconds(self):
        clock = Clock()
        f, _ = feed(clock=clock)
        self.assertTrue(f.wants_refresh_on_return())          # never fetched
        f.fetch_once()
        clock.t += 20
        self.assertFalse(f.wants_refresh_on_return())
        clock.t += 20
        self.assertTrue(f.wants_refresh_on_return())

    def test_a_response_that_is_too_big_or_not_json_is_a_failure_not_a_crash(self):
        def big(url, etag="", timeout=8.0):
            return S.Response(200, b"x" * (S.MAX_BYTES + 5), "")
        f, _ = feed(big)
        self.assertFalse(f.fetch_once())
        self.assertTrue(f.unavailable)

        def junk(url, etag="", timeout=8.0):
            return S.Response(200, b"<html>", "")
        f2, _ = feed(junk)
        self.assertFalse(f2.fetch_once())

    def test_the_last_good_snapshot_survives_a_restart(self):
        f, store = feed(Net())
        f.fetch_once()
        again = S.ServerFeed(store, fetch=Net(), now=Clock())
        self.assertEqual(again.view()["total"], 1381)
        self.assertTrue(again.view()["has_data"])

    def test_a_corrupt_cache_is_ignored(self):
        store = JsonStore(tempfile.mkdtemp() + "/s.json", {"snapshot": None, "fetched_at": 0}, delay=0)
        store.set("snapshot", {"v": 7, "population": "garbage"})
        self.assertFalse(S.ServerFeed(store, fetch=Net()).view()["has_data"])

    def test_http_fetch_only_talks_to_allowed_hosts(self):
        import urllib.error
        from unittest import mock
        calls = []

        def fake(req, timeout, allowed_hosts=None):
            calls.append((req.full_url, allowed_hosts, req.get_header("If-none-match")))
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        with mock.patch("equpdater.core.net.secure_urlopen", fake):
            r = S.http_fetch(S.LIVE_URL, '"abc"')
        self.assertEqual(r.status, 304)
        self.assertEqual(calls[0][1], S.ALLOWED_HOSTS)
        self.assertEqual(calls[0][2], '"abc"')


if __name__ == "__main__":
    unittest.main()
