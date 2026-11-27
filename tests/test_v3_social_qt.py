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
    from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt

from equpdater.core import account as A
from equpdater.core import presence as P


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
    def seed(self, p, names=("Thrall", "Jaina", "Arthas")):
        for n in names:
            self.assertEqual(p["social_service"].add_friend(n), "")

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
        p["account"].setStatus("invisible")
        pump(150)
        self.assertEqual(dot.property("color").name().lower(), P.COLOURS[P.OFFLINE].lower())
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
    PAGES = ("news", "tweaks", "addons", "mods", "mpq")

    def test_the_sidebar_is_visible_on_every_page_in_every_skin(self):
        p = self.start()
        for sid in sorted(p["manager"].skins):
            p["settings_bridge"].setSkin(sid)
            for page in self.PAGES:
                p["app"].tab = page
                pump(60)
                side = find(self.win, "socialSidebar")
                self.assertTrue(side.property("visible"), (sid, page))
                self.assertGreater(side.property("width"), 150, (sid, page))
        self.assertEqual(self.qml_errors(), [])

    def test_the_removed_controls_do_not_exist(self):
        p = self.start()
        self.seed(p)
        pump(100)
        for name in ("sidebarCollapse", "sidebarExpand", "sortButton"):
            self.assertIsNone(find(self.win, name), name)
        for attr in ("collapsed", "toggleCollapsed", "enabled", "realms", "realmFilter", "setRealmFilter", "sort",
                     "setSort", "values", "setSetting", "autoAwayChoices", "intervalChoices", "skinDefaultExpanded"):
            self.assertFalse(hasattr(p["social"], attr), attr)
        texts = {str(i.property("text")) for i in walk(find(self.win, "socialSidebar")) if i.property("text") is not None}
        for banned in ("All", "N'Zoth", "Y'Shaarj", "C'Thun", "‹", "›", "Sort: Status", "Sort"):
            self.assertNotIn(banned, texts, banned)
        self.assertIn("Add Friend", texts)
        self.assertIn("FRIENDS", texts)

    def test_the_sidebar_has_a_fixed_width_that_leaves_the_pages_their_room(self):
        p = self.start()
        for width in (860, 1000, 1280):
            self.win.setProperty("width", width)
            pump(120)
            side = find(self.win, "socialSidebar")
            area = find(self.win, "pageArea")
            self.assertLessEqual(side.property("width"), 260)
            s = side.mapToScene(side.boundingRect().topLeft()).x()
            a_right = area.mapToScene(area.boundingRect().topLeft()).x() + area.property("width")
            self.assertLessEqual(a_right, s, width)                       # the pages end before the sidebar starts

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

    def test_no_service_means_unavailable_statuses_and_zero_requests(self):
        p = self.start(skin="battle-net")
        self.seed(p)
        p["social"].start()
        pump(1500)
        friends = [r for r in self.rows(p) if r["kind"] == "friend"]
        self.assertEqual({r["state"] for r in friends}, {"unknown"})
        self.assertEqual({r["statusLine"] for r in friends}, {"Unavailable"})
        self.assertEqual(p["presence_service"].requests, 0)
        self.assertEqual(p["social"].onlineText, "0 online")
        self.assertEqual([r["title"] for r in self.rows(p) if r["kind"] == "header"], ["Unavailable"])
        self.assertEqual(self.qml_errors(), [])

    def test_search_filters_the_list(self):
        p = self.start()
        self.seed(p)
        p["social"].setSearch("ja")
        self.assertEqual([r["name"] for r in self.rows(p) if r["kind"] == "friend"], ["Jaina"])
        p["social"].setSearch("")
        self.assertEqual(p["social"].friendCount, 3)
        field = find(self.win, "friendSearch")
        QMetaObject.invokeMethod(field, "edited", Qt.DirectConnection, Q_ARG(str, "thr"))     # typing in the field
        pump(60)
        self.assertEqual([r["name"] for r in self.rows(p) if r["kind"] == "friend"], ["Thrall"])


