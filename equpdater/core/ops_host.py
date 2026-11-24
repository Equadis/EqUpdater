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

import os
import threading

from .. import branding, config as _config
from ..config import load_config, update_config
from ..planner import skipped_notably, updatable
from .addons_ops import AddonsOps
from ..i18n import tr
from .logsink import log
from .mods_ops import ModsOps
from .mods_rows import ModsRows
from .mpq_ops import MPQ_PATCHES
from .mpq_page_ops import MpqPageOps
from .settings_ops import SettingsOps
from .av_ops import AntivirusOps
from .launch_ops import LaunchOps


def CONFIG_FILE_PATH():
    return _config.CONFIG_FILE


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

    def ask_yes_no_cancel(self, title, body, **kw):
        self.asked.append(("yes_no_cancel", title))
        return None            # "decide later": the safe answer

    def info(self, title, body, **kw):
        self.asked.append(("info", title))

    def error(self, title, body, **kw):
        self.asked.append(("error", title))


class OpsHost(AntivirusOps, LaunchOps, SettingsOps, MpqPageOps, ModsRows, AddonsOps, ModsOps):
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
        self._mpq_busy = None
        self._mpq_busy_text = ""
        self._mpq_installed_rows: list = []
        self._mpq_available_rows: list = list(MPQ_PATCHES)
        self._mpq_updates_count = 0
        self._running = False
        self._first_run = not os.path.exists(CONFIG_FILE_PATH())
        self._client_ready = False
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
        self.events("btn", mode="busy", label=label)

    def _set_btn_play(self):
        self._btn_mode = "play"
        self.events("busy", on=False, label="")
        self.events("btn", mode="play", label="")

    def _set_btn_update(self):
        self._btn_mode = "update"
        self.events("busy", on=False, label="")
        self.events("btn", mode="update", label="")

    def _refresh_ready_state(self):
        """PLAY and the status line. 3.0 has no base-client sync, so there is
        no "update available": the client is ready when there is something
        to start. Otherwise the 2.x rules: never while addons install, and
        not while a mod is in an error state."""
        self._refresh_launch_menu()
        if self._addons_installing:
            self._set_btn_busy(tr("Installing…"))
            self._status_var.set(tr("Downloading addons…"))
            return
        client = self._game_path.get().strip()
        self._client_ready = bool(client) and bool(self._launch_target(client))
        if not self._client_ready:
            self._set_btn_update()
            self._status_error("No game found - choose the game folder in Settings", None)
            return
        if self._mods_have_errors():
            self._set_btn_busy(tr("PLAY"))
            self._status_error(tr("Mod errors – check the MODS tab"),
                               lambda: self._show_page("MODS"))
        else:
            self._set_btn_play()
            self._status_var.set("Ready to play")

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

    def _progress_set(self, done, total, label=""):
        self._progress = (done, total, label)
        self.events("progress", done=done, total=total, label=label)

    def _progress_clear(self):
        if self._progress is not None:
            self._progress = None
            self.events("progress", done=0, total=0, label="")

    _progress = None

    def _mods_finished(self, fresh_mods):
        self._progress_clear()
        self._mod_pending_state = self._mod_pending_state  # kept as the logic set it
        self.events("mods_finished")
        self.events("mods_rows")
        self._refresh_mods_badge()
        self._maybe_install_default_addons()
        if self._update_all_chain:
            self.after(0, self._chain_addon_update_all)
        self._refresh_ready_state()

    def _set_pending_reconcile(self, mode):
        """2.x's pending client reconcile belongs to the base-client sync,
        which 3.0 does not have; nothing to schedule."""

    def _apply_tweaks_worker(self, client_dir: str, tweaks: dict):
        """Patch WoW.exe and Config.wtf with ``tweaks`` (what the 1.3
        migration needs); logs instead of raising, like the Tk original."""
        from . import tweaks as T
        try:
            T.patch_exe(client_dir, tweaks, lambda m, tag="": self._log_line(m, tag))
            T.update_config_wtf(client_dir, tweaks)
            self._log_line("Tweaks applied.", "ok")
        except Exception as exc:
            self._log_line(f"Tweak patch failed: {exc}", "err")

    def _show_page(self, page):
        self.events("show_page", page=page)

    def _minimize_window(self):
        self.events("minimize")

    def _refresh_launch_menu(self):
        self.events("launch_menu")

    def _diag_changed(self, _attempt=None):
        """From an observer thread: tell the view (if any) to re-read the disk."""
        self.after(0, lambda: self.events("diag_changed"))

    def _refresh_nav_badges(self):
        self.events("nav_badges")

    def _restart(self):
        self.events("restart")

    def _render_mpq(self):
        self.events("mpq_rows")

    def _refresh_mpq_badge(self):
        self.events("mpq_badge", count=self._mpq_updates_count)

    def _refresh_update_all_btn(self):
        """As the Tk button did: the run is over once nothing is busy and no
        chained addon step is waiting; the view follows through an event."""
        busy = (self._btn_mode == "busy" or self._addons_busy
                or getattr(self, "_running", False))
        if self._update_all_running and not busy and not self._update_all_chain:
            self._update_all_running = False
        self.events("update_all")

    def _update_all_count(self) -> int:
        return self._mod_updates_count + self._addon_updates_count

    def _render_addons(self, keep_scroll=False):
        self.events("addons_rows", keep_scroll=keep_scroll)

    def _refresh_addons_badge(self):
        """Addons that can be safely updated (the nav badge): the Tk window's
        rule - unmanaged, modified or diverged ones are not pending tasks."""
        count = sum(1 for r in self._addons_status["addons"].values()
                    if r["status"] == "updateAvailable")
        if count != self._addon_updates_count:
            self._addon_updates_count = count
        self.events("addons_badge", count=count)
        self._refresh_update_all_btn()

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
        """The one-time first-run question (2.x wording). Records that it was
        asked and the answer, so it is never asked again."""
        names = "\n".join("    • %s" % m["name"] for m in mods)
        yes = bool(self.dialogs.ask_yes_no(
            tr("Install essential mods?"),
            tr("{app} can install these client mods:\n\n{mods}\n\n"
               "They are DLLs the game loads at start-up, downloaded from each "
               "mod's own release page. Nothing is installed unless you say "
               "yes.\n\nYou can change this later in Settings → Install "
               "essential mods, or pick mods one at a time on the MODS "
               "tab.\n\nInstall them now?", app=branding.APP_NAME, mods=names)))

        def _merge(c):
            c["essential_mods_asked"] = True
            c["auto_install_mods"] = yes
        self._cfg = update_config(_merge)
        return yes

    def _ask_yes_no_cancel(self, title, body, **kw):
        return self.dialogs.ask_yes_no_cancel(title, body, **kw)

    def _update_all_preview(self, plans) -> bool:
        """What the update will and will not do; True to go ahead. Shown only
        when something is being held back (2.x: a confirmation nobody needs is
        one everybody learns to dismiss)."""
        doing, held = updatable(plans), skipped_notably(plans)
        if not held:
            return True
        lines = []
        if doing:
            lines.append(tr("Updates to install: {count}", count=len(doing)))
            for p in doing:
                lines.append("    %s   %s → %s" % (p.component_id, p.local, p.remote)
                             if p.local and p.remote else "    %s" % p.component_id)
            lines.append("")
        else:
            lines += [tr("Nothing can be safely updated right now."), ""]
        lines.append(tr("Not being changed:"))
        for p in held:
            lines += ["    %s" % p.component_id, "        %s" % tr(str(p.reason))]
        lines += ["", tr("Anything listed under “not being changed” stays exactly "
                         "as it is. Each one can still be dealt with individually from its "
                         "own tab.")]
        if not doing:
            self.dialogs.info(tr("Update all"), "\n".join(lines))
            return False
        return bool(self.dialogs.ask_ok_cancel(tr("Update all"), "\n".join(lines)))
