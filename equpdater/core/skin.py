"""Skin engine: declarative, data-only, safe to load from untrusted folders.

A skin is a folder::

    <skins>/<id>/manifest.json   identity and ``skin_api`` version
                 theme.json      palette, fonts, metrics, components, background
                 layout.json     (optional) positions of the fixed layout slots
                 <images, fonts, one background animation>

and nothing else is ever read: no QML, Python, JavaScript or CSS comes from a
skin. The application's QML components read their appearance *only* from the
resolved theme (``Skin.theme``), by stable component ids (``COMPONENTS``).

Safety (a skin may come from anywhere):
* every path is relative and must stay inside the skin folder (no ``..``, no
  absolute paths, no symlinks out); files are checked by extension, size and,
  for images, by header, before the theme refers to them;
* JSON files are size-capped; unknown keys are ignored;
* anything missing, oversized, unreadable or of the wrong type falls back,
  *per asset*, to the next layer: skin -> the built-in flat theme. Problems
  are collected in ``Skin.problems`` (shown by Settings), never raised.

Lookup order for one component field: the skin's own value, then its
``extends`` parent (``button.play`` extends ``button`` by name), then the
built-in theme. So a minimal skin that only sets a palette is complete.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

#: The skin format this build reads. A skin naming a higher ``skin_api`` is
#: refused with a clear message; lower ones are read as far as they go.
SKIN_API = 1

MAX_JSON = 256 * 1024
MAX_IMAGE = 12 * 1024 * 1024
MAX_FONT = 6 * 1024 * 1024
MAX_ANIMATION = 40 * 1024 * 1024
IMAGE_EXT = {".png", ".webp", ".jpg", ".jpeg"}
ANIM_EXT = {".webp", ".gif", ".mp4", ".webm", ".png", ".jpg", ".jpeg"}
FONT_EXT = {".ttf", ".otf"}

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_MAGIC = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg",
          b"RIFF": ".webp", b"GIF8": ".gif"}

#: Button-like states a component may give a texture / colour for.
STATES = ("normal", "hover", "pressed", "disabled", "selected")

#: The stable component ids. Documented in docs/v3/SKINNING.md. A skin may
#: define any subset; every id always resolves (to the built-in theme at worst).
COMPONENTS = (
    "window", "panel", "dialog", "panel.header", "divider", "scrim",
    "button", "button.primary", "button.play", "button.small", "button.link",
    "splitbutton.arrow",
    "nav.tab", "checkbox", "input", "menu", "menu.item", "progress", "scrollbar",
    "badge", "tooltip", "statusdot",
    "text.body", "text.dim", "text.heading", "text.title", "text.link",
    "text.error", "text.new", "text.ok",
    "icon.refresh", "icon.settings", "icon.arrow-down", "icon.discord",
    "icon.support", "icon.logo", "icon.external", "icon.update",
    "icon.download", "icon.remove", "icon.release", "icon.link", "icon.check",
)

#: Implicit parents (``extends`` in a theme overrides these).
PARENT = {"panel.header": "panel", "dialog": "panel", "button.primary": "button",
          "button.play": "button.primary", "button.small": "button",
          "button.link": "button", "menu.item": "button",
          "splitbutton.arrow": "button.play"}

BUILTIN_THEME = {
    "palette": {
        "bg": "#14161a", "panel": "#1d2025", "dialog": "#1d2025", "panelBorder": "#33373e",
        "text": "#e6e8eb", "textDim": "#9aa1ab", "accent": "#d9b44a",
        "accentLight": "#f0d27a", "link": "#7fb0ff", "danger": "#ff6b6b",
        "ok": "#6fcf97", "new": "#ff5d5d", "scrim": "#99000000",
        "buttonFace": "#2a2e35", "buttonHover": "#343942",
        "buttonPressed": "#23262c", "buttonDisabled": "#202328",
        "primary": "#2f6f3a", "primaryHover": "#3a8647",
        "primaryPressed": "#275c30",
    },
    "fonts": {
        "body": {"family": "Segoe UI", "size": 13},
        "dim": {"family": "Segoe UI", "size": 11},
        "heading": {"family": "Segoe UI", "size": 15, "bold": True},
        "title": {"family": "Segoe UI", "size": 22, "bold": True},
        "button": {"family": "Segoe UI", "size": 13, "bold": True},
        "nav": {"family": "Segoe UI", "size": 14, "bold": True},
    },
    "metrics": {"radius": 3, "spacing": 8, "panelPadding": 14,
                "borderWidth": 1, "scrollbarWidth": 8, "animationMs": 120},
    "components": {
        "window": {"colors": {"normal": "$bg"}},
        "panel": {"colors": {"normal": "$panel"}, "borderColors":
                  {"normal": "$panelBorder"}, "borderWidth": 1, "radius": 3,
                  "padding": [14, 12, 14, 12]},
        "dialog": {"colors": {"normal": "$dialog"}},
        "panel.header": {"textColors": {"normal": "$accent"}, "font": "heading"},
        "divider": {"colors": {"normal": "$panelBorder"}},
        "scrim": {"colors": {"normal": "$scrim"}},
        "button": {"colors": {"normal": "$buttonFace", "hover": "$buttonHover",
                              "pressed": "$buttonPressed",
                              "disabled": "$buttonDisabled",
                              "selected": "$buttonHover"},
                   "borderColors": {"normal": "$panelBorder",
                                    "hover": "$accent"},
                   "textColors": {"normal": "$text", "hover": "$accentLight",
                                  "disabled": "$textDim"},
                   "borderWidth": 1, "radius": 3, "padding": [14, 6, 14, 6],
                   "minHeight": 30, "font": "button"},
        "button.primary": {"colors": {"normal": "$primary",
                                      "hover": "$primaryHover",
                                      "pressed": "$primaryPressed",
                                      "disabled": "$buttonDisabled"},
                           "textColors": {"normal": "#ffffff",
                                          "hover": "#ffffff",
                                          "disabled": "$textDim"}},
        "button.play": {"minHeight": 44, "padding": [28, 8, 28, 8]},
        "button.small": {"minHeight": 24, "padding": [10, 3, 10, 3]},
        "button.link": {"textures": None,
                        "colors": {"normal": "#00000000", "hover": "#00000000",
                                   "pressed": "#00000000"},
                        "borderWidth": 0, "padding": [0, 4, 0, 4], "minHeight": 0,
                        "textColors": {"normal": "$accent",
                                       "hover": "$accentLight"}},
        "splitbutton.arrow": {"padding": [8, 8, 8, 8]},
        "nav.tab": {"colors": {"normal": "#00000000", "hover": "#00000000",
                               "selected": "#00000000"},
                    "borderWidth": 0,
                    "textColors": {"normal": "$text", "hover": "$accentLight",
                                   "selected": "$accent"},
                    "font": "nav", "padding": [4, 4, 4, 4]},
        "checkbox": {"colors": {"normal": "$buttonFace", "selected": "$accent"},
                     "borderColors": {"normal": "$panelBorder"},
                     "textColors": {"normal": "$text", "hover": "$accentLight"}},
        "input": {"colors": {"normal": "$buttonPressed", "hover": "$buttonPressed",
                             "selected": "$buttonPressed", "disabled": "$buttonDisabled"},
                  "borderColors": {"normal": "$panelBorder", "hover": "$accent",
                                   "selected": "$accent"},
                  "textColors": {"normal": "$text", "disabled": "$textDim"},
                  "borderWidth": 1, "radius": 3, "padding": [8, 4, 8, 4],
                  "minHeight": 28, "font": "body"},
        "menu": {"colors": {"normal": "$dialog"},
                 "borderColors": {"normal": "$panelBorder"}, "borderWidth": 1,
                 "padding": [4, 4, 4, 4]},
        "menu.item": {"textures": None,
                      "colors": {"normal": "#00000000", "hover": "$buttonHover"},
                      "borderWidth": 0, "minHeight": 28,
                      "padding": [10, 4, 10, 4]},
        "progress": {"colors": {"normal": "$buttonFace", "selected": "$accent"},
                     "borderColors": {"normal": "$panelBorder"}, "radius": 3},
        "scrollbar": {"colors": {"normal": "#00000000", "hover": "$accent",
                                 "pressed": "$accentLight",
                                 "selected": "$panelBorder"}, "radius": 4},
        "badge": {"colors": {"normal": "$accent"},
                  "textColors": {"normal": "#1a1a1a"}},
        "tooltip": {"colors": {"normal": "$panel"},
                    "borderColors": {"normal": "$panelBorder"},
                    "textColors": {"normal": "$text"}},
        "statusdot": {"colors": {"normal": "$textDim", "selected": "$ok",
                                 "disabled": "$danger", "hover": "$accent"}},
        "text.body": {"textColors": {"normal": "$text"}, "font": "body"},
        "text.dim": {"textColors": {"normal": "$textDim"}, "font": "dim"},
        "text.heading": {"textColors": {"normal": "$accent"}, "font": "heading"},
        "text.title": {"textColors": {"normal": "$text"}, "font": "title"},
        "text.link": {"textColors": {"normal": "$accent",
                                     "hover": "$accentLight"}},
        "text.error": {"textColors": {"normal": "$danger"}},
        "text.new": {"textColors": {"normal": "$new"}},
        "text.ok": {"textColors": {"normal": "$ok"}},
    },
    "background": {"darken": 1.0, "fillMode": "cover"},
    "layout": {"headerHeight": 96, "logoSize": 46, "navSpacing": 28,
               "contentMargin": 48, "panelGap": 14, "footerHeight": 96,
               "navRight": 48},
}


@dataclass
class Skin:
    id: str
    name: str
    root: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    skin_api: int = SKIN_API
    theme: dict = field(default_factory=dict)       # resolved, QML-ready
    fonts: list = field(default_factory=list)       # font files to register
    problems: list = field(default_factory=list)    # shown, never raised
    official: bool = False
    preview: str = ""                                # file URL or ""


# ── small helpers ────────────────────────────────────────────────────────────

def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_json(path: Path, problems: list, what: str):
    try:
        if path.stat().st_size > MAX_JSON:
            problems.append(f"{what}: larger than {MAX_JSON // 1024} KB, ignored")
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            problems.append(f"{what}: must be a JSON object")
            return None
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        problems.append(f"{what}: {exc}")
        return None


def safe_file(root: Path, rel, exts: set, limit: int, problems: list,
              what: str = "") -> Path | None:
    """The file ``rel`` names inside ``root``, or None (with a problem noted).
    The only way a skin file path becomes a real path."""
    if not isinstance(rel, str) or not rel:
        return None
    label = what or rel
    if rel.startswith(("/", "\\")) or ":" in rel or ".." in Path(rel).parts:
        problems.append(f"{label}: path '{rel}' leaves the skin folder")
        return None
    path = root / rel
    try:
        real = path.resolve(strict=True)
        if root.resolve() not in real.parents:
            problems.append(f"{label}: '{rel}' resolves outside the skin folder")
            return None
        if path.is_symlink() or not real.is_file():
            problems.append(f"{label}: '{rel}' is not a plain file")
            return None
        if real.suffix.lower() not in exts:
            problems.append(f"{label}: '{rel}' has an unsupported type "
                            f"(allowed: {', '.join(sorted(exts))})")
            return None
        if real.stat().st_size > limit:
            problems.append(f"{label}: '{rel}' is larger than "
                            f"{limit // 1048576} MB")
            return None
        if real.suffix.lower() in IMAGE_EXT | {".gif"}:
            with open(real, "rb") as f:
                head = f.read(12)
            if not any(head.startswith(m) for m in _MAGIC):
                problems.append(f"{label}: '{rel}' is not the image type its "
                                f"name says")
                return None
        return real
    except FileNotFoundError:
        problems.append(f"{label}: file '{rel}' not found, default used")
        return None
    except OSError as exc:
        problems.append(f"{label}: '{rel}': {exc}")
        return None


def _sf(memo: dict, root, rel, exts, limit, problems, label):
    """safe_file, asked once per distinct file: a texture a parent component
    passes down to its children is checked, and complained about, once."""
    key = (rel, tuple(sorted(exts)))
    if key not in memo:
        memo[key] = safe_file(root, rel, exts, limit, problems, label)
    return memo[key]


def _url(path: Path) -> str:
    return path.as_uri()


# ── loading ──────────────────────────────────────────────────────────────────

def read_manifest(folder: str):
    """(manifest dict | None, problems) for one skin folder."""
    problems: list = []
    root = Path(folder)
    man = _read_json(root / "manifest.json", problems, "manifest.json")
    if man is None:
        if not problems:
            problems.append("manifest.json is missing")
        return None, problems
    sid = man.get("id")
    if not isinstance(sid, str) or not _ID.match(sid):
        problems.append("manifest.json: 'id' must be lowercase letters, "
                        "digits and dashes")
        return None, problems
    api = man.get("skin_api", 1)
    if not isinstance(api, int) or api < 1:
        problems.append("manifest.json: 'skin_api' must be a whole number")
        return None, problems
    if api > SKIN_API:
        problems.append(f"this skin needs skin_api {api}; this EqUpdater reads "
                        f"up to {SKIN_API}. Update EqUpdater or use another skin.")
        return None, problems
    return man, problems


def load_skin(folder: str, *, official: bool = False) -> Skin | None:
    """Load one skin folder into a resolved ``Skin``; None only when the
    manifest itself is unusable. Everything else degrades per asset."""
    man, problems = read_manifest(folder)
    if man is None:
        return Skin(id=os.path.basename(folder.rstrip("/\\")) or "invalid",
                    name=os.path.basename(folder), root=folder,
                    problems=problems, official=official)
    root = Path(folder)
    theme_raw = _read_json(root / "theme.json", problems, "theme.json") or {}
    layout_raw = _read_json(root / "layout.json", problems, "layout.json") or {}
    if layout_raw:
        theme_raw = _merge(theme_raw, {"layout": layout_raw})
    merged = _merge(BUILTIN_THEME, {k: v for k, v in theme_raw.items()
                                    if k in ("palette", "fonts", "metrics",
                                             "components", "background",
                                             "layout")})
    skin = Skin(id=man["id"], name=str(man.get("name") or man["id"])[:60],
                root=folder, version=str(man.get("version", ""))[:20],
                author=str(man.get("author", ""))[:80],
                description=str(man.get("description", ""))[:300],
                skin_api=man.get("skin_api", 1), problems=problems,
                official=official)
    prev = safe_file(root, man.get("preview", "preview.png"), IMAGE_EXT,
                     MAX_IMAGE, [], "preview")
    skin.preview = _url(prev) if prev else ""
    skin.theme, skin.fonts = _resolve(merged, root, problems)
    return skin


def _color(value, palette: dict, problems: list, where: str, default="#00000000"):
    if isinstance(value, str) and value.startswith("$"):
        value = palette.get(value[1:])
    if isinstance(value, str) and _COLOR.match(value):
        return value
    if value is not None:
        problems.append(f"{where}: '{value}' is not a colour (#RRGGBB or "
                        f"#AARRGGBB, or $name from the palette)")
    return default


def _resolve(theme: dict, root: Path, problems: list):
    """Palette references -> colours, asset paths -> file URLs, inheritance
    flattened. Result is plain data: dicts, lists, strings, numbers."""
    palette = {}
    for k, v in (theme.get("palette") or {}).items():
        palette[k] = _color(v, {}, problems, f"palette.{k}", "#ff00ff")
    fonts, font_files = {}, []
    for name, spec in (theme.get("fonts") or {}).items():
        if isinstance(spec, dict):
            fonts[name] = {"family": str(spec.get("family", "Segoe UI")),
                           "size": max(6, min(72, int(spec.get("size", 13)))),
                           "bold": bool(spec.get("bold", False)),
                           "italic": bool(spec.get("italic", False))}
    for rel in theme.get("fontFiles", []) or []:
        f = safe_file(root, rel, FONT_EXT, MAX_FONT, problems, "fontFiles")
        if f:
            font_files.append(str(f))
    comps_raw = theme.get("components") or {}
    out_comps, memo = {}, {}
    for cid in COMPONENTS:
        chain = [cid]
        seen = {cid}
        cur = (comps_raw.get(cid) or {}).get("extends") or PARENT.get(cid)
        while cur and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = (comps_raw.get(cur) or {}).get("extends") or PARENT.get(cur)
        flat: dict = {}
        for name in reversed(chain):             # parent first, child overrides
            flat = _merge(flat, comps_raw.get(name) or {})
        out_comps[cid] = _finish_component(cid, flat, palette, fonts, root,
                                           problems, memo)
    bg = _resolve_background(theme.get("background") or {}, palette, root,
                             problems)
    return {"palette": palette, "fonts": fonts,
            "metrics": theme.get("metrics") or {},
            "components": out_comps, "background": bg,
            "layout": theme.get("layout") or {}}, font_files


def _finish_component(cid, spec, palette, fonts, root, problems, memo) -> dict:
    where = f"components.{cid}"
    out = {"id": cid}
    for key in ("colors", "borderColors", "textColors"):
        out[key] = {s: _color(spec.get(key, {}).get(s), palette, problems,
                              f"{where}.{key}.{s}",
                              "" if key != "textColors" else "")
                    for s in STATES if spec.get(key, {}).get(s) is not None}
    out["textures"] = {}
    for state, rel in (spec.get("textures") or {}).items():   # None clears
        if state in STATES or state == "fill":
            f = _sf(memo, root, rel, IMAGE_EXT, MAX_IMAGE, problems,
                    f"{where}.textures.{state}")
            if f:
                out["textures"][state] = _url(f)
    slice_ = spec.get("slice")
    if isinstance(slice_, list) and len(slice_) == 4 and all(
            isinstance(n, int) and 0 <= n <= 512 for n in slice_):
        out["slice"] = slice_
    else:
        out["slice"] = [0, 0, 0, 0]
        if slice_ is not None:
            problems.append(f"{where}.slice: expected four whole numbers "
                            f"[left, top, right, bottom]")
    pad = spec.get("padding", [0, 0, 0, 0])
    out["padding"] = pad if isinstance(pad, list) and len(pad) == 4 else [0] * 4
    for key, default in (("borderWidth", 0), ("radius", 0), ("minHeight", 0),
                         ("opacity", 1.0), ("sliceScale", 1.0)):
        v = spec.get(key, default)
        out[key] = v if isinstance(v, (int, float)) else default
    font = spec.get("font")
    out["font"] = dict(fonts.get(font, fonts.get("body", {}))) \
        if isinstance(font, str) else (font if isinstance(font, dict) else
                                       dict(fonts.get("body", {})))
    icon = spec.get("icon")
    if icon:
        f = _sf(memo, root, icon, IMAGE_EXT, MAX_IMAGE, problems, f"{where}.icon")
        out["icon"] = _url(f) if f else ""
    else:
        out["icon"] = ""
    return out


def _resolve_background(bg: dict, palette, root, problems) -> dict:
    out = {"static": "", "animation": "", "animationKind": "", "fps": 0,
           "darken": float(bg.get("darken", 1.0) or 1.0),
           "fillMode": "cover" if bg.get("fillMode", "cover") != "tile" else "tile",
           "color": _color(bg.get("color"), palette, problems, "background.color",
                           palette.get("bg", "#000000")),
           "overlay": _color(bg.get("overlay"), palette, problems,
                             "background.overlay", "#00000000"),
           "fadeTop": int(bg.get("fadeTop", 0) or 0),
           "fadeBottom": int(bg.get("fadeBottom", 0) or 0)}
    s = safe_file(root, bg.get("static"), IMAGE_EXT, MAX_IMAGE, problems,
                  "background.static")
    if s:
        out["static"] = _url(s)
    a = safe_file(root, bg.get("animation"), ANIM_EXT, MAX_ANIMATION, problems,
                  "background.animation")
    if a:
        out["animation"] = _url(a)
        out["animationKind"] = "video" if a.suffix.lower() in (".mp4", ".webm") \
            else "image"
        out["fps"] = int(bg.get("fps", 0) or 0)
    return out


def builtin_skin() -> Skin:
    """The always-available flat skin, built from code. Used when no skin
    loads at all, and as the per-field fallback of every other skin."""
    theme, _ = _resolve(copy.deepcopy(BUILTIN_THEME), Path("."), [])
    return Skin(id="builtin", name="Built-in", theme=theme, official=True)


def discover(skins_dir: str, official_ids=()) -> dict:
    """{id: Skin} for every subfolder of ``skins_dir`` that has a usable
    manifest. Skins with only soft problems are still listed (problems kept);
    a folder whose manifest is unusable is reported under its folder name."""
    found: dict = {}
    try:
        names = sorted(os.listdir(skins_dir))
    except OSError:
        return found
    for name in names:
        folder = os.path.join(skins_dir, name)
        if not os.path.isdir(folder) or os.path.islink(folder):
            continue
        skin = load_skin(folder, official=name in official_ids)
        if skin is not None:
            found.setdefault(skin.id, skin)
    return found
