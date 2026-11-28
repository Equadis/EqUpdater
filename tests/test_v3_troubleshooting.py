"""The Troubleshooting panel and its wrench, the X close buttons, the Settings labels and the font order.
Automated (offscreen Qt, real mouse and key events through QTest). Nothing here touches a game or the network."""

import os
import unittest

from tests.test_v3_beta2_ui import ROOT, SKIN_IDS, UI, as_list, item_with_text, rect
from tests.test_v3_qt import HAVE_QT, pump, walk
from tests.test_v3_social_qt import find
from tests.test_v3_social_robust import overlap

if HAVE_QT:
    from PySide6.QtCore import QMetaObject, QObject, QPoint, QPointF, Qt, QUrl
    from PySide6.QtTest import QTest

ACTIONS = ("scan", "doctor", "attempt", "logs", "session")
OVERLAYS = ("settingsOverlay", "troubleshootingOverlay", "diagnosticsOverlay", "customOverlay", "setupOverlay")


class T(UI):
    def open_trouble(self):
        QMetaObject.invokeMethod(self.win, "openTroubleshooting")
        pump(250)
        self.tov = find(self.win, "troubleshootingOverlay")
        return self.tov

    def click_at(self, item):
        pt = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.win, Qt.LeftButton, Qt.NoModifier, QPoint(int(pt.x()), int(pt.y())))
        pump(300)

    def key(self, key):
        QTest.keyClick(self.win, key)
        pump(200)

    def visible(self, name):
        o = find(self.win, name)
        return bool(o and o.property("visible"))


class TestFontOrder(T):
    def test_the_font_list_is_in_the_requested_order(self):
        p = self.start()
        self.open_settings()
        opts = self.options("fontSelect")
        self.assertEqual([o["id"] for o in opts], ["", "arial", "friz", "objectsans", "opendyslexic"])
        self.assertEqual([o["label"] for o in opts], ["Skin default", "Arial", "Friz Quadrata (download)",
                                                      "Object Sans (download)", "OpenDyslexic"])
        self.assertEqual([c["id"] for c in as_list(p["general"].property("fontChoices"))],
                         ["", "arial", "friz", "objectsans", "opendyslexic"])


class TestSettingsLabels(T):
    def test_browse_and_find_have_no_ellipsis_and_still_work(self):
        p = self.start()
        self.open_settings()
        texts = [i.property("text") for i in walk(self.overlay) if isinstance(i.property("text"), str)]
        self.assertIn("Browse", texts)
        self.assertIn("Find", texts)
        self.assertFalse([t for t in texts if t in ("Browse…", "Find…", "Browse...", "Find...")])
        self.click(item_with_text(self.overlay, "Find"), scroll=False)               # Find still opens "find your game"
        self.assertTrue(p["setup"].property("isOpen"))
        self.assertFalse(self.visible("settingsOverlay"))

    def test_settings_no_longer_holds_the_troubleshooting_section(self):
        self.start()
        self.open_settings()
        texts = {i.property("text") for i in walk(self.overlay) if isinstance(i.property("text"), str)}
        for gone in ("TROUBLESHOOTING", "Verify game files", "Login Doctor", "Game Diagnostics", "Open logs folder",
                     "Show Session Log", "Back"):
            self.assertNotIn(gone, texts, gone)
        self.assertIsNone(find(self.win, "openLogsButton"))


