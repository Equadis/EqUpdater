"""Interface changes after the manual beta tests: six skins and their order, the skin migration, Settings
(Appearance, Language with flags, staying open), font previews and Object Sans, Low-memory rendering gone,
the shell layout (spacing constants, symmetric margins, the friends list in its own space), and the shared
Addons/Mods/MPQ row actions. Automated (offscreen Qt, real mouse events through QTest)."""

import json
import os
import shutil
import tempfile
import unittest

from tests.test_v3_qt import Base, HAVE_QT, pump, walk
from tests.test_v3_social_qt import find

if HAVE_QT:
    from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtQml import QQmlComponent
    from PySide6.QtTest import QTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER = ["Bubbles", "Classic (2004)", "Blizzard", "Turtle", "Minimal (Dark)", "Minimal (Light)"]
SKIN_IDS = ["bubbles", "classic-2004", "battle-net", "turtle-wow", "minimal-dark", "minimal-light"]
LANGS = [("enUS", "English", "gb"), ("deDE", "Deutsch", "de"), ("ruRU", "Русский", "ru"),
         ("zhCN", "简体中文", "cn"), ("esES", "Español", "es"), ("ptBR", "Português (Brasil)", "br")]


def item_with_text(root, text):
    for it in walk(root):
        if it.property("text") == text and it.property("width"):
            return it


def as_list(v):
    return v.toVariant() if hasattr(v, "toVariant") else v


def rect(item):
    tl = item.mapToScene(item.boundingRect().topLeft())
    return (tl.x(), tl.y(), tl.x() + item.width(), tl.y() + item.height())


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

    def options(self, name):
        return as_list(find(self.win, name).property("options"))

    def popup_rows(self, name):
        """The visible row labels of an open drop-down, in order, as (item, label)."""
        sel = find(self.win, name)
        bottom = rect(sel)[3]
        labels = [o["label"] for o in self.options(name)]
        rows = []
        for it in walk(self.win.property("contentItem")):
            if it.metaObject().className().startswith("QQuickText") and it.isVisible() and it.property("text") in labels \
                    and rect(it)[1] > bottom - 1:
                rows.append(it)
        rows.sort(key=lambda i: rect(i)[1])
        return rows

    def choose(self, name, option_id):
        sel = find(self.win, name)
        self.click(sel)
        label = next(o["label"] for o in self.options(name) if o["id"] == option_id)
        row = next(r for r in self.popup_rows(name) if r.property("text") == label)
        self.click(row, scroll=False)


class TestSkinList(UI):
    def test_six_skins_with_the_exact_names_in_the_exact_order(self):
        p = self.start()
        skins = list(p["settings_bridge"].skins)
        self.assertEqual([s["name"] for s in skins], ORDER)
        self.assertEqual([s["id"] for s in skins], SKIN_IDS)
        for s in skins:
            self.assertEqual(s["author"], "")                      # no "· EqUpdater" suffix
        self.assertNotIn("minimal", [s["id"] for s in skins])

    def test_the_skin_selector_lists_exactly_those_names_top_to_bottom(self):
        self.start()
        self.open_settings()
        self.assertEqual([o["label"] for o in self.options("skinSelect")], ORDER)
        sel = find(self.win, "skinSelect")
        self.click(sel)
        shown = [r.property("text") for r in self.popup_rows("skinSelect")]
        self.assertEqual(shown, ORDER)
        for old in ("Battle.net", "Turtle WoW", "Minimal Dark", "Minimal Light", "Minimal"):
            self.assertNotIn(old, shown)
        texts = [str(i.property("text")) for i in walk(self.win.property("contentItem")) if i.property("text")]
        self.assertFalse([t for t in texts if "EqUpdater" in t and "·" in t])

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


