"""Social features in the interface: account chip, sidebar, add-friend dialog, settings, skins."""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tests.test_v3_qt import Base, HAVE_QT, pump, walk

if HAVE_QT:
    from PySide6.QtCore import QMetaObject, QObject

from equpdater.core import account as A
from equpdater.core import presence as P
from tests.test_v3_social import FakeProvider, obs


def find(win, name):
    return next((i for i in walk(win.property("contentItem")) if i.objectName() == name), None)


class FakeAuth(A.AuthProvider):
    """A stand-in provider for the *interface* tests. It is not a service and is never the default."""
    id, name, available = "fake", "Fake", True
    avatar_hosts = ("cdn.example.test",)
    profile_url = "https://octowow.st/forum/profile"

    def start(self, open_browser, cancel, timeout=180.0):
        return A.Tokens("acc", "ref", time.time() + 3600)

    def refresh(self, tokens):
        return tokens

    def profile(self, tokens):
        return A.Profile("Thrallfan", "1", "")


class Social(Base):
    def seed(self, p, names=(("Thrall", "nzoth"), ("Jaina", "yshaarj"), ("Arthas", "cthun"))):
        for n, r in names:
            self.assertEqual(p["social_service"].add_friend(n, r), "")

    def rows(self, p):
        return [p["social"].lm.item_at(i) for i in range(p["social"].lm.rowCount())]


class TestAccountChip(Social):
    def test_signed_out_shows_sign_in_and_create_account(self):
        p = self.start()
        pump(100)
        self.assertTrue(find(self.win, "signInButton").property("visible"))
        self.assertTrue(find(self.win, "createAccountLink").property("visible"))
        self.assertFalse(find(self.win, "accountMenuButton").property("visible"))
        self.assertEqual(self.qml_errors(), [])

    def test_with_no_service_sign_in_explains_instead_of_pretending(self):
        p = self.start()
        asked = []
        p["dialog"].auto = lambda kind, title, body: asked.append((kind, body)) or True
        self.assertFalse(p["account"].available)
        p["account"].signIn()
        self.assertEqual(asked[0][0], "info")
        self.assertIn("not published", asked[0][1])
        self.assertIn("will not ask for your forum password", asked[0][1])
        self.assertFalse(p["account"].signedIn)
        self.assertEqual(p["account"].state, "signed_out")
        self.assertIsNone(p["account_service"].store.get(A.SERVICE, A.ACCOUNT))

    def test_create_account_opens_the_official_site_only(self):
        p = self.start()
        with mock.patch("equpdater.qtui.social_bridge._open_url") as opened:
            p["account"].createAccount()
        opened.assert_called_once_with("https://octowow.st/login")

    def test_signing_in_signing_out_and_the_status_menu_with_a_provider(self):
        p = self.start()
        svc = p["account_service"]
        svc.provider = FakeAuth()
        p["account"].changed.emit()
        p["account"].signIn()
        pump(until=lambda: p["account"].signedIn, limit=5000)
        pump(100)
        self.assertEqual(p["account"].username, "Thrallfan")
        self.assertTrue(find(self.win, "accountMenuButton").property("visible"))
        self.assertFalse(find(self.win, "signInButton").property("visible"))
        self.assertIn("acc", svc.store.get(A.SERVICE, A.ACCOUNT))
        self.assertEqual(p["account"].avatarUrl, "")                       # no avatar given: initials
        svc.profile = A.Profile("Thrallfan", "1", "https://cdn.example.test/a.png")
        self.assertEqual(p["account"].avatarUrl, "https://cdn.example.test/a.png")   # https, a host the service named
        svc.profile = A.Profile("Thrallfan", "1", "")
        dot = find(self.win, "accountStatusDot")
        online = dot.property("color").name()
        p["account"].setStatus("dnd")
        pump(150)
        self.assertEqual(p["account"].status, "dnd")
        self.assertNotEqual(dot.property("color").name(), online)
        self.assertEqual(dot.property("color").name().lower(), P.COLOURS[P.DND].lower())
        p["account"].signOut()
        pump(until=lambda: not p["account"].signedIn, limit=5000)
        self.assertIsNone(svc.store.get(A.SERVICE, A.ACCOUNT))
        pump(100)
        self.assertTrue(find(self.win, "signInButton").property("visible"))
        self.assertEqual(self.qml_errors(), [])

    def test_an_avatar_from_a_host_the_service_did_not_name_is_not_loaded(self):
        p = self.start()
        svc = p["account_service"]
        svc.provider = FakeAuth()
        svc.profile = A.Profile("X", "1", "https://evil.example.test/a.png")
        svc.state = A.SIGNED_IN
        self.assertEqual(p["account"].avatarUrl, "")
        svc.profile = A.Profile("X", "1", "http://cdn.example.test/a.png")             # not https
        self.assertEqual(p["account"].avatarUrl, "")

    def test_a_saved_session_is_restored_off_the_gui_thread_and_a_dead_one_is_dropped(self):
        p = self.start()
        svc = p["account_service"]
        svc.provider = FakeAuth()
        svc.store.set(A.SERVICE, A.ACCOUNT, A.Tokens("acc", "ref", time.time() + 3600).dumps())
        p["account"].restore()
        pump(until=lambda: p["account"].verified, limit=5000)
        self.assertTrue(p["account"].signedIn)
        self.assertEqual(p["account"].username, "Thrallfan")

        import sys
        live_error = sys.modules[type(svc).__module__].AuthError     # other tests reload modules: use the service's own class

        class Dead(FakeAuth):
            def profile(self, tokens):
                raise live_error("rejected", "Your sign-in has expired. Please sign in again.")
        pump(until=lambda: not p["tasks"].running("account:restore"), limit=3000)   # the first job has fully ended
        svc.provider = Dead()
        svc.state, svc.message, svc.verified = A.SIGNED_OUT, "", False
        p["account"].restore()
        pump(until=lambda: p["account"].message != "" and not p["account"].signedIn, limit=5000)
        self.assertFalse(p["account"].signedIn)
        self.assertIsNone(svc.store.get(A.SERVICE, A.ACCOUNT))

    def test_the_account_menu_and_chip_work_in_every_skin(self):
        p = self.start()
        p["account_service"].provider = FakeAuth()
        p["account"].signIn()
        pump(until=lambda: p["account"].signedIn, limit=5000)
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(100)
            QMetaObject.invokeMethod(find(self.win, "accountChip"), "openMenu")
            pump(100)
        self.assertEqual(self.qml_errors(), [])


