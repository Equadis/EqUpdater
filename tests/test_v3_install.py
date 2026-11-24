"""Phase 4: finding an existing game and extracting a downloaded archive."""

import io
import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile

from equpdater.core import install as I
from tests.test_v3_core import Pump
from equpdater.core.tasks import TaskManager

EXE = b"MZ" + b"\0" * (I.MIN_EXE_BYTES + 10)


def make_game(root, mpqs=4, partial=(), exe=EXE):
    os.makedirs(os.path.join(root, "Data"), exist_ok=True)
    with open(os.path.join(root, "WoW.exe"), "wb") as f:
        f.write(exe)
    for i in range(mpqs):
        with open(os.path.join(root, "Data", f"patch-{i}.mpq"), "wb") as f:
            f.write(b"MPQ\x1a")
    for name in partial:
        with open(os.path.join(root, "Data", name), "wb") as f:
            f.write(b"x")
    return root


def zip_of(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)


class TestInspect(Tmp):
    def test_a_complete_client(self):
        c = I.inspect_folder(make_game(os.path.join(self.d, "g")))
        self.assertEqual(c.state, "complete")
        self.assertTrue(c.usable)

    def test_unfinished_torrent_leftovers_are_called_out(self):
        c = I.inspect_folder(make_game(os.path.join(self.d, "g"), partial=("patch-9.mpq.aria2",)))
        self.assertEqual(c.state, "unfinished")
        self.assertIn("unfinished", " ".join(c.evidence))

    def test_a_tiny_or_missing_exe_is_not_a_client(self):
        self.assertEqual(I.inspect_folder(make_game(os.path.join(self.d, "a"), exe=b"MZ")).state, "incomplete")
        os.makedirs(os.path.join(self.d, "b"))
        self.assertEqual(I.inspect_folder(os.path.join(self.d, "b")).state, "incomplete")

    def test_too_few_data_archives_is_incomplete(self):
        self.assertEqual(I.inspect_folder(make_game(os.path.join(self.d, "g"), mpqs=1)).state, "incomplete")

    def test_looking_changes_nothing(self):
        g = make_game(os.path.join(self.d, "g"))
        before = sorted(os.listdir(g)), sorted(os.listdir(os.path.join(g, "Data")))
        I.inspect_folder(g)
        self.assertEqual(before, (sorted(os.listdir(g)), sorted(os.listdir(os.path.join(g, "Data")))))


class TestFind(Tmp):
    def test_sources_are_listed_once_complete_first(self):
        a = make_game(os.path.join(self.d, "a"))
        b = make_game(os.path.join(self.d, "b"))
        empty = os.path.join(self.d, "empty")
        os.makedirs(empty)
        got = I.find_installs({"out_dir": empty}, {"clientDir": a, "activeClientDir": a},
                              places=[b, a, os.path.join(self.d, "missing")])
        self.assertEqual([c.path for c in got], [os.path.normpath(a), os.path.normpath(b), os.path.normpath(empty)])
        self.assertEqual([c.source for c in got], ["octolauncher", "common", "equpdater"])

    def test_no_launcher_and_no_config_is_fine(self):
        self.assertEqual(I.find_installs({}, {}, places=[]), [])