class TestFriendActions(Social):
    def test_add_friend_by_forum_name_with_duplicates_refused(self):
        p = self.start()
        self.assertEqual(p["social"].addFriend("Thrall"), "")
        self.assertIn("already", p["social"].addFriend("thrall"))
        self.assertNotEqual(p["social"].addFriend("x"), "")
        self.assertEqual(p["social"].friendCount, 1)

    def test_the_dialog_asks_only_for_a_forum_name_and_reports_errors(self):
        p = self.start()
        p["social"].openAdd()
        pump(150)
        self.assertTrue(find(self.win, "addFriendOverlay").property("visible"))
        for gone in ("friendForumField", "realm_nzoth"):
            self.assertIsNone(find(self.win, gone), gone)
        field, confirm = find(self.win, "friendNameField"), find(self.win, "addFriendConfirm")
        field.setProperty("text", "x")
        QMetaObject.invokeMethod(confirm, "clicked")
        pump(100)
        self.assertTrue(find(self.win, "addFriendError").property("visible"))
        self.assertTrue(find(self.win, "addFriendOverlay").property("visible"))
        field.setProperty("text", "Thrall")
        QMetaObject.invokeMethod(confirm, "clicked")
        pump(150)
        self.assertEqual(p["social"].friendCount, 1)
        self.assertFalse(find(self.win, "addFriendOverlay").property("visible"))

    def test_favourite_and_unfavourite_and_remove(self):
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
        self.assertNotIn("Favourites", [r["title"] for r in self.rows(p) if r["kind"] == "header"])
        p["social"].removeFriend(jaina["id"])
        self.assertEqual(sorted(r["name"] for r in self.rows(p) if r["kind"] == "friend"), ["Arthas", "Thrall"])
        self.assertEqual(p["social"].copyText(next(r["id"] for r in self.rows(p) if r.get("name") == "Arthas")), "Arthas")

    def test_the_context_menu_has_exactly_the_conventional_entries(self):
        p = self.start()
        self.seed(p)
        menu = self.win.findChild(QObject, "friendMenu")
        entries = menu.property("entries")
        entries = entries.toVariant() if hasattr(entries, "toVariant") else entries
        self.assertEqual([e.get("label") for e in entries if not e.get("separator")],
                         ["Favourite", "Copy Name", "Remove Friend"])

    def test_friend_rows_and_dialogs_render_in_every_skin(self):
        p = self.start()
        self.seed(p)
        for sid in p["manager"].skins:
            p["settings_bridge"].setSkin(sid)
            pump(120)
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

    def click(self, item):
        from PySide6.QtCore import QPoint, QPointF
        from PySide6.QtTest import QTest
        pt = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseClick(self.win, Qt.LeftButton, Qt.NoModifier, QPoint(int(pt.x()), int(pt.y())))

    def test_states_colours_and_status_lines_from_a_service(self):
        self.fixture({"thrall": {"game": "in_game", "launcher": "online", "realm": "nzoth"},
                      "jaina": {"launcher": "away"}, "arthas": {"game": "offline"},
                      "illidan": {"launcher": "dnd"}, "tyrande": {"launcher": "online"}})
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        p = self.start(data_dir=data, skin="battle-net")
        self.seed(p, ("Thrall", "Jaina", "Arthas", "Illidan", "Tyrande"))
        p["social"].start()
        pump(until=lambda: p["presence_service"].requests > 0, limit=6000)
        pump(300)
        by = {r["name"]: r for r in self.rows(p) if r["kind"] == "friend"}
        self.assertEqual(by["Thrall"]["statusLine"], "In-game · N'Zoth")
        self.assertEqual(by["Thrall"]["dot"].lower(), "#3ddc84")
        self.assertEqual(by["Jaina"]["dot"].lower(), "#f2c744")
        self.assertEqual(by["Illidan"]["statusLine"], "Launcher open")
        self.assertEqual(by["Illidan"]["dot"].lower(), "#ef5350")
        self.assertEqual(by["Tyrande"]["statusLine"], "Launcher open")
        self.assertEqual(by["Arthas"]["statusLine"], "Offline")
        self.assertEqual(p["social"].onlineText, "4 online")
        for r in by.values():
            for key in ("charClass", "level", "guild", "zone", "detail", "nameColour"):
                self.assertNotIn(key, r)
        self.assertEqual(self.qml_errors(), [])

    def test_the_status_dot_menu_has_the_four_choices_with_the_conventional_colours(self):
        p = self.start()
        p["account_service"].provider = FakeAuth()
        p["account"].signIn()
        pump(until=lambda: p["account"].signedIn, limit=5000)
        pump(150)
        choices = list(p["account"].statusChoices)
        self.assertEqual([c["label"] for c in choices], ["Online", "Away", "Do Not Disturb", "Invisible"])
        self.assertEqual([c["dot"].lower() for c in choices], ["#3ddc84", "#f2c744", "#ef5350", "#7d8590"])
        menu = self.win.findChild(QObject, "statusMenu")
        self.assertIsNotNone(menu)
        hit = find(self.win, "statusDotButton")
        self.assertTrue(hit.property("visible"))
        self.click(hit)
        pump(250)
        self.assertTrue(menu.property("visible"))                                          # clicking the dot opens it
        for choice, colour in (("away", "#f2c744"), ("dnd", "#ef5350"), ("invisible", "#7d8590"), ("online", "#3ddc84")):
            p["account"].setStatus(choice)
            pump(100)
            self.assertEqual(find(self.win, "accountStatusDot").property("color").name().lower(), colour)

    def test_invisible_is_published_as_invisible_and_everything_still_works(self):
        self.fixture({"thrall": {"launcher": "online"}})
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        p = self.start(data_dir=data)
        self.seed(p, ("Thrall",))
        prov = p["presence_service"].provider
        type(prov).published.clear()
        p["social_service"].set_status("invisible")
        sent = type(prov).published[-1]
        self.assertEqual((sent.status, sent.activity, sent.realm), ("invisible", "launcher", ""))
        p["presence_service"].poll()                                                       # friends still load
        pump(100)
        self.assertEqual(next(r for r in self.rows(p) if r["kind"] == "friend")["statusLine"], "Launcher open")
        self.assertEqual(p["social_service"].status, "invisible")

    def test_a_service_outage_keeps_the_last_data(self):
        path = self.fixture({"thrall": {"game": "in_game", "realm": "nzoth"}})
        p = self.start(skin="battle-net")
        self.seed(p, ("Thrall",))
        p["social"].start()
        pump(until=lambda: p["presence_service"].last_ok > 0, limit=6000)
        with open(path, "w") as f:
            json.dump({"__fail__": True}, f)
        p["presence_service"].poll()
        pump(100)
        row = next(r for r in self.rows(p) if r.get("name") == "Thrall")
        self.assertEqual(row["state"], "in_game")