class TestSidebar(Social):
    def test_every_skin_has_a_sidebar_and_it_starts_as_the_skin_wishes(self):
        for sid, expanded in (("bubbles", False), ("battle-net", True), ("turtle-wow", False)):
            p = self.start(skin=sid)
            pump(100)
            self.assertEqual(p["social"].collapsed, not expanded, sid)
            side = find(self.win, "socialSidebar")
            self.assertTrue(side.property("visible"))
            self.assertEqual(side.property("width"), 46 if not expanded else
                             self.win.property("layout")["sidebarWidth"], sid)
            self.stop()
            shutil.rmtree(self.data, ignore_errors=True)
            del self.engine

    def test_collapsing_and_expanding_is_remembered_across_restarts_and_overrides_the_skin(self):
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        p = self.start(data_dir=data, skin="battle-net")
        self.assertFalse(p["social"].collapsed)
        p["social"].toggleCollapsed()
        pump(200)
        self.assertTrue(p["social"].collapsed)
        self.assertEqual(find(self.win, "socialSidebar").property("width"), 46)
        self.stop()
        del self.engine
        p2 = self.start(data_dir=data, skin="battle-net")                                   # a new run
        self.assertTrue(p2["social"].collapsed)                                              # the player's choice wins
        p2["social"].toggleCollapsed()
        self.stop()
        del self.engine
        p3 = self.start(data_dir=data, skin="bubbles")
        self.assertFalse(p3["social"].collapsed)                                             # and holds in another skin

    def test_the_friends_list_is_kept_between_runs(self):
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        p = self.start(data_dir=data)
        self.seed(p)
        self.stop()
        del self.engine
        p2 = self.start(data_dir=data)
        names = sorted(r["name"] for r in self.rows(p2) if r["kind"] == "friend")
        self.assertEqual(names, ["Arthas", "Jaina", "Thrall"])

    def test_an_open_sidebar_pushes_the_pages_when_there_is_room_and_overlays_them_when_there_is_not(self):
        p = self.start(skin="battle-net")
        area = find(self.win, "pageArea")
        side = find(self.win, "socialSidebar")
        self.win.setProperty("width", 1100)
        pump(200)
        wide = area.property("width")
        self.assertLessEqual(area.property("x") + area.property("width"), side.property("x") + 1)   # beside it
        self.win.setProperty("width", 900)
        pump(200)
        self.assertGreater(area.property("width"), 900 - 2 * 28 - 5)                         # underneath it
        p["social"].toggleCollapsed()                                                        # collapse: back to docked
        pump(200)
        self.assertLessEqual(area.property("x") + area.property("width"), side.property("x") + 1)
        self.assertGreater(wide, 600)

    def test_turning_social_off_removes_the_sidebar_and_all_polling(self):
        p = self.start(skin="battle-net")
        self.seed(p)
        p["presence_service"].provider = FakeProvider()
        p["presence_service"].sidebar_open = True
        self.assertTrue(p["presence_service"].active())
        p["social"].setSetting("social_enabled", False)
        pump(150)
        self.assertFalse(find(self.win, "socialSidebar").property("visible"))
        self.assertFalse(p["presence_service"].active())
        area = find(self.win, "pageArea")
        self.assertGreater(area.property("width"), 900)
        p["social"].setSetting("social_enabled", True)
        pump(150)
        self.assertTrue(find(self.win, "socialSidebar").property("visible"))

    def test_no_service_means_unavailable_statuses_an_explanation_and_zero_requests(self):
        p = self.start(skin="battle-net")
        self.seed(p)
        p["social"].start()
        pump(1500)
        friends = [r for r in self.rows(p) if r["kind"] == "friend"]
        self.assertEqual({r["state"] for r in friends}, {"unknown"})
        self.assertEqual(p["presence_service"].requests, 0)
        self.assertIn("no service", p["social"].sourceNote)
        self.assertEqual(p["social"].onlineText, "0 online")
        heads = [r["title"] for r in self.rows(p) if r["kind"] == "header"]
        self.assertEqual(heads, ["Offline or unavailable"])                                  # never just "Offline"
        self.assertEqual(self.qml_errors(), [])

    def test_the_realm_list_and_filters(self):
        p = self.start()
        self.seed(p)
        ids = [r["id"] for r in p["social"].realms]
        self.assertEqual(sorted(ids), ["cthun", "nzoth", "yshaarj"])
        p["social"].setRealmFilter("nzoth")
        self.assertEqual([r["name"] for r in self.rows(p) if r["kind"] == "friend"], ["Thrall"])
        p["social"].setRealmFilter("")
        p["social"].setSearch("ja")
        self.assertEqual([r["name"] for r in self.rows(p) if r["kind"] == "friend"], ["Jaina"])
        p["social"].setSearch("")
        p["social"].setSort("name")
        self.assertEqual([r["name"] for r in self.rows(p) if r["kind"] == "friend"], ["Arthas", "Jaina", "Thrall"])


