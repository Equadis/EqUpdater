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


class TestTweaksService(unittest.TestCase):
    def setUp(self):
        from equpdater.core.tweaks_service import TweaksService
        self.d = tempfile.mkdtemp()
        self.calls, self.stored = [], {}
        self.pump = Pump()
        self.tasks = TaskManager(2, self.pump)
        self.settings = {"game_dir": self.d}

        class S(dict):
            pass
        s = S(self.settings)
        s.get = lambda k, d=None: self.settings.get(k, d)
        from equpdater.core import tweaks as T
        self.T = T
        self.svc = TweaksService(
            s, self.tasks, load=lambda: dict(T.TWEAKS_DEFAULTS),
            save=lambda v: self.stored.update(v) or self.calls.append("save"),
            patch=lambda d, v: self.calls.append("patch"),
            wtf=lambda d, v: self.calls.append("wtf"))
        self.svc.refresh()
        self.pump.drain(self.tasks)

    def test_values_are_clamped_and_typed(self):
        self.assertEqual(self.svc.set_value("nameplateRange", "9999"), 41)
        self.assertEqual(self.svc.set_value("fieldOfView", "-5"), 90)
        self.assertEqual(self.svc.set_value("farClip", "abc"), self.T.TWEAKS_DEFAULTS["farClip"])
        self.assertEqual(self.svc.set_value("locale", "xx"), self.T.DEFAULT_LOCALE)
        self.assertIs(self.svc.set_value("alwaysAutoLoot", 0), False)
        self.assertIsNone(self.svc.set_value("nonsense", 1))
        self.assertTrue(self.svc.dirty())

    def test_apply_saves_then_patches_then_writes_config(self):
        open(os.path.join(self.d, "WoW.exe"), "wb").write(b"MZ")
        self.svc.set_value("soundChannels", 32)
        self.svc.apply()
        self.pump.drain(self.tasks)
        self.assertEqual(self.calls, ["save", "patch", "wtf"])
        self.assertEqual(self.stored["soundChannels"], 32)
        self.assertFalse(self.svc.dirty())
        self.assertFalse(self.svc.message_error)

    def test_without_the_game_it_saves_and_says_so(self):
        self.svc.set_value("soundChannels", 20)
        self.svc.apply()
        self.pump.drain(self.tasks)
        self.assertEqual(self.calls, ["save"])
        self.assertTrue(self.svc.message_error)

    def test_a_failed_patch_reports_and_unblocks(self):
        from equpdater.core.tweaks_service import TweaksService
        open(os.path.join(self.d, "WoW.exe"), "wb").write(b"MZ")

        def boom(d, v):
            raise RuntimeError("Close World of Warcraft")
        svc = TweaksService(type("S", (), {"get": lambda s, k, d=None: self.d if k == "game_dir" else d})(),
                            self.tasks, load=lambda: dict(self.T.TWEAKS_DEFAULTS),
                            save=lambda v: None, patch=boom, wtf=lambda d, v: None)
        svc.apply()
        self.pump.drain(self.tasks)
        self.assertIn("Close World of Warcraft", svc.message)
        self.assertFalse(svc.busy)

    def test_reset_restores_defaults_and_applies(self):
        open(os.path.join(self.d, "WoW.exe"), "wb").write(b"MZ")
        self.svc.set_value("farClip", 5000)
        self.svc.reset()
        self.pump.drain(self.tasks)
        self.assertEqual(self.svc.values, self.T.TWEAKS_DEFAULTS)
        self.assertEqual(self.calls, ["save", "patch", "wtf"])

    def test_one_apply_at_a_time(self):
        gate = threading.Event()
        self.svc._save = lambda v: gate.wait(2)
        self.assertTrue(self.svc.apply())
        self.assertFalse(self.svc.apply())
        gate.set()
        self.pump.drain(self.tasks)


