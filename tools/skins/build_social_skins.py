"""Generate the Turtle WoW and Battle.net skins (reproducible, original artwork).

    python tools/skins/build_social_skins.py

Both are *full* skins, not palette variants: their own frame, button, input
and menu looks, type, spacing, layout and a background made for them. The
pictures are drawn here from scratch (gradients, silhouettes, glows); no part
of either launcher's artwork is used, only their general look:

* **Turtle WoW**  dark translucent panels with an olive and gold frame, gold
  type, olive buttons, a green PLAY button, compact spacing, a twilight
  fantasy landscape.
* **Battle.net**  navy and charcoal, electric-blue accent, clean modern type,
  navigation beside the logo, a large blue PLAY button, soft background
  lighting, and the social sidebar open by default.
"""

import json
import math
import os
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_poc_assets as poc            # noqa: E402
import build_official_skins as off        # noqa: E402

ROOT, SK, hexrgb = poc.ROOT, poc.SK, poc.hexrgb


def rgb(h, a=255):
    return hexrgb(h, a)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def vgradient(size, top, bottom):
    w, h = size
    im = Image.new("RGBA", size)
    px = im.load()
    for y in range(h):
        c = lerp(top, bottom, y / max(1, h - 1))
        for x in range(w):
            px[x, y] = c
    return im


def rounded(size, radius, fill_top, fill_bottom, border=None, border_w=1, ss=4):
    """A rounded rectangle with a vertical gradient and optional border, drawn
    at 4x and reduced so the corners are smooth, with real alpha."""
    w, h = size
    big = (w * ss, h * ss)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big[0] - 1, big[1] - 1), radius * ss, fill=255)
    body = vgradient(big, fill_top, fill_bottom)
    if border:
        inner = Image.new("L", big, 0)
        bw = border_w * ss
        ImageDraw.Draw(inner).rounded_rectangle((bw, bw, big[0] - 1 - bw, big[1] - 1 - bw),
                                                max(0, radius - border_w) * ss, fill=255)
        frame = Image.new("RGBA", big, border)
        body = Image.composite(body, frame, inner)
    body.putalpha(mask)
    return body.resize(size, Image.Resampling.LANCZOS)


# ── Turtle WoW ────────────────────────────────────────────────────────────────────
OLIVE, OLIVE_L, OLIVE_D = "#6b7a33", "#8a9c45", "#3b4520"
GOLD, GOLD_D = "#e8c14a", "#8a6f24"


