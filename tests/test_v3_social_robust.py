"""Social features must never get in the launcher's way, and must look right in every skin."""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tests.test_v3_qt import Base, pump
from tests.test_v3_social_qt import FakeAuth, Social, find
from equpdater.core import account as A
from equpdater.core import credstore as C
from equpdater.core import presence as P


def rect(item):
    top_left = item.mapToScene(item.boundingRect().topLeft())
    return (top_left.x(), top_left.y(), top_left.x() + item.width(), top_left.y() + item.height())


def overlap(a, b):
    return a[0] < b[2] - 0.5 and b[0] < a[2] - 0.5 and a[1] < b[3] - 0.5 and b[1] < a[3] - 0.5


class RaisingStore(C.CredentialStore):
    kind = "broken"
    persistent = True

    def set(self, *a):
        raise OSError("credential manager is not working")

    def get(self, *a):
        raise OSError("credential manager is not working")

    def delete(self, *a):
        raise OSError("credential manager is not working")


class TestUnavailableServicesNeverGetInTheWay(Social):
    def test_a_broken_credential_store_does_not_stop_sign_in_or_start_up(self):
        p = self.start()
        svc = p["account_service"]
        svc.store = RaisingStore()
        svc.provider = FakeAuth()
        p["account"].restore()                                         # reading the store fails
        pump(100)
        self.assertEqual(svc.state, A.SIGNED_OUT)
        p["account"].signIn()                                          # saving the tokens fails: still signed in, for this run
        pump(until=lambda: p["account"].signedIn, limit=5000)
        self.assertEqual(p["account"].username, "Thrallfan")
        p["account"].signOut()                                         # deleting fails: still signed out
        pump(until=lambda: not p["account"].signedIn, limit=5000)
        self.assertEqual(self.qml_errors(), [])

    def test_a_damaged_friends_file_is_set_aside_and_the_launcher_starts(self):
        data = tempfile.mkdtemp(prefix="equ-bad-")
        self.addCleanup(shutil.rmtree, data, True)
        with open(os.path.join(data, "social.json"), "w") as f:
            f.write("{ this is not json")
        p = self.start(data_dir=data)
        self.assertEqual(p["social"].friendCount, 0)
        self.assertTrue([n for n in os.listdir(data) if n.startswith("social.json.corrupt-")])
        self.assertEqual(p["social_service"].add_friend("Thrall", "nzoth"), "")
        self.assertEqual(self.qml_errors(), [])

    def test_a_presence_service_that_misbehaves_in_any_way_is_contained(self):
        p = self.start()
        self.seed(p)
        pres = p["presence_service"]

        class Bad(P.PresenceProvider):
            id, name, available = "bad", "bad", True
            error = ValueError("garbage")

            def lookup(self, friends, token):
                raise self.error
        pres.provider = Bad()
        pres.sidebar_open = True
        for exc in (ValueError("x"), KeyError("y"), RuntimeError("z"), TypeError("t"), OSError("o"), P.ProviderError("p")):
            Bad.error = exc
            self.assertFalse(pres.poll())                              # never raises
        self.assertGreaterEqual(pres.failures, 6)
        pump(100)
        self.assertEqual(p["social"].friendCount, 3)                   # the list is untouched

    def test_a_failure_in_the_friends_list_keeps_what_is_shown_and_nothing_else_breaks(self):
        p = self.start()
        self.seed(p)
        before = p["social"].lm.rowCount()
        with mock.patch.object(p["social_service"], "rows", side_effect=RuntimeError("boom")):
            p["social"]._refresh()                                     # must not raise
        self.assertEqual(p["social"].lm.rowCount(), before)

    def test_every_optional_service_may_fail_to_start_without_stopping_the_launcher(self):
        from equpdater.qtui import main
        p = self.start()
        started = []

        class Boom:
            def start(self):
                raise RuntimeError("server status broke")

            def restore(self):
                raise RuntimeError("account broke")
        parts = {"server": Boom(), "social": type("S", (), {"start": lambda s: started.append("social")})(),
                 "account": Boom()}
        main.start_optional_services(parts)                            # none of them may stop the others
        self.assertEqual(started, ["social"])

    def test_the_launcher_part_of_the_program_does_not_depend_on_the_social_part(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        social = ("social", "account", "presence", "friends", "credstore", "realms")
        for rel in ("equpdater/core/launch_ops.py", "equpdater/core/launch_funcs.py", "equpdater/core/mods_ops.py",
                    "equpdater/core/addons_ops.py", "equpdater/core/mpq_ops.py", "equpdater/core/tweaks.py",
                    "equpdater/core/settings_ops.py", "equpdater/core/diagnostics_service.py",
                    "equpdater/core/launch.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                text = f.read()
            for line in text.splitlines():
                if line.startswith(("import ", "from ")):
                    for word in social:
                        self.assertNotRegex(line, r"\b%s\b" % word, f"{rel}: {line}")

    def test_play_stays_ready_and_the_pages_work_while_every_social_service_is_broken(self):
        p = self.start()
        p["account_service"].store = RaisingStore()
        p["presence_service"].provider = type("B", (P.PresenceProvider,), {
            "id": "b", "name": "b", "available": True,
            "lookup": lambda self, f, t: (_ for _ in ()).throw(RuntimeError("down"))})()
        with mock.patch.object(p["social_service"], "rows", side_effect=RuntimeError("boom")):
            p["social"]._refresh()
        p["social"].start()
        p["account"].restore()
        for page in ("news", "tweaks", "addons", "mods", "mpq"):
            p["app"].tab = page
            pump(80)
        p["launch"].refresh()
        pump(100)
        self.assertTrue(p["launch"].playText)                           # the PLAY button still has its text and state
        self.assertEqual(self.qml_errors(), [])


class TestEverySkinHasTheAccountChipAndTheSidebar(Social):
    WIDTHS = (860, 1000, 1280)

    def geometry_ok(self, p, sid, width, expanded, signed_in):
        problems = []
        win = self.win
        win.setProperty("width", width)
        win.setProperty("height", 700)
        pump(150)
        header, footer = find(win, "headerArea"), find(win, "footerArea")
        chip, nav, gear = find(win, "accountChip"), find(win, "navRow"), find(win, "settingsGear")
        side = find(win, "socialSidebar")
        area = find(win, "pageArea")
        where = f"{sid} {width}px {'expanded' if expanded else 'collapsed'} {'in' if signed_in else 'out'}"
        h, f, c, n, g, s = (rect(x) for x in (header, footer, chip, nav, gear, side))
        if not (c[0] >= 0 and c[2] <= width and c[1] >= h[1] - 0.5 and c[3] <= h[3] + 0.5):
            problems.append(f"{where}: the account chip leaves the header {c} {h}")
        for name, other in (("navigation", n), ("settings gear", g)):
            if overlap(c, other):
                problems.append(f"{where}: the account chip overlaps the {name} {c} {other}")
        if not (s[0] >= 0 and s[2] <= width + 0.5):
            problems.append(f"{where}: the sidebar leaves the window {s}")
        if s[1] < h[3] - 0.5 or s[3] > f[1] + 0.5:
            problems.append(f"{where}: the sidebar overlaps the header or the footer {s} {h} {f}")
        a = rect(area)
        docked = (not expanded) or width >= 940
        if docked and overlap(a, s):
            problems.append(f"{where}: the page area runs under a docked sidebar {a} {s}")
        margin = p["skin"].theme["layout"].get("contentMargin", 48)
        if not docked and a[2] < width - margin - 1:
            problems.append(f"{where}: the page area shrank although the sidebar overlays it")
        return problems

    def test_geometry_in_every_skin_at_every_width_collapsed_or_expanded_signed_out_or_in(self):
        p = self.start()
        svc = p["account_service"]
        svc.provider = FakeAuth()
        svc.profile = A.Profile("A" * 24 + "Longname", "1", "")
        problems = []
        for sid in sorted(p["manager"].skins):
            p["settings_bridge"].setSkin(sid)
            for signed_in in (False, True):
                svc.state = A.SIGNED_IN if signed_in else A.SIGNED_OUT
                p["account"].changed.emit()
                for expanded in (False, True):
                    if p["social"].collapsed == expanded:
                        p["social"].toggleCollapsed()
                    for width in self.WIDTHS:
                        problems += self.geometry_ok(p, sid, width, expanded, signed_in)
        self.maxDiff = None
        self.assertEqual(problems, [], "\n".join(problems[:40]))
        self.assertEqual(self.qml_errors(), [])

    def test_the_sidebar_and_chip_use_the_skins_own_look(self):
        p = self.start()
        self.seed(p)
        looks = {}
        for sid in sorted(p["manager"].skins):
            p["settings_bridge"].setSkin(sid)
            pump(80)
            if p["social"].collapsed:
                p["social"].toggleCollapsed()
                pump(80)
            theme = p["skin"].theme["components"]
            self.assertTrue(theme["sidebar"]["textures"] or theme["sidebar"]["colors"], sid)
            looks[sid] = (json.dumps(theme["sidebar"], sort_keys=True), json.dumps(theme["menu"], sort_keys=True))
        self.assertEqual(len(set(looks.values())) >= 5, True)           # the skins differ, they are not one shared style


if __name__ == "__main__":
    unittest.main()
