"""The Auto Loot setting, as bytes in WoW.exe. No addon.

How the client decides, read from the code (OctoWoW 1.18.1, WoW build 5875):
right-clicking a corpse reaches one of two branches (AUTOLOOT_OFFSETS), each
asking a flag on the player, "was this looted with the auto-loot modifier?".
Stock, the branch auto-loots when the flag is set. Flipped (Auto Loot on), it
auto-loots when it is not, so plain clicks and Shift swap meaning.

Why that was not enough on its own:

- The flag is stale. The client writes it from Shift only when the server's
  loot reply arrives (0x5edca5), so a click reads Shift as it was at the
  *previous* loot. Seen in game 2026-11-08: Shift changed nothing, either
  way. In its place SuperWoW's ``SetAutoloot(v)`` ORs ``v`` into the flag
  before every interact, which SuperAPI calls every frame from a saved
  option, and which inverted the setting.
- The routine that records the flag (0x5df460) also loots an open window at
  once when Shift is down: with Auto Loot on, Shift-click flashed the window
  and took everything (SHIFT_LOOT_OFFSET).

The fix, all in WoW.exe: each branch asks the client's own Shift check
(0x41f8f0, what Lua's ``IsShiftKeyDown`` calls) at the moment of the click
instead of the flag. The two 8-byte flag reads (``mov cl,[eax+0x1d30]; test
cl,cl``) become a call to ten bytes of code in alignment padding next to the
first branch (CAVE_OFFSET), which leaves the answer in the flags the branch
tests. SuperWoW's value is then never read, so no addon can turn the setting
around. With Auto Loot on, the Shift path in 0x5df460 is closed too.

An addon that takes items from the window itself (pfUI's autopickup) is that
addon's own setting; nothing here stops it. 2.1.4's first builds installed an
addon, ``!EqLootGate``, to stand in front of those calls; it is removed."""

from __future__ import annotations

import os
import shutil

#: The two branches in WoW.exe, as file offsets, and what they hold.
AUTOLOOT_OFFSETS = (0x0c1ecf, 0x0c2b25)
JZ, JNZ, JMP = 0x74, 0x75, 0xEB

#: .text is mapped at 0x401000 from file offset 0x1000.
_TEXT_DELTA = 0x400000

#: The flag read before each branch, stock: `mov cl,[eax+0x1d30]; test cl,cl`.
_READ_FLAG = bytes.fromhex("8a88301d000084c9")
SITE_OFFSETS = tuple(off - len(_READ_FLAG) for off in AUTOLOOT_OFFSETS)

#: Alignment padding after the function holding the first branch: twelve
#: `nop`s, never executed. Ten of them become the Shift check.
CAVE_OFFSET = 0x0c1ef4
_CAVE_STOCK = b"\x90" * 10
#: The client's "is this modifier down" (ecx 0 = Shift), as IsShiftKeyDown
#: and the loot reply call it. Returns non-zero when down.
_SHIFT_CHECK_VA = 0x41f8f0


def _rel_call(at_off: int, target_va: int) -> bytes:
    return b"\xe8" + (target_va - (at_off + _TEXT_DELTA + 5)).to_bytes(
        4, "little", signed=True)


#: xor ecx,ecx; call shift check; test eax,eax; ret -- the flags carry the
#: answer back to the branch, which tests them as it tested the flag.
_CAVE = (b"\x31\xc9" + _rel_call(CAVE_OFFSET + 2, _SHIFT_CHECK_VA)
         + b"\x85\xc0\xc3")


def _site(off: int) -> bytes:
    """call the cave, then three nops: eight bytes, as the flag read was."""
    return _rel_call(off, CAVE_OFFSET + _TEXT_DELTA) + b"\x90\x90\x90"


#: A third way the client loots everything: the routine that records the
#: flag (0x5df460) also loots the open window there and then if Shift is
#: down. Stock, that is "Shift takes everything". With Auto Loot on, Shift is
#: what keeps the window open, so this path took everything a moment after
#: the window appeared. Seen in game 2026-11-07. On: the jump past the loot
#: call is made unconditional. Off: stock.
SHIFT_LOOT_OFFSET = 0x1df46e
#: `mov [ecx+0x1d30], al` just before it: the flag being recorded.
_SHIFT_HEAD = b"\x88\x81\x30\x1d\x00\x00"

