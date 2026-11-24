"""The Settings page's logic, Qt-free.

Everything here is a 2.x setting with its 2.x key and meaning, written through
the shared config (``update_config``), except the 3.0-only ones (skin,
animation, font override) which live in 3.0's own settings file.

Changing the game folder runs the lifted ``_game_folder_changed`` (it clears
the records tied to the old folder, unless the old path was a Windows/Wine
path being moved across). "Install missing ..." after switching an auto-install
option on is deferred until Settings closes, as in 2.x.
"""

from __future__ import annotations

import os

from .. import branding, i18n, platforms
from ..config import load_config, update_config
from . import logsink
from .ops_host import OpsHost

#: font override -> (family, size scale). "" means: the skin's own fonts.
FONT_OVERRIDES = {
    "": ("", 1.0),
    "arial": ("Arial", 1.0),
    "friz": ("Friz Quadrata", 1.0),
    "opendyslexic": ("OpenDyslexic", 0.92),      # 2.x's footprint compromise
}


def apply_font_override(theme: dict, choice: str) -> dict:
    """The theme with every font replaced by the chosen family (copy; the
    skin's own theme is untouched). Sizes are scaled for faces that run large."""
    family, scale = FONT_OVERRIDES.get(choice, FONT_OVERRIDES[""])
    if not family:
        return theme
    import copy
    out = copy.deepcopy(theme)

    def patch(f):
        if isinstance(f, dict) and f:
            f["family"] = family
            f["size"] = max(6, round(f.get("size", 13) * scale))
    for f in out.get("fonts", {}).values():
        patch(f)
    for comp in out.get("components", {}).values():
        patch(comp.get("font"))
    return out


class GeneralService:
    TOGGLES = {            # name -> (config key, default)
        "clearWdb": ("clear_wdb_on_launch", False),
        "closeOnLaunch": ("close_on_launch", False),
        "autoMods": ("auto_install_mods", False),
        "autoAddons": ("auto_install_addons", False),
    }

    def __init__(self, host: OpsHost, settings, tasks):
        self.host, self.settings, self.tasks = host, settings, tasks
        self._retrigger_mods = self._retrigger_addons = False

    # -- toggles -----------------------------------------------------------
    def get(self, name: str) -> bool:
        key, default = self.TOGGLES[name]
        return bool(load_config().get(key, default))

    def set(self, name: str, value: bool) -> None:
        key, _d = self.TOGGLES[name]
        value = bool(value)

        def merge(c):
            c[key] = value
            if name == "autoMods":
                c["essential_mods_asked"] = True     # a choice made here is an answer
        self.host._cfg = update_config(merge)
        if name == "autoMods":
            self._retrigger_mods = value
        elif name == "autoAddons":
            self._retrigger_addons = value

    def close(self) -> None:
        """Settings closed: do what turning an auto-install on promised."""
        if self._retrigger_mods:
            self._retrigger_mods = False
            self.host._install_missing_essential_mods()
        if self._retrigger_addons:
            self._retrigger_addons = False
            self.host._install_missing_recommended_addons()

    # -- game folder -------------------------------------------------------
    def game_dir(self) -> str:
        return (self.settings.get("game_dir") or "").strip()

    def change_game_dir(self, path: str) -> str:
        """Switch to ``path``. Returns '' or the reason it was refused."""
        path = os.path.normpath((path or "").strip())
        if not path or path == ".":
            return "Choose a folder."
        if not os.path.isdir(path):
            return "That folder does not exist."
        if os.path.normcase(path) == os.path.normcase(self.game_dir()):
            return ""
        self.host._game_folder_changed(path)          # lifted: wipes folder-scoped state
        self.settings.set("game_dir", path)
        self.host.events("game_dir_changed")
        return ""

    def open_game_dir(self) -> bool:
        d = self.game_dir()
        if not d or not os.path.isdir(d):
            return False
        try:
            platforms.open_folder(d)
        except OSError:
            return False
        return True

    # -- fonts / language -----------------------------------------------------
    def font_override(self) -> str:
        return self.settings.get("font_override") or ""

    def set_font_override(self, choice: str):
        if choice in FONT_OVERRIDES:
            self.settings.set("font_override", choice)

    def font_choices(self) -> list:
        from ..i18n import SYSTEM_FONT_LANGUAGES
        locked = i18n.language() in SYSTEM_FONT_LANGUAGES
        return [{"id": k, "family": v[0], "locked": locked and bool(k)}
                for k, v in FONT_OVERRIDES.items()]

    def language_label(self) -> str:
        from .tweaks import LOCALES
        return LOCALES.get(i18n.language(), (0, i18n.language()))[1]

    # -- log ---------------------------------------------------------------
    def session_log(self) -> str:
        return "\n".join(f"{t}  {m}" for t, m, _tag in list(logsink.recent)[-400:])
