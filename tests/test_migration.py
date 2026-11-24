"""Moving from OctoLauncher: mods and addons already there can become managed.

Real files in a temporary game folder, the real clone format on disk, and no
network or Tk display."""
import copy
import hashlib
import os
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from equpdater import app, gitlocal
from equpdater.states import Status

SHA = "a" * 40
UPSTREAM = "https://github.com/shagu/ShaguDPS"


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def write_index(git_dir: str, entries: dict, version: int = 2):
    """A .git/index, as isomorphic-git writes it."""
    out = b"DIRC" + struct.pack(">II", version, len(entries))
    for rel, content in sorted(entries.items()):
        name = rel.encode()
        entry = (b"\0" * 40 + bytes.fromhex(blob_sha(content))
                 + struct.pack(">H", min(len(name), 0xfff)) + name)
        entry += b"\0" * (8 - len(entry) % 8)
        out += entry
    with open(os.path.join(git_dir, "index"), "wb") as f:
        f.write(out)


def make_clone(folder: str, files: dict, remote: str = UPSTREAM + ".git",
               head: str = "ref: refs/heads/main\n", packed: bool = False,
               with_login: bool = False):
    """An addon folder as OctoLauncher's clone leaves it."""
    git_dir = os.path.join(folder, ".git")
    os.makedirs(os.path.join(git_dir, "refs", "heads"))
    if with_login:
        remote = remote.replace("https://", "https://me:secret-token@")
    with open(os.path.join(git_dir, "config"), "w") as f:
        f.write('[core]\n\tbare = false\n[remote "origin"]\n'
                f'\turl = {remote}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    with open(os.path.join(git_dir, "HEAD"), "w") as f:
        f.write(head)
    if packed:
        with open(os.path.join(git_dir, "packed-refs"), "w") as f:
            f.write(f"# pack-refs with: peeled\n{SHA} refs/heads/main\n")
    else:
        with open(os.path.join(git_dir, "refs", "heads", "main"), "w") as f:
            f.write(SHA + "\n")
    write_index(git_dir, files)
    for rel, content in files.items():
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = tmp.name
        self.cfg = {"mods": {}, "addons": {}}

        def update(fn):
            fn(self.cfg)
            return self.cfg
        for name, patch in (
                ("load_config", mock.patch.object(
                    app, "load_config",
                    side_effect=lambda *a: copy.deepcopy(self.cfg))),
                ("update_config", mock.patch.object(
                    app, "update_config", side_effect=update)),
                ("log", mock.patch.object(app, "log"))):
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)

    def put(self, *rels, content=b"their build"):
        for rel in rels:
            path = os.path.join(self.client, rel)
            os.makedirs(os.path.dirname(path) or self.client, exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)

    def mod(self, mod_id):
        return next(m for m in app.MODS_REGISTRY if m["id"] == mod_id)

    def host(self, **extra):
        """Enough of the app for the methods under test, none of Tk."""
        ns = SimpleNamespace(
            _game_path=SimpleNamespace(get=lambda: self.client),
            _mod_pending_state={}, _mods_state=[],
            _addons_status={"state": "done", "addons": {}, "available": []},
            _running=False, _settings_overlay=None,
            after=lambda *a: None,
            _log_line=lambda *a: None, _render_mod_rows=lambda: None,
            _refresh_mods_badge=lambda: None, _refresh_manage_all=lambda: None,
            _addons_verify=lambda **k: None)
        cls = app.EqUpdaterApp
        for name in ("_found_mods", "_found_addons", "_record_mod_adoption",
                     "_record_addon_adoption", "_identify_adopted_addons",
                     "_manage_all_found", "_offer_manage_found"):
            setattr(ns, name, getattr(cls, name).__get__(ns))
        ns.__dict__.update(extra)
        return ns