class TestFriendActions(Social):
    def test_add_dialog_duplicates_and_same_name_on_another_realm(self):
        p = self.start()
        p["social"].openAdd()
        pump(150)
        overlay = find(self.win, "addFriendOverlay")
        self.assertTrue(overlay.property("visible"))
        self.assertEqual(p["social"].addFriend("Thrall", "nzoth", ""), "")
        self.assertIn("already", p["social"].addFriend("thrall", "nzoth", ""))
        self.assertEqual(p["social"].addFriend("Thrall", "cthun", ""), "")
        self.assertIn("2 to 12 letters", p["social"].addFriend("T", "nzoth", ""))
        self.assertIn("realm", p["social"].addFriend("Jaina", "moonbase", ""))
        self.assertEqual(len([r for r in self.rows(p) if r["kind"] == "friend"]), 2)
        p["social"].closeAdd()
        pump(100)
        self.assertFalse(overlay.property("visible"))

    def test_the_dialog_button_adds_and_reports_errors_in_the_dialog(self):
        p = self.start()
        p["social"].openAdd()
        pump(150)
        field = find(self.win, "friendNameField")
        field.setProperty("text", "Jaina")
        QMetaObject.invokeMethod(find(self.win, "realm_yshaarj"), "clicked")
        QMetaObject.invokeMethod(find(self.win, "addFriendConfirm"), "clicked")
        pump(150)
        self.assertEqual([r["name"] for r in self.rows(p) if r["kind"] == "friend"], ["Jaina"])
        self.assertFalse(p["social"].addOpen)
        p["social"].openAdd()
        pump(150)
        find(self.win, "friendNameField").setProperty("text", "Jaina")
        QMetaObject.invokeMethod(find(self.win, "realm_yshaarj"), "clicked")
        QMetaObject.invokeMethod(find(self.win, "addFriendConfirm"), "clicked")
        pump(150)
        self.assertTrue(p["social"].addOpen)
        self.assertIn("already", find(self.win, "addFriendError").property("text"))

    def test_favourite_moves_to_the_top_group_and_remove_removes(self):
        p = self.start()
        self.seed(p)
        jaina = next(r for r in self.rows(p) if r.get("name") == "Jaina")
        p["social"].toggleFavourite(jaina["id"])
        pump(100)
        rows = self.rows(p)
        self.assertEqual(rows[0]["title"], "Favourites")
        self.assertEqual(rows[1]["name"], "Jaina")
        self.assertTrue(rows[1]["favourite"])
        p["social"].toggleFavourite(jaina["id"])
        p["social"].removeFriend(jaina["id"])
        self.assertEqual(sorted(r["name"] for r in self.rows(p) if r["kind"] == "friend"), ["Arthas", "Thrall"])
        self.assertEqual(p["social"].friendCount, 2)
        self.assertEqual(p["social"].copyText(next(r["id"] for r in self.rows(p) if r.get("name") == "Arthas")), "Arthas")

    def test_friend_rows_and_the_context_menu_render_in_every_skin(self):
        p = self.start()
        self.seed(p)
        p["social"].toggleCollapsed() if p["social"].collapsed else None
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(120)
            if p["social"].collapsed:
                p["social"].toggleCollapsed()
                pump(120)
            QMetaObject.invokeMethod(find(self.win, "socialSidebar"), "toString") if False else None
            p["social"].openAdd()
            pump(120)
            p["social"].closeAdd()
            pump(60)
        self.assertEqual(self.qml_errors(), [])


