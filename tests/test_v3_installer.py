"""The EqUpdater 3 beta installer: staged, never over 2.x, refuses while 2.x runs."""

import importlib
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock


def mod():
    return importlib.import_module("equpdater.installer3")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.m = mod()

    def build(self, name="build", marker="v1", exe=True):
        p = os.path.join(self.d, name)
        os.makedirs(os.path.join(p, "_internal"))
        if exe:
            with open(os.path.join(p, self.m.EXE), "w") as f:
                f.write(marker)
        with open(os.path.join(p, "_internal", "data.txt"), "w") as f:
            f.write(marker)
        return p

    def payload(self, build, name="payload.zip"):
        z = os.path.join(self.d, name)
        with zipfile.ZipFile(z, "w") as zf:
            for folder, _s, files in os.walk(build):
                for n in files:
                    p = os.path.join(folder, n)
                    zf.write(p, os.path.relpath(p, build))
        return z

    def read(self, *parts):
        with open(os.path.join(*parts)) as f:
            return f.read()


class TestSwap(Tmp):
    def test_first_install_and_update_replace_the_build_and_leave_no_debris(self):
        dest = os.path.join(self.d, "Programs", "EqUpdater3")
        exe = self.m.swap_in(self.build(marker="v1"), dest)
        self.assertEqual(self.read(exe), "v1")
        self.m.swap_in(self.build("b2", "v2"), dest)
        self.assertEqual(self.read(dest, self.m.EXE), "v2")
        self.assertEqual(sorted(os.listdir(os.path.dirname(dest))), ["EqUpdater3"])

    def test_a_failed_swap_puts_the_old_version_back(self):
        dest = os.path.join(self.d, "Programs", "EqUpdater3")
        self.m.swap_in(self.build(marker="v1"), dest)
        real = os.rename
        calls = []

        def flaky(a, b):
            calls.append((a, b))
            if b == dest and a.endswith(".new"):
                raise OSError("locked")
            return real(a, b)
        with self.assertRaises(self.m.InstallError):
            self.m.swap_in(self.build("b2", "v2"), dest, rename=flaky)
        self.assertEqual(self.read(dest, self.m.EXE), "v1")
        self.assertEqual(sorted(os.listdir(os.path.dirname(dest))), ["EqUpdater3"])

    def test_an_incomplete_build_is_refused_before_anything_moves(self):
        dest = os.path.join(self.d, "Programs", "EqUpdater3")
        self.m.swap_in(self.build(marker="v1"), dest)
        with self.assertRaises(self.m.InstallError):
            self.m.swap_in(self.build("b2", exe=False), dest)
        self.assertEqual(self.read(dest, self.m.EXE), "v1")

    def test_an_earlier_run_that_stopped_half_way_is_finished(self):
        dest = os.path.join(self.d, "Programs", "EqUpdater3")
        os.makedirs(dest + ".old")
        with open(os.path.join(dest + ".old", self.m.EXE), "w") as f:
            f.write("old")
        self.m.swap_in(self.build("b2", "v2"), dest)
        self.assertEqual(self.read(dest, self.m.EXE), "v2")
        self.assertFalse(os.path.exists(dest + ".old"))


class TestUnpack(Tmp):
    def test_the_build_is_found_inside_the_payload(self):
        out = self.m.unpack(self.payload(self.build()), os.path.join(self.d, "stage"))
        self.assertTrue(os.path.isfile(os.path.join(out, self.m.EXE)))

    def test_a_path_leaving_the_folder_is_refused(self):
        z = os.path.join(self.d, "bad.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(self.m.EXE, "x")
            zf.writestr("../evil.txt", "x")
        with self.assertRaises(self.m.InstallError):
            self.m.unpack(z, os.path.join(self.d, "stage"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "evil.txt")))

    def test_a_payload_without_the_program_is_refused(self):
        with self.assertRaises(self.m.InstallError):
            self.m.unpack(self.payload(self.build(exe=False)), os.path.join(self.d, "stage"))