def turtle_panel():
    """Dark translucent panel: olive outer line, gold hairline, corner studs."""
    s = 64
    ss = 4
    big = Image.new("RGBA", (s * ss, s * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, s * ss - 1, s * ss - 1), 7 * ss, fill=rgb("#0f0d08", 222))
    d.rounded_rectangle((0, 0, s * ss - 1, s * ss - 1), 7 * ss, outline=rgb(OLIVE_D), width=3 * ss)
    d.rounded_rectangle((2 * ss, 2 * ss, s * ss - 1 - 2 * ss, s * ss - 1 - 2 * ss), 5 * ss,
                        outline=rgb(OLIVE, 235), width=ss)
    d.rounded_rectangle((4 * ss, 4 * ss, s * ss - 1 - 4 * ss, s * ss - 1 - 4 * ss), 3 * ss,
                        outline=rgb(GOLD_D, 170), width=ss // 2)
    for cx, cy in ((6, 6), (s - 7, 6), (6, s - 7), (s - 7, s - 7)):
        d.ellipse(((cx - 2) * ss, (cy - 2) * ss, (cx + 2) * ss, (cy + 2) * ss), fill=rgb(GOLD, 255),
                  outline=rgb(GOLD_D), width=ss // 2)
    return big.resize((s, s), Image.Resampling.LANCZOS)


def turtle_button(top, bottom, border, glow=None):
    im = rounded((48, 34), 5, rgb(top), rgb(bottom), rgb(border), 2)
    hi = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(hi).line((4, 3, 43, 3), fill=rgb("#ffffff", 46), width=1)     # a bevel highlight
    im = Image.alpha_composite(im, hi)
    if glow:
        g = rounded((48, 34), 5, rgb(glow, 0), rgb(glow, 70), rgb(glow, 160), 1)
        im = Image.alpha_composite(im, g)
    return im


def landscape(size=(1600, 900), seed=7):
    """A twilight fantasy scene: sky, moon, far ridges, a tower, forest."""
    rnd = random.Random(seed)
    w, h = size
    img = vgradient(size, rgb("#17305c"), rgb("#9a6a5c"))                       # twilight to dusk
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((w * 0.62, h * 0.12, w * 0.62 + 170, h * 0.12 + 170), fill=rgb("#fff1c4", 255))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    moon = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(moon).ellipse((w * 0.62 + 40, h * 0.12 + 40, w * 0.62 + 130, h * 0.12 + 130),
                                 fill=rgb("#fff7de", 255))
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, moon.filter(ImageFilter.GaussianBlur(1.2)))
    stars = ImageDraw.Draw(img)
    for _ in range(220):
        x, y = rnd.randrange(w), rnd.randrange(int(h * 0.5))
        a = rnd.randrange(80, 230)
        stars.point((x, y), fill=(255, 255, 240, a))

    def ridge(base, amp, color, rough):
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        pts, y = [(0, h)], base
        for x in range(0, w + 20, 20):
            y += rnd.uniform(-rough, rough)
            y = min(max(y, base - amp), base + amp)
            pts.append((x, y))
        pts.append((w, h))
        ImageDraw.Draw(layer).polygon(pts, fill=color)
        return layer
    img = Image.alpha_composite(img, ridge(h * 0.62, 90, rgb("#3a4a6a", 255), 22).filter(ImageFilter.GaussianBlur(1.5)))
    img = Image.alpha_composite(img, ridge(h * 0.70, 70, rgb("#27324c", 255), 18))
    tower = Image.new("RGBA", size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tower)
    tx, ty = w * 0.2, h * 0.52
    td.rectangle((tx, ty, tx + 54, h * 0.72), fill=rgb("#141a28"))
    td.polygon([(tx - 10, ty), (tx + 27, ty - 70), (tx + 64, ty)], fill=rgb("#141a28"))
    td.rectangle((tx + 20, ty + 40, tx + 32, ty + 70), fill=rgb("#e8b04a", 230))             # a lit window
    img = Image.alpha_composite(img, tower.filter(ImageFilter.GaussianBlur(0.6)))
    img = Image.alpha_composite(img, ridge(h * 0.80, 40, rgb("#10151f", 255), 14))
    forest = Image.new("RGBA", size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(forest)
    for x in range(-20, w + 20, 18):
        th = rnd.randrange(60, 150)
        base = h * 0.86 + rnd.randrange(-14, 14)
        fd.polygon([(x, base), (x + 18, base), (x + 9, base - th)], fill=rgb("#0a0e15"))
    fd.rectangle((0, h * 0.86, w, h), fill=rgb("#0a0e15"))
    img = Image.alpha_composite(img, forest)
    mist = vgradient(size, rgb("#8aa0b0", 0), rgb("#8aa0b0", 60))
    img = Image.alpha_composite(img, mist.crop((0, 0, w, h)))
    return img.convert("RGB")


def build_turtle():
    folder = "turtle-wow"
    poc.write_icons(folder, rgb(GOLD))
    off.logo(folder)
    for n in ("discord", "coffee"):
        Image.open(os.path.join(ROOT, n + ".png")).convert("RGBA").save(poc.out(folder, "icons", n + ".png"))
    bg = poc.out(folder, "backgrounds", "x")
    bgdir = os.path.dirname(bg)
    landscape().save(os.path.join(bgdir, "twilight.webp"), quality=84, method=4)
    turtle_panel().save(poc.out(folder, "panels", "frame.png"))
    states = {"normal": ("#76863a", "#4a5826", "#9fb257"), "hover": ("#8c9f48", "#586a2d", "#d9c25a"),
              "pressed": ("#3d4a1f", "#53632a", "#6b7a33"), "disabled": ("#3a3d33", "#2a2c25", "#4b4e42")}
    play = {"normal": ("#58b030", "#2f6a17", "#9be06b"), "hover": ("#6cc83e", "#38801c", "#e8e36a"),
            "pressed": ("#2f6a17", "#3f8a22", "#58b030"), "disabled": ("#3a3d33", "#2a2c25", "#4b4e42")}
    for st, (t, b, bd) in states.items():
        turtle_button(t, b, bd).save(poc.out(folder, "buttons", f"normal-{st}.png"))
    for st, (t, b, bd) in play.items():
        turtle_button(t, b, bd).save(poc.out(folder, "buttons", f"play-{st}.png"))
    palette = {"bg": "#0b0a07", "panel": "#e00f0d08", "dialog": "#f2100e09", "panelBorder": "#4b5526",
               "text": "#e9e0c4", "textDim": "#a89f80", "accent": GOLD, "accentLight": "#f6dc8a",
               "link": "#f0cf6a", "danger": "#e0795a", "ok": "#9bd05a", "new": "#ff7a4a", "scrim": "#b3050400",
               "buttonFace": "#4a5826", "buttonHover": "#5d6e30", "buttonPressed": "#353f1b",
               "buttonDisabled": "#2d2f26", "primary": "#4f9a2e", "primaryHover": "#62b53a",
               "primaryPressed": "#2f6a17"}

    def tex(prefix):
        return {s: f"buttons/{prefix}-{s}.png" for s in ("normal", "hover", "pressed", "disabled")} | {
            "selected": f"buttons/{prefix}-hover.png"}
    gold_text = {"normal": GOLD, "hover": "#fff3c4", "pressed": "#d4ae3c", "disabled": "#85806a", "selected": "#fff3c4"}
    fonts = off.fonts("Friz Quadrata")
    fonts["body"]["size"], fonts["dim"]["size"], fonts["heading"]["size"] = 12, 10, 14      # compact
    fonts["button"]["size"], fonts["nav"]["size"], fonts["title"]["size"] = 12, 13, 22
    theme = {
        "palette": palette, "fonts": fonts,
        "metrics": {"radius": 4, "spacing": 6, "panelPadding": 10, "scrollbarWidth": 7, "animationMs": 100},
        "background": {"color": "$bg", "static": "backgrounds/twilight.webp", "darken": 0.9,
                       "fadeTop": 0, "fadeBottom": 90},
        "components": {
            "panel": {"textures": {"normal": "panels/frame.png"}, "slice": [16, 16, 16, 16],
                      "padding": [18, 14, 16, 12]},
            "dialog": {"textures": {"normal": "panels/frame.png"}, "slice": [16, 16, 16, 16],
                       "padding": [20, 16, 18, 14]},
            "sidebar": {"textures": {"normal": "panels/frame.png"}, "slice": [16, 16, 16, 16],
                        "padding": [14, 12, 12, 10]},
            "button": {"textures": tex("normal"), "slice": [10, 10, 10, 10], "textColors": gold_text,
                       "minHeight": 26, "padding": [12, 3, 12, 3]},
            "button.small": {"minHeight": 22, "padding": [9, 2, 9, 2]},
            "button.play": {"textures": tex("play"), "slice": [10, 10, 10, 10],
                            "textColors": {**gold_text, "normal": "#fff6cf"}, "minHeight": 40,
                            "padding": [34, 6, 34, 6]},
            "nav.tab": {"textColors": {"normal": "#cdbf8c", "hover": "#fff3c4", "selected": GOLD}},
            "input": {"colors": {"normal": "#e6141208", "hover": "#e61a1809", "selected": "#e61a1809",
                                 "disabled": "#e0201f17"},
                      "borderColors": {"normal": "#5a6730", "hover": "#8a9c45", "selected": GOLD},
                      "radius": 3, "minHeight": 24, "padding": [8, 3, 8, 3]},
            "checkbox": {"colors": {"normal": "#e6141208", "selected": GOLD}, "borderColors": {"normal": "#6b7a33"}},
            "menu": {"colors": {"normal": "#f5100e09"}, "borderColors": {"normal": "#6b7a33"}, "borderWidth": 1},
            "menu.item": {"colors": {"normal": "#00000000", "hover": "#664a5826"}, "minHeight": 24},
            "tooltip": {"colors": {"normal": "#f5100e09"}, "borderColors": {"normal": "#8a6f24"},
                        "textColors": {"normal": "#e9e0c4"}},
            "progress": {"colors": {"normal": "#e6141208", "selected": "#7fae3a"},
                         "borderColors": {"normal": "#5a6730"}, "radius": 3},
            "scrollbar": {"colors": {"normal": "#00000000", "hover": "#8a9c45", "pressed": GOLD,
                                     "selected": "#4b5526"}, "radius": 3},
            "divider": {"colors": {"normal": "#4b5526"}},
            "badge": {"colors": {"normal": "#e8c14a"}, "textColors": {"normal": "#1a1608"}},
            "text.heading": {"textColors": {"normal": GOLD}},
            "panel.header": {"textColors": {"normal": GOLD}},
            **off.icon_components()}}
    layout = {"headerHeight": 80, "logoSize": 40, "navSpacing": 22, "contentMargin": 28, "panelGap": 10,
              "footerHeight": 84, "navRight": 44, "navOffset": 10, "sidebarWidth": 244}
    theme["layout"] = layout
    off.write_skin(folder, {
        "id": "turtle-wow", "name": "Turtle WoW", "license": "Artwork: EqUpdater project (drawn for this skin)",
        "description": "Dark translucent panels, an olive and gold frame, gold type, olive buttons and a green PLAY button over a twilight fantasy scene. Compact."},
        theme)


# ── Battle.net ───────────────────────────────────────────────────────────────────────
BLUE, BLUE_L = "#148eff", "#4fb0ff"


def glow_background(size=(1600, 900)):
    """Navy with soft lighting: a blue bloom upper left, a cooler one lower right,
    a faint diagonal sweep, and a little grain so the gradient does not band."""
    w, h = size
    img = vgradient(size, rgb("#0d1424"), rgb("#070b14"))

    def bloom(cx, cy, r, color, alpha):
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse((cx - r, cy - r, cx + r, cy + r), fill=rgb(color, alpha))
        return layer.filter(ImageFilter.GaussianBlur(r * 0.55))
    img = Image.alpha_composite(img, bloom(w * 0.18, h * 0.05, 420, BLUE, 120))
    img = Image.alpha_composite(img, bloom(w * 0.85, h * 0.95, 460, "#2a5bd7", 80))
    img = Image.alpha_composite(img, bloom(w * 0.55, h * 0.30, 300, "#4f6bff", 26))
    sweep = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(sweep).polygon([(w * 0.1, 0), (w * 0.28, 0), (w * 0.7, h), (w * 0.52, h)],
                                  fill=rgb("#ffffff", 9))
    img = Image.alpha_composite(img, sweep.filter(ImageFilter.GaussianBlur(40)))
    rnd = random.Random(3)
    grain = Image.effect_noise(size, 12).convert("L").point(lambda v: int(abs(v - 128) / 8))
    img = Image.alpha_composite(img, Image.merge("RGBA", (grain, grain, grain, grain.point(lambda v: v * 3))))
    del rnd
    return img.convert("RGB")


def bnet_button(top, bottom, border=None, radius=5, size=(40, 32)):
    return rounded(size, radius, rgb(top), rgb(bottom), rgb(border) if border else None, 1)


def build_battlenet():
    folder = "battle-net"
    poc.write_icons(folder, rgb("#c9d6ea"))
    off.logo(folder)
    for n in ("discord", "coffee"):
        Image.open(os.path.join(ROOT, n + ".png")).convert("RGBA").save(poc.out(folder, "icons", n + ".png"))
    bgdir = os.path.dirname(poc.out(folder, "backgrounds", "x"))
    glow_background().save(os.path.join(bgdir, "navy-glow.webp"), quality=86, method=4)
    play = {"normal": ("#2a9bff", "#0f6fe0", "#6bb8ff"), "hover": ("#4fb0ff", "#1f84f2", "#a2d3ff"),
            "pressed": ("#0f6fe0", "#0b57b8", "#2a9bff"), "disabled": ("#26324a", "#1c2538", "#2b3750")}
    flat = {"normal": ("#26324a", "#202a3d", "#31405c"), "hover": ("#304062", "#283653", "#148eff"),
            "pressed": ("#1a2233", "#161d2c", "#31405c"), "disabled": ("#1b2130", "#171c29", "#222b3c")}
    for st, (t, b, bd) in play.items():
        bnet_button(t, b, bd, 6, (48, 40)).save(poc.out(folder, "buttons", f"play-{st}.png"))
    for st, (t, b, bd) in flat.items():
        bnet_button(t, b, bd, 4).save(poc.out(folder, "buttons", f"normal-{st}.png"))
    rounded((48, 48), 8, rgb("#161d2b", 238), rgb("#111827", 242), rgb("#26324a"), 1).save(
        poc.out(folder, "panels", "card.png"))
    rounded((48, 48), 8, rgb("#1b2436"), rgb("#161d2c"), rgb("#2c3a56"), 1).save(
        poc.out(folder, "panels", "dialog.png"))
    rounded((48, 48), 0, rgb("#0f1522", 244), rgb("#0c111c", 248), None, 0).save(
        poc.out(folder, "panels", "sidebar.png"))
    palette = {"bg": "#070b14", "panel": "#ee141b29", "dialog": "#ff1a2233", "panelBorder": "#232d3f",
               "text": "#e9eef7", "textDim": "#8a97ad", "accent": BLUE, "accentLight": BLUE_L, "link": BLUE_L,
               "danger": "#ff5a5f", "ok": "#3ddc84", "new": "#ff5a5f", "scrim": "#b0020610",
               "buttonFace": "#202a3b", "buttonHover": "#2b3850", "buttonPressed": "#18202e",
               "buttonDisabled": "#171c29", "primary": BLUE, "primaryHover": "#3aa0ff", "primaryPressed": "#0f74d4"}

    def tex(prefix):
        return {s: f"buttons/{prefix}-{s}.png" for s in ("normal", "hover", "pressed", "disabled")} | {
            "selected": f"buttons/{prefix}-hover.png"}
    fonts = off.fonts("Segoe UI")
    fonts["nav"] = {"family": "Segoe UI", "size": 14, "bold": True}
    fonts["title"] = {"family": "Segoe UI", "size": 20, "bold": True}
    theme = {
        "palette": palette, "fonts": fonts,
        "metrics": {"radius": 6, "spacing": 10, "panelPadding": 16, "scrollbarWidth": 8, "animationMs": 140},
        "background": {"color": "$bg", "static": "backgrounds/navy-glow.webp", "darken": 1.0, "fadeTop": 0, "fadeBottom": 0},
        "components": {
            "panel": {"textures": {"normal": "panels/card.png"}, "slice": [14, 14, 14, 14], "padding": [18, 14, 16, 12]},
            "dialog": {"textures": {"normal": "panels/dialog.png"}, "slice": [14, 14, 14, 14], "padding": [22, 18, 20, 16]},
            "sidebar": {"textures": {"normal": "panels/sidebar.png"}, "slice": [4, 4, 4, 4], "padding": [14, 14, 12, 12]},
            "button": {"textures": tex("normal"), "slice": [8, 8, 8, 8],
                       "textColors": {"normal": "#e9eef7", "hover": "#ffffff", "pressed": "#c9d6ea",
                                      "disabled": "#5f6b82", "selected": "#ffffff"},
                       "minHeight": 32, "padding": [16, 6, 16, 6]},
            "button.small": {"minHeight": 26, "padding": [12, 4, 12, 4]},
            "button.play": {"textures": tex("play"), "slice": [12, 12, 12, 12],
                            "textColors": {"normal": "#ffffff", "hover": "#ffffff", "pressed": "#dbe9ff",
                                           "disabled": "#6b7790"},
                            "minHeight": 54, "padding": [44, 10, 44, 10]},
            "nav.tab": {"textColors": {"normal": "#9aa8c0", "hover": "#ffffff", "selected": "#ffffff"}},
            "input": {"colors": {"normal": "#e60d1320", "hover": "#e6101829", "selected": "#e6101829",
                                 "disabled": "#e0151a26"},
                      "borderColors": {"normal": "#2c3a56", "hover": "#41567f", "selected": BLUE},
                      "radius": 4, "minHeight": 30, "padding": [10, 5, 10, 5]},
            "checkbox": {"colors": {"normal": "#0d1320", "selected": BLUE}, "borderColors": {"normal": "#41567f"}},
            "menu": {"colors": {"normal": "#fa161d2c"}, "borderColors": {"normal": "#2c3a56"}, "borderWidth": 1, "radius": 6},
            "menu.item": {"colors": {"normal": "#00000000", "hover": "#55148eff"}, "minHeight": 30},
            "tooltip": {"colors": {"normal": "#fa0f1522"}, "borderColors": {"normal": "#2c3a56"},
                        "textColors": {"normal": "#e9eef7"}},
            "progress": {"colors": {"normal": "#18202e", "selected": BLUE}, "borderColors": {"normal": "#232d3f"}, "radius": 3},
            "scrollbar": {"colors": {"normal": "#00000000", "hover": "#41567f", "pressed": BLUE, "selected": "#2c3a56"},
                          "radius": 4},
            "divider": {"colors": {"normal": "#222c3e"}},
            "badge": {"colors": {"normal": BLUE}, "textColors": {"normal": "#ffffff"}},
            "text.heading": {"textColors": {"normal": "#ffffff"}},
            "panel.header": {"textColors": {"normal": "#ffffff"}},
            **off.icon_components()}}
    theme["layout"] = {"headerHeight": 72, "logoSize": 34, "navSpacing": 6, "contentMargin": 28, "panelGap": 14,
                       "footerHeight": 108, "navAlign": "left", "navLeftMargin": 40, "navRight": 90,
                       "sidebarWidth": 272, "sidebarDefault": "expanded"}
    off.write_skin(folder, {
        "id": "battle-net", "name": "Battle.net", "license": "Artwork: EqUpdater project (drawn for this skin)",
        "description": "Navy and charcoal with an electric-blue accent: modern type, navigation beside the logo, a large blue PLAY button, soft lighting and the social sidebar open."},
        theme)


if __name__ == "__main__":
    build_turtle()
    build_battlenet()
    print("skins written to", SK)
