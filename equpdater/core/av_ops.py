"""Windows Defender exclusion for the game folder, and the Linux game launcher
settings, Qt-free.

Defender: some mods are flagged by antivirus by mistake and stop being
downloaded or installed. 2.x asked once per game folder whether to add the
folder to Defender's exclusions (``Add-MpPreference``, which Windows confirms
with its own UAC prompt) and recorded the answer; so does this. The request
goes through ``_shell_execute`` so a test never runs PowerShell.

Launcher (Linux): how PLAY starts the Windows client. Stored in the shared
``game_launcher`` record, exactly as 2.x wrote it (launch command, Wine prefix,
environment variables, "saved as typed").
"""

from __future__ import annotations

import os

from .. import platforms
from ..config import load_config, update_config
from ..i18n import tr
from .launch_funcs import env_problem_text

LAUNCHER_KEYS = ("launch_command", "wine_prefix", "env_vars")


def _windows_shell_execute(file: str, params: str) -> int:
    import ctypes
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", file, params, None, 0)


class AntivirusOps:
    _av_excluded = False
    _shell_execute = staticmethod(_windows_shell_execute)

    def _allow_through_antivirus(self):
        """Ask Windows to add the game folder to Defender's exclusions (UAC)."""
        client_dir = os.path.normpath(self._game_path.get().strip())
        if not client_dir or client_dir == ".":
            return
        cmd = f"Add-MpPreference -ExclusionPath '{client_dir}'"
        r = self._shell_execute("powershell.exe",
                                f'-NoProfile -WindowStyle Hidden -Command "{cmd}"')
        if r > 32:
            self._av_excluded = True
            update_config(lambda c: c.__setitem__("defender_offered_for", client_dir))
            self._log_line(f"Requested Defender exclusion for: {client_dir}\n", "ok")
        else:
            self._log_line("Antivirus exclusion cancelled.\n", "err")

    def _prompt_av_exclusion(self, title: str | None = None):
        """Ask once for this folder; the answer, yes or no, is recorded."""
        client = os.path.normpath(self._game_path.get().strip())
        update_config(lambda c: c.__setitem__("defender_offered_for", client))
        yes = self._ask_yes_no(
            title or tr("Game folder changed"),
            tr("It is highly recommended to add the game folder to your "
               "antivirus exclusions. Antivirus software may incorrectly "
               "detect some mods as threats and prevent them from being "
               "downloaded or installed properly.\n\n"
               "Do you want to add the game folder to Defender exclusions?"))
        if yes:
            self._allow_through_antivirus()

    def _offer_av_exclusion(self):
        """Offer the exclusion once per game folder (not on Linux, not for a
        folder already asked about or excluded)."""
        if not platforms.WINDOWS or os.environ.get("EQUPDATER_NO_DEFENDER_PROMPT"):
            return
        client = self._game_path.get().strip()
        if not client or self._av_excluded or not os.path.isdir(client):
            return
        if load_config().get("defender_offered_for") == os.path.normpath(client):
            return
        self._prompt_av_exclusion(tr("Add the game folder to Defender exclusions?"))


class LauncherSettings:
    """The Linux "Game launcher" fields, over the shared record."""

    @staticmethod
    def available() -> bool:
        return not platforms.WINDOWS

    @staticmethod
    def values() -> dict:
        saved = load_config().get("game_launcher") or {}
        return {k: saved.get(k, "") for k in LAUNCHER_KEYS}

    @staticmethod
    def set(key: str, value: str):
        if key not in LAUNCHER_KEYS:
            raise KeyError(key)
        value = value if key == "env_vars" else (value or "").strip()

        def merge(c):
            c.setdefault("game_launcher", {})[key] = value
        update_config(merge)

    @staticmethod
    def runner_text() -> str:
        launcher = load_config().get("game_launcher") or {}
        if (launcher.get("launch_command") or "").strip():
            return tr("Using your launch command.")
        name, path = platforms.detect_runner()
        return (tr("Automatic: {runner}", runner=f"{name} ({path})") if name else
                tr("Automatic: nothing found. Install Wine, or set a launch command."))

    @staticmethod
    def env_problems(text: str) -> str:
        _vars, problems = platforms.parse_env_lines(text)
        return env_problem_text(problems) if problems else ""
