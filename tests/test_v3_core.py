"""3.0 core: task manager, stores and the news service (no Qt, no network)."""

import json
import os
import queue
import tempfile
import threading
import time
import unittest

from equpdater.core.news_service import (NewsService, body_to_html, is_new,
                                         BODY_TTL)
from equpdater.core.store import JsonStore
from equpdater.core.tasks import Cancel, TaskManager


class Pump:
    """Delivers results on the test thread, like the GUI thread would."""

    def __init__(self):
        self.q = queue.Queue()

    def __call__(self, fn):
        self.q.put(fn)

    def drain(self, tasks, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.q.get(timeout=0.02)()
            except queue.Empty:
                if tasks.active_count() == 0 and self.q.empty():
                    return
        raise AssertionError("tasks did not finish")


class TestTasks(unittest.TestCase):
    def test_same_key_runs_once_and_both_callers_hear(self):
        pump, gate, runs, heard = Pump(), threading.Event(), [], []
        tm = TaskManager(2, pump)

        def slow():
            runs.append(1)
            gate.wait(2)
            return 7
        self.assertTrue(tm.submit("k", slow, on_done=heard.append))
        self.assertFalse(tm.submit("k", slow, on_done=heard.append))
        gate.set()
        pump.drain(tm)
        self.assertEqual((len(runs), heard), (1, [7, 7]))
        self.assertEqual(tm.deduped_count, 1)

    def test_error_is_delivered_and_a_cancelled_job_is_silent(self):
        pump, errs, done = Pump(), [], []
        tm = TaskManager(2, pump)
        tm.submit("bad", lambda: 1 / 0, on_error=errs.append)
        started = threading.Event()

        def long(cancel: Cancel):
            started.set()
            while not cancel.cancelled:
                time.sleep(0.01)
            return "late"
        tm.submit("c", long, on_done=done.append, wants_cancel=True)
        started.wait(2)
        tm.cancel("c")
        pump.drain(tm)
        self.assertIsInstance(errs[0], ZeroDivisionError)
        self.assertEqual(done, [])

    def test_pool_is_bounded(self):
        tm = TaskManager(2)
        peak, now, lock = [0], [0], threading.Lock()

        def work():
            with lock:
                now[0] += 1
                peak[0] = max(peak[0], now[0])
            time.sleep(0.05)
            with lock:
                now[0] -= 1
        for i in range(8):
            tm.submit(i, work)
        while tm.active_count():
            time.sleep(0.01)
        self.assertLessEqual(peak[0], 2)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "s.json")

    def test_debounce_one_write_and_no_write_when_unchanged(self):
        s = JsonStore(self.p, {"a": 1}, delay=0.05)
        for i in range(10):
            s.set("a", i + 2)
        time.sleep(0.25)
        self.assertEqual(s.writes, 1)
        s.set("a", 11)          # same value as last: nothing to write
        time.sleep(0.15)
        self.assertEqual(s.writes, 1)

    def test_corrupt_file_is_kept_and_defaults_used(self):
        with open(self.p, "w") as f:
            f.write("{not json")
        s = JsonStore(self.p, {"a": 1})
        self.assertEqual(s.get("a"), 1)
        self.assertTrue([n for n in os.listdir(self.d) if ".corrupt-" in n])

    def test_migration_walks_forward_and_backs_up(self):
        with open(self.p, "w") as f:
            json.dump({"_schema": 1, "old": 5}, f)
        s = JsonStore(self.p, {}, schema=3, delay=0,
                      migrations={1: lambda d: {**d, "x": d["old"]},
                                  2: lambda d: {**d, "y": d["x"] + 1}})
        self.assertEqual((s.get("x"), s.get("y")), (5, 6))
        self.assertTrue(os.path.exists(self.p + ".v1.bak"))


