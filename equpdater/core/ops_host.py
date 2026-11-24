"""The host the lifted mods/addons logic runs on, without Tk.

``ModsOps``/``AddonsOps``/``ModsRows`` were methods of the Tk window; they use
``self`` for state and for a few hooks. ``OpsHost`` supplies exactly those:

* **state** the code reads and writes (``_mods_state``, ``_addons_status``,
  ``_mod_pending_state`` ...), as plain attributes;
* **tiny adapters** with the Tk call shapes (``_game_path.get()``,
  ``_status_var.set()``, ``after(ms, fn)``);
* **events** instead of widgets: each repaint hook (``_render_mod_rows``,
  ``_refresh_mods_badge``, ``_mods_finished`` ...) emits a named event, and the
  Qt services translate events into *granular* model updates;
* **dialogs** through ``self.dialogs`` (the Qt bridge answers them in a nested
  event loop on the GUI thread, like Tk's modal calls; tests answer directly).

Threading contract (same as Tk's): hooks run on the GUI thread (``after``
marshals there); workers started by the logic never touch the host's state
except through ``after``.
"""

from __future__ import annotations

import threading

from ..config import load_config
from .addons_ops import AddonsOps
from .logsink import log
from .mods_ops import ModsOps
from .mods_rows import ModsRows


class Var:
    """The slice of ``tk.StringVar``/``BooleanVar`` the logic uses."""

    def __init__(self, value=None, on_set=None):
        self._v, self._on_set = value, on_set

    def get(self):
        return self._v

    def set(self, value):
        self._v = value
        if self._on_set:
            self._on_set(value)


class AutoDialogs:
    """Headless answers: what a test (or a batch run) gives to every question."""

    def __init__(self, yes=True):
        self.yes, self.asked = yes, []

    def ask_yes_no(self, title, body, **kw):
        self.asked.append(("yes_no", title))
        return self.yes

    def ask_ok_cancel(self, title, body, **kw):
        self.asked.append(("ok_cancel", title))
        return self.yes

    def info(self, title, body, **kw):
        self.asked.append(("info", title))

    def error(self, title, body, **kw):
        self.asked.append(("error", title))


class OpsHost(ModsRows, AddonsOps, ModsOps):
    def __init__(self, settings, schedule=None, events=None, dialogs=None):
        self.settings = settings
        self._schedule = schedule or (lambda ms, fn, *a: fn(*a))
        self.events = events or (lambda name, **kw: None)
        self.dialogs = dialogs or AutoDialogs()
        self._cfg = load_config()
        # state the lifted code owns
        self._mods_state: list = []
        self._mod_pending_state: dict = {}
        self._mod_row_vars: dict = {}
        self._mod_updates_count = 0
        self._addon_updates_count = 0
        self._addons_status = {"state": "idle", "addons": {}, "available": []}
        self._addons_busy = False
        self._addons_installing = False
        self._addons_identify_started = False
        self._addons_verified_ts = 0.0
        self._addon_errors: dict = {}
        self._last_addon_failure = None
        self._default_mods_install_started = False
        self._default_addons_install_started = False
        self._update_all_chain = False
        self._update_all_running = False
        self._btn_mode = "play"
        self._status_var = Var("", lambda v: self.events("status", text=v))
        self._lock = threading.RLock()

    # -- Tk-shaped adapters ----------------------------------------------
    @property
    def _game_path(self):
        return Var((self.settings.get("game_dir") or "").strip())

    def after(self, ms, fn=None, *args):
        self._schedule(ms, fn, *args)

    def _new_bool_var(self, value=False):
        return Var(value)

    def _log_line(self, text, tag=""):
        log(text.rstrip("\n"), tag)

    def _set_btn_busy(self, label="…"):
        self._btn_mode = "busy"
        self.events("busy", on=True, label=label)

    def _set_btn_play(self):
        self._btn_mode = "play"
        self.events("busy", on=False, label="")

    _set_btn_update = _set_btn_play

    def _status_error(self, text, action=None):
        self.events("status_error", text=text)

    def _show_logs(self):
        self.events("show_logs")

    # -- repaint hooks -> events -------------------------------------------
    def _render_mod_rows(self):
        self.events("mods_rows")

    def _rebuild_mod_rows(self):
        self.events("mods_rows")

    def _refresh_mods_badge(self):
        try:
            n = self._count_mod_updates()
        except Exception:
            n = 0
        if n != self._mod_updates_count:
            self._mod_updates_count = n
            self.events("mods_badge", count=n)

    def _refresh_apply_btn_visibility(self):
        self.events("pending", pending=dict(self._mod_pending_state))

    def _mods_finished(self, fresh_mods):
        self._mod_pending_state = self._mod_pending_state  # kept as the logic set it
        self.events("mods_finished")
        self.events("mods_rows")
        self._refresh_mods_badge()
        self._maybe_install_default_addons()
        if self._update_all_chain:
            self.after(0, self._chain_addon_update_all)
        self._refresh_ready_state()

    def _refresh_ready_state(self):
        self.events("ready")

    def _refresh_update_all_btn(self):
        self.events("update_all")

    def _update_all_count(self) -> int:
        return self._mod_updates_count + self._addon_updates_count

    def _render_addons(self, keep_scroll=False):
        self.events("addons_rows", keep_scroll=keep_scroll)

    def _refresh_addons_badge(self):
        self.events("addons_badge")

    def _refresh_addons_footer(self):
        self.events("addons_footer")

    def _refresh_manage_all(self):
        self.events("addons_footer")

    def _offer_manage_found(self):
        self.events("offer_manage_found")

    def _after_identify(self):
        self.events("addons_rows", keep_scroll=True)

    def _mod_icons_stale(self, mods_cfg) -> bool:
        return False

    # -- dialogs -------------------------------------------------------------
    def _ask_yes_no(self, title, body, **kw):
        return self.dialogs.ask_yes_no(title, body, **kw)

    def _ask_ok_cancel(self, title, body, **kw):
        return self.dialogs.ask_ok_cancel(title, body, **kw)

    def _show_info(self, title, body, **kw):
        return self.dialogs.info(title, body, **kw)

    def _show_error(self, title, body, **kw):
        return self.dialogs.error(title, body, **kw)

    def _ask_essential_mods(self, mods) -> bool:
        return bool(self.dialogs.ask_yes_no("essential", str([m["name"] for m in mods])))

    def _addon_dir_conflict_prompt(self, client: str) -> bool:
        return bool(self.dialogs.ask_yes_no("addon_dir_conflict", client))

    def _update_all_preview(self, plans) -> bool:
        return bool(self.dialogs.ask_yes_no("update_all", str(len(plans))))
