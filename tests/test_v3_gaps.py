"""Phase 3 closing gaps: Defender exclusion, Linux game launcher, first-run language."""

import os
import shutil
import tempfile
import unittest
from unittest import mock

import importlib


def live():
    """The modules as the code under test holds them now: other tests reload
    equpdater.* modules, so names bound at import time can be stale."""
    av = importlib.import_module("equpdater.core.av_ops")
    ss = importlib.import_module("equpdater.core.settings_service")
    ops = importlib.import_module("equpdater.core.ops_host")
    return av, ss, ops


class _Win:
    """Pretend to be Windows (or not) for every module that asks."""
    def __init__(self, value):
        av, ss, _ops = live()
        self.ps = [mock.patch.object(m, "WINDOWS", value)
                   for m in {id(x): x for x in (av.platforms, ss.platforms)}.values()]

    def __enter__(self):
        for p in self.ps:
            p.start()

    def __exit__(self, *a):
        for p in self.ps:
            p.stop()


class _Config:
    @staticmethod
    def load_config():
        return live()[0].load_config()

    @staticmethod
    def update_config(fn):
        return live()[0].update_config(fn)


config = _Config
class Env(unittest.TestCase):
    def setUp(self):
        av, ss, ops = live()
        AutoDialogs, OpsHost = ops.AutoDialogs, ops.OpsHost
        CFG_NS = av.load_config.__globals__
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.game = os.path.join(self.d, "game")
        os.makedirs(self.game)
        self._cfg = CFG_NS["CONFIG_FILE"]
        CFG_NS["CONFIG_FILE"] = os.path.join(self.d, "config.json")
        self.addCleanup(CFG_NS.__setitem__, "CONFIG_FILE", self._cfg)
        self.store = {"game_dir": self.game}
        store = self.store
        settings = type("S", (), {"get": lambda s, k, d=None: store.get(k, d),
                                  "set": lambda s, k, v: store.__setitem__(k, v)})()
        self.dialogs = AutoDialogs()
        self.asked = []
        self.answer = True
        self.dialogs.ask_yes_no = lambda t, b, **k: self.asked.append((t, b)) or self.answer
        self.host = OpsHost(settings, dialogs=self.dialogs)
        self.events = []
        self.host.events = lambda name, **kw: self.events.append(name)
        self.settings = settings


class TestDefender(Env):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.host._shell_execute = lambda f, p: self.calls.append((f, p)) or 33
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        os.environ.pop("EQUPDATER_NO_DEFENDER_PROMPT", None)
        self.addCleanup(env.stop)
        w = _Win(True)
        w.__enter__()
        self.addCleanup(w.__exit__)

    def test_asked_once_per_folder_and_the_answer_is_recorded(self):
        self.host._offer_av_exclusion()
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(config.load_config()["defender_offered_for"], os.path.normpath(self.game))
        self.host._offer_av_exclusion()
        self.assertEqual(len(self.asked), 1)

    def test_yes_asks_windows_to_exclude_exactly_that_folder(self):
        self.host._offer_av_exclusion()
        f, p = self.calls[0]
        self.assertEqual(f, "powershell.exe")
        self.assertIn("Add-MpPreference -ExclusionPath '%s'" % os.path.normpath(self.game), p)
        self.assertTrue(self.host._av_excluded)

    def test_no_does_not_run_anything_and_is_not_asked_again(self):
        self.answer = False
        self.host._offer_av_exclusion()
        self.assertEqual(self.calls, [])
        self.host._offer_av_exclusion()
        self.assertEqual(len(self.asked), 1)

    def test_a_cancelled_uac_prompt_is_not_recorded_as_excluded(self):
        self.host._shell_execute = lambda f, p: 5
        self.host._allow_through_antivirus()
        self.assertFalse(self.host._av_excluded)

    def test_not_on_linux_and_not_when_the_prompt_is_switched_off(self):
        with _Win(False):
            self.host._offer_av_exclusion()
        with mock.patch.dict(os.environ, {"EQUPDATER_NO_DEFENDER_PROMPT": "1"}):
            self.host._offer_av_exclusion()
        self.assertEqual(self.asked, [])

    def test_changing_the_game_folder_offers_it_for_the_new_folder(self):
        GeneralService = live()[1].GeneralService
        other = os.path.join(self.d, "other")
        os.makedirs(other)
        svc = GeneralService(self.host, self.settings, None)
        self.assertEqual(svc.change_game_dir(other), "")
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.store["game_dir"], os.path.normpath(other))

    def test_the_settings_button_refuses_off_windows_and_without_a_folder(self):
        GeneralService = live()[1].GeneralService
        svc = GeneralService(self.host, self.settings, None)
        self.assertEqual(svc.exclude_from_defender(), "")
        self.assertEqual(len(self.calls), 1)
        with _Win(False):
            self.assertTrue(svc.exclude_from_defender())


