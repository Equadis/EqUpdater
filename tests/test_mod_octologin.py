"""OctoLogin in the Mods list: installed where its own installer puts it
(mods/OctoLogin.dll), registered in dlls.txt by that path, and never
registered twice because a hand-written entry used the other slash."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DLL = b"MZ fake OctoLogin"


def release(*names):
    return {"tag_name": "v1.1.0", "assets": [
        {"name": n, "size": 60928,
         "browser_download_url": f"https://github.com/fmustafayaman/OctoLogin/"
                                 f"releases/download/v1.1.0/{n}"}
        for n in names]}


class Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestOctoLogin(unittest.TestCase):

    def setUp(self):
        from equpdater import app       # other test files reload the package
        self.app = app
        self.mod = next(m for m in app.MODS_REGISTRY if m["id"] == "OctoLogin")
        self.client = tempfile.mkdtemp(prefix="equ-octologin-")

    def tearDown(self):
        shutil.rmtree(self.client, ignore_errors=True)

    def dlls(self):
        with open(os.path.join(self.client, "dlls.txt")) as f:
            return f.read().splitlines()

    def write_dlls(self, *lines):
        with open(os.path.join(self.client, "dlls.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")

    def test_the_entry(self):
        src = self.mod["source"]
        self.assertTrue(self.mod["essential"])          # recommended: the star
        self.assertEqual(src["kind"], "github_release")
        self.assertEqual((src["owner"], src["repo"]),
                         ("fmustafayaman", "OctoLogin"))
        self.assertEqual(self.mod["repo_url"],
                         "https://github.com/fmustafayaman/OctoLogin")
        self.assertEqual(self.mod["register_dll"], "mods/OctoLogin.dll")
        self.assertEqual(self.mod["installed_files"], ["mods/OctoLogin.dll"])

    def test_installs_the_dll_into_mods(self):
        urls = []

        def fetch(req, **kw):
            urls.append(req.full_url)
            return Resp(DLL)
        with mock.patch.object(self.app, "secure_urlopen", fetch):
            written = self.app.install_mod(
                self.mod, self.client,
                release("OctoLogin-v1.1.0.zip", "OctoLogin.dll"))
        self.assertEqual(written, ["mods/OctoLogin.dll"])
        self.assertEqual(urls, ["https://github.com/fmustafayaman/OctoLogin/"
                                "releases/download/v1.1.0/OctoLogin.dll"])
        with open(os.path.join(self.client, "mods", "OctoLogin.dll"), "rb") as f:
            self.assertEqual(f.read(), DLL)
        self.assertFalse(os.path.exists(os.path.join(self.client, "OctoLogin.dll")))

    def test_a_mod_without_dest_still_lands_under_the_asset_name(self):
        helpers = next(m for m in self.app.MODS_REGISTRY
                       if m["id"] == "VanillaHelpers")
        with mock.patch.object(self.app, "secure_urlopen",
                               lambda req, **kw: Resp(DLL)):
            written = self.app.install_mod(
                helpers, self.client, {"tag_name": "v1", "assets": [
                    {"name": "VanillaHelpers.dll", "size": 1,
                     "browser_download_url": "https://github.com/x"}]})
        self.assertEqual(written, ["VanillaHelpers.dll"])
        self.assertTrue(os.path.exists(
            os.path.join(self.client, "VanillaHelpers.dll")))

    def test_registered_last_of_the_known_mods(self):
        self.write_dlls("my-own.dll", "UnitXP_SP3.dll", "VanillaHelpers.dll")
        self.app.add_dll(self.client, "mods/OctoLogin.dll")
        self.assertEqual(self.dlls(), ["VanillaHelpers.dll", "UnitXP_SP3.dll",
                                       "mods/OctoLogin.dll", "my-own.dll"])

    def test_either_slash_is_the_same_entry(self):
        self.write_dlls("SuperWoWhook.dll", "mods\\OctoLogin.dll")
        self.assertFalse(self.app.ensure_dll(self.client, "mods/OctoLogin.dll"))
        self.app.add_dll(self.client, "mods/OctoLogin.dll")
        self.assertEqual(self.dlls(), ["SuperWoWhook.dll", "mods\\OctoLogin.dll"])
        self.app.remove_dll(self.client, "mods/OctoLogin.dll")
        self.assertEqual(self.dlls(), ["SuperWoWhook.dll"])

    def test_the_sweep_puts_it_back(self):
        """Something else rewrote dlls.txt without it (the official
        launcher does): the next Apply or PLAY registers it again."""
        os.makedirs(os.path.join(self.client, "mods"))
        with open(os.path.join(self.client, "mods", "OctoLogin.dll"), "wb") as f:
            f.write(DLL)
        self.write_dlls("SuperWoWhook.dll")
        repaired = self.app.reconcile_dlls_txt(self.client, {"OctoLogin": {
            "enabled": True, "installed_version": "v1.1.0",
            "installed_files": ["mods/OctoLogin.dll"]}})
        self.assertEqual(repaired, ["mods/OctoLogin.dll"])
        self.assertEqual(self.dlls(), ["SuperWoWhook.dll", "mods/OctoLogin.dll"])


if __name__ == "__main__":
    unittest.main()