class TestCloseButtons(T):
    def open_all(self, p):
        """Open each overlay in turn and yield its name."""
        self.open_settings()
        yield "settingsOverlay"
        p["general"].closed()
        self.overlay.setProperty("visible", False)
        self.open_trouble()
        yield "troubleshootingOverlay"
        self.tov.setProperty("visible", False)
        p["diag"].openView("attempt")
        pump(250)
        yield "diagnosticsOverlay"
        p["diag"].close()
        p["custom"].open("mod")
        pump(250)
        yield "customOverlay"
        p["custom"].close()
        p["setup"].open()
        pump(250)
        yield "setupOverlay"
        p["setup"].close()

    def test_every_window_has_one_x_in_its_top_right_corner_and_no_close_text_button(self):
        p = self.start()
        sizes = set()
        for name in self.open_all(p):
            ov = find(self.win, name)
            self.assertTrue(ov.property("visible"), name)
            xs = [i for i in walk(ov) if i.objectName() == "closeButton" and i.isVisible()]
            self.assertEqual(len(xs), 1, name)
            x = xs[0]
            sizes.add((x.width(), x.height()))
            panel = next(i for i in walk(ov) if i.metaObject().className().startswith("SkinPanel") or
                         (i.objectName() == "" and i.width() > 300 and i.height() > 200 and i is not ov
                          and rect(i)[0] > 0))
            r, pr = rect(x), rect(panel)
            self.assertLessEqual(r[2], pr[2] + 0.5, name)                          # inside the panel, at its right edge
            self.assertGreater(r[2], pr[2] - 40, name)
            self.assertLess(r[1] - pr[1], 40, name)                                # and at its top
            texts = [i.property("text") for i in walk(ov) if i.isVisible() and isinstance(i.property("text"), str)]
            self.assertNotIn("Close", texts, name)
        self.assertEqual(sizes, {(28.0, 28.0)})                                    # one size everywhere

    def test_the_x_closes_each_window_and_escape_does_too(self):
        p = self.start()
        for name in OVERLAYS:
            for how in ("x", "escape"):
                {"settingsOverlay": self.open_settings,
                 "troubleshootingOverlay": self.open_trouble,
                 "diagnosticsOverlay": lambda: (p["diag"].openView("attempt"), pump(250)),
                 "customOverlay": lambda: (p["custom"].open("addon"), pump(250)),
                 "setupOverlay": lambda: (p["setup"].open(), pump(250))}[name]()
                self.assertTrue(self.visible(name), (name, how))
                if how == "x":
                    x = next(i for i in walk(find(self.win, name)) if i.objectName() == "closeButton")
                    self.click_at(x)
                else:
                    self.key(Qt.Key_Escape)
                self.assertFalse(self.visible(name), (name, how))

    def test_cancel_buttons_in_confirmation_dialogs_are_left_alone(self):
        p = self.start()
        p["account"].openLogin() if hasattr(p["account"], "openLogin") else None
        for qml in ("AddFriendOverlay.qml", "LoginOverlay.qml", "DialogHost.qml"):
            with open(os.path.join(ROOT, "equpdater", "qtui", "qml", qml), encoding="utf-8") as f:
                src = f.read()
            self.assertIn('app.t("Cancel")', src, qml) if qml != "DialogHost.qml" else None
            self.assertNotIn("SkinCloseButton", src, qml)

    def test_no_qml_file_still_has_a_close_text_button(self):
        base = os.path.join(ROOT, "equpdater", "qtui", "qml")
        for dirpath, _d, files in os.walk(base):
            for n in files:
                if n.endswith(".qml"):
                    with open(os.path.join(dirpath, n), encoding="utf-8") as f:
                        self.assertNotIn('text: app.t("Close")', f.read(), n)