def entry(i, day, kind="normal"):
    return {"id": str(i), "title": f"T{i}", "author": "A",
            "date": f"2026-11-{day:02d}T10:00:00+00:00",
            "url": f"https://octowow.st/forum/viewtopic.php?t={i}",
            "body": "", "html": "", "kind": kind}


class FakeFetch:
    def __init__(self):
        self.requests = []
        self.pages = {2: {0: ([entry(5, 20), entry(4, 18)], 2),
                          2: ([entry(3, 10), entry(2, 5)], None)},
                      4: {0: ([entry(9, 21)], None)}}
        self.fail = False
        self.gate = None

    def listing(self, forum, start=0):
        self.requests.append(("list", forum, start))
        if self.gate:
            self.gate.wait(3)
        if self.fail:
            raise RuntimeError("down")
        return self.pages[forum][start]

    def first_post(self, e):
        self.requests.append(("post", e["id"]))
        if self.gate:
            self.gate.wait(3)
        return {"body": "see here " * 3, "links": [[4, 8, "https://x.test/"]],
                "author": "A", "date": e["date"]}

    def feed(self):
        self.requests.append(("feed",))
        return []


class Listener:
    def __init__(self):
        self.events = []

    def list_changed(self, key, first):
        self.events.append(("list", key, first))

    def entries_changed(self, key, ids):
        self.events.append(("entries", key, tuple(ids)))

    def state_changed(self, key):
        self.events.append(("state", key))


