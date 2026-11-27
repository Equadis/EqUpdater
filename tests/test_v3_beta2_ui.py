"""Interface changes after the first manual beta test: six skins and their order, the skin migration,
Settings staying open, font previews and Object Sans, Language only in Settings. Automated (offscreen Qt)."""

import json
import os
import shutil
import tempfile
import unittest

from tests.test_v3_qt import Base, HAVE_QT, pump, walk
from tests.test_v3_social_qt import find

if HAVE_QT:
    from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER = ["Bubbles", "Classic (2004)", "Blizzard", "Turtle", "Minimal (Dark)", "Minimal (Light)"]


def item_with_text(root, text):
    for it in walk(root):
        if it.property("text") == text and it.property("width"):
            return it


class UI(Base):
    def open_settings(self):
        QMetaObject.invokeMethod(self.win, "openSettings")
        pump(250)
        self.overlay = find(self.win, "settingsOverlay")
        self.flick = next(i for i in walk(self.overlay) if i.metaObject().className() == "QQuickFlickable")

    def click(self, item, scroll=True):
        if scroll:
            y = item.mapToItem(self.flick.property("contentItem"), QPointF(0, 0)).y()
            self.flick.setProperty("contentY", max(0.0, y - 120))
            pump(120)
        pt = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.win, Qt.LeftButton, Qt.NoModifier, QPoint(int(pt.x()), int(pt.y())))
        pump(350)


class TestSkinList(UI):
    def test_six_skins_with_the_exact_names_in_the_exact_order(self):
        p = self.start()
        skins = list(p["settings_bridge"].skins)
        self.assertEqual([s["name"] for s in skins], ORDER)
        self.assertEqual([s["id"] for s in skins],
                         ["bubbles", "classic-2004", "battle-net", "turtle-wow", "minimal-dark", "minimal-light"])
        for s in skins:
            self.assertEqual(s["author"], "")                      # no "· EqUpdater" suffix
        self.assertNotIn("minimal", [s["id"] for s in skins])

    def test_the_settings_buttons_show_exactly_those_names(self):
        self.start()
        self.open_settings()
        texts = list(dict.fromkeys(str(i.property("text")) for i in walk(self.overlay) if i.property("text")))
        for name in ORDER:
            self.assertIn(name, texts)
        for old in ("Battle.net", "Turtle WoW", "Minimal Dark", "Minimal Light", "Minimal"):
            self.assertNotIn(old, texts, old)
        self.assertFalse([t for t in texts if "EqUpdater" in t and "·" in t])
        shown = [t for t in texts if t in ORDER]
        self.assertEqual(shown, ORDER)                             # top to bottom

    def test_a_user_of_the_removed_minimal_skin_moves_to_minimal_dark_and_it_is_saved(self):
        data = tempfile.mkdtemp(prefix="equ-skin-")
        self.addCleanup(shutil.rmtree, data, True)
        with open(os.path.join(data, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "imported_from_2x": True, "skin": "minimal"}, f)
        p = self.start(data_dir=data, skin="minimal")
        self.assertEqual(p["settings_bridge"].currentSkin, "minimal-dark")
        self.assertEqual(p["settings"].get("skin"), "minimal-dark")

    def test_the_other_saved_skins_and_a_missing_one_behave(self):
        from equpdater.qtui.bridge import SkinManager
        official = os.path.join(ROOT, "skins")
        for saved, expect in (("turtle-wow", "turtle-wow"), ("battle-net", "battle-net"), ("minimal-light", "minimal-light"),
                              ("classic-2004", "classic-2004"), ("no-such-skin", "bubbles")):
            store = {"skin": saved}

            class S:
                def get(self, k, d=None):
                    return store.get(k, d)

                def set(self, k, v):
                    store[k] = v
            m = SkinManager(official, tempfile.mkdtemp(prefix="equ-us-"), S())
            self.assertEqual(m.current.id, expect, saved)
        store = {"skin": "minimal"}
        m = SkinManager(official, tempfile.mkdtemp(prefix="equ-us-"), S())
        self.assertEqual((m.current.id, store["skin"]), ("minimal-dark", "minimal-dark"))

    def test_the_classic_play_button_renders_with_the_shared_texture(self):
        p = self.start(skin="classic-2004")
        comp = p["skin"].theme["components"]
        self.assertEqual(comp["button.play"]["textures"]["normal"], comp["button"]["textures"]["normal"])
        pump(200)
        play = find(self.win, "playButton")
        self.assertIsNotNone(play)
        main = next(i for i in walk(play) if i.property("kind") == "button.play")
        self.assertIn("buttons/normal-", main.property("tex"))                 # the shared Classic texture, in every state
        self.assertNotIn("play-", main.property("tex"))
        self.assertEqual(self.qml_errors(), [])