class TestExtract(Tmp):
    def archive(self, files, name="g.zip"):
        return zip_of(os.path.join(self.d, name), files)

    GAME = {"WoW.exe": EXE, "Data/patch-1.mpq": b"MPQ\x1a"}

    def test_extracts_and_the_result_is_a_complete_game_folder(self):
        arc = self.archive({**self.GAME, **{f"Data/p{i}.mpq": b"MPQ\x1a" for i in range(3)}})
        seen = []
        out = I.extract_archive(arc, os.path.join(self.d, "out"), lambda d, t: seen.append((d, t)))
        self.assertTrue(I.inspect_folder(out).usable)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out.extracting")))
        self.assertTrue(seen and seen[-1][0] == seen[-1][1])

    def test_a_single_wrapper_folder_is_dropped(self):
        arc = self.archive({f"OctoWoW/{k}": v for k, v in self.GAME.items()})
        out = I.extract_archive(arc, os.path.join(self.d, "out"))
        self.assertTrue(os.path.isfile(os.path.join(out, "WoW.exe")))

    def test_paths_leaving_the_folder_are_refused_and_nothing_is_left(self):
        for bad in ("../evil.txt", "/abs.txt", "a/../../evil.txt", "C:/evil.txt"):
            arc = self.archive({**self.GAME, bad: b"x"}, "bad.zip")
            with self.assertRaises(I.InstallError):
                I.extract_archive(arc, os.path.join(self.d, "out"))
            self.assertFalse(os.path.exists(os.path.join(self.d, "out")))
            self.assertFalse(os.path.exists(os.path.join(self.d, "evil.txt")))

    def test_a_symlink_entry_is_refused(self):
        p = os.path.join(self.d, "l.zip")
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("WoW.exe", EXE)
            zi = zipfile.ZipInfo("link")
            zi.external_attr = 0o120777 << 16
            zf.writestr(zi, "/etc/passwd")
        with self.assertRaises(I.InstallError):
            I.extract_archive(p, os.path.join(self.d, "out"))

    def test_a_tar_with_a_link_is_refused_and_a_plain_one_works(self):
        p = os.path.join(self.d, "g.tar")
        with tarfile.open(p, "w") as tf:
            ti = tarfile.TarInfo("WoW.exe")
            ti.size = len(EXE)
            tf.addfile(ti, io.BytesIO(EXE))
        self.assertTrue(os.path.isfile(os.path.join(I.extract_archive(p, os.path.join(self.d, "o1")), "WoW.exe")))
        q = os.path.join(self.d, "l.tar")
        with tarfile.open(q, "w") as tf:
            ti = tarfile.TarInfo("x")
            ti.type, ti.linkname = tarfile.SYMTYPE, "/etc"
            tf.addfile(ti)
        with self.assertRaises(I.InstallError):
            I.extract_archive(q, os.path.join(self.d, "o2"))

    def test_not_a_game_archive_is_refused_before_anything_is_written(self):
        arc = self.archive({"readme.txt": b"hi"})
        with self.assertRaises(I.InstallError):
            I.extract_archive(arc, os.path.join(self.d, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "out")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "out.extracting")))

    def test_a_tiny_exe_in_the_archive_is_refused(self):
        arc = self.archive({"WoW.exe": b"MZ"})
        with self.assertRaises(I.InstallError):
            I.extract_archive(arc, os.path.join(self.d, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.d, "out")))

    def test_a_populated_destination_is_never_touched(self):
        dest = os.path.join(self.d, "out")
        os.makedirs(dest)
        with open(os.path.join(dest, "mine.txt"), "w") as f:
            f.write("keep")
        with self.assertRaises(I.InstallError):
            I.extract_archive(self.archive(self.GAME), dest)
        self.assertEqual(os.listdir(dest), ["mine.txt"])

    def test_an_empty_existing_destination_is_used(self):
        dest = os.path.join(self.d, "out")
        os.makedirs(dest)
        self.assertTrue(os.path.isfile(os.path.join(I.extract_archive(self.archive(self.GAME), dest), "WoW.exe")))

    def test_not_enough_space_is_reported_before_extracting(self):
        with self.assertRaises(I.InstallError) as cm:
            I.extract_archive(self.archive(self.GAME), os.path.join(self.d, "out"), free_space=lambda p: 1000)
        self.assertIn("free space", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.d, "out.extracting")))

    def test_cancelling_removes_the_partial_folder(self):
        arc = self.archive(self.GAME)
        with self.assertRaises(I.Cancelled):
            I.extract_archive(arc, os.path.join(self.d, "out"), cancel=lambda: True)
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith("out")], [])

    def test_7z_and_rar_say_what_to_do(self):
        for ext in ("a.7z", "a.rar", "a.exe"):
            with self.assertRaises(I.InstallError):
                I.archive_kind(ext)

    def test_a_corrupt_archive_is_reported_not_raised_raw(self):
        p = os.path.join(self.d, "bad.zip")
        with open(p, "wb") as f:
            f.write(b"not a zip")
        with self.assertRaises(I.InstallError):
            I.plan_archive(p)


