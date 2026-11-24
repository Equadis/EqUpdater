"""The Auto Loot checkbox and the bytes it sets in WoW.exe.

What only the game can show (the loot path itself) was checked by hand; these
cover every decision EqUpdater makes."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from equpdater import lootgate  # noqa: E402

A, B = lootgate.AUTOLOOT_OFFSETS


S = lootgate.SHIFT_LOOT_OFFSET
C = lootgate.CAVE_OFFSET


def fake_exe(path, byte=lootgate.JZ, test=b"\x84\xc9", shift=lootgate.JZ):
    """Every place the patch reads, as the stock client has them. ``shift``
    stays stock unless given: OctoLauncher and EqUpdater 2.1.4 left it so."""
    data = bytearray(S + 64)
    for off in (A, B):
        data[off - 8:off - 2] = b"\x8a\x88\x30\x1d\x00\x00"
        data[off - 2:off] = test
        data[off] = byte
    data[C:C + 12] = b"\x90" * 12
    data[S - 6:S] = b"\x88\x81\x30\x1d\x00\x00"
    data[S] = shift
    with open(path, "wb") as f:
        f.write(data)


def byte_at(path, off):
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(1)[0]


class TestExeBytes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="equ-loot-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.exe = os.path.join(self.dir, "WoW.exe")

    def test_both_states_read_and_reverse(self):
        fake_exe(self.exe, lootgate.JZ)
        self.assertIs(lootgate.exe_state(self.exe), False)
        lootgate.set_exe_state(self.exe, True)
        self.assertIs(lootgate.exe_state(self.exe), True)
        with open(self.exe, "rb") as f:
            data = f.read()
        self.assertEqual((data[A], data[B]), (lootgate.JNZ, lootgate.JNZ))
        lootgate.set_exe_state(self.exe, False)
        self.assertIs(lootgate.exe_state(self.exe), False)

    def test_on_also_stops_shift_taking_everything(self):
        """Seen in game: Auto Loot on, Shift held, the window flashed and
        everything was taken -- by the routine that records Shift. On, its
        jump past the loot call is unconditional; off, it is stock."""
        fake_exe(self.exe, lootgate.JZ)
        lootgate.set_exe_state(self.exe, True)
        self.assertEqual(byte_at(self.exe, S), lootgate.JMP)
        self.assertTrue(lootgate.exe_complete(self.exe, True))
        lootgate.set_exe_state(self.exe, False)
        self.assertEqual(byte_at(self.exe, S), lootgate.JZ)
        self.assertTrue(lootgate.exe_complete(self.exe, False))

    def test_both_branches_ask_shift_not_the_flag(self):
        """The flag is stale and SuperWoW writes into it: each branch calls
        the client's own Shift check instead, whichever way it is set."""
        fake_exe(self.exe, lootgate.JZ)
        for on in (True, False):
            lootgate.set_exe_state(self.exe, on)
            with open(self.exe, "rb") as f:
                data = f.read()
            cave_va = C + 0x400000
            for off in (A, B):
                site = off - 8
                self.assertEqual(data[site], 0xE8)
                rel = int.from_bytes(data[site + 1:site + 5], "little", signed=True)
                self.assertEqual(site + 0x400000 + 5 + rel, cave_va)
                self.assertEqual(data[site + 5:off], b"\x90\x90\x90")
            self.assertEqual(data[C:C + 2], b"\x31\xc9")          # Shift
            rel = int.from_bytes(data[C + 3:C + 7], "little", signed=True)
            self.assertEqual(cave_va + 7 + rel, 0x41f8f0)
            self.assertEqual(data[C + 7:C + 10], b"\x85\xc0\xc3")
            self.assertEqual(data[C + 10:C + 12], b"\x90\x90")   # untouched
            self.assertTrue(lootgate.exe_complete(self.exe, on))
            self.assertIs(lootgate.exe_state(self.exe), on)

    def test_half_a_patch_is_incomplete(self):
        """Calls into a cave that is not there would crash the game."""
        fake_exe(self.exe, lootgate.JZ)
        lootgate.set_exe_state(self.exe, False)
        with open(self.exe, "r+b") as f:
            f.seek(C)
            f.write(b"\x90" * 10)
        self.assertIs(lootgate.exe_state(self.exe), False)
        self.assertFalse(lootgate.exe_complete(self.exe, False))

    def test_an_older_patch_reads_on_but_incomplete(self):
        fake_exe(self.exe, lootgate.JNZ)            # branches only
        self.assertIs(lootgate.exe_state(self.exe), True)
        self.assertFalse(lootgate.exe_complete(self.exe, True))

    def test_a_different_shift_routine_is_another_build(self):
        fake_exe(self.exe, lootgate.JZ)
        with open(self.exe, "r+b") as f:
            f.seek(S - 1)
            f.write(b"\x90")
        self.assertIsNone(lootgate.exe_state(self.exe))

    def test_one_branch_flipped_is_not_either_state(self):
        fake_exe(self.exe, lootgate.JZ)
        with open(self.exe, "r+b") as f:
            f.seek(A)
            f.write(bytes([lootgate.JNZ]))
        self.assertIsNone(lootgate.exe_state(self.exe))

    def test_another_build_is_left_alone(self):
        fake_exe(self.exe, lootgate.JZ, test=b"\x90\x90")
        self.assertIsNone(lootgate.exe_state(self.exe))
        with self.assertRaises(ValueError):
            lootgate.set_exe_state(self.exe, True)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read()[A], lootgate.JZ)

    def test_a_missing_exe_is_no_state(self):
        self.assertIsNone(lootgate.exe_state(self.exe))

    def test_real_client_offsets_when_present(self):
        # A real client to read, if one is named: EQUPDATER_TEST_CLIENT.
        real = os.path.join(os.environ.get("EQUPDATER_TEST_CLIENT", ""), "WoW.exe")
        if not os.environ.get("EQUPDATER_TEST_CLIENT") or not os.path.exists(real):
            self.skipTest("no client here")
        self.assertIn(lootgate.exe_state(real), (True, False))


