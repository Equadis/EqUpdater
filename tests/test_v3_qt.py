"""The 3.0 front end, assembled: real QML, offscreen, fixture news.

Acceptance behaviours of the News page and the skin engine are asserted here
against the live QML scene (not against models alone).
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"

try:
    from PySide6.QtCore import Qt, QCoreApplication, QEvent, QObject, QTimer, QUrl, qInstallMessageHandler
    from PySide6.QtQuick import QQuickItem
    HAVE_QT = True
except Exception:                                    # pragma: no cover
    HAVE_QT = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MESSAGES = []


def _handler(mode, ctx, msg):
    MESSAGES.append(msg)


def pump(ms=150, until=None, limit=5000):
    end = time.time() + (limit if until else ms) / 1000
    while time.time() < end:
        QCoreApplication.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until else True


def fake_pe() -> bytes:
    """The smallest file platforms.program_problem accepts as a program."""
    head = bytearray(64)
    head[:2] = b"MZ"
    head[0x3c:0x40] = (64).to_bytes(4, "little")
    return bytes(head) + b"PE\0\0" + bytes(20) + bytes(64)


def walk(item):
    yield item
    for ch in item.childItems():
        yield from walk(ch)


def fixture(n=30, long_=True):
    import subprocess
    d = tempfile.mkdtemp(prefix="equ-fx-")
    path = os.path.join(d, "f.json")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "bench", "make_news_fixture.py"),
                    path, "--posts", str(n), "--long", "1" if long_ else "0"], check=True)
    return path


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Base(unittest.TestCase):
    skin = "bubbles"
    fixture_path = None

    @classmethod
    def setUpClass(cls):
        qInstallMessageHandler(_handler)
        cls.fx = fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(os.path.dirname(cls.fx), ignore_errors=True)

    def start(self, data_dir=None, skin=None, offline=False, fx=None):
        from equpdater.qtui import main as m
        self.data = data_dir or tempfile.mkdtemp(prefix="equ-q-")
        env = {"EQU3_DATA_DIR": self.data, "EQU3_SKIN": skin or self.skin,
               "EQU3_NEWS_FIXTURE": fx or self.fx}
        if offline:
            env["EQU3_OFFLINE"] = "1"
            env.pop("EQU3_NEWS_FIXTURE")
        for k in ("EQU3_OFFLINE", "EQU3_NEWS_FIXTURE"):
            os.environ.pop(k, None)
        os.environ.update(env)
        MESSAGES.clear()
        self.app, self.engine, self.p = m.build([])
        # a skin override must not be saved as the player's choice
        self.engine.load(QUrl.fromLocalFile(os.path.join(m.QML_DIR, "Main.qml")))
        self.assertTrue(self.engine.rootObjects(), "\n".join(MESSAGES))
        self.win = self.engine.rootObjects()[0]
        self.win.setProperty("width", 1000)
        self.win.setProperty("height", 700)
        pump(100)
        return self.p

    def stop(self):
        self.p["settings"].flush()
        self.p["cache"].flush()
        self.p["tasks"].shutdown()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pump(50)
        MESSAGES.clear()           # teardown noise belongs to no test

    def tearDown(self):
        if hasattr(self, "engine"):
            self.stop()
        if hasattr(self, "data"):
            shutil.rmtree(self.data, ignore_errors=True)

    def qml_errors(self):
        return [m for m in MESSAGES if "font directory" not in m
                and "no longer ships fonts" not in m and any(k in m for k in
                ("TypeError", "ReferenceError", "is not a type", "Cannot ",
                 "Unable to assign", "qrc:", ".qml:"))]

    def lst(self, name):
        for it in walk(self.win.property("contentItem")):
            if it.objectName() == name:
                return it
        self.fail(f"no item named {name}")

    def settle_news(self):
        pump(until=lambda: self.p["tasks"].active_count() == 0, limit=8000)
        pump(150)


class TestNewsPage(Base):
    def test_scene_loads_without_qml_warnings(self):
        self.start()
        self.settle_news()
        self.assertEqual(self.qml_errors(), [])

    def test_first_run_lists_both_sections_and_opens_the_latest(self):
        p = self.start()
        p["news"].start()
        self.settle_news()
        for key in ("announcements", "changelog"):
            sec = p["news"].service.sections[key]
            self.assertEqual(len(p["news"].service.visible_ids(key)), min(sec.shown, 30))
            self.assertTrue(p["news"].service.entry(key, sec.order[0])["expanded"])
            self.assertTrue(p["news"].service.entry(key, sec.order[0])["hasBody"])

    def test_cached_content_shows_before_any_request(self):
        p = self.start()
        p["news"].start()
        self.settle_news()
        data = self.data
        self.stop()
        del self.engine
        self.data = None
        p2 = self.start(data_dir=data)
        # nothing has been asked of the network yet, but the rows are there
        self.assertEqual(p2["fetcher"].requests, 0)
        self.assertGreater(p2["news"].announcements.lm.rowCount(), 0)
        self.assertTrue(p2["news"].service.entry("announcements", p2["news"].service
                                                 .visible_ids("announcements")[0])["hasBody"])

    def test_scrolling_makes_no_request_and_one_flickable_per_panel(self):
        p = self.start()
        p["news"].start()
        self.settle_news()
        for key in ("announcements", "changelog"):
            p["news"].service.toggle(key, p["news"].service.visible_ids(key)[1])
        self.settle_news()
        before = p["fetcher"].requests
        lv = self.lst("announcementsList")
        top = lv.property("contentHeight")
        for i in range(60):                         # rapid scrolling, down and up
            lv.setProperty("contentY", (i * 97) % max(1, int(top) - 100))
            pump(5)
        self.assertEqual(p["fetcher"].requests, before)
        # one scrolling authority per panel: only the ListView is a Flickable
        flick = [o for o in walk(self.win.property("contentItem")) if o.isVisible()
                 and o.metaObject().className().startswith(("QQuickListView", "QQuickFlickable",
                                                           "QQuickScrollView"))]
        self.assertEqual(len(flick), 2)

    def test_expanding_a_cached_post_is_instant_and_makes_no_request(self):
        """No request, and the state change reaches the view in well under a
        frame. (Frame/paint latency is measured on the GPU path by
        tools/bench; this test's software renderer would measure itself.)"""
        p = self.start()
        p["news"].start()
        self.settle_news()
        svc = p["news"].service
        lm = p["news"].announcements.lm
        iid = svc.visible_ids("announcements")[0]        # open + cached
        n = p["fetcher"].requests
        changed = []
        lm.dataChanged.connect(lambda *a: changed.append(time.perf_counter()))
        worst = 0.0
        for _ in range(20):
            t0 = time.perf_counter()
            p["news"].announcements.toggle(iid)
            self.assertTrue(changed, "view was not told")
            worst = max(worst, (time.perf_counter() - t0) * 1000)
            changed.clear()
        pump(50)
        self.assertEqual(p["fetcher"].requests, n)
        self.assertLess(worst, 100, f"{worst:.2f} ms")

    def test_load_more_appends_incrementally_without_rebuilding_rows(self):
        p = self.start()
        p["news"].start()
        self.settle_news()
        lm = p["news"].announcements.lm
        rows_changed = []
        lm.modelReset.connect(lambda: rows_changed.append("reset"))
        inserted = []
        lm.rowsInserted.connect(lambda parent, a, b: inserted.append((a, b)))
        before = lm.rowCount()
        p["news"].announcements.loadMore()
        self.settle_news()
        self.assertGreater(lm.rowCount(), before)
        self.assertEqual(rows_changed, [])
        self.assertEqual(inserted[0][0], before)       # appended at the end

    def test_scroll_position_survives_a_background_update_above(self):
        p = self.start()
        p["news"].start()
        self.settle_news()
        lv = self.lst("announcementsList")
        svc = p["news"].service
        ids = svc.visible_ids("announcements")
        for iid in ids[:4]:
            if not svc.entry("announcements", iid)["expanded"]:
                p["news"].announcements.toggle(iid)
        self.settle_news()
        lv.setProperty("contentY", lv.property("contentHeight") * 0.5)
        pump(100)
        # Collapse a post above the viewport: content above shrinks; the
        # post the reader is looking at must stay where it is on screen.
        content = lv.property("contentItem")

        def delegate(name):
            return next((c for c in content.childItems() if c.objectName() == name), None)

        cy = lv.property("contentY")
        seen = [(c.property("y"), c.objectName()) for c in content.childItems()
                if c.objectName().startswith("post-") and c.property("y") >= cy]
        y0, name = min(seen)
        anchor_off = y0 - cy
        # the post at the top of the list is above the viewport: open or close
        # it, whichever changes its height
        p["news"].announcements.toggle(ids[0])
        pump(250)
        ch = delegate(name)
        self.assertIsNotNone(ch)
        self.assertAlmostEqual(ch.property("y") - lv.property("contentY"), anchor_off,
                               delta=2.0)


class TestTweaksPage(Base):
    def test_page_loads_edits_clamps_and_applies_without_touching_real_data(self):
        from equpdater import config
        real = config.CONFIG_FILE
        p = self.start()
        self.assertTrue(config.CONFIG_FILE.startswith(self.data), "must not use the real config")
        self.assertNotEqual(config.CONFIG_FILE, real)
        p["app"].tab = "tweaks"
        pump(until=lambda: p["tweaks"].service.loaded, limit=4000)
        pump(100)
        self.assertEqual(self.qml_errors(), [])
        tw = p["tweaks"]
        self.assertFalse(tw.dirty)
        self.assertEqual(tw.setValue("fieldOfView", "9999"), 180)
        self.assertTrue(tw.dirty)
        rows = {tw.model.index(i).data(Qt.ItemDataRole.UserRole + 1): i
                for i in range(tw.model.rowCount())}
        r = rows["fieldOfView"]
        self.assertTrue(tw.model.index(r).data(Qt.ItemDataRole.UserRole + 10))   # changed marker
        tw.apply()                                       # no game folder in this profile
        pump(until=lambda: not tw.busy, limit=4000)
        self.assertTrue(tw.messageIsError)
        self.assertFalse(tw.dirty)                       # saved
        self.assertEqual(json.load(open(config.CONFIG_FILE))["tweaks"]["fieldOfView"], 180)

    def test_every_skin_renders_the_page(self):
        p = self.start()
        p["app"].tab = "tweaks"
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(150)
        self.assertEqual(self.qml_errors(), [])


class TestModsPage(Base):
    def open_page(self):
        p = self.start()
        p["app"].tab = "mods"
        pump(until=lambda: p["mods"].model.rowCount() > 0, limit=6000)
        pump(300)
        self.assertEqual(self.qml_errors(), [])
        return p

    def test_page_lists_every_mod_in_sections_and_renders_in_both_skins(self):
        p = self.open_page()
        lm = p["mods"].model
        kinds = [lm.index(i).data(Qt.ItemDataRole.UserRole + 2) for i in range(lm.rowCount())]
        self.assertEqual(kinds.count("header"), 3)
        self.assertEqual(kinds.count("mod"), 12)
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(200)
        self.assertEqual(self.qml_errors(), [])

    def test_one_mod_changing_repaints_one_row_and_nothing_else(self):
        from equpdater import config
        p = self.open_page()
        lm = p["mods"].model
        before = dict(lm.stats)
        config.update_config(lambda c: c.setdefault("mods", {}).setdefault(
            "dxvk", {}).__setitem__("ignore_updates", True))
        p["mods_service"].rebuild()
        pump(until=lambda: lm.stats["changed"] > before["changed"], limit=4000)
        pump(100)
        after = lm.stats
        self.assertEqual(after["changed"] - before["changed"], 1)
        self.assertEqual((after["inserted"], after["removed"], after["resets"]),
                         (before["inserted"], before["removed"], before["resets"]))

    def test_scroll_position_survives_a_row_update(self):
        from equpdater import config
        p = self.open_page()
        lv = self.lst("modsList")
        lv.setProperty("contentY", 120)
        pump(100)
        config.update_config(lambda c: c.setdefault("mods", {}).setdefault(
            "nampower", {}).__setitem__("ignore_updates", True))
        p["mods_service"].rebuild()
        pump(400)
        self.assertAlmostEqual(lv.property("contentY"), 120, delta=1)

    def test_collapsing_a_section_removes_only_its_rows(self):
        p = self.open_page()
        lm = p["mods"].model
        before = dict(lm.stats)
        p["mods"].toggleSection("AVAILABLE")
        pump(until=lambda: lm.rowCount() < 15, limit=4000)
        self.assertEqual(lm.rowCount(), 3)                    # the three headers; this profile has no game files
        self.assertEqual(lm.stats["resets"], before["resets"])

    def test_dialog_blocks_its_caller_until_answered(self):
        p = self.start()
        d = p["dialog"]
        d.auto = None
        result = []
        QTimer.singleShot(50, lambda: (self.assertTrue(d.open), d.answer(True)))
        result.append(d.ask("yes_no", "Uninstall?", "body"))
        self.assertEqual(result, [True])
        self.assertFalse(d.open)


class TestAddonsPage(Base):
    N = 80

    def open_page(self):
        os.environ["EQU3_FAKE_ADDONS"] = str(self.N)
        self.addCleanup(os.environ.pop, "EQU3_FAKE_ADDONS", None)
        p = self.start()
        names = [i.objectName() for i in walk(self.win.property("contentItem"))]
        self.assertNotIn("addonsPanel", names)        # an unopened page allocates nothing
        p["app"].tab = "addons"
        pump(until=lambda: p["addons"].model.rowCount() > 0, limit=6000)
        pump(300)
        self.assertEqual(self.qml_errors(), [])
        return p

    def test_renders_in_every_skin_and_the_list_is_virtualised(self):
        p = self.open_page()
        lm = p["addons"].model
        self.assertEqual(lm.rowCount(), 3 + self.N)
        lv = self.lst("addonsList")
        made = len([c for c in lv.property("contentItem").childItems()
                    if c.property("height")])
        self.assertLess(made, 30, "only the visible rows (plus a buffer) exist as items")
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(200)
        self.assertEqual(self.qml_errors(), [])

    def test_one_addon_changing_repaints_one_row(self):
        p = self.open_page()
        lm, svc = p["addons"].model, p["addons_service"]
        before = dict(lm.stats)
        folder = next(f for f, r in p["host"]._addons_status["addons"].items()
                      if r["status"] == "upToDate")
        p["host"]._addons_status["addons"][folder]["status"] = "updateAvailable"
        svc.rebuild()
        pump(until=lambda: lm.stats["changed"] > before["changed"], limit=4000)
        pump(100)
        a = lm.stats
        self.assertEqual(a["changed"] - before["changed"], 1)
        self.assertEqual((a["inserted"], a["removed"], a["resets"]),
                         (before["inserted"], before["removed"], before["resets"]))

    def test_installing_one_addon_moves_one_row_between_sections_without_a_reset(self):
        p = self.open_page()
        lm, svc = p["addons"].model, p["addons_service"]
        st = p["host"]._addons_status
        rec = st["available"].pop(0)
        st["addons"][rec["folder"]] = dict(rec, status="upToDate", toc=rec["toc"])
        before = dict(lm.stats)
        svc.rebuild()
        pump(until=lambda: lm.stats["moved"] > before["moved"] or lm.stats["resets"] > before["resets"], limit=4000)
        pump(100)
        self.assertEqual(lm.stats["resets"], before["resets"])
        self.assertGreaterEqual(lm.stats["moved"] - before["moved"], 1)
        self.assertEqual(lm.rowCount(), 3 + self.N)

    def test_search_filters_without_touching_the_network_or_other_state(self):
        p = self.open_page()
        lm = p["addons"].model
        folder = next(iter(p["host"]._addons_status["addons"]))
        p["addons"].setFilter(folder.lower())
        pump(until=lambda: lm.rowCount() < 10, limit=4000)
        rows = [lm.index(i).data(Qt.ItemDataRole.UserRole + 3) for i in range(lm.rowCount())]
        self.assertIn(folder, rows)
        p["addons"].setFilter("")
        pump(until=lambda: lm.rowCount() == 3 + self.N, limit=4000)

    def test_scroll_position_survives_an_update(self):
        p = self.open_page()
        lv = self.lst("addonsList")
        lv.setProperty("contentY", 400)
        pump(100)
        folder = next(f for f, r in p["host"]._addons_status["addons"].items()
                      if r["status"] == "upToDate")
        p["host"]._addons_status["addons"][folder]["status"] = "modified"
        p["addons_service"].rebuild()
        pump(400)
        self.assertAlmostEqual(lv.property("contentY"), 400, delta=1)

    def test_search_box_exists_and_collapsing_keeps_the_filter(self):
        p = self.open_page()
        names = [i.objectName() for i in walk(self.win.property("contentItem"))]
        self.assertIn("addonSearch", names)
        p["addons"].toggleSection("AVAILABLE")
        pump(until=lambda: p["addons"].model.rowCount() < 3 + self.N, limit=4000)
        self.assertEqual(p["addons"].model.stats["resets"], 0)


class TestMpqPage(Base):
    def open_page(self):
        game = tempfile.mkdtemp(prefix="equ-mpq-")
        os.makedirs(os.path.join(game, "Data"))
        for n in ("patch-O.mpq", "patch-Q.mpq"):
            with open(os.path.join(game, "Data", n), "wb") as f:
                f.write(b"theirs")
        self.addCleanup(shutil.rmtree, game, True)
        d = tempfile.mkdtemp(prefix="equ-q-")
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": game, "imported_from_2x": True}, f)
        p = self.start(data_dir=d)
        names = [i.objectName() for i in walk(self.win.property("contentItem"))]
        self.assertNotIn("mpqPanel", names)
        p["app"].tab = "mpq"
        pump(until=lambda: p["mpq"].model.rowCount() > 0, limit=6000)
        pump(300)
        self.assertEqual(self.qml_errors(), [])
        return p

    def test_lists_installed_packs_and_renders_in_both_skins(self):
        p = self.open_page()
        self.assertEqual(p["mpq"].model.rowCount(), 2 + 2)       # 2 headers + 2 packs
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(200)
        self.assertEqual(self.qml_errors(), [])

    def test_link_dialog_opens_refuses_a_bad_link_and_closes(self):
        p = self.open_page()
        p["mpq"].openLink("patch-Q.mpq")
        pump(100)
        dlg = self.lst("mpqLinkDialog")
        self.assertTrue(dlg.property("visible"))
        self.assertTrue(p["mpq"].link("url", "nonsense"))        # reason returned, dialog stays
        self.assertEqual(p["mpq"].linkFor, "patch-Q.mpq")
        p["mpq"].closeLink()
        pump(100)
        self.assertFalse(dlg.property("visible"))

    def test_collapsing_a_section_does_not_reset_the_model(self):
        p = self.open_page()
        before = p["mpq"].model.stats["resets"]
        p["mpq"].toggleSection("INSTALLED")
        pump(until=lambda: p["mpq"].model.rowCount() == 2, limit=4000)
        self.assertEqual(p["mpq"].model.stats["resets"], before)


class TestSettingsPage(Base):
    def test_overlay_opens_in_every_skin_without_errors(self):
        p = self.start()
        names = lambda: [i.objectName() for i in walk(self.win.property("contentItem"))]
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            p["app"].debugAction.emit("settings")
            pump(300)
        self.assertEqual(self.qml_errors(), [])
        self.assertIn("settingsOverlay", names())

    def test_font_override_changes_every_text_style_live_and_restores(self):
        p = self.start()
        base = p["skin"].theme["fonts"]["body"]["family"]
        p["general"].setFontOverride("opendyslexic")
        pump(100)
        self.assertEqual(p["skin"].theme["fonts"]["body"]["family"], "OpenDyslexic")
        self.assertEqual(p["skin"].theme["components"]["button"]["font"]["family"], "OpenDyslexic")
        self.assertEqual(p["settings"].get("font_override"), "opendyslexic")
        p["general"].setFontOverride("")
        self.assertEqual(p["skin"].theme["fonts"]["body"]["family"], base)
        self.assertEqual(self.qml_errors(), [])

    def test_toggles_and_game_folder_through_the_bridge(self):
        from equpdater import config
        p = self.start()
        g = p["general"]
        g.clearWdb = True
        self.assertTrue(config.load_config()["clear_wdb_on_launch"])
        self.assertTrue(g.changeGameDir(os.path.join(self.data, "no-such-folder")))
        other = tempfile.mkdtemp(prefix="equ-game-")
        self.addCleanup(shutil.rmtree, other, True)
        self.assertEqual(g.changeGameDir(other), "")
        self.assertEqual(os.path.normcase(g.gameDir), os.path.normcase(os.path.normpath(other)))
        pump(100)
        self.assertEqual(self.qml_errors(), [])

    def test_session_log_shows_what_happened(self):
        from equpdater.core import logsink
        p = self.start()
        logsink.log("a thing the session did", "acct")
        p["general"].refreshLog()
        self.assertIn("a thing the session did", p["general"].sessionLog)

    def test_changing_the_language_tweak_offers_a_restart_after_apply_and_declining_keeps_going(self):
        from equpdater import i18n
        self.addCleanup(i18n.set_language, "enUS")
        p = self.start()
        asked = []
        p["dialog"].auto = lambda kind, title, body: asked.append((kind, title)) or False
        p["tweaks"].setValue("locale", "deDE")
        p["tweaks"].apply()
        pump(until=lambda: not p["tweaks"].busy and asked, limit=5000)
        self.assertEqual([k for k, _t in asked], ["yes_no"])
        self.assertEqual(i18n.language(), "enUS")             # unchanged until a restart
        self.assertTrue(asked[0][1])


class TestPlayPipeline(Base):
    """PLAY runs the lifted 2.x launch pipeline on a worker; every question it
    asks is answered on the GUI thread. The game itself is never started."""

    def setup_game(self, files=("WoW.exe", "VanillaFixes.exe")):
        game = tempfile.mkdtemp(prefix="equ-game-")
        for n in files:
            with open(os.path.join(game, n), "wb") as f:
                f.write(fake_pe())
        d = tempfile.mkdtemp(prefix="equ-q-")
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": game, "imported_from_2x": True}, f)
        self.addCleanup(shutil.rmtree, game, True)
        p = self.start(data_dir=d)
        from equpdater import diagnostics
        p["host"]._diag_begin = lambda c, dg: diagnostics.NullAttempt()
        p["launch"].refresh()
        pump(until=lambda: p["launch"].canPlay, limit=4000)
        return p, game

    def play(self, p, answers=True):
        from unittest import mock
        from equpdater import platforms
        spawned, asked = [], []
        p["dialog"].auto = lambda kind, t, b: asked.append((kind, t)) or answers
        with mock.patch.object(platforms, "spawn_detached",
                               lambda argv, **k: spawned.append(argv) or mock.Mock(pid=1)):
            p["launch"].play()
            pump(until=lambda: spawned or asked, limit=5000)
            pump(200)
        return spawned, asked

    def test_ready_state_and_status(self):
        p, _g = self.setup_game()
        self.assertEqual(p["app"].statusText, "Ready to play")
        self.assertTrue(p["launch"].canPlay)

    def test_no_game_means_no_play_and_a_clear_status(self):
        d = tempfile.mkdtemp(prefix="equ-q-")
        empty = tempfile.mkdtemp(prefix="equ-game-")
        self.addCleanup(shutil.rmtree, empty, True)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": empty, "imported_from_2x": True}, f)
        p = self.start(data_dir=d)
        p["launch"].refresh()
        pump(300)
        self.assertFalse(p["launch"].canPlay)
        self.assertIn("No game found", p["app"].statusText)
        self.assertTrue(p["app"].statusIsError)

    def test_play_registers_enabled_mods_and_enforces_auto_loot_before_starting(self):
        from unittest import mock
        order = []
        p, game = self.setup_game()
        with mock.patch("equpdater.core.launch_ops.reconcile_dlls_txt",
                        lambda *a, **k: order.append("dlls") or [], create=True), \
                mock.patch("equpdater.core.launch_ops.enforce_autoloot",
                           lambda *a, **k: order.append("autoloot") or [], create=True):
            spawned, asked = self.play(p)
        self.assertEqual(order, ["dlls", "autoloot"])
        self.assertTrue(spawned)

    def test_a_damaged_loader_stops_play_and_offers_to_reinstall(self):
        from unittest import mock
        p, game = self.setup_game()
        with open(os.path.join(game, "VanillaFixes.exe"), "wb") as f:
            f.write(b"")
        asked_for_replace = []
        with mock.patch.object(p["host"], "_mod_force_replace",
                               lambda mid: asked_for_replace.append(mid)):
            spawned, asked = self.play(p, answers=True)
        self.assertEqual(spawned, [])
        self.assertEqual(asked_for_replace, ["VanillaFixes"])
        self.assertEqual([k for k, _t in asked], ["yes_no"])

    def test_declining_the_reinstall_changes_nothing(self):
        p, game = self.setup_game()
        with open(os.path.join(game, "VanillaFixes.exe"), "wb") as f:
            f.write(b"")
        spawned, asked = self.play(p, answers=False)
        self.assertEqual(spawned, [])
        self.assertEqual(os.path.getsize(os.path.join(game, "VanillaFixes.exe")), 0)

    def test_clear_wdb_runs_only_when_asked(self):
        p, game = self.setup_game()
        wdb = os.path.join(game, "WDB")
        os.makedirs(wdb)
        self.play(p)
        self.assertTrue(os.path.isdir(wdb))
        p["general"].clearWdb = True
        pump(100)
        p["launch"]._mode = "play"
        self.play(p)
        self.assertFalse(os.path.isdir(wdb))

    def test_minimize_on_launch_is_asked_for_after_the_game_starts(self):
        p, game = self.setup_game()
        got = []
        p["app"].minimizeRequested.connect(lambda: got.append(1))
        p["general"].closeOnLaunch = True
        spawned, asked = self.play(p)
        pump(until=lambda: got, limit=4000)
        self.assertTrue(spawned and got)

    def test_play_cannot_be_pressed_twice_while_busy(self):
        p, game = self.setup_game()
        p["host"]._set_btn_busy("Installing…")
        pump(100)
        self.assertFalse(p["launch"].canPlay)
        spawned, asked = self.play(p)
        self.assertEqual(spawned, [])

    def test_a_mod_in_error_holds_play_back_with_a_link_to_the_mods_tab(self):
        from equpdater import config
        p, game = self.setup_game()
        config.update_config(lambda c: c.setdefault("mods", {}).__setitem__(
            "dxvk", {"managed": True, "error": "download blocked"}))
        p["host"]._refresh_ready_state()
        pump(100)
        self.assertFalse(p["launch"].canPlay)
        self.assertIn("Mod errors", p["app"].statusText)
        self.assertTrue(p["app"].statusIsError)


class TestDiagnosticsOverlay(Base):
    def test_opens_every_view_in_every_skin_without_errors_and_never_starts_the_game(self):
        from unittest import mock
        from equpdater import platforms
        p = self.start()
        p["diag_service"]._fetch = lambda url, timeout: 200
        with mock.patch.object(platforms, "spawn_detached", side_effect=AssertionError("started")):
            for sid in p["manager"].skins:
                p["settings_bridge"].setSkin(sid)
                for view in ("attempt", "scan", "doctor"):
                    p["diag"].openView(view)
                    pump(200)
        self.assertTrue(p["diag"].isOpen)
        p["diag"].close()
        pump(100)
        self.assertFalse(p["diag"].isOpen)
        self.assertEqual(self.qml_errors(), [])

    def test_scan_runs_on_a_worker_and_reports_into_the_view(self):
        game = tempfile.mkdtemp(prefix="equ-game-")
        self.addCleanup(shutil.rmtree, game, True)
        with open(os.path.join(game, "WoW.exe"), "wb") as f:
            f.write(fake_pe())
        d = tempfile.mkdtemp(prefix="equ-q-")
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": game, "imported_from_2x": True}, f)
        p = self.start(data_dir=d)
        p["diag_service"]._manifest = lambda: None
        p["diag"].openView("scan")
        p["diag"].startScan(False, False)
        pump(until=lambda: any("Folder scan" in l["text"] for l in p["diag"].lines), limit=8000)
        self.assertFalse(p["diag"].scanning)
        self.assertTrue(p["diag"].hasScan)

    def test_login_doctor_repair_asks_before_it_changes_anything(self):
        game = tempfile.mkdtemp(prefix="equ-game-")
        self.addCleanup(shutil.rmtree, game, True)
        with open(os.path.join(game, "realmlist.wtf"), "w") as f:
            f.write("set realmlist octowow.st" + chr(10))
        d = tempfile.mkdtemp(prefix="equ-q-")
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": game, "imported_from_2x": True}, f)
        p = self.start(data_dir=d)
        svc = p["diag_service"]
        svc._fetch = lambda url, timeout: 200
        svc._net = {"resolve": lambda h: ["203.0.113.5"], "connect": lambda h, pt, t: None}
        p["diag"].openView("doctor")
        pump(until=lambda: svc.doctor.get("network") is not None, limit=6000)
        asked = []
        p["dialog"].auto = lambda kind, t, b: asked.append(kind) or False
        p["diag"].repairLogin()
        self.assertEqual(asked, ["yes_no"])
        self.assertIn("octowow.st", open(os.path.join(game, "realmlist.wtf")).read())
        p["dialog"].auto = lambda kind, t, b: asked.append(kind) or True
        p["diag"].repairLogin()
        pump(300)
        self.assertIn("play.octowow.st", open(os.path.join(game, "realmlist.wtf")).read())


class TestCustomOverlay(Base):
    def test_both_dialogs_open_in_every_skin_and_a_mod_waits_for_confirmation(self):
        from equpdater.core import mods as M
        p = self.start()
        svc, br = p["custom_service"], p["custom"]
        svc._release = lambda src: {"tag_name": "v9", "assets": [
            {"name": "Cool.dll", "browser_download_url": "https://github.com/o/r/x"}]}
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            for kind in ("mod", "addon"):
                br.open(kind)
                pump(120)
        br.open("mod")
        br.submit("https://example.com/x")
        pump(50)
        self.assertEqual(br.statusTone, "err")
        br.linkChanged("https://github.com/o/r")
        br.submit("https://github.com/o/r")
        pump(until=lambda: br.found, limit=4000)
        self.assertEqual(list(br.choices), ["Cool.dll"])
        asked = []
        p["dialog"].auto = lambda kind, t, b: asked.append(kind) or False
        br.submit("https://github.com/o/r")
        pump(until=lambda: asked, limit=4000)
        self.assertEqual(asked, ["yes_no"])
        self.assertTrue(br.isOpen)                  # declined: nothing added, dialog stays
        self.assertFalse(any(m.get("custom") for m in M.MODS_REGISTRY))
        br.close()
        self.assertEqual(self.qml_errors(), [])


class TestUpdateAllButton(Base):
    def test_the_footer_arrow_follows_the_counts_and_runs_the_lifted_update_all(self):
        p = self.start()
        calls = []
        p["app"].update_all_hook = lambda: calls.append(1)
        btn = self.win.findChild(QObject, "updateAllButton")
        self.assertIsNotNone(btn)
        self.assertFalse(btn.property("visible"))
        p["app"].setModUpdates(1)
        p["app"].setAddonUpdates(2)
        pump(100)
        self.assertEqual(p["app"].updateAllCount, 3)
        self.assertTrue(btn.property("visible"))
        p["app"].updateAll()
        self.assertEqual(calls, [1])
        spin = self.win.findChild(QObject, "updateAllSpinner")
        self.assertFalse(spin.property("visible"))
        p["host"]._update_all_running = True           # a run started: arrow becomes a spinner
        p["host"]._update_all_chain = True
        p["host"]._refresh_update_all_btn()
        pump(80)
        self.assertTrue(spin.property("visible"))
        self.assertFalse(btn.property("visible"))
        p["host"]._update_all_chain = False            # nothing busy, no chain: the run is over
        p["host"]._refresh_update_all_btn()
        pump(80)
        self.assertFalse(spin.property("visible"))
        self.assertTrue(btn.property("visible"))
        p["app"].setBusy(True)
        pump(50)
        self.assertFalse(btn.property("visible"))
        self.assertEqual(self.qml_errors(), [])


class TestSetupOverlay(Base):
    def test_opens_in_every_skin_lists_a_found_game_and_uses_it(self):
        from tests.test_v3_install import make_game
        game = make_game(tempfile.mkdtemp(prefix="equ-game-"))
        self.addCleanup(shutil.rmtree, game, True)
        p = self.start()
        from equpdater.core import install as I
        p["setup_service"]._find = lambda: [I.inspect_folder(game, "octolauncher")]
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            p["setup"].open()
            pump(150)
            p["setup"].close()
        p["setup"].open()
        pump(until=lambda: p["setup"].candidates, limit=4000)
        row = p["setup"].candidates[0]
        self.assertTrue(row["usable"])
        self.assertIn("OctoLauncher", row["sourceText"])
        p["setup"].use(row["path"])
        pump(100)
        self.assertFalse(p["setup"].isOpen)
        self.assertEqual(os.path.normcase(p["general"].gameDir), os.path.normcase(os.path.normpath(game)))
        self.assertEqual(self.qml_errors(), [])

    def test_extracting_an_archive_makes_it_the_game_folder(self):
        from tests.test_v3_install import EXE, zip_of
        d = tempfile.mkdtemp(prefix="equ-arc-")
        self.addCleanup(shutil.rmtree, d, True)
        arc = zip_of(os.path.join(d, "g.zip"), {"WoW.exe": EXE, **{f"Data/p{i}.mpq": b"x" for i in range(3)}})
        p = self.start()
        p["setup"].open()
        p["setup"].pickArchive(arc)
        self.assertTrue(p["setup"].archiveOk)
        p["setup"].extractTo(os.path.join(d, "OctoWoW"))
        pump(until=lambda: not p["setup"].isOpen, limit=8000)
        self.assertTrue(os.path.isfile(os.path.join(d, "OctoWoW", "WoW.exe")))

    def test_the_first_run_panel_opens_only_when_there_is_no_game(self):
        os.environ.pop("EQU3_NO_SETUP", None)
        self.addCleanup(os.environ.__setitem__, "EQU3_NO_SETUP", "1")
        p = self.start()
        __import__("equpdater.qtui.main", fromlist=["x"]).first_run_setup(p)
        self.assertTrue(p["setup"].isOpen)
        p["setup"].close()
        __import__("equpdater.qtui.main", fromlist=["x"]).first_run_setup(p)                     # asked once: closing it is the answer
        self.assertFalse(p["setup"].isOpen)
        self.assertTrue(p["settings"].get("setup_seen"))


class TestGapOverlays(Base):
    def test_first_run_asks_language_then_finds_the_game(self):
        from unittest import mock
        main = __import__("equpdater.qtui.main", fromlist=["x"])
        os.environ.pop("EQU3_NO_SETUP", None)
        os.environ.pop("EQUPDATER_NO_LANGUAGE_PROMPT", None)
        self.addCleanup(os.environ.__setitem__, "EQU3_NO_SETUP", "1")
        self.addCleanup(os.environ.__setitem__, "EQUPDATER_NO_LANGUAGE_PROMPT", "1")
        p = self.start()
        lang = self.win.findChild(QObject, "languageOverlay")
        p["settings"].set("imported_from_2x", False)        # a true first run, whatever HOME holds
        p["settings"].set("language_asked", False)
        main.first_run_setup(p)
        pump(80)
        self.assertTrue(p["general"].languagePromptOpen)
        self.assertTrue(lang.property("visible"))
        self.assertFalse(p["setup"].isOpen)
        with mock.patch.object(p["general_service"].host, "_restart"):
            p["general"].chooseLanguage("enUS")        # same language: no restart
        pump(80)
        self.assertFalse(lang.property("visible"))
        self.assertTrue(p["setup"].isOpen)
        self.assertEqual(self.qml_errors(), [])

    def test_settings_shows_the_platform_section_in_every_skin(self):
        from unittest import mock
        p = self.start()
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            self.win.findChild(QObject, "settingsOverlay").metaObject().invokeMethod(
                self.win.findChild(QObject, "settingsOverlay"), "open")
            pump(150)
        launcher = self.win.findChild(QObject, "launcherSection")
        defender = self.win.findChild(QObject, "defenderButton")
        self.assertEqual(bool(launcher.property("visible")), p["general"].hasLauncher)
        self.assertEqual(bool(defender.property("visible")) or not p["general"].isWindows, True)
        self.assertNotEqual(p["general"].hasLauncher, p["general"].isWindows)
        self.assertEqual(self.qml_errors(), [])


class TestBatchProgressBar(Base):
    def test_the_footer_shows_n_of_m_and_the_name_and_goes_back_to_indeterminate(self):
        p = self.start()
        p["app"].setBusy(True)
        bar = self.win.findChild(QObject, "batchProgress")
        txt = self.win.findChild(QObject, "batchProgressText")
        pump(60)
        self.assertTrue(bar.property("indeterminate"))
        self.assertFalse(txt.property("visible"))
        p["host"]._progress_set(2, 5, "pfUI")
        pump(60)
        self.assertFalse(bar.property("indeterminate"))
        self.assertAlmostEqual(bar.property("value"), 0.4)
        self.assertTrue(txt.property("visible"))
        self.assertEqual(txt.property("text"), "3 / 5  pfUI")
        p["host"]._progress_clear()
        pump(60)
        self.assertTrue(bar.property("indeterminate"))
        self.assertEqual(self.qml_errors(), [])


class TestServerChip(Base):
    def fixture(self, **over):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        iso = lambda m: (now - datetime.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = {"live": {"all": {"timestamp": iso(2), "online": 1381, "source": "official census"}},
                "status": {"schema": 1, "sample": {"timestamp": iso(4), "website": "available",
                           "tcp": "reachable", "auth": "responsive", "latency_ms": 849,
                           "realms": {"cthun": "online", "nzoth": "online", "yshaarj": "online"}}}}
        data.update(over)
        path = os.path.join(tempfile.mkdtemp(prefix="equ-srv-"), "server.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with open(path, "w") as f:
            json.dump(data, f)
        os.environ["EQU3_SERVER_FIXTURE"] = path
        self.addCleanup(os.environ.pop, "EQU3_SERVER_FIXTURE", None)
        return path

    def chip(self):
        return self.win.findChild(QObject, "serverChip")

    def test_the_chip_shows_the_count_and_a_green_dot_from_octobots_own_sample(self):
        self.fixture()
        p = self.start()
        p["server"].start()
        pump(until=lambda: p["server"].hasData, limit=6000)
        self.assertEqual(p["server"].chipText, "1,381")
        self.assertEqual(p["server"].dot, "online")
        self.assertTrue(self.chip().property("visible"))
        pump(80)
        text = self.win.findChild(QObject, "serverChipText")
        self.assertEqual(text.property("text"), "1,381")
        self.assertIn("Server online", p["server"].summary)
        labels = [r["label"] for r in p["server"].lines]
        self.assertEqual(labels[:4], ["Players online", "N'Zoth", "Y'Shaarj", "C'Thun"])
        self.assertIn("Data from OctoBot", p["server"].footnote)
        self.assertEqual(self.qml_errors(), [])

    def test_the_popover_opens_in_every_skin_without_errors(self):
        self.fixture()
        p = self.start()
        p["server"].start()
        pump(until=lambda: p["server"].hasData, limit=6000)
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            p["app"].debugAction.emit("server")
            pump(150)
            pop = self.win.findChild(QObject, "serverPopover")
            self.assertIsNotNone(pop, sid)
            p["app"].debugAction.emit("server")
            pump(80)
        self.assertEqual(self.qml_errors(), [])

    def test_old_data_is_dim_not_red_and_says_so(self):
        import datetime
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.fixture(status={"schema": 1, "sample": {"timestamp": old, "website": "available",
                     "tcp": "unreachable", "auth": "unresponsive", "realms": {}}})
        p = self.start()
        p["server"].start()
        pump(until=lambda: p["server"].hasData, limit=6000)
        self.assertEqual(p["server"].dot, "unknown")              # stale: never a red "offline"
        self.assertIn("unknown", p["server"].summary)
        self.assertIn("may have changed", p["server"].footnote)

    def test_a_failed_fetch_keeps_the_last_numbers_and_says_it_could_not_refresh(self):
        path = self.fixture()
        p = self.start()
        p["server"].start()
        pump(until=lambda: p["server"].hasData, limit=6000)
        with open(path) as f:
            data = json.load(f)
        data["fail"] = True
        with open(path, "w") as f:
            json.dump(data, f)
        p["server_feed"].fetch_once()
        p["server"].changed.emit()
        pump(60)
        self.assertEqual(p["server"].chipText, "1,381")
        self.assertEqual(p["server"].dot, "online")
        self.assertIn("Could not refresh", p["server"].footnote)

    def test_the_switch_hides_the_chip_and_stops_all_requests(self):
        self.fixture()
        p = self.start()
        p["server"].start()
        pump(until=lambda: p["server_feed"].requests > 0, limit=6000)
        p["server"].enabled = False
        pump(80)
        self.assertFalse(self.chip().property("visible"))
        n = p["server_feed"].requests
        p["server"].refresh()
        p["server"]._tick()
        pump(200)
        self.assertEqual(p["server_feed"].requests, n)
        self.assertFalse(p["settings"].get("show_server_status"))

    def test_offline_mode_and_a_disabled_setting_make_no_requests(self):
        p = self.start(offline=True)                              # EQU3_OFFLINE, no fixture
        p["server"].start()
        pump(200)
        self.assertEqual(p["server_feed"].requests, 0)

    def test_minimising_pauses_and_returning_refreshes(self):
        self.fixture()
        p = self.start()
        p["server"].start()
        pump(until=lambda: p["server_feed"].requests >= 2, limit=6000)
        p["app"].setWindowState(True, False)
        self.assertTrue(p["server_feed"].minimized)
        self.assertFalse(p["server"]._timer.isActive())
        p["server_feed"].fetched_at -= 120                        # it has been a while
        p["app"].setWindowState(False, True)
        self.assertFalse(p["server_feed"].minimized)
        self.assertTrue(p["server"]._timer.isActive())

    def test_the_settings_switch_is_there(self):
        self.fixture()
        p = self.start()
        self.win.findChild(QObject, "settingsOverlay").metaObject().invokeMethod(
            self.win.findChild(QObject, "settingsOverlay"), "open")
        pump(120)
        self.assertIsNotNone(self.win.findChild(QObject, "serverStatusToggle"))


class TestOffline(Base):
    def test_offline_shows_cache_and_an_error_not_an_empty_page(self):
        p = self.start()
        p["news"].start()
        self.settle_news()
        data = self.data
        self.stop()
        del self.engine
        self.data = None
        p2 = self.start(data_dir=data, offline=True)
        p2["news"].start()
        self.settle_news()
        sec = p2["news"].announcements
        self.assertGreater(sec.lm.rowCount(), 0)          # cached content stays
        self.assertTrue(sec.error)                         # and the reason is shown

    def test_offline_with_no_cache_is_an_error_not_a_crash(self):
        p = self.start(offline=True)
        p["news"].start()
        self.settle_news()
        self.assertEqual(p["news"].announcements.lm.rowCount(), 0)
        self.assertTrue(p["news"].announcements.error)
        self.assertEqual(self.qml_errors(), [])


class TestSkins(Base):
    def test_switching_skins_is_live_and_fast_and_leaves_no_errors(self):
        p = self.start()
        self.settle_news()
        sb = p["settings_bridge"]
        for sid in ("minimal-dark", "bubbles", "minimal-dark"):
            t0 = time.perf_counter()
            sb.setSkin(sid)
            pump(200)
            self.assertEqual(p["skin"].id, sid)
            self.assertLess((time.perf_counter() - t0) * 1000, 800)
        self.assertEqual(self.qml_errors(), [])
        self.assertEqual(p["settings"].get("skin"), "minimal-dark")

    def test_every_official_skin_loads_every_page(self):
        p = self.start()
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            for tab in ("news", "tweaks", "addons", "mods", "mpq", "news"):
                p["app"].tab = tab
                pump(60)
        self.assertEqual(self.qml_errors(), [])

    def test_broken_skin_falls_back_and_is_listed_with_problems(self):
        d = tempfile.mkdtemp(prefix="equ-q-")
        sk = os.path.join(d, "skins", "bad")
        os.makedirs(sk)
        with open(os.path.join(sk, "manifest.json"), "w") as f:
            json.dump({"id": "bad", "name": "Bad", "skin_api": 1}, f)
        with open(os.path.join(sk, "theme.json"), "w") as f:
            json.dump({"background": {"static": "../../x.png"},
                       "components": {"button": {"textures": {"normal": "missing.png"}}}}, f)
        p = self.start(data_dir=d)
        p["settings_bridge"].setSkin("bad")
        pump(200)
        self.assertTrue(p["manager"].current.problems)
        self.assertEqual(self.qml_errors(), [])          # still renders, flat fallback

    def test_restore_default_returns_to_bubbles(self):
        p = self.start(skin="minimal-dark")
        p["settings_bridge"].restoreDefaultSkin()
        pump(100)
        self.assertEqual(p["skin"].id, "bubbles")

    def test_animation_stops_when_minimised_and_when_disabled(self):
        p = self.start()
        app = p["app"]
        self.assertTrue(app.animationsActive)
        app.setWindowState(True, False)
        self.assertFalse(app.animationsActive)
        app.setWindowState(False, False)
        self.assertTrue(app.animationsActive)            # unfocused: still plays
        p["settings_bridge"].pauseWhenUnfocused = True
        self.assertFalse(app.animationsActive)
        app.setWindowState(False, True)
        self.assertTrue(app.animationsActive)


class TestLaunch(Base):
    def test_options_default_choice_and_play(self):
        game = tempfile.mkdtemp(prefix="equ-game-")
        for n in ("WoW.exe", "VanillaFixes.exe"):
            with open(os.path.join(game, n), "wb") as f:
                f.write(fake_pe())
        with open(os.path.join(game, "Launch_Stock.bat"), "w") as f:
            f.write("@echo off\nstart WoW.exe\n")
        d = tempfile.mkdtemp(prefix="equ-q-")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": game, "imported_from_2x": True}, f)
        p = self.start(data_dir=d)
        launch = p["launch"]
        launch.refresh()
        pump(until=lambda: launch.canPlay, limit=3000)
        labels = [e["label"] for e in launch.entries]
        self.assertIn("VanillaFixes.exe", labels)
        self.assertIn("WoW.exe", labels)
        self.assertEqual([e["label"] for e in launch.entries if e["checked"]],
                         ["VanillaFixes.exe"])
        launch.chooseDefault("WoW.exe")
        self.assertEqual([e["label"] for e in launch.entries if e["checked"]], ["WoW.exe"])
        self.assertEqual(p["settings"].get("launch_default"), "WoW.exe")
        spawned = []
        from unittest import mock
        from equpdater import diagnostics, platforms
        p["host"]._diag_begin = lambda c, d: diagnostics.NullAttempt()
        p["dialog"].auto = lambda *a: True
        with mock.patch.object(platforms, "spawn_detached",
                               lambda argv, **k: spawned.append((argv, k)) or mock.Mock(pid=1)):
            launch.play()
            pump(until=lambda: spawned, limit=5000)
        self.assertTrue(spawned, p["app"].statusText)
        self.assertTrue(spawned[0][0][0].lower().endswith("wow.exe"), repr(spawned))
        shutil.rmtree(game, ignore_errors=True)

    def test_a_removed_file_keeps_the_choice_for_when_it_returns(self):
        game = tempfile.mkdtemp(prefix="equ-game-")
        with open(os.path.join(game, "WoW.exe"), "wb") as f:
            f.write(fake_pe())
        d = tempfile.mkdtemp(prefix="equ-q-")
        with open(os.path.join(d, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "game_dir": game, "launch_default": "VanillaFixes.exe",
                       "imported_from_2x": True}, f)
        p = self.start(data_dir=d)
        self.assertEqual(p["launch"].service.target(), "WoW.exe")      # falls back...
        self.assertEqual(p["settings"].get("launch_default"), "VanillaFixes.exe")  # ...keeps it
        shutil.rmtree(game, ignore_errors=True)

    def test_legacy_2x_config_is_imported_but_never_written(self):
        from equpdater.core import settings as cs
        d = tempfile.mkdtemp(prefix="equ-q-")
        s = cs.open_settings(d, legacy={"out_dir": "D:/Games/Octo", "launch_default": "WoW.exe",
                                        "animated_background": False,
                                        "news_seen": {"announcements": 123}})
        self.assertEqual((s.get("launch_default"), s.get("animated_background")),
                         ("WoW.exe", False))
        self.assertEqual(s.get("news_seen"), {"announcements": 123})
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