#: The addon the first 2.1.4 builds installed.
FOLDER = "!EqLootGate"
_GATE_MARK = b"## Author: EqUpdater"


def _layout_of(data: bytes) -> dict | None:
    """What each patched spot holds, or None when any of them is not one of
    the forms this client build has: another build, whose bytes mean nothing
    here."""
    branches, sites = [], []
    for site, off in zip(SITE_OFFSETS, AUTOLOOT_OFFSETS):
        head = data[site:off + 1]
        if len(head) < len(_READ_FLAG) + 1:
            return None
        if head[:-1] == _READ_FLAG:
            sites.append(False)
        elif head[:-1] == _site(site):
            sites.append(True)
        else:
            return None
        if head[-1] not in (JZ, JNZ):
            return None
        branches.append(head[-1])
    cave = data[CAVE_OFFSET:CAVE_OFFSET + len(_CAVE)]
    if cave not in (_CAVE, _CAVE_STOCK):
        return None
    head = data[SHIFT_LOOT_OFFSET - len(_SHIFT_HEAD):SHIFT_LOOT_OFFSET + 1]
    if len(head) < len(_SHIFT_HEAD) + 1             or head[:-1] != _SHIFT_HEAD or head[-1] not in (JZ, JMP):
        return None
    return {"branches": branches, "sites": sites, "cave": cave == _CAVE,
            "shift": head[-1]}


def _layout(exe_path: str) -> dict | None:
    try:
        with open(exe_path, "rb") as f:
            return _layout_of(f.read(SHIFT_LOOT_OFFSET + 1))
    except OSError:
        return None


def exe_state(exe_path: str) -> bool | None:
    """What WoW.exe does now: True auto-loots on a plain click, False needs
    Shift. None when the file cannot be read, or it is another client
    build."""
    layout = _layout(exe_path)
    if layout is None:
        return None
    if layout["branches"] == [JNZ, JNZ]:
        return True
    if layout["branches"] == [JZ, JZ]:
        return False
    return None


def exe_complete(exe_path: str, enabled: bool) -> bool:
    """Whether everything besides the two branches matches ``enabled`` too:
    the branches read Shift, and the Shift path is closed when on.
    OctoLauncher, and the first EqUpdater 2.1.4 builds, set less."""
    layout = _layout(exe_path)
    return (layout is not None and all(layout["sites"]) and layout["cave"]
            and layout["shift"] == (JMP if enabled else JZ))


def patches(enabled: bool) -> list:
    """Every (file offset, bytes) the setting writes. The cave comes before
    the calls into it."""
    return ([(off, bytes([JNZ if enabled else JZ])) for off in AUTOLOOT_OFFSETS]
            + [(SHIFT_LOOT_OFFSET, bytes([JMP if enabled else JZ])),
               (CAVE_OFFSET, _CAVE)]
            + [(site, _site(site)) for site in SITE_OFFSETS])


def patches_for(data, enabled: bool) -> list:
    """``patches`` for this WoW.exe's bytes, or none at all when it is
    another build: a call into code that is not there would crash it."""
    return patches(enabled) if _layout_of(bytes(data[:SHIFT_LOOT_OFFSET + 1]))         else []


def set_exe_state(exe_path: str, enabled: bool) -> None:
    """Write the setting, in place. Raises OSError when it cannot be written
    (the game is running), and ValueError on a build it does not recognise."""
    if _layout(exe_path) is None:
        raise ValueError("unrecognised WoW.exe")
    with open(exe_path, "r+b") as f:
        for off, data in patches(enabled):
            f.seek(off)
            f.write(data)


def remove_gate(addons_dir: str) -> bool:
    """Delete the addon the first 2.1.4 builds installed, if it is ours.
    Returns whether it removed it."""
    folder = os.path.join(addons_dir, FOLDER)
    try:
        with open(os.path.join(folder, FOLDER + ".toc"), "rb") as f:
            if _GATE_MARK not in f.read():
                return False
    except OSError:
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not os.path.exists(folder)