class TestAppearanceAndLanguage(UI):
    def test_appearance_stacks_skin_over_font_with_matching_selectors(self):
        p = self.start()
        self.open_settings()
        texts = [str(i.property("text")) for i in walk(self.overlay) if i.property("text")]
        self.assertEqual(texts.count("APPEARANCE"), 1)
        skin_sel, font_sel = find(self.win, "skinSelect"), find(self.win, "fontSelect")
        skin_lab = item_with_text(self.overlay, "Skin")
        font_lab = item_with_text(self.overlay, "Font")
        s, f = rect(skin_sel), rect(font_sel)
        self.assertEqual((s[2] - s[0], s[3] - s[1]), (f[2] - f[0], f[3] - f[1]))      # same width and height
        self.assertAlmostEqual(s[0], f[0], delta=0.5)                                   # aligned left
        self.assertGreater(f[1], s[3])                                                  # stacked: Skin above Font
        self.assertAlmostEqual(rect(skin_lab)[0], rect(font_lab)[0], delta=0.5)         # labels aligned
        self.assertLess(s[2] - s[0], 260)                                               # narrower than before
        # nothing explanatory under them
        self.assertNotIn("The language of EqUpdater and of the game.", texts)

    def test_the_flag_images_exist_and_are_real_pictures(self):
        from PIL import Image
        for _c, _l, flag in LANGS:
            path = os.path.join(ROOT, "equpdater", "qtui", "qml", "flags", flag + ".png")
            self.assertTrue(os.path.isfile(path), flag)
            im = Image.open(path)
            self.assertEqual(im.size, (48, 32))
            self.assertGreater(len(set(im.convert("RGB").getdata())), 3, flag)         # not a blank square

    def test_the_language_list_has_the_six_languages_with_their_flags(self):
        p = self.start()
        choices = as_list(p["tweaks"].languageChoices)
        self.assertEqual([(c["code"], c["label"], c["flag"]) for c in choices], LANGS)
        self.assertEqual(sum(1 for c in choices if c["current"]), 1)

    def test_the_flag_shows_on_the_selected_language_and_on_every_option(self):
        p = self.start()
        self.open_settings()

        def flags_under(root):
            return sorted(os.path.basename(i.property("source").toString()) for i in walk(root)
                          if i.metaObject().className() == "QQuickImage" and "flags/" in i.property("source").toString() and i.isVisible())
        sel = find(self.win, "languageSelect")
        self.assertEqual(flags_under(sel), ["gb.png"])                                  # English, selected: its flag
        self.click(sel)
        shown = flags_under(self.win.property("contentItem"))
        for _c, _l, flag in LANGS:
            self.assertIn(flag + ".png", shown, flag)
        self.assertEqual(self.qml_errors(), [])

    def test_the_selector_renders_flags_in_every_skin(self):
        p = self.start()
        self.open_settings()
        for sid in SKIN_IDS:
            p["settings_bridge"].setSkin(sid)
            pump(150)
            sel = find(self.win, "languageSelect")
            self.assertTrue(sel.property("visible"), sid)
            self.assertTrue([i for i in walk(sel) if i.metaObject().className() == "QQuickImage" and i.isVisible()], sid)
        self.assertEqual(self.qml_errors(), [])

    def test_the_tweaks_page_has_no_language_row_and_settings_has_one_selector(self):
        p = self.start()
        ids = [r["id"] for r in p["tweaks"].service.rows()]
        self.assertNotIn("locale", ids)
        self.assertNotIn("Language", [r["label"] for r in p["tweaks"].service.rows()])
        p["app"].tab = "tweaks"
        pump(200)
        texts = [str(i.property("text")) for i in walk(self.win.property("contentItem")) if i.property("text")]
        self.assertNotIn("Language", texts)
        self.open_settings()
        self.assertEqual(len([i for i in walk(self.overlay) if i.objectName() == "languageSelect"]), 1)
        self.assertEqual(len([i for i in walk(self.overlay) if i.property("text") == "LANGUAGE"]), 1)

    def test_the_stored_language_value_is_unchanged_by_the_move(self):
        from equpdater.core import tweaks as T
        self.assertIn("locale", T.TWEAKS_DEFAULTS)                          # same key, same file, same patch
        self.assertEqual(T.TWEAKS_DEFAULTS["locale"], T.DEFAULT_LOCALE)


class TestLowMemoryRenderingInvestigation(unittest.TestCase):
    def test_the_setting_only_ever_changed_the_launchers_own_window(self):
        """The finding that justified removing it: the only code that read the preference chose the
        graphics API of the launcher's own Qt window. Nothing in the game's launch path read it."""
        import re
        offenders = []
        for d, _s, files in os.walk(os.path.join(ROOT, "equpdater")):
            for f in files:
                if f.endswith((".py", ".qml")) and "__pycache__" not in d and "locales" not in d:
                    p = os.path.join(d, f)
                    text = open(p, encoding="utf-8", errors="ignore").read()
                    if re.search(r"softwareRendering|rendererNeedsRestart|get\(\"renderer\"\)", text):
                        offenders.append(p)
        self.assertEqual(offenders, [])
        with open(os.path.join(ROOT, "equpdater", "qtui", "main.py"), encoding="utf-8") as f:
            main = f.read()
        self.assertIn('os.environ.get("EQU3_RENDERER") == "software"', main)        # only a diagnostic switch is left


