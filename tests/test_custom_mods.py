"""Custom mods: one .dll from a GitHub, Codeberg or OctoWoW Git release,
installed to mods/<file>.dll and registered in dlls.txt, kept in the config
and put into MODS_REGISTRY so the ordinary mod code installs, updates and
removes it."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DLL = b"MZ fake custom mod"


class Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def release(*names, tag="v2.0"):
    return {"tag_name": tag, "assets": [
        {"name": n, "size": 1024,
         "browser_download_url": f"https://github.com/someone/CoolMod/"
                                 f"releases/download/{tag}/{n}"}
        for n in names]}


class TestParseLink(unittest.TestCase):

    def setUp(self):
        from equpdater import app
        self.app = app

    def parse(self, url):
        return self.app.parse_mod_release_link(url)

    def test_repository_and_releases_page(self):
        for url in ("https://github.com/someone/CoolMod",
                    "https://github.com/someone/CoolMod/releases",
                    "https://github.com/someone/CoolMod/releases/latest"):
            self.assertEqual(self.parse(url),
                             {"kind": "github_release", "owner": "someone",
                              "repo": "CoolMod", "asset": None})

    def test_a_release_download_names_the_dll(self):
        src = self.parse("https://github.com/someone/CoolMod/releases/"
                         "download/v2.0/Cool%20Mod.dll")
        self.assertEqual(src["asset"], "Cool Mod.dll")

    def test_codeberg_and_octowow_git(self):
        self.assertEqual(self.parse("https://codeberg.org/a/b")["kind"],
                         "codeberg_release")
        src = self.parse("https://octowow.st/git/a/b")
        self.assertEqual((src["kind"], src["forge"]),
                         ("gitea_release", "octowow.st/git"))

    def test_refused(self):
        for url in ("", "http://github.com/a/b", "https://example.com/a/b",
                    "https://github.com/a/b/blob/main/x.dll",
                    "https://github.com/a/b/releases/download/v1/x.zip"):
            with self.assertRaises(ValueError, msg=url):
                self.parse(url)

    def test_release_dlls(self):
        self.assertEqual(self.app.release_dlls(
            release("b.dll", "notes.txt", "A.DLL", "src.zip")), ["A.DLL", "b.dll"])
        self.assertEqual(self.app.release_dlls(None), [])


class CustomModBase(unittest.TestCase):

    def setUp(self):
        from equpdater import app
        self.app = app
        self.cfg = {}
        load = lambda *a, **k: self.cfg

        def update(mutator, *a, **k):
            mutator(self.cfg)
            return self.cfg
        self.patches = [mock.patch.object(app, "load_config", load),
                        mock.patch.object(app, "update_config", update)]
        for p in self.patches:
            p.start()
        self.client = tempfile.mkdtemp(prefix="equ-custom-mod-")
        self.src = {"kind": "github_release", "owner": "someone",
                    "repo": "CoolMod", "asset": None}

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.app.load_custom_mods({})
        shutil.rmtree(self.client, ignore_errors=True)

    def dlls(self):
        with open(os.path.join(self.client, "dlls.txt")) as f:
            return f.read().splitlines()


class TestRegistry(CustomModBase):

    def test_added_mod_is_an_ordinary_registry_entry(self):
        mid = self.app.add_custom_mod(self.src, "CoolMod.dll")
        mod = next(m for m in self.app.MODS_REGISTRY if m["id"] == mid)
        self.assertTrue(mod["custom"])
        self.assertFalse(mod["essential"])
        self.assertEqual(mod["name"], "CoolMod")
        self.assertEqual(mod["register_dll"], "mods/CoolMod.dll")
        self.assertEqual(mod["installed_files"], ["mods/CoolMod.dll"])
        self.assertEqual(mod["repo_url"], "https://github.com/someone/CoolMod")
        self.assertEqual(self.cfg["custom_mods"], [
            {"kind": "github_release", "owner": "someone", "repo": "CoolMod",
             "dll": "CoolMod.dll"}])

    def test_loaded_again_from_the_config(self):
        self.app.add_custom_mod(self.src, "CoolMod.dll")
        self.app.load_custom_mods({})
        self.assertFalse(any(m.get("custom") for m in self.app.MODS_REGISTRY))
        self.assertEqual(self.app.load_custom_mods(self.cfg),
                         ["custom:coolmod.dll"])
        self.assertEqual(self.app.load_custom_mods(self.cfg),
                         ["custom:coolmod.dll"])          # not twice
        self.assertEqual(sum(1 for m in self.app.MODS_REGISTRY
                             if m.get("custom")), 1)

    def test_names_already_used_are_refused(self):
        for dll in ("ClassicAPI.dll", "octologin.dll", "VfPatcher.dll"):
            self.assertIsNotNone(self.app.custom_mod_conflict(dll), dll)
        self.app.add_custom_mod(self.src, "CoolMod.dll")
        with self.assertRaises(ValueError):
            self.app.add_custom_mod(self.src, "coolmod.DLL")
        for dll in ("../x.dll", "a/b.dll", "x.exe", "", ".dll"):
            self.assertIsNotNone(self.app.custom_mod_conflict(dll), dll)

    def test_forget(self):
        mid = self.app.add_custom_mod(self.src, "CoolMod.dll")
        self.cfg["mods"] = {mid: {"enabled": True}, "ClassicAPI": {}}
        self.cfg["mod_release_cache"] = {mid: {}}
        self.app.forget_custom_mod(mid)
        self.assertEqual(self.cfg["custom_mods"], [])
        self.assertEqual(self.cfg["mods"], {"ClassicAPI": {}})
        self.assertEqual(self.cfg["mod_release_cache"], {})
        self.assertFalse(any(m["id"] == mid for m in self.app.MODS_REGISTRY))


class TestInstall(CustomModBase):

    def mod(self, src=None, dll="CoolMod.dll"):
        mid = self.app.add_custom_mod(src or self.src, dll)
        return next(m for m in self.app.MODS_REGISTRY if m["id"] == mid)

    def test_installs_only_the_chosen_dll_into_mods(self):
        urls = []

        def fetch(req, **kw):
            urls.append(req.full_url)
            return Resp(DLL)
        with mock.patch.object(self.app, "secure_urlopen", fetch):
            written = self.app.install_mod(
                self.mod(), self.client,
                release("Other.dll", "CoolMod.dll", "CoolMod-src.zip"))
        self.assertEqual(written, ["mods/CoolMod.dll"])
        self.assertEqual(urls, ["https://github.com/someone/CoolMod/releases/"
                                "download/v2.0/CoolMod.dll"])
        with open(os.path.join(self.client, "mods", "CoolMod.dll"), "rb") as f:
            self.assertEqual(f.read(), DLL)

    def test_a_name_with_wildcards_is_matched_exactly(self):
        with mock.patch.object(self.app, "secure_urlopen",
                               lambda req, **kw: Resp(DLL)):
            written = self.app.install_mod(
                self.mod(dll="Mod[1].dll"), self.client,
                release("Mod1.dll", "Mod[1].dll"))
        self.assertEqual(written, ["mods/Mod[1].dll"])

    def test_codeberg_and_octowow_git_honour_the_mods_folder(self):
        for src in ({"kind": "codeberg_release", "owner": "a", "repo": "b"},
                    {"kind": "gitea_release", "owner": "a", "repo": "b",
                     "forge": "octowow.st/git"}):
            self.app.load_custom_mods({})
            self.cfg.clear()
            with mock.patch.object(self.app, "secure_urlopen",
                                   lambda req, **kw: Resp(DLL)):
                written = self.app.install_mod(
                    self.mod(src), self.client, release("CoolMod.dll"))
            self.assertEqual(written, ["mods/CoolMod.dll"], src["kind"])

    def test_latest_release_for_each_host(self):
        calls = []
        with mock.patch.object(self.app, "_github_latest",
                               lambda *a, **k: calls.append("gh") or {}), \
                mock.patch.object(self.app, "_codeberg_latest",
                                  lambda *a, **k: calls.append("cb") or {}), \
                mock.patch.object(self.app, "_gitea_latest",
                                  lambda api, *a, **k: calls.append(api) or {}):
            self.app._release_latest({"kind": "github_release", "owner": "a",
                                      "repo": "b"})
            self.app._release_latest({"kind": "codeberg_release", "owner": "a",
                                      "repo": "b"})
            self.app._release_latest({"kind": "gitea_release", "owner": "a",
                                      "repo": "b", "forge": "octowow.st/git"})
        self.assertEqual(calls[:2], ["gh", "cb"])
        self.assertIn("octowow.st", calls[2])

    def test_registered_after_the_known_mods_and_removed_cleanly(self):
        mod = self.mod()
        with open(os.path.join(self.client, "dlls.txt"), "w") as f:
            f.write("mods/OctoLogin.dll\nVanillaHelpers.dll\n")
        self.app.add_dll(self.client, mod["register_dll"])
        self.assertEqual(self.dlls(), ["VanillaHelpers.dll", "mods/OctoLogin.dll",
                                       "mods/CoolMod.dll"])
        os.makedirs(os.path.join(self.client, "mods"))
        with open(os.path.join(self.client, "mods", "CoolMod.dll"), "wb") as f:
            f.write(DLL)
        self.app.uninstall_mod(mod, self.client)
        self.app.remove_dll(self.client, mod["register_dll"])
        self.assertFalse(os.path.exists(
            os.path.join(self.client, "mods", "CoolMod.dll")))
        self.assertEqual(self.dlls(), ["VanillaHelpers.dll", "mods/OctoLogin.dll"])


from tests.test_app_smoke import HAVE_TK, _close_app  # noqa: E402


_REAL_THREAD = __import__("threading").Thread


def _thread(target=None, args=(), **kw):
    """The dialog's release lookup runs inline (Tk's after() needs a running
    mainloop from another thread); every other thread is a real one -- an
    inline animation or news worker would never return."""
    if "_open_custom_mod_dialog" in getattr(target, "__qualname__", ""):
        return type("T", (), {"start": lambda self: target(*args)})()
    return _REAL_THREAD(target=target, args=args, **kw)


@unittest.skipUnless(HAVE_TK, "no display")
class TestDialog(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="equ-custom-mod-ui-")
        os.environ["LOCALAPPDATA"] = cls.tmp
        os.environ["XDG_DATA_HOME"] = cls.tmp
        for mod in [m for m in list(sys.modules) if m.startswith("equpdater")]:
            del sys.modules[mod]
        from equpdater import app
        cls.m = app
        cls.app = app.EqUpdaterApp()
        for _ in range(8):
            cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.m.load_custom_mods({})
        _close_app(cls.app)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.client = tempfile.mkdtemp(prefix="client-", dir=self.tmp)
        open(os.path.join(self.client, "WoW.exe"), "wb").close()
        self.app._game_path.set(self.client)
        # The folder is the saved one: closing the dialog is then just that,
        # not a folder change with its reconcile prompt.
        self.app._cfg["out_dir"] = self.client
        self.app._first_run = False
        # The first-run mods question is answered: it opens an overlay of its
        # own when a game folder appears, at a moment no test controls.
        self.m.update_config(lambda c: c.update(essential_mods_asked=True,
                                                auto_install_mods=False))
        self.app._switch_tab("MODS")
        self.updates, self.asked = [], []
        self.patches = [
            mock.patch.object(self.m.threading, "Thread", _thread),
            # Closing a dialog is "confirm": never let it reach a Windows
            # prompt or a reconcile here.
            mock.patch.object(self.app, "_prompt_av_exclusion", lambda: None),
            mock.patch.object(self.app, "_offer_reconcile", lambda *a, **k: None),
            mock.patch.object(self.m, "_release_latest",
                              lambda src, **k: release("CoolMod.dll", "Other.dll")),
            mock.patch.object(self.app, "_update_mod", self.updates.append),
            mock.patch("tkinter.messagebox.askyesno",
                       lambda t, b, **kw: self.asked.append((t, b)) or True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        if self.app._settings_overlay is not None:
            self.app._close_settings()
        for p in reversed(self.patches):
            p.stop()
        for mid in [m["id"] for m in self.m.MODS_REGISTRY if m.get("custom")]:
            self.m.forget_custom_mod(mid)

    def pump(self):
        for _ in range(4):
            self.app.update()

    def test_find_choose_warn_install_remove(self):
        a = self.app
        a._open_custom_mod_dialog()
        dialog = a._settings_overlay
        ui = a._custom_mod_ui
        ui["url"].set("https://example.com/a/b")
        ui["find"]()
        self.assertIn("GitHub", ui["status"].cget("text"))

        ui["url"].set("https://github.com/someone/CoolMod")
        ui["find"]()
        self.pump()
        self.assertEqual(ui["state"]["dlls"], ["CoolMod.dll", "Other.dll"])
        ui["choice"].set("Other.dll")
        ui["install"]()
        self.pump()

        self.assertEqual(len(self.asked), 1)               # the warning
        title, body = self.asked[0]
        self.assertIn("Other.dll", title)
        self.assertIn("Only install mods you trust", body)
        self.assertIn("github.com/someone/CoolMod", body)
        self.assertFalse(dialog.winfo_exists())            # the dialog closed
        self.assertEqual(self.updates, ["custom:other.dll"])
        self.assertIn("custom:other.dll", a._mod_row_vars)
        self.assertEqual(self.m.load_config()["custom_mods"][0]["dll"],
                         "Other.dll")

        os.makedirs(os.path.join(self.client, "mods"))
        with open(os.path.join(self.client, "mods", "Other.dll"), "wb") as f:
            f.write(DLL)
        self.m.add_dll(self.client, "mods/Other.dll")
        a._remove_custom_mod("custom:other.dll")
        self.pump()
        self.assertFalse(os.path.exists(
            os.path.join(self.client, "mods", "Other.dll")))
        self.assertFalse(os.path.exists(os.path.join(self.client, "dlls.txt")))
        self.assertNotIn("custom:other.dll", a._mod_row_vars)
        self.assertEqual(self.m.load_config()["custom_mods"], [])

    def test_declining_the_warning_installs_nothing(self):
        a = self.app
        a._open_custom_mod_dialog()
        dialog = a._settings_overlay
        ui = a._custom_mod_ui
        ui["url"].set("https://github.com/someone/CoolMod/releases/download/"
                      "v2.0/CoolMod.dll")
        ui["find"]()
        self.pump()
        self.assertEqual(ui["state"]["dlls"], ["CoolMod.dll"])
        with mock.patch("tkinter.messagebox.askyesno", lambda *x, **k: False):
            ui["install"]()
        self.assertEqual(self.updates, [])
        self.assertFalse(self.m.load_config().get("custom_mods"))
        self.assertTrue(dialog.winfo_exists())             # still open


if __name__ == "__main__":
    unittest.main()
