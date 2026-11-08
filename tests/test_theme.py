"""Themes: Modern is what it always was, Classic draws from artwork that ships
with the app, a missing artwork pack falls back instead of failing, and the
choice persists. The theme is presentation only; nothing here touches the
rules the planner protects.

The window tests are skipped where there is no display.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["EQUPDATER_NO_LANGUAGE_PROMPT"] = "1"

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    HAVE_TK = False

from equpdater import theme  # noqa: E402


class TestThemeDefinitions(unittest.TestCase):
    def test_modern_is_the_default_and_unknown_names_mean_modern(self):
        self.assertEqual(theme.DEFAULT, theme.MODERN)
        for junk in (None, "", "nonsense", 3, "CLASSIC "):
            expected = theme.CLASSIC if junk == "CLASSIC " else theme.MODERN
            self.assertEqual(theme.normalize(junk), expected)

    def test_modern_palette_is_the_one_the_app_always_had(self):
        # The values that were literals in app.py before themes existed.
        c = theme.THEMES[theme.MODERN].colors
        self.assertEqual((c["bg"], c["panel"], c["gold"], c["text"]),
                         ("#06132d", "#081a35", "#c99a3d", "#eee9dc"))
        self.assertFalse(theme.THEMES[theme.MODERN].skinned)

    def test_both_themes_define_every_token(self):
        self.assertEqual(set(theme.THEMES[theme.CLASSIC].colors),
                         set(theme.THEMES[theme.MODERN].colors))

    def test_classic_text_is_readable_on_its_panel(self):
        def lum(h):
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
            f = lambda v: v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
            return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)

        def contrast(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + .05) / (lo + .05)
        c = theme.THEMES[theme.CLASSIC].colors
        for fg in ("text", "text_dim", "gold", "ok", "err"):
            self.assertGreater(contrast(c[fg], c["panel"]), 4.5, fg)


class TestClassicArt(unittest.TestCase):
    def setUp(self):
        self.art = theme.classic_art()
        self.assertIsNotNone(self.art, "assets/classic is missing")

    def test_every_required_file_ships(self):
        for name in theme.REQUIRED:
            self.assertTrue(os.path.isfile(
                os.path.join(self.art.dir, name + ".png")), name)

    def test_buttons_are_exactly_the_size_asked_in_every_state(self):
        for big in (True, False):
            for state in theme.STATES:
                for w, h in ((90, 30), (200, 46), (37, 32)):
                    img = self.art.button(big, w, h, state)
                    self.assertEqual(img.size, (max(w, img.width), h))
                    self.assertGreaterEqual(img.width, w)

    def test_button_caps_keep_their_proportions_when_stretched(self):
        """Only the middle stretches: the left cap of a 100 px and a 400 px
        wide button is the same picture."""
        a = self.art.button(False, 100, 34)
        b = self.art.button(False, 400, 34)
        cap = round(18 * 34 / 42)
        self.assertEqual(a.crop((0, 0, cap, 34)).tobytes(),
                         b.crop((0, 0, cap, 34)).tobytes())

    def test_states_differ_and_the_original_is_not_edited(self):
        up = self.art.button(False, 120, 34, "up").tobytes()
        raw = self.art.raw("small_button_left_cap").copy().tobytes()
        for state in ("hover", "down", "off", "sel"):
            self.assertNotEqual(self.art.button(False, 120, 34, state).tobytes(), up)
        self.assertEqual(self.art.raw("small_button_left_cap").tobytes(), raw)

    def test_the_frame_is_transparent_inside(self):
        f = self.art.frame(600, 400)
        self.assertEqual(f.size, (600, 400))
        self.assertEqual(f.getpixel((300, 200))[3], 0)
        self.assertGreater(f.getpixel((300, 3))[3], 200)

    def test_scene_fits_any_window_size(self):
        for w, h in ((1000, 700), (1250, 875), (2000, 1400), (640, 400)):
            self.assertEqual(self.art.scene(w, h, 1.0, 64, h - 140).size, (w, h))

    def test_images_are_cached(self):
        self.assertIs(self.art.button(False, 80, 30), self.art.button(False, 80, 30))
        self.assertIs(self.art.frame(300, 200), self.art.frame(300, 200))


class TestFallback(unittest.TestCase):
    def tearDown(self):
        theme._ART.clear()

    def test_missing_artwork_means_modern_not_a_crash(self):
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.object(theme, "asset_dir_candidates",
                                  lambda: [empty]):
            theme._ART.clear()
            self.assertIsNone(theme.classic_art())
            self.assertEqual(theme.resolve("classic").key, theme.MODERN)

    def test_a_partial_pack_is_not_used(self):
        with tempfile.TemporaryDirectory() as part:
            src = theme.find_asset_dir()
            for name in theme.REQUIRED[:-1]:
                with open(os.path.join(src, name + ".png"), "rb") as a, \
                        open(os.path.join(part, name + ".png"), "wb") as b:
                    b.write(a.read())
            with mock.patch.object(theme, "asset_dir_candidates",
                                   lambda: [part]):
                theme._ART.clear()
                self.assertIsNone(theme.classic_art())

    def test_the_artwork_is_packaged(self):
        with open(os.path.join(ROOT, "build.py"), encoding="utf-8") as f:
            self.assertIn("CLASSIC_DIR", f.read())


@unittest.skipUnless(HAVE_TK, "needs a display")
class TestClassicWindow(unittest.TestCase):
    """The real window, in both themes, on an empty profile."""

    def make(self, theme_key, scale=None):
        from equpdater import config
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        cfg_path = os.path.join(tmp, "config.json")
        cfg = {"app_version": "99", "animated_background": False}
        if theme_key:
            cfg["theme"] = theme_key
        with open(cfg_path, "w") as f:
            json.dump(cfg, f)
        patches = [mock.patch.object(config, "CONFIG_FILE", cfg_path),
                   mock.patch.object(config, "APP_DATA_DIR", tmp)]
        from equpdater import app as m
        patches += [mock.patch.object(m, "CONFIG_FILE", cfg_path),
                    mock.patch.object(m, "APP_DATA_DIR", tmp)]
        for n in ("_load_news", "_start_verify", "_addons_verify",
                  "_mpq_check", "_load_mods_state", "_check_updater_update",
                  "_diag_recover", "_offer_av_exclusion"):
            patches.append(mock.patch.object(m.EqUpdaterApp, n,
                                             lambda self, *a, **k: None))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        saved = (m.WIN_W, m.WIN_H, m.FOOT_H)
        self.addCleanup(lambda: (setattr(m, "WIN_W", saved[0]),
                                 setattr(m, "WIN_H", saved[1]),
                                 setattr(m, "FOOT_H", saved[2])))
        app = m.EqUpdaterApp()
        self.addCleanup(self.close, app)
        app.update()
        return app, m

    @staticmethod
    def close(app):
        try:
            app.destroy()
        except Exception:
            pass

    def test_no_setting_means_modern_and_unchanged_colours(self):
        app, m = self.make(None)
        self.assertEqual(app._theme.key, "modern")
        self.assertIsNone(app._art)
        self.assertEqual(m.C_BG, "#06132d")
        self.assertEqual(app._inset, app._px(40))

    def test_classic_starts_and_every_tab_opens(self):
        app, m = self.make("classic")
        self.assertEqual(app._theme.key, "classic")
        self.assertIsNotNone(app._art)
        self.assertEqual(m.C_BG, theme.THEMES["classic"].colors["bg"])
        for tab in ("TWEAKS", "ADDONS", "MODS", "MPQ", "NEWS"):
            app._switch_tab(tab)
            app.update()
            self.assertTrue(app._active_panel.winfo_ismapped(), tab)
        # The tabs are stone buttons: an image in each tab's tag.
        for tab in app._nav_pos:
            kinds = {app._hdr_canvas.type(i)
                     for i in app._hdr_canvas.find_withtag(f"nav_{tab}")}
            self.assertIn("image", kinds)

    def test_modern_comes_back_after_classic(self):
        self.make("classic")
        app, m = self.make("modern")
        self.assertEqual(m.C_BG, "#06132d")
        self.assertIsNone(app._art)

    def test_classic_with_the_artwork_missing_still_launches(self):
        # The app's own copy of the module: other tests re-import the
        # package, so the one imported above may not be the one in use.
        from equpdater import app as m
        with mock.patch.object(m.themes, "asset_dir_candidates", lambda: []):
            m.themes._ART.clear()
            try:
                app, m = self.make("classic")
                self.assertEqual(app._theme.key, "modern")
                self.assertIsNone(app._art)
            finally:
                m.themes._ART.clear()

    def test_the_choice_is_saved_and_offered_a_restart(self):
        app, m = self.make("modern")
        asked = []
        with mock.patch("tkinter.messagebox.askyesno",
                        lambda *a, **k: asked.append(a) or False):
            app._theme_var.set("classic")
            app._change_theme()
        self.assertEqual(len(asked), 1)
        self.assertEqual(m.load_config()["theme"], "classic")
        # Choosing what is already showing asks nothing.
        with mock.patch("tkinter.messagebox.askyesno",
                        lambda *a, **k: asked.append(a) or False):
            app._theme_var.set("modern")
            app._change_theme()
        self.assertEqual(len(asked), 1)
        self.assertEqual(m.load_config()["theme"], "modern")

    def test_classic_resizes_without_distortion(self):
        app, m = self.make("classic")
        app.geometry("1200x800")
        for _ in range(4):
            app.update()
        app._apply_resize()
        app.update()
        w, h = m.WIN_W, m.WIN_H
        self.assertEqual(app._bg_pil.size, (w, h))
        self.assertFalse(app._panels_hidden)

    def test_row_icons_sit_on_stone_in_classic_only(self):
        app, _ = self.make("classic")
        frame = tk.Frame(app)
        icon = app._action_icon(frame, "link", "tip", lambda: None)
        self.assertEqual(icon.find_withtag("stone") and 1, 1)
        app2, _ = self.make("modern")
        icon2 = app2._action_icon(tk.Frame(app2), "link", "tip", lambda: None)
        self.assertFalse(icon2.find_withtag("stone"))


if __name__ == "__main__":
    unittest.main()
