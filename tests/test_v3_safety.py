"""Write safety: atomic replacement and the per-game-folder operation lock."""

import multiprocessing
import os
import shutil
import tempfile
import threading
import time
import unittest

from equpdater.core import atomic, oplock


def _hold(path, seconds, ready):
    from equpdater.core import oplock as o
    with o.game_folder(path, 5):
        ready.set()
        time.sleep(seconds)


class TestAtomic(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.p = os.path.join(self.d, "f.txt")
        with open(self.p, "w") as f:
            f.write("old")

    def test_a_failed_write_leaves_the_old_file_and_no_temp(self):
        with self.assertRaises(RuntimeError):
            with atomic.replacing(self.p, "w") as f:
                f.write("half of the ne")
                raise RuntimeError("disk full")
        self.assertEqual(open(self.p).read(), "old")
        self.assertEqual(os.listdir(self.d), ["f.txt"])

    def test_success_replaces_in_one_step(self):
        atomic.write_text(self.p, "new", encoding="utf-8")
        self.assertEqual(open(self.p).read(), "new")
        self.assertEqual(os.listdir(self.d), ["f.txt"])

    def test_the_old_file_is_untouched_until_the_new_one_is_complete(self):
        seen = []
        with atomic.replacing(self.p, "w") as f:
            f.write("partial")
            f.flush()
            seen.append(open(self.p).read())
        self.assertEqual(seen, ["old"])
        self.assertEqual(open(self.p).read(), "partial")

    def test_a_new_file_in_a_new_folder(self):
        q = os.path.join(self.d, "sub", "x.bin")
        atomic.write_bytes(q, b"\x00\x01")
        self.assertEqual(open(q, "rb").read(), b"\x00\x01")

    def test_config_wtf_dlls_and_zip_members_use_it(self):
        import inspect
        from equpdater.core import mods, tweaks
        self.assertIn("atomic.replacing", inspect.getsource(mods._write_dlls_txt))
        self.assertIn("atomic.replacing", inspect.getsource(mods._write_dxvk_conf))
        self.assertIn("atomic.replacing", inspect.getsource(mods._install_mod_zip))
        self.assertIn("atomic.replacing", inspect.getsource(tweaks.write_config_wtf))
        self.assertIn("atomic.replacing", inspect.getsource(tweaks.update_config_wtf))


class TestLock(unittest.TestCase):
    def setUp(self):
        self.game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.game, True)

    def test_reentrant_in_one_thread(self):
        with oplock.game_folder(self.game, 1):
            with oplock.game_folder(self.game, 1):
                pass

    def test_a_second_thread_waits_then_proceeds(self):
        order = []

        def other():
            with oplock.game_folder(self.game, 5):
                order.append("other")
        with oplock.game_folder(self.game, 5):
            t = threading.Thread(target=other)
            t.start()
            time.sleep(0.3)
            order.append("first")
        t.join(5)
        self.assertEqual(order, ["first", "other"])

    def test_timeout_says_what_is_wrong(self):
        ready = threading.Event()
        done = threading.Event()

        def holder():
            with oplock.game_folder(self.game, 5):
                ready.set()
                done.wait(5)
        t = threading.Thread(target=holder)
        t.start()
        ready.wait(5)
        try:
            with self.assertRaises(oplock.GameFolderBusy) as cm:
                with oplock.game_folder(self.game, 0.3, "patching WoW.exe"):
                    pass
            self.assertIn("Another EqUpdater", str(cm.exception))
        finally:
            done.set()
            t.join(5)

    def test_another_process_is_excluded_and_death_releases_the_lock(self):
        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Event()
        proc = ctx.Process(target=_hold, args=(self.game, 1.5, ready))
        proc.start()
        self.assertTrue(ready.wait(20))
        with self.assertRaises(oplock.GameFolderBusy):
            with oplock.game_folder(self.game, 0.3):
                pass
        proc.terminate()                       # dies holding it
        proc.join(10)
        with oplock.game_folder(self.game, 5):  # the OS released it
            pass

    def test_the_mutating_operations_take_the_lock(self):
        from equpdater.core import addons, mods, tweaks
        for fn in (mods.install_mod, mods.uninstall_mod, addons.install_addon_files,
                   tweaks.patch_exe):
            self.assertTrue(hasattr(fn, "__wrapped__"), fn.__name__)


if __name__ == "__main__":
    unittest.main()
