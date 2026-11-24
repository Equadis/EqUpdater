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


class TestInPlaceWritesAreAtomic(unittest.TestCase):
    """The last two in-place writers: the Auto Loot byte patch and the pfUI lua files."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_a_failed_autoloot_write_leaves_wow_exe_exactly_as_it_was(self):
        from unittest import mock
        from tests.test_lootgate import fake_exe
        from equpdater import lootgate
        exe = os.path.join(self.d, "WoW.exe")
        fake_exe(exe, lootgate.JZ)
        with open(exe, "rb") as f:
            before = f.read()

        def die(f):
            raise OSError("disk full")
        with mock.patch("os.fsync", die):
            with self.assertRaises(OSError):
                lootgate.set_exe_state(exe, True)
        with open(exe, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(os.listdir(self.d), ["WoW.exe"])

    def test_an_unrecognised_exe_is_refused_untouched(self):
        from equpdater import lootgate
        exe = os.path.join(self.d, "WoW.exe")
        with open(exe, "wb") as f:
            f.write(b"MZ" + b"x" * 5000)
        with self.assertRaises(ValueError):
            lootgate.set_exe_state(exe, True)
        self.assertEqual(os.path.getsize(exe), 5002)

    def test_a_failed_pfui_write_keeps_the_original_lua(self):
        from unittest import mock
        from equpdater.core import addons
        base = os.path.join(self.d, "Interface", "AddOns", "pfUI")
        os.makedirs(os.path.join(base, "env"))
        prof = os.path.join(base, "env", "profiles.lua")
        with open(prof, "w", encoding="utf-8") as f:
            f.write("pfUI_profiles = {}\n")

        def die(f):
            raise OSError("disk full")
        with mock.patch("os.fsync", die):
            addons.patch_pfui_default_profile(self.d)
        with open(prof, encoding="utf-8") as f:
            self.assertEqual(f.read(), "pfUI_profiles = {}\n")
        self.assertEqual(os.listdir(os.path.join(base, "env")), ["profiles.lua"])

    def test_the_pfui_patch_still_works_and_is_idempotent(self):
        from equpdater.core import addons
        base = os.path.join(self.d, "Interface", "AddOns", "pfUI")
        os.makedirs(os.path.join(base, "env"))
        prof = os.path.join(base, "env", "profiles.lua")
        with open(prof, "w", encoding="utf-8") as f:
            f.write("pfUI_profiles = {}\n")
        addons.patch_pfui_default_profile(self.d)
        with open(prof, encoding="utf-8") as f:
            once = f.read()
        addons.patch_pfui_default_profile(self.d)
        with open(prof, encoding="utf-8") as f:
            self.assertEqual(f.read(), once)
        self.assertIn('pfUI_profiles["Default"]', once)


if __name__ == "__main__":
    unittest.main()
