"""Make the Auto Loot setting win over addons.

What the setting does: it flips one branch, in two places, in WoW.exe (see
AUTOLOOT_OFFSETS). The client asks one flag on the player, "was this looted
with the auto-loot modifier?", and the stock branch auto-loots when the flag
is set; flipped, it auto-loots when it is not. Plain clicks and Shift swap
meaning, and nothing else changes.

What undid it, read from the code that does it:

- SuperWoW's ``SetAutoloot(v)`` keeps ``v`` in the DLL and ORs it into that
  flag before every interact. So SetAutoloot(1) means "always the Shift
  branch": always auto-loot on a stock exe, and *never* on a flipped one.
  SuperAPI, on EqUpdater's recommended list, offers that as a saved option and
  says so in its tooltip ("all of these will be reversed").
- Addons with an auto-loot of their own (pfUI's autopickup, for one) call
  ``LootSlot`` and ``ConfirmLootSlot`` from their LOOT_OPENED handler, which
  takes everything from a window the setting says should stay open.

Neither can be fixed from outside the game. Both go through a Lua global looked
up when it is called, so a small addon loaded first (the "!" sorts ahead of
every ordinary name) stands in front of them. It does the same thing whichever
way the setting is: the flag then means Shift and only Shift, and the branch in
WoW.exe is the one decision. No addon's saved settings are touched and none is
disabled.

It cannot help against code that does not go through those globals: see
the limits in README.md's Auto Loot section."""

from __future__ import annotations

import os

FOLDER = "!EqLootGate"

TOC = """## Interface: 11200
## Title: EqUpdater Loot Gate
## Notes: Keeps EqUpdater's Auto Loot setting in charge of looting. Installed by EqUpdater.
## Author: EqUpdater
EqLootGate.lua
"""

# Lua 5.0: no `...` expressions, no `#`, and getglobal-free global access.
LUA = """-- Installed by EqUpdater; see equpdater/lootgate.py. Edits are overwritten.
EqLootGate = { ignoredSetAutoloot = 0, ignoredLootSlot = 0 }
local gate = EqLootGate

-- SuperWoW ORs this value into the client's autoloot flag before every
-- interact. Held at 0, the flag means Shift and nothing else, and the setting
-- in WoW.exe decides. The value outlives a /reload, so it is cleared on load.
if SetAutoloot then
    local native = SetAutoloot
    native(0)
    SetAutoloot = function(value)
        if value ~= nil then
            gate.ignoredSetAutoloot = gate.ignoredSetAutoloot + 1
        end
        return 0
    end
end

-- A loot window is open only when looting is manual, so taking an item from
-- it is a player's click. A click handler has a string in arg1; OnUpdate has
-- a number, and an event handler for the window runs in the frame it opened.
local openedAt = nil
local frame = CreateFrame("Frame")
frame:RegisterEvent("LOOT_OPENED")
frame:SetScript("OnEvent", function()
    openedAt = GetTime()
end)

local function fromPlayer()
    return type(arg1) == "string" and GetTime() ~= openedAt
end

local function guard(native)
    return function(slot)
        if fromPlayer() then
            return native(slot)
        end
        gate.ignoredLootSlot = gate.ignoredLootSlot + 1
    end
end

LootSlot = guard(LootSlot)
ConfirmLootSlot = guard(ConfirmLootSlot)

SLASH_EQLOOT1 = "/eqloot"
SlashCmdList["EQLOOT"] = function()
    DEFAULT_CHAT_FRAME:AddMessage("EqLootGate: ignored "
        .. gate.ignoredSetAutoloot .. " SetAutoloot and "
        .. gate.ignoredLootSlot .. " automatic loot calls from addons.")
end
"""

#: The two branches in WoW.exe, as file offsets, and what they hold.
AUTOLOOT_OFFSETS = (0x0c1ecf, 0x0c2b25)
JZ, JNZ = 0x74, 0x75
#: `test cl, cl`, just before each: what says this is the same client build.
_TEST_CL = b"\x84\xc9"

#: A third way the client loots everything, which the two branches do not
#: cover: the routine that records the Shift flag (0x5df460) also loots the
#: open window there and then if Shift is down -- when the window opens with
#: Shift held, or Shift is pressed while it is open. Stock, that is "Shift
#: takes everything". With Auto Loot on, Shift is what keeps the window open,
#: so this path took everything a moment after the window appeared. Seen in
#: game 2026-11-07. On: the jump past the loot call is made unconditional, so
#: the two branches alone decide. Off: stock.
SHIFT_LOOT_OFFSET = 0x1df46e
JMP = 0xEB
#: `mov [ecx+0x1d30], al` just before it: the flag being recorded.
_SHIFT_HEAD = b"\x88\x81\x30\x1d\x00\x00"


def _shift_byte(f) -> int | None:
    f.seek(SHIFT_LOOT_OFFSET - len(_SHIFT_HEAD))
    head = f.read(len(_SHIFT_HEAD) + 1)
    if len(head) < len(_SHIFT_HEAD) + 1 or head[:-1] != _SHIFT_HEAD \
            or head[-1] not in (JZ, JMP):
        return None
    return head[-1]


def install(addons_dir: str) -> bool:
    """Write the addon if it is missing or differs. Returns whether it wrote."""
    folder = os.path.join(addons_dir, FOLDER)
    wanted = {FOLDER + ".toc": TOC, "EqLootGate.lua": LUA}
    changed = False
    os.makedirs(folder, exist_ok=True)
    for name, text in wanted.items():
        path = os.path.join(folder, name)
        data = text.encode("utf-8")
        try:
            with open(path, "rb") as f:
                if f.read() == data:
                    continue
        except OSError:
            pass
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        changed = True
    return changed


def exe_state(exe_path: str) -> bool | None:
    """What WoW.exe does now: True auto-loots on a plain click, False needs
    Shift. None when the file cannot be read, or those offsets are not those
    branches: another client build, whose bytes mean nothing here."""
    try:
        with open(exe_path, "rb") as f:
            found = []
            for off in AUTOLOOT_OFFSETS:
                f.seek(off - 2)
                head = f.read(3)
                if len(head) < 3 or head[:2] != _TEST_CL:
                    return None
                found.append(head[2])
            if _shift_byte(f) is None:
                return None
    except OSError:
        return None
    if found == [JNZ, JNZ]:
        return True
    if found == [JZ, JZ]:
        return False
    return None


def exe_complete(exe_path: str, enabled: bool) -> bool:
    """Whether the Shift path matches ``enabled`` too. OctoLauncher, and
    EqUpdater before 2.1.5, set only the two branches."""
    try:
        with open(exe_path, "rb") as f:
            return _shift_byte(f) == (JMP if enabled else JZ)
    except OSError:
        return False


def set_exe_state(exe_path: str, enabled: bool) -> None:
    """Set both branches and the Shift path, in place. Raises OSError when it
    cannot be written (the game is running), and ValueError on a build it does
    not recognise."""
    if exe_state(exe_path) is None:
        raise ValueError("unrecognised WoW.exe")
    with open(exe_path, "r+b") as f:
        for off in AUTOLOOT_OFFSETS:
            f.seek(off)
            f.write(bytes([JNZ if enabled else JZ]))
        f.seek(SHIFT_LOOT_OFFSET)
        f.write(bytes([JMP if enabled else JZ]))
