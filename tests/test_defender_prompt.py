"""After installing: the Defender exclusion is offered once per game folder."""
import copy
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from equpdater import app


class TestDefenderOffer(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = tmp.name
        self.cfg = {}

        def update(fn):
            fn(self.cfg)
            return self.cfg
        for patch in (
                mock.patch.object(app, "load_config",
                                  side_effect=lambda *a: copy.deepcopy(self.cfg)),
                mock.patch.object(app, "update_config", side_effect=update),
                mock.patch.object(app.platforms, "WINDOWS", True),
                mock.patch.dict(os.environ, {"EQUPDATER_NO_DEFENDER_PROMPT": ""})):
            patch.start()
            self.addCleanup(patch.stop)
        self.later = []
        self.excluded = []
        cls = app.EqUpdaterApp
        self.host = SimpleNamespace(
            _game_path=SimpleNamespace(get=lambda: self.client),
            _first_run=False, _av_excluded=False, _settings_overlay=None,
            _asking=False,
            after=lambda ms, fn: self.later.append(fn),
            _allow_through_antivirus=lambda: self.excluded.append(1))
        for name in ("_offer_av_exclusion", "_prompt_av_exclusion"):
            setattr(self.host, name, getattr(cls, name).__get__(self.host))

    def offer(self, answer):
        with mock.patch("tkinter.messagebox.askyesno",
                        return_value=answer) as ask:
            self.host._offer_av_exclusion()
        return ask

    def test_yes_adds_the_exclusion(self):
        ask = self.offer(True)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(ask.call_args[0][0],
                         app.tr("Add the game folder to Defender exclusions?"))
        self.assertEqual(self.excluded, [1])

    def test_asked_once_per_folder_whatever_the_answer(self):
        self.offer(False)
        self.assertEqual(self.excluded, [])
        self.assertEqual(self.offer(True).call_count, 0)
        self.assertEqual(self.cfg["defender_offered_for"],
                         os.path.normpath(self.client))

    def test_another_folder_is_asked_about(self):
        self.offer(False)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.client = other.name
        self.assertEqual(self.offer(False).call_count, 1)

    def test_not_on_a_first_run(self):
        """Settings opens first; closing it asks (see _close_settings)."""
        self.host._first_run = True
        self.assertEqual(self.offer(True).call_count, 0)

    def test_not_without_a_game_folder(self):
        self.client = ""
        self.assertEqual(self.offer(True).call_count, 0)

    def test_not_off_windows(self):
        with mock.patch.object(app.platforms, "WINDOWS", False):
            self.assertEqual(self.offer(True).call_count, 0)

    def test_not_after_excluding_from_settings(self):
        self.host._av_excluded = True
        self.assertEqual(self.offer(True).call_count, 0)

    def test_waits_for_settings_and_for_another_question(self):
        for blocker in ("_settings_overlay", "_asking"):
            with self.subTest(blocker=blocker):
                self.later.clear()
                setattr(self.host, blocker, object() if blocker ==
                        "_settings_overlay" else True)
                self.assertEqual(self.offer(True).call_count, 0)
                self.assertEqual(len(self.later), 1)     # asks again later
                setattr(self.host, blocker, None if blocker ==
                        "_settings_overlay" else False)

    def test_the_manage_question_waits_while_this_one_is_open(self):
        host = SimpleNamespace(
            _game_path=SimpleNamespace(get=lambda: self.client),
            _settings_overlay=None, _running=False, _asking=True,
            after=lambda ms, fn: self.later.append(fn))
        host._offer_manage_found = app.EqUpdaterApp._offer_manage_found.__get__(host)
        with mock.patch.dict(os.environ, {"EQUPDATER_NO_MIGRATION_PROMPT": ""}):
            host._offer_manage_found()
        self.assertEqual(len(self.later), 1)


if __name__ == "__main__":
    unittest.main()