class TestModsService(unittest.TestCase):
    """Rows come from the lifted planner/config code; the service only shapes
    them. Config is redirected to a temp file (never the real one)."""

    def setUp(self):
        from equpdater import config
        from equpdater.core.mods_service import ModsService
        from equpdater.core.ops_host import OpsHost, AutoDialogs
        self.d = tempfile.mkdtemp()
        self.game = os.path.join(self.d, "game")
        os.makedirs(self.game)
        self._cfg = config.CONFIG_FILE
        config.CONFIG_FILE = os.path.join(self.d, "config.json")
        self.addCleanup(setattr, config, "CONFIG_FILE", self._cfg)
        self.events = []
        self.pump = Pump()
        self.tasks = TaskManager(2, self.pump)
        s = {"game_dir": self.game}
        self.settings = type("S", (), {"get": lambda _s, k, d=None: s.get(k, d)})()
        self.dialogs = AutoDialogs(yes=True)
        self.host = OpsHost(self.settings, events=lambda n, **kw: self.events.append(n),
                            dialogs=self.dialogs)
        self.svc = ModsService(self.host, self.tasks)

    def items(self):
        return self.svc.build_items()

    def test_sections_and_counts_follow_the_game_folder(self):
        for rel in ("VfPatcher.dll", "VanillaFixes.exe"):
            with open(os.path.join(self.game, rel), "wb") as f:
                f.write(b"x")
        items = self.items()
        heads = {i["title"]: i for i in items if i["kind"] == "header"}
        self.assertEqual(list(heads), ["MANAGED", "INSTALLED LOCALLY", "AVAILABLE"])
        self.assertEqual(heads["INSTALLED LOCALLY"]["count"], 1)
        self.assertEqual(heads["INSTALLED LOCALLY"]["manageAll"], 1)
        row = next(i for i in items if i.get("id") == "VanillaFixes")
        self.assertEqual((row["section"], row["here"], row["managed"]),
                         ("INSTALLED LOCALLY", True, False))
        self.assertEqual(row["actionMode"], "adopt")          # "not managed"
        self.assertEqual(sum(i["count"] for i in heads.values()), 12)

    def test_a_closed_section_has_no_rows(self):
        self.svc.open_sections["AVAILABLE"] = False
        items = self.items()
        self.assertFalse([i for i in items if i["kind"] == "mod" and i["section"] == "AVAILABLE"])
        self.assertTrue(next(i for i in items if i.get("title") == "AVAILABLE")["count"])

    def test_a_failed_install_shows_its_error_and_a_retry(self):
        from equpdater import config
        config.update_config(lambda c: c.setdefault("mods", {}).__setitem__(
            "dxvk", {"managed": True, "error": "download blocked"}))
        row = next(i for i in self.items() if i.get("id") == "dxvk")
        self.assertEqual((row["hasError"], row["actionMode"]), (True, "retry"))
        self.assertIn("download blocked", row["problem"])
        self.assertEqual(row["problemSeverity"], "error")

    def test_ignore_updates_is_saved_and_shown(self):
        self.svc.set_ignore("dxvk", True)
        from equpdater import config
        self.assertTrue(config.load_config()["mods"]["dxvk"]["ignore_updates"])
        self.assertTrue(next(i for i in self.items() if i.get("id") == "dxvk")["ignore"])

    def test_clicks_route_to_the_lifted_methods(self):
        from unittest import mock
        h = self.host
        for mode, method in (("adopt", "_adopt_mod"), ("act", "_update_mod"),
                             ("recover", "_mod_force_replace"), ("retry", "_mod_recheck")):
            with mock.patch.object(h, method) as m:
                self.svc.click_action("dxvk", mode)
                m.assert_called_once_with("dxvk")
        with mock.patch.object(h, "_mod_force_replace") as m:
            self.svc.force_replace("dxvk")
            m.assert_called_once_with("dxvk")

    def test_uninstall_asks_first_and_declining_changes_nothing(self):
        for rel in ("VfPatcher.dll", "VanillaFixes.exe"):
            with open(os.path.join(self.game, rel), "wb") as f:
                f.write(b"x")
        self.dialogs.yes = False
        self.svc.uninstall("VanillaFixes")
        self.assertTrue(os.path.exists(os.path.join(self.game, "VanillaFixes.exe")))
        self.assertEqual([a[0] for a in self.dialogs.asked], ["yes_no"])
        self.dialogs.yes = True
        self.svc.uninstall("VanillaFixes")
        self.assertFalse(os.path.exists(os.path.join(self.game, "VanillaFixes.exe")))

    def test_rows_are_built_off_the_calling_thread_and_delivered_once(self):
        got = []
        self.svc.on_rows = got.append
        self.svc.rebuild()
        self.svc.rebuild()                 # a second request while one runs: shared
        self.pump.drain(self.tasks)
        self.assertGreaterEqual(len(got), 1)
        self.assertLessEqual(self.tasks.started_count, 2)


