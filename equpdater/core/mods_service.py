"""The Mods page's logic, Qt-free, on top of the lifted 2.x operations.

Every decision still comes from the lifted code (``mod_plan``, the planner
rules, ``_apply_mods_worker`` ...): this class builds *rows* from them for a
view, and routes the view's clicks to the same methods the Tk window called.
Rows are computed on a worker (the section of each mod needs a look at the
game folder) and delivered to the GUI thread whole; the model then diffs them
by key, so one mod changing repaints one row.
"""

from __future__ import annotations

from ..i18n import tr
from ..planner import CAUSE_LOOKUP
from ..states import Status
from . import mods as M
from .ops_host import OpsHost


def action_for(mod: dict, state: dict, live, client: str, actions: dict,
               plan=None) -> dict | None:
    """The row's action word, what a click does and the tooltip: the logic of
    the Tk ``_style_mod_action_label`` without the widget."""
    if state.get("error"):
        return {"text": tr("retry"), "mode": "retry", "tip": tr("Try again")}
    p = plan or M.mod_plan(mod, state, live, client)
    entry = actions.get(p.status)
    if not entry:
        return None
    text, mode, tip = entry
    text, tip = tr(text), tr(tip)
    if p.status is Status.UPDATE_AVAILABLE and p.remote:
        text = tr("update to {version}", version=p.remote)
        tip = f"{p.local} → {p.remote}"
    elif p.status is Status.LOCAL_NEWER:
        tip = (tr("Installed {local}; the configured source offers {remote}.",
                  local=p.local, remote=p.remote)
               + " " + tr("EqUpdater will not replace it.") + "\n\n"
               + tr("Right-click to install {version} anyway.", version=p.remote))
    elif p.status is Status.MODIFIED:
        tip = tip + "\n\n" + tr("Right-click to replace them anyway.")
    elif p.status is Status.UNVERIFIABLE and p.detail.get("cause") == CAUSE_LOOKUP:
        look = M.MOD_LOOKUPS.get(mod["id"]) or {}
        text, mode = tr("can't check"), "retry"
        tip = (tr("Could not check the latest release: {reason}.",
                  reason=look.get("reason") or tr("the source did not answer"))
               + "\n\n" + tr("Nothing is changed. Click to check again."))
    elif p.status is Status.UNVERIFIABLE:
        tip = (tr("Installed {local}; the configured source offers {remote}.",
                  local=p.local, remote=p.remote) + " " + tip + "\n\n"
               + tr("Right-click to install {version} anyway.", version=p.remote))
    elif p.status is Status.VERSION_UNKNOWN:
        tip = (tip + "\n\n" + tr("Click to replace them with {version}. You are "
                                 "asked first.", version=p.remote))
    elif p.status is Status.MISSING_FILES and not p.local:
        mode = "recover"
    return {"text": text, "mode": mode, "tip": tip}


def problem_line(state: dict) -> tuple:
    """(text, severity) for the line under a row; severity 'error'|'warn'|''."""
    if state.get("error"):
        return f"⚠  {tr(state['error'])}", "error"
    if state.get("update_error"):
        return ("⚠  " + tr("Update failed: {reason}. Your current files "
                                "were kept.", reason=tr(state["update_error"])),
                "warn")
    return "", ""


class ModsService:
    def __init__(self, host: OpsHost, tasks, load_config=None):
        self.host, self.tasks = host, tasks
        self._load_config = load_config or M.load_config
        self.open_sections: dict = {}
        self.loaded = False
        self.on_rows = lambda items: None     # set by the bridge (GUI thread)
        self.sections = type(host).MOD_SECTIONS

    # -- rows -------------------------------------------------------------
    def build_items(self) -> list:
        """Headers and rows, in 2.x's order. Runs on a worker."""
        h = self.host
        cfg = self._load_config()
        mods_cfg = cfg.get("mods", {})
        client = h._game_path.get().strip()
        ordered = sorted(M.MODS_REGISTRY,
                         key=lambda m: (not m.get("essential", False), m["name"].lower()))
        groups = {t: [] for t in self.sections}
        for mod in ordered:
            groups[h._mod_section(mod, mods_cfg.get(mod["id"], {}))].append(mod)
        found = {m["id"] for m in h._found_mods()} if client else set()
        out = []
        for title in self.sections:
            is_open = self.open_sections.get(title, True)
            out.append({"key": "h:" + title, "kind": "header", "title": title,
                        "count": len(groups[title]), "open": is_open,
                        "manageAll": len(found) if title == "INSTALLED LOCALLY" else 0})
            if not is_open:
                continue
            for mod in groups[title]:
                out.append(self._row(mod, mods_cfg.get(mod["id"], {}), client,
                                     title))
        return out

    def _row(self, mod, state, client, section) -> dict:
        h = self.host
        live = next((m for m in h._mods_state if m["id"] == mod["id"]), None)
        plan = M.mod_plan(mod, state, live, client)
        act = action_for(mod, state, live or {}, client, h._MOD_ACTIONS, plan)
        text, sev = problem_line(state)
        here = bool(client) and bool(M.mod_found_files(mod, client))
        pending = h._mod_pending_state.get(mod["id"], {})
        return {
            "key": "m:" + mod["id"], "kind": "mod", "id": mod["id"],
            "section": section, "name": mod["name"],
            "essential": bool(mod.get("essential")),
            "description": tr(mod["description"]),
            "version": h._mod_version_text(state, live or {}),
            "installedGreen": M.mod_has_files(state),
            "hasError": bool(state.get("error")),
            "problem": text, "problemSeverity": sev,
            "actionText": (act or {}).get("text", ""),
            "actionMode": (act or {}).get("mode", ""),
            "actionTip": (act or {}).get("tip", ""),
            "ignore": bool(state.get("ignore_updates", False)),
            "enabled": bool(pending.get("enabled", state.get("enabled", False))),
            "here": here, "managed": bool(state.get("managed")),
            "custom": bool(mod.get("custom")),
            "repoUrl": mod.get("repo_url", ""),
            "busy": False,
        }

    def rebuild(self):
        """Compute rows off the GUI thread; deliver them whole."""
        self.tasks.submit("mods:rows", self.build_items,
                          on_done=lambda items: self.on_rows(items))

    def refresh(self):
        """Ask the sources for the latest versions (worker), then rebuild."""
        def work():
            self.host._load_mods_state()
        self.tasks.submit("mods:refresh", work,
                          on_done=lambda _r: (setattr(self, "loaded", True),
                                              self.rebuild()),
                          on_error=lambda _e: self.rebuild())

    # -- clicks ---------------------------------------------------------------
    def toggle_section(self, title):
        self.open_sections[title] = not self.open_sections.get(title, True)
        self.rebuild()

    def set_ignore(self, mod_id, value):
        self.host._set_ignore(mod_id, self.host._new_bool_var(bool(value)))
        self.rebuild()

    def install(self, mod_id):
        self.host._mod_install(mod_id)

    def uninstall(self, mod_id):
        self.host._mod_uninstall(mod_id)
        self.rebuild()

    def release(self, mod_id):
        self.host._mod_release(mod_id)

    def click_action(self, mod_id, mode):
        h = self.host
        if mode == "adopt":
            h._adopt_mod(mod_id)
        elif mode == "act":
            h._update_mod(mod_id)
        elif mode == "recover":
            h._mod_force_replace(mod_id)
        elif mode == "retry":
            h._mod_recheck(mod_id)

    def force_replace(self, mod_id):
        self.host._mod_force_replace(mod_id)

    def manage_all(self):
        self.host._manage_all_found()
        self.rebuild()