class TestLauncherSettings(Env):
    def test_values_are_the_shared_record_saved_as_typed(self):
        L = live()[0].LauncherSettings
        self.assertEqual(L.values(), {"launch_command": "", "wine_prefix": "", "env_vars": ""})
        L.set("launch_command", "  umu-run {exe}  ")
        L.set("env_vars", "DXVK_HUD=fps\n")
        got = config.load_config()["game_launcher"]
        self.assertEqual(got["launch_command"], "umu-run {exe}")
        self.assertEqual(got["env_vars"], "DXVK_HUD=fps\n")           # env text is kept exactly
        with self.assertRaises(KeyError):
            L.set("bogus", "x")

    def test_a_bad_env_line_is_named_and_a_good_one_is_not(self):
        L = live()[0].LauncherSettings
        self.assertEqual(L.env_problems("DXVK_HUD=fps"), "")
        self.assertTrue(L.env_problems("this has no equals sign"))

    def test_runner_text_says_whether_the_command_or_automatic_is_used(self):
        L = live()[0].LauncherSettings
        L.set("launch_command", "lutris")
        self.assertIn("launch command", L.runner_text())
        L.set("launch_command", "")
        with mock.patch.object(live()[0].platforms, "detect_runner", lambda: ("wine", "/usr/bin/wine")):
            self.assertIn("wine", L.runner_text())
        with mock.patch.object(live()[0].platforms, "detect_runner", lambda: (None, None)):
            self.assertIn("nothing found", L.runner_text())

    def test_it_is_a_linux_setting(self):
        L = live()[0].LauncherSettings
        with _Win(True):
            self.assertFalse(L.available())
        with _Win(False):
            self.assertTrue(L.available())


class TestFirstLanguage(Env):
    def setUp(self):
        super().setUp()
        from equpdater import i18n
        GeneralService = live()[1].GeneralService
        self.i18n = i18n
        i18n.set_language("enUS")
        self.addCleanup(i18n.set_language, "enUS")
        self.svc = GeneralService(self.host, self.settings, None)
        e = mock.patch.dict(os.environ, {}, clear=False)
        e.start()
        os.environ.pop("EQUPDATER_NO_LANGUAGE_PROMPT", None)
        self.addCleanup(e.stop)

    def test_asked_on_a_first_run_only_and_only_once(self):
        self.assertTrue(self.svc.needs_language_prompt())
        self.svc.choose_language("enUS")
        self.assertFalse(self.svc.needs_language_prompt())

    def test_never_asked_when_2x_settings_were_imported_unless_support_asks(self):
        self.store["imported_from_2x"] = True
        self.assertFalse(self.svc.needs_language_prompt())
        config.update_config(lambda c: c.__setitem__("ask_language", True))
        self.assertTrue(self.svc.needs_language_prompt())
        self.svc.choose_language("enUS")
        self.assertNotIn("ask_language", config.load_config())

    def test_the_choice_is_the_language_tweak_and_restarts_to_show_it(self):
        from equpdater.core.tweaks import load_tweaks_config
        self.assertTrue(self.svc.choose_language("deDE"))
        self.assertEqual(load_tweaks_config()["locale"], "deDE")
        self.assertIn("restart", self.events)

    def test_keeping_the_current_language_does_not_restart(self):
        self.assertFalse(self.svc.choose_language("enUS"))
        self.assertNotIn("restart", self.events)

    def test_closing_the_prompt_keeps_what_is_saved(self):
        self.assertFalse(self.svc.choose_language(""))
        self.assertFalse(self.svc.needs_language_prompt())

    def test_every_language_is_offered_in_its_own_words(self):
        labels = {c["code"]: c["label"] for c in self.svc.language_choices()}
        self.assertEqual(labels["deDE"], "Deutsch")
        self.assertEqual(labels["enUS"], "English")
        self.assertEqual(sum(c["current"] for c in self.svc.language_choices()), 1)


if __name__ == "__main__":
    unittest.main()