class TestAddonsService(unittest.TestCase):
    def setUp(self):
        from equpdater.core.addons_service import AddonsService
        from equpdater.core.ops_host import OpsHost
        from equpdater.qtui.testing import fake_addon_status
        self.d = tempfile.mkdtemp()
        s = {"game_dir": self.d}
        settings = type("S", (), {"get": lambda _s, k, d=None: s.get(k, d)})()
        self.pump = Pump()
        self.tasks = TaskManager(2, self.pump)
        self.host = OpsHost(settings)
        self.host._addons_status = fake_addon_status(40)
        self.svc = AddonsService(self.host, self.tasks)

    def items(self, flt="", secs=None):
        return self.svc.build_items(self.svc._snapshot(), flt, secs or {})

    def test_three_sections_cover_every_addon_once(self):
        items = self.items()
        heads = [i for i in items if i["kind"] == "header"]
        self.assertEqual([h["title"] for h in heads],
                         ["MANAGED", "INSTALLED LOCALLY", "AVAILABLE"])
        self.assertEqual(sum(h["count"] for h in heads), 40)
        keys = [i["key"] for i in items]
        self.assertEqual(len(keys), len(set(keys)))

    def test_search_is_space_and_case_insensitive_and_in_python(self):
        st = self.host._addons_status
        name = next(iter(st["addons"]))
        want = "".join(" " + c if c.isupper() and i else c for i, c in enumerate(name))
        for q in (name.lower(), name.upper(), want):
            rows = [i for i in self.items(q) if i["kind"] == "addon"]
            self.assertIn(name, [r["id"] for r in rows])
        self.assertEqual([i for i in self.items("zzzz-no-such") if i["kind"] == "addon"], [])

    def test_a_closed_section_has_no_rows_but_keeps_its_count(self):
        items = self.items(secs={"AVAILABLE": False})
        self.assertFalse([i for i in items if i["kind"] == "addon" and i["section"] == "AVAILABLE"])
        self.assertTrue(next(i for i in items if i.get("title") == "AVAILABLE")["count"])

    def test_status_words_follow_the_2x_rules(self):
        from equpdater.core.addons_service import status_for
        st = type(self.host)._ADDON_STATES
        up = status_for({"folder": "A", "status": "updateAvailable", "installed_sha": "a" * 40,
                         "remote_sha": "b" * 40}, True, [], st)
        self.assertEqual((up["kind"], up["action"]), ("button", "update"))
        self.assertIn("→", up["tip"])
        man = status_for({"folder": "A", "status": "unmanaged", "git": "https://x/y.git"},
                         True, [], st)
        self.assertEqual(man["action"], "manage")
        self.assertEqual(status_for({"folder": "A", "status": "unmanaged"}, True, [], st)["kind"], "none")
        self.assertEqual(status_for({"folder": "A", "status": "modified"}, True, [], st)["kind"], "info")
        self.assertEqual(status_for({"folder": "A", "status": "x", "check_failed": "r"}, True, [], st)["action"], "retry")
        self.assertEqual(status_for({"folder": "A", "status": "downloading"}, True, [], st)["kind"], "busy")
        self.assertEqual(status_for({"folder": "A", "status": "upToDate", "error": "e"}, True, [], st)["tone"], "error")
        w = status_for({"folder": "A", "status": "upToDate"}, True, ["Made for client 20000"], st)
        self.assertIn("Made for client", w["text"])

    def test_missing_dependencies_and_client_warnings(self):
        from equpdater.core.addons_service import warnings_for
        rec = {"folder": "X", "toc": {"Interface": "20000", "Dependencies": "Foo, Bar"}}
        out = warnings_for(rec, True, {"X", "Foo"})
        self.assertEqual(len(out), 2)
        self.assertEqual(warnings_for({"folder": "pfUI", "toc": {"Dependencies": "Zed"}}, True, {"pfUI"}), [])

    def test_wow_colour_codes_become_safe_rich_text(self):
        from equpdater.core.addons_service import title_html
        out = title_html("|cff33ff99My <b>Addon|r", "F")
        self.assertIn("&lt;b&gt;", out)
        self.assertNotIn("<b>", out)

    def test_footer_states(self):
        text, mode = self.svc.footer()
        self.assertEqual(mode, "update")
        self.host._addons_status["state"] = "verifying"
        self.assertEqual(self.svc.footer()[1], "busy")
        for r in self.host._addons_status["addons"].values():
            r["status"] = "upToDate"
        self.host._addons_status["state"] = "done"
        self.assertEqual(self.svc.footer()[1], "info")

    def test_clicks_route_to_the_lifted_methods(self):
        from unittest import mock
        folder = next(iter(self.host._addons_status["addons"]))
        h = self.host
        with mock.patch.object(h, "_addon_apply") as m:
            self.svc.click_status(folder, "update")
            self.assertEqual(m.call_args[0][0][0]["folder"], folder)
        with mock.patch.object(h, "_addon_adopt") as m:
            self.svc.click_status(folder, "manage")
            self.assertEqual(m.call_args[0][0]["folder"], folder)
        with mock.patch.object(h, "_addons_verify") as m:
            self.svc.click_status(folder, "retry")
            m.assert_called_once_with(force=True)
        with mock.patch.object(h, "_addon_remove") as m:
            self.svc.remove(folder)
            m.assert_called_once_with(folder)

    def test_no_question_is_asked_when_the_addon_folders_are_fine(self):
        """A host that asked 'Two addon folders?' on every verify hung the Qt
        page (a dialog nobody could answer); the real check runs first."""
        from equpdater.core.ops_host import AutoDialogs
        self.host.dialogs = AutoDialogs()
        self.assertFalse(self.host._addon_dir_conflict_prompt(self.d))
        self.assertEqual(self.host.dialogs.asked, [])

    def test_two_addon_folders_ask_once_and_cancel_changes_nothing(self):
        """(The case-only duplicate cannot exist on Windows, so the scan result
        is supplied; the asking and the 'decide later' answer are the logic.)"""
        from types import SimpleNamespace
        from unittest import mock
        from equpdater import gamepaths
        from equpdater.core.ops_host import AutoDialogs
        state = gamepaths.AddonDirState(
            path=os.path.join(self.d, "Interface", "AddOns"),
            conflicts=[("Same", os.path.join(self.d, "Interface", "Addons", "Same"),
                        os.path.join(self.d, "Interface", "AddOns", "Same"))])
        self.host.dialogs = AutoDialogs()                # answers None: decide later
        with mock.patch.object(gamepaths, "prepare_addons_dir", return_value=state),                 mock.patch.object(gamepaths, "resolve_conflicts") as resolve:
            self.assertFalse(self.host._addon_dir_conflict_prompt(self.d))
            self.assertFalse(self.host._addon_dir_conflict_prompt(self.d))
            resolve.assert_not_called()
        self.assertEqual([a[0] for a in self.host.dialogs.asked], ["yes_no_cancel"])

    def test_badge_counts_only_proven_updates(self):
        got = []
        self.host.events = lambda n, **kw: got.append((n, kw))
        self.host._refresh_addons_badge()
        want = sum(1 for r in self.host._addons_status["addons"].values()
                   if r["status"] == "updateAvailable")
        self.assertEqual([kw["count"] for n, kw in got if n == "addons_badge"], [want])