class TestNews(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.pump, self.fetch, self.lst = Pump(), FakeFetch(), Listener()
        self.clock = [time.mktime((2026, 11, 22, 12, 0, 0, 0, 0, 0))]
        self.mk()

    def mk(self):
        self.tasks = TaskManager(4, self.pump)
        self.store = JsonStore(os.path.join(self.d, "news.json"),
                               {"sections": {}}, delay=0)
        self.seen = JsonStore(os.path.join(self.d, "settings.json"),
                              {"news_seen": {}}, delay=0)
        self.svc = NewsService(self.store, self.tasks, self.fetch, self.lst,
                               now=lambda: self.clock[0], seen_store=self.seen)

    def settle(self):
        self.pump.drain(self.tasks)

    def test_first_run_lists_prefetches_latest_and_opens_it(self):
        self.svc.start()
        self.settle()
        ids = self.svc.visible_ids("announcements")
        self.assertEqual(ids[0], "5")
        e = self.svc.entry("announcements", "5")
        self.assertTrue(e["expanded"] and e["hasBody"])
        self.assertIn('<a href="https://x.test/">here</a>', e["bodyHtml"])
        # only the latest post was prefetched; older ones cost nothing yet
        posts = [r for r in self.fetch.requests if r[0] == "post"]
        self.assertIn(("post", "5"), posts)
        self.assertNotIn(("post", "4"), posts)

    def test_second_launch_shows_cache_with_no_request(self):
        self.svc.start()
        self.settle()
        self.store.flush()
        n = len(self.fetch.requests)
        self.mk()                              # a new process, same files
        self.assertTrue(self.svc.entry("announcements", "5")["hasBody"])
        self.assertEqual(len(self.fetch.requests), n)   # constructing asked nothing

    def test_expanding_a_cached_post_makes_no_request(self):
        self.svc.start()
        self.settle()
        self.svc.toggle("announcements", "5")      # close
        self.svc.toggle("announcements", "5")      # open again
        self.settle()
        self.assertEqual([r for r in self.fetch.requests if r == ("post", "5")],
                         [("post", "5")])

    def test_opening_an_uncached_post_fetches_once_even_if_clicked_twice(self):
        self.svc.start()
        self.settle()
        self.fetch.gate = threading.Event()
        self.svc.toggle("announcements", "4")
        self.svc.toggle("announcements", "4")      # closes
        self.svc.toggle("announcements", "4")      # opens while still loading
        self.fetch.gate.set()
        self.settle()
        self.assertEqual(self.fetch.requests.count(("post", "4")), 1)
        self.assertTrue(self.svc.entry("announcements", "4")["hasBody"])

    def test_load_more_is_incremental_and_reports_the_insertion_point(self):
        self.svc.sections["announcements"].shown = 2
        self.svc.start()
        self.settle()
        self.lst.events.clear()
        self.svc.load_more("announcements")        # page 2 is read on demand
        self.settle()
        self.assertIn(("list", "announcements", 2), self.lst.events)
        self.assertEqual(self.svc.visible_ids("announcements"), ["5", "4", "3", "2"])

    def test_failed_refresh_keeps_cache_and_reports_error(self):
        self.svc.start()
        self.settle()
        self.fetch.fail = True
        self.svc.refresh("announcements")
        self.settle()
        sec = self.svc.sections["announcements"]
        self.assertTrue(sec.error)
        self.assertEqual(self.svc.visible_ids("announcements")[0], "5")

    def test_refresh_updates_in_place_and_does_not_reopen_closed_posts(self):
        self.svc.start()
        self.settle()
        self.svc.toggle("announcements", "5")          # player closes it
        self.fetch.pages[2][0] = ([dict(entry(5, 20), title="Edited"),
                                   entry(4, 18)], 2)
        self.lst.events.clear()
        self.svc.refresh("announcements")
        self.settle()
        self.assertFalse(self.svc.entry("announcements", "5")["expanded"])
        self.assertEqual(self.svc.entry("announcements", "5")["title"], "Edited")
        self.assertIn(("entries", "announcements", ("5",)), self.lst.events)
        self.assertFalse([e for e in self.lst.events if e[0] == "list"])

    def test_a_late_reply_after_a_newer_refresh_is_ignored(self):
        sec = self.svc.sections["announcements"]
        self.svc.start()
        sec.epoch += 1                      # what a reset does
        self.settle()
        self.assertEqual(sec.order, [])

    def test_pinned_older_than_oldest_read_is_held_back(self):
        self.fetch.pages[2][0] = ([entry(5, 20), entry(4, 18),
                                   entry(1, 1, "sticky")], 2)
        self.svc.start()
        self.settle()
        self.assertNotIn("1", self.svc.visible_ids("announcements"))

    def test_stale_cached_body_is_refreshed_quietly(self):
        self.svc.start()
        self.settle()
        self.clock[0] += BODY_TTL + 5
        n = self.fetch.requests.count(("post", "5"))
        self.svc.refresh("announcements")
        self.settle()
        self.assertEqual(self.fetch.requests.count(("post", "5")), n + 1)
        self.assertTrue(self.svc.entry("announcements", "5")["hasBody"])

    def test_new_marks(self):
        now = self.clock[0]
        self.assertTrue(is_new("2026-11-21T10:00:00+00:00", None, now))
        self.assertFalse(is_new("2026-11-01T10:00:00+00:00", None, now))
        self.assertTrue(is_new("2026-11-01T10:00:00+00:00",
                               now - 40 * 86400, now))    # newer than last visit
        self.assertFalse(is_new("garbage", None, now))


class TestBodyHtml(unittest.TestCase):
    def test_escapes_and_drops_unsafe_links(self):
        out = body_to_html("a <b> &\nhere", [[0, 1, "javascript:alert(1)"],
                                              [8, 12, "https://ok.test/?a=1&b=2"]])
        self.assertNotIn("javascript", out)
        self.assertIn("&lt;b&gt;", out)
        self.assertIn("<br>", out)
        self.assertIn('href="https://ok.test/?a=1&amp;b=2"', out)

    def test_overlapping_and_out_of_range_links_do_not_duplicate_text(self):
        body = "hello world"
        out = body_to_html(body, [[0, 5, "https://a.test/"], [3, 8, "https://b.test/"],
                                  [50, 60, "https://c.test/"]])
        self.assertEqual(out.count("hello"), 1)
        self.assertEqual(out.count("world"), 1)


if __name__ == "__main__":
    unittest.main()
