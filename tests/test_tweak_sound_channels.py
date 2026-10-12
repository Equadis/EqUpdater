"""Tweaks -> SOUND -> Sound Channels: a slider for the two channel settings
EqUpdater always wrote as 64 (tester request, from the Modernization Tool)."""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSoundChannels(unittest.TestCase):

    def setUp(self):
        from equpdater import app       # other test files reload the package
        self.m = app
        self.client = tempfile.mkdtemp(prefix="equ-sound-")

    def tearDown(self):
        shutil.rmtree(self.client, ignore_errors=True)

    def config(self):
        path = os.path.join(self.client, "WTF", "Config.wtf")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def values(self, key):
        return re.findall(r'(?im)^SET %s "([^"]*)"$' % key, self.config())

    def test_the_slider(self):
        m = self.m
        self.assertEqual(m.TWEAKS_DEFAULTS["soundChannels"], 64)   # unchanged default
        self.assertEqual(m.TWEAKS_LIMITS["soundChannels"], (12, 64))
        ids = [t[0] for t in m.TWEAKS_ITEMS]
        self.assertEqual(ids.index("soundChannels"),
                         ids.index("soundInBackground") + 1)       # under SOUND
        item = m.TWEAKS_ITEMS[ids.index("soundChannels")]
        self.assertEqual((item[2], item[8]), ("number", 1))

    def test_a_fresh_config_uses_it(self):
        self.m.write_config_wtf(self.client, {**self.m.TWEAKS_DEFAULTS,
                                              "soundChannels": 32})
        self.assertEqual(self.values("SoundSoftwareChannels"), ["32"])
        self.assertEqual(self.values("SoundMaxHardwareChannels"), ["32"])

    def test_the_default_writes_what_it_always_did(self):
        self.m.write_config_wtf(self.client, dict(self.m.TWEAKS_DEFAULTS))
        self.assertEqual(self.values("SoundSoftwareChannels"), ["64"])
        self.assertEqual(self.values("SoundMaxHardwareChannels"), ["64"])

    def test_apply_updates_an_existing_config_in_place(self):
        os.makedirs(os.path.join(self.client, "WTF"))
        with open(os.path.join(self.client, "WTF", "Config.wtf"), "w",
                  encoding="utf-8") as f:
            f.write('SET gxResolution "2560x1440"\n'
                    'SET SoundSoftwareChannels "64"\n'
                    'SET soundmaxhardwarechannels "64"\n'
                    'SET accountName "someone"\n')
        self.m.update_config_wtf(self.client, {**self.m.TWEAKS_DEFAULTS,
                                               "soundChannels": 24})
        self.assertEqual(self.values("SoundSoftwareChannels"), ["24"])
        self.assertEqual(self.values("SoundMaxHardwareChannels"), ["24"])
        self.assertIn('SET gxResolution "2560x1440"', self.config())
        self.assertIn('SET accountName "someone"', self.config())

    def test_a_config_without_them_gets_them(self):
        os.makedirs(os.path.join(self.client, "WTF"))
        with open(os.path.join(self.client, "WTF", "Config.wtf"), "w",
                  encoding="utf-8") as f:
            f.write('SET gxWindow "1"\n')
        self.m.update_config_wtf(self.client, {**self.m.TWEAKS_DEFAULTS,
                                               "soundChannels": 48})
        self.assertEqual(self.values("SoundSoftwareChannels"), ["48"])
        self.assertEqual(self.values("SoundMaxHardwareChannels"), ["48"])

    def test_always_a_whole_number_in_range(self):
        channels = self.m.sound_channels
        for given, written in ((200, 64), (3, 12), (31.6, 32), ("40", 40),
                               ("lots", 64), (None, 64)):
            with self.subTest(given=given):
                self.assertEqual(channels({"soundChannels": given}), written)
        self.assertEqual(channels({}), 64)


if __name__ == "__main__":
    unittest.main()