class TestSettingsStaysOpen(UI):
    def test_selecting_a_skin_font_and_restoring_the_default_never_closes_settings(self):
        p = self.start()
        self.open_settings()
        self.assertTrue(self.overlay.property("visible"))
        for name in ("Minimal (Light)", "Turtle", "Classic (2004)", "Blizzard", "Minimal (Dark)", "Bubbles"):
            self.click(item_with_text(self.overlay, name))
            self.assertTrue(self.overlay.property("visible"), name)
        self.assertEqual(p["settings_bridge"].currentSkin, "bubbles")
        for fid in ("font_arial", "font_friz", "font_opendyslexic", "font_objectsans", "font_default"):
            self.click(find(self.win, fid))
            self.assertTrue(self.overlay.property("visible"), fid)
        self.click(item_with_text(self.overlay, "Turtle"))
        self.click(item_with_text(self.overlay, "Restore default skin"))
        self.assertTrue(self.overlay.property("visible"))
        self.assertEqual(p["settings_bridge"].currentSkin, "bubbles")
        self.assertEqual(self.qml_errors(), [])

    def test_checkboxes_keep_settings_open_and_apply_at_once(self):
        p = self.start()
        self.open_settings()
        box = item_with_text(self.overlay, "Animated background")
        before = p["settings_bridge"].animatedBackground
        self.click(box)
        self.assertTrue(self.overlay.property("visible"))
        self.assertNotEqual(p["settings_bridge"].animatedBackground, before)
        low = item_with_text(self.overlay, "Low-memory rendering")
        self.click(low)
        self.assertTrue(self.overlay.property("visible"))

    def test_the_scroll_position_survives_a_skin_change(self):
        self.start()
        self.open_settings()
        target = item_with_text(self.overlay, "Minimal (Light)")
        self.click(target)
        y = self.flick.property("contentY")
        self.assertGreater(y, 100)
        self.click(item_with_text(self.overlay, "Turtle"), scroll=False)
        self.assertTrue(self.overlay.property("visible"))
        self.assertGreater(self.flick.property("contentY"), 100)                  # not thrown back to the top
        self.assertAlmostEqual(self.flick.property("contentY"), y, delta=80)

    def test_only_an_outside_click_the_close_button_or_escape_closes_it(self):
        self.start()
        self.open_settings()
        close = item_with_text(self.overlay, "Close")
        self.assertIsNotNone(close)
        self.click(close, scroll=False)
        self.assertFalse(self.overlay.property("visible"))
        self.open_settings()
        QTest.mouseClick(self.win, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))             # on the scrim, outside the window
        pump(250)
        self.assertFalse(self.overlay.property("visible"))

    def test_the_skin_list_is_not_rebuilt_by_choosing_a_skin(self):
        p = self.start()
        self.open_settings()
        rebuilt = []
        p["settings_bridge"].skinsChanged.connect(lambda: rebuilt.append(1))
        for sid in ("turtle-wow", "minimal-light", "bubbles"):
            p["settings_bridge"].setSkin(sid)
        p["settings_bridge"].restoreDefaultSkin()
        self.assertEqual(rebuilt, [])


