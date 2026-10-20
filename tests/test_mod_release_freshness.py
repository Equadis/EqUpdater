"""Mod release checks: every launch looks again (with a five-minute floor),
and a failed check is reported, never papered over with the old release.

A tester's ClassicAPI stayed on 1.15.16 after 1.16.0 came out, through
several restarts: the release cache lasted an hour, and launch used it."""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestReleaseFreshness(unittest.TestCase):

    def setUp(self):
        from equpdater import app
        self.app = app
        self.mod = next(m for m in app.MODS_REGISTRY if m["id"] == "ClassicAPI")
        self.cfg = {}
        self.calls = []

        def update(mutator, *a, **k):
            mutator(self.cfg)
            return self.cfg
        self.patches = [
            mock.patch.object(app, "load_config", lambda *a, **k: self.cfg),
            mock.patch.object(app, "update_config", update),
            mock.patch.object(app, "_release_latest", self.latest),
        ]
        for p in self.patches:
            p.start()
        self.answer = {"tag_name": "v1.16.0", "assets": []}

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def latest(self, src, raise_errors=False):
        self.calls.append(src["repo"])
        return self.answer

    def cache(self, tag, age):
        self.cfg["mod_release_cache"] = {"ClassicAPI": {
            "timestamp": time.time() - age,
            "release": {"tag_name": tag, "assets": []}}}

    def version(self, **kw):
        return self.app.fetch_mod_latest_version_cached(self.mod, **kw)

    def test_launch_looks_again_after_five_minutes(self):
        self.cache("v1.15.16", age=10 * 60)
        # A tab switch within the hour still uses the cache ...
        self.assertEqual(self.version(), "v1.15.16")
        self.assertEqual(self.calls, [])
        # ... a launch does not.
        self.assertEqual(self.version(
            max_age=self.app._MOD_VERSION_STARTUP_MAX_AGE), "v1.16.0")
        self.assertEqual(self.calls, ["ClassicAPI"])
        self.assertEqual(self.cfg["mod_release_cache"]["ClassicAPI"]
                         ["release"]["tag_name"], "v1.16.0")

    def test_a_burst_of_launches_is_answered_from_the_cache(self):
        self.cache("v1.16.0", age=60)
        self.assertEqual(self.version(
            max_age=self.app._MOD_VERSION_STARTUP_MAX_AGE), "v1.16.0")
        self.assertEqual(self.calls, [])

    def test_a_failed_check_is_not_the_old_release(self):
        self.cache("v1.15.16", age=10 * 60)
        self.answer = None                              # GitHub did not answer
        self.assertIsNone(self.version(
            max_age=self.app._MOD_VERSION_STARTUP_MAX_AGE))
        # The row then says it could not check, rather than "up to date".
        comp = self.app.mod_component(
            self.mod, {"enabled": True, "installed_version": "v1.15.16",
                       "managed": True}, None, "")
        self.assertTrue(comp.lookup_failed)

    def test_the_launch_check_asks_for_fresh_releases(self):
        seen = []
        fake = mock.Mock()
        with mock.patch.object(self.app, "fetch_mod_latest_version_cached",
                               lambda mod, **kw: seen.append(kw) or None):
            self.app.EqUpdaterApp._load_mods_state(
                fake, max_age=self.app._MOD_VERSION_STARTUP_MAX_AGE)
        self.assertTrue(seen)
        self.assertTrue(all(kw == {"max_age": 300} for kw in seen))


if __name__ == "__main__":
    unittest.main()
