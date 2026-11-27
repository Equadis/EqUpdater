"""Draw the six language flags (reproducible; PIL only): equpdater/qtui/qml/flags/<id>.png, 48x32.

Windows draws flag emoji as two letters, not as flags, so the Language selector uses these bundled
images. Simplified on purpose (no coat of arms on Spain, no lettering on Brazil): they are 24x16 on
screen. Run: python tools/assets/build_flags.py
"""

import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "equpdater", "qtui", "qml", "flags")
W, H, K = 48, 32, 8                                   # final size, supersampling


def canvas():
    im = Image.new("RGBA", (W * K, H * K))
    return im, ImageDraw.Draw(im)


def finish(im):
    return im.resize((W, H), Image.Resampling.LANCZOS)


def star(d, cx, cy, r, fill, rot=-90):
    pts = []
    for i in range(10):
        a = math.radians(rot + i * 36)
        rr = r if i % 2 == 0 else r * 0.382
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def gb():
    im, d = canvas()
    w, h = W * K, H * K
    d.rectangle((0, 0, w, h), fill="#012169")
    for a, b in (((0, 0), (w, h)), ((0, h), (w, 0))):
        d.line((*a, *b), fill="#ffffff", width=int(h * 0.20))
    for a, b in (((0, 0), (w, h)), ((0, h), (w, 0))):
        d.line((*a, *b), fill="#c8102e", width=int(h * 0.07))
    d.rectangle((0, h * 0.36, w, h * 0.64), fill="#ffffff")
    d.rectangle((w * 0.42, 0, w * 0.58, h), fill="#ffffff")
    d.rectangle((0, h * 0.42, w, h * 0.58), fill="#c8102e")
    d.rectangle((w * 0.46, 0, w * 0.54, h), fill="#c8102e")
    return finish(im)


def stripes(colours, weights=None):
    im, d = canvas()
    weights = weights or [1] * len(colours)
    total, y = sum(weights), 0.0
    for c, wt in zip(colours, weights):
        d.rectangle((0, y, W * K, y + H * K * wt / total), fill=c)
        y += H * K * wt / total
    return finish(im)


def cn():
    im, d = canvas()
    d.rectangle((0, 0, W * K, H * K), fill="#de2910")
    star(d, 9 * K, 9 * K, 5.5 * K, "#ffde00")
    for cx, cy in ((18, 4), (21, 8), (21, 13), (18, 17)):
        star(d, cx * K, cy * K, 2.4 * K, "#ffde00", rot=-90 + math.degrees(math.atan2(9 - cy, 9 - cx)) + 90)
    return finish(im)


def br():
    im, d = canvas()
    w, h = W * K, H * K
    d.rectangle((0, 0, w, h), fill="#009c3b")
    d.polygon([(w * 0.5, h * 0.09), (w * 0.93, h * 0.5), (w * 0.5, h * 0.91), (w * 0.07, h * 0.5)], fill="#ffdf00")
    d.ellipse((w * 0.5 - h * 0.27, h * 0.5 - h * 0.27, w * 0.5 + h * 0.27, h * 0.5 + h * 0.27), fill="#002776")
    d.arc((w * 0.5 - h * 0.27, h * 0.5 - h * 0.14, w * 0.5 + h * 0.27, h * 0.5 + h * 0.4), 200, 340, fill="#ffffff", width=int(h * 0.04))
    return finish(im)


def main():
    os.makedirs(OUT, exist_ok=True)
    flags = {"gb": gb(), "de": stripes(["#000000", "#dd0000", "#ffce00"]),
             "ru": stripes(["#ffffff", "#0039a6", "#d52b1e"]), "cn": cn(),
             "es": stripes(["#aa151b", "#f1bf00", "#aa151b"], [1, 2, 1]), "br": br()}
    for name, im in flags.items():
        # a 1 px dark rim so a white band never melts into a light panel
        rim = ImageDraw.Draw(im)
        rim.rectangle((0, 0, W - 1, H - 1), outline=(0, 0, 0, 70))
        im.save(os.path.join(OUT, name + ".png"))
        print(name)


if __name__ == "__main__":
    main()
