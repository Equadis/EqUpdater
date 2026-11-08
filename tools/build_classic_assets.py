"""Rebuild the Classic (2004) theme's runtime artwork from the artwork pack.

    python tools/build_classic_assets.py PATH/TO/EqUpdater_Classic_2004_Art_V1

Writes ``assets/classic/*.png``. Everything is cropping, mirroring, feathering,
and classical (non-generative) inpainting of the *supplied* images:

* frame corners/edges, stone buttons, flames: the 2004 installer crops
  (``04_extracted_installer_details``, ``05_prototype_9slice``), unaltered
  apart from rectangular crops;
* scenery (sky, ruins, statues, braziers) and the logo plaque: cut from the
  supplied concept (``03_visual_target``), with the text/UI baked into it
  painted out by diffusion inpainting (OpenCV Telea) so it can be drawn live.

No image-generation model is involved. Needs Pillow, numpy and opencv-python.
This is a development tool; the application only ships the PNG output.
"""
from __future__ import annotations

import os
import shutil
import sys

import cv2
import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "assets", "classic")


def inpaint(img: Image.Image, boxes, *, grow=6, scale=4) -> Image.Image:
    """Paint out ``boxes`` (left, top, right, bottom) by diffusion from their
    surroundings, done at 1/scale size and blended back with film grain so a
    large hole does not look like a flat smear."""
    rgb = np.array(img.convert("RGB"))
    h, w = rgb.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    for l, t, r, b in boxes:
        mask[max(0, t - grow):b + grow, max(0, l - grow):r + grow] = 255
    small = cv2.resize(rgb, (w // scale, h // scale), interpolation=cv2.INTER_AREA)
    msmall = cv2.resize(mask, (w // scale, h // scale), interpolation=cv2.INTER_NEAREST)
    msmall = cv2.dilate(msmall, np.ones((3, 3), np.uint8))
    filled = cv2.inpaint(small, msmall, 5, cv2.INPAINT_TELEA)
    filled = cv2.GaussianBlur(filled, (0, 0), 2.0)
    big = cv2.resize(filled, (w, h), interpolation=cv2.INTER_CUBIC)
    rng = np.random.default_rng(7)
    grain = rng.normal(0, 3.0, big.shape[:2])[..., None]
    big = np.clip(big.astype(np.float32) + grain, 0, 255)
    soft = cv2.GaussianBlur(mask, (0, 0), 3).astype(np.float32)[..., None] / 255
    out = rgb * (1 - soft) + big * soft
    return Image.fromarray(out.astype(np.uint8))


def feather(img: Image.Image, *, left=0, right=0, top=0, bottom=0) -> Image.Image:
    """RGBA copy whose alpha ramps from 0 to 1 over the given pixel widths."""
    img = img.convert("RGBA")
    w, h = img.size
    a = np.ones((h, w), np.float32)
    xs, ys = np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32)
    if left:
        a *= np.clip(xs / left, 0, 1)[None, :]
    if right:
        a *= np.clip((w - 1 - xs) / right, 0, 1)[None, :]
    if top:
        a *= np.clip(ys / top, 0, 1)[:, None]
    if bottom:
        a *= np.clip((h - 1 - ys) / bottom, 0, 1)[:, None]
    a = a * a * (3 - 2 * a)                       # smoothstep
    img.putalpha(Image.fromarray((a * 255).astype(np.uint8)))
    return img


def main(pack: str) -> None:
    concept = Image.open(os.path.join(
        pack, "03_visual_target", "classic_2004_addons_concept.png")).convert("RGB")
    det = os.path.join(pack, "04_extracted_installer_details")
    nine = os.path.join(pack, "05_prototype_9slice")
    os.makedirs(OUT, exist_ok=True)

    # Stone texture first: panels and the plaque's stone use it.
    stone = Image.open(os.path.join(
        nine, "button_blank_365px_prototype.png")).convert("RGB")
    cell = stone.crop((51, 14, 314, 49))
    tex = Image.new("RGB", (263 * 4, 35 * 8))
    for y in range(0, tex.height, 35):
        for x in range(0, tex.width, 263):
            tex.paste(cell, (x, y))
    from PIL import ImageEnhance, ImageDraw
    tex = ImageEnhance.Color(tex).enhance(0.38)
    tex = Image.blend(Image.new("RGB", tex.size, "#0b1726"), tex, 0.17)
    tex.crop((0, 0, 263 * 2, 35 * 4)).save(
        os.path.join(OUT, "stone_tile.png"), optimize=True)

    # 1. Scenery between the statues. Work on the client area only (below
    # the concept's fake title bar), so nothing of it can bleed in.
    ox, oy = 14, 50
    area = concept.crop((ox, oy, 1658, 872))

    def rel(l, t, r, b):
        return (l - ox, t - oy, r - ox, b - oy)

    # Sky behind the logo plaque: the mirrored sky from its right.
    sky = area.crop(rel(580, 50, 1010, 135)).transpose(Image.FLIP_LEFT_RIGHT)
    sky_mask = feather(sky, right=30, bottom=20).getchannel("A")
    area.paste(sky.resize((430, 85)), rel(150, 50, 580, 135), sky_mask)
    # Under the tabs: the horizon's last rows drawn on downward, blurred
    # sideways and fading to the dark above the panel.
    band = area.crop(rel(150, 122, 1522, 132)).resize((1372, 1))
    band = band.filter(ImageFilter.GaussianBlur(14))
    fill = Image.new("RGB", (1372, 86))
    dark = (14, 9, 8)
    for y in range(86):
        t = min(1.0, y / 60.0) ** 0.8
        row = band.point(lambda v, t=t: v)  # copy
        px = [tuple(round(c * (1 - t) + d * t) for c, d in zip(band.getpixel((x, 0)), dark))
              for x in range(1372)]
        r = Image.new("RGB", (1372, 1))
        r.putdata(px)
        fill.paste(r, (0, y))
    area.paste(fill, rel(150, 132, 1522, 218))
    # Behind the panel (covered at run time, but its margins show): a smooth
    # haze from the header's dark down to the ground's horizon colour.
    top_row = area.crop(rel(150, 217, 1522, 218)).resize((1372, 1))
    bot_row = area.crop(rel(150, 736, 1522, 737)).resize((1372, 1)).filter(
        ImageFilter.GaussianBlur(30))
    ph = 736 - 218
    haze = Image.new("RGB", (1372, ph))
    for y in range(ph):
        t = (y / ph) ** 2.2
        a = top_row
        r = Image.blend(a, bot_row, t)
        haze.paste(r, (0, y))
    area.paste(haze, rel(150, 218, 1522, 736))
    # The rest of the UI: painted out by diffusion. The footer shows the
    # ground, so it needs to be plausible.
    ui_boxes = [
        rel(150, 742, 385, 782),      # status text
        rel(150, 782, 515, 868),      # PLAY and the update arrow
        rel(1375, 778, 1525, 872),    # Discord / Ko-fi / version
    ]
    clean = inpaint(area, ui_boxes)
    clean.crop((150 - ox, 0, 1522 - ox, clean.height)).save(
        os.path.join(OUT, "scene.png"), optimize=True)

    # 2. The statues and braziers that flank the panel, feathered into it.
    left = clean.crop((0, 80, 152 - ox, 750))
    feather(left, right=40, top=60, bottom=60).save(
        os.path.join(OUT, "side_left.png"), optimize=True)
    right = clean.crop((1522 - ox, 80, 1658 - ox, 750))
    feather(right, left=40, top=60, bottom=60).save(
        os.path.join(OUT, "side_right.png"), optimize=True)

    # 3. The logo plaque with its wordmark and icon replaced by stone grain
    # (the wordmark is drawn live, in the user's font).
    plaque = concept.crop((112, 42, 572, 212)).convert("RGB")
    blue = Image.new("RGB", plaque.size, (22, 40, 66))
    stone_patch = Image.blend(blue, tex.crop((0, 0, 460, 170)).resize(plaque.size), 0.55)
    grad = Image.linear_gradient("L").resize(plaque.size).transpose(Image.FLIP_TOP_BOTTOM)
    stone_patch = Image.composite(
        Image.blend(stone_patch, Image.new("RGB", plaque.size, (46, 74, 112)), 0.45),
        stone_patch, grad.point(lambda v: v // 3))
    hole = Image.new("L", plaque.size, 0)
    ImageDraw.Draw(hole).rounded_rectangle((46, 50, 408, 130), radius=14, fill=255)
    plaque.paste(stone_patch, (0, 0), hole.filter(ImageFilter.GaussianBlur(5)))
    rgba = plaque.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (3, 10, rgba.width - 4, rgba.height - 20), radius=46, fill=255)
    rgba.putalpha(mask.filter(ImageFilter.GaussianBlur(4)))
    rgba.save(os.path.join(OUT, "plaque.png"), optimize=True)

    # 4. The 2004 installer's own frame, buttons and fire, unaltered.
    # Frame edges: only the rows/columns that are frame (the crops carry a
    # pixel or two of the screenshot's window chrome beside them).
    def sub(name, box):
        Image.open(os.path.join(det, name + "_reference.png")).convert("RGB") \
            .crop(box).save(os.path.join(OUT, name.replace("_reference", "") + ".png"))
    sub("frame_top_edge", (0, 1, 75, 14))
    sub("frame_bottom_edge", (0, 9, 72, 22))
    sub("frame_left_edge", (1, 0, 15, 53))
    sub("frame_right_edge", (16, 0, 30, 51))
    # Corners: opaque on the frame bars, fading into the scene beyond them.
    BAR = 14
    for name, box, flip in (
            ("corner_top_left", (1, 1, 97, 79), (False, False)),
            ("corner_top_right", (0, 1, 100, 80), (True, False)),
            ("corner_bottom_left", (1, 0, 97, 71), (False, True)),
            ("corner_bottom_right", (0, 0, 100, 71), (True, True))):
        im = Image.open(os.path.join(det, name + "_reference.png")).convert("RGB").crop(box)
        # Work as a top-left corner, then put it back.
        if flip[0]:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        if flip[1]:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
        w, h = im.size
        a = feather(im, right=w * 0.55, bottom=h * 0.55).getchannel("A")
        bars = Image.new("L", (w, h), 0)
        ImageDraw.Draw(bars).rectangle((0, 0, w, BAR), fill=255)
        ImageDraw.Draw(bars).rectangle((0, 0, BAR, h), fill=255)
        from PIL import ImageChops
        a = ImageChops.lighter(a, bars)
        im = im.convert("RGBA")
        im.putalpha(a)
        if flip[0]:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        if flip[1]:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
        im.save(os.path.join(OUT, name + ".png"), optimize=True)
    shutil.copyfile(os.path.join(det, "blizzard_badge_reference.png"),
                    os.path.join(OUT, "blizzard_badge.png"))
    for name in ("button_left_cap", "button_center_1px_repeat",
                 "button_right_cap", "small_button_left_cap",
                 "small_button_center_1px_repeat", "small_button_right_cap"):
        shutil.copyfile(os.path.join(nine, name + ".png"),
                        os.path.join(OUT, name + ".png"))

    print("wrote", OUT)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
