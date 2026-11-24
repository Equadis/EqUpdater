"""Skin engine (Qt-free): validation, fallbacks, and what a hostile skin can't do."""

import json
import os
import shutil
import tempfile
import unittest

from equpdater.core import skin

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make(files: dict, manifest=None, theme=None):
    d = tempfile.mkdtemp(prefix="equ-skin-")
    os.makedirs(os.path.join(d, "s"))
    folder = os.path.join(d, "s")
    man = {"id": "t", "name": "T", "skin_api": 1} if manifest is None else manifest
    if man is not False:
        with open(os.path.join(folder, "manifest.json"), "w") as f:
            json.dump(man, f)
    if theme is not None:
        with open(os.path.join(folder, "theme.json"), "w") as f:
            json.dump(theme, f)
    for rel, data in files.items():
        p = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return d, folder


class TestSkin(unittest.TestCase):
    def tearDown(self):
        for d in getattr(self, "dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def load(self, *a, **k):
        d, folder = make(*a, **k)
        self.dirs = getattr(self, "dirs", []) + [d]
        return skin.load_skin(folder)

    def test_a_skin_with_only_a_manifest_is_complete(self):
        s = self.load({})
        for cid in skin.COMPONENTS:
            self.assertIn(cid, s.theme["components"])
        self.assertTrue(s.theme["components"]["button"]["colors"]["normal"])

    def test_every_component_id_resolves_for_every_shipped_skin(self):
        found = skin.discover(os.path.join(ROOT, "skins"))
        self.assertIn("bubbles", found)
        self.assertIn("minimal-dark", found)
        for s in found.values():
            self.assertEqual([p for p in s.problems], [], s.id)
            self.assertEqual(set(skin.COMPONENTS), set(s.theme["components"]))

    def test_texture_and_nine_slice_resolve_to_a_file_url(self):
        s = self.load({"b/n.png": PNG},
                      theme={"components": {"button": {
                          "textures": {"normal": "b/n.png"}, "slice": [3, 3, 3, 3]}}})
        c = s.theme["components"]["button"]
        self.assertTrue(c["textures"]["normal"].startswith("file:"))
        self.assertEqual(c["slice"], [3, 3, 3, 3])
        # children inherit, a child can clear
        self.assertIn("normal", s.theme["components"]["button.play"]["textures"])
        self.assertEqual(s.theme["components"]["button.link"]["textures"], {})

    def test_missing_asset_falls_back_and_is_reported(self):
        s = self.load({}, theme={"components": {"button": {
            "textures": {"normal": "nope.png"}}}})
        self.assertEqual(s.theme["components"]["button"]["textures"], {})
        self.assertTrue(any("nope.png" in p for p in s.problems))

    def test_path_traversal_and_absolute_paths_are_refused(self):
        outside = tempfile.mkdtemp()
        with open(os.path.join(outside, "x.png"), "wb") as f:
            f.write(PNG)
        self.dirs = [outside]
        for bad in ("../x.png", "/etc/passwd", "C:\\Windows\\x.png", "a/../../x.png"):
            s = self.load({}, theme={"background": {"static": bad}})
            self.assertEqual(s.theme["background"]["static"], "", bad)
            self.assertTrue(s.problems, bad)

    def test_wrong_type_wrong_magic_and_oversize_are_refused(self):
        s = self.load({"a.exe": PNG, "b.png": b"MZ" + b"\0" * 40,
                       "c.png": PNG + b"\0" * (skin.MAX_IMAGE + 1)},
                      theme={"components": {
                          "button": {"textures": {"normal": "a.exe", "hover": "b.png",
                                                  "pressed": "c.png"}}}})
        self.assertEqual(s.theme["components"]["button"]["textures"], {})
        self.assertEqual(len(s.problems), 3)

    def test_symlink_out_of_the_folder_is_refused(self):
        d, folder = make({})
        self.dirs = [d]
        target = os.path.join(d, "secret.png")
        with open(target, "wb") as f:
            f.write(PNG)
        try:
            os.symlink(target, os.path.join(folder, "link.png"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted here")
        with open(os.path.join(folder, "theme.json"), "w") as f:
            json.dump({"background": {"static": "link.png"}}, f)
        s = skin.load_skin(folder)
        self.assertEqual(s.theme["background"]["static"], "")

    def test_bad_manifest_and_future_api(self):
        self.assertTrue(self.load({}, manifest={"id": "Bad Id!"}).problems)
        s = self.load({}, manifest={"id": "x", "skin_api": 99})
        self.assertTrue(any("skin_api 99" in p for p in s.problems))
        self.assertTrue(self.load({}, manifest=False).problems)

    def test_bad_values_never_raise(self):
        s = self.load({}, theme={"palette": {"accent": "gold"},
                                 "components": {"panel": {"slice": [1, 2],
                                                          "padding": "wide",
                                                          "borderWidth": "thick"}},
                                 "fonts": {"body": {"size": 9999}}})
        self.assertTrue(s.problems)
        self.assertLessEqual(s.theme["fonts"]["body"]["size"], 72)

    def test_oversized_json_is_ignored(self):
        s = self.load({"theme.json": b" " * (skin.MAX_JSON + 10)})
        self.assertTrue(any("larger than" in p for p in s.problems))

    def test_cyclic_extends_terminates(self):
        s = self.load({}, theme={"components": {"button": {"extends": "button.play"},
                                                "button.play": {"extends": "button"}}})
        self.assertIn("button", s.theme["components"])

    def test_palette_reference_resolves(self):
        s = self.load({}, theme={"palette": {"accent": "#112233"},
                                 "components": {"badge": {"colors": {"normal": "$accent"}}}})
        self.assertEqual(s.theme["components"]["badge"]["colors"]["normal"], "#112233")


if __name__ == "__main__":
    unittest.main()