class TestSettingsStaysOpen(UI):
    def test_selecting_a_skin_font_and_restoring_the_default_never_closes_settings(self):
        p = self.start()
        self.open_settings()
        self.assertTrue(self.overlay.property("visible"))
        for sid in ("minimal-light", "turtle-wow", "classic-2004", "battle-net", "minimal-dark", "bubbles"):
            self.choose("skinSelect", sid)
            self.assertTrue(self.overlay.property("visible"), sid)
            self.assertEqual(p["settings_bridge"].currentSkin, sid)
        for fid in ("arial", "friz", "opendyslexic", "objectsans", ""):
            self.choose("fontSelect", fid)
            self.assertTrue(self.overlay.property("visible"), fid)
            self.assertEqual(p["general"].fontOverride, fid)
        self.choose("skinSelect", "turtle-wow")
        self.click(find(self.win, "restoreSkinButton"))
        self.assertTrue(self.overlay.property("visible"))
        self.assertEqual(p["settings_bridge"].currentSkin, "bubbles")
        self.assertEqual(self.qml_errors(), [])

    def test_the_language_choice_keeps_settings_open(self):
        p = self.start()
        self.open_settings()
        applied = []
        p["tweaks"].service.set_language = lambda code, cb=None: applied.append(code) or True
        self.choose("languageSelect", "deDE")
        self.assertTrue(self.overlay.property("visible"))
        self.assertEqual(applied, ["deDE"])

    def test_checkboxes_keep_settings_open_and_apply_at_once(self):
        p = self.start()
        self.open_settings()
        box = item_with_text(self.overlay, "Animated background")
        before = p["settings_bridge"].animatedBackground
        self.click(box)
        self.assertTrue(self.overlay.property("visible"))
        self.assertNotEqual(p["settings_bridge"].animatedBackground, before)
        self.click(item_with_text(self.overlay, "Pause animation when the window is not focused"))
        self.assertTrue(self.overlay.property("visible"))

    def test_the_scroll_position_survives_a_skin_change(self):
        p = self.start()
        self.win.setProperty("height", 600)                                         # the smallest supported window
        pump(150)
        self.open_settings()
        top = max(1.0, self.flick.property("contentHeight") - self.flick.property("height"))
        self.assertGreater(top, 40)                                                # there is something to scroll
        self.flick.setProperty("contentY", top * 0.6)
        pump(120)
        y = self.flick.property("contentY")
        for sid in ("minimal-light", "turtle-wow", "bubbles"):
            p["settings_bridge"].setSkin(sid)
            pump(250)
            self.assertTrue(self.overlay.property("visible"))
            self.assertGreater(self.flick.property("contentY"), 10, sid)           # not thrown back to the top
            self.assertAlmostEqual(self.flick.property("contentY"), y, delta=60, msg=sid)
        self.choose("skinSelect", "classic-2004")                                 # and through a real click
        self.assertTrue(self.overlay.property("visible"))

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
    FAMILIES = {"": None, "arial": "Arial", "friz": "Friz Quadrata", "opendyslexic": "OpenDyslexic", "objectsans": "Object Sans"}

    def test_every_option_is_drawn_in_its_own_face_whatever_font_is_active(self):
        p = self.start()
        self.open_settings()

        def faces():
            opts = self.options("fontSelect")
            self.assertEqual([o["id"] for o in opts], list(self.FAMILIES))
            sel = find(self.win, "fontSelect")
            self.click(sel)
            rows = {r.property("text"): r.property("font").family() for r in self.popup_rows("fontSelect")}
            self.click(sel)                                                              # close it again
            return {o["id"]: (rows.get(o["label"]), o["family"]) for o in opts}
        first = faces()
        for fid, fam in self.FAMILIES.items():
            if fam:
                self.assertEqual(first[fid], (fam, fam), fid)                           # the label is drawn in that family
        for choice in ("arial", "friz", "opendyslexic", "objectsans", ""):
            p["general"].setFontOverride(choice)
            pump(200)
            self.assertEqual(faces(), first, choice)                                     # no preview changes with the app font

    def test_the_selected_font_label_is_drawn_in_that_font(self):
        p = self.start()
        self.open_settings()
        p["general"].setFontOverride("friz")
        pump(200)
        sel = find(self.win, "fontSelect")
        label = next(i for i in walk(sel) if i.metaObject().className() == "QQuickText" and i.property("text") == "Friz Quadrata")
        self.assertEqual(label.property("font").family(), "Friz Quadrata")

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


