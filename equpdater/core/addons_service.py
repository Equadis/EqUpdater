"""The Addons page's logic, Qt-free, on top of the lifted 2.x operations.

Same split as ``mods_service``: decisions (status, plans, ownership, failure
text) come from the lifted code; this builds *rows* for a view from the
host's ``_addons_status`` and routes clicks to the same methods the Tk window
called. Filtering and ordering are done here on a snapshot, off the GUI
thread, so typing in the search box never waits on a list of hundreds.

Row tones map to skin text styles: ``act`` (text.link), ``ok`` (text.ok),
``warn`` (text.link), ``dim`` (text.dim), ``error`` (text.error).
"""

from __future__ import annotations

import html

from ..gitcompare import same_repo, short as short_sha
from ..i18n import tr
from . import addons as A
from .ops_host import OpsHost

SECTIONS = ("MANAGED", "INSTALLED LOCALLY", "AVAILABLE")
_TONE = {"#d4b43c": "warn", "#91a3bd": "dim"}


def title_html(toc_title: str, folder: str) -> str:
    """The addon's title with its WoW colour codes, as escaped rich text."""
    title = toc_title or folder
    parts = []
    for seg, col in A.parse_wow_colored(title)[:6]:
        txt = html.escape(seg, quote=False)
        parts.append(f'<font color="{col}">{txt}</font>' if col else txt)
    return "".join(parts)


def matches(rec: dict, flt: str) -> bool:
    if not flt:
        return True
    title = A.strip_wow_colors((rec.get("toc") or {}).get("Title") or "")
    hay = f"{rec['folder']} {title}".lower()
    return flt in hay or flt.replace(" ", "") in hay.replace(" ", "")


def warnings_for(rec: dict, installed: bool, known: set) -> list:
    toc = rec.get("toc") or {}
    out = []
    if toc.get("Interface") and toc["Interface"] != "11200":
        out.append(tr("Made for client {version}", version=toc["Interface"]))
    if installed and rec["folder"] != "pfUI":
        deps = [d.strip() for d in
                (toc.get("Dependencies") or "").replace(";", ",").split(",")
                if d.strip()]
        missing = [d for d in deps if d not in known]
        if missing:
            out.append(tr("Missing dependencies: {names}", names=", ".join(missing)))
    return out


def status_for(rec: dict, installed: bool, warnings: list, states: dict) -> dict:
    """What the right-hand end of a row says, and what a click on it does:
    the logic of the Tk ``_addon_row_status`` without widgets."""
    status = rec.get("status")
    none = {"kind": "none", "text": "", "action": "", "tone": "dim", "tip": ""}
    if status == "downloading":
        return {**none, "kind": "busy", "text": tr("downloading…")}
    if rec.get("check_failed") and not rec.get("error"):
        return {"kind": "button", "text": tr("can't check"), "action": "retry",
                "tone": "act",
                "tip": tr("Nothing is changed. Click to check again.")}
    if status == "invalid" or rec.get("error"):
        return {**none, "kind": "info", "text": "⛔ " + tr("Addon error"),
                "tone": "error"}
    if status == "unmanaged":
        if rec.get("suggested_git") or rec.get("git"):
            return {"kind": "button", "text": tr("Manage"), "action": "manage",
                    "tone": "ok",
                    "tip": tr("Let {app} update this addon from {source}. Your "
                              "current files are not changed now.",
                              app="EqUpdater",
                              source=rec.get("suggested_git") or rec.get("git"))}
        return none
    if status == "updateAvailable" and installed:
        tip = ""
        if rec.get("installed_sha") and rec.get("remote_sha"):
            tip = "%s → %s" % (short_sha(rec["installed_sha"]),
                               short_sha(rec["remote_sha"]))
        return {"kind": "button", "text": tr("Update"), "action": "update",
                "tone": "act", "tip": tip}
    if warnings:
        return {**none, "kind": "info", "text": "⚠ %s" % warnings[0], "tone": "warn"}
    entry = states.get(status)
    if entry:
        text, colour, tip = entry
        text, tip = tr(text), (tr(tip) if tip else tip)
        if rec.get("reason") and status == "differentSource":
            tip = (tip + "\n\n" + tr(str(rec["reason"]))).strip()
        return {**none, "kind": "info", "text": text,
                "tone": _TONE.get(colour, "warn"), "tip": tip}
    return none


