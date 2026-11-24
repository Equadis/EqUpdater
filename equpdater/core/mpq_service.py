"""The MPQ page's logic, Qt-free, on the lifted 2.x operations.

Rows come from the host's scan (``_mpq_installed_rows`` / ``_mpq_available_rows``,
filled by the lifted ``_mpq_check``): who made the pack, where it comes from,
and the judgement (``mpq.judge``) as a word with an explanation. Clicks go to the
same methods the Tk window called (``_mpq_fetch``, ``_mpq_uninstall`` ...).
"""

from __future__ import annotations

from .. import mpq
from ..i18n import tr
from . import mpq_ops as M
from .ops_host import OpsHost

_TONE = {"#d4b43c": "warn", None: "dim"}


def installed_row(row: dict, states: dict, busy_file, busy_text) -> dict:
    entry, src, fname, state = row["entry"], row["source"], row["file"], row.get("state")
    name = entry["name"] if entry else fname
    desc = tr(entry["description"]) if entry else ""
    listed = bool(entry and src and src.get("kind") == "catalogue")
    word, tone, tip = "", "dim", ""
    if state in states:
        word, colour, tip = states[state]
        word, tip, tone = tr(word), tr(tip), _TONE.get(colour, "warn")
    if src:
        source_text, can_link = tr("Source: {source}", source=mpq.describe_source(src)), False
    else:
        source_text, can_link = tr("Source: not linked"), True
    credit = entry if listed else None
    return {
        "key": "i:" + fname, "kind": "pack", "section": "INSTALLED", "file": fname,
        "name": name, "showFile": bool(fname.lower() != name.lower()),
        "description": desc, "installed": True, "linked": bool(src),
        "busy": busy_text if busy_file == fname else "",
        "action": "update" if (row["update"] and src) else "",
        "actionText": tr("Update") if (row["update"] and src) else "",
        "stateText": word, "stateTone": tone, "stateTip": tip,
        "offersReplace": bool(src and state in mpq.REPLACEABLE),
        "sourceText": source_text, "canLink": can_link,
        "linkTip": tr("EqUpdater did not install this pack and does not know "
                      "where it came from, so it cannot check it for updates. "
                      "Linking its source changes no files.") if state == "unmanaged" else "",
        "author": (credit or {}).get("author", ""),
        "homepage": (credit or {}).get("homepage", ""),
    }


def available_row(entry: dict, busy_file, busy_text) -> dict:
    return {
        "key": "a:" + entry["file"], "kind": "pack", "section": "AVAILABLE",
        "file": entry["file"], "name": entry["name"], "showFile": True,
        "description": tr(entry["description"]), "installed": False, "linked": False,
        "busy": busy_text if busy_file == entry["file"] else "",
        "action": "install", "actionText": tr("Install"),
        "stateText": "", "stateTone": "dim", "stateTip": "", "offersReplace": False,
        "sourceText": "", "canLink": False, "linkTip": "",
        "author": entry.get("author", ""), "homepage": entry.get("homepage", ""),
    }


class MpqService:
    def __init__(self, host: OpsHost, tasks, settings_get=None):
        self.host, self.tasks = host, tasks
        self.open_sections: dict = {}
        self.on_rows = lambda items: None
        self.opened = False
        self.link_for = ""                        # file the link dialog is for

    def build_items(self) -> list:
        h = self.host
        states = type(h)._MPQ_STATES
        busy, text = h._mpq_busy, h._mpq_busy_text
        out = []
        for title, rows in (("INSTALLED", list(h._mpq_installed_rows)),
                            ("AVAILABLE", list(h._mpq_available_rows))):
            is_open = self.open_sections.get(title, True)
            out.append({"key": "h:" + title, "kind": "header", "title": title,
                        "count": len(rows), "open": is_open})
            if not is_open:
                continue
            for r in rows:
                out.append(installed_row(r, states, busy, text) if title == "INSTALLED"
                           else available_row(r, busy, text))
        return out

    def rebuild(self):
        h = self.host
        # snapshot on the GUI thread; the building is cheap but off-thread anyway
        self.tasks.submit("mpq:rows", self.build_items,
                          on_done=lambda items: self.on_rows(items))

    # -- actions ----------------------------------------------------------
    def open(self):
        if not self.opened:
            self.opened = True
            self.host._mpq_check()

    def check(self):
        self.host._mpq_check()

    def toggle_section(self, title):
        self.open_sections[title] = not self.open_sections.get(title, True)
        self.rebuild()

    def _installed(self, file):
        return next((r for r in self.host._mpq_installed_rows if r["file"] == file), None)

    def click(self, file, action):
        h = self.host
        if action == "install":
            entry = M.mpq_patch_for(file)
            if entry:
                h._mpq_fetch(entry["file"], mpq.catalogue_source(entry["file"]), entry["name"])
        elif action == "update":
            row = self._installed(file)
            if row and row["source"]:
                name = row["entry"]["name"] if row["entry"] else file
                h._mpq_fetch(file, row["source"], name)

    def replace(self, file):
        row = self._installed(file)
        if row and row["source"]:
            self.host._mpq_replace(row)

    def uninstall(self, file):
        self.host._mpq_uninstall(file)

    def unlink(self, file):
        self.host._mpq_unlink(file)

    def catalogue(self) -> list:
        return [{"file": e["file"], "name": e["name"]} for e in M.MPQ_PATCHES]

    def link(self, file, choice: str, url: str) -> str:
        """Link ``file`` to a source. Returns '' on success or the reason it
        was refused (shown in the dialog, as in 2.x)."""
        if choice.startswith("cat:"):
            source = mpq.catalogue_source(choice[4:])
        else:
            try:
                source = mpq.parse_source(url)
            except ValueError as e:
                return str(e)
        self.host._mpq_link(file, source)
        return ""

    def set_ignore_speech(self, value: bool):
        from ..config import update_config
        update_config(lambda c: c.__setitem__("ignore_speech", bool(value)))

    def ignore_speech(self) -> bool:
        from ..config import load_config
        return bool(load_config().get("ignore_speech", False))