class TestOldGate(unittest.TestCase):
    def test_ours_is_removed_and_nobody_elses(self):
        with tempfile.TemporaryDirectory() as d:
            gate = os.path.join(d, lootgate.FOLDER)
            os.makedirs(gate)
            with open(os.path.join(gate, lootgate.FOLDER + ".toc"), "w") as f:
                f.write("## Interface: 11200\n## Author: EqUpdater\n")
            self.assertTrue(lootgate.remove_gate(d))
            self.assertFalse(os.path.exists(gate))
            self.assertFalse(lootgate.remove_gate(d))       # nothing there
            os.makedirs(gate)
            with open(os.path.join(gate, lootgate.FOLDER + ".toc"), "w") as f:
                f.write("## Author: someone else\n")
            self.assertFalse(lootgate.remove_gate(d))
            self.assertTrue(os.path.isdir(gate))


class TestCheckboxAndLaunch(unittest.TestCase):
    def setUp(self):
        from equpdater import app
        self.app = app
        self.dir = tempfile.mkdtemp(prefix="equ-loot-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.exe = os.path.join(self.dir, "WoW.exe")
        self.cfg = {"out_dir": self.dir}
        patchers = [
            mock.patch.object(app, "load_config", lambda *a, **k: self.cfg),
            mock.patch.object(app, "client_exe_locked", lambda d: False),
            mock.patch.object(app, "writable_addons_path",
                              lambda d: os.path.join(d, "Interface", "AddOns")),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_octolauncher_client_off_reads_off(self):
        # OctoLauncher left it off; the default of on must not flip it.
        fake_exe(self.exe, lootgate.JZ)
        self.assertIs(self.app.load_tweaks_config()["alwaysAutoLoot"], False)
        notes = self.app.enforce_autoloot(self.dir)
        self.assertEqual(len(notes), 1)
        self.assertIn("reads Shift", notes[0])
        self.assertIs(lootgate.exe_state(self.exe), False)
        self.assertTrue(lootgate.exe_complete(self.exe, False))
        self.assertEqual(self.app.enforce_autoloot(self.dir), [])

    def test_octolauncher_client_on_reads_on(self):
        fake_exe(self.exe, lootgate.JNZ)
        self.assertIs(self.app.load_tweaks_config()["alwaysAutoLoot"], True)

    def test_a_saved_choice_outranks_the_file_and_is_put_back_at_play(self):
        # EqUpdater says off; OctoLauncher played since and wrote on.
        fake_exe(self.exe, lootgate.JNZ)
        self.cfg["tweaks"] = {"alwaysAutoLoot": False}
        self.assertIs(self.app.load_tweaks_config()["alwaysAutoLoot"], False)
        notes = self.app.enforce_autoloot(self.dir)
        self.assertIs(lootgate.exe_state(self.exe), False)
        self.assertTrue(any("did not match" in n for n in notes))
        self.cfg["tweaks"] = {"alwaysAutoLoot": True}
        self.app.enforce_autoloot(self.dir)
        self.assertIs(lootgate.exe_state(self.exe), True)

    def test_play_completes_an_older_patch(self):
        """A client patched on by OctoLauncher or an earlier EqUpdater has the
        branches but not the rest: PLAY finishes the job, and takes away the
        addon those EqUpdater builds installed."""
        gate = os.path.join(self.dir, "Interface", "AddOns", lootgate.FOLDER)
        os.makedirs(gate)
        with open(os.path.join(gate, lootgate.FOLDER + ".toc"), "w") as f:
            f.write("## Author: EqUpdater\n")
        fake_exe(self.exe, lootgate.JNZ)
        self.cfg["tweaks"] = {"alwaysAutoLoot": True}
        notes = self.app.enforce_autoloot(self.dir)
        self.assertEqual(byte_at(self.exe, S), lootgate.JMP)
        self.assertTrue(lootgate.exe_complete(self.exe, True))
        self.assertTrue(any("Shift" in n for n in notes))
        self.assertTrue(any("removed" in n for n in notes))
        self.assertFalse(os.path.exists(gate))
        self.assertEqual(self.app.enforce_autoloot(self.dir),
                         [])                         # nothing left to do

    def test_running_game_is_not_written_to(self):
        fake_exe(self.exe, lootgate.JNZ)
        self.cfg["tweaks"] = {"alwaysAutoLoot": False}
        with mock.patch.object(self.app, "client_exe_locked", lambda d: True):
            notes = self.app.enforce_autoloot(self.dir)
        self.assertIs(lootgate.exe_state(self.exe), True)
        self.assertTrue(any("in use" in n for n in notes))

    def test_unknown_build_is_left_alone(self):
        fake_exe(self.exe, lootgate.JZ, test=b"\x90\x90")
        self.cfg["tweaks"] = {"alwaysAutoLoot": True}
        self.app.enforce_autoloot(self.dir)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read()[A], lootgate.JZ)

    def test_the_patch_uses_the_same_bytes_both_ways(self):
        worker = self.app.UpdateWorker(self.dir, None, None)
        fake_exe(self.exe, lootgate.JNZ)
        with open(self.exe, "rb") as f:
            buf = bytearray(f.read())
        for wanted in (True, False):
            patches = dict((p[0], p[3]) for p in worker.build_tweaks(
                buf, {"alwaysAutoLoot": wanted}))["alwaysAutoLoot"]
            self.assertEqual(patches, lootgate.patches(wanted))
            out = bytearray(buf)
            for off, data in patches:
                out[off:off + len(data)] = data
            with open(self.exe, "wb") as f:
                f.write(out)
            self.assertIs(lootgate.exe_state(self.exe), wanted)
            self.assertTrue(lootgate.exe_complete(self.exe, wanted))

    def test_apply_writes_nothing_into_another_build(self):
        worker = self.app.UpdateWorker(self.dir, None, None)
        buf = bytearray(S + 64)                     # nothing where it looks
        patches = dict((p[0], p[3]) for p in worker.build_tweaks(
            buf, {"alwaysAutoLoot": True}))["alwaysAutoLoot"]
        self.assertEqual(patches, [])


if __name__ == "__main__":
    unittest.main()
