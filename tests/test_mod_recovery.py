"""OctoLauncher-installed mods handed to EqUpdater, and every way back when
their version cannot be verified. Real files, no Tk, no network."""
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

from equpdater import app, octolauncher
from equpdater.planner import CAUSE_LOOKUP, CAUSE_UNORDERABLE
from equpdater.states import Action, Status


def registry_mod(mod_id):
    return copy.deepcopy(next(m for m in app.MODS_REGISTRY if m["id"] == mod_id))


def write_files(client, files, content=b"octolauncher build"):
    for rel in files:
        path = os.path.join(client, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)


def adopted(client, files, version=None):
    """The record the affected user's 2.1.3 config holds."""
    return {"managed": True, "adopted": True, "installed_by": None,
            "enabled": True, "ignore_updates": False,
            "installed_version": version, "installed_files": list(files),
            "content_hash": app.files_hash(client, files),
            "hash_baseline": "adoption", "error": None, "discovered": True}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = tmp.name
        app.MOD_LOOKUPS.clear()
        self.addCleanup(app.MOD_LOOKUPS.clear)


class TestStatesAreSeparate(Base):
    """Files present, managed, version known, latest resolved, update proven:
    five facts, and none of them stands in for another."""

    def setUp(self):
        super().setUp()
        self.mod = registry_mod("VanillaFixes")
        self.files = self.mod["installed_files"]
        write_files(self.client, self.files)
        self.state = adopted(self.client, self.files)

    def test_known_release_with_unknown_local_version(self):
        p = app.mod_plan(self.mod, self.state, {"latest_version": "v1.5.3"},
                         self.client)
        self.assertIs(p.status, Status.VERSION_UNKNOWN)
        self.assertIs(p.action, Action.SKIP_VERSION_UNKNOWN)
        self.assertFalse(p.will_update)
        self.assertIsNone(p.local, "the latest must not read as installed")
        self.assertEqual(p.remote, "v1.5.3")

    def test_failed_lookup_is_not_an_unknown_version(self):
        p = app.mod_plan(self.mod, self.state, None, self.client)
        self.assertIs(p.status, Status.UNVERIFIABLE)
        self.assertEqual(p.detail["cause"], CAUSE_LOOKUP)
        self.assertFalse(p.will_update)

    def test_unorderable_versions_are_neither(self):
        state = adopted(self.client, self.files, version="custom-build")
        p = app.mod_plan(self.mod, state, {"latest_version": "v1.5.3"},
                         self.client)
        self.assertIs(p.status, Status.UNVERIFIABLE)
        self.assertEqual(p.detail["cause"], CAUSE_UNORDERABLE)
        self.assertFalse(p.will_update)

    def test_pinned_download_offers_its_pinned_version(self):
        mod = registry_mod("transmogfix")
        write_files(self.client, mod["installed_files"])
        state = adopted(self.client, mod["installed_files"])
        p = app.mod_plan(mod, state, None, self.client)
        self.assertIs(p.status, Status.VERSION_UNKNOWN)
        self.assertEqual(p.remote, mod["source"]["pinned_version"])

    def test_unknown_version_keeps_the_mod_in_the_model(self):
        self.assertTrue(app.mod_has_files(self.state))
        text = app.EqUpdaterApp._mod_version_text(
            None, self.state, {"latest_version": "v1.5.3"})
        self.assertEqual(text, app.tr("unknown"))

    def test_latest_is_shown_only_for_a_mod_not_installed(self):
        text = app.EqUpdaterApp._mod_version_text(
            None, {}, {"latest_version": "v1.5.3"})
        self.assertEqual(text, "v1.5.3")