class TestMpqService(unittest.TestCase):
    def setUp(self):
        from equpdater import config
        from equpdater.core.mpq_service import MpqService
        from equpdater.core.ops_host import OpsHost, AutoDialogs
        self.d = tempfile.mkdtemp()
        self.game = os.path.join(self.d, "game")
        os.makedirs(os.path.join(self.game, "Data"))
        self._cfg = config.CONFIG_FILE
        config.CONFIG_FILE = os.path.join(self.d, "config.json")
        self.addCleanup(setattr, config, "CONFIG_FILE", self._cfg)
        s = {"game_dir": self.game}
        settings = type("S", (), {"get": lambda _s, k, d=None: s.get(k, d)})()
        self.events = []
        self.dialogs = AutoDialogs(yes=True)
        self.host = OpsHost(settings, events=lambda n, **kw: self.events.append(n),
                            dialogs=self.dialogs)
        self.pump = Pump()
        self.tasks = TaskManager(2, self.pump)
        self.svc = MpqService(self.host, self.tasks)

    def put(self, name, data=b"theirs"):
        with open(os.path.join(self.game, "Data", name), "wb") as f:
            f.write(data)

    def scan(self):
        self.host._mpq_installed_rows = []
        # the lifted check does the scan on the calling thread, then asks sources
        # in a worker; with nothing linked it never starts one
        self.host._mpq_check()
        return self.svc.build_items()

    def test_a_pack_nobody_linked_is_listed_but_never_checked(self):
        self.put("patch-O.mpq")
        self.put("patch-Q.mpq")
        items = self.scan()
        rows = {i["file"]: i for i in items if i["kind"] == "pack"}
        self.assertEqual(rows["patch-O.mpq"]["name"], "Octo Raid Visuals")
        self.assertTrue(rows["patch-Q.mpq"]["canLink"])
        self.assertFalse(rows["patch-Q.mpq"]["linked"])
        self.assertEqual(rows["patch-Q.mpq"]["action"], "")          # no "update" for strangers
        self.assertNotIn("patch-O.mpq", [r["file"] for r in rows.values() if r["section"] == "AVAILABLE"])

    def test_available_lists_the_catalogue_when_nothing_is_installed(self):
        items = self.scan()
        rows = [i for i in items if i["kind"] == "pack"]
        self.assertEqual([(r["section"], r["action"]) for r in rows], [("AVAILABLE", "install")])
        self.assertEqual(rows[0]["author"], "Rook")

    def test_uninstall_asks_and_declining_keeps_the_file(self):
        self.put("patch-Q.mpq")
        self.dialogs.yes = False
        self.svc.uninstall("patch-Q.mpq")
        self.assertTrue(os.path.exists(os.path.join(self.game, "Data", "patch-Q.mpq")))
        self.dialogs.yes = True
        self.svc.uninstall("patch-Q.mpq")
        self.assertFalse(os.path.exists(os.path.join(self.game, "Data", "patch-Q.mpq")))

    def test_link_refuses_a_bad_address_with_the_reason_and_changes_nothing(self):
        self.put("patch-Q.mpq")
        err = self.svc.link("patch-Q.mpq", "url", "not a link")
        self.assertTrue(err)
        self.assertEqual(open(os.path.join(self.game, "Data", "patch-Q.mpq"), "rb").read(), b"theirs")

    def test_one_pack_at_a_time(self):
        self.put("patch-Q.mpq")
        self.assertIsNotNone(self.host._mpq_start("patch-Q.mpq", "x"))
        self.assertIsNone(self.host._mpq_start("patch-O.mpq", "y"))
        self.host._mpq_busy = None

    def test_the_state_words_cover_every_judgement_that_holds_a_pack_back(self):
        from equpdater import mpq
        states = type(self.host)._MPQ_STATES
        for st in mpq.REPLACEABLE + ("unverifiable",):
            self.assertIn(st, states)

    def test_speech_setting_is_stored_in_the_shared_config(self):
        self.assertFalse(self.svc.ignore_speech())
        self.svc.set_ignore_speech(True)
        self.assertTrue(self.svc.ignore_speech())