class TestShellLayout(UI):
    WIDTHS = (860, 1000, 1280)

    def metrics(self, p):
        theme = p["skin"].theme["layout"]
        return theme["contentMargin"], theme["panelGap"]

    def test_the_outer_margins_are_equal_and_the_gaps_are_the_shared_constant_in_every_skin_and_size(self):
        p = self.start()
        problems = []
        for sid in SKIN_IDS:
            p["settings_bridge"].setSkin(sid)
            for width in self.WIDTHS:
                self.win.setProperty("width", width)
                self.win.setProperty("height", 700)
                pump(150)
                margin, gap = self.metrics(p)
                area, side = rect(find(self.win, "pageArea")), rect(find(self.win, "socialSidebar"))
                nav = find(self.win, "navRow")
                where = f"{sid} {width}"
                left = area[0]                                                           # left edge of the page (Announcements)
                right = width - side[2]                                                  # right edge of the friends list
                if abs(left - right) > 0.5 or abs(left - margin) > 0.5:
                    problems.append(f"{where}: left {left} right {right} margin {margin}")
                if abs((side[0] - area[2]) - gap) > 0.5:
                    problems.append(f"{where}: gap {side[0] - area[2]} != {gap}")
                if nav.property("spacing") != gap:
                    problems.append(f"{where}: navigation spacing {nav.property('spacing')} != panel gap {gap}")
        self.assertEqual(problems, [], "; ".join(problems[:12]))

    def test_the_news_panels_are_one_gap_apart_and_so_are_the_tabs(self):
        p = self.start()
        pump(300)
        for sid in SKIN_IDS:
            p["settings_bridge"].setSkin(sid)
            pump(120)
            margin, gap = self.metrics(p)
            panels = sorted((rect(i) for i in walk(self.win.property("contentItem")) if i.objectName() == "newsPanel" and i.isVisible()),
                            key=lambda r: r[0])
            self.assertEqual(len(panels), 2, sid)
            self.assertAlmostEqual(panels[1][0] - panels[0][2], gap, delta=0.5)
            tabs = sorted((rect(i) for i in walk(find(self.win, "navRow")) if i.metaObject().className().endswith("SkinNavTab") or
                           (i.property("text") in ("NEWS", "TWEAKS", "ADDONS", "MODS", "MPQ") and i.property("badge") is not None)),
                          key=lambda r: r[0])
            for a, b in zip(tabs, tabs[1:]):
                self.assertAlmostEqual(b[0] - a[2], gap, delta=0.5, msg=sid)

    def test_the_sidebar_is_in_its_own_layout_space_and_settings_is_drawn_above_it(self):
        p = self.start()
        side = find(self.win, "socialSidebar")
        area = find(self.win, "pageArea")
        self.assertLessEqual(rect(area)[2], rect(side)[0])                              # the pages end before the friends list
        self.open_settings()
        ov = find(self.win, "settingsOverlay")
        host = ov.parentItem()                                                          # the lazy loader that holds Settings
        self.assertGreater(host.property("z"), side.property("z"))
        self.assertEqual(self.qml_errors(), [])

    def test_no_hard_coded_outer_margin_is_left_in_the_shell(self):
        with open(os.path.join(ROOT, "equpdater", "qtui", "qml", "Main.qml"), encoding="utf-8") as f:
            main = f.read()
        self.assertIn("readonly property real gap: layout.panelGap", main)
        self.assertIn("readonly property real margin: layout.contentMargin", main)
        self.assertNotIn("rightMargin: 14", main)
        self.assertNotIn("sidebar.width + 10", main)
        self.assertIn("spacing: win.gap", main)


