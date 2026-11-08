"""Visual themes: what they are made of, and the artwork the Classic one needs.

A theme is presentation only. It names a palette (the ``C_*`` colours the
application draws with), whether the window is *skinned* with artwork, and
which font it prefers. Nothing here knows about addons, mods, updates or the
game; a theme can be switched without touching a rule that protects a user's
files.

Two themes:

``modern``
    The look EqUpdater has always had. Its palette is exactly the one that was
    hard-coded in ``app.py``; no artwork. The default for everybody.

``classic``
    The 2004 World of Warcraft installer: black-metal frame, Dark Portal
    scenery, stone buttons on a dark-blue panel with gold trim. Needs the
    PNGs in ``assets/classic`` (see :class:`ClassicArt`). If they are missing,
    or Pillow is, ``resolve()`` quietly hands back Modern.

The artwork is cropped, mirrored, feathered and sliced -- never redrawn: see
``tools/build_classic_assets.py`` for exactly how each file was derived from
the supplied artwork pack.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from . import branding

try:                                    # Pillow is a hard dependency of the
    from PIL import Image, ImageChops, ImageEnhance  # app, but a theme must
except Exception:                       # never be the thing that stops it.
    Image = ImageChops = ImageEnhance = None

MODERN = "modern"
CLASSIC = "classic"
DEFAULT = MODERN


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    #: Colour tokens. The names are the application's ``C_*`` constants,
    #: lower-cased, plus a few the old code spelled as literals.
    colors: dict = field(default_factory=dict)
    #: Drawn with artwork (frame, stone buttons, scenery) rather than flat.
    skinned: bool = False
    #: The font choice the theme looks right in, used until the user picks
    #: one themselves ("friz", "arial", "opendyslexic"); None keeps theirs.
    font: str | None = None
    #: Space (design pixels) left each side of the content panels.
    panel_inset: int = 40
    #: Whether the bubble animation belongs to this theme.
    animated_background: bool = True


MODERN_COLORS = {
    "bg": "#06132d", "panel": "#081a35", "panel_alt": "#0b2142",
    "panel_bdr": "#18385f", "hdr": "#06142c", "divider": "#19385f",
    "gold": "#c99a3d", "gold_lt": "#e4bd67", "purple": "#d7b56c",
    "text": "#eee9dc", "text_dim": "#91a3bd", "log_bg": "#041027",
    "ok": "#79bf73", "err": "#d17a7a", "mod_hl": "#a8b83c", "new": "#ff3b3b",
    # Literals the application used inline.
    "tip_bg": "#0f0b16", "entry_bg": "#070a17", "add_link": "#d76f9e",
    "add_link_hl": "#eb96ba", "warn": "#d4b43c",
}

# The palette from the artwork pack's asset map (metal black #101317, stone
# blue #152638, deep panel #0a121c, gold #b88d38 / #edc76b, parchment #eee0b5,
# amber #c87519, action red #bd5e4f, status green #7eb874), widened into the
# full set of tokens. Text is parchment; "dim" text is a darker parchment, not
# the modern theme's blue-grey, so it keeps its contrast on the darker stone.
CLASSIC_COLORS = {
    "bg": "#0a0c10", "panel": "#0c1724", "panel_alt": "#13243a",
    "panel_bdr": "#8c6a2c", "hdr": "#101317", "divider": "#2a3a4d",
    "gold": "#d4a43f", "gold_lt": "#f2cf7a", "purple": "#e8c97c",
    "text": "#eee0b5", "text_dim": "#bdb190", "log_bg": "#080e16",
    "ok": "#7eb874", "err": "#d9735f", "mod_hl": "#b9c94a", "new": "#ff4a3a",
    "tip_bg": "#101317", "entry_bg": "#060b12", "add_link": "#e0a5ae",
    "add_link_hl": "#f1c3ca", "warn": "#e0b63f",
}

THEMES = {
    MODERN: Theme(MODERN, "Modern", MODERN_COLORS),
    CLASSIC: Theme(CLASSIC, "Classic (2004)", CLASSIC_COLORS, skinned=True,
                   font="friz", panel_inset=64, animated_background=False),
}


def normalize(key) -> str:
    """A stored theme name as a known key; anything else is the default."""
    key = str(key or "").strip().lower()
    return key if key in THEMES else DEFAULT


# ── artwork ──────────────────────────────────────────────────────────────────

ASSET_SUBDIR = os.path.join("assets", "classic")

#: Without every one of these the Classic theme cannot be drawn.
REQUIRED = (
    "scene", "side_left", "side_right", "plaque", "stone_tile",
    "corner_top_left", "corner_top_right", "corner_bottom_left",
    "corner_bottom_right", "frame_top_edge", "frame_bottom_edge",
    "frame_left_edge", "frame_right_edge",
    "button_left_cap", "button_center_1px_repeat", "button_right_cap",
    "small_button_left_cap", "small_button_center_1px_repeat",
    "small_button_right_cap",
)


def asset_dir_candidates() -> list:
    """Where the Classic artwork may live: bundled in a frozen build, or in
    the source tree (and a ``EQUPDATER_CLASSIC_ASSETS`` override)."""
    out = []
    override = os.environ.get("EQUPDATER_CLASSIC_ASSETS")
    if override:
        out.append(override)
    base = getattr(sys, "_MEIPASS", None)
    if base:
        out.append(os.path.join(base, ASSET_SUBDIR))
    out.append(os.path.join(branding.app_dir(), ASSET_SUBDIR))
    return out


def find_asset_dir() -> str | None:
    for path in asset_dir_candidates():
        if all(os.path.isfile(os.path.join(path, n + ".png")) for n in REQUIRED):
            return path
    return None


def _tint(img, rgb, amount):
    """``img`` (RGBA) moved ``amount`` of the way towards colour ``rgb``,
    alpha untouched."""
    r, g, b, a = img.split()
    solid = Image.new("RGB", img.size, rgb)
    mixed = Image.blend(Image.merge("RGB", (r, g, b)), solid, amount)
    out = mixed.convert("RGBA")
    out.putalpha(a)
    return out


def _adjust(img, *, brightness=1.0, color=1.0, contrast=1.0):
    r, g, b, a = img.split()
    rgb = Image.merge("RGB", (r, g, b))
    if color != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(color)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
    if brightness != 1.0:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


#: Button states -> how the one stone sprite is recoloured. Non-destructive:
#: the artwork is never edited, only drawn lighter, darker or warmer.
STATES = ("up", "hover", "down", "off", "sel")


def apply_state(img, state: str):
    if state == "hover":
        return _tint(_adjust(img, brightness=1.28, contrast=1.05),
                     (237, 199, 107), 0.10)
    if state == "down":
        return _adjust(img, brightness=0.68)
    if state == "off":
        return _adjust(img, brightness=0.62, color=0.2)
    if state == "sel":          # the open tab: warm, lit from within
        return _tint(_adjust(img, brightness=1.35, contrast=1.08),
                     (200, 117, 25), 0.30)
    return img


class ClassicArt:
    """The Classic theme's artwork: loaded once, sliced and composed on
    demand, and cached. Pure Pillow; Tk image objects are made by the app
    (they need a root window) from what this returns."""

    #: Native heights/cap widths of the two stone-button sprites (px).
    LARGE = ("button", 61, 31, 30)
    SMALL = ("small_button", 42, 18, 20)

    def __init__(self, directory: str):
        self.dir = directory
        self._raw: dict = {}
        self._cache: dict = {}

    # loading ---------------------------------------------------------------

    def raw(self, name: str):
        if name not in self._raw:
            with Image.open(os.path.join(self.dir, name + ".png")) as im:
                self._raw[name] = im.convert("RGBA")
        return self._raw[name]

    def _memo(self, key, make):
        if key not in self._cache:
            if len(self._cache) > 400:          # sizes change with the window
                self._cache.clear()
            self._cache[key] = make()
        return self._cache[key]

    # three-slice buttons ---------------------------------------------------

    def button(self, big: bool, width: int, height: int, state: str = "up"):
        """A stone button of exactly ``width`` x ``height``: the caps keep
        the sprite's proportions, only the middle stretches."""
        return self._memo(("btn", big, width, height, state),
                          lambda: self._button(big, width, height, state))

    def _button(self, big, width, height, state):
        prefix, native_h, lw, rw = self.LARGE if big else self.SMALL
        left, mid, right = (self.raw(f"{prefix}_left_cap"),
                            self.raw(f"{prefix}_center_1px_repeat"),
                            self.raw(f"{prefix}_right_cap"))
        k = height / native_h
        cl = max(2, round(lw * k))
        cr = max(2, round(rw * k))
        width = max(width, cl + cr + 2)
        out = Image.new("RGBA", (width, height))
        lcap = left.resize((cl, height), Image.Resampling.LANCZOS)
        rcap = right.resize((cr, height), Image.Resampling.LANCZOS)
        out.paste(lcap, (0, 0))
        # The middle runs from the left cap's last column to the right
        # cap's first, so the lighting passes from one to the other
        # without a seam wherever the sprite is stretched.
        edge = Image.new("RGBA", (2, height))
        edge.paste(lcap.crop((cl - 1, 0, cl, height)), (0, 0))
        edge.paste(rcap.crop((0, 0, 1, height)), (1, 0))
        out.paste(edge.resize((width - cl - cr, height),
                              Image.Resampling.BILINEAR), (cl, 0))
        out.paste(rcap, (width - cr, 0))
        return apply_state(out, state)

    # stone ------------------------------------------------------------------

    def stone(self, width: int, height: int):
        """The panel stone, tiled to size."""
        def make():
            tile = self.raw("stone_tile").convert("RGB")
            out = Image.new("RGB", (width, height))
            for y in range(0, height, tile.height):
                for x in range(0, width, tile.width):
                    out.paste(tile, (x, y))
            return out
        return self._memo(("stone", width, height), make)

    # the window -------------------------------------------------------------

    def frame(self, width: int, height: int, scale: float = 1.0):
        """The 2004 installer's black-metal frame as a transparent overlay."""
        return self._memo(("frame", width, height, round(scale, 3)),
                          lambda: self._frame(width, height, scale))

    def _scaled(self, name, scale):
        im = self.raw(name)
        if abs(scale - 1.0) < 0.01:
            return im
        return im.resize((max(1, round(im.width * scale)),
                          max(1, round(im.height * scale))),
                         Image.Resampling.LANCZOS)

    def _frame(self, w, h, sc):
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        top, bot = self._scaled("frame_top_edge", sc), self._scaled("frame_bottom_edge", sc)
        lef, rig = self._scaled("frame_left_edge", sc), self._scaled("frame_right_edge", sc)
        for x in range(0, w, top.width):
            out.paste(top, (x, 0))
        for x in range(0, w, bot.width):
            out.paste(bot, (x, h - bot.height))
        for y in range(0, h, lef.height):
            out.paste(lef, (0, y))
        for y in range(0, h, rig.height):
            out.paste(rig, (w - rig.width, y))
        for name, at in (("corner_top_left", "tl"), ("corner_top_right", "tr"),
                         ("corner_bottom_left", "bl"),
                         ("corner_bottom_right", "br")):
            c = self._scaled(name, sc)
            x = 0 if at[1] == "l" else w - c.width
            y = 0 if at[0] == "t" else h - c.height
            out.alpha_composite(c, (x, y))
        return out

    def scene(self, width: int, height: int, scale: float, inset: int,
              panel_bottom: int):
        """The window behind the panels: the scenery, the statues either
        side, then the frame. ``inset`` is the panel margin in pixels;
        ``panel_bottom`` is where the content panels end."""
        return self._memo(
            ("scene", width, height, round(scale, 3), inset, panel_bottom),
            lambda: self._scene(width, height, scale, inset, panel_bottom))

    def _scene(self, w, h, sc, inset, panel_bottom):
        base = self.raw("scene").convert("RGB")
        k = max(w / base.width, h / base.height)
        big = base.resize((max(w, round(base.width * k)),
                           max(h, round(base.height * k))),
                          Image.Resampling.LANCZOS)
        left = (big.width - w) // 2
        out = big.crop((left, big.height - h, left + w, big.height)).convert("RGBA")
        # Statues: as wide as the margin (a little wider, so the panel's
        # gold edge overlaps them), standing on the panel's bottom edge.
        for name, right_side in (("side_left", False), ("side_right", True)):
            side = self.raw(name)
            sk = (inset + 10 * sc) / side.width
            piece = side.resize((round(side.width * sk), round(side.height * sk)),
                                Image.Resampling.LANCZOS)
            y = min(h - piece.height, panel_bottom + round(34 * sc) - piece.height)
            y = max(0, y)
            x = w - piece.width if right_side else 0
            out.alpha_composite(piece, (x, y))
        out.alpha_composite(self.frame(w, h, sc))
        return out.convert("RGB")

    def plaque(self, scale: float):
        """The wordmark's stone plaque at ``scale`` times its native size."""
        return self._memo(("plaque", round(scale, 3)), lambda: (
            self.raw("plaque").resize(
                (round(self.raw("plaque").width * scale),
                 round(self.raw("plaque").height * scale)),
                Image.Resampling.LANCZOS)))


_ART: dict = {}


def classic_art() -> ClassicArt | None:
    """The Classic artwork, or None if it (or Pillow) is not there."""
    if Image is None:
        return None
    if "art" not in _ART:
        directory = find_asset_dir()
        _ART["art"] = ClassicArt(directory) if directory else None
    return _ART["art"]


def resolve(key) -> Theme:
    """The theme to use for a stored setting: the requested one, or Modern
    when the artwork it needs is not available."""
    theme = THEMES[normalize(key)]
    if theme.skinned and classic_art() is None:
        return THEMES[DEFAULT]
    return theme
