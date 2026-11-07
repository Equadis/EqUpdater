"""WinError 193 when PLAY starts VanillaFixes.exe.

Windows answers "%1 is not a valid Win32 application" for an empty or cut-off
program (shown below with real files). A reported VanillaFixes.exe/VfPatcher.dll
pair matched no published VanillaFixes release, and the same file started from
Explorer. So: name a damaged program before starting it, keep EqUpdater's own
DLL folder out of the game, start a whole program Explorer's way when Windows
refuses it, and write mod files whole."""
import errno
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from equpdater import platforms  # noqa: E402

WINDOWS = os.name == "nt"


def tiny_pe(sections=1, raw_size=0x200, raw_ptr=0x200, total=0x400) -> bytes:
    """A minimal PE: DOS header, NT headers, a section table."""
    dos = bytearray(64)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3c, 64)
    opt = 0xe0
    nt = b"PE\0\0" + struct.pack("<HHIIIHH", 0x14c, sections, 0, 0, 0, opt, 0x102)
    table = b""
    for _ in range(sections):
        table += b".text\0\0\0" + struct.pack("<IIIIIIHHI", 0x100, 0x1000,
                                              raw_size, raw_ptr, 0, 0, 0, 0, 0)
    head = bytes(dos) + nt + bytes(opt) + table
    return head + bytes(max(0, total - len(head)))


class TestProgramProblem(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_a_whole_program_has_none(self):
        self.assertIsNone(platforms.program_problem(self.write("a.exe", tiny_pe())))

    def test_empty(self):
        self.assertEqual(platforms.program_problem(self.write("a.exe", b"")), "empty")

    def test_not_a_program(self):
        self.assertEqual(platforms.program_problem(
            self.write("a.exe", b"@echo off\r\n" * 10)), "damaged")

    def test_cut_short_after_its_headers(self):
        """A download cut short keeps its headers and loses its end."""
        self.assertEqual(platforms.program_problem(
            self.write("a.exe", tiny_pe(total=0x400)[:0x300])), "damaged")

    def test_headers_pointing_past_the_end(self):
        self.assertEqual(platforms.program_problem(
            self.write("a.exe", b"MZ" + bytes(58) + struct.pack("<I", 4096))),
            "damaged")

    @unittest.skipUnless(WINDOWS, "Windows only")
    def test_windows_says_193_for_exactly_these(self):
        """The error players saw, from a real CreateProcess."""
        with open(os.path.join(os.environ["SystemRoot"], "System32",
                               "where.exe"), "rb") as f:
            cut = f.read(4096)                  # a real program, cut short
        for data in (b"", cut):
            path = self.write("x.exe", data)
            self.assertIsNotNone(platforms.program_problem(path))
            with self.assertRaises(OSError) as e:
                subprocess.Popen([path], creationflags=0x8, close_fds=True)
            self.assertEqual(e.exception.winerror, 193)


@unittest.skipUnless(WINDOWS, "Windows only")
class TestDllSearch(unittest.TestCase):
    """PyInstaller's bootloader sets a DLL folder that children inherit."""

    def dll_dir(self):
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetDllDirectoryW(1024, buf)
        return buf.value

    def test_a_child_inherits_it(self):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(r"C:\EqUpdater\_internal")
        self.addCleanup(ctypes.windll.kernel32.SetDllDirectoryW, None)
        out = subprocess.run(
            [sys.executable, "-c", "import ctypes;b=ctypes.create_unicode_buffer("
             "1024);ctypes.windll.kernel32.GetDllDirectoryW(1024,b);print(b.value)"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, r"C:\EqUpdater\_internal")

    def test_cleared_for_the_child_and_put_back_after(self):
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(r"C:\EqUpdater\_internal")
        self.addCleanup(ctypes.windll.kernel32.SetDllDirectoryW, None)
        with mock.patch.object(platforms, "frozen", return_value=True):
            with platforms.external_dll_search():
                self.assertEqual(self.dll_dir(), "")
            self.assertEqual(self.dll_dir(), r"C:\EqUpdater\_internal")
            with self.assertRaises(RuntimeError):
                with platforms.external_dll_search():
                    raise RuntimeError
            self.assertEqual(self.dll_dir(), r"C:\EqUpdater\_internal")


@unittest.skipUnless(WINDOWS, "Windows only")
class TestRefusedProgram(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tool = os.path.join(tmp.name, "tool.exe")
        shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "where.exe"),
                    self.tool)

    def refuse(self, code):
        def popen(*a, **k):
            raise OSError(errno.ENOEXEC, "refused", None, code)
        return mock.patch.object(subprocess, "Popen", side_effect=popen)

    def test_a_refused_program_starts_as_a_double_click_does(self):
        with self.refuse(193):
            proc = platforms.spawn_detached([self.tool], cwd=os.path.dirname(self.tool))
        self.assertIsInstance(proc, platforms.ShellProcess)
        self.assertGreater(proc.pid, 0)
        self.assertEqual(proc.wait(10), 2)       # where.exe with no arguments

    def test_other_errors_are_not_retried_that_way(self):
        with self.refuse(2), mock.patch.object(platforms, "shell_start") as shell:
            with self.assertRaises(OSError):
                platforms.spawn_detached([self.tool])
        shell.assert_not_called()

    def test_access_denied_retries_without_breaking_away(self):
        calls = []

        def popen(argv, **k):
            calls.append(k["creationflags"])
            if len(calls) == 1:
                raise OSError(errno.EACCES, "denied", None, 5)
            return mock.Mock(pid=1)
        with mock.patch.object(subprocess, "Popen", side_effect=popen):
            platforms.spawn_detached([self.tool])
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        self.assertTrue(calls[0] & breakaway)
        self.assertFalse(calls[1] & breakaway)

    def test_when_both_ways_fail_the_first_error_is_reported(self):
        with self.refuse(193), mock.patch.object(
                platforms, "shell_start", side_effect=OSError("shell said no")):
            with self.assertRaises(OSError) as e:
                platforms.spawn_detached([self.tool])
        self.assertEqual(e.exception.winerror, 193)


class TestWrittenWhole(unittest.TestCase):
    def test_an_interrupted_write_leaves_the_old_file(self):
        from equpdater import app
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "VanillaFixes.exe")
            with open(dest, "wb") as f:
                f.write(b"old program")
            real_open = open

            def failing(path, mode="r", *a, **k):
                f = real_open(path, mode, *a, **k)
                if path.endswith(".equpdater-new"):
                    f.write(b"half")
                    f.close()
                    raise OSError("disk went away")
                return f
            with mock.patch("builtins.open", side_effect=failing):
                with self.assertRaises(OSError):
                    app._write_whole(dest, b"new program")
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"old program")
            self.assertEqual(os.listdir(d), ["VanillaFixes.exe"])

    def test_a_whole_write_replaces_it(self):
        from equpdater import app
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "a.dll")
            app._write_whole(dest, b"new")
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"new")


if __name__ == "__main__":
    unittest.main()
