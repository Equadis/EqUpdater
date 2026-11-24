"""Generate the image assets of the proof-of-concept skins (reproducible).

    python tools/skins/build_poc_assets.py

* Bubbles: the animated ocean (bubbles.gif -> animated WebP, quality 85, frame
  timings kept: ~22.6 MB -> ~2.7 MB, PSNR ~40 dB, see docs/v3/PERFORMANCE.md),
  the still, the existing footer icons, a downsized logo, and 9-slice textures
  for the Play button.
* Minimal Dark: only icons. Everything else is colours in theme.json - which
  is the point of that skin.

Icons drawn here (refresh, settings, arrow, support, chat) are simple
primitives made for EqUpdater; the Discord and Ko-fi marks are the files
2.x already ships (discord.png, coffee.png).
"""

import math
import os
import shutil
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SK = os.path.join(ROOT, "skins")


def out(*parts):
    p = os.path.join(SK, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def hexrgb(h, a=255):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


# ── icons (drawn at 4x, reduced for smooth edges) ────────────────────────────
S, K = 64, 4


def canvas():
    im = Image.new("RGBA", (S * K, S * K), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def finish(im):
    return im.resize((S, S), Image.Resampling.LANCZOS)


def icon_refresh(color):
    im, d = canvas()
    c, r, w = S * K // 2, 22 * K, 6 * K
    d.arc((c - r, c - r, c + r, c + r), 40, 320, fill=color, width=w)
    a = math.radians(320)
    tip = (c + r * math.cos(a), c + r * math.sin(a))
    d.polygon([(tip[0] + 2 * K, tip[1] - 14 * K), (tip[0] + 2 * K, tip[1] + 6 * K),
               (tip[0] - 16 * K, tip[1] - 4 * K)], fill=color)
    return finish(im)


def icon_settings(color):
    im, d = canvas()
    c = S * K // 2
    for i in range(8):
        a = math.radians(i * 45)
        x, y = c + 24 * K * math.cos(a), c + 24 * K * math.sin(a)
        d.line((c, c, x, y), fill=color, width=9 * K)
    d.ellipse((c - 20 * K, c - 20 * K, c + 20 * K, c + 20 * K), fill=color)
    d.ellipse((c - 9 * K, c - 9 * K, c + 9 * K, c + 9 * K), fill=(0, 0, 0, 0))
    return finish(im)


def icon_arrow_down(color):
    im, d = canvas()
    c = S * K // 2
    d.polygon([(c - 18 * K, c - 8 * K), (c + 18 * K, c - 8 * K), (c, c + 12 * K)],
              fill=color)
    return finish(im)


def icon_support(color):
    """A cup with a heart-free simple outline: 'support' without a brand mark."""
    im, d = canvas()
    d.rounded_rectangle((12 * K, 20 * K, 44 * K, 52 * K), radius=6 * K, fill=color)
    d.arc((36 * K, 26 * K, 56 * K, 46 * K), 270, 90, fill=color, width=5 * K)
    d.rectangle((8 * K, 52 * K, 48 * K, 56 * K), fill=color)
    return finish(im)


def icon_chat(color):
    im, d = canvas()
    d.rounded_rectangle((6 * K, 10 * K, 58 * K, 44 * K), radius=10 * K, fill=color)
    d.polygon([(18 * K, 42 * K), (18 * K, 58 * K), (32 * K, 44 * K)], fill=color)
    return finish(im)


def icon_download(color):
    im, d = canvas()
    c = S * K // 2
    d.rectangle((c - 5 * K, 8 * K, c + 5 * K, 34 * K), fill=color)
    d.polygon([(c - 16 * K, 32 * K), (c + 16 * K, 32 * K), (c, 50 * K)], fill=color)
    d.rectangle((10 * K, 54 * K, 54 * K, 59 * K), fill=color)
    return finish(im)


def icon_remove(color):
    im, d = canvas()
    d.rectangle((14 * K, 14 * K, 50 * K, 19 * K), fill=color)
    d.rectangle((26 * K, 8 * K, 38 * K, 14 * K), fill=color)
    d.polygon([(18 * K, 22 * K), (46 * K, 22 * K), (43 * K, 58 * K), (21 * K, 58 * K)], fill=color)
    return finish(im)


def icon_release(color):
    im, d = canvas()
    d.arc((6 * K, 18 * K, 34 * K, 46 * K), 40, 320, fill=color, width=6 * K)
    d.arc((30 * K, 18 * K, 58 * K, 46 * K), 220, 140, fill=color, width=6 * K)
    return finish(im)


def icon_link(color):
    im, d = canvas()
    d.rectangle((10 * K, 22 * K, 42 * K, 54 * K), outline=color, width=5 * K)
    d.polygon([(34 * K, 8 * K), (56 * K, 8 * K), (56 * K, 30 * K)], fill=color)
    d.line((26 * K, 38 * K, 54 * K, 10 * K), fill=color, width=5 * K)
    return finish(im)


def icon_check(color):
    im, d = canvas()
    d.line((12 * K, 34 * K, 26 * K, 48 * K), fill=color, width=8 * K)
    d.line((26 * K, 48 * K, 52 * K, 16 * K), fill=color, width=8 * K)
    return finish(im)


def icon_swap(color):
    im, d = canvas()
    d.line((10 * K, 22 * K, 50 * K, 22 * K), fill=color, width=6 * K)
    d.polygon([(44 * K, 10 * K), (58 * K, 22 * K), (44 * K, 34 * K)], fill=color)
    d.line((14 * K, 44 * K, 54 * K, 44 * K), fill=color, width=6 * K)
    d.polygon([(20 * K, 32 * K), (6 * K, 44 * K), (20 * K, 56 * K)], fill=color)
    return finish(im)


def write_icons(skin, color, extra=True):
    icons = {"refresh": icon_refresh, "settings": icon_settings,
             "arrow-down": icon_arrow_down, "download": icon_download,
             "remove": icon_remove, "release": icon_release, "link": icon_link,
             "check": icon_check, "swap": icon_swap}
    if extra:
        icons.update({"support": icon_support, "discord": icon_chat})
    for name, fn in icons.items():
        fn(color).save(out(skin, "icons", name + ".png"))


# ── nine-slice button textures (a 24 px square with a 1 px border) ───────────
def button_texture(top, bottom, border):
    n = 24
    im = Image.new("RGBA", (n, n))
    px = im.load()
    t, b, bd = hexrgb(top), hexrgb(bottom), hexrgb(border)
    for y in range(n):
        f = y / (n - 1)
        row = tuple(int(t[i] + (b[i] - t[i]) * f) for i in range(3)) + (255,)
        for x in range(n):
            px[x, y] = bd if x in (0, n - 1) or y in (0, n - 1) else row
    # soften the four corner pixels so the slice shows as a rounded corner
    for x, y in ((0, 0), (n - 1, 0), (0, n - 1), (n - 1, n - 1)):
        px[x, y] = (0, 0, 0, 0)
    return im


def build_bubbles():
    shutil.copyfile(os.path.join(ROOT, "pubbles.png"),
                    out("bubbles", "backgrounds", "ocean-still.png"))
    gif = Image.open(os.path.join(ROOT, "bubbles.gif"))
    frames, durations = [], []
    for i in range(gif.n_frames):
        gif.seek(i)
        frames.append(gif.convert("RGB").copy())
        durations.append(gif.info.get("duration", 30))
    frames[0].save(out("bubbles", "backgrounds", "ocean.webp"), save_all=True,
                   append_images=frames[1:], duration=durations, loop=0,
                   quality=85, method=4)
    logo = Image.open(os.path.join(ROOT, "icon.png")).convert("RGBA")
    logo.resize((128, 128), Image.Resampling.LANCZOS).save(out("bubbles", "icons", "logo.png"))
    for n in ("discord", "coffee"):
        shutil.copyfile(os.path.join(ROOT, n + ".png"), out("bubbles", "icons", n + ".png"))
    write_icons("bubbles", hexrgb("#c99a3d"), extra=False)
    for state, (t, b, bd) in {"normal": ("#4d8049", "#396637", "#6f9d69"),
                              "hover": ("#5b9155", "#427640", "#8aba84"),
                              "pressed": ("#396637", "#2f5a2d", "#6f9d69"),
                              "disabled": ("#24344a", "#1a293e", "#304966")}.items():
        button_texture(t, b, bd).save(out("bubbles", "buttons", f"play-{state}.png"))
    for state, (t, b, bd) in {"normal": ("#2b4a6e", "#1f3a5a", "#3a5f8a"),
                              "hover": ("#355a84", "#27476e", "#d7b56c"),
                              "pressed": ("#1f3a5a", "#193049", "#3a5f8a"),
                              "disabled": ("#16253a", "#101c2d", "#1f3550")}.items():
        button_texture(t, b, bd).save(out("bubbles", "buttons", f"normal-{state}.png"))


def build_minimal_dark():
    write_icons("minimal-dark", hexrgb("#c9ced6"), extra=True)
    shutil.copyfile(os.path.join(ROOT, "icon.png"), out("minimal-dark", "icons", "_tmp.png"))
    logo = Image.open(out("minimal-dark", "icons", "_tmp.png")).convert("RGBA")
    logo.resize((128, 128), Image.Resampling.LANCZOS).save(out("minimal-dark", "icons", "logo.png"))
    os.remove(out("minimal-dark", "icons", "_tmp.png"))


if __name__ == "__main__":
    build_bubbles()
    build_minimal_dark()
    print("skins written to", SK)