class AddonsService:
    def __init__(self, host: OpsHost, tasks):
        self.host, self.tasks = host, tasks
        self.filter_text = ""
        self.open_sections: dict = {}
        self.on_rows = lambda items: None        # set by the bridge (GUI thread)
        self.on_footer = lambda text, mode: None
        self.opened = False

    # -- snapshot (GUI thread) -> rows (worker) ---------------------------
    def _snapshot(self) -> dict:
        st = self.host._addons_status
        return {"addons": dict(st["addons"]), "available": list(st["available"]),
                "state": st["state"], "busy": self.host._addons_busy}

    def build_items(self, snap: dict, flt: str, open_sections: dict) -> list:
        flt = flt.strip().lower()
        addons, avail = snap["addons"], snap["available"]
        states = type(self.host)._ADDON_STATES
        known = set(addons)

        def keep(rows):
            return [r for r in sorted(rows, key=lambda r: r["folder"].lower())
                    if matches(r, flt)]
        every = keep(addons.values())
        managed = [r for r in every if r.get("status") != "unmanaged"]
        local = [r for r in every if r.get("status") == "unmanaged"]
        available = keep(a for a in avail if a["folder"] not in addons)
        available.sort(key=lambda a: (a["folder"] not in A.RECOMMENDED_ADDONS,
                                      a["folder"].lower()))
        out = []
        for title, rows in zip(SECTIONS, (managed, local, available)):
            is_open = open_sections.get(title, True)
            out.append({"key": "h:" + title, "kind": "header", "title": title,
                        "count": len(rows), "open": is_open})
            if not is_open:
                continue
            for rec in rows:
                out.append(self._row(rec, title, addons, known, states))
        return out

    def _row(self, rec, section, addons, known, states) -> dict:
        folder = rec["folder"]
        installed = folder in addons
        toc = rec.get("toc") or {}
        warns = warnings_for(rec, installed, known)
        st = status_for(rec, installed, warns, states)
        suggested = rec.get("suggested_git")
        switch = bool(installed and suggested and rec.get("git")
                      and not rec.get("custom")
                      and not same_repo(suggested, rec["git"]))
        problem, severity = "", ""
        if rec.get("error"):
            problem, severity = f"⚠  {tr(rec['error'])}", "error"
        elif rec.get("check_failed"):
            problem = ("⚠  " + tr("Could not check the addon's source: {reason}. "
                                  "Your installed files are not changed.",
                                  reason=tr(rec["check_failed"])))
            severity = "warn"
        git = rec.get("git") or ""
        return {
            "key": "a:" + folder, "kind": "addon", "id": folder,
            "section": section, "titleHtml": title_html(toc.get("Title"), folder),
            "description": A.strip_wow_colors(toc.get("Notes")
                                              or rec.get("description") or ""),
            "recommended": folder in A.RECOMMENDED_ADDONS,
            "installed": installed,
            "canRelease": bool(installed and rec.get("status") not in
                               ("unmanaged", "available", None)),
            "switch": switch, "switchTip": (tr(
                "A different source is recommended for this addon:\n{source}\n\n"
                "Switching forks is a choice, not an update, so nothing happens "
                "until you ask.", source=suggested) if switch else ""),
            "statusKind": st["kind"], "statusText": st["text"],
            "statusAction": st["action"], "statusTone": st["tone"],
            "statusTip": st["tip"],
            "offersReplace": bool(st["kind"] == "info" and A.addon_offers_replace(rec)),
            "problem": problem, "problemSeverity": severity,
            "repoUrl": (git[:-4] if git.endswith(".git") else git),
        }

    def footer(self, snap: dict | None = None) -> tuple:
        snap = snap or self._snapshot()
        if snap["state"] == "verifying" or snap["busy"]:
            return tr("Checking…"), "busy"
        recs = snap["addons"].values()
        if any(r["status"] == "updateAvailable" for r in recs):
            return tr("Update all"), "update"
        held = sum(1 for r in recs if r["status"] in (
            "localNewer", "modified", "diverged", "differentSource", "unverifiable"))
        if held:
            return tr("Nothing to update · {count} held back", count=held), "info"
        return tr("Everything up to date"), "info"

    def rebuild(self):
        snap = self._snapshot()
        flt, secs = self.filter_text, dict(self.open_sections)
        self.on_footer(*self.footer(snap))
        self.tasks.submit("addons:rows",
                          lambda: self.build_items(snap, flt, secs),
                          on_done=lambda items: self.on_rows(items))

    # -- actions (GUI thread; the lifted methods start their own workers) ---
    def open(self):
        if not self.opened:
            self.opened = True
            self.host._addons_verify(force=False)
            self.rebuild()

    def verify(self, force=True):
        self.host._addons_verify(force=force)

    def set_filter(self, text: str):
        if text != self.filter_text:
            self.filter_text = text
            self.rebuild()

    def toggle_section(self, title):
        self.open_sections[title] = not self.open_sections.get(title, True)
        self.rebuild()

    def _rec(self, folder):
        h = self.host._addons_status
        rec = h["addons"].get(folder) or next(
            (a for a in h["available"] if a["folder"] == folder), None)
        return dict(rec) if rec else None

    def click_status(self, folder, action):
        rec = self._rec(folder)
        if rec is None:
            return
        if action == "update":
            self.host._addon_apply([rec])
        elif action == "manage":
            self.host._addon_adopt(rec)
        elif action == "retry":
            self.host._addons_verify(force=True)

    def install(self, folder):
        rec = self._rec(folder)
        if rec:
            self.host._addon_apply([rec])

    def remove(self, folder):
        self.host._addon_remove(folder)

    def release(self, folder):
        self.host._addon_release(folder)

    def switch_source(self, folder):
        rec = self._rec(folder)
        if rec and rec.get("suggested_git"):
            self.host._addon_switch_source(folder, rec["suggested_git"])

    def force_replace(self, folder):
        rec = self._rec(folder)
        if rec:
            self.host._addon_force_replace(rec)

    def update_all(self):
        self.host._addon_update_all()

    def manage_all(self):
        self.host._manage_all_found()