class TestNoSocialSettings(Social):
    def settings_texts(self):
        ov = find(self.win, "settingsOverlay")
        return [str(i.property("text")) for i in walk(ov) if i.property("text") not in (None, "")]

    def test_settings_has_no_social_section_or_controls(self):
        p = self.start()
        QMetaObject.invokeMethod(self.win, "openSettings")
        pump(250)
        texts = self.settings_texts()
        for banned in ("SOCIAL", "Enable social sidebar", "Show my launcher presence", "Show my in-game presence",
                       "Show my current character", "Show my realm", "Show my current zone",
                       "Friend login notifications", "Default presence status",
                       "Set Away automatically when EqUpdater is idle", "Refresh friends every"):
            self.assertNotIn(banned, texts, banned)
        for name in ("socialEnabledToggle", "showLauncherToggle", "showGameToggle", "showCharacterToggle",
                     "showRealmToggle", "showZoneToggle", "notifyToggle", "settingsSignOut"):
            self.assertIsNone(find(self.win, name), name)

    def test_old_social_preferences_are_gone_after_start_up_and_change_nothing(self):
        data = tempfile.mkdtemp(prefix="equ-soc-")
        self.addCleanup(shutil.rmtree, data, True)
        with open(os.path.join(data, "settings.json"), "w") as f:
            json.dump({"_schema": 1, "imported_from_2x": True, "social_enabled": False, "sidebar_collapsed": True,
                       "show_character": True, "show_zone": True, "default_status": "dnd", "friends_realm": "nzoth",
                       "friend_notifications": True, "presence_interval": 900}, f)
        p = self.start(data_dir=data)
        pump(100)
        self.assertTrue(find(self.win, "socialSidebar").property("visible"))            # social_enabled: false is ignored
        self.assertEqual(p["social_service"].status, "online")                           # default_status ignored
        self.assertEqual(p["presence_service"].interval, 5.0)
        for key in ("social_enabled", "sidebar_collapsed", "show_character", "show_zone", "default_status",
                    "friends_realm", "friend_notifications", "presence_interval"):
            self.assertNotIn(key, p["settings"].data, key)