class TestInstall(Tmp):
    def setUp(self):
        super().setUp()
        self.start, self.desk = os.path.join(self.d, "start"), os.path.join(self.d, "desk")
        os.makedirs(self.start)
        os.makedirs(self.desk)
        self.made = []
        p = mock.patch.object(self.m, "_shortcut", lambda lnk, exe: self.made.append(lnk) or True)
        p.start()
        self.addCleanup(p.stop)
        self.dest = os.path.join(self.d, "Programs", "EqUpdater3")
        self.zip = self.payload(self.build(marker="v1"))

    def go(self, **kw):
        kw.setdefault("warm", None)
        return self.m.install(self.zip, self.dest, folders=[self.desk, self.start],
                              two_x_dir=os.path.join(self.d, "no2x"), **kw)

    def test_installs_and_makes_only_its_own_shortcuts(self):
        exe, created = self.go()
        self.assertTrue(os.path.isfile(exe))
        names = sorted(os.path.basename(c) for c in created)
        self.assertEqual(names, ["EqUpdater 3 Beta.lnk", "EqUpdater 3 Beta.lnk"])
        self.assertFalse(any(n.startswith("EqUpdater.") for n in names))

    def test_a_2x_shortcut_is_never_touched(self):
        two_x = os.path.join(self.start, "EqUpdater.lnk")
        with open(two_x, "w") as f:
            f.write("points at 2.x")
        self.go()
        self.assertEqual(self.read(two_x), "points at 2.x")
        self.assertNotIn(two_x, self.made)

    def test_the_desktop_shortcut_is_only_made_when_asked_for(self):
        _exe, created = self.go(want_desktop=lambda: False)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].startswith(self.start))

    def test_refuses_while_2x_is_running_and_changes_nothing(self):
        with mock.patch("equpdater.core.install.installed_2x_running", lambda d=None: True):
            with self.assertRaises(self.m.InstallError) as cm:
                self.go()
        self.assertIn("EqUpdater 2 is running", str(cm.exception))
        self.assertFalse(os.path.exists(self.dest))
        self.assertEqual(self.made, [])

    def test_refuses_while_the_beta_itself_is_running(self):
        self.go()
        with mock.patch.object(self.m, "_exe_in_use", lambda folder: True):
            with self.assertRaises(self.m.InstallError) as cm:
                self.go()
        self.assertIn("is running", str(cm.exception))
        self.assertEqual(self.read(self.dest, self.m.EXE), "v1")

    def test_installing_the_same_version_again_changes_nothing_that_matters(self):
        self.go()
        self.go()
        self.assertEqual(self.read(self.dest, self.m.EXE), "v1")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.dest))), ["EqUpdater3"])

    def test_the_cache_is_warmed_after_the_swap_and_a_failure_to_warm_is_harmless(self):
        seen = []
        self.go(warm=lambda exe: seen.append((exe, os.path.isfile(exe))))
        self.assertEqual(seen, [(os.path.join(self.dest, self.m.EXE), True)])

    def test_warm_cache_runs_the_headless_self_test_and_survives_errors(self):
        calls = []

        def ok(cmd, **kw):
            calls.append((cmd, kw["env"]["QT_QPA_PLATFORM"]))
            return mock.Mock(returncode=0)
        self.assertTrue(self.m.warm_cache("X.exe", run=ok))
        self.assertIn("--gui", calls[0][0][1:] + [""])
        self.assertEqual(calls[0][1], "offscreen")

        def boom(cmd, **kw):
            raise OSError("not a program")
        self.assertFalse(self.m.warm_cache("X.exe", run=boom))

    def test_it_installs_beside_2x_not_over_it(self):
        self.assertNotEqual(os.path.basename(self.m.default_install_dir()), "EqUpdater")
        self.assertEqual(os.path.basename(self.m.default_install_dir()), "EqUpdater3")


class TestPackagingLists(unittest.TestCase):
    """The package leaves out whole Qt areas; make sure none the app uses is among them."""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        cls.b = importlib.import_module("build_v3")
        cls.root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "equpdater", "qtui")

    def qml_imports(self):
        import re
        found = set()
        for d, _s, files in os.walk(os.path.join(self.root, "qml")):
            for f in files:
                if f.endswith(".qml") and f != "VideoLayer.qml":     # only for a video skin
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        found |= set(re.findall(r"^import\s+([\w.]+)", fh.read(), re.M))
        return found

    def test_no_imported_qml_module_is_pruned(self):
        for mod_name in self.qml_imports():
            if mod_name == "EqUi":
                continue
            rel = "qml/" + mod_name.replace(".", "/")
            for pruned in self.b.PRUNE_DIRS:
                self.assertFalse(rel == pruned or rel.startswith(pruned + "/"),
                                 f"{mod_name} is imported by the QML but pruned ({pruned})")

    def test_no_python_qt_module_used_is_excluded(self):
        import re
        used = set()
        for d, _s, files in os.walk(os.path.dirname(self.root)):
            for f in files:
                if f.endswith(".py") and "installer" not in f:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        used |= set(re.findall(r"PySide6\.(Qt\w+)", fh.read()))
        excluded = {e.split(".", 1)[1] for e in self.b.EXCLUDES if e.startswith("PySide6.")}
        self.assertEqual(used & excluded, set())

    def test_the_installer_build_does_not_carry_qt(self):
        import inspect
        src = inspect.getsource(self.b.build_setup)
        self.assertIn('"PySide6"', src)

    def test_no_official_skin_needs_video(self):
        # QtMultimedia is left out of the package; if an official skin ever
        # animates with a video file, this and the package must change together
        skins = os.path.join(os.path.dirname(self.root), "..", "skins")
        for name in os.listdir(skins):
            for f in ("theme.json", "manifest.json"):
                path = os.path.join(skins, name, f)
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read().lower()
                    self.assertNotIn(".mp4", text, name)
                    self.assertNotIn(".webm", text, name)


if __name__ == "__main__":
    unittest.main()