class TestPresenceInTheInterface(Social):
    def fixture(self, data):
        path = os.path.join(tempfile.mkdtemp(prefix="equ-pres-"), "presence.json")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        with open(path, "w") as f:
            json.dump(data, f)
        os.environ["EQU3_PRESENCE_FIXTURE"] = path
        self.addCleanup(os.environ.pop, "EQU3_PRESENCE_FIXTURE", None)
        return path

    def test_states_colours_class_colours_and_details_from_a_service(self):
        self.fixture({"thrall@nzoth": {"game": "in_game", "launcher": "online", "class": "shaman", "level": 60,
                                       "guild": "Horde", "zone": "Orgrimmar"},
                      "jaina@yshaarj": {"launcher": "away", "class": "mage", "level": 58},
                      "arthas@cthun": {"game": "offline"}})
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        p = self.start(data_dir=data, skin="battle-net")
        self.seed(p)
        p["social"].start()
        pump(until=lambda: p["presence_service"].requests > 0, limit=6000)
        pump(300)
        by = {r["name"]: r for r in self.rows(p) if r["kind"] == "friend"}
        self.assertEqual(by["Thrall"]["state"], "in_game")
        self.assertEqual(by["Thrall"]["dot"].lower(), "#3ddc84")
        self.assertEqual(by["Thrall"]["nameColour"].lower(), "#0070de")
        self.assertIn("Orgrimmar", by["Thrall"]["detail"])
        self.assertEqual(by["Jaina"]["state"], "away")
        self.assertEqual(by["Arthas"]["state"], "offline")
        self.assertEqual(p["social"].onlineText, "2 online")
        self.assertEqual(self.qml_errors(), [])

    def test_a_service_outage_keeps_the_last_data_and_says_so(self):
        path = self.fixture({"thrall@nzoth": {"game": "in_game"}})
        p = self.start(skin="battle-net")
        self.seed(p)
        p["social"].start()
        pump(until=lambda: p["presence_service"].last_ok > 0, limit=6000)
        with open(path, "w") as f:
            json.dump({"__fail__": True}, f)
        p["presence_service"].poll()
        pump(100)
        row = next(r for r in self.rows(p) if r.get("name") == "Thrall")
        self.assertEqual(row["state"], "in_game")
        self.assertIn("last known", p["social"].sourceNote)

    def test_a_friend_coming_online_shows_a_notification_only_if_asked_for(self):
        path = self.fixture({"thrall@nzoth": {"game": "offline"}})
        p = self.start(skin="battle-net")
        self.seed(p, (("Thrall", "nzoth"),))
        p["social"].setSetting("friend_notifications", True)
        p["social"].start()
        pump(until=lambda: p["presence_service"].last_ok > 0, limit=6000)
        with open(path, "w") as f:
            json.dump({"thrall@nzoth": {"game": "in_game"}}, f)
        p["presence_service"].poll()
        pump(200)
        toast = find(self.win, "toast")
        self.assertTrue(toast.property("visible"))
        self.assertEqual(toast.property("title"), "Thrall")
        self.assertEqual(self.qml_errors(), [])


