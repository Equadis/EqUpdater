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
    "font_override": "",           # "", "arial", "friz", "opendyslexic", "objectsans"
    "game_launcher": None,           # Linux launch command / env, as in 2.x
    "news_seen": {},
    "show_server_status": True,     # the footer chip (reads OctoBot's static status files)
    "imported_from_2x": False,
}


#: The only social preferences left. Everything else about the sidebar and presence is fixed in
#: ``core/social_service.py`` (sidebar always on; launcher and in-game presence on, subject to the
#: chosen status; realm shown while in game; character and zone never; Away after 15 minutes idle).
SOCIAL_DEFAULTS = {
    "own_status": "online",             # the player's own choice: online | away | dnd | invisible
    "account_username": "",
    "account_avatar_url": "",
}
#: Social settings the first beta stored. They no longer exist and are removed on load, so an old
#: file can never change the behaviour (for example "show_character": true).
OBSOLETE_SOCIAL_KEYS = ("social_enabled", "sidebar_collapsed", "show_launcher_presence", "show_game_presence",
                        "show_character", "show_realm", "show_zone", "friend_notifications", "auto_away_minutes",
                        "default_status", "friends_sort", "friends_realm", "presence_interval",
                        "renderer",        # "Low-memory rendering": only changed how the launcher itself draws; removed
                        "dev_tokens")      # session tokens an early test build kept in this file: secrets belong in the credential store
DEFAULTS.update(SOCIAL_DEFAULTS)


def data_dir() -> str:
    return os.path.join(branding.app_data_dir(), "v3")


def skins_dir() -> str:
    """Where players put skins: next to their settings, survives updates."""
    return os.path.join(data_dir(), "skins")


class SettingsStore(_store.JsonStore):
    """``game_dir`` is a *shared* record: every lifted backend function reads
    ``out_dir`` from the 2.x config, so that is where the game folder lives.
    ``settings.json`` keeps a copy only as the fallback for a profile that
    has no config yet. Reading and writing ``game_dir`` here goes to the
    shared record, so 2.x and 3.0 can never disagree about it."""

    #: settings key -> the 2.x config key that owns it (shared records)
    SHARED = {"game_dir": "out_dir", "launch_default": "launch_default",
              "game_launcher": "game_launcher"}

    def get(self, key, default=None):
        if key in self.SHARED:
            from .. import config as _cfg
            val = _cfg.load_config().get(self.SHARED[key])
            if val:
                return os.path.normpath(val) if key == "game_dir" else val
        return super().get(key, default)

    def set(self, key, value):
        if key in self.SHARED and value:
            from .. import config as _cfg
            _cfg.update_config(lambda c: c.__setitem__(self.SHARED[key], value))
        super().set(key, value)


def open_server_cache(directory: str | None = None):
    """The last good OctoBot snapshot (so the footer chip is not empty at start)."""
    d = directory or data_dir()
    return _store.JsonStore(os.path.join(d, "cache", "server.json"),
                            {"snapshot": None, "fetched_at": 0}, schema=1, delay=2.0)


def open_social_store(directory: str | None = None):
    """The friends list and the realm list, on this computer only."""
    d = directory or data_dir()
    return _store.JsonStore(os.path.join(d, "social.json"), {"friends": [], "realms": [], "realms_at": 0},
                            schema=1, delay=0.5)


def migrate_social(s) -> None:
    """Drop the preferences of the removed Social section; keep (or repair) the player's own status."""
    with s._lock:
        gone = [k for k in OBSOLETE_SOCIAL_KEYS if k in s.data]
        for k in gone:
            del s.data[k]
        if s.data.get("own_status") not in ("online", "away", "dnd", "invisible"):
            s.data["own_status"] = "online"
            gone.append("own_status")
        if gone:
            s._schedule()


def open_settings(directory: str | None = None, legacy: dict | None = None):
    d = directory or data_dir()
    s = SettingsStore(os.path.join(d, "settings.json"), DEFAULTS, schema=1)
    migrate_social(s)
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
    # (the base class: importing must not write the shared 2.x config back)
    if legacy.get("out_dir"):
        _store.JsonStore.set(s, "game_dir", os.path.normpath(legacy["out_dir"]))
    else:
        _store.JsonStore.set(s, "game_dir",
                             os.path.join(os.path.expanduser("~"), "Games", "OctoWoW"))
    acc = (legacy.get("accessibility") or {})
    choice = acc.get("font_choice") or ("opendyslexic" if acc.get("dyslexic_font") else "")
    if choice in ("friz", "opendyslexic"):           # 2.x's "arial" was its default
        s.set("font_override", choice)
    if legacy.get("launch_default"):
        _store.JsonStore.set(s, "launch_default", legacy["launch_default"])
    if "animated_background" in legacy:
        s.set("animated_background", bool(legacy["animated_background"]))
    if legacy.get("game_launcher"):
        _store.JsonStore.set(s, "game_launcher", legacy["game_launcher"])
    seen = legacy.get("news_seen")
    if isinstance(seen, dict):
        s.set("news_seen", {k: v for k, v in seen.items()
                            if isinstance(v, (int, float))})
    s.set("imported_from_2x", True)


def open_news_cache(directory: str | None = None):
    d = directory or data_dir()
    return _store.JsonStore(os.path.join(d, "cache", "news.json"),
                            {"sections": {}}, schema=1, delay=1.0)