class TestLookup(Base):
    def setUp(self):
        super().setUp()
        self.mod = registry_mod("VanillaHelpers")
        self.cfg = {}
        patches = [mock.patch.object(app, "load_config",
                                     side_effect=lambda: self.cfg),
                   mock.patch.object(app, "update_config",
                                     side_effect=lambda fn: fn(self.cfg))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    RELEASE = {"tag_name": "v1.2", "assets": [
        {"name": "VanillaHelpers.dll", "size": 3,
         "browser_download_url": "https://github.com/x", "digest": "sha256:ab"}]}

    def test_failure_says_why_is_not_cached_and_retry_recovers(self):
        with mock.patch.object(app, "_release_latest",
                               side_effect=RuntimeError("HTTP 500 from github")):
            self.assertIsNone(app._fetch_release_cached(self.mod))
        look = app.MOD_LOOKUPS[self.mod["id"]]
        self.assertEqual(look["outcome"], "failed")
        self.assertIn("HTTP 500", look["reason"])
        self.assertNotIn("mod_release_cache", self.cfg)

        with mock.patch.object(app, "_release_latest",
                               return_value=self.RELEASE):
            rel = app._fetch_release_cached(self.mod)
        self.assertEqual(rel["tag_name"], "v1.2")
        self.assertEqual(app.MOD_LOOKUPS[self.mod["id"]]["outcome"], "fetched")

    def test_cache_answers_then_force_goes_past_it(self):
        with mock.patch.object(app, "_release_latest",
                               return_value=self.RELEASE) as latest:
            app._fetch_release_cached(self.mod)
            app._fetch_release_cached(self.mod)
            self.assertEqual(latest.call_count, 1)
            self.assertEqual(app.MOD_LOOKUPS[self.mod["id"]]["outcome"], "cached")
            app._fetch_release_cached(self.mod, force=True)
            self.assertEqual(latest.call_count, 2)

    def test_failed_force_does_not_serve_the_stale_entry(self):
        with mock.patch.object(app, "_release_latest",
                               return_value=self.RELEASE):
            app._fetch_release_cached(self.mod)
        with mock.patch.object(app, "_release_latest",
                               side_effect=RuntimeError("down")):
            self.assertIsNone(app._fetch_release_cached(self.mod, force=True))

    def test_release_without_the_mods_file_is_not_an_answer(self):
        renamed = {"tag_name": "v2", "assets": [
            {"name": "Renamed.dll", "size": 1, "browser_download_url": "x"}]}
        with mock.patch.object(app, "_release_latest", return_value=renamed):
            self.assertIsNone(app._fetch_release_cached(self.mod))
        look = app.MOD_LOOKUPS[self.mod["id"]]
        self.assertEqual(look["outcome"], "no-asset")
        self.assertIn("v2", look["reason"])

    def test_diagnostic_line_names_mod_source_and_cause(self):
        with mock.patch.object(app, "_release_latest",
                               side_effect=RuntimeError("HTTP 500")):
            app._fetch_release_cached(self.mod)
        line = app.mod_lookup_line(self.mod, {"installed_version": None})
        for part in ("VanillaHelpers", "isfir/VanillaHelpers", "failed",
                     "HTTP 500", "installed version unknown"):
            self.assertIn(part, line)


class TestIdentification(Base):
    def digest_release(self, data, tag="v1"):
        return {"tag_name": tag, "assets": [{
            "name": "nampower.dll", "size": len(data),
            "browser_download_url": "x",
            "digest": "sha256:" + hashlib.sha256(data).hexdigest()}]}

    def test_exact_digest_identifies_without_a_download(self):
        mod = registry_mod("nampower")
        write_files(self.client, ["nampower.dll"], b"the release")
        with mock.patch.object(app, "install_mod") as install:
            self.assertTrue(app.identify_installed_release(
                mod, ["nampower.dll"], self.client,
                self.digest_release(b"the release")))
            self.assertFalse(app.identify_installed_release(
                mod, ["nampower.dll"], self.client,
                self.digest_release(b"another build")))
        install.assert_not_called()

    def test_archive_is_compared_in_a_scratch_folder(self):
        mod = registry_mod("VanillaFixes")
        files = mod["installed_files"]
        write_files(self.client, files, b"exact")

        def install(m, target, release=None):
            write_files(target, files, release["body"])
            return list(files)
        with mock.patch.object(app, "install_mod", side_effect=install), \
                mock.patch.object(app, "APP_DATA_DIR", self.client + "-data"), \
                mock.patch.object(app, "log"):
            rel = {"tag_name": "v1", "body": b"exact", "assets": [
                {"name": "vanillafixes-1.zip", "size": 1,
                 "browser_download_url": "x"}]}
            self.assertTrue(app.identify_installed_release(
                mod, files, self.client, rel))
            rel["body"] = b"different"
            self.assertFalse(app.identify_installed_release(
                mod, files, self.client, rel))
        with open(os.path.join(self.client, files[0]), "rb") as f:
            self.assertEqual(f.read(), b"exact", "the game folder is not touched")

    def test_baseline_hash_alone_identifies_nothing(self):
        mod = registry_mod("nampower")
        write_files(self.client, ["nampower.dll"])
        state = adopted(self.client, ["nampower.dll"])
        p = app.mod_plan(mod, state, {"latest_version": "v4.6.3"}, self.client)
        self.assertIs(p.status, Status.VERSION_UNKNOWN)

    def run_identify(self, cfg, release, hints=None, by_tag=None):
        def update(fn):
            fn(cfg)
            return cfg
        worker = SimpleNamespace()
        with mock.patch.object(app, "load_config", side_effect=lambda: cfg), \
                mock.patch.object(app, "update_config", side_effect=update), \
                mock.patch.object(app, "log"), \
                mock.patch.object(app, "_fetch_release_cached",
                                  return_value=release), \
                mock.patch.object(app, "release_by_tag",
                                  return_value=by_tag) as tag_lookup, \
                mock.patch.object(app.octolauncher, "version_hints",
                                  return_value=hints or {}), \
                mock.patch.object(app, "MODS_REGISTRY",
                                  [registry_mod("nampower")]):
            names = app.EqUpdaterApp._identify_unversioned_mods(
                worker, self.client)
        return names, tag_lookup

    def test_hint_that_the_files_contradict_is_not_recorded(self):
        """The real case: OctoLauncher said v4.6.2, the file was v4.6.3."""
        write_files(self.client, ["nampower.dll"], b"v463 bytes")
        cfg = {"mods": {"nampower": adopted(self.client, ["nampower.dll"])}}
        names, _ = self.run_identify(
            cfg, self.digest_release(b"v463 bytes", "v4.6.3"),
            hints={"nampower": "v4.6.2"})
        self.assertEqual(names, ["Nampower"])
        rec = cfg["mods"]["nampower"]
        self.assertEqual(rec["installed_version"], "v4.6.3")
        self.assertEqual(rec["hash_baseline"], "release")

    def test_older_release_is_found_through_the_hint(self):
        write_files(self.client, ["nampower.dll"], b"v462 bytes")
        cfg = {"mods": {"nampower": adopted(self.client, ["nampower.dll"])}}
        names, _ = self.run_identify(
            cfg, self.digest_release(b"v463 bytes", "v4.6.3"),
            hints={"nampower": "v4.6.2"},
            by_tag=self.digest_release(b"v462 bytes", "v4.6.2"))
        self.assertEqual(cfg["mods"]["nampower"]["installed_version"], "v4.6.2")

    def test_no_match_leaves_unknown_and_is_not_asked_twice(self):
        write_files(self.client, ["nampower.dll"], b"custom build")
        cfg = {"mods": {"nampower": adopted(self.client, ["nampower.dll"])}}
        rel = self.digest_release(b"v463 bytes", "v4.6.3")
        hint = self.digest_release(b"v462 bytes", "v4.6.2")
        names, tags = self.run_identify(cfg, rel, {"nampower": "v4.6.2"}, hint)
        self.assertEqual(names, [])
        self.assertIsNone(cfg["mods"]["nampower"]["installed_version"])
        self.assertEqual(tags.call_count, 1)
        _, tags = self.run_identify(cfg, rel, {"nampower": "v4.6.2"}, hint)
        self.assertEqual(tags.call_count, 0, "the answer is remembered")


class TestOctoLauncherHints(Base):
    def settings(self, **over):
        st = {"clientDir": self.client, "activeClientDir": self.client, "mods": {
            "nampower": {"installedVersion": "v4.6.2",
                         "installedFiles": ["nampower.dll"]},
            "dxvk": {"installedVersion": "v2.7.1-1",
                     "installedFiles": ["d3d9.dll"]},
            "vanillaFixes": {"installedFiles": []},
        }}
        st.update(over)
        return st

    def test_reads_versions_for_this_folder(self):
        hints = octolauncher.version_hints(self.client, app.MODS_REGISTRY,
                                           self.settings())
        self.assertEqual(hints, {"nampower": "v4.6.2"})

    def test_another_game_folder_says_nothing(self):
        st = self.settings(clientDir=r"C:\elsewhere", activeClientDir=None)
        self.assertEqual(octolauncher.version_hints(
            self.client, app.MODS_REGISTRY, st), {})

    def test_a_different_fork_is_a_different_mod(self):
        hints = octolauncher.version_hints(self.client, app.MODS_REGISTRY,
                                           self.settings())
        self.assertNotIn("dxvk", hints)

    def test_only_the_named_fields_are_kept(self):
        path = os.path.join(self.client, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mods": {}, "clientDir": "x", "secret": "s"}, f)
        self.assertEqual(set(octolauncher.read_settings(path)),
                         {"mods", "clientDir", "activeClientDir"})

    def test_unreadable_settings_are_no_hints(self):
        self.assertIsNone(octolauncher.read_settings(
            os.path.join(self.client, "missing.json")))
        self.assertEqual(octolauncher.version_hints(
            self.client, app.MODS_REGISTRY,
            path=os.path.join(self.client, "missing.json")), {})


class TestApply(Base):
    """The worker, against real files."""

    def apply(self, mod, state, enabled=True, force=False, targeted=False,
              release=None, install=None):
        cfg = {"mods": {mod["id"]: state} if state is not None else {}}

        def update(fn):
            fn(cfg)
            return cfg
        worker = SimpleNamespace(
            _mod_pending_state={mod["id"]: {"enabled": enabled}},
            after=lambda *a: None)
        install = install or (lambda m, c, release=None:
                              write_files(c, m["installed_files"], b"replaced")
                              or list(m["installed_files"]))
        with mock.patch.object(app, "MODS_REGISTRY", [mod]), \
                mock.patch.object(app, "load_config",
                                  side_effect=lambda: copy.deepcopy(cfg)), \
                mock.patch.object(app, "update_config", side_effect=update), \
                mock.patch.object(app, "_fetch_release_cached",
                                  return_value=release) as self.lookup, \
                mock.patch.object(app, "fetch_mod_latest_version_cached",
                                  return_value=mod["source"].get("pinned_version")), \
                mock.patch.object(app, "install_mod",
                                  side_effect=install) as self.install, \
                mock.patch.object(app, "APP_DATA_DIR", self.client + "-data"), \
                mock.patch.object(app, "add_dll"), \
                mock.patch.object(app, "remove_dll"), \
                mock.patch.object(app, "ensure_dll", return_value=False), \
                mock.patch.object(app, "log"), \
                mock.patch.object(app, "reconcile_dlls_txt", return_value=[]):
            app.EqUpdaterApp._apply_mods_worker(
                worker, self.client, mod["id"] if targeted else None, force)
        return cfg["mods"].get(mod["id"])

    def read(self, rel):
        try:
            with open(os.path.join(self.client, rel), "rb") as f:
                return f.read()
        except OSError:
            return None

    RELEASE = {"tag_name": "v1.5.3", "assets": [
        {"name": "vanillafixes-1.5.3.zip", "size": 9,
         "browser_download_url": "x"}]}

    def setUp(self):
        super().setUp()
        self.mod = registry_mod("VanillaFixes")
        self.files = self.mod["installed_files"]

    def test_apply_never_replaces_an_unidentified_build(self):
        write_files(self.client, self.files)
        state = adopted(self.client, self.files)
        rec = self.apply(self.mod, state, release=self.RELEASE)
        self.install.assert_not_called()
        self.assertEqual(self.read(self.files[0]), b"octolauncher build")
        self.assertIsNone(rec["installed_version"])
        self.assertTrue(rec["managed"])

    def test_uninstall_needs_no_version_and_no_network(self):
        write_files(self.client, self.files)
        rec = self.apply(self.mod, adopted(self.client, self.files), enabled=False)
        self.assertIsNone(self.read(self.files[0]))
        self.assertEqual(self.lookup.call_count, 0)
        self.assertFalse(rec["managed"])

    def test_uninstall_of_a_partly_missing_install(self):
        write_files(self.client, self.files[:1])
        state = adopted(self.client, self.files[:1])
        state["installed_files"] = list(self.files)
        self.apply(self.mod, state, enabled=False)
        self.assertIsNone(self.read(self.files[0]))

    def test_partly_missing_unidentified_install_waits_to_be_asked(self):
        write_files(self.client, self.files[:1])
        state = adopted(self.client, self.files[:1])
        state["installed_files"] = list(self.files)
        self.apply(self.mod, state, release=self.RELEASE)
        self.install.assert_not_called()
        self.assertEqual(self.read(self.files[0]), b"octolauncher build")

    def test_explicit_reinstall_of_a_partly_missing_install(self):
        write_files(self.client, self.files[:1])
        state = adopted(self.client, self.files[:1])
        state["installed_files"] = list(self.files)
        rec = self.apply(self.mod, state, force=True, targeted=True,
                         release=self.RELEASE)
        self.assertEqual(self.read(self.files[1]), b"replaced")
        self.assertEqual(rec["installed_version"], "v1.5.3")

    def test_explicit_reinstall_of_an_unidentified_build(self):
        write_files(self.client, self.files)
        rec = self.apply(self.mod, adopted(self.client, self.files), force=True,
                         targeted=True, release=self.RELEASE)
        self.assertEqual(self.read(self.files[0]), b"replaced")
        self.assertEqual(rec["installed_version"], "v1.5.3")
        self.assertEqual(rec["installed_by"], app.branding.APP_NAME)

    def test_failed_download_keeps_the_files_and_the_record(self):
        write_files(self.client, self.files)
        state = adopted(self.client, self.files)

        def fail(m, c, release=None):
            raise RuntimeError("HTTP 503 from github.com")
        rec = self.apply(self.mod, state, force=True, targeted=True,
                         release=self.RELEASE, install=fail)
        for rel in self.files:
            self.assertEqual(self.read(rel), b"octolauncher build")
        self.assertIn("503", rec["update_error"])
        self.assertTrue(rec["managed"])
        self.assertEqual(rec["installed_files"], list(self.files))
        self.assertFalse(rec.get("error"), "the mod works; PLAY is not held")

    def test_install_failing_part_way_puts_the_old_files_back(self):
        write_files(self.client, self.files)

        def half(m, c, release=None):
            write_files(c, m["installed_files"][:1], b"half written")
            raise OSError(28, "No space left on device")
        self.apply(self.mod, adopted(self.client, self.files), force=True,
                   targeted=True, release=self.RELEASE, install=half)
        for rel in self.files:
            self.assertEqual(self.read(rel), b"octolauncher build")

    def test_download_without_the_files_counts_as_failed(self):
        write_files(self.client, self.files)
        rec = self.apply(self.mod, adopted(self.client, self.files), force=True,
                         targeted=True, release=self.RELEASE,
                         install=lambda m, c, release=None: [])
        self.assertEqual(self.read(self.files[0]), b"octolauncher build")
        self.assertTrue(rec["update_error"])

    def test_failed_update_of_a_known_version_keeps_it(self):
        write_files(self.client, self.files)
        state = adopted(self.client, self.files, version="v1.5.2")
        state["installed_by"] = app.branding.APP_NAME

        def fail(m, c, release=None):
            raise RuntimeError("down")
        rec = self.apply(self.mod, state, release=self.RELEASE, install=fail)
        self.assertEqual(self.read(self.files[0]), b"octolauncher build")
        self.assertEqual(rec["installed_version"], "v1.5.2")

    def test_replace_with_no_answer_from_the_source_changes_nothing(self):
        write_files(self.client, self.files)
        rec = self.apply(self.mod, adopted(self.client, self.files), force=True,
                         targeted=True, release=None)
        self.install.assert_not_called()
        self.assertEqual(self.read(self.files[0]), b"octolauncher build")
        self.assertTrue(rec["update_error"])

    def test_unmanaged_files_stay_untouched(self):
        write_files(self.client, self.files)
        state = {"managed": False, "enabled": True, "discovered": True,
                 "installed_version": None, "installed_files": list(self.files),
                 "content_hash": app.files_hash(self.client, self.files)}
        for enabled in (True, False):
            self.apply(self.mod, dict(state), enabled=enabled,
                       release=self.RELEASE)
            self.install.assert_not_called()
            self.assertEqual(self.read(self.files[0]), b"octolauncher build")

    def test_downgrade_is_still_refused(self):
        write_files(self.client, self.files)
        state = adopted(self.client, self.files, version="v9.0")
        state["installed_by"] = app.branding.APP_NAME
        self.apply(self.mod, state, release=self.RELEASE)
        self.install.assert_not_called()

    def test_proven_update_still_runs(self):
        write_files(self.client, self.files)
        state = adopted(self.client, self.files, version="v1.0")
        state["installed_by"] = app.branding.APP_NAME
        rec = self.apply(self.mod, state, release=self.RELEASE)
        self.assertEqual(self.read(self.files[0]), b"replaced")
        self.assertEqual(rec["installed_version"], "v1.5.3")


class TestDigestCheck(unittest.TestCase):
    def test_mismatch_is_refused(self):
        asset = {"digest": "sha256:" + hashlib.sha256(b"good").hexdigest()}
        app._check_digest(asset, b"good")
        with self.assertRaises(RuntimeError):
            app._check_digest(asset, b"bad")

    def test_no_digest_is_no_check(self):
        app._check_digest({"digest": None}, b"anything")
        app._check_digest({}, b"anything")


if __name__ == "__main__":
    unittest.main()
