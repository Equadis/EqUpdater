"""The Tweaks page's logic, Qt-free.

Values and limits are the 2.x tables (``core.tweaks``); this adds what the Tk
page kept in widgets: the editable copy, clamping, "has it changed", and
Apply / Reset as background operations. Like 2.x, Apply saves first, then
patches WoW.exe and updates Config.wtf; a failure leaves the saved values and
(since ``patch_exe`` is atomic) the game file as they were.
"""

from __future__ import annotations

import os

from . import tweaks as T


class TweaksService:
    def __init__(self, settings, tasks, *, load=None, save=None, patch=None,
                 wtf=None):
        self.settings, self.tasks = settings, tasks
        self._load = load or T.load_tweaks_config
        self._save = save or T.save_tweaks_config
        self._patch = patch or T.patch_exe
        self._wtf = wtf or T.update_config_wtf
        self.saved: dict = dict(T.TWEAKS_DEFAULTS)
        self.values: dict = dict(self.saved)
        self.busy = False
        self.message, self.message_error = "", False
        self.loaded = False

    # -- structure -------------------------------------------------------
    def rows(self) -> list:
        """One dict per row of the page, in 2.x's order."""
        out = []
        for tid, label, kind, _on, _x, tip, lo, hi, step in T.TWEAKS_ITEMS:
            if tid is None:
                out.append({"id": "", "label": label, "kind": "section"})
                continue
            if tid == "locale":              # the Language control lives in Settings, not on this page
                continue
            row = {"id": tid, "label": label, "kind": kind, "tip": tip or "",
                   "value": self.values.get(tid), "min": lo, "max": hi,
                   "step": step or 1, "default": T.TWEAKS_DEFAULTS.get(tid),
                   "changed": self.values.get(tid) != self.saved.get(tid)}
            out.append(row)
        return out

    # -- state -----------------------------------------------------------
    def game_dir(self) -> str:
        return (self.settings.get("game_dir") or "").strip()

    def has_game(self) -> bool:
        d = self.game_dir()
        return bool(d) and os.path.exists(os.path.join(d, "WoW.exe"))

    def dirty(self) -> bool:
        return self.values != self.saved

    def refresh(self, on_done=None):
        """Read the stored values on a worker (it may read WoW.exe)."""
        def done(vals):
            self.saved, self.values, self.loaded = dict(vals), dict(vals), True
            if on_done:
                on_done()
        self.tasks.submit("tweaks:load", self._load, on_done=done)

    # -- editing ---------------------------------------------------------
    def set_value(self, tid: str, raw):
        """Store ``raw`` for ``tid``, clamped; returns the stored value (the
        page shows it back, so an out-of-range entry never stays on screen)."""
        default = T.TWEAKS_DEFAULTS.get(tid)
        if tid not in T.TWEAKS_DEFAULTS:
            return None
        if isinstance(default, bool):
            v = bool(raw)
        elif isinstance(default, str):
            v = raw if raw in T.LOCALES else T.DEFAULT_LOCALE
        else:
            try:
                v = int(float(raw))
            except (TypeError, ValueError):
                v = default
            lo, hi = T.TWEAKS_LIMITS.get(tid, (None, None))
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
        self.values[tid] = v
        return v

    def fov_hint(self) -> int:
        try:
            return T.fov_default_for_display()
        except Exception:
            return T.TWEAKS_DEFAULTS["fieldOfView"]

    # -- actions ---------------------------------------------------------
    def apply(self, on_done=None):
        return self._run(dict(self.values), "apply", on_done)

    def reset(self, on_done=None):
        self.values = dict(T.TWEAKS_DEFAULTS)
        return self._run(dict(T.TWEAKS_DEFAULTS), "reset", on_done)

    def set_language(self, code: str, on_done=None):
        """The Language setting (Settings page): the stored game language, applied exactly as
        the Language tweak was: saved, then WoW.exe patched and Config.wtf updated. Other edits
        waiting unsaved on the Tweaks page are neither applied nor lost."""
        if code not in T.LOCALES or self.busy:
            return False
        pending = {k: v for k, v in self.values.items() if v != self.saved.get(k)}

        def values():
            vals = dict(self._load())
            vals["locale"] = code
            return vals

        def finished():
            self.values.update({k: v for k, v in pending.items() if k != "locale"})
            if on_done:
                on_done()
        return self._run(values, "language", finished)

    def _run(self, values, why: str, on_done):
        if self.busy:
            return False
        self.busy, self.message, self.message_error = True, "", False
        patch_now = self.has_game()
        game = self.game_dir()
        box = {}

        def work():
            vals = box["values"] = values() if callable(values) else values
            values_ = vals
            self._save(values_)                      # saved first, as in 2.x
            if not game:
                return "no_game"
            if not patch_now:
                return "no_exe"
            self._patch(game, values_)
            self._wtf(game, values_)
            return "applied"

        def done(result):
            self.busy = False
            self.saved = dict(box["values"])
            self.values = dict(box["values"])
            self.message = {"applied": "Tweaks applied.",
                            "no_exe": "WoW.exe not found – run Update first.",
                            "no_game": "Game folder not set."}[result]
            self.message_error = result != "applied"
            if on_done:
                on_done()

        def failed(exc):
            self.busy = False
            self.message = f"Tweaks failed – {exc}"
            self.message_error = True
            if on_done:
                on_done()
        return self.tasks.submit(f"tweaks:{why}", work, on_done=done,
                                 on_error=failed)