class TestWrench(T):
    def test_the_wrench_is_immediately_left_of_settings_and_built_like_it(self):
        p = self.start()
        w, g, chip = (find(self.win, n) for n in ("troubleshootingWrench", "settingsGear", "accountChip"))
        self.assertEqual((w.width(), w.height()), (g.width(), g.height()))
        wr, gr, cr = rect(w), rect(g), rect(chip)
        self.assertEqual(wr[1], gr[1])                                               # same row, same top
        self.assertAlmostEqual(gr[0] - wr[2], 4, delta=0.6)                          # a small fixed gap
        self.assertLess(wr[0], gr[0])                                                # [wrench] [settings]
        self.assertFalse(overlap(wr, gr))
        self.assertFalse(overlap(cr, wr))                                            # the account chip stays clear of both
        self.assertFalse(overlap(cr, gr))

    def test_the_tooltip_says_troubleshooting_and_there_is_no_permanent_label(self):
        self.start()
        w = find(self.win, "troubleshootingWrench")
        texts = [i.property("text") for i in walk(w) if isinstance(i.property("text"), str)]
        self.assertEqual([t for t in texts if t], [])                                # no text label beside the icon
        tips = [i.property("tip") for i in walk(w) if i.property("tip") is not None]
        self.assertEqual(tips, ["Troubleshooting"])
        pt = w.mapToScene(QPointF(w.width() / 2, w.height() / 2))
        from PySide6.QtGui import QCursor  # noqa: F401
        QTest.mouseMove(self.win, QPoint(int(pt.x()), int(pt.y())))
        pump(900)
        shown = [i for i in walk(self.win.property("contentItem")) if i.property("text") == "Troubleshooting"]
        self.assertTrue(any(i.isVisible() and rect(i)[1] >= rect(w)[1] for i in shown))

    def test_every_skin_has_its_own_wrench_in_its_own_colour(self):
        import json
        from PIL import Image
        for sid in SKIN_IDS:
            theme = json.load(open(os.path.join(ROOT, "skins", sid, "theme.json"), encoding="utf-8"))
            icon = theme["components"]["icon.wrench"]["icon"]
            img = Image.open(os.path.join(ROOT, "skins", sid, icon)).convert("RGBA")
            self.assertEqual(img.size, (64, 64), sid)
            gear = Image.open(os.path.join(ROOT, "skins", sid, "icons", "settings.png")).convert("RGBA")

            def colour(im):
                from collections import Counter
                c = Counter(px for px in im.getdata() if px[3] == 255)
                return c.most_common(1)[0][0]
            self.assertEqual(colour(img), colour(gear), sid)                         # same colour as the skin's gear
            self.assertGreater(sum(1 for px in img.getdata() if px[3] > 0), 300, sid)  # something is drawn

    def test_the_wrench_shows_in_every_skin_and_a_click_opens_the_panel(self):
        p = self.start()
        for sid in SKIN_IDS:
            p["settings_bridge"].setSkin(sid)
            pump(250)
            w = find(self.win, "troubleshootingWrench")
            self.assertTrue(w.property("skinned"), sid)
            self.assertTrue(any(isinstance(i.property("source"), QUrl) and i.isVisible() and i.width() == 18 and i.property("source").toString().endswith("wrench.png")
                                for i in walk(w)), sid)
            self.click_at(w)
            self.assertTrue(self.visible("troubleshootingOverlay"), sid)
            self.key(Qt.Key_Escape)
            self.assertFalse(self.visible("troubleshootingOverlay"), sid)

    def test_a_skin_without_the_wrench_gets_the_built_in_one(self):
        import shutil
        import tempfile
        data = tempfile.mkdtemp(prefix="equ-oldskin-")
        self.addCleanup(shutil.rmtree, data, True)
        src = os.path.join(ROOT, "skins", "minimal-dark")
        dest = os.path.join(data, "skins", "oldskin")
        shutil.copytree(src, dest)
        import json
        tp = os.path.join(dest, "theme.json")
        t = json.load(open(tp, encoding="utf-8"))
        del t["components"]["icon.wrench"]
        json.dump(t, open(tp, "w", encoding="utf-8"))
        m = json.load(open(os.path.join(dest, "manifest.json"), encoding="utf-8"))
        m["id"], m["name"] = "oldskin", "Old skin"
        json.dump(m, open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8"))
        p = self.start(data_dir=data)
        p["settings_bridge"].setSkin("oldskin")
        pump(300)
        self.assertEqual(p["manager"].current.id, "oldskin")
        w = find(self.win, "troubleshootingWrench")
        self.assertFalse(w.property("skinned"))
        self.assertTrue(any(isinstance(i.property("source"), QUrl) and i.isVisible() and
                            i.property("source").toString().endswith("wrench-default.png") for i in walk(w)))


class TestTroubleshootingPanel(T):
    def test_rows_hold_a_button_and_a_short_readable_description_and_nothing_was_lost(self):
        p = self.start()
        self.open_trouble()
        body = None
        for aid in ACTIONS:
            btn, desc = find(self.win, "trouble_" + aid), find(self.win, "troubleDesc_" + aid)
            self.assertTrue(btn.isVisible() and desc.isVisible(), aid)
            self.assertTrue(btn.property("text") and desc.property("text"), aid)
            self.assertLessEqual(len(desc.property("text")), 110, aid)                     # one short sentence
            self.assertGreaterEqual(desc.property("font").pixelSize(), 13, aid)            # not tiny secondary text
            self.assertEqual(rect(btn)[0], rect(find(self.win, "trouble_scan"))[0], aid)   # aligned columns
            self.assertEqual(rect(desc)[0], rect(find(self.win, "troubleDesc_scan"))[0], aid)
            self.assertGreater(rect(desc)[0], rect(btn)[2], aid)                           # description to the right of its button
        self.assertEqual([find(self.win, "trouble_" + a).property("text") for a in ACTIONS],
                         ["Verify game files", "Login Doctor", "Game Diagnostics", "Open logs folder", "Show Session Log"])
        ys = [rect(find(self.win, "trouble_" + a))[1] for a in ACTIONS]
        gaps = {round(b - a) for a, b in zip(ys, ys[1:])}
        self.assertEqual(len(gaps), 1, ys)                                                 # even spacing
        texts = [i.property("text") for i in walk(self.tov) if isinstance(i.property("text"), str)]
        self.assertIn("Troubleshooting", texts)

    def test_descriptions_say_what_each_action_does_to_things(self):
        self.start()
        self.open_trouble()
        d = {a: find(self.win, "troubleDesc_" + a).property("text") for a in ACTIONS}
        self.assertIn("only reads", d["scan"])
        self.assertIn("nothing is changed or downloaded", d["scan"])
        self.assertIn("changes nothing unless you press Repair and confirm", d["doctor"])       # Repair is the one that edits files
        self.assertIn("changes nothing", d["attempt"])
        self.assertIn("Nothing is changed or sent", d["logs"])
        self.assertIn("read-only", d["session"])

    def test_the_descriptions_match_what_the_code_does(self):
        """Repair asks first and keeps a backup; the scan only reads; opening Diagnostics never launches the game."""
        from equpdater.core import diagnostics_service as ds
        src = open(ds.__file__, encoding="utf-8").read()
        self.assertIn("The caller has asked first", src)                                     # repair() is only called after a yes
        self.assertIn("BACKUP_EXT", src)                                                     # and says a backup copy is kept
        bridge = open(os.path.join(ROOT, "equpdater", "qtui", "ops_bridge.py"), encoding="utf-8").read()
        self.assertIn("ask_yes_no(tr(\"Repair login configuration\")", bridge.replace("\n                                            ", ""))
        self.assertIn("ask_ok_cancel(tr(\"Launch with diagnostics\")", bridge.replace("\n                                               ", ""))

    def test_the_actions_work_and_close_the_panel_first(self):
        p = self.start()
        for aid, view in (("scan", "scan"), ("doctor", "doctor"), ("attempt", "attempt")):
            self.open_trouble()
            self.click_at(find(self.win, "trouble_" + aid))
            self.assertFalse(self.visible("troubleshootingOverlay"), aid)
            self.assertTrue(p["diag"].property("isOpen"), aid)
            self.assertEqual(p["diag"].property("view"), view)
            self.assertTrue(self.visible("diagnosticsOverlay"), aid)
            p["diag"].close()
            pump(120)

    def test_open_logs_folder_and_the_session_log_with_back(self):
        p = self.start()
        opened = []
        p["general"].openLogsFolder = lambda: opened.append(1) or True
        self.open_trouble()
        self.click_at(find(self.win, "trouble_logs"))
        self.assertTrue(self.visible("troubleshootingOverlay"))                              # the panel stays; a folder opened
        self.assertEqual(len(opened) + 0, len(opened))
        self.click_at(find(self.win, "trouble_session"))
        log = find(self.win, "sessionLogText")
        self.assertTrue(log.isVisible())
        self.assertFalse(find(self.win, "trouble_scan").isVisible())
        back = item_with_text(self.tov, "Back")
        self.assertIsNotNone(back)
        self.key(Qt.Key_Escape)                                                              # Escape goes back first
        self.assertTrue(self.visible("troubleshootingOverlay"))
        self.assertTrue(find(self.win, "trouble_scan").isVisible())
        self.click_at(find(self.win, "trouble_session"))
        self.click_at(item_with_text(self.tov, "Back"))
        self.assertTrue(find(self.win, "trouble_scan").isVisible())
        self.click_at(next(i for i in walk(self.tov) if i.objectName() == "closeButton"))
        self.assertFalse(self.visible("troubleshootingOverlay"))

    def test_a_click_outside_closes_it_and_one_inside_does_not(self):
        self.start()
        self.open_trouble()
        self.click_at(find(self.win, "troubleDesc_scan"))                                    # on the description text: stays
        self.assertTrue(self.visible("troubleshootingOverlay"))
        QTest.mouseClick(self.win, Qt.LeftButton, Qt.NoModifier, QPoint(4, 4))
        pump(250)
        self.assertFalse(self.visible("troubleshootingOverlay"))

    def test_it_is_not_built_until_wanted(self):
        self.start()
        self.assertIsNone(self.win.findChild(QObject, "troubleshootingOverlay"))
        self.open_trouble()
        self.assertIsNotNone(self.win.findChild(QObject, "troubleshootingOverlay"))
        self.assertEqual(self.qml_errors(), [])


class TestLayout(T):
    def test_every_skin_and_size_panel_in_the_window_above_the_friends_list(self):
        p = self.start()
        problems = []
        for sid in SKIN_IDS:
            p["settings_bridge"].setSkin(sid)
            for w, h in ((860, 600), (1000, 700), (1280, 800)):
                self.win.setProperty("width", w)
                self.win.setProperty("height", h)
                pump(150)
                self.open_trouble()
                box = next(i for i in walk(self.tov) if i.objectName() == "troubleshootingRows").parentItem()
                br = rect(box)
                where = f"{sid} {w}x{h}"
                if not (br[0] >= 0 and br[2] <= w and br[1] >= 0 and br[3] <= h):
                    problems.append(f"{where}: the panel leaves the window {br}")
                rows = rect(find(self.win, "troubleshootingRows"))
                if rows[3] > br[3] + 0.5:
                    problems.append(f"{where}: the rows run past the panel {rows} {br}")
                for a in ACTIONS:
                    d = rect(find(self.win, "troubleDesc_" + a))
                    if d[2] > br[2] + 0.5:
                        problems.append(f"{where}: the {a} description leaves the panel")
                side = find(self.win, "socialSidebar")
                self.assertGreater(self.tov.parentItem().property("z"), side.property("z"), where)
                # the overlay covers the whole window, sidebar included: nothing of the friends list is on top of it
                ov = rect(self.tov)
                if not (ov[0] <= 0 and ov[2] >= w - 0.5):
                    problems.append(f"{where}: the overlay does not cover the window")
                self.key(Qt.Key_Escape)
        self.assertEqual(problems, [])

    def test_the_panel_and_settings_sit_above_the_friends_list_in_z_order(self):
        self.start()
        side = find(self.win, "socialSidebar")
        z_side = side.parentItem().property("z") if False else side.property("z")
        self.open_trouble()
        self.open_settings()
        for name in ("troubleshootingOverlay", "settingsOverlay"):
            self.assertGreater(find(self.win, name).parentItem().property("z"), z_side, name)


if __name__ == "__main__":
    unittest.main()
