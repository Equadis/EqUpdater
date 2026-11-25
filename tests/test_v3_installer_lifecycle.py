"""Install, upgrade and uninstall of the beta, in a sandbox, with settings and a game folder
checked byte for byte. The installer only ever touches its own program folder, its own
shortcuts and its own Apps & features entry."""

import hashlib
import importlib
import json
import os
import unittest
import uuid
from unittest import mock

from tests.test_v3_installer import Tmp


class TestPreservationUpgradeAndUninstall(Tmp):
    def setUp(self):
        super().setUp()
        self.key = r"Software\EqUpdaterTest\\" + uuid.uuid4().hex[:8]
        self.addCleanup(self.m.unregister, self.key)
        self.home = os.path.join(self.d, "Local")
        self.data = os.path.join(self.home, "EqUpdater")
        self.game = os.path.join(self.d, "Games", "OctoWoW")
        os.makedirs(os.path.join(self.data, "v3"))
        os.makedirs(os.path.join(self.game, "Data"))
        os.makedirs(os.path.join(self.game, "Interface", "AddOns", "pfUI"))
        for rel, body in (("EqUpdater/config.json", b'{"out_dir": "x", "mods": {"a": 1}}'),
                          ("EqUpdater/v3/settings.json", b'{"skin": "bubbles"}'),
                          ("EqUpdater/v3/social.json", b'{"friends": []}')):
            with open(os.path.join(self.home, *rel.split("/")), "wb") as f:
                f.write(body)
        for rel in ("WoW.exe", "Data/patch-1.mpq", "Interface/AddOns/pfUI/pfUI.lua", "dlls.txt", "Config.wtf"):
            with open(os.path.join(self.game, *rel.split("/")), "wb") as f:
                f.write(os.urandom(300) + rel.encode())
        self.start, self.desk = os.path.join(self.d, "start"), os.path.join(self.d, "desk")
        os.makedirs(self.start)
        os.makedirs(self.desk)
        with open(os.path.join(self.start, "EqUpdater.lnk"), "wb") as f:                 # 2.x's own shortcut
            f.write(b"two-x")
        with open(os.path.join(self.start, "Other.lnk"), "wb") as f:
            f.write(b"other")
        self.dest = os.path.join(self.home, "Programs", "EqUpdater3")

        def fake_shortcut(lnk, exe):
            with open(lnk, "wb") as f:
                f.write(exe.encode())
            return True
        p = mock.patch.object(self.m, "_shortcut", fake_shortcut)
        p.start()
        self.addCleanup(p.stop)
        self.before = self.snapshot([self.data, self.game, self.start])

    def snapshot(self, roots):
        out = {}
        for r in roots:
            for d, _s, files in os.walk(r):
                for f in files:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        out[p] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def untouched(self):
        keep = {k: v for k, v in self.before.items() if k.startswith((self.data, self.game))}
        return keep == self.snapshot([self.data, self.game])

    def go(self, marker="v1", **kw):
        z = self.payload(self.build("b-" + marker, marker))
        kw.setdefault("warm", None)
        return self.m.install(z, self.dest, folders=[self.desk, self.start],
                              two_x_dir=os.path.join(self.d, "no2x"), registry_key=self.key, **kw)

    def test_installing_changes_nothing_but_its_own_things(self):
        self.go()
        self.assertTrue(self.untouched())
        self.assertEqual(self.read(self.start, "EqUpdater.lnk"), "two-x")
        self.assertEqual(self.read(self.start, "Other.lnk"), "other")
        self.assertTrue(os.path.isfile(os.path.join(self.dest, self.m.MARKER)))
        self.assertTrue(os.path.isfile(os.path.join(self.start, self.m.SHORTCUT)))
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as k:
            self.assertEqual(winreg.QueryValueEx(k, "DisplayName")[0], "EqUpdater 3 Beta")
            self.assertIn("--uninstall", winreg.QueryValueEx(k, "UninstallString")[0])
            self.assertEqual(winreg.QueryValueEx(k, "InstallLocation")[0], self.dest)

    def test_an_upgrade_replaces_the_program_and_leaves_everything_else(self):
        self.go("v1")
        self.go("v2", want_desktop=lambda: False)
        self.assertEqual(self.read(self.dest, self.m.EXE), "v2")
        marker = json.load(open(os.path.join(self.dest, self.m.MARKER)))
        self.assertEqual(marker["version"], importlib.import_module("equpdater.version3").APP_VERSION_V3)
        self.assertTrue(self.untouched())
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.dest))), ["EqUpdater3"])

    def test_uninstall_removes_the_program_its_shortcuts_and_its_entry_and_nothing_else(self):
        self.go()
        removed = self.m.uninstall(self.dest, folders=[self.desk, self.start], registry_key=self.key,
                                   shortcut_target=lambda p: self.read(p))
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(os.path.join(self.start, self.m.SHORTCUT)))
        self.assertTrue(any("Apps & features" in r for r in removed))
        import winreg
        with self.assertRaises(OSError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key)
        self.assertEqual(self.before, self.snapshot([self.data, self.game, self.start]))      # byte for byte

    def test_a_shortcut_with_our_name_that_points_elsewhere_is_left_alone(self):
        self.go()
        lnk = os.path.join(self.desk, self.m.SHORTCUT)
        with open(lnk, "wb") as f:
            f.write(b"somebody else's program")
        self.m.uninstall(self.dest, folders=[self.desk, self.start], registry_key=self.key,
                         shortcut_target=lambda p: self.read(p))
        self.assertTrue(os.path.exists(lnk))

    def test_uninstall_refuses_a_folder_it_did_not_make_even_the_game_folder(self):
        for victim in (self.game, self.data, self.d):
            with self.assertRaises(self.m.InstallError):
                self.m.uninstall(victim, folders=[], registry_key=None)
        self.assertTrue(self.untouched())

    def test_uninstall_refuses_while_the_program_is_running(self):
        self.go()
        with mock.patch.object(self.m, "_exe_in_use", lambda f: True):
            with self.assertRaises(self.m.InstallError):
                self.m.uninstall(self.dest, folders=[], registry_key=None)
        self.assertTrue(os.path.isdir(self.dest))

    def test_the_program_removing_itself_hands_the_folder_to_a_helper(self):
        self.go()
        handed = []
        self.m.uninstall(self.dest, folders=[self.desk, self.start], registry_key=self.key,
                         defer=handed.append, shortcut_target=lambda p: self.read(p))
        self.assertEqual(handed, [self.dest])
        self.assertTrue(os.path.isdir(self.dest))                                # the helper removes it after exit
        self.assertFalse(os.path.exists(os.path.join(self.start, self.m.SHORTCUT)))

    def test_the_deferred_helper_waits_then_removes_only_that_folder(self):
        spawned = []
        self.m.deferred_delete(self.dest, spawn=lambda cmd, **kw: spawned.append((cmd, kw)))
        cmd = spawned[0][0]
        self.assertIsInstance(cmd, str)                                    # one string: cmd's own quoting
        self.assertTrue(cmd.startswith('cmd /c "ping -n 3'))
        self.assertIn('rd /s /q "%s"' % self.dest, cmd)
        self.assertTrue(cmd.endswith('""'))

    def test_the_deferred_helper_really_removes_a_folder_when_run_by_cmd(self):
        victim = os.path.join(self.d, "victim dir")                        # a space in the path, as in real life
        os.makedirs(os.path.join(victim, "_internal"))
        with open(os.path.join(victim, "x.txt"), "w") as f:
            f.write("x")
        import subprocess
        import time
        procs = []
        self.m.deferred_delete(victim, spawn=lambda cmd, **kw: procs.append(subprocess.Popen(cmd, **kw)))
        procs[0].wait(30)
        for _ in range(20):
            if not os.path.exists(victim):
                break
            time.sleep(0.5)
        self.assertFalse(os.path.exists(victim))

    def test_a_failed_upgrade_keeps_the_previous_version(self):
        self.go("v1")
        real = os.rename

        def flaky(a, b):
            if str(a).endswith(".new"):
                raise OSError("locked")
            return real(a, b)
        z = self.payload(self.build("b-v2", "v2"))
        real_swap = self.m.swap_in
        with mock.patch.object(self.m, "swap_in", lambda b, d: real_swap(b, d, rename=flaky)):
            with self.assertRaises(self.m.InstallError):
                self.m.install(z, self.dest, folders=[self.desk, self.start],
                               two_x_dir=os.path.join(self.d, "no2x"), warm=None, registry_key=self.key)
        self.assertEqual(self.read(self.dest, self.m.EXE), "v1")
        self.assertTrue(self.untouched())

    def test_silent_mode_installs_and_uninstalls_and_reports_in_a_log(self):
        z = self.payload(self.build("b-v9", "v9"))
        log = os.path.join(self.d, "log.txt")
        with mock.patch.object(self.m, "payload_path", lambda: z),                 mock.patch("equpdater.core.install.installed_2x_running", lambda d=None: False):
            args = self.m.parse_args(["--silent", "--no-shortcuts", "--no-launch", "--no-warm", "--no-register",
                                      "--install-dir", self.dest, "--log", log])
            code = self.m.run_silent(args)
            self.assertEqual(code, 0, self.read(log))
            self.assertIn("installed:", self.read(log))
            self.assertEqual(self.read(self.dest, self.m.EXE), "v9")
            args = self.m.parse_args(["--silent", "--uninstall", "--no-shortcuts", "--no-register",
                                      "--install-dir", self.dest, "--log", log])
            self.assertEqual(self.m.run_silent(args), 0)
            self.assertFalse(os.path.exists(self.dest))
            args = self.m.parse_args(["--silent", "--uninstall", "--no-shortcuts", "--no-register",
                                      "--install-dir", self.game, "--log", log])
            self.assertEqual(self.m.run_silent(args), 1)                          # the game folder: refused
            self.assertIn("error:", self.read(log))
        self.assertTrue(os.path.isfile(os.path.join(self.game, "WoW.exe")))
        self.assertTrue(self.untouched())

    def test_the_apps_and_features_entry_can_be_registered_and_removed(self):
        import winreg
        self.assertTrue(self.m.register(os.path.join(self.dest, self.m.EXE), self.key))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as k:
            self.assertEqual(winreg.QueryValueEx(k, "Publisher")[0], "EqUpdater")
        self.assertTrue(self.m.unregister(self.key))
        self.assertFalse(self.m.unregister(self.key))


if __name__ == "__main__":
    unittest.main()