class TestTemporaryLoginAndSharedFriends(Social):
    """The interface against a real test backend on localhost (equpdater/core/devsocial.py)."""

    def setUp(self):
        from tests.social_harness import Server
        self.server = Server(time.time)
        self.addCleanup(self.server.stop)
        os.environ["EQU3_SOCIAL_URL"] = self.server.url
        self.addCleanup(os.environ.pop, "EQU3_SOCIAL_URL", None)

    def other(self, name):
        from tests.social_harness import Client
        c = Client(self.server, time.time)
        self.addCleanup(shutil.rmtree, c.tmp, True)
        self.assertTrue(c.login(name))
        return c

    def sign_in(self, p, name):
        field, confirm = find(self.win, "loginNameField"), find(self.win, "loginConfirm")
        field.setProperty("text", name)
        QMetaObject.invokeMethod(field, "edited", Qt.DirectConnection, Q_ARG(str, name))
        pump(80)
        QMetaObject.invokeMethod(confirm, "clicked")
        pump(until=lambda: p["account"].signedIn or p["account"].message != "", limit=6000)
        pump(150)

    def test_the_sign_in_button_asks_for_a_username_and_nothing_else(self):
        p = self.start()
        pump(100)
        self.assertTrue(p["account"].usernameLogin)
        self.assertFalse(find(self.win, "createAccountLink").property("visible"))      # no accounts to create
        QMetaObject.invokeMethod(find(self.win, "signInButton"), "clicked")
        pump(200)
        self.assertTrue(find(self.win, "loginOverlay").property("visible"))
        for banned in ("password", "email", "Password", "Email"):
            self.assertFalse([i for i in walk(find(self.win, "loginOverlay")) if i.property("placeholder") and banned in str(i.property("placeholder"))])
        self.sign_in(p, "Thrallfan")
        self.assertTrue(p["account"].signedIn)
        self.assertEqual(p["account"].username, "Thrallfan")
        self.assertFalse(find(self.win, "loginOverlay").property("visible"))
        self.assertEqual(self.qml_errors(), [])

    def test_a_name_that_is_taken_is_refused_in_the_dialog_whatever_the_case(self):
        self.other("Thrallfan")
        p = self.start()
        p["account"].openLogin()
        pump(200)
        self.sign_in(p, "THRALLFAN")
        self.assertFalse(p["account"].signedIn)
        self.assertTrue(find(self.win, "loginOverlay").property("visible"))               # stays open for another try
        self.assertIn("in use", find(self.win, "loginError").property("text"))
        self.assertTrue(find(self.win, "loginError").property("visible"))

    def test_switching_accounts_frees_the_old_name(self):
        p = self.start()
        p["account"].openLogin()
        pump(200)
        self.sign_in(p, "FirstName")
        self.assertEqual(p["account"].username, "FirstName")
        p["account"].openLogin()
        pump(200)
        self.sign_in(p, "SecondName")
        pump(until=lambda: p["account"].username == "SecondName", limit=5000)
        self.assertEqual(p["account"].username, "SecondName")
        taker = self.other("firstname")                                                    # the old name is free again
        self.assertEqual(taker.account.username, "FirstName")

    def test_friends_come_from_the_shared_backend_and_show_what_they_are_doing(self):
        import json as _json
        from tests.social_harness import SimActivity
        sim = SimActivity(tempfile.mkdtemp(prefix="equ-sim-"))
        self.addCleanup(shutil.rmtree, os.path.dirname(sim.path), True)
        from tests.social_harness import Client
        friend = Client(self.server, time.time, activity=sim)
        self.addCleanup(shutil.rmtree, friend.tmp, True)
        friend.login("Jainaprb")
        p = self.start()
        p["social"].start()
        p["account"].openLogin()
        pump(200)
        self.sign_in(p, "Thrallfan")
        p["social"].addFriendAsync("jainaprb")                                            # any case finds the user
        added = []
        p["social"].addFinished.connect(added.append)
        pump(until=lambda: p["social"].friendCount == 1 or added, limit=6000)
        p["social"].kick()
        sim.set("in_game", "yshaarj")
        friend.presence.poll()
        pump(until=lambda: any(r.get("statusLine", "").startswith("In-game") for r in self.rows(p) if r["kind"] == "friend"), limit=9000)
        row = next(r for r in self.rows(p) if r["kind"] == "friend")
        self.assertEqual((row["name"], row["statusLine"]), ("Jainaprb", "In-game · Y'Shaarj"))
        for key in ("charClass", "level", "guild", "zone", "detail"):
            self.assertNotIn(key, row)
        self.assertEqual(p["social"].onlineText, "1 online")

    def test_adding_an_unknown_user_reports_it_in_the_add_dialog(self):
        p = self.start()
        p["account"].openLogin()
        pump(200)
        self.sign_in(p, "Thrallfan")
        p["social"].openAdd()
        pump(200)
        field, confirm = find(self.win, "friendNameField"), find(self.win, "addFriendConfirm")
        field.setProperty("text", "Nobodyhere")
        QMetaObject.invokeMethod(confirm, "clicked")
        pump(until=lambda: find(self.win, "addFriendError").property("visible"), limit=6000)
        self.assertIn("No user", find(self.win, "addFriendError").property("text"))
        self.assertTrue(find(self.win, "addFriendOverlay").property("visible"))

    def test_when_the_backend_goes_away_the_launcher_carries_on(self):
        friend = self.other("Jainaprb")
        p = self.start()
        p["account"].openLogin()
        pump(200)
        self.sign_in(p, "Thrallfan")
        self.assertEqual(p["social_service"].add_friend("Jainaprb"), "")
        p["presence_service"].poll()
        self.server.stop()
        p["presence_service"].last_try = 0
        p["presence_service"].poll()
        self.assertGreaterEqual(p["presence_service"].failures, 1)
        self.assertEqual(p["social"].friendCount, 1)                                       # the list stays on screen
        self.assertTrue(p["account"].signedIn)
        p["launch"].refresh()
        pump(150)
        self.assertTrue(p["launch"].playText)                                              # the launcher itself is unaffected
        self.assertEqual(self.qml_errors(), [])


if __name__ == "__main__":
    unittest.main()