class TestReadClone(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_a_clone_names_its_source_commit_and_branch(self):
        make_clone(self.dir, {"a.lua": b"x"})
        c = gitlocal.read_clone(self.dir)
        self.assertEqual((c.remote, c.sha, c.branch),
                         (UPSTREAM + ".git", SHA, "main"))

    def test_a_packed_ref_is_found(self):
        make_clone(self.dir, {"a.lua": b"x"}, packed=True)
        self.assertEqual(gitlocal.read_clone(self.dir).sha, SHA)

    def test_a_pinned_clone_has_a_commit_and_no_branch(self):
        make_clone(self.dir, {"a.lua": b"x"}, head=SHA + "\n")
        c = gitlocal.read_clone(self.dir)
        self.assertEqual((c.sha, c.branch), (SHA, None))

    def test_a_login_in_the_remote_never_comes_out(self):
        make_clone(self.dir, {"a.lua": b"x"}, with_login=True)
        remote = gitlocal.read_clone(self.dir).remote
        self.assertNotIn("secret-token", remote)
        self.assertEqual(remote, UPSTREAM + ".git")

    def test_a_plain_folder_is_not_a_clone(self):
        self.assertIsNone(gitlocal.read_clone(self.dir))

    def test_files_matching_the_index_are_clean(self):
        make_clone(self.dir, {"a.lua": b"x\n", "sub/b.toc": b"y\n"})
        self.assertTrue(gitlocal.clone_is_clean(self.dir))

    def test_an_edited_file_is_not_clean(self):
        make_clone(self.dir, {"a.lua": b"x\n"})
        with open(os.path.join(self.dir, "a.lua"), "wb") as f:
            f.write(b"my edit\n")
        self.assertFalse(gitlocal.clone_is_clean(self.dir))

    def test_a_deleted_file_is_not_clean(self):
        make_clone(self.dir, {"a.lua": b"x\n", "b.lua": b"y\n"})
        os.remove(os.path.join(self.dir, "b.lua"))
        self.assertFalse(gitlocal.clone_is_clean(self.dir))

    def test_crlf_line_endings_are_the_same_content(self):
        make_clone(self.dir, {"a.lua": b"x\ny\n"})
        with open(os.path.join(self.dir, "a.lua"), "wb") as f:
            f.write(b"x\r\ny\r\n")
        self.assertTrue(gitlocal.clone_is_clean(self.dir))

    def test_an_untracked_file_does_not_count(self):
        make_clone(self.dir, {"a.lua": b"x\n"})
        with open(os.path.join(self.dir, "mine.txt"), "wb") as f:
            f.write(b"notes")
        self.assertTrue(gitlocal.clone_is_clean(self.dir))

    def test_an_index_it_cannot_read_proves_nothing(self):
        make_clone(self.dir, {"a.lua": b"x\n"})
        write_index(os.path.join(self.dir, ".git"), {"a.lua": b"x\n"}, version=4)
        self.assertFalse(gitlocal.clone_is_clean(self.dir))

    def test_a_real_git_clone_reads_the_same(self):
        import shutil
        import subprocess
        if not shutil.which("git"):
            self.skipTest("no git here")
        run = lambda *a: subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c",
             "core.autocrlf=false", *a], cwd=self.dir, check=True,
            capture_output=True)
        run("init", "-q", "-b", "main")
        with open(os.path.join(self.dir, "a.lua"), "wb") as f:
            f.write(b"x\n")
        run("add", ".")
        run("commit", "-qm", "c")
        run("remote", "add", "origin", UPSTREAM)
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.dir,
                             capture_output=True, text=True).stdout.strip()
        c = gitlocal.read_clone(self.dir)
        self.assertEqual((c.remote, c.sha, c.branch), (UPSTREAM, sha, "main"))
        self.assertTrue(gitlocal.clone_is_clean(self.dir))


