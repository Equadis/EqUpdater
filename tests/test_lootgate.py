"""The Auto Loot checkbox, the two bytes it sets, and the addon that keeps
addons from undoing them.

The Lua runs for real when an interpreter is on PATH, against a stand-in for
the client. What only the game can show (the native loot path itself) is the
manual list in the README; these cover every decision EqUpdater makes."""

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


def fake_exe(path, byte=lootgate.JZ, test=b"\x84\xc9", shift=lootgate.JZ):
    """The three places the patch reads, as the client has them. ``shift``
    stays stock unless given: OctoLauncher and EqUpdater 2.1.4 left it so."""
    data = bytearray(S + 64)
    for off in (A, B):
        data[off - 2:off] = test
        data[off] = byte
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


class TestInstall(unittest.TestCase):
    def test_writes_once_and_repairs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(lootgate.install(d))
            self.assertFalse(lootgate.install(d))
            lua = os.path.join(d, lootgate.FOLDER, "EqLootGate.lua")
            with open(lua, "w", encoding="utf-8") as f:
                f.write("-- edited")
            self.assertTrue(lootgate.install(d))
            with open(lua, encoding="utf-8") as f:
                self.assertIn("EqLootGate", f.read())
            self.assertEqual(sorted(os.listdir(os.path.join(d, lootgate.FOLDER))),
                             ["!EqLootGate.toc", "EqLootGate.lua"])

    def test_lua_50_safe(self):
        # The suite's Lua is not the game's: a `...` expression or `#` passes
        # here and kills the file in game.
        for bad in ("...", " #", "select(", "string.gmatch", "%s*"):
            self.assertNotIn(bad, lootgate.LUA.replace("-- ", ""), bad)


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
        self.assertEqual(self.app.enforce_autoloot(self.dir),
                         ["Auto Loot: installed the gate addon."])
        self.assertIs(lootgate.exe_state(self.exe), False)

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
        """A client patched on by OctoLauncher or EqUpdater 2.1.4 has the
        branches but not the Shift path: PLAY finishes the job."""
        fake_exe(self.exe, lootgate.JNZ)
        self.cfg["tweaks"] = {"alwaysAutoLoot": True}
        notes = self.app.enforce_autoloot(self.dir)
        self.assertEqual(byte_at(self.exe, S), lootgate.JMP)
        self.assertTrue(any("Shift" in n for n in notes))
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
        buf = bytearray(B + 64)
        for wanted, byte in ((True, lootgate.JNZ), (False, lootgate.JZ)):
            patches = dict((p[0], p[3]) for p in worker.build_tweaks(
                buf, {"alwaysAutoLoot": wanted}))["alwaysAutoLoot"]
            shift = lootgate.JMP if wanted else lootgate.JZ
            self.assertEqual(patches, [(A, bytes([byte])), (B, bytes([byte])),
                                       (S, bytes([shift]))])


HARNESS = r"""
-- A stand-in for the client: only what the gate touches.
local now = 100
function GetTime() return now end
local handlers = {}
function CreateFrame() return {
    RegisterEvent = function() end,
    SetScript = function(self, _, fn) handlers.event = fn end } end
SlashCmdList = {}
DEFAULT_CHAT_FRAME = { AddMessage = function() end }
local swValue, looted, confirmed = 1, 0, 0     -- SuperWoW's value survives /reload
if WITH_SUPERWOW then SetAutoloot = function(v) if v then swValue = v end return swValue end end
LootSlot = function() looted = looted + 1 end
ConfirmLootSlot = function() confirmed = confirmed + 1 end

dofile(GATE)

local function check(what, got, want)
    if got ~= want then
        print("FAIL " .. what .. ": got " .. tostring(got) .. ", want " .. tostring(want))
        os.exit(1)
    end
end

if WITH_SUPERWOW then
    check("cleared on load", swValue, 0)
    SetAutoloot(1)                     -- SuperAPI "Always on", every frame
    check("addon cannot set it", swValue, 0)
    check("still looks present", type(SetAutoloot), "function")
else
    check("nothing invented", SetAutoloot, nil)
end

-- pfUI's autopickup: from LOOT_OPENED, in the frame the window opened.
arg1 = nil
handlers.event()
LootSlot(1); ConfirmLootSlot(1)
check("event handler loots nothing", looted + confirmed, 0)
arg1 = "LeftButton"                    -- even with a click left in arg1
LootSlot(1)
check("same frame loots nothing", looted, 0)

-- A staged addon: OnUpdate, later frames.
now = 100.5
arg1 = 0.016
LootSlot(1)
check("OnUpdate loots nothing", looted, 0)

-- The player clicks an item, and accepts a bind-on-pickup popup.
now = 101
arg1 = "LeftButton"
LootSlot(2)
check("a click loots", looted, 1)
ConfirmLootSlot(2)
check("a click confirms", confirmed, 1)
arg1 = "ENTER"
ConfirmLootSlot(3)
check("the Enter key confirms", confirmed, 2)
SlashCmdList["EQLOOT"]()
print("ok")
"""


class TestGateLua(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = next((p for p in (shutil.which(n) for n in ("lua", "luajit", "lua5.1"))
                        if p), None)
        if not cls.lua:
            raise unittest.SkipTest("no Lua interpreter here")

    def run_gate(self, superwow):
        with tempfile.TemporaryDirectory() as d:
            lootgate.install(d)
            gate = os.path.join(d, lootgate.FOLDER, "EqLootGate.lua").replace("\\", "/")
            script = os.path.join(d, "harness.lua")
            with open(script, "w", encoding="utf-8") as f:
                f.write(f'GATE = "{gate}"\nWITH_SUPERWOW = {str(superwow).lower()}\n'
                        + HARNESS)
            return subprocess.run([self.lua, script], capture_output=True, text=True)

    def test_with_superwow(self):
        r = self.run_gate(True)
        self.assertEqual((r.stdout.strip(), r.stderr), ("ok", ""))

    def test_without_superwow(self):
        r = self.run_gate(False)
        self.assertEqual((r.stdout.strip(), r.stderr), ("ok", ""))


if __name__ == "__main__":
    unittest.main()