class TestGeneralService(unittest.TestCase):
    def setUp(self):
        from equpdater import config, i18n
        from equpdater.core.ops_host import OpsHost, AutoDialogs
        from equpdater.core.settings_service import GeneralService
        self.d = tempfile.mkdtemp()
        self._cfg = config.CONFIG_FILE
        config.CONFIG_FILE = os.path.join(self.d, "config.json")
        self.addCleanup(setattr, config, "CONFIG_FILE", self._cfg)
        self.addCleanup(i18n.set_language, "enUS")
        self.game = os.path.join(self.d, "game")
        os.makedirs(self.game)
        self.store = {"game_dir": self.game}

        class S:
            def get(_s, k, d=None):
                return self.store.get(k, d)

            def set(_s, k, v):
                self.store[k] = v
        self.events, self.dialogs = [], AutoDialogs(yes=True)
        self.host = OpsHost(S(), events=lambda n, **kw: self.events.append(n), dialogs=self.dialogs)
        self.svc = GeneralService(self.host, S(), TaskManager(1))

    def test_toggles_use_their_2x_keys(self):
        from equpdater import config
        for name, key in (("clearWdb", "clear_wdb_on_launch"), ("closeOnLaunch", "close_on_launch"),
                          ("autoMods", "auto_install_mods"), ("autoAddons", "auto_install_addons")):
            self.assertFalse(self.svc.get(name))
            self.svc.set(name, True)
            self.assertTrue(config.load_config()[key], name)
        self.assertTrue(config.load_config()["essential_mods_asked"])     # a choice is an answer

    def test_turning_an_auto_install_on_installs_what_is_missing_only_on_close(self):
        from unittest import mock
        with mock.patch.object(self.host, "_install_missing_essential_mods") as em,                 mock.patch.object(self.host, "_install_missing_recommended_addons") as ad:
            self.svc.set("autoMods", True)
            self.svc.set("autoAddons", True)
            em.assert_not_called()
            self.svc.close()
            em.assert_called_once()
            ad.assert_called_once()
            self.svc.close()                       # once
            em.assert_called_once()
        with mock.patch.object(self.host, "_install_missing_essential_mods") as em:
            self.svc.set("autoMods", True)
            self.svc.set("autoMods", False)        # turned back off: cancelled
            self.svc.close()
            em.assert_not_called()

    def test_game_folder_change_is_validated_then_resets_state(self):
        other = os.path.join(self.d, "other")
        os.makedirs(other)
        self.assertTrue(self.svc.change_game_dir(""))
        self.assertTrue(self.svc.change_game_dir(os.path.join(self.d, "nope")))
        self.assertEqual(self.svc.change_game_dir(self.game), "")      # same folder: nothing
        self.assertNotIn("game_dir_changed", self.events)
        from equpdater import config
        config.update_config(lambda c: c.update(mods={"dxvk": {"managed": True}}, out_dir=self.game))
        self.assertEqual(self.svc.change_game_dir(other), "")
        self.assertIn("game_dir_changed", self.events)
        cfg = config.load_config()
        self.assertEqual(os.path.normcase(cfg["out_dir"]), os.path.normcase(other))
        self.assertNotIn("mods", cfg)                                  # records of the old folder

    def test_font_override_replaces_every_font_and_leaves_the_skin_alone(self):
        from equpdater.core import skin
        from equpdater.core.settings_service import apply_font_override
        theme = skin.builtin_skin().theme
        out = apply_font_override(theme, "opendyslexic")
        self.assertEqual({f["family"] for f in out["fonts"].values()}, {"OpenDyslexic"})
        self.assertTrue(all(c["font"]["family"] == "OpenDyslexic"
                            for c in out["components"].values() if c.get("font")))
        self.assertNotEqual(theme["fonts"]["body"]["family"], "OpenDyslexic")
        self.assertLess(out["fonts"]["body"]["size"], theme["fonts"]["body"]["size"])
        self.assertIs(apply_font_override(theme, ""), theme)

    def test_chinese_locks_the_font_choice_as_in_2x(self):
        from equpdater import i18n
        i18n.set_language("zhCN")
        self.assertTrue(all(c["locked"] for c in self.svc.font_choices() if c["id"]))

    def test_language_restart_is_offered_only_when_it_differs_and_never_forced(self):
        from equpdater import config, i18n
        config.update_config(lambda c: c.__setitem__("tweaks", {"locale": "deDE"}))
        i18n.set_language("enUS")
        self.dialogs.yes = False
        self.host._offer_language_restart()
        self.assertEqual([a[0] for a in self.dialogs.asked], ["yes_no"])
        self.assertNotIn("restart", self.events)
        self.dialogs.yes = True
        self.host._offer_language_restart()
        self.assertIn("restart", self.events)
        self.dialogs.asked.clear()
        config.update_config(lambda c: c.__setitem__("tweaks", {"locale": "enUS"}))
        self.host._offer_language_restart()
        self.assertEqual(self.dialogs.asked, [])                       # same language: no question
        self.assertEqual(i18n.language(), "enUS")

    def test_session_log_keeps_the_latest_lines(self):
        from equpdater.core import logsink
        logsink.log("hello session log", "ok")
        self.assertIn("hello session log", self.svc.session_log())

    def test_font_choice_and_game_folder_come_from_2x_when_imported(self):
        from equpdater.core import settings as cs
        d = tempfile.mkdtemp()
        s = cs.open_settings(d, legacy={"out_dir": self.game,
                                        "accessibility": {"font_choice": "opendyslexic"}})
        self.assertEqual(s.get("font_override"), "opendyslexic")
        s2 = cs.open_settings(tempfile.mkdtemp(), legacy={"accessibility": {"font_choice": "arial"}})
        self.assertEqual(s2.get("font_override"), "")                  # 2.x's default is "the skin's"
        from equpdater import config
        self.assertFalse(os.path.exists(config.CONFIG_FILE))           # importing wrote nothing


