"""A SMOKE test of the DEPLOYED Worker over https/wss, from this computer. Opt-in: it creates a handful of real
accounts on the hosted test environment (accounts are kept for good there) and uses its rate limits.

    set EQU3_TEST_LIVE_URL=https://equpdater-social.<subdomain>.workers.dev
    set EQU3_TEST_LIVE_KEY=<a tester key>           (EQU3_TEST_LIVE_ADMIN=<admin key> adds the metrics check)

It checks that the deployment behaves like the Worker tested locally. It is one computer talking to Cloudflare's
network: it is NOT a cross-machine test, and what it proves is stated in docs/v3/SOCIAL_TEST_BACKEND.md."""

import os
import time
import unittest

from tests.social_harness import Client, LiveBackend, wait_for
from tests.social_scenarios import Scenarios, uname

URL, KEY = os.environ.get("EQU3_TEST_LIVE_URL", ""), os.environ.get("EQU3_TEST_LIVE_KEY", "")
GREEN, YELLOW, RED = "#3ddc84", "#f2c744", "#ef5350"


@unittest.skipUnless(URL and KEY, "set EQU3_TEST_LIVE_URL and EQU3_TEST_LIVE_KEY to smoke-test the deployed Worker")
class TestDeployedWorker(Scenarios, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = LiveBackend(URL, KEY)

    def test_https_only_a_key_is_needed_and_names_are_kept(self):
        self.assertTrue(URL.startswith("https://"))
        c, thief = self.make(), self.make()
        self.assertTrue(c.auth.requires_key())
        self.assertFalse(c.login(uname("lv"), key="not-the-key"))
        self.assertIn("tester key", c.account.message)
        name = uname("lv")
        self.assertTrue(c.login(name))
        c.logout()
        self.assertFalse(thief.login(name.upper()))
        self.assertIn("already registered", thief.account.message)
        self.assertTrue(c.login(name))                                                    # the owner gets back in with no typing

    def test_presence_over_wss_is_pushed_and_fast_and_invisible_leaks_nothing(self):
        a, b, na, nb = self.pair()
        self.assertTrue(a.provider.link.connected)
        times = []
        for status, dot in (("dnd", RED), ("away", YELLOW), ("online", GREEN)):
            t0 = time.time()
            b.svc.set_status(status)
            self.assertTrue(wait_for(lambda d=dot: a.dot(nb) == d, 5), status)
            times.append(time.time() - t0)
        print(f"\n  deployed push latency, three changes: {', '.join(f'{t * 1000:.0f} ms' for t in times)}")
        self.assertLess(max(times), 3.0)
        mark = len(a.messages)
        b.svc.set_status("invisible")
        self.assertTrue(a.sees(nb, "Offline", 5))
        self.assertNotIn("in_game", " ".join(str(m) for m in a.messages[mark:]))
        before = len(a.http.log) + len(b.http.log)
        time.sleep(3)
        self.assertEqual(len(a.http.log) + len(b.http.log), before)                       # nothing polls

    def test_friends_and_the_recovery_transfer_work_on_the_deployment(self):
        a, b, na, nb = self.pair()
        a.svc.toggle_favourite(a.book.all()[0].id)
        self.assertTrue(wait_for(lambda: a.rows()[nb]["favourite"], 5))
        code = a.vault.get("recovery", na)
        other = self.make()
        self.assertTrue(other.login(na, recovery=code))
        self.assertTrue(wait_for(lambda: other.names() == [nb] and other.rows()[nb]["favourite"], 6))
        self.assertTrue(wait_for(lambda: a.account.state != "signed_in", 8))              # the first computer was signed out
        self.assertIn("No user", other.svc.add_friend(uname("nobody")))

    @unittest.skipUnless(os.environ.get("EQU3_TEST_LIVE_ADMIN"), "no admin key")
    def test_metrics_need_the_admin_key_and_hold_no_names(self):
        a, b, na, nb = self.pair()
        self.assertEqual(a.be.call("GET", "/v1/admin/metrics", token=a.account.tokens.access)[0], 401)
        status, obj, _h = a.be.call("GET", "/v1/admin/metrics", token=os.environ["EQU3_TEST_LIVE_ADMIN"])
        self.assertEqual(status, 200)
        self.assertGreaterEqual(obj["connected"], 2)
        self.assertNotIn(na, str(obj))


if __name__ == "__main__":
    unittest.main()
