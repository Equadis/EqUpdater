"""PLAY's choice of what to start: which files are offered, which one wins,
how each is run, and the button and list that choose. Real files."""
import os
import subprocess
import tempfile
import time
import tkinter as tk
import unittest
from unittest import mock

from equpdater import platforms
from equpdater.ui import GradientButton

LAUNCHES = b'@echo off\r\nstart "" VanillaFixes.exe\r\n'


def touch(folder, name, content=b"x"):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name


class TestOptions(Base):
    def test_loader_then_game_then_scripts(self):
        touch(self.dir, "WoW.exe")
        touch(self.dir, "VanillaFixes.exe")
        touch(self.dir, "b_launch.bat", LAUNCHES)
        touch(self.dir, "A_launch.cmd", b"wow.exe")
        self.assertEqual(platforms.launch_options(self.dir),
                         ["VanillaFixes.exe", "WoW.exe", "A_launch.cmd",
                          "b_launch.bat"])

    def test_other_programs_are_not_the_game(self):
        touch(self.dir, "WoW.exe")
        for other in ("OctoLauncher_Installer.exe", "ShowAllDisplayDevices.exe",
                      "unins000.exe", "Scan.dll", "readme.txt"):
            touch(self.dir, other)
        self.assertEqual(platforms.launch_options(self.dir), ["WoW.exe"])

    def test_a_script_that_does_not_start_the_game_is_not_offered(self):
        touch(self.dir, "backup.bat", b"@echo off\r\ncopy a b\r\n")
        self.assertEqual(platforms.launch_options(self.dir), [])

    def test_script_names_cmd_would_read_as_syntax_are_not_offered(self):
        for name in ("a&calc.bat", "b(1).bat", "c%PATH%.bat", "d^e.bat"):
            touch(self.dir, name, LAUNCHES)
        touch(self.dir, "Launch WoW-2.bat", LAUNCHES)
        self.assertEqual(platforms.launch_options(self.dir),
                         ["Launch WoW-2.bat"])

    def test_the_name_is_spelled_as_on_disk(self):
        touch(self.dir, "wow.EXE")
        self.assertEqual(platforms.launch_options(self.dir), ["wow.EXE"])

    def test_a_folder_named_like_the_game_is_not_it(self):
        os.mkdir(os.path.join(self.dir, "WoW.exe"))
        self.assertEqual(platforms.launch_options(self.dir), [])

    def test_a_missing_folder_has_none(self):
        self.assertEqual(
            platforms.launch_options(os.path.join(self.dir, "gone")), [])


class TestResolve(unittest.TestCase):
    OPTIONS = ["VanillaFixes.exe", "WoW.exe", "go.bat"]

    def test_the_loader_until_chosen_otherwise(self):
        self.assertEqual(platforms.resolve_launch_target(self.OPTIONS),
                         "VanillaFixes.exe")

    def test_the_saved_default_wins(self):
        self.assertEqual(
            platforms.resolve_launch_target(self.OPTIONS, "go.bat"), "go.bat")

    def test_case_does_not_lose_it(self):
        self.assertEqual(
            platforms.resolve_launch_target(self.OPTIONS, "WOW.EXE"), "WoW.exe")

    def test_a_missing_default_falls_back(self):
        self.assertEqual(
            platforms.resolve_launch_target(self.OPTIONS, "gone.bat"),
            "VanillaFixes.exe")

    def test_nothing_to_start(self):
        self.assertIsNone(platforms.resolve_launch_target([], "go.bat"))


class TestPlan(Base):
    @unittest.skipUnless(platforms.WINDOWS, "Windows launch")
    def test_exe_runs_itself_with_no_console(self):
        exe = os.path.join(self.dir, "WoW.exe")
        plan = platforms.launch_plan(self.dir, exe)
        self.assertEqual(plan.argv, [exe])
        self.assertFalse(plan.console)

    @unittest.skipUnless(platforms.WINDOWS, "Windows launch")
    def test_script_goes_to_cmd_by_name_from_the_game_folder(self):
        plan = platforms.launch_plan(self.dir, os.path.join(self.dir, "go.bat"))
        self.assertEqual(plan.argv[1:], ["/d", "/c", ".\\go.bat"])
        self.assertEqual(plan.cwd, self.dir)
        self.assertTrue(plan.console)

    @unittest.skipUnless(platforms.WINDOWS, "Windows launch")
    def test_script_really_runs_in_the_game_folder(self):
        """Through a folder and a file name with spaces, and an & in the
        folder that cmd.exe would read as a second command."""
        folder = os.path.join(self.dir, "Game & Stuff")
        os.mkdir(folder)
        touch(folder, "Launch WoW.bat",
              b"@echo off\r\nrem WoW.exe\r\ncd > where.txt\r\n")
        plan = platforms.launch_plan(folder,
                                     os.path.join(folder, "Launch WoW.bat"))
        proc = platforms.spawn_detached(plan.argv, cwd=plan.cwd, env=plan.env,
                                        console=plan.console)
        self.addCleanup(proc.wait)
        where = os.path.join(folder, "where.txt")
        for _ in range(100):
            if os.path.exists(where) and os.path.getsize(where):
                break
            time.sleep(.1)
        with open(where) as f:
            self.assertTrue(os.path.samefile(f.read().strip(), folder))

    @unittest.skipIf(platforms.WINDOWS, "Linux launch")
    def test_linux_gives_the_script_to_the_runner(self):
        script = os.path.join(self.dir, "go.bat")
        plan = platforms.launch_plan(self.dir, script,
                                     which=lambda n: "/usr/bin/wine"
                                     if n == "wine" else None)
        self.assertEqual(plan.argv, ["/usr/bin/wine", script])


class TestSplitButton(unittest.TestCase):
    def setUp(self):
        from equpdater.app import PLAY_GRADIENT
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.addCleanup(self.root.destroy)
        self.play, self.arrow = [], []
        self.btn = GradientButton(self.root, width=132, height=38, text="PLAY",
                                  palette=PLAY_GRADIENT,
                                  command=lambda: self.play.append(1))
        self.btn.pack()
        self.btn.set_split(16, lambda: self.arrow.append(1))

    def click(self, down, up=None):
        up = down if up is None else up
        self.btn._press(mock.Mock(x=down, y=10))
        self.btn._release(mock.Mock(x=up, y=10))

    def test_each_part_does_its_own_thing(self):
        self.click(125)
        self.assertEqual((self.arrow, self.play), ([1], []))
        self.click(5)
        self.assertEqual((self.arrow, self.play), ([1], [1]))

    def test_the_arrow_is_the_right_edge(self):
        self.click(132 - 16)
        self.click(132 - 17)
        self.assertEqual((self.arrow, self.play), ([1], [1]))

    def test_sliding_between_parts_does_nothing(self):
        self.click(125, 80)
        self.click(80, 125)
        self.assertEqual((self.arrow, self.play), ([], []))

    def test_without_a_split_all_of_it_is_the_button(self):
        self.btn.set_split()
        self.click(125)
        self.assertEqual((self.arrow, self.play), ([], [1]))

    def test_disabled_does_neither(self):
        self.btn.set_enabled(False)
        self.click(125)
        self.click(80)
        self.assertEqual((self.arrow, self.play), ([], []))

    def test_the_keyboard_reaches_the_arrow(self):
        self.btn._invoke_split()
        self.assertEqual(self.arrow, [1])


if __name__ == "__main__":
    unittest.main()