class TestModsFoundOnDisk(Base):
    def test_a_mod_with_no_record_reads_not_managed_not_disabled(self):
        """Discovery runs only on a first start. A config that already had a
        mod left the rest with no record, reading "disabled" with nothing to
        click."""
        self.put("nampower.dll")
        plan = app.mod_plan(self.mod("nampower"), {}, None, self.client)
        self.assertIs(plan.status, Status.UNMANAGED)

    def test_a_mod_nothing_is_here_for_is_not_installed(self):
        plan = app.mod_plan(self.mod("nampower"), {}, None, self.client)
        self.assertIs(plan.status, Status.NOT_INSTALLED)

    def test_a_removed_mod_stays_removed(self):
        rec = app.new_mod_record(managed=False, enabled=False)
        plan = app.mod_plan(self.mod("nampower"), rec, None, self.client)
        self.assertIs(plan.status, Status.NOT_INSTALLED)

    def test_the_dll_alone_is_the_multi_monitor_fix(self):
        """OctoLauncher installs only the DLL. Asking for all three files
        made it read as not installed."""
        self.put("VanillaMultiMonitorFix.dll")
        mod = self.mod("VanillaMultiMonitorFix")
        self.assertTrue(app.mod_files_on_disk(mod, self.client))
        self.assertIs(app.mod_plan(mod, {}, None, self.client).status,
                      Status.UNMANAGED)

    def test_discovery_records_only_the_files_that_are_there(self):
        self.put("VanillaMultiMonitorFix.dll")
        host = SimpleNamespace(_cfg={})
        app.EqUpdaterApp._discover_existing_mods(host, self.client)
        rec = self.cfg["mods"]["VanillaMultiMonitorFix"]
        self.assertEqual(rec["installed_files"], ["VanillaMultiMonitorFix.dll"])
        self.assertFalse(rec["managed"])

    def test_ticking_a_found_mod_does_not_write_over_it(self):
        """A tick is not consent to replace. It was, for this mod."""
        self.put("VanillaMultiMonitorFix.dll")
        mod = copy.deepcopy(self.mod("VanillaMultiMonitorFix"))
        host = SimpleNamespace(
            _mod_pending_state={mod["id"]: {"enabled": True}},
            after=lambda *a: None)
        with mock.patch.object(app, "MODS_REGISTRY", [mod]), \
                mock.patch.object(app, "_fetch_release_cached", return_value=None), \
                mock.patch.object(app, "install_mod") as install, \
                mock.patch.object(app, "reconcile_dlls_txt", return_value=[]):
            app.EqUpdaterApp._apply_mods_worker(host, self.client)
        install.assert_not_called()
        with open(os.path.join(self.client, "VanillaMultiMonitorFix.dll"),
                  "rb") as f:
            self.assertEqual(f.read(), b"their build")

    def test_adopting_tracks_the_files_found_and_changes_none(self):
        self.put("VanillaMultiMonitorFix.dll")
        host = self.host(_cfg={})
        host._record_mod_adoption(self.mod("VanillaMultiMonitorFix"))
        rec = self.cfg["mods"]["VanillaMultiMonitorFix"]
        self.assertTrue(rec["managed"] and rec["adopted"])
        self.assertIsNone(rec["installed_version"])
        self.assertEqual(rec["installed_files"], ["VanillaMultiMonitorFix.dll"])
        plan = app.mod_plan(self.mod("VanillaMultiMonitorFix"), rec,
                            {"latest_version": "0.2"}, self.client)
        self.assertIs(plan.status, Status.VERSION_UNKNOWN)

    def test_identifying_it_looks_at_the_dll_not_the_players_settings(self):
        """The text file is the player's to edit, and OctoLauncher never
        installed the exe."""
        self.put("VanillaMultiMonitorFix.dll", content=b"release dll")
        self.put("VMMFix_preferred_monitor.txt", content=b"2")
        mod = self.mod("VanillaMultiMonitorFix")

        def fake_install(_m, scratch, release=None):
            out = {"VanillaMultiMonitorFix.dll": b"release dll",
                   "VMMFix_preferred_monitor.txt": b"0",
                   "ShowAllDisplayDevices.exe": b"tool"}
            for rel, content in out.items():
                with open(os.path.join(scratch, rel), "wb") as f:
                    f.write(content)
            return list(out)
        rel = {"tag_name": "0.2", "assets": [
            {"name": "release.zip", "size": 1, "browser_download_url": "x"}]}
        with mock.patch.object(app, "install_mod", side_effect=fake_install):
            self.assertTrue(app.identify_installed_release(
                mod, ["VanillaMultiMonitorFix.dll",
                      "VMMFix_preferred_monitor.txt"], self.client, rel))
        self.put("VanillaMultiMonitorFix.dll", content=b"another build")
        with mock.patch.object(app, "install_mod", side_effect=fake_install):
            self.assertFalse(app.identify_installed_release(
                mod, ["VanillaMultiMonitorFix.dll",
                      "VMMFix_preferred_monitor.txt"], self.client, rel))


