"""3.0 settings and where its files live.

``settings.json`` (user preferences) and ``cache/news.json`` sit in
``<app data>/v3/``. The 2.x ``config.json`` is **read once, never written**:
the values 3.0 understands are copied forward the first time and the file is
left exactly as it was, so 2.x keeps working beside 3.0.
"""

from __future__ import annotations

import os

from .. import branding
from . import store as _store

DEFAULTS = {
    "skin": "bubbles",
    "animated_background": True,
    "pause_when_unfocused": False,
    "game_dir": "",
    "launch_default": None,
    "game_launcher": None,           # Linux launch command / env, as in 2.x
    "news_seen": {},
    "imported_from_2x": False,
}


def data_dir() -> str:
    return os.path.join(branding.app_data_dir(), "v3")


def skins_dir() -> str:
    """Where players put skins: next to their settings, survives updates."""
    return os.path.join(data_dir(), "skins")


def open_settings(directory: str | None = None, legacy: dict | None = None):
    d = directory or data_dir()
    s = _store.JsonStore(os.path.join(d, "settings.json"), DEFAULTS, schema=1)
    if not s.get("imported_from_2x"):
        if legacy is None:
            try:
                from .. import config as _cfg
                legacy = _cfg.load_config()             # read-only
            except Exception:
                legacy = {}
        import_legacy(s, legacy)
    return s


def import_legacy(s, legacy: dict) -> None:
    """Copy forward what 3.0 shares with 2.x. Never writes to 2.x's file."""
    if legacy.get("out_dir"):
        s.set("game_dir", os.path.normpath(legacy["out_dir"]))
    else:
        s.set("game_dir", os.path.join(os.path.expanduser("~"), "Games", "OctoWoW"))
    if legacy.get("launch_default"):
        s.set("launch_default", legacy["launch_default"])
    if "animated_background" in legacy:
        s.set("animated_background", bool(legacy["animated_background"]))
    if legacy.get("game_launcher"):
        s.set("game_launcher", legacy["game_launcher"])
    seen = legacy.get("news_seen")
    if isinstance(seen, dict):
        s.set("news_seen", {k: v for k, v in seen.items()
                            if isinstance(v, (int, float))})
    s.set("imported_from_2x", True)


def open_news_cache(directory: str | None = None):
    d = directory or data_dir()
    return _store.JsonStore(os.path.join(d, "cache", "news.json"),
                            {"sections": {}}, schema=1, delay=1.0)