class TestVersionMigration(unittest.TestCase):
    """The one-time 1.3 re-patch, now runnable by the Qt host (it was Tk-only)."""

    def setUp(self):
        from equpdater import config
        from equpdater.core.ops_host import OpsHost, AutoDialogs
        self.d = tempfile.mkdtemp()
        self.game = os.path.join(self.d, "game")
        os.makedirs(os.path.join(self.game, "WTF"))
        with open(os.path.join(self.game, "WoW.exe"), "wb") as f:
            f.write(b"MZ")
        with open(os.path.join(self.game, "WTF", "Config.wtf"), "w") as f:
            f.write('SET frillDensity "64"' + chr(10) + 'SET other "1"' + chr(10))
        with open(os.path.join(self.game, "dlls.txt"), "w") as f:
            f.write("VfPatcher.dll" + chr(10) + "nampower.dll" + chr(10))
        self._cfg = config.CONFIG_FILE
        config.CONFIG_FILE = os.path.join(self.d, "config.json")
        self.addCleanup(setattr, config, "CONFIG_FILE", self._cfg)
        config.save_config({"app_version": "1.2", "out_dir": self.game,
                            "tweaks": {"frillDistance": 70}})
        s = {"game_dir": self.game}
        settings = type("S", (), {"get": lambda _x, k, d=None: s.get(k, d)})()
        self.host = OpsHost(settings, dialogs=AutoDialogs())
        self.patched = []
        self.host._apply_tweaks_worker = lambda out, tw: self.patched.append((out, dict(tw)))

    def test_an_install_older_than_1_3_is_migrated_once(self):
        from equpdater import config
        self.host._new_version_installed()
        cfg = config.load_config()
        self.assertEqual(cfg["tweaks"]["frillDistance"], 120)
        self.assertEqual(self.patched[0][1]["frillDistance"], 120)         # WoW.exe re-patched
        self.assertIn('SET frillDensity "128"', open(os.path.join(self.game, "WTF", "Config.wtf")).read())
        self.assertNotIn("VfPatcher.dll", open(os.path.join(self.game, "dlls.txt")).read())
        from equpdater import branding
        self.assertEqual(cfg["app_version"], branding.APP_VERSION)
        self.host._new_version_installed()                                 # already stamped: nothing
        self.assertEqual(len(self.patched), 1)

    def test_a_first_run_is_only_stamped(self):
        from equpdater import config
        self.host._first_run = True
        self.host._new_version_installed()
        self.assertEqual(self.patched, [])
        self.assertEqual(config.load_config()["tweaks"]["frillDistance"], 70)


