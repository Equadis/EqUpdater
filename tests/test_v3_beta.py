"""Beta prep: crash reports, one window per data folder, the beta's version."""

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mods():
    return (importlib.import_module("equpdater.core.crashlog"),
            importlib.import_module("equpdater.core.single"),
            importlib.import_module("equpdater.core.logsink"))


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.crash, self.single, self.logsink = mods()
        self.hooks = (sys.excepthook, threading.excepthook)
        self.addCleanup(self.restore)

    def restore(self):
        import faulthandler
        sys.excepthook, threading.excepthook = self.hooks
        faulthandler.disable()
        f = self.crash._state.get("native")
        if f:
            f.close()
            self.crash._state["native"] = None
        self.crash._state.update(dir="", previous=None)


class TestCrashReports(Tmp):
    def raise_in_hook(self, exc):
        try:
            raise exc
        except Exception:                                   # noqa: BLE001
            sys.excepthook(*sys.exc_info())

    def test_an_uncaught_error_is_filed_with_the_version_and_reaches_the_session_log(self):
        self.crash.install(self.d, "3.0.0-test")
        self.raise_in_hook(ValueError("boom in the page"))
        reports = self.crash.list_reports(self.d)
        self.assertEqual(len(reports), 1)
        with open(os.path.join(self.d, reports[0]), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("EqUpdater 3.0.0-test (python)", text)
        self.assertIn("ValueError: boom in the page", text)
        self.assertIn("end of the session log", text)
        lines = [m for _t, m, _g in list(self.logsink.recent)[-3:]]
        self.assertTrue(any("unexpected error" in m and "boom in the page" in m for m in lines))

    def test_the_user_name_and_home_folder_are_removed_from_a_report(self):
        self.crash.install(self.d, "x")
        home = os.path.expanduser("~")
        self.raise_in_hook(RuntimeError(f"could not read {home}{os.sep}Games{os.sep}x"))
        with open(os.path.join(self.d, self.crash.list_reports(self.d)[0]), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(home, text)
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if len(user) >= 3:
            self.assertNotIn(user, text)

    def test_an_error_in_a_background_thread_is_filed_too(self):
        self.crash.install(self.d, "x")

        def work():
            raise KeyError("in a worker")
        t = threading.Thread(target=work, name="worker-7")
        t.start()
        t.join()
        reports = self.crash.list_reports(self.d)
        self.assertEqual(len(reports), 1)
        with open(os.path.join(self.d, reports[0]), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("in thread worker-7", text)
        self.assertIn("KeyError", text)

    def test_only_the_newest_reports_are_kept(self):
        self.crash.install(self.d, "x")
        t = [1_700_000_000]
        for _ in range(self.crash.KEEP + 4):
            t[0] += 60
            self.crash.write_report("python", "ValueError: x", now=lambda: t[0])
        self.assertEqual(len(self.crash.list_reports(self.d)), self.crash.KEEP)

    def test_a_native_crash_left_by_the_last_run_is_filed_and_announced_once(self):
        with open(os.path.join(self.d, self.crash.NATIVE), "w") as f:
            f.write("Windows fatal exception: access violation\n")
        prev = self.crash.install(self.d, "x")
        self.assertTrue(prev and os.path.isfile(prev))
        with open(prev, encoding="utf-8") as f:
            self.assertIn("access violation", f.read())
        self.assertTrue(any("stopped unexpectedly last time" in m for _t, m, _g in list(self.logsink.recent)[-3:]))

    def test_a_clean_exit_is_not_mistaken_for_a_crash_next_time(self):
        self.crash.install(self.d, "x")
        self.crash.clean_exit()
        self.assertIsNone(self.crash.install(self.d, "x"))
        self.assertEqual(self.crash.list_reports(self.d), [])

    def test_a_keyboard_interrupt_is_not_a_crash(self):
        self.crash.install(self.d, "x")
        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt:
            import io
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()):
                sys.excepthook(*sys.exc_info())
        self.assertEqual(self.crash.list_reports(self.d), [])

    def test_an_unwritable_folder_never_makes_things_worse(self):
        blocker = os.path.join(self.d, "file")
        with open(blocker, "w") as f:
            f.write("x")
        self.crash.install(os.path.join(blocker, "logs"), "x")          # a path under a file
        self.raise_in_hook(ValueError("still no crash of our own"))


class TestSingleInstance(Tmp):
    def test_a_second_window_for_the_same_data_folder_is_refused(self):
        a, b = self.single.SingleInstance(self.d), self.single.SingleInstance(self.d)
        self.assertTrue(a.acquire(wait=0))
        self.assertFalse(b.acquire(wait=0.3))
        a.release()
        self.assertTrue(b.acquire(wait=0))
        b.release()

    def test_a_restart_waits_for_the_old_window_to_let_go(self):
        a, b = self.single.SingleInstance(self.d), self.single.SingleInstance(self.d)
        self.assertTrue(a.acquire(wait=0))
        threading.Timer(0.4, a.release).start()
        self.assertTrue(b.acquire(wait=5))
        b.release()

    def test_different_data_folders_do_not_block_each_other(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        a, b = self.single.SingleInstance(self.d), self.single.SingleInstance(other)
        self.assertTrue(a.acquire(wait=0) and b.acquire(wait=0))
        a.release()
        b.release()

    def test_a_process_that_dies_does_not_leave_the_lock_behind(self):
        code = ("import sys, os; sys.path.insert(0, %r); "
                "from equpdater.core.single import SingleInstance; "
                "s = SingleInstance(%r); assert s.acquire(wait=0); os._exit(0)" % (ROOT, self.d))
        subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=30)
        s = self.single.SingleInstance(self.d)
        self.assertTrue(s.acquire(wait=0))
        s.release()

    def test_the_real_program_refuses_a_second_copy_and_says_so(self):
        env = {k: v for k, v in os.environ.items() if k not in ("EQU3_NEWS_FIXTURE", "EQU3_SERVER_FIXTURE",
                                                              "EQU3_FAKE_SITE", "EQU3_ALLOW_MULTI")}
        env.update(EQU3_DATA_DIR=self.d, EQU3_OFFLINE="1", EQU3_NO_SETUP="1", QT_QPA_PLATFORM="offscreen",
                   EQU3_EXIT_AFTER_MS="60000")
        first = subprocess.Popen([sys.executable, "-m", "equpdater.qtui"], env=env, cwd=ROOT,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            marks = os.path.join(self.d, "instance.lock")
            import time
            end = time.time() + 20
            while time.time() < end and not os.path.exists(marks):
                time.sleep(0.1)
            time.sleep(1.0)
            # the message box would block on Windows: say nothing but return the code
            env2 = dict(env, EQU3_EXIT_AFTER_MS="3000")
            code = ("import sys; sys.path.insert(0, %r); from equpdater.core import single; "
                    "single.tell_user = lambda m=None: print('REFUSED'); "
                    "from equpdater.qtui import main; sys.exit(main.run())" % ROOT)
            r = subprocess.run([sys.executable, "-I", "-c", code], env=env2, cwd=ROOT, timeout=60,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 3, r.stderr[-400:])
            self.assertIn("REFUSED", r.stdout)
        finally:
            first.terminate()
            first.wait(15)


class TestManageFoundOffer(unittest.TestCase):
    """The 2.x first-run question about mods and addons already in the folder
    (the 3.0 host had left it as an event nobody handled)."""

    def setUp(self):
        from unittest import mock
        ops = importlib.import_module("equpdater.core.ops_host")
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        ns = ops.load_config.__globals__
        old = ns["CONFIG_FILE"]
        ns["CONFIG_FILE"] = os.path.join(self.d, "config.json")
        self.addCleanup(ns.__setitem__, "CONFIG_FILE", old)
        self.game = os.path.join(self.d, "game")
        os.makedirs(self.game)
        settings = type("S", (), {"get": lambda s, k, d=None: self.game if k == "game_dir" else d})()
        self.host = ops.OpsHost(settings)
        self.asked = []
        self.host._manage_all_found = lambda: self.asked.append(1)
        self.host._found_mods = lambda: [{"id": "m"}]
        self.host._found_addons = lambda: []
        self.ops = ops
        e = mock.patch.dict(os.environ, {}, clear=False)
        e.start()
        os.environ.pop("EQUPDATER_NO_MIGRATION_PROMPT", None)
        self.addCleanup(e.stop)

    def test_asked_once_for_a_folder_that_has_something_to_manage(self):
        self.host._offer_manage_found()
        self.host._offer_manage_found()
        self.assertEqual(self.asked, [1])

    def test_not_asked_when_there_is_nothing_to_manage_or_the_prompt_is_off(self):
        self.host._found_mods = lambda: []
        self.host._offer_manage_found()
        self.assertEqual(self.asked, [])
        self.host._found_mods = lambda: [{"id": "m"}]
        os.environ["EQUPDATER_NO_MIGRATION_PROMPT"] = "1"
        self.host._offer_manage_found()
        self.assertEqual(self.asked, [])

    def test_waits_while_something_else_is_running(self):
        later = []
        self.host.after = lambda ms, fn, *a: later.append((ms, fn))
        self.host._running = True
        self.host._offer_manage_found()
        self.assertEqual(self.asked, [])
        self.assertEqual(later[0][0], 3000)


class TestBetaGate(unittest.TestCase):
    """tools/beta_check.py: the mechanical part of handing out a beta."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        cls.gate = importlib.import_module("beta_check")

    def run_check(self, fn):
        self.gate.results.clear()
        fn()
        return list(self.gate.results)

    def test_version_documents_secrets_and_skins_pass_on_this_tree(self):
        for fn in (self.gate.check_version, self.gate.check_docs, self.gate.check_secrets, self.gate.check_skins):
            fails = [r for r in self.run_check(fn) if r[0] == "FAIL"]
            self.assertEqual(fails, [], fn.__name__)

    def test_the_gate_notices_a_secret(self):
        import re
        self.assertTrue(self.gate.SECRET.search("token = ghp_" + "a" * 36))
        self.assertTrue(self.gate.SECRET.search("-----BEGIN RSA " + "PRIVATE KEY-----"))
        self.assertFalse(self.gate.SECRET.search("nothing to see here"))

    def test_the_gate_notices_a_wrong_version_and_a_missing_document(self):
        from unittest import mock
        with mock.patch("equpdater.qtui.bridge.APP_VERSION_V3", "3.0.0"):
            self.assertTrue(any(r[0] == "FAIL" for r in self.run_check(self.gate.check_version)))
        with mock.patch.object(self.gate, "REQUIRED_DOCS", self.gate.REQUIRED_DOCS + ("NOPE.md",)):
            self.assertTrue(any(r[0] == "FAIL" for r in self.run_check(self.gate.check_docs)))

    def test_the_classic_artwork_warning_cannot_be_forgotten(self):
        res = self.run_check(self.gate.check_skins)
        self.assertTrue(any(r[0] == "WARN" and "classic-2004" in r[1] for r in res))

    def test_the_2x_release_files_are_untouched_when_the_baseline_is_available(self):
        res = self.run_check(self.gate.check_two_x_untouched)
        self.assertFalse(any(r[0] == "FAIL" for r in res), res)

    def test_the_hand_off_list_names_what_only_a_person_can_confirm(self):
        text = " ".join(self.gate.human_items())
        for needle in ("PLAY", "installer", "artwork", "User-Agent", "Actions", "Linux"):
            self.assertIn(needle, text)


class TestBetaVersion(unittest.TestCase):
    def test_the_version_says_beta_and_matches_what_the_package_is_named_for(self):
        from equpdater.qtui.bridge import APP_VERSION_V3
        self.assertRegex(APP_VERSION_V3, r"^3\.0\.0-beta\.\d+$")

    def test_the_forum_user_agent_still_identifies_as_the_released_version(self):
        # OctoWoW's protection was tuned to "EqUpdater/<x.y.z> Python-urllib"; a beta must not break it
        from equpdater import branding
        self.assertRegex(branding.user_agent(), r"^EqUpdater/\d+\.\d+\.\d+ Python-urllib/")


if __name__ == "__main__":
    unittest.main()
