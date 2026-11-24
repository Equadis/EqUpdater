"""Tweaks: the WoW.exe binary patches, Config.wtf, and the stored values.

Lifted verbatim from ``equpdater/app.py`` (``tools/lift.py``); ``app.py``
re-exports every name. No Tk, no Qt. Logging goes through ``logsink``.
"""

import math
import os
import shutil
import struct
import subprocess

from .. import branding, gamepaths, lootgate, platforms
from ..config import ensure_dir, load_config, update_config
from ..i18n import N_
from . import atomic
from . import oplock
from .logsink import log

APP_DATA_DIR = branding.app_data_dir()


PRISTINE_WOW_PATH  = os.path.join(APP_DATA_DIR, "base-WoW.exe")


# The pristine (unpatched) WoW.exe carries 0xa1 at this offset; a patched one
# carries 0xb8 (see the locale patch). Used to cache a clean base for re-patch.
_LOCALE_ASSERT_OFFSET = 0x1b2115


# Game-language (WoW.exe locale) patch
# The language is switched by patching three spots in WoW.exe. 
# LOCALE_NAMES is the exe's built-in 8-slot locale table (at
# 0x45591c - index*8). Selectable languages map to a slot index; ruRU and ptBR
# reuse the unused zhTW / xxYY slots and rename them. Offsets/bytes verified
# against the pristine base-WoW.exe.
_LOCALE_TAG_OFFSET   = 0x1b2115                 # the 0xa1/0xb8 assert instruction


_LOCALE_INDEX_OFFSET = 0x253c


_LOCALE_NAME_OFFSET  = lambda i: 0x45591c - i * 8


LOCALE_NAMES = ["enUS", "koKR", "frFR", "deDE",
                "zhCN", "zhTW", "esES", "xxYY"]


# selectable code -> (slot index, display label), in menu order
LOCALES = {
    "enUS": (0, "English"),
    "deDE": (3, "Deutsch"),
    "ruRU": (5, "Русский"),
    "zhCN": (4, "中文 (简体)"),
    "esES": (6, "Español"),
    "ptBR": (7, "Português (BR)"),
}


DEFAULT_LOCALE = "enUS"


def locale_patches(locale: str):
    """The three WoW.exe byte edits that force the given game language, as
    (offset, bytes) tuples applied on top of the pristine base."""
    if locale not in LOCALES:
        locale = DEFAULT_LOCALE
    idx, _ = LOCALES[locale]
    carrier = LOCALE_NAMES[idx]                 # exe slot name at this index
    return [
        # assert instruction: mov eax, <carrier as reversed dword> (was mov eax,[imm])
        (_LOCALE_TAG_OFFSET,
         bytes([0xb8]) + bytes(carrier, "latin1")[::-1]),
        # locale index: mov esi, <index>; jmp +0x1f
        (_LOCALE_INDEX_OFFSET,
         bytes([0xbe, idx, 0x00, 0x00, 0x00, 0xeb, 0x1f])),
        # rename the slot's locale-name string to the selected tag
        (_LOCALE_NAME_OFFSET(idx), bytes(locale, "latin1")),
    ]


def refresh_pristine_wow(client_dir: str):
    """Cache the freshly-synced (unpatched) WoW.exe as the pristine base, so a
    later re-patch always starts from clean bytes."""
    exe = os.path.join(client_dir, "WoW.exe")
    try:
        with open(exe, "rb") as f:
            f.seek(_LOCALE_ASSERT_OFFSET)
            pristine = f.read(1) == b"\xa1"
        if pristine:
            shutil.copyfile(exe, PRISTINE_WOW_PATH)
            log("Cached pristine WoW.exe base.", "dim")
    except OSError:
        pass


def read_pristine_wow(client_dir: str) -> bytes:
    """The clean base to patch from: the cached pristine exe if present, else
    the on-disk WoW.exe."""
    if os.path.exists(PRISTINE_WOW_PATH):
        with open(PRISTINE_WOW_PATH, "rb") as f:
            return f.read()
    with open(os.path.join(client_dir, "WoW.exe"), "rb") as f:
        return f.read()


