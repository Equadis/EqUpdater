"""What PLAY starts, and starting it. Qt-free.

The detection and the plan are the 2.x functions in ``platforms`` unchanged
(``launch_options``, ``resolve_launch_target``, ``launch_plan``,
``program_problem``, ``spawn_detached``). This module only owns the *choice*
(persisted in 3.0's settings, imported once from 2.x) and the launch call.

Not yet migrated (Phase 3, listed in FEATURE_PARITY.md): the pre-launch steps
2.x runs in ``_launch_game`` - dlls.txt reconcile, the Auto Loot gate, the
launch-attempt diagnostics record and WDB clearing - and the post-launch
watcher. ``prelaunch`` is the seam they plug into.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .. import platforms


@dataclass
class LaunchResult:
    ok: bool
    message: str = ""
    target: str = ""


class LaunchService:
    def __init__(self, settings, spawn=None, prelaunch=()):
        self.settings = settings
        self._spawn = spawn or platforms.spawn_detached
        self.prelaunch = list(prelaunch)       # callables(client_dir, exe) -> None

    # -- state ----------------------------------------------------------
    def game_dir(self) -> str:
        return (self.settings.get("game_dir") or "").strip()

    def options(self) -> list:
        d = self.game_dir()
        return platforms.launch_options(d) if d and os.path.isdir(d) else []

    def target(self, options=None) -> str | None:
        opts = self.options() if options is None else options
        return platforms.resolve_launch_target(opts, self.settings.get("launch_default"))

    def choose_default(self, name: str) -> bool:
        """Remember the player's choice. Only a listed file can be chosen; the
        saved name is kept even if its file vanishes for a while (a mod update
        removes VanillaFixes.exe briefly), as in 2.x."""
        if name.lower() not in [o.lower() for o in self.options()]:
            return False
        self.settings.set("launch_default", name)
        return True

    def can_play(self) -> bool:
        return self.target() is not None

    # -- launching ------------------------------------------------------
    def launch(self) -> LaunchResult:
        client = self.game_dir()
        label = self.target()
        if not label:
            return LaunchResult(False, "No launch files found")
        exe = os.path.join(client, label)
        if not os.path.exists(exe):
            return LaunchResult(False, f"{label} not found in the game folder", label)
        if label.lower().endswith(".exe"):
            try:
                problem = platforms.program_problem(exe)
            except OSError as exc:
                return LaunchResult(False, f"Could not read {label}: {exc}", label)
            if problem:
                return LaunchResult(
                    False, f"{label} is {problem}: not a complete Windows program",
                    label)
        for step in self.prelaunch:
            step(client, exe)
        plan = platforms.launch_plan(client, exe, self.settings.get("game_launcher"))
        if plan.env_problems:
            return LaunchResult(False, "The game launcher's environment variables "
                                       "have lines that cannot be used", label)
        if not plan.ok:
            return LaunchResult(False, "No way to run the game was found "
                                       "(no launch command, no wine or umu-run)", label)
        try:
            self._spawn(plan.argv, cwd=plan.cwd, env=plan.env, output=None,
                        console=plan.console)
        except OSError as exc:
            return LaunchResult(False, f"{plan.argv[0]} could not be started: "
                                       f"{exc.strerror or exc}", label)
        return LaunchResult(True, f"Launched {label}", label)