class TestSharedRowActions(UI):
    ORDER = ["actionFolder", "actionLink", "actionUninstall", "actionRelease"]
    KINDS = ["icon.folder", "icon.link", "icon.remove", "icon.close"]

    def component(self, **props):
        engine = self.engine
        comp = QQmlComponent(engine, QUrl.fromLocalFile(os.path.join(ROOT, "equpdater", "qtui", "qml", "EqUi", "SkinRowActions.qml")))
        obj = comp.create(engine.rootContext())
        self.assertIsNotNone(obj, comp.errorString())
        for k, v in props.items():
            obj.setProperty(k, v)
        obj.setParentItem(self.win.property("contentItem"))
        self._keep = getattr(self, "_keep", []) + [comp, obj]              # QML objects die with their Python reference
        pump(60)
        return obj

    def buttons(self, comp):
        by = {i.objectName(): i for i in walk(comp) if i.objectName() in self.ORDER}
        return [by[n] for n in self.ORDER]

    def test_the_four_actions_in_the_agreed_order_with_the_agreed_icons(self):
        self.start()
        comp = self.component()
        btns = self.buttons(comp)
        xs = [rect(b)[0] for b in btns]
        self.assertEqual(xs, sorted(xs))                                                # left to right
        self.assertEqual([b.property("kind") for b in btns], self.KINDS)               # folder, link, trash, X
        self.assertEqual([b.property("tip") for b in btns],
                         ["Open folder", "Open repository", "Uninstall", "Stop managing. Your files are kept."])
        self.assertEqual(len({(b.property("width"), b.property("height")) for b in btns}), 1)   # one size
        gaps = {round(xs[i + 1] - rect(btns[i])[2], 2) for i in range(3)}
        self.assertEqual(len(gaps), 1)                                                  # one spacing

    def test_a_disabled_action_stays_in_its_place(self):
        self.start()
        full = self.component()
        base = [rect(b) for b in self.buttons(full)]
        part = self.component(folderEnabled=False, linkEnabled=False, uninstallEnabled=True, releaseEnabled=False)
        shifted = [rect(b) for b in self.buttons(part)]
        self.assertEqual(base, shifted)
        self.assertEqual([b.property("enabled") for b in self.buttons(part)], [False, False, True, False])

    def test_the_icons_exist_in_every_skin(self):
        from equpdater.core import skin as S
        skins = S.discover(os.path.join(ROOT, "skins"))
        for sid, s in skins.items():
            for cid in self.KINDS:
                spec = s.theme["components"][cid]
                self.assertTrue(spec.get("icon"), f"{sid}: {cid}")
                self.assertTrue(os.path.isfile(os.path.join(ROOT, "skins", sid, "icons", os.path.basename(spec["icon"].replace("file:///", "")))
                                               ) or os.path.isfile(spec["icon"].replace("file:///", "")), f"{sid}: {cid}")

    def test_the_three_pages_use_the_one_component_and_no_private_copies(self):
        base = os.path.join(ROOT, "equpdater", "qtui", "qml", "pages")
        for name in ("ModsPage.qml", "AddonRow.qml", "MpqPage.qml"):
            with open(os.path.join(base, name), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("SkinRowActions", name + text)
            self.assertIn("SkinRowActions {", text, name)
            for gone in ('kind: "icon.release"', 'kind: "icon.remove"', 'kind: "icon.link"'):
                self.assertNotIn(gone, text, f"{name} still draws its own {gone}")

    def test_the_addon_rows_use_it_and_enable_only_what_applies(self):
        os.environ["EQU3_FAKE_ADDONS"] = "12"
        self.addCleanup(os.environ.pop, "EQU3_FAKE_ADDONS", None)
        p = self.start()
        p["app"].tab = "addons"
        pump(until=lambda: p["addons"].model.rowCount() > 0, limit=6000)
        pump(400)
        groups = {}
        for i in walk(self.win.property("contentItem")):
            if i.objectName() in self.ORDER and i.isVisible():
                groups.setdefault(rect(i)[1] // 4, []).append(i)
        self.assertGreater(len(groups), 2)
        for row_buttons in groups.values():
            self.assertEqual([b.objectName() for b in sorted(row_buttons, key=lambda b: rect(b)[0])], self.ORDER)

    def test_open_folder_only_opens_folders_inside_the_game_folder(self):
        from unittest import mock
        p = self.start()
        game = tempfile.mkdtemp(prefix="equ-game-")
        self.addCleanup(shutil.rmtree, game, True)
        os.makedirs(os.path.join(game, "Interface", "AddOns", "pfUI"))
        p["settings"].set("game_dir", game)
        outside = tempfile.mkdtemp(prefix="equ-out-")
        self.addCleanup(shutil.rmtree, outside, True)
        with mock.patch("equpdater.platforms.open_folder") as opened:
            self.assertTrue(p["app"].openFolder(os.path.join(game, "Interface", "AddOns", "pfUI")))
            self.assertFalse(p["app"].openFolder(outside))
            self.assertFalse(p["app"].openFolder(os.path.join(game, "..")))
            self.assertFalse(p["app"].openFolder(os.path.join(game, "missing")))
        self.assertEqual(opened.call_count, 1)

    def test_uninstall_and_stop_managing_mean_different_things(self):
        """Uninstall deletes the installed content; Stop managing only lets go of it. The wording says so and the
        existing service tests (tests/test_v3_ops.py and test_v3_addons.py) cover the files."""
        self.start()
        comp = self.component()
        tips = {b.objectName(): b.property("tip") for b in self.buttons(comp)}
        self.assertEqual(tips["actionUninstall"], "Uninstall")
        self.assertIn("files are kept", tips["actionRelease"])


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