class TestAddonsFound(Base):
    FOLDER = "ShaguDPS"

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.client, "Interface", "AddOns")
        self.dir = os.path.join(self.root, self.FOLDER)
        os.makedirs(self.dir)
        patch = mock.patch.object(app, "addons_path", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def clone(self, **kw):
        make_clone(self.dir, {"ShaguDPS.toc": b"## Title: x\n", "a.lua": b"y\n"},
                   **kw)

    def adopt(self, **rec):
        self.host()._record_addon_adoption(
            {"folder": self.FOLDER, "suggested_git": UPSTREAM, **rec})
        return self.cfg["addons"][self.FOLDER]

    def test_adopting_records_no_commit_until_one_is_proven(self):
        self.assertIsNone(self.adopt()["sha"])

    def test_a_clean_clone_gets_its_commit_without_a_download(self):
        self.clone()
        self.adopt()
        with mock.patch.object(app, "addon_remote_sha") as remote, \
                mock.patch.object(app, "install_addon_files") as install:
            self.host()._identify_adopted_addons(self.client)
        rec = self.cfg["addons"][self.FOLDER]
        self.assertEqual((rec["sha"], rec["branch"]), (SHA, "main"))
        remote.assert_not_called()
        install.assert_not_called()

    def test_an_already_adopted_addon_gets_its_commit_too(self):
        """Somebody who adopted with an older version has no commit recorded
        and should not need to do anything."""
        self.clone()
        self.cfg["addons"][self.FOLDER] = app.new_addon_record(
            managed=True, adopted=True, git=UPSTREAM, content_hash="old")
        self.host()._identify_adopted_addons(self.client)
        self.assertEqual(self.cfg["addons"][self.FOLDER]["sha"], SHA)

    def test_local_edits_keep_a_clone_from_claiming_its_commit(self):
        """Recording it would let the next update overwrite the edits."""
        self.clone()
        with open(os.path.join(self.dir, "a.lua"), "wb") as f:
            f.write(b"my edit\n")
        self.adopt()
        host = self.host(_addon_is_commit=lambda *a: False)
        with mock.patch.object(app, "addon_remote_sha", return_value="b" * 40):
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
        self.assertIsNone(self.cfg["addons"][self.FOLDER]["sha"])

    def test_a_clone_of_another_repository_says_nothing_about_this_one(self):
        self.clone(remote="https://github.com/someone/else.git")
        self.adopt()
        host = self.host(_addon_is_commit=lambda *a: False)
        with mock.patch.object(app, "addon_remote_sha", return_value="b" * 40):
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
        self.assertIsNone(self.cfg["addons"][self.FOLDER]["sha"])

    def test_a_folder_matching_the_latest_commit_exactly_gets_it(self):
        self.adopt()
        host = self.host(_addon_is_commit=lambda *a: True)
        with mock.patch.object(app, "addon_remote_sha", return_value="b" * 40):
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
        self.assertEqual(self.cfg["addons"][self.FOLDER]["sha"], "b" * 40)

    def test_a_mismatch_is_not_downloaded_again_for_the_same_commit(self):
        self.adopt()
        compare = mock.Mock(return_value=False)
        host = self.host(_addon_is_commit=compare)
        with mock.patch.object(app, "addon_remote_sha", return_value="b" * 40):
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
        self.assertEqual(compare.call_count, 1)

    def test_no_answer_from_the_source_records_nothing_and_asks_again(self):
        self.adopt()
        compare = mock.Mock(return_value=None)
        host = self.host(_addon_is_commit=compare)
        with mock.patch.object(app, "addon_remote_sha", return_value="b" * 40):
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
            app.EqUpdaterApp._identify_adopted_addons(host, self.client)
        self.assertEqual(compare.call_count, 2)
        self.assertIsNone(self.cfg["addons"][self.FOLDER]["sha"])

    def test_a_source_other_than_the_catalogues_is_kept(self):
        """It keeps updating from where it came from, not swapped for a fork
        and not nagged about."""
        rec = self.adopt(catalog_git="https://github.com/other/ShaguDPS")
        self.assertTrue(rec["custom_source"])
        self.assertEqual(rec["git"], UPSTREAM)

    def test_the_catalogues_source_keeps_its_pin(self):
        rec = self.adopt(catalog_git=UPSTREAM, catalog_ref="v1.0")
        self.assertFalse(rec["custom_source"])
        self.assertEqual(rec["ref"], "v1.0")


class TestManageAll(Base):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.client, "Interface", "AddOns")
        os.makedirs(self.root)
        patch = mock.patch.object(app, "addons_path", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.put("nampower.dll", "VanillaFixes.exe", "VfPatcher.dll")
        self.addon = {"folder": "Tmog", "status": "unmanaged",
                      "suggested_git": "https://github.com/Otari98/Tmog"}
        self.thread = mock.patch.object(app.threading, "Thread")
        self.thread.start()
        self.addCleanup(self.thread.stop)

    def run_host(self, addons=None):
        host = self.host(_cfg={})
        host._addons_status["addons"] = {
            r["folder"]: r for r in (addons if addons is not None
                                     else [self.addon])}
        return host

    def test_one_yes_manages_every_mod_and_addon_found(self):
        host = self.run_host()
        with mock.patch("tkinter.messagebox.askyesno", return_value=True) as ask:
            host._manage_all_found()
        self.assertEqual(ask.call_count, 1)
        self.assertTrue(self.cfg["mods"]["nampower"]["managed"])
        self.assertTrue(self.cfg["mods"]["VanillaFixes"]["managed"])
        self.assertTrue(self.cfg["addons"]["Tmog"]["managed"])

    def test_no_files_change(self):
        host = self.run_host()
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            host._manage_all_found()
        with open(os.path.join(self.client, "nampower.dll"), "rb") as f:
            self.assertEqual(f.read(), b"their build")
        self.assertEqual(os.listdir(self.root), [])

    def test_no_manages_nothing(self):
        host = self.run_host()
        with mock.patch("tkinter.messagebox.askyesno", return_value=False):
            host._manage_all_found()
        self.assertEqual(self.cfg["mods"], {})
        self.assertEqual(self.cfg["addons"], {})

    def test_an_addon_with_no_known_source_is_left_out(self):
        host = self.run_host([{"folder": "Mine", "status": "unmanaged"}])
        self.assertEqual(host._found_addons(), [])

    def test_an_addon_released_on_purpose_is_left_out(self):
        self.cfg["addons"]["Tmog"] = {"managed": False, "released": True}
        self.assertEqual(self.run_host()._found_addons(), [])

    def test_a_managed_mod_is_not_asked_about_again(self):
        self.cfg["mods"]["nampower"] = app.new_mod_record(
            managed=True, installed_files=["nampower.dll"],
            installed_version="v1")
        names = [m["id"] for m in self.run_host()._found_mods()]
        self.assertNotIn("nampower", names)
        self.assertIn("VanillaFixes", names)

    def test_the_offer_comes_once_per_game_folder(self):
        host = self.run_host()
        with mock.patch.dict(os.environ, {"EQUPDATER_NO_MIGRATION_PROMPT": ""}), \
                mock.patch("tkinter.messagebox.askyesno",
                           return_value=False) as ask:
            host._offer_manage_found()
            host._offer_manage_found()
        self.assertEqual(ask.call_count, 1)

    def test_nothing_to_manage_asks_nothing(self):
        for rel in ("nampower.dll", "VanillaFixes.exe", "VfPatcher.dll"):
            os.remove(os.path.join(self.client, rel))
        host = self.run_host([])
        with mock.patch.dict(os.environ, {"EQUPDATER_NO_MIGRATION_PROMPT": ""}), \
                mock.patch("tkinter.messagebox.askyesno") as ask:
            host._offer_manage_found()
        ask.assert_not_called()
        self.assertNotIn("found_offered_for", self.cfg)


if __name__ == "__main__":
    unittest.main()
