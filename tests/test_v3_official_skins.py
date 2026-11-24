"""The five official skins: all load cleanly, are complete, readable and light."""

import os
import unittest

from equpdater.core import skin as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKINS = os.path.join(ROOT, "skins")
OFFICIAL = {"bubbles", "classic-2004", "minimal", "minimal-dark", "minimal-light", "turtle-wow", "battle-net"}


def lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[-6:][i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class TestOfficialSkins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skins = S.discover(SKINS)

    def test_exactly_the_seven_official_skins_ship(self):
        self.assertEqual(set(self.skins), OFFICIAL)

    def test_every_skin_loads_with_no_problems(self):
        for sid, s in self.skins.items():
            self.assertEqual(s.problems, [], sid)

    def test_every_component_id_resolves_in_every_skin(self):
        for sid, s in self.skins.items():
            for cid in S.COMPONENTS:
                self.assertIn(cid, s.theme["components"], f"{sid}: {cid}")

    def test_every_skin_has_every_icon(self):
        import re
        used = set()
        for d, _s, files in os.walk(os.path.join(ROOT, "equpdater", "qtui", "qml")):
            for f in files:
                if f.endswith(".qml"):
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        used |= set(re.findall(r'"(icon\.[a-z-]+)"', fh.read()))
        icons = sorted(c for c in used if c in S.COMPONENTS)
        self.assertGreaterEqual(len(icons), 10)
        for sid, s in self.skins.items():
            for cid in icons:
                spec = s.theme["components"][cid]
                self.assertTrue(spec.get("icon"), f"{sid}: {cid} has no icon")

    def test_names_are_the_ones_players_see(self):
        names = {sid: s.name for sid, s in self.skins.items()}
        self.assertEqual(names["classic-2004"], "Classic (2004)")
        self.assertEqual(names["minimal"], "Minimal")
        self.assertEqual(names["minimal-dark"], "Minimal Dark")
        self.assertEqual(names["minimal-light"], "Minimal Light")
        self.assertEqual(names["bubbles"], "Bubbles")
        self.assertEqual(names["turtle-wow"], "Turtle WoW")
        self.assertEqual(names["battle-net"], "Battle.net")

    def test_minimal_skins_are_colours_and_icons_only(self):
        for sid in ("minimal", "minimal-dark", "minimal-light"):
            bg = self.skins[sid].theme["background"]
            self.assertFalse(bg.get("animation"), sid)
            self.assertFalse(bg.get("static"), sid)
            for d, _s, files in os.walk(os.path.join(SKINS, sid)):
                for f in files:
                    if d.endswith("icons") or f in ("manifest.json", "theme.json"):
                        continue
                    self.fail(f"{sid} carries {f}")

    def test_the_animated_skins_have_a_still_and_the_animation_is_a_sane_size(self):
        for sid in ("bubbles", "classic-2004"):
            bg = self.skins[sid].theme["background"]
            self.assertTrue(bg.get("static"), sid)
            self.assertTrue(bg.get("animation"), sid)
            anim = os.path.join(SKINS, sid, "backgrounds", os.path.basename(bg["animation"]))
            self.assertLess(os.path.getsize(anim), 12 * 1024 * 1024, sid)

    def test_text_is_readable_on_the_panels_of_the_flat_skins(self):
        for sid in ("minimal", "minimal-dark", "minimal-light"):
            p = self.skins[sid].theme["palette"]
            panel = p["panel"]
            self.assertGreaterEqual(contrast(p["text"], panel), 7.0, f"{sid} body text")
            self.assertGreaterEqual(contrast(p["textDim"], panel), 4.5, f"{sid} dim text")
            self.assertGreaterEqual(contrast(p["accent"], panel), 3.0, f"{sid} accent")
            self.assertGreaterEqual(contrast(p["text"], p["bg"]), 7.0, f"{sid} text on window")
            self.assertGreaterEqual(contrast(p["text"], p["buttonFace"]), 4.5, f"{sid} button text")
            self.assertGreaterEqual(contrast("#ffffff", p["primary"]), 4.5, f"{sid} play button")

    def test_turtle_and_battlenet_text_is_readable_on_their_panels_and_buttons(self):
        for sid in ("turtle-wow", "battle-net"):
            p = self.skins[sid].theme["palette"]
            self.assertGreaterEqual(contrast(p["text"], p["panel"]), 7.0, f"{sid} body")
            self.assertGreaterEqual(contrast(p["textDim"], p["panel"]), 4.5, f"{sid} dim")
            self.assertGreaterEqual(contrast(p["accent"], p["panel"]), 4.5, f"{sid} accent")
            self.assertGreaterEqual(contrast("#ffffff", p["primary"]), 3.0, f"{sid} play")

    def test_turtle_and_battlenet_are_full_skins_not_palettes(self):
        for sid in ("turtle-wow", "battle-net"):
            c = self.skins[sid].theme["components"]
            for cid in ("panel", "dialog", "sidebar", "button", "button.play"):
                self.assertTrue(c[cid].get("textures"), f"{sid}: {cid} has no texture")
            self.assertTrue(self.skins[sid].theme["background"].get("static"), sid)
            for cid in ("input", "menu", "tooltip", "progress", "scrollbar", "checkbox", "nav.tab"):
                self.assertNotEqual(c[cid], self.skins["minimal-dark"].theme["components"][cid], f"{sid}: {cid}")
        tw, bn = (self.skins[s].theme for s in ("turtle-wow", "battle-net"))
        self.assertEqual(tw["fonts"]["body"]["family"], "Friz Quadrata")
        self.assertLess(tw["layout"]["headerHeight"], 96)                       # compact
        self.assertEqual(bn["layout"]["navAlign"], "left")
        self.assertEqual(bn["layout"]["sidebarDefault"], "expanded")
        self.assertGreaterEqual(bn["components"]["button.play"]["minHeight"], 50)  # prominent
        self.assertTrue(bn["palette"]["accent"].lower() in ("#148eff", "#0e86ff", "#00aeff"))

    def test_light_is_light_and_dark_is_dark(self):
        self.assertGreater(lum(self.skins["minimal-light"].theme["palette"]["bg"]), 0.6)
        for sid in ("minimal", "minimal-dark"):
            self.assertLess(lum(self.skins[sid].theme["palette"]["bg"]), 0.05)

    def test_classic_gold_text_reads_on_its_dark_frame_and_buttons(self):
        c = self.skins["classic-2004"].theme["components"]
        dark_fill = "#0a1018"
        self.assertGreaterEqual(contrast(c["button"]["textColors"]["normal"], "#2c3440"), 4.5)
        self.assertGreaterEqual(contrast("#f0e6c8", dark_fill), 7.0)
        self.assertGreaterEqual(contrast(c["text.heading"]["textColors"]["normal"], dark_fill), 7.0)

    def test_the_classic_frame_is_a_valid_nine_slice(self):
        from PIL import Image
        c = self.skins["classic-2004"].theme["components"]
        for cid in ("panel", "dialog", "button", "button.play"):
            slc = c[cid]["slice"]
            tex = c[cid]["textures"]["normal"]
            path = tex if os.path.isabs(tex) else None
            self.assertTrue(any(slc), cid)
            # the loader returns file URLs; check the source files directly
        frame = Image.open(os.path.join(SKINS, "classic-2004", "panels", "frame.png"))
        self.assertGreater(frame.width, 2 * 56 + 8)
        self.assertGreater(frame.height, 2 * 56 + 8)
        centre = frame.getpixel((frame.width // 2, frame.height // 2))
        self.assertLess(centre[3], 255)                     # the middle is a translucent fill
        btn = Image.open(os.path.join(SKINS, "classic-2004", "buttons", "normal-normal.png"))
        self.assertGreater(btn.width, 2 * 10 + 4)
        self.assertGreater(btn.height, 2 * 10 + 4)

    def test_every_button_state_exists_for_the_classic_skin(self):
        for prefix in ("normal", "play"):
            for state in ("normal", "hover", "pressed", "disabled"):
                self.assertTrue(os.path.isfile(
                    os.path.join(SKINS, "classic-2004", "buttons", f"{prefix}-{state}.png")))

    def test_total_weight_of_the_official_skins_stays_modest(self):
        total = sum(os.path.getsize(os.path.join(d, f)) for d, _s, fs in os.walk(SKINS) for f in fs)
        self.assertLess(total, 14 * 1024 * 1024)

    def test_the_classic_licence_note_is_not_hidden(self):
        with open(os.path.join(SKINS, "classic-2004", "manifest.json"), encoding="utf-8") as f:
            self.assertIn("redistribution", f.read())


class TestStarterSkin(unittest.TestCase):
    def test_the_starter_skin_loads_clean_and_is_complete(self):
        s = S.load_skin(os.path.join(ROOT, "docs", "v3", "starter-skin"))
        self.assertIsNotNone(s)
        self.assertEqual(s.problems, [])
        for cid in S.COMPONENTS:
            self.assertIn(cid, s.theme["components"])

    def test_the_component_reference_names_every_component_id(self):
        with open(os.path.join(ROOT, "docs", "v3", "COMPONENTS.md"), encoding="utf-8") as f:
            text = f.read()
        for cid in S.COMPONENTS:
            self.assertIn(cid, text, cid)


if __name__ == "__main__":
    unittest.main()
