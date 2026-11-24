"""Generate the three official skins added in Phase 5 (reproducible).

    python tools/skins/build_official_skins.py

* **Minimal**        graphite grey; colours and icons only.
* **Minimal Light**  light theme; colours and icons only (icons drawn dark).
* **Classic (2004)** the ornate frame, steel buttons, textured backdrop and the
  animated login scene, from the artwork in ``tools/skins/source/classic/``.

Bubbles and Minimal Dark come from ``build_poc_assets.py``.

Everything a skin contains is written here, theme.json included, so the skins
can be rebuilt (or their art swapped) without hand-editing JSON.
"""

import json
import os
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_poc_assets as poc  # noqa: E402

ROOT, SK = poc.ROOT, poc.SK
SRC = os.path.join(HERE, "source", "classic")
hexrgb = poc.hexrgb

FONT_KEYS = ("body", "dim", "heading", "title", "button", "nav")


def fonts(family, scale=1.0):
    base = {"body": 13, "dim": 11, "heading": 15, "title": 24, "button": 13, "nav": 13}
    bold = {"heading", "title", "button", "nav"}
    out = {}
    for k in FONT_KEYS:
        f = {"family": family, "size": round(base[k] * scale)}
        if k in bold:
            f["bold"] = True
        out[k] = f
    return out


ICON_COMPONENTS = {"icon.logo": "logo", "icon.refresh": "refresh", "icon.settings": "settings",
                   "icon.arrow-down": "arrow-down", "icon.discord": "discord",
                   "icon.support": "support", "icon.download": "download",
                   "icon.remove": "remove", "icon.release": "release", "icon.link": "link",
                   "icon.check": "check", "icon.update": "swap"}


def icon_components(prefix="icons/"):
    return {cid: {"icon": f"{prefix}{name}.png"} for cid, name in ICON_COMPONENTS.items()}