class TestFonts(UI):
    IDS = ("font_default", "font_arial", "font_friz", "font_opendyslexic", "font_objectsans")

    def test_every_option_is_drawn_in_its_own_face_whatever_font_is_active(self):
        p = self.start()
        self.open_settings()
        want = {"font_arial": "Arial", "font_friz": "Friz Quadrata", "font_opendyslexic": "OpenDyslexic",
                "font_objectsans": "Object Sans"}

        def faces():
            out = {}
            for fid in self.IDS:
                b = find(self.win, fid)
                self.assertIsNotNone(b, fid)
                out[fid] = b.property("fontFamily")
                label = next(i for i in walk(b) if i.metaObject().className() == "QQuickText" and i.property("text"))
                self.assertEqual(label.property("font").family(), out[fid], fid)
            return out
        first = faces()
        for fid, fam in want.items():
            self.assertEqual(first[fid], fam)
        for choice in ("arial", "friz", "opendyslexic", "objectsans", ""):
            p["general"].setFontOverride(choice)
            pump(200)
            self.assertEqual(faces(), first, choice)                 # the other previews do not change

    def test_the_buttons_keep_one_size_and_the_selection_follows_the_choice(self):
        p = self.start()
        self.open_settings()
        sizes = {(find(self.win, f).property("width"), find(self.win, f).property("height")) for f in self.IDS}
        self.assertEqual(len(sizes), 1)
        for choice, fid in (("friz", "font_friz"), ("objectsans", "font_objectsans"), ("", "font_default")):
            self.click(find(self.win, fid))
            self.assertEqual(p["general"].fontOverride, choice)
            self.assertTrue(find(self.win, fid).property("selected"))
            self.assertEqual(sum(1 for f in self.IDS if find(self.win, f).property("selected")), 1)

    def test_object_sans_is_registered_with_its_weights_and_drives_the_theme(self):
        p = self.start()
        self.assertIn("Object Sans", QFontDatabase.families())
        styles = QFontDatabase.styles("Object Sans")
        self.assertIn("Regular", styles)
        self.assertIn("Heavy", styles)
        self.assertEqual(QFontDatabase.weight("Object Sans", "Regular"), 400)
        self.assertEqual(QFontDatabase.weight("Object Sans", "Heavy"), 800)
        p["general"].setFontOverride("objectsans")
        theme = p["skin"].theme
        self.assertEqual({f["family"] for f in theme["fonts"].values() if f}, {"Object Sans"})
        self.assertEqual(p["general"].fontOverride, "objectsans")
        self.assertEqual(p["settings"].get("font_override"), "objectsans")        # persisted

    def test_object_sans_files_are_bundled_with_the_other_fonts_in_dev_and_package_builds(self):
        for n in ("ObjectSansRegular.otf", "ObjectSansSlanted.otf", "ObjectSansHeavy.otf", "ObjectSansHeavySlanted.otf"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "fonts", n)), n)
        with open(os.path.join(ROOT, "tools", "build_v3.py"), encoding="utf-8") as f:
            self.assertIn('"fonts") + sep + "fonts"', f.read())            # the whole folder goes into the package
        with open(os.path.join(ROOT, "fonts", "README.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Object Sans", text)
        self.assertIn("NOT been confirmed", text)                          # the licence question is written down


class TestLanguageLivesInSettings(UI):
    def test_the_tweaks_page_has_no_language_row_and_settings_has_one_selector(self):
        p = self.start()
        ids = [r["id"] for r in p["tweaks"].service.rows()]
        self.assertNotIn("locale", ids)
        labels = [r["label"] for r in p["tweaks"].service.rows()]
        self.assertNotIn("Language", labels)
        p["app"].tab = "tweaks"
        pump(200)
        texts = [str(i.property("text")) for i in walk(self.win.property("contentItem")) if i.property("text")]
        self.assertNotIn("Language", texts)
        self.open_settings()
        buttons = [i for i in walk(self.overlay) if i.objectName() == "languageButton"]
        self.assertEqual(len(buttons), 1)
        langs = list(p["tweaks"].languageChoices)
        self.assertGreaterEqual(len(langs), 5)
        self.assertEqual(sum(1 for c in langs if c["current"]), 1)
        heading = [i for i in walk(self.overlay) if i.property("text") == "LANGUAGE"]
        self.assertEqual(len(heading), 1)

    def test_the_stored_language_value_is_unchanged_by_the_move(self):
        from equpdater.core import tweaks as T
        self.assertIn("locale", T.TWEAKS_DEFAULTS)                          # same key, same file, same patch
        self.assertEqual(T.TWEAKS_DEFAULTS["locale"], T.DEFAULT_LOCALE)


class TestLanguageService(unittest.TestCase):
    def test_choosing_a_language_saves_it_and_patches_the_game_like_the_tweak_did(self):
        from equpdater.core.tweaks_service import TweaksService
        from equpdater.core import tweaks as T

        class Tasks:
            def submit(self, key, fn, on_done=None, on_error=None):
                try:
                    r = fn()
                except Exception as e:                  # noqa: BLE001
                    on_error(e)
                else:
                    on_done(r)
                return True
        saved, patched, wtf = [], [], []
        game = tempfile.mkdtemp(prefix="equ-lang-")
        self.addCleanup(shutil.rmtree, game, True)
        open(os.path.join(game, "WoW.exe"), "wb").write(b"MZ")
        st = {"game_dir": game}

        class Settings:
            def get(self, k, d=None):
                return st.get(k, d)
        stored = dict(T.TWEAKS_DEFAULTS, fieldOfView=110)
        svc = TweaksService(Settings(), Tasks(), load=lambda: dict(stored), save=lambda v: saved.append(dict(v)),
                            patch=lambda g, v: patched.append(v["locale"]), wtf=lambda g, v: wtf.append(v["locale"]))
        svc.values = dict(stored, fieldOfView=130)                           # an unsaved edit on the Tweaks page
        svc.saved = dict(stored)
        done = []
        self.assertTrue(svc.set_language("deDE", lambda: done.append(1)))
        self.assertEqual(saved[-1]["locale"], "deDE")
        self.assertEqual(saved[-1]["fieldOfView"], 110)                      # only the language was applied
        self.assertEqual((patched, wtf, done), (["deDE"], ["deDE"], [1]))
        self.assertEqual(svc.values["fieldOfView"], 130)                     # the pending edit is not lost
        self.assertFalse(svc.set_language("xxXX"))                           # unknown codes are refused


if __name__ == "__main__":
    unittest.main()
