"""Why unrelated addons all failed their source check at once.

Twenty GitHub addons cost twenty REST API requests per refresh, out of 60 an
hour shared with every mod release check. When the allowance ran out every
addon read "Could not reach the addon's source" although each repository was
fine, and the cause, which the lookup knew, was thrown away. The check now
reads git's ref advertisement, which does not spend that allowance."""

import email.message
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from equpdater import app  # noqa: E402

OLD = "a" * 40
NEW = "b" * 40
TAG_OBJECT = "c" * 40
REPO = "https://github.com/OldManAlpha/aux-addon"


def pkt(text: bytes) -> bytes:
    return b"%04x" % (len(text) + 4) + text


def advertisement(*refs, caps=b"multi_ack symref=HEAD:refs/heads/main"):
    out = pkt(b"# service=git-upload-pack\n") + b"0000"
    for i, (sha, name) in enumerate(refs):
        line = f"{sha} {name}".encode()
        out += pkt(line + (b"\0" + caps if i == 0 else b"") + b"\n")
    return out + b"0000"


class Response(io.BytesIO):
    def __init__(self, body, content_type="application/x-git-upload-pack-advertisement"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def limited(url="https://api.github.com/repos/a/b/commits?per_page=1"):
    headers = email.message.Message()
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "2000000000"
    return urllib.error.HTTPError(url, 403, "rate limit exceeded", headers,
                                  io.BytesIO())


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = {}

        def update(fn, *a, **k):
            fn(self.cfg)
            return self.cfg
        for p in (mock.patch.object(app, "load_config", lambda *a, **k: self.cfg),
                  mock.patch.object(app, "update_config", update),
                  mock.patch.object(app, "log")):
            p.start()
            self.addCleanup(p.stop)
        app.ADDON_LOOKUP_FAILURES.clear()


class TestRefs(Base):
    def lookup(self, body, pin=None):
        with mock.patch.object(app, "secure_urlopen",
                               return_value=Response(body)) as op:
            sha = app._git_refs_sha(REPO, pin)
        return sha, op.call_args[0][0].full_url

    def test_default_branch_is_the_first_line(self):
        sha, url = self.lookup(advertisement(
            (NEW, "HEAD"), (NEW, "refs/heads/main"), (OLD, "refs/heads/dev")))
        self.assertEqual(sha, NEW)
        self.assertEqual(
            url, REPO + ".git/info/refs?service=git-upload-pack")

    def test_pinned_branch(self):
        sha, _ = self.lookup(advertisement(
            (NEW, "HEAD"), (NEW, "refs/heads/main"), (OLD, "refs/heads/dev")),
            pin="dev")
        self.assertEqual(sha, OLD)

    def test_annotated_tag_peels_to_its_commit(self):
        sha, _ = self.lookup(advertisement(
            (NEW, "HEAD"), (TAG_OBJECT, "refs/tags/v1"), (OLD, "refs/tags/v1^{}")),
            pin="v1")
        self.assertEqual(sha, OLD)

    def test_a_pin_that_is_not_listed_is_no_answer(self):
        self.assertIsNone(self.lookup(advertisement((NEW, "HEAD")), pin="gone")[0])

    def test_an_empty_repository_is_no_answer(self):
        self.assertIsNone(self.lookup(advertisement(
            ("0" * 40, "capabilities^{}")))[0])

    def test_an_html_page_is_not_mistaken_for_refs(self):
        with mock.patch.object(app, "secure_urlopen",
                               return_value=Response(b"<html>", "text/html")):
            with self.assertRaises(ValueError):
                app._git_refs_sha(REPO)


class TestLookupSurvivesTheApiLimit(Base):
    def test_twenty_addons_spend_none_of_the_allowance(self):
        api = mock.Mock(side_effect=limited())
        with mock.patch.object(app, "_git_refs_sha", return_value=NEW), \
                mock.patch.object(app, "_api_json", api):
            for i in range(20):
                self.assertEqual(app.addon_remote_sha(
                    f"https://github.com/o/addon{i}", force=True), NEW)
        self.assertEqual(api.call_count, 0)

    def test_when_refs_are_unavailable_the_api_still_answers(self):
        with mock.patch.object(app, "_git_refs_sha",
                               side_effect=urllib.error.URLError("blocked")), \
                mock.patch.object(app, "_api_json",
                                  return_value=[{"sha": NEW}]):
            self.assertEqual(app.addon_remote_sha(REPO, force=True), NEW)

    def test_a_commit_pin_goes_to_the_api(self):
        refs = mock.Mock()
        with mock.patch.object(app, "_git_refs_sha", refs), \
                mock.patch.object(app, "_api_json",
                                  return_value={"sha": OLD}):
            self.assertEqual(app.addon_remote_sha(REPO, ref=OLD, force=True), OLD)
        self.assertEqual(refs.call_count, 0)

    def test_both_failing_keeps_the_specific_reason(self):
        with mock.patch.object(app, "_git_refs_sha",
                               side_effect=urllib.error.URLError("blocked")), \
                mock.patch.object(app, "_api_json", side_effect=limited()):
            self.assertIsNone(app.addon_remote_sha(REPO, force=True))
        reason = app.addon_lookup_reason(REPO)
        self.assertIn("limit", reason.lower())

    def test_failure_is_not_cached_and_recovers(self):
        with mock.patch.object(app, "_git_refs_sha",
                               side_effect=urllib.error.URLError("offline")), \
                mock.patch.object(app, "_api_json",
                                  side_effect=urllib.error.URLError("offline")):
            self.assertIsNone(app.addon_remote_sha(REPO))
        self.assertIn("offline", app.addon_lookup_reason(REPO))
        self.assertEqual(self.cfg.get("addon_sha_cache", {}), {})
        with mock.patch.object(app, "_git_refs_sha", return_value=NEW):
            self.assertEqual(app.addon_remote_sha(REPO), NEW)
        self.assertIsNone(app.addon_lookup_reason(REPO))


class TestReasons(unittest.TestCase):
    def test_a_real_rate_limit_says_until_when(self):
        self.assertIn("limit", app._describe_net_error(limited()).lower())

    def test_a_403_that_is_not_the_limit_is_not_called_one(self):
        headers = email.message.Message()
        headers["X-RateLimit-Remaining"] = "41"
        err = urllib.error.HTTPError("https://api.github.com/repos/a/b", 403,
                                     "Forbidden", headers, io.BytesIO())
        self.assertIn("403", app._describe_net_error(err))

    def test_a_missing_repository(self):
        err = urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found",
                                     email.message.Message(), io.BytesIO())
        self.assertEqual(app._describe_net_error(err),
                         "repository or branch not found")


if __name__ == "__main__":
    unittest.main()