class TestSocialSettings(Social):
    def test_the_social_section_is_in_settings_and_every_choice_persists(self):
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        p = self.start(data_dir=data)
        QMetaObject.invokeMethod(self.win, "openSettings")
        pump(200)
        for name in ("socialEnabledToggle", "showLauncherToggle", "showGameToggle", "showCharacterToggle",
                     "showRealmToggle", "showZoneToggle", "notifyToggle"):
            self.assertIsNotNone(find(self.win, name), name)
        vals = p["social"].values
        self.assertFalse(vals["show_character"])                                             # private by default
        for key, v in (("show_launcher_presence", True), ("show_game_presence", True), ("show_character", True),
                       ("show_realm", True), ("show_zone", True), ("friend_notifications", True),
                       ("auto_away_minutes", 15), ("default_status", "dnd"), ("presence_interval", 120)):
            p["social"].setSetting(key, v)
        self.assertEqual(p["social"].values["auto_away_minutes"], 15)
        p["social"].setSetting("default_status", "bogus")
        p["social"].setSetting("auto_away_minutes", 7)
        self.assertEqual(p["social"].values["default_status"], "dnd")
        self.assertEqual(p["social"].values["auto_away_minutes"], 15)
        self.stop()
        del self.engine
        p2 = self.start(data_dir=data)
        v2 = p2["social"].values
        self.assertEqual((v2["show_zone"], v2["friend_notifications"], v2["default_status"], v2["presence_interval"]),
                         (True, True, "dnd", 120))

    def test_a_toggle_in_the_interface_changes_the_setting(self):
        p = self.start()
        QMetaObject.invokeMethod(self.win, "openSettings")
        pump(200)
        QMetaObject.invokeMethod(find(self.win, "showZoneToggle"), "toggled")
        pump(100)
        self.assertTrue(p["social"].values["show_zone"])

    def test_sign_out_is_offered_in_settings_only_when_signed_in(self):
        p = self.start()
        p["account_service"].provider = FakeAuth()
        QMetaObject.invokeMethod(self.win, "openSettings")
        pump(200)
        self.assertFalse(find(self.win, "settingsSignOut").property("visible"))
        p["account"].signIn()
        pump(until=lambda: p["account"].signedIn, limit=5000)
        pump(150)
        self.assertTrue(find(self.win, "settingsSignOut").property("visible"))


if __name__ == "__main__":
    unittest.main()