def write_skin(folder, manifest, theme):
    os.makedirs(os.path.join(SK, folder), exist_ok=True)
    manifest = {"version": "1.0.0", "skin_api": 1, "author": "EqUpdater", **manifest}
    with open(os.path.join(SK, folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(SK, folder, "theme.json"), "w", encoding="utf-8") as f:
        json.dump(theme, f, indent=2, ensure_ascii=False)
        f.write("\n")


def logo(folder):
    im = Image.open(os.path.join(ROOT, "icon.png")).convert("RGBA")
    im.resize((128, 128), Image.Resampling.LANCZOS).save(poc.out(folder, "icons", "logo.png"))


# ── Minimal and Minimal Light ───────────────────────────────────────────────────
def build_minimal():
    poc.write_icons("minimal", hexrgb("#cfd3d9"))
    logo("minimal")
    palette = {"bg": "#202226", "panel": "#2a2d32", "dialog": "#2f3238", "panelBorder": "#41454d",
               "text": "#e9eaec", "textDim": "#9a9fa8", "accent": "#5fb8a6", "accentLight": "#8fd3c5",
               "link": "#7cc4d6", "danger": "#ef7b7b", "ok": "#7ccf94", "new": "#ef7b7b",
               "scrim": "#99000000", "buttonFace": "#33363c", "buttonHover": "#3e4249",
               "buttonPressed": "#2a2d32", "buttonDisabled": "#2b2e33",
               "primary": "#2d7667", "primaryHover": "#378a79", "primaryPressed": "#265f53"}
    write_skin("minimal", {
        "id": "minimal", "name": "Minimal", "license": "Artwork: EqUpdater project",
        "description": "Neutral graphite theme. Colours and icons only, no animated background."},
        {"palette": palette, "fonts": fonts("Segoe UI"), "background": {"color": "$bg"},
         "components": icon_components()})


def build_minimal_light():
    poc.write_icons("minimal-light", hexrgb("#444b57"))
    logo("minimal-light")
    palette = {"bg": "#eef0f3", "panel": "#ffffff", "dialog": "#ffffff", "panelBorder": "#cfd3da",
               "text": "#1f2329", "textDim": "#5d6572", "accent": "#2563c9", "accentLight": "#4a85e6",
               "link": "#1f5bbf", "danger": "#c93c3c", "ok": "#238a4a", "new": "#c93c3c",
               "scrim": "#66000000", "buttonFace": "#e3e6eb", "buttonHover": "#d6dae1",
               "buttonPressed": "#c9ced6", "buttonDisabled": "#eceef1",
               "primary": "#2563c9", "primaryHover": "#3874de", "primaryPressed": "#1d52a8"}
    write_skin("minimal-light", {
        "id": "minimal-light", "name": "Minimal Light", "license": "Artwork: EqUpdater project",
        "description": "Neutral light theme. Colours and icons only, no animated background."},
        {"palette": palette, "fonts": fonts("Segoe UI"), "background": {"color": "$bg"},
         "components": {**icon_components(), "button.play": {"textColors": {
             "normal": "#ffffff", "hover": "#ffffff", "disabled": "#8a919c"}}}})


# ── Classic (2004) ────────────────────────────────────────────────────────────────
def _fill_region(im, seed, color_fn, thresh):
    """Replace the connected region around ``seed`` (within ``thresh`` of the
    seed colour) by ``color_fn(x, y)``; returns the new image."""
    w, h = im.size
    rgb = im.convert("RGB")
    marker = (255, 0, 255)
    ImageDraw.floodfill(rgb, seed, marker, thresh=thresh)
    out = im.copy()
    px, rp = out.load(), rgb.load()
    for y in range(h):
        for x in range(w):
            if rp[x, y] == marker:
                px[x, y] = color_fn(x, y)
    return out


def classic_panel():
    """The ornate frame, its grey middle made a dark translucent fill, halved so
    one source pixel is one logical pixel (a nine-slice is drawn 1:1)."""
    im = Image.open(os.path.join(SRC, "border.png")).convert("RGBA")
    w, h = im.size
    im = _fill_region(im, (w // 2, h // 2), lambda x, y: (9, 13, 24, 226), thresh=40)
    return im.resize((w // 2, h // 2), Image.Resampling.LANCZOS)


def classic_button(top, bottom, tint=None, dim=1.0):
    """The steel frame with its white inside filled by a vertical gradient."""
    im = Image.open(os.path.join(SRC, "button_border.png")).convert("RGBA")
    w, h = im.size
    t, b = hexrgb(top), hexrgb(bottom)

    def fill(x, y):
        f = y / (h - 1)
        return tuple(int(t[i] + (b[i] - t[i]) * f) for i in range(3)) + (255,)
    im = _fill_region(im, (w // 2, h // 2), fill, thresh=70)
    if tint:
        r, g, bl = hexrgb(tint)[:3]
        px = im.load()
        for y in range(h):
            for x in range(w):
                pr, pg, pb, pa = px[x, y]
                px[x, y] = (min(255, int(pr * 0.82 + r * 0.18)), min(255, int(pg * 0.82 + g * 0.18)),
                            min(255, int(pb * 0.82 + bl * 0.18)), pa)
    if dim != 1.0:
        px = im.load()
        for y in range(h):
            for x in range(w):
                pr, pg, pb, pa = px[x, y]
                gray = (pr + pg + pb) // 3
                px[x, y] = (int((pr + gray) / 2 * dim), int((pg + gray) / 2 * dim),
                            int((pb + gray) / 2 * dim), pa)
    return im.resize((w // 4, h // 4), Image.Resampling.LANCZOS)


def classic_backgrounds():
    d = poc.out("classic-2004", "backgrounds", "x")
    d = os.path.dirname(d)
    blue = Image.open(os.path.join(SRC, "blue.png")).convert("RGB")
    blue.resize((1600, 900), Image.Resampling.LANCZOS).save(
        os.path.join(d, "stone-blue.webp"), quality=86, method=4)
    gif = Image.open(os.path.join(SRC, "wow2004.gif"))
    frames, durations = [], []
    for i in range(gif.n_frames):
        gif.seek(i)
        frames.append(gif.convert("RGB").copy())
        durations.append(gif.info.get("duration", 40) or 40)
    frames[0].save(os.path.join(d, "login-2004.webp"), save_all=True,
                   append_images=frames[1:], duration=durations, loop=0, quality=78, method=4)


STATES = {"normal": ("#3a4452", "#1c222c", None, 1.0),
          "hover": ("#4c5a6e", "#273140", "#ffd100", 1.0),
          "pressed": ("#161b23", "#232b37", None, 0.9),
          "disabled": ("#2a2f37", "#1b1f25", None, 0.45)}
PLAY_STATES = {"normal": ("#3e6b2a", "#1f3a14", "#ffd100", 1.0),
               "hover": ("#59932f", "#2c5218", "#ffd100", 1.0),
               "pressed": ("#1d3612", "#2c5218", None, 0.95),
               "disabled": ("#2a2f37", "#1b1f25", None, 0.45)}


def build_classic():
    folder = "classic-2004"
    poc.write_icons(folder, hexrgb("#ffd100"))
    logo(folder)
    for n in ("discord", "coffee"):
        im = Image.open(os.path.join(ROOT, n + ".png")).convert("RGBA")
        im.save(poc.out(folder, "icons", n + ".png"))
    classic_backgrounds()
    classic_panel().save(poc.out(folder, "panels", "frame.png"))
    for state, (t, b, tint, dim) in STATES.items():
        classic_button(t, b, tint, dim).save(poc.out(folder, "buttons", f"normal-{state}.png"))
    for state, (t, b, tint, dim) in PLAY_STATES.items():
        classic_button(t, b, tint, dim).save(poc.out(folder, "buttons", f"play-{state}.png"))
    palette = {"bg": "#070b14", "panel": "#e60a1018", "dialog": "#f20a1018", "panelBorder": "#4a3a1c",
               "text": "#f0e6c8", "textDim": "#a79d7e", "accent": "#ffd100", "accentLight": "#ffe680",
               "link": "#4fa3ff", "danger": "#ff5a4f", "ok": "#5fd35f", "new": "#ff3b3b",
               "scrim": "#b3000510", "buttonFace": "#2c3440", "buttonHover": "#3c4858",
               "buttonPressed": "#1a2028", "buttonDisabled": "#22262d",
               "primary": "#3e6b2a", "primaryHover": "#59932f", "primaryPressed": "#1d3612"}

    def tex(prefix):
        return {s: f"buttons/{prefix}-{s}.png" for s in ("normal", "hover", "pressed", "disabled")} | {
            "selected": f"buttons/{prefix}-hover.png"}
    theme = {
        "palette": palette,
        "fonts": fonts("Friz Quadrata"),
        "background": {"color": "$bg", "static": "backgrounds/stone-blue.webp",
                       "animation": "backgrounds/login-2004.webp", "darken": 0.55,
                       "fadeTop": 0, "fadeBottom": 0},
        "components": {
            "panel": {"textures": {"normal": "panels/frame.png"}, "slice": [56, 56, 56, 56],
                      "padding": [26, 22, 26, 20]},
            "dialog": {"textures": {"normal": "panels/frame.png"}, "slice": [56, 56, 56, 56],
                       "padding": [26, 22, 26, 20]},
            "button": {"textures": tex("normal"), "slice": [10, 10, 10, 10],
                       "textColors": {"normal": "#ffd100", "hover": "#ffffff",
                                      "pressed": "#e6c25a", "disabled": "#7d7a6e",
                                      "selected": "#ffffff"},
                       "padding": [14, 6, 14, 6]},
            "button.play": {"textures": tex("play"), "slice": [10, 10, 10, 10],
                            "textColors": {"normal": "#ffd100", "hover": "#ffffff",
                                           "pressed": "#e6c25a", "disabled": "#7d7a6e"},
                            "minHeight": 40, "padding": [34, 8, 34, 8]},
            "nav.tab": {"textColors": {"normal": "#ffd100", "hover": "#ffffff", "selected": "#ffffff"}},
            "text.heading": {"textColors": {"normal": "#ffd100"}},
            "panel.header": {"textColors": {"normal": "#ffd100"}},
            **icon_components()}}
    write_skin(folder, {
        "id": "classic-2004", "name": "Classic (2004)",
        "license": "Artwork supplied by the project owner; confirm redistribution rights before release.",
        "description": "The look of the original 2004 client: ornate frame, steel buttons, gold type and the animated login scene."},
        theme)


if __name__ == "__main__":
    build_minimal()
    build_minimal_light()
    build_classic()
    print("skins written to", SK)