class TestGuards(Tmp):
    def test_a_running_2x_is_detected_only_when_its_program_is_there_and_locked(self):
        from unittest import mock
        from equpdater import deploy
        self.assertFalse(I.installed_2x_running(self.d))             # nothing installed
        open(os.path.join(self.d, deploy.EXE), "wb").close()
        with mock.patch.object(deploy, "_exe_in_use", lambda folder: True):
            self.assertTrue(I.installed_2x_running(self.d))
        with mock.patch.object(deploy, "_exe_in_use", lambda folder: False):
            self.assertFalse(I.installed_2x_running(self.d))

    def test_a_game_that_is_running_blocks_changing_its_folder(self):
        from unittest import mock
        g = make_game(os.path.join(self.d, "g"))
        with mock.patch.object(I.platforms, "client_in_use", lambda d: True):
            self.assertIn("running", I.folder_busy(g))
        with mock.patch.object(I.platforms, "client_in_use", lambda d: False):
            self.assertEqual(I.folder_busy(g), "")

    def test_octolauncher_sign_in_is_never_opened(self):
        # find_installs reads only the settings file through octolauncher.read_settings
        d = os.path.join(self.d, "octo-launcher")
        os.makedirs(d)
        with open(os.path.join(d, "auth.json"), "w") as f:
            f.write("SECRET")
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write('{"clientDir": "%s", "token": "SECRET"}' % self.d.replace("\\", "/"))
        from equpdater import octolauncher
        st = octolauncher.read_settings(os.path.join(d, "settings.json"))
        self.assertNotIn("token", st)
        opened = []
        real = open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real(path, *a, **k)
        from unittest import mock
        with mock.patch("builtins.open", spy):
            I.find_installs({}, st, places=[])
        self.assertFalse(any("auth.json" in p for p in opened))


class TestSetupService(Tmp):
    def setUp(self):
        super().setUp()
        from equpdater.core.ops_host import AutoDialogs, OpsHost
        from equpdater.core.setup_service import SetupService
        settings = type("S", (), {"get": lambda s, k, d=None: None})()
        self.host = OpsHost(settings, dialogs=AutoDialogs())
        self.pump = Pump()
        self.tasks = TaskManager(2, self.pump)
        self.changed = []
        self.svc = SetupService(self.host, self.tasks, lambda p: self.changed.append(p) or "",
                                find=lambda: [I.inspect_folder(self.game)])
        self.game = make_game(os.path.join(self.d, "g"))

    def test_scan_lists_candidates_on_a_worker(self):
        self.assertTrue(self.svc.scan())
        self.pump.drain(self.tasks)
        self.assertEqual(self.svc.candidates[0]["state"], "complete")
        self.assertEqual(self.svc.status[1], "ok")

    def test_use_sets_the_game_folder_only_for_a_real_client(self):
        self.assertEqual(self.svc.use(self.game), "")
        self.assertEqual(self.changed, [self.game])
        empty = os.path.join(self.d, "e")
        os.makedirs(empty)
        self.assertTrue(self.svc.use(empty))
        self.assertEqual(self.changed, [self.game])

    def test_extract_then_use(self):
        arc = zip_of(os.path.join(self.d, "a.zip"), {"WoW.exe": EXE, "Data/a.mpq": b"x"})
        self.assertTrue(self.svc.inspect_archive(arc))
        done = []
        self.assertTrue(self.svc.extract(arc, os.path.join(self.d, "out"), done.append))
        self.pump.drain(self.tasks)
        self.assertEqual(done, [os.path.join(self.d, "out")])
        self.assertFalse(self.svc.extracting)

    def test_an_unsafe_archive_reports_and_keeps_nothing(self):
        arc = zip_of(os.path.join(self.d, "a.zip"), {"WoW.exe": EXE, "../x": b"x"})
        self.assertIsNone(self.svc.inspect_archive(arc))
        self.assertEqual(self.svc.status[1], "err")


if __name__ == "__main__":
    unittest.main()