def client_exe_locked(out_dir: str) -> bool:
    """True when WoW.exe in *out_dir* must not be written now.

    Windows: asked by trying to open WoW.exe for writing rather than by
    listing processes, because the question that matters is not "is a
    program called WoW running somewhere" -- it is "can I write *this*
    file". Windows locks a running image against writing, so the answer is
    exact for the file we are about to patch, it needs no extra dependency,
    and it is right when the player is running a second install from
    another folder. "r+b" does not truncate, where the "wb" the patcher
    itself uses would.

    Linux locks nothing: a game running under Wine would simply be
    overwritten. There the processes are asked whether this client's
    WoW.exe is running, and a read-only WoW.exe counts as well -- see
    platforms.client_in_use.

    Named for what it tests rather than for the usual cause. A running game
    is why this is nearly always true, but a read-only file is the same
    answer to the same question, and the message below says both rather
    than asserting the one it cannot know.
    """
    exe = os.path.join(out_dir, "WoW.exe")
    if not os.path.exists(exe):
        return False
    return platforms.client_in_use(out_dir)


CLOSE_THE_GAME = ("WoW.exe cannot be written. Close World of Warcraft if it is "
                  "running, then run this again. (If the game is closed, check "
                  "that WoW.exe is not read-only.)")


def write_config_wtf(client_dir: str, tweaks: dict | None = None):
    """Write a fresh Config.wtf from scratch, overwriting any existing one.
    Never raises – logs the error if the file can't be written (read-only,
    locked by a running game, or an unwritable folder)."""
    if tweaks is None:
        tweaks = load_tweaks_config()
    far_clip  = tweaks.get("farClip",        TWEAKS_DEFAULTS["farClip"])
    cam_dist  = tweaks.get("cameraDistance", TWEAKS_DEFAULTS["cameraDistance"])
    nameplate = tweaks.get("nameplateRange", TWEAKS_DEFAULTS["nameplateRange"])
    fov_deg   = tweaks.get("fieldOfView",    TWEAKS_DEFAULTS["fieldOfView"])
    fov_rad   = round(fov_deg * math.pi / 180.0, 6)
    bg_sound  = 1 if tweaks.get("soundInBackground",
                                TWEAKS_DEFAULTS["soundInBackground"]) else 0
    channels  = sound_channels(tweaks)

    di  = _get_display_info_safe()
    # No realmList here. The login route lives in realmlist.wtf
    # (play.octowow.st, see logindoctor.LOGIN_HOST) and the client saves the
    # value it used back into Config.wtf itself; a realmList written here
    # could only disagree with it. EqUpdater used to write "octowow.st",
    # the website's address, which Login Doctor now reports and repairs.
    vars_ = {
        "patchList": "octowow.st",
        "readTOS": 1, "readEULA": 1,
        "profanityFilter": 0,
        "gxResolution": f"{di['width']}x{di['height']}",
        "gxWindow": 1, "gxMaximize": 1,
        "gxVSync": 0,
        "gxColorBits": 24, "gxDepthBits": 24,
        "gxRefresh": di["refresh_rate"],
        "gxMultisampleQuality": 0, "gxMultisample": 2,
        "hwDetect": 0,
        "pixelShaders": 1, "M2UsePixelShaders": 1,
        "specular": 1,
        "anisotropic": 16, "trilinear": 1,
        "lod": 0, "lodDist": 100,
        "texLodBias": 0,
        "shadowLevel": 0,
        "particleDensity": 1,
        "fullAlpha": 1,
        "SmallCull": 0.01,
        "farClip": far_clip,
        "DistCull": 888.8,
        "frillDensity": 128,
        "unitDrawDist": 300,
        "weatherDensity": 3,
        "FoV": fov_rad,
        "NameplateRange": nameplate,
        "CameraDistanceMax": cam_dist,
        "cameraDistanceMaxFactor": 1,
        "scriptMemory": 512000,
        "uiScale": 1,
        "mouseSpeed": 1,
        "autoSelfCast": 1,
        "movie": 0,
        "movieSubtitle": 1,
        "checkAddonVersion": 0,
        "minimapZoom": 0, "minimapInsideZoom": 0,
        "EnableErrorSpeech": 0,
        "SoundZoneMusicNoDelay": 1,
        "SoundMaxHardwareChannels": channels,
        "SoundSoftwareChannels": channels,
        "UncapSounds": 1,
        "BackgroundSound": bg_sound,
        "NP_NameplateDistance": nameplate,
        "NP_SpellQueueWindowMs": 150,
        "NP_EnableAuraCastEvents": 1,
        "NP_EnableAutoAttackEvents": 1,
        "NP_EnableSpellStartEvents": 1,
        "NP_EnableSpellGoEvents": 1,
        "NP_EnableSpellHealEvents": 1,
        "NP_QueueCastTimeSpells": 0,
        "NP_QueueInstantSpells": 0,
        "NP_QueueChannelingSpells": 0,
        "NP_QueueTargetingSpells": 0,
        "NP_QueueSpellsOnCooldown": 0,
        "NP_ChatBubbleDistance": 60,
        "NP_ChatBubblesWhisper": 1,
        "NP_ChatBubblesRaid": 1,
        "NP_ChatBubblesBattleground": 1,
        "ChatBubblesParty": 1,
    }
    try:
        cfg_dir = gamepaths.game_path(client_dir, "WTF")
        ensure_dir(cfg_dir)
        with atomic.replacing(gamepaths.game_path(client_dir, "WTF", "Config.wtf"),
                              "w", encoding="utf-8") as f:
            for k, v in vars_.items():
                f.write(f'SET {k} "{v}"\n')
        log("Config.wtf written.", "ok")
    except Exception as e:
        log(f"Could not write Config.wtf: {e}", "err")


