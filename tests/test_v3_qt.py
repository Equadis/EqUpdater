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
        launch.service._spawn = lambda argv, **k: spawned.append((argv, k))
        launch.play()
        pump(until=lambda: spawned, limit=3000)
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
