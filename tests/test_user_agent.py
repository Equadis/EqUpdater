"""The User-Agent, and asking a compare endpoint only once per commit pair.

OctoWoW's DDoS protection challenges ``EqUpdater/<version>`` alone and lets
``EqUpdater/<version> Python-urllib/<x.y>`` through; OctoWoW asked that
EqUpdater stay first, and that the Git compare endpoint be called only when
the latest commit has actually changed (2026-10)."""

import ast
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class TestUserAgent(unittest.TestCase):

    def setUp(self):
        from equpdater import app, branding
        self.app, self.branding = app, branding

    def test_the_format(self):
        b = self.branding
        self.assertEqual(
            b.USER_AGENT,
            f"EqUpdater/{b.APP_VERSION} Python-urllib/"
            f"{sys.version_info[0]}.{sys.version_info[1]}")
        self.assertTrue(b.USER_AGENT.startswith("EqUpdater/"))
        self.assertRegex(b.USER_AGENT,
                         r"^EqUpdater/\d+(\.\d+){2,3} Python-urllib/\d+\.\d+$")
        self.assertEqual(b.user_agent("9.9.9", (3, 13, 0)),
                         "EqUpdater/9.9.9 Python-urllib/3.13")

    def test_it_is_urllibs_own_token(self):
        """The second part is exactly what urllib sends on its own."""
        import urllib.request
        default = dict(urllib.request.OpenerDirector().addheaders)["User-agent"]
        self.assertTrue(self.branding.USER_AGENT.endswith(" " + default))

    def test_the_app_sends_it(self):
        self.assertEqual(self.app.UA, self.branding.USER_AGENT)
        sent = []

        class Resp:
            headers = {}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

        def opener(req, **kw):
            sent.append(req.get_header("User-agent"))
            return Resp()
        with mock.patch.object(self.app, "secure_urlopen", opener):
            self.app._api_json("https://octowow.st/git/api/v1/repos/a/b")
        self.assertEqual(sent, [self.branding.USER_AGENT])

    def test_the_fallback_git_fetcher_sends_it(self):
        from equpdater import gitcompare
        sent = []

        class Resp:
            def read(self, *a):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *e):
                return False

        def urlopen(req, timeout=None):
            sent.append(req.get_header("User-agent"))
            return Resp()
        with mock.patch.object(gitcompare.urllib.request, "urlopen", urlopen):
            gitcompare._default_fetch("https://example.com/x")
        self.assertEqual(sent, [self.branding.USER_AGENT])

    def test_every_request_in_the_code_names_one(self):
        """Every urllib.request.Request(...) EqUpdater builds sets a
        User-Agent header: one without falls back to urllib's bare
        ``Python-urllib/x.y``, which drops the EqUpdater part."""
        missing = []
        pkg = os.path.join(ROOT, "equpdater")
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(pkg, name), encoding="utf-8").read())
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", None) == "Request"):
                    continue
                headers = next((k.value for k in node.keywords
                                if k.arg == "headers"), None)
                keys = ([getattr(k, "value", None) for k in headers.keys]
                        if isinstance(headers, ast.Dict) else [])
                if "User-Agent" not in keys:
                    missing.append(f"{name}:{node.lineno}")
        self.assertEqual(missing, [])


class TestCompareOnlyWhenTheCommitChanged(unittest.TestCase):

    REPO = "https://octowow.st/git/Dusk/LevelRange-Octo"
    OLD, NEW, NEWER = "a" * 40, "b" * 40, "c" * 40

    def setUp(self):
        from equpdater import app
        from equpdater.gitcompare import Ancestry
        self.app, self.Ancestry = app, Ancestry
        self.cfg = {}
        self.asked = []
        self.answer = Ancestry.REMOTE_AHEAD

        def update(mutator, *a, **k):
            mutator(self.cfg)
            return self.cfg

        def ancestry(url, installed, remote, fetch=None, **kw):
            self.asked.append((installed, remote))
            return self.answer
        self.patches = [
            mock.patch.object(app, "load_config", lambda *a, **k: self.cfg),
            mock.patch.object(app, "update_config", update),
            mock.patch.object(app, "ancestry", ancestry),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def check(self, installed, remote, url=None, force=False):
        return self.app.addon_ancestry(url or self.REPO, installed, remote,
                                       force=force)

    def test_a_pending_update_is_compared_once(self):
        A = self.Ancestry
        self.assertIs(self.check(self.OLD, self.NEW), A.REMOTE_AHEAD)
        for _ in range(5):                  # five more verifies
            self.assertIs(self.check(self.OLD, self.NEW), A.REMOTE_AHEAD)
        self.assertEqual(self.asked, [(self.OLD, self.NEW)])
        # The same repository written another way is the same question.
        self.check(self.OLD, self.NEW, url=self.REPO.upper() + ".git")
        self.check(self.OLD, self.NEW, force=True)       # definite: kept
        self.assertEqual(len(self.asked), 1)

    def test_a_new_latest_commit_is_compared(self):
        self.check(self.OLD, self.NEW)
        self.check(self.OLD, self.NEWER)
        self.assertEqual(self.asked, [(self.OLD, self.NEW), (self.OLD, self.NEWER)])

    def test_the_same_commit_never_asks(self):
        self.assertIs(self.check(self.NEW, self.NEW), self.Ancestry.IDENTICAL)
        self.assertIs(self.check("", self.NEW), self.Ancestry.UNKNOWN)
        self.assertEqual(self.asked, [])

    def test_unknown_is_asked_again_only_after_a_while(self):
        """Blocked or failed: not re-asked at every verify, but not
        remembered for good either."""
        self.answer = self.Ancestry.UNKNOWN
        self.check(self.OLD, self.NEW)
        self.check(self.OLD, self.NEW)
        self.assertEqual(len(self.asked), 1)
        entry = next(iter(self.cfg["addon_ancestry_cache"].values()))
        entry["timestamp"] = time.time() - self.app.ADDON_ANCESTRY_RETRY - 1
        self.answer = self.Ancestry.REMOTE_AHEAD
        self.assertIs(self.check(self.OLD, self.NEW), self.Ancestry.REMOTE_AHEAD)
        self.assertEqual(len(self.asked), 2)

    def test_a_refresh_retries_an_unknown(self):
        self.answer = self.Ancestry.UNKNOWN
        self.check(self.OLD, self.NEW)
        self.check(self.OLD, self.NEW, force=True)
        self.assertEqual(len(self.asked), 2)

    def test_the_cache_stays_small(self):
        with mock.patch.object(self.app, "ADDON_ANCESTRY_CACHE_MAX", 3):
            for i in range(6):
                self.check(self.OLD, f"{i:040d}")
        self.assertEqual(len(self.cfg["addon_ancestry_cache"]), 3)

    def test_the_verify_uses_it(self):
        import inspect
        src = inspect.getsource(self.app.EqUpdaterApp._addons_verify)
        self.assertIn("addon_ancestry(", src)
        self.assertNotRegex(src, r"(?<!addon_)ancestry\(rec")


if __name__ == "__main__":
    unittest.main()