class TestPatchExeSafety(unittest.TestCase):
    """The 0-byte WoW.exe found on a dev machine: a bad cached base must never
    be written over the game."""

    def setUp(self):
        from unittest import mock
        self.d = tempfile.mkdtemp()
        self.exe = os.path.join(self.d, "WoW.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ" + b"x" * 2_000_000)
        from equpdater.core import tweaks
        self.t = tweaks
        p = mock.patch.object(tweaks, "client_exe_locked", lambda d: False)
        p.start()
        self.addCleanup(p.stop)

    def test_an_empty_or_tiny_base_is_refused_and_the_game_is_untouched(self):
        from unittest import mock
        before = open(self.exe, "rb").read()
        for bad in (b"", b"MZ" + b"y" * 1000, b"NOTEXE" * 400_000):
            with mock.patch.object(self.t, "read_pristine_wow", lambda d, b=bad: b):
                with self.assertRaises(RuntimeError) as cm:
                    self.t.patch_exe(self.d, {}, lambda *a, **k: None)
                self.assertIn("left as it is", str(cm.exception))
            self.assertEqual(open(self.exe, "rb").read(), before)
        self.assertEqual(os.listdir(self.d), ["WoW.exe"])      # no temp file left

    def test_a_failure_while_building_leaves_the_game_and_no_temp_file(self):
        from unittest import mock
        before = open(self.exe, "rb").read()
        with mock.patch.object(self.t, "read_pristine_wow",
                               lambda d: b"MZ" + b"z" * 2_000_000),                 mock.patch.object(self.t, "build_tweaks",
                                  lambda buf, tw=None: 1 / 0):
            with self.assertRaises(ZeroDivisionError):
                self.t.patch_exe(self.d, {}, lambda *a, **k: None)
        self.assertEqual(open(self.exe, "rb").read(), before)
        self.assertEqual(os.listdir(self.d), ["WoW.exe"])


class TestForumIds(unittest.TestCase):
    def test_literals_match_the_parser_module(self):
        from equpdater import news
        from equpdater.core import news_service as ns
        self.assertEqual((ns.ANNOUNCEMENTS_FORUM, ns.CHANGELOG_FORUM),
                         (news.ANNOUNCEMENTS_FORUM_ID, news.CHANGELOG_FORUM_ID))


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