def sound_channels(tweaks: dict) -> int:
    """The Sound Channels tweak as a whole number in range: the game reads
    both of its channel settings as integers, and a hand-edited config can
    hold anything."""
    lo, hi = TWEAKS_LIMITS["soundChannels"]
    try:
        value = int(round(float(tweaks.get("soundChannels",
                                           TWEAKS_DEFAULTS["soundChannels"]))))
    except (TypeError, ValueError):
        value = TWEAKS_DEFAULTS["soundChannels"]
    return max(lo, min(hi, value))


def update_config_wtf(client_dir: str, tweaks: dict):
    cfg_path = gamepaths.game_path(client_dir, "WTF", "Config.wtf")
    if not os.path.exists(cfg_path):
        write_config_wtf(client_dir, tweaks)
        return

    far_clip  = tweaks.get("farClip",        TWEAKS_DEFAULTS["farClip"])
    cam_dist  = tweaks.get("cameraDistance", TWEAKS_DEFAULTS["cameraDistance"])
    nameplate = tweaks.get("nameplateRange", TWEAKS_DEFAULTS["nameplateRange"])
    fov_deg   = tweaks.get("fieldOfView",    TWEAKS_DEFAULTS["fieldOfView"])
    fov_rad   = round(fov_deg * math.pi / 180.0, 6)
    bg_sound  = 1 if tweaks.get("soundInBackground",
                                TWEAKS_DEFAULTS["soundInBackground"]) else 0
    channels  = sound_channels(tweaks)
    updates = {
        "farClip":              str(far_clip),
        "CameraDistanceMax":    str(cam_dist),
        "NP_NameplateDistance": str(nameplate),
        "FoV":                  str(fov_rad),
        "NameplateRange":       str(nameplate),
        "BackgroundSound":      str(bg_sound),
        "SoundSoftwareChannels":    str(channels),
        "SoundMaxHardwareChannels": str(channels),
    }

    with open(cfg_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        matched = False
        for key, val in updates.items():
            if line.strip().lower().startswith(f"set {key.lower()} "):
                new_lines.append("SET " + key + ' "' + val + '"\n')
                updated_keys.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append("SET " + key + ' "' + val + '"\n')

    with atomic.replacing(cfg_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    log(f"  Config.wtf updated: farClip={far_clip}, CameraDistanceMax={cam_dist}, "
        f"NameplateRange={nameplate}, NP_NameplateDistance={nameplate}, "
        f"FoV={fov_rad}", "dim")


# These defaults deliberately match what the stock OctoWoW client and its
# official launcher already write into WoW.exe, so installing the updater does
# not change how the game looks until the player asks for a change. The
# upstream farClip 777 / frillDistance 120 / cameraDistance 50 silently
# replaced the launcher's 1000 / 70 / 100 on a fresh install: a shorter world
# draw distance, nearly double the ground clutter to render, and half the
# camera zoom-out.
TWEAKS_DEFAULTS = {
    "locale":          DEFAULT_LOCALE,
    "alwaysAutoLoot":  True,
    "nameplateRange":  41,
    "fieldOfView":     110,
    "farClip":         1000,
    "frillDistance":   70,
    "cameraDistance":  100,
    "soundInBackground": True,
    # What EqUpdater has always written for both channel settings.
    "soundChannels":   64,
}


TWEAKS_ITEMS = [
    (None, N_("GENERAL"), "section", False, None, None, None, None, None),

    ("locale",            N_("Language"),    "dropdown", False, None,
     None,
     None, None, None),

    ("alwaysAutoLoot",    N_("Auto Loot"),        "checkbox", True,  None,
     N_("On: looting takes everything, and Shift opens the loot window. Off: "
        "looting opens the window, and Shift takes everything. This wins over "
        "addons with an auto-loot of their own."),
     None, None, None),

    ("nameplateRange",    N_("Nameplate Range"),          "number",   False, None,
     N_("Distance at which nameplates are visible. [0–41]"),
     0, 41, 1),

    (None, N_("CAMERA"), "section", False, None, None, None, None, None),

    ("fieldOfView",       N_("Field of View"),            "number",   False, None,
     N_("Recommended values by aspect ratio: [4:3 = 90] [16:9 = 110] [21:9 = 150] [32:9 = 180]"),
     90, 180, 5),

    ("cameraDistance",    N_("Camera Distance"),          "number",   False, None,
         N_("Maximum camera zoom-out distance. [50–100]"),
         50, 100, 1),

    (None, N_("GRAPHICS"), "section", False, None, None, None, None, None),

    ("farClip",           N_("World Distance"),          "number",   False, None,
     N_("How far terrain and world objects are drawn. [100–10,000]"),
     100, 10000, 1),

    ("frillDistance",     N_("Ground Clutter Distance"),  "number",   False, None,
     N_("How far grass and small rocks are drawn. [0–300]"),
     0, 300, 1),

    (None, N_("SOUND"), "section", False, None, None, None, None, None),

    ("soundInBackground", N_("Sound in Background"),        "checkbox", True,  None,
     N_("Allows game sounds to play in the background."),
     None, None, None),

    ("soundChannels",     N_("Sound Channels"),           "number",   False, None,
     N_("How many sounds can play at once. More keeps sounds from cutting out in busy fights. [12–64]"),
     12, 64, 1),
]


# {tweak_id: (min, max)} for every numeric tweak — the single source of
# truth for clamping, wherever the value is read from the UI.
TWEAKS_LIMITS = {t[0]: (t[6], t[7]) for t in TWEAKS_ITEMS
                 if t[0] is not None and t[2] == "number"}


_FOV_REFS = [
    (4  / 3,   90),
    (16 / 9,  110),
    (21 / 9,  150),
    (32 / 9,  180),
]


def fov_default_for_display() -> int:
    try:
        info  = _get_display_info_safe()
        ratio = info["width"] / info["height"] if info["height"] else 16 / 9
    except Exception:
        ratio = 16 / 9

    if ratio <= _FOV_REFS[0][0]:
        return _FOV_REFS[0][1]
    if ratio >= _FOV_REFS[-1][0]:
        return _FOV_REFS[-1][1]
    for i in range(len(_FOV_REFS) - 1):
        r0, f0 = _FOV_REFS[i]
        r1, f1 = _FOV_REFS[i + 1]
        if r0 <= ratio <= r1:
            t   = (ratio - r0) / (r1 - r0)
            raw = f0 + t * (f1 - f0)
            return round(round(raw / 5) * 5)
    return 110


#: What Config.wtf gets where the display cannot be asked (Linux, or a
#: failed query). The game runs windowed and maximised (gxWindow,
#: gxMaximize), so the window still fills the screen.
_DISPLAY_FALLBACK = {"width": 1920, "height": 1080, "refresh_rate": 60}


def _get_display_info_safe() -> dict:
    """The primary display's mode; never raises. Windows asks the display
    driver. Elsewhere -- this runs on worker threads, where Tk may not be
    asked -- it is the fallback: before, ctypes.windll raised on Linux and
    a fresh Config.wtf was never written there."""
    if not platforms.WINDOWS:
        return dict(_DISPLAY_FALLBACK)
    try:
        info = _query_display_info()
    except Exception:
        return dict(_DISPLAY_FALLBACK)
    if not info["width"] or not info["height"]:
        return dict(_DISPLAY_FALLBACK)
    return info


def _query_display_info() -> dict:
    import ctypes
    ENUM_CURRENT_SETTINGS = -1

    class DEVMODE(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName",         ctypes.c_wchar * 32),
            ("dmSpecVersion",        ctypes.c_ushort),
            ("dmDriverVersion",      ctypes.c_ushort),
            ("dmSize",               ctypes.c_ushort),
            ("dmDriverExtra",        ctypes.c_ushort),
            ("dmFields",             ctypes.c_ulong),
            ("dmPositionX",          ctypes.c_long),
            ("dmPositionY",          ctypes.c_long),
            ("dmDisplayOrientation", ctypes.c_ulong),
            ("dmDisplayFixedOutput", ctypes.c_ulong),
            ("dmColor",              ctypes.c_short),
            ("dmDuplex",             ctypes.c_short),
            ("dmYResolution",        ctypes.c_short),
            ("dmTTOption",           ctypes.c_short),
            ("dmCollate",            ctypes.c_short),
            ("dmFormName",           ctypes.c_wchar * 32),
            ("dmLogPixels",          ctypes.c_ushort),
            ("dmBitsPerPel",         ctypes.c_ulong),
            ("dmPelsWidth",          ctypes.c_ulong),
            ("dmPelsHeight",         ctypes.c_ulong),
            ("dmDisplayFlags",       ctypes.c_ulong),
            ("dmDisplayFrequency",   ctypes.c_ulong),
        ]

    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    ctypes.windll.user32.EnumDisplaySettingsW(
        None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm))
    return {"width": dm.dmPelsWidth, "height": dm.dmPelsHeight,
            "refresh_rate": dm.dmDisplayFrequency}


def load_tweaks_config() -> dict:
    cfg    = load_config()
    stored = cfg.get("tweaks", {})
    # The aspect-ratio figure from fov_default_for_display() is a *suggestion*
    # shown beside the Field of View box - it is never applied on its own.
    # Seeding the default from it rewrote WoW.exe's field of view behind the
    # player's back: a 3440x1440 display silently became 150 degrees.
    values = {k: stored.get(k, v) for k, v in TWEAKS_DEFAULTS.items()}
    # Never set here: an existing client (OctoLauncher's, say) already does
    # one or the other, and the checkbox says which rather than a default
    # that a later Apply would then write over the player's choice.
    if "alwaysAutoLoot" not in stored and cfg.get("out_dir"):
        state = lootgate.exe_state(os.path.join(cfg["out_dir"], "WoW.exe"))
        if state is not None:
            values["alwaysAutoLoot"] = state
    return values


def save_tweaks_config(values: dict):
    update_config(lambda c: c.__setitem__("tweaks", values))


def build_tweaks(buf, tweaks: dict | None = None):
    if tweaks is None:
        tweaks = load_tweaks_config()

    fov_deg = tweaks.get("fieldOfView", TWEAKS_DEFAULTS["fieldOfView"])
    fov     = fov_deg * (math.pi / 180.0)
    flags   = struct.unpack_from("<H", buf, 0x126)[0] | 0x20

    nameplate = float(tweaks.get("nameplateRange", TWEAKS_DEFAULTS["nameplateRange"]))
    far_clip  = float(tweaks.get("farClip",        TWEAKS_DEFAULTS["farClip"]))
    frill     = float(tweaks.get("frillDistance",  TWEAKS_DEFAULTS["frillDistance"]))
    cam_dist  = float(tweaks.get("cameraDistance", TWEAKS_DEFAULTS["cameraDistance"]))
    snd_bg    = 0x27 if tweaks.get("soundInBackground", TWEAKS_DEFAULTS["soundInBackground"]) else 0x14

    always_loot = tweaks.get("alwaysAutoLoot", TWEAKS_DEFAULTS["alwaysAutoLoot"])
    locale      = tweaks.get("locale", TWEAKS_DEFAULTS["locale"])

    # fmt: off
    return [
        ("gameLanguage", "bytes", None, locale_patches(locale)),
        ("largeAddress",          "uint16", 0x126,     flags),
        ("fieldOfView",           "float",  0x4089b4,  fov),
        ("cameraDistance",        "float",  0x4089a4,  cam_dist),
        ("farClip",               "float",  0x40fed8,  far_clip),
        ("frillDistance",         "float",  0x467958,  frill),
        ("nameplateRange",        "float",  0x40c448,  nameplate),
        ("soundInBackground",     "int8",   0x3a4869,  snd_bg),
        ("alwaysAutoLoot", "bytes", None,
         lootgate.patches_for(buf, always_loot)),
        # cameraSkipFix is baked into the torrent's WoW.exe, so we don't
        # apply it. skillUiGateHijack and octowowUrlAllowlist below are
        # baked in too, but the official launcher still applies these 2
        # specific patches, so we mirror it in case the Octo devs drop them
        # from WoW.exe again.
        ("octowowUrlAllowlist", "bytes", None, [
            (0x45ccd8, bytes([
                0x6f,0x63,0x74,0x6f,0x77,0x6f,0x77,0x2e,0x73,0x74,
                0x00,0x00,0x00,0x00,0x00,0x00,
            ])),
        ]),
        ("skillUiGateHijack", "bytes", None, [
            (0x002ddf90, bytes([
                0x55,0x8b,0xec,0x83,0xec,0x08,0x53,0x56,0x57,0x8b,0x3d,0x60,0xab,0xce,0x00,0x83,
                0xff,0xff,0x89,0x55,0xfc,0x89,0x4d,0xf8,0x74,0x79,0x8b,0x75,0x08,0x8b,0x15,0x58,
                0xab,0xce,0x00,0x8b,0xc7,0x23,0xc6,0x8d,0x04,0x40,0x8b,0x4c,0x82,0x08,0xf6,0xc1,
                0x01,0x8d,0x44,0x82,0x04,0x75,0x04,0x85,0xc9,0x75,0x05,0x33,0xc9,0x8d,0x49,0x00,
                0xf6,0xc1,0x01,0x75,0x4e,0x85,0xc9,0x74,0x4a,0x39,0x31,0x74,0x13,0x8b,0xc7,0x23,
                0xc6,0x8d,0x04,0x40,0x8d,0x04,0x82,0x8b,0x00,0x03,0xc1,0x8b,0x48,0x04,0xeb,0xe0,
                0x8b,0x59,0x1c,0x8b,0x71,0x18,0x33,0xff,0x85,0xdb,0x7e,0x27,0x8d,0x64,0x24,0x00,
                0x8b,0x4e,0x0c,0x8b,0x56,0x08,0x6a,0x00,0x6a,0x00,0x51,0x8b,0x4d,0xf8,0x52,0x8b,
                0x55,0xfc,0xe8,0xb9,0xfd,0xff,0xff,0x84,0xc0,0x75,0x13,0x47,0x83,0xc6,0x20,0x3b,
                0xfb,0x7c,0xdd,0x5f,0x5e,0x33,0xc0,0x5b,0x8b,0xe5,0x5d,0xc2,0x04,0x00,0x5f,0x8b,
                0xc6,0x5e,0x5b,0x8b,0xe5,0x5d,0xc2,0x04,0x00,0x90,0x90,0x90,0x90,0x90,0x90,0x90,
            ])),
        ]),
    ]



#: A real WoW.exe is megabytes. A base shorter than this is not one: patching
#: it would write a stub over the player's game (an empty cached base did
#: exactly that to a test machine's WoW.exe).
MIN_BASE_BYTES = 1_000_000


def base_problem(base: bytes) -> str | None:
    """Why ``base`` cannot be the clean WoW.exe to patch from, or None."""
    if len(base) < MIN_BASE_BYTES:
        return f"the clean WoW.exe is only {len(base)} bytes"
    if base[:2] != b"MZ":
        return "the clean WoW.exe is not a Windows program"
    return None


@oplock.guarded(0, "patching WoW.exe")
def patch_exe(out_dir: str, tweaks: dict | None = None, log_fn=None):
    """Write WoW.exe = pristine base + the tweak patches.

    Same patches as 2.x. Changed: the new file is built in memory, checked,
    written beside the old one and swapped in with ``os.replace``, so a bad
    base, a failed write or a full disk can no longer leave a truncated game
    executable (2.x opened WoW.exe with "wb" first)."""
    say = log_fn or (lambda m, tag="": log(m, tag))
    exe = os.path.join(out_dir, "WoW.exe")
    if not os.path.exists(exe):
        raise RuntimeError(f"WoW.exe not found in {out_dir}")

    # tweaks and a language change both reach here on their own, so this has
    # its own guard (a running game locks the file).
    if client_exe_locked(out_dir):
        raise RuntimeError(CLOSE_THE_GAME)

    say(chr(10) + "Applying binary tweaks to WoW.exe…")
    # Patch the pristine (unpatched) base rather than the on-disk exe, so a
    # re-patch (tweak or language change) never stacks on patched bytes.
    base = read_pristine_wow(out_dir)
    problem = base_problem(base)
    if problem:
        raise RuntimeError(f"Not patching: {problem}. WoW.exe was left as it is. "
                           f"Use Settings -> Verify game files.")
    buf = bytearray(base)
    for label, kind, offset, value in build_tweaks(buf, tweaks):
        say(f"  {label}", "dim")
        if kind == "float":
            struct.pack_into("<f", buf, offset, value)
        elif kind == "int8":
            struct.pack_into("<b", buf, offset, value)
        elif kind == "uint16":
            struct.pack_into("<H", buf, offset, value)
        elif kind == "bytes":
            for off, data in value:
                buf[off: off + len(data)] = data
    if len(buf) != len(base):
        raise RuntimeError("Not patching: a patch changed the file's size. "
                           "WoW.exe was left as it is.")
    tmp = exe + ".eqtmp"
    try:
        with open(tmp, "wb") as f:
            f.write(buf)
            f.flush()
            os.fsync(f.fileno())
        if os.path.getsize(tmp) != len(buf):
            raise RuntimeError("Not patching: the new WoW.exe was not written "
                               "in full. WoW.exe was left as it is.")
        os.replace(tmp, exe)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    say("WoW.exe patched.", "ok")
