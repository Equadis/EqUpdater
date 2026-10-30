"""The News tab's forum reader.

The fixtures follow phpBB 3.3 prosilver markup: a listing where old pinned
announcements sit above newer topics, where a pinned topic has a reply newer
than everything else, where every link carries a session id, and a topic page
whose opening post is followed by replies. Those are the traps the reader has
to walk around; the fetch tests check that every failure names its stage.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from equpdater import news                                  # noqa: E402

SID = "sid=0123456789abcdef0123456789abcdef"


def row(kind, tid, title, author, created, last_by, last_at, time_tag=True):
    started = (f'<time datetime="{created}">{created}</time>' if time_tag
               else created)
    return f'''
    <li class="row bg1 {kind}">
      <dl class="row-item {kind}_read">
        <dt title="No unread posts">
          <div class="list-inner">
            <a href="./viewtopic.php?t={tid}&amp;{SID}" class="topictitle">{title}</a>
            <br />
            <div class="responsive-show" style="display: none;">
              Last post by <a href="./memberlist.php?mode=viewprofile&amp;u=9&amp;{SID}"
                 class="username">{last_by}</a> &laquo;
              <a href="./viewtopic.php?p=99&amp;{SID}#p99">
                <time datetime="{last_at}">{last_at}</time></a>
            </div>
            <div class="topic-poster responsive-hide left-box">
              by <a href="./memberlist.php?mode=viewprofile&amp;u=2&amp;{SID}"
                 style="color: #AA0000;" class="username-coloured">{author}</a>
              &raquo; {started}
            </div>
          </div>
        </dt>
        <dd class="posts">4 <dfn>Replies</dfn></dd>
        <dd class="views">900 <dfn>Views</dfn></dd>
        <dd class="lastpost">
          <span><dfn>Last post </dfn>by
            <a href="./memberlist.php?mode=viewprofile&amp;u=9&amp;{SID}"
               class="username">{last_by}</a>
            <a href="./viewtopic.php?p=99&amp;{SID}#p99" title="Go to last post">»</a>
            <br /><time datetime="{last_at}">{last_at}</time>
          </span>
        </dd>
      </dl>
    </li>'''


ANNOUNCEMENTS = f'''<!DOCTYPE html><html><head><title>Announcements - OctoWoW</title></head>
<body id="phpbb"><div class="forumbg announcement"><ul class="topiclist topics">
{row("global-announce", 12, "Server Rules", "Kestrel",
     "2026-04-02T10:00:00+00:00", "Newbie", "2026-09-24T12:30:00+00:00")}
{row("sticky", 15, "Read before posting", "Kestrel",
     "2026-04-05T10:00:00+00:00", "Someone", "2026-09-20T08:00:00+00:00")}
</ul></div><div class="forumbg"><ul class="topiclist topics">
{row("", 2595, "DDoS Updates", "Kestrel",
     "2026-09-24T07:06:00+00:00", "Kestrel", "2026-09-24T07:06:00+00:00")}
{row("", 2594, "Whitelist Updates", "Kestrel",
     "2026-09-20T18:00:00+00:00", "Player", "2026-09-23T09:00:00+00:00")}
</ul></div></body></html>'''


def topic_page(tid, title, first_body, first_author="Kestrel",
               created="2026-09-23T16:00:00+00:00"):
    reply = '''
    <div id="p{pid}" class="post has-profile bg1">
      <div class="inner"><div class="postbody"><div id="post_content{pid}">
        <h3><a href="#p{pid}">Re: {title}</a></h3>
        <p class="author"><span class="responsive-hide">by <strong>
          <a href="./memberlist.php?u=9" class="username">Replier{pid}</a></strong>
          &raquo; </span><time datetime="2026-09-24T09:00:00+00:00">x</time></p>
        <div class="content">This is reply {pid}, not the patch notes.</div>
      </div></div></div>
    </div>'''
    return f'''<!DOCTYPE html><html><body id="phpbb">
    <div class="rules"><div class="inner"><div class="content">Forum rules text</div></div></div>
    <div id="p9000" class="post has-profile bg2">
      <div class="inner"><div class="postbody"><div id="post_content9000">
        <h3 class="first"><a href="#p9000">{title}</a></h3>
        <p class="author"><a class="unread" href="#"><span class="sr-only">Post</span></a>
          <span class="responsive-hide">by <strong>
          <a href="./memberlist.php?u=2" style="color:#AA0000"
             class="username-coloured">{first_author}</a></strong> &raquo; </span>
          <time datetime="{created}">Wed Sep 23, 2026 4:00 pm</time></p>
        <div class="content">{first_body}</div>
        <div id="sig9000" class="signature">my signature</div>
      </div></div></div>
    </div>
    {reply.format(pid=9001, title=title)}
    {reply.format(pid=9002, title=title)}
    <script>var x = "<div class='content'>not this</div>";</script>
    </body></html>'''


PATCH_BODY = ('<strong>Fixes</strong><br>Fixed Onyxia breath.<br>'
              '<ul><li>Hunters: pets no longer despawn</li>'
              '<li>Mages: blink works again</li></ul>'
              '<blockquote><div><cite>Dev wrote:</cite>quoted bit</div></blockquote>')

CHALLENGE = '''<!DOCTYPE HTML><html lang="en-US"><head>
<title>Just a moment please...</title><script src="/bf.jquery.max.js"></script>
</head><body>checking your browser</body></html>'''


class TestListing(unittest.TestCase):
    def setUp(self):
        self.rows = news.parse_forum_topics(ANNOUNCEMENTS, news.forum_url(2))

    def test_every_row_is_read(self):
        self.assertEqual({r.topic_id for r in self.rows}, {12, 15, 2595, 2594})

    def test_pinned_rows_are_not_newest(self):
        """Old pinned topics head the page; the newest post is further down."""
        self.assertEqual(self.rows[0].topic_id, 12)          # page order
        newest = news.newest_first(self.rows)
        self.assertEqual([r.topic_id for r in newest], [2595, 2594, 15, 12])
        self.assertEqual(newest[0].title, "DDoS Updates")

    def test_creation_date_not_last_post(self):
        """Topic 12 had a reply today; it was still started in April."""
        r = next(r for r in self.rows if r.topic_id == 12)
        self.assertEqual(r.created_at,
                         datetime(2026, 4, 2, 10, tzinfo=timezone.utc))
        self.assertEqual(r.author, "Kestrel")                # not "Newbie"
        self.assertEqual(r.kind, "announce")

    def test_urls_are_canonical(self):
        r = next(r for r in self.rows if r.topic_id == 2595)
        self.assertEqual(r.url, "https://octowow.st/forum/viewtopic.php?t=2595")
        self.assertNotIn("sid", r.url)

    def test_text_dates_on_older_templates(self):
        html = ("<ul>" + row("", 7, "Old style", "A", "Thu Sep 24, 2026 7:06 am",
                              "B", "2026-09-25T00:00:00+00:00", time_tag=False)
                + "</ul>")
        (r,) = news.parse_forum_topics(html)
        self.assertEqual(r.created_at,
                         datetime(2026, 9, 24, 7, 6, tzinfo=timezone.utc))

    def test_undated_rows_fall_back_to_topic_id(self):
        rows = [news.TopicRow(5, "a", None, None, "u"),
                news.TopicRow(9, "b", None, None, "u"),
                news.TopicRow(1, "c", None,
                              datetime(2020, 1, 1, tzinfo=timezone.utc), "u")]
        self.assertEqual([r.topic_id for r in news.newest_first(rows)], [1, 9, 5])

    def test_topic_ids_from_every_link_style(self):
        base = news.forum_url(2)
        for href, want in (("./viewtopic.php?f=2&t=31&sid=abc", 31),
                           ("viewtopic.php?t=31&amp;sid=abc", 31),
                           ("https://octowow.st/forum/viewtopic.php?t=31", 31),
                           ("./some-title-topic31.html", 31),
                           ("./viewtopic.php?p=99#p99", None)):
            with self.subTest(href=href):
                self.assertEqual(news.topic_id_from_href(href, base), want)


class TestFirstPost(unittest.TestCase):
    def test_opening_post_only(self):
        author, created, content = news.parse_first_post(
            topic_page(2600, "2026-09-23", PATCH_BODY))
        self.assertEqual(author, "Kestrel")
        self.assertEqual(created, datetime(2026, 9, 23, 16, tzinfo=timezone.utc))
        self.assertIn("Fixed Onyxia breath.", content)
        self.assertIn("• Hunters: pets no longer despawn", content)
        self.assertIn("• Mages: blink works again", content)
        for absent in ("reply 9001", "reply 9002", "Replier", "Forum rules",
                       "my signature", "not this"):
            self.assertNotIn(absent, content)

    def test_line_breaks_survive(self):
        _, _, content = news.parse_first_post(topic_page(1, "t", PATCH_BODY))
        self.assertIn("Fixes\nFixed Onyxia breath.", content)

    def test_page_without_a_post(self):
        self.assertIsNone(news.parse_first_post("<html><body>nothing</body></html>"))


class TestChallenge(unittest.TestCase):
    def test_detected_by_header(self):
        self.assertTrue(news.is_challenge_page("", {"X-BF-Challenge": "pending"}))

    def test_detected_by_page(self):
        self.assertTrue(news.is_challenge_page(CHALLENGE))

    def test_forum_is_not_a_challenge(self):
        self.assertFalse(news.is_challenge_page(ANNOUNCEMENTS, {}))


class _Headers(dict):
    def get_content_charset(self):
        return "utf-8"


class _Resp:
    def __init__(self, body, headers=None):
        self.body = body.encode("utf-8")
        self.headers = _Headers(headers or {})

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSite:
    """A forum reachable only through `opener`, recording what was asked."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def opener(self, req, timeout):
        url = req.full_url
        self.asked.append(url)
        for key, page in self.pages.items():
            if key in url:
                if isinstance(page, Exception):
                    raise page
                return _Resp(*page) if isinstance(page, tuple) else _Resp(page)
        raise OSError("404 " + url)


def kw(site):
    return {"opener": site.opener, "user_agent": "EqUpdater/test", "timeout": 1}


CHANGELOG = ("<ul>" + row("sticky", 3, "How to report bugs", "Kestrel",
                          "2026-01-01T00:00:00+00:00", "X", "2026-09-24T11:00:00+00:00")
             + row("", 2600, "2026-09-23", "Kestrel", "2026-09-23T16:00:00+00:00",
                   "Replier9002", "2026-09-24T09:00:00+00:00")
             + row("", 2580, "2026-09-16", "Kestrel", "2026-09-16T16:00:00+00:00",
                   "Y", "2026-09-17T09:00:00+00:00") + "</ul>")


class TestFetch(unittest.TestCase):
    def test_announcement_is_the_newest_started_topic(self):
        site = FakeSite({"viewforum.php?f=2": ANNOUNCEMENTS,
                         "viewtopic.php?t=2595": topic_page(
                             2595, "DDoS Updates", "Mitigation is live.",
                             created="2026-09-24T07:06:00+00:00")})
        item = news.fetch_latest_post(2, **kw(site)).to_item()
        self.assertEqual(item["title"], "DDoS Updates")
        self.assertEqual(item["body"], "Mitigation is live.")
        self.assertEqual(item["url"], "https://octowow.st/forum/viewtopic.php?t=2595")
        self.assertEqual(item["date"], "2026-09-24T07:06:00+00:00")
        # Two requests: the listing and that one topic. Nothing else.
        self.assertEqual(site.asked, [news.forum_url(2), news.topic_url(2595)])

    def test_patch_notes_are_one_listing_request(self):
        site = FakeSite({"viewforum.php?f=4": CHANGELOG})
        rows = news.fetch_topic_list(4, 8, **kw(site))
        self.assertEqual(site.asked, [news.forum_url(4)])
        items = [r.to_item() for r in rows]
        self.assertEqual([i["title"] for i in items],
                         ["2026-09-23", "2026-09-16", "How to report bugs"])
        self.assertEqual(items[0]["author"], "Kestrel")          # not the replier
        self.assertEqual(items[0]["date"], "2026-09-23T16:00:00+00:00")
        self.assertEqual(items[0]["url"],
                         "https://octowow.st/forum/viewtopic.php?t=2600")

    def test_patch_note_limit(self):
        site = FakeSite({"viewforum.php?f=4": CHANGELOG})
        self.assertEqual(len(news.fetch_topic_list(4, 2, **kw(site))), 2)

    def test_first_post_of_a_patch_note_is_post_one(self):
        site = FakeSite({"t=2600": topic_page(2600, "2026-09-23", PATCH_BODY)})
        (row2600,) = [r for r in news.parse_forum_topics(CHANGELOG)
                      if r.topic_id == 2600]
        post = news.fetch_first_post(row2600, **kw(site))
        self.assertIn("Fixed Onyxia breath.", post.content)
        self.assertNotIn("reply", post.content)
        self.assertNotIn("quoted bit", post.content)             # quotes dropped
        self.assertEqual(post.author, "Kestrel")

    def test_listing_asks_for_creation_order(self):
        site = FakeSite({"viewforum.php?f=4": CHANGELOG})
        news.fetch_topic_list(4, 1, **kw(site))
        self.assertIn("sk=tt", site.asked[0])

    def test_challenge_is_named_not_reported_as_empty(self):
        site = FakeSite({"viewforum.php": (CHALLENGE, {"X-BF-Challenge": "pending"})})
        with self.assertRaises(news.ForumBlockedError) as cm:
            news.fetch_latest_post(2, **kw(site))
        self.assertEqual(cm.exception.stage, "forum 2 listing")
        self.assertIn("DDoS-protection", cm.exception.short)
        self.assertEqual(len(site.asked), 1)                     # no retry storm

    def test_challenge_on_the_topic_page(self):
        site = FakeSite({"viewforum.php?f=2": ANNOUNCEMENTS,
                         "viewtopic": CHALLENGE})
        with self.assertRaises(news.ForumBlockedError) as cm:
            news.fetch_latest_post(2, **kw(site))
        self.assertEqual(cm.exception.stage, "topic 2595")

    def test_a_page_with_no_topics_is_an_error(self):
        site = FakeSite({"viewforum.php": "<html><body>maintenance</body></html>"})
        with self.assertRaises(news.ForumError) as cm:
            news.fetch_topic_list(4, 8, **kw(site))
        self.assertEqual(cm.exception.stage, "forum 4 listing parse")

    def test_a_topic_page_with_no_post_is_an_error(self):
        site = FakeSite({"viewforum.php?f=2": ANNOUNCEMENTS,
                         "viewtopic": "<html><body>gone</body></html>"})
        with self.assertRaises(news.ForumError) as cm:
            news.fetch_latest_post(2, **kw(site))
        self.assertEqual(cm.exception.stage, "topic 2595 parse")

    def test_network_errors_name_the_stage(self):
        site = FakeSite({})
        with self.assertRaises(news.ForumError) as cm:
            news.fetch_latest_post(2, **kw(site))
        self.assertEqual(cm.exception.stage, "forum 2 listing")
        self.assertIn("forum 2 listing failed", str(cm.exception))

    def test_no_json_feed_is_consulted(self):
        site = FakeSite({"viewforum.php?f=4": CHANGELOG})
        news.fetch_topic_list(4, 8, **kw(site))
        self.assertFalse([u for u in site.asked
                          if "octonews" in u or u.endswith(".json")])
        self.assertFalse(hasattr(news, "OCTONEWS_URL"))


FEED_ITEM = {"id": "2026-10-08-ddos", "title": "DDoS Updates",
             "date": "2026-10-08", "body": "Mitigation is live.\nThanks!",
             "author": "Kestrel",
             "url": "https://octowow.st/forum/viewtopic.php?t=2595"}


def feed(*items, **top):
    return json.dumps(dict({"items": list(items)}, **top))


class TestNewsFeed(unittest.TestCase):
    """news.json against OctoLauncher's NewsFeedSchema. No network."""

    def test_valid_feed_maps_to_the_news_item(self):
        (item,) = news.parse_news_feed(feed(FEED_ITEM))
        self.assertEqual(item, {"id": "2026-10-08-ddos", "title": "DDoS Updates",
                                "date": "2026-10-08",
                                "body": "Mitigation is live.\nThanks!",
                                "html": "", "author": "Kestrel",
                                "url": "https://octowow.st/forum/viewtopic.php?t=2595"})

    def test_multiple_items_keep_the_feed_order(self):
        older = dict(FEED_ITEM, id="old", title="Older", date="2026-09-01")
        items = news.parse_news_feed(feed(FEED_ITEM, older))
        self.assertEqual([i["id"] for i in items], ["2026-10-08-ddos", "old"])

    def test_empty_items_is_valid(self):
        self.assertEqual(news.parse_news_feed('{"items": []}'), [])

    def test_author_and_url_optional_and_not_invented(self):
        for item in ({k: v for k, v in FEED_ITEM.items() if k != "author"},
                     dict(FEED_ITEM, author=None)):
            with self.subTest(item=item):
                self.assertIsNone(news.parse_news_feed(feed(item))[0]["author"])
        no_url = {k: v for k, v in FEED_ITEM.items() if k != "url"}
        self.assertIsNone(news.parse_news_feed(feed(no_url))[0]["url"])

    def test_unknown_extra_fields_are_ignored(self):
        self.assertEqual(len(news.parse_news_feed(
            feed(dict(FEED_ITEM, pinned=True), version=2))), 1)

    def assertMalformed(self, text):
        with self.assertRaises(news.ForumError) as cm:
            news.parse_news_feed(text)
        self.assertEqual(cm.exception.stage, "news.json parse")
        self.assertEqual(cm.exception.short, "the news feed was malformed")

    def test_malformed_top_level(self):
        for text in ("[]", '"news"', "null", "{}", '{"items": {}}',
                     '{"items": null}', '{"Items": []}'):
            with self.subTest(text=text):
                self.assertMalformed(text)

    def test_missing_required_fields(self):
        for key in ("id", "title", "date", "body"):
            with self.subTest(missing=key):
                self.assertMalformed(feed({k: v for k, v in FEED_ITEM.items()
                                           if k != key}))

    def test_invalid_field_types(self):
        for key, value in (("id", 7), ("title", None), ("date", 20261008),
                           ("body", ["x"]), ("author", 5), ("author", {})):
            with self.subTest(key=key, value=value):
                self.assertMalformed(feed(dict(FEED_ITEM, **{key: value})))
        self.assertMalformed(feed("not an object"))

    def test_invalid_url(self):
        for link in ("viewtopic.php?t=1", "/forum/x", "ftp://octowow.st/x",
                     "javascript:alert(1)", "https://", "", 42):
            with self.subTest(url=link):
                self.assertMalformed(feed(dict(FEED_ITEM, url=link)))

    def test_one_bad_item_rejects_the_whole_feed(self):
        self.assertMalformed(feed(FEED_ITEM, {"id": "x"}))

    def test_malformed_json(self):
        for text in ("", "{", "<html>oops</html>", '{"items": [}'):
            with self.subTest(text=text):
                self.assertMalformed(text)

    # ── fetching ───────────────────────────────────────────────────────────

    def test_fetch_is_one_request_to_news_json(self):
        site = FakeSite({"news.json": feed(FEED_ITEM)})
        items = news.fetch_news_feed(**kw(site))
        self.assertEqual(site.asked, ["https://octowow.st/news.json"])
        self.assertEqual(items[0]["url"], FEED_ITEM["url"])

    def test_timeout_and_http_errors(self):
        import socket
        import urllib.error
        for exc in (socket.timeout("timed out"),
                    urllib.error.HTTPError(news.NEWS_FEED_URL, 503, "busy", {}, None),
                    urllib.error.URLError("unreachable")):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(news.ForumError) as cm:
                    news.fetch_news_feed(**kw(FakeSite({"news.json": exc})))
                self.assertEqual(cm.exception.stage, "news.json")
                self.assertEqual(cm.exception.short,
                                 "octowow.st could not be reached")

    def test_challenge_is_blocked_not_content(self):
        for page in (CHALLENGE, (feed(FEED_ITEM), {"X-BF-Challenge": "pending"})):
            with self.subTest(page=str(page)[:30]):
                with self.assertRaises(news.ForumBlockedError) as cm:
                    news.fetch_news_feed(**kw(FakeSite({"news.json": page})))
                self.assertEqual(cm.exception.stage, "news.json")
                self.assertIn("DDoS-protection", cm.exception.short)

    def test_the_timeout_is_passed_through(self):
        seen = []

        def opener(req, timeout):
            seen.append(timeout)
            return _Resp(feed(FEED_ITEM))
        news.fetch_news_feed(opener=opener, user_agent="t", timeout=8)
        self.assertEqual(seen, [8])

try:
    import tkinter  # noqa: F401
    HAVE_TK = True
except ImportError:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "the app needs tkinter")
class TestAnnouncementsFallbackOrder(unittest.TestCase):
    """Forum 2 first; news.json only when the forum fails; if both fail the
    forum's error is raised and the panel keeps its cached post."""

    def setUp(self):
        # The app's own news module: other test files reload the package,
        # and an exception class from a stale copy is not the one the app
        # catches.
        from equpdater import app
        self.app, self.n = app, sys.modules["equpdater.news"]
        self.POST = self.n.ForumPost(2848, "Realm restart", "Kestrel",
                                     "2026-10-09T12:00:00+00:00",
                                     "https://octowow.st/forum/viewtopic.php?t=2848", "b")

    def run_with(self, forum, feed):
        from unittest import mock
        app = self.app
        calls = []

        def topic(*a, **k):
            calls.append("forum")
            if isinstance(forum, Exception):
                raise forum
            return forum

        def news_feed(**k):
            calls.append("news.json")
            if isinstance(feed, Exception):
                raise feed
            return feed
        with mock.patch.object(app, "fetch_latest_post", topic), \
                mock.patch.object(app, "fetch_news_feed", news_feed):
            try:
                return app.fetch_featured_post(), calls
            except self.n.ForumError as exc:
                return exc, calls

    def blocked(self):
        return self.n.ForumBlockedError("forum 2 listing", "u", "challenge",
                                      short="octowow.st is showing its "
                                            "DDoS-protection check to apps right now")

    def test_forum_first(self):
        item, calls = self.run_with(self.POST, [dict(FEED_ITEM)])
        self.assertEqual(item["title"], "Realm restart")
        self.assertEqual(calls, ["forum"])

    def test_news_json_when_the_forum_is_blocked(self):
        item, calls = self.run_with(self.blocked(),
                                    self.n.parse_news_feed(feed(FEED_ITEM)))
        self.assertEqual(item["id"], "2026-10-08-ddos")
        self.assertEqual(calls, ["forum", "news.json"])
        # Says why the feed stands in (shown under the post, not cached).
        self.assertIn("DDoS-protection", item["_note"])

    def test_the_newest_dated_feed_item_wins(self):
        """A pinned older post may lead the feed: the panel shows the
        newest by date, not the first."""
        pinned = dict(FEED_ITEM, id="beta", title="Beta Test Info",
                      date="2026-04-28")
        newer = dict(FEED_ITEM, id="new", date="2026-10-09")
        item, _calls = self.run_with(self.blocked(),
                                     self.n.parse_news_feed(feed(pinned, FEED_ITEM, newer)))
        self.assertEqual(item["id"], "new")

    def test_empty_feed_keeps_the_cache(self):
        item, _calls = self.run_with(self.blocked(), [])
        self.assertIsNone(item)            # _load_featured keeps the cache

    def test_both_failing_reports_the_forum(self):
        malformed = self.n.ForumError("news.json parse", "u", "bad",
                                    short="the news feed was malformed")
        exc, calls = self.run_with(self.blocked(), malformed)
        self.assertIsInstance(exc, self.n.ForumBlockedError)
        self.assertEqual(calls, ["forum", "news.json"])


if __name__ == "__main__":
    unittest.main()


# ──────────────────────────────────────────────────────────────────────────────
#  Regression: topic titles, never latest-post links or replies (2026-10-24)
# ──────────────────────────────────────────────────────────────────────────────
#
# From 2026-10-09 the panels read one thread each newest-post-first, and
# showed players' replies ("We patiently wait for Thorn Gorge fix...") as
# patch notes. The rows below follow OctoWoW's live forum markup (2026-10-24):
# a page-number list of viewtopic.php?t= links inside the row, the mobile
# "Last post by" line, "View the latest post" and "Mark as read" links, and a
# start date in text rather than a <time>.

def live_row(tid, title, author, started, last_by, last_pid, pages=3,
             latest_first=False):
    latest = (f'''<div class="responsive-show" style="display: none;">
      Last post by <a href="./memberlist.php?mode=viewprofile&amp;u=12438&amp;{SID}"
        class="username">{last_by}</a> &laquo; <a href="./viewtopic.php?p={last_pid}&amp;{SID}#p{last_pid}"
        title="Go to last post">Mon Oct 12, 2026 1:05 pm</a></div>''')
    title_link = (f'<a href="./viewtopic.php?t={tid}&amp;{SID}" '
                  f'class="topictitle">{title}</a><br />')
    pagination = "".join(
        f'<li><a href="./viewtopic.php?t={tid}&amp;{SID}&amp;start={10 * n}">{n + 1}</a></li>'
        for n in range(pages))
    head = latest + title_link if latest_first else title_link + latest
    return f'''
    <li class="row bg2">
      <dl class="icon topic_read_hot">
        <dt title="No unread posts"><div class="list-inner">
          {head}
          <span class="responsive-show left-box" style="display: none;">Replies: <strong>35</strong></span>
          <div class="pagination"><ul>{pagination}</ul></div>
          <div class="responsive-hide">
            by <a href="./memberlist.php?mode=viewprofile&amp;u=60&amp;{SID}"
                  class="username">{author}</a> &raquo; {started}
          </div>
          <a href="/forum/app.php/markread/topic/4/{tid}?hash=b4d0dbaa&amp;{SID}"
             class="mark-topic-read is-read" title="Mark as read" data-topic-id="{tid}"></a>
        </div></dt>
        <dd class="posts">35 <dfn>Replies</dfn></dd>
        <dd class="views">216154 <dfn>Views</dfn></dd>
        <dd class="lastpost"><span><dfn>Last post </dfn>by
          <a href="./memberlist.php?mode=viewprofile&amp;u=12438&amp;{SID}" class="username">{last_by}</a>
          <a href="./viewtopic.php?p={last_pid}&amp;{SID}#p{last_pid}" title="Go to last post"><span
             class="imageset icon_topic_latest" title="View the latest post">View the latest post</span></a>
          <br />Mon Oct 12, 2026 1:05 pm</span></dd>
      </dl>
    </li>'''


LIVE_CHANGELOG = ("<ul class=\"topiclist topics\">"
                  + live_row(3087, "2026-10-24", "Archer", "Sat Oct 24, 2026 7:42 am",
                             "Rook", 17501, pages=1)
                  + live_row(2816, "2026-10-06", "Kestrel", "Tue Oct 06, 2026 4:54 am",
                             "Covernl", 16964, pages=4, latest_first=True)
                  + live_row(2730, "2026-09-30", "Kestrel", "Wed Sep 30, 2026 6:21 am",
                             "Cinnamonspice", 17421)
                  + "</ul>")

LIVE_ANNOUNCEMENTS = ("<ul class=\"topiclist topics\">"
                      + row("announce", 3, "Beta Test Info", "Kestrel",
                            "2026-04-28T21:16:00+00:00", "Someone",
                            "2026-10-23T08:00:00+00:00")
                      + live_row(2848, "Blackwing Lair Arrives November 1st",
                                 "Kestrel", "Thu Oct 08, 2026 9:00 am",
                                 "PvPer", 17000)
                      + live_row(3086, "Cross Realm Battlegrounds", "Kestrel",
                                 "Sat Oct 24, 2026 7:37 am", "Fan", 17510, pages=1)
                      + "</ul>")


class TestTopicTitlesNotLatestPosts(unittest.TestCase):

    def rows(self, html):
        return news.parse_forum_topics(html, news.forum_url(4))

    def test_only_the_title_link_makes_a_row(self):
        rows = self.rows(LIVE_CHANGELOG)
        self.assertEqual([(r.topic_id, r.title) for r in rows],
                         [(3087, "2026-10-24"), (2816, "2026-10-06"),
                          (2730, "2026-09-30")])
        for r in rows:
            self.assertEqual(r.url, news.topic_url(r.topic_id))
            self.assertNotIn("p=", r.url)
            self.assertNotIn("start=", r.url)

    def test_the_starter_not_the_last_poster(self):
        by_id = {r.topic_id: r for r in self.rows(LIVE_CHANGELOG)}
        self.assertEqual(by_id[2816].author, "Kestrel")       # not Covernl
        self.assertEqual(by_id[3087].author, "Archer")        # not Rook
        self.assertEqual(by_id[2816].created_at.isoformat(),
                         "2026-10-06T04:54:00+00:00")         # not Oct 12

    def test_no_link_text_becomes_a_title(self):
        titles = " ".join(r.title for r in self.rows(LIVE_CHANGELOG))
        for junk in ("View the latest post", "Go to last post", "Mark as read",
                     "Covernl", "Last post", "Replies"):
            self.assertNotIn(junk, titles)
        self.assertFalse(any(r.title.isdigit() for r in self.rows(LIVE_CHANGELOG)))

    def test_latest_post_links_alone_are_not_rows(self):
        only_latest = ('<ul><li class="row"><dl><dt><div class="list-inner">'
                       '<a href="./viewtopic.php?p=17000#p17000" title="Go to last post">'
                       'View the latest post</a></div></dt></dl></li></ul>')
        self.assertEqual(self.rows(only_latest), [])


def _topic_with_replies(tid, title, first_body, author="Kestrel"):
    """A topic page: the opening post, then players' replies."""
    return topic_page(tid, title, first_body, first_author=author)


class TestNewsPanelsReadTheForums(unittest.TestCase):
    """What the app's News panels fetch, through its own functions."""

    def setUp(self):
        from unittest import mock
        from equpdater import app
        self.app = app
        self.cfg = {}

        def update(mutator, *a, **k):
            mutator(self.cfg)
            return self.cfg
        self.site = FakeSite({
            "viewforum.php?f=4": LIVE_CHANGELOG,
            "viewforum.php?f=2": LIVE_ANNOUNCEMENTS,
            "viewtopic.php?t=3087": _topic_with_replies(3087, "2026-10-24",
                                                        "Shared BG queues.", "Archer"),
            "viewtopic.php?t=2816": _topic_with_replies(2816, "2026-10-06",
                                                        "Cla'ckora can be summoned."),
            "viewtopic.php?t=2730": _topic_with_replies(2730, "2026-09-30",
                                                        "Inns rest only faction-aligned."),
            "viewtopic.php?t=3086": _topic_with_replies(3086, "Cross Realm Battlegrounds",
                                                        "Cross Realm BGs are live."),
        })
        self.patches = [
            mock.patch.object(app, "_news_open", self.site.opener),
            mock.patch.object(app, "load_config", lambda *a, **k: self.cfg),
            mock.patch.object(app, "update_config", update),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_changelog_is_forum_4_topics_with_their_opening_posts(self):
        items = self.app.fetch_patch_notes()
        self.assertEqual([i["title"] for i in items],
                         ["2026-10-24", "2026-10-06", "2026-09-30"])
        self.assertEqual(items[1]["body"], "Cla'ckora can be summoned.")
        self.assertEqual(items[1]["author"], "Kestrel")
        self.assertEqual(items[1]["url"], news.topic_url(2816))
        for item in items:
            self.assertNotIn("reply", item["body"])
            self.assertNotIn("Replier", item["author"] or "")
        self.assertEqual(self.site.asked[0], news.forum_url(4))
        # Each topic opened on its first page, default order: no sk=t&sd=d.
        self.assertEqual(self.site.asked[1:], [news.topic_url(t)
                                               for t in (3087, 2816, 2730)])

    def test_a_refresh_is_the_listing_alone(self):
        self.app.fetch_patch_notes()
        self.site.asked.clear()
        items = self.app.fetch_patch_notes()
        self.assertEqual(self.site.asked, [news.forum_url(4)])
        self.assertEqual(items[0]["body"], "Shared BG queues.")

    def test_changelog_links_survive_the_cache(self):
        self.site.pages["viewtopic.php?t=2816"] = topic_page(2816, "2026-10-06",
                                                             LINK_BODY)
        first = self.app.fetch_patch_notes()[1]
        self.site.asked.clear()
        again = self.app.fetch_patch_notes()[1]       # from the day's cache
        self.assertEqual(self.site.asked, [news.forum_url(4)])
        for item in (first, again):
            self.assertEqual([item["body"][s:e] for s, e, _u in item["links"]],
                             ["Click here to enter", "the rules"])

    def test_an_old_preview_is_read_again(self):
        self.app.fetch_patch_notes()
        self.cfg["news_first_posts"]["2816"]["timestamp"] -= self.app.NEWS_FIRST_POST_TTL
        self.site.asked.clear()
        self.app.fetch_patch_notes()
        self.assertEqual(self.site.asked, [news.forum_url(4), news.topic_url(2816)])

    def test_a_challenged_topic_stops_the_rest(self):
        self.site.pages["viewtopic.php?t=3087"] = (CHALLENGE, {"X-BF-Challenge": "pending"})
        items = self.app.fetch_patch_notes()
        self.assertEqual(self.site.asked, [news.forum_url(4), news.topic_url(3087)])
        self.assertEqual([i["title"] for i in items],
                         ["2026-10-24", "2026-10-06", "2026-09-30"])
        self.assertTrue(all(i["body"] == "" for i in items))   # titles only

    def test_a_challenged_listing_is_an_error(self):
        self.site.pages["viewforum.php?f=4"] = (CHALLENGE, {"X-BF-Challenge": "pending"})
        with self.assertRaises(self.app.ForumBlockedError):
            self.app.fetch_patch_notes()

    def test_announcement_is_forum_2s_newest_topic_opening_post(self):
        item = self.app.fetch_featured_post()
        self.assertEqual(item["title"], "Cross Realm Battlegrounds")
        self.assertEqual(item["body"], "Cross Realm BGs are live.")
        self.assertEqual(self.site.asked, [news.forum_url(2), news.topic_url(3086)])
        self.assertNotIn("_note", item)        # the forum, not the fallback

    def test_no_topic_is_read_newest_post_first(self):
        self.app.fetch_featured_post()
        self.app.fetch_patch_notes()
        self.assertFalse([u for u in self.site.asked if "sk=t&" in u or "p=" in u])
        self.assertFalse(hasattr(news, "fetch_topic_posts"))
        self.assertFalse(hasattr(news, "ANNOUNCEMENTS_TOPIC_ID"))


# ──────────────────────────────────────────────────────────────────────────────
#  Every announcement: forum pages, and the list under the title
# ──────────────────────────────────────────────────────────────────────────────

def forum_page(rows_html, starts=(), current=0):
    """A listing page with the forum's own page-number links."""
    pages = "".join(
        f'<li><a class="button" href="./viewforum.php?f=2&amp;{SID}&amp;sk=tt'
        f'&amp;sd=d&amp;start={n}">{n // 25 + 1}</a></li>'
        for n in starts)
    return (f'<html><body><div class="pagination"><ul>{pages}</ul></div>'
            f'<ul class="topiclist topics">{rows_html}</ul></body></html>')


def numbered_rows(first, count, day0=24):
    """``count`` ordinary topics, ids first, first-1, ..., one a day back
    from 2026-10-<day0>."""
    out = []
    for i in range(count):
        tid = first - i
        out.append(row("", tid, f"Topic {tid}", "Kestrel",
                       f"2026-10-{day0 - i:02d}T08:00:00+00:00",
                       "Replier", "2026-10-25T09:00:00+00:00"))
    return "".join(out)


PINNED_APRIL = row("announce", 3, "Beta Test Info", "Kestrel",
                   "2026-04-28T21:16:00+00:00", "X", "2026-10-23T08:00:00+00:00")


class TestForumPages(unittest.TestCase):

    def test_next_page_from_the_forums_own_links(self):
        html = forum_page(live_row(2816, "2026-10-06", "Kestrel",
                                   "Tue Oct 06, 2026 4:54 am", "C", 1, pages=4),
                          starts=(25, 50))
        # The topic row's own page links (viewtopic ...&start=30) do not count.
        self.assertEqual(news.next_page_start(html, 0), 25)
        self.assertEqual(news.next_page_start(html, 25), 50)
        self.assertIsNone(news.next_page_start(html, 50))
        self.assertIsNone(news.next_page_start(live_row(1, "x", "a", "b", "c", 2)))

    def test_a_later_page_is_one_request(self):
        site = FakeSite({"start=25": forum_page(numbered_rows(100, 3),
                                                starts=(0, 25))})
        rows, nxt = news.fetch_forum_page(2, 25, **kw(site))
        self.assertEqual(site.asked, [news.forum_url(2, start=25)])
        self.assertTrue(site.asked[0].endswith("&start=25"))
        self.assertIsNone(nxt)
        self.assertEqual([r.topic_id for r in rows], [100, 99, 98])

    def test_the_first_page_url_is_unchanged(self):
        self.assertEqual(news.forum_url(2), news.forum_url(2, start=0))
        self.assertNotIn("start=", news.forum_url(2))

    def test_an_empty_page_is_an_error(self):
        site = FakeSite({"start=25": "<html>maintenance</html>"})
        with self.assertRaises(news.ForumError) as cm:
            news.fetch_forum_page(2, 25, **kw(site))
        self.assertEqual(cm.exception.stage, "forum 2 listing from 25 parse")


# ──────────────────────────────────────────────────────────────────────────────
#  Links in a post
# ──────────────────────────────────────────────────────────────────────────────

LINK_BODY = ('The deadline is creeping   closer.<br><br>'
             'Deadline: Sunday, October 29th<br><br>'
             'Where: <a href="./viewtopic.php?t=2742&amp;sid=abc" class="postlink">'
             'Click here to enter</a><br>'
             'Rules: <a href="https://example.com/rules">the rules</a> and '
             '<a href="javascript:alert(1)">not a link</a>.'
             '<blockquote><div><cite>Q</cite><a href="https://quoted.example">'
             'quoted link</a></div></blockquote>')


class TestPostLinks(unittest.TestCase):

    def parsed(self, body=LINK_BODY):
        return news.parse_first_post_links(
            topic_page(3010, "Transmog", body), news.topic_url(3010))

    def test_links_keep_their_words_and_address(self):
        author, _created, content, links = self.parsed()
        self.assertEqual(author, "Kestrel")
        spans = [(content[s:e], url) for s, e, url in links]
        self.assertEqual(spans, [
            ("Click here to enter",
             "https://octowow.st/forum/viewtopic.php?t=2742&sid=abc"),
            ("the rules", "https://example.com/rules"),
        ])
        # The text itself is as before: tidied, no marks left in it.
        self.assertIn("The deadline is creeping closer.", content)
        self.assertIn("Where: Click here to enter", content)
        self.assertIn("not a link", content)               # words kept
        self.assertNotIn("quoted link", content)            # quotes dropped
        self.assertFalse(any(c in content for c in "\x01\x02\x03"))
        self.assertEqual(news.parse_first_post(
            topic_page(3010, "Transmog", LINK_BODY))[2], content)

    def test_an_unclosed_link_is_text(self):
        _a, _c, content, links = self.parsed('Go <a href="https://x.example">here')
        self.assertEqual(content, "Go here")
        self.assertEqual(links, ())

    def test_fetched_posts_carry_their_links(self):
        site = FakeSite({"t=3010": topic_page(3010, "Transmog", LINK_BODY)})
        (row3010,) = [r for r in news.parse_forum_topics(
            "<ul>" + row("", 3010, "Transmog", "Kestrel",
                         "2026-10-19T08:00:00+00:00", "X",
                         "2026-10-20T08:00:00+00:00") + "</ul>")]
        item = news.fetch_first_post(row3010, **kw(site)).to_item()
        self.assertEqual([item["body"][s:e] for s, e, _u in item["links"]],
                         ["Click here to enter", "the rules"])


@unittest.skipUnless(HAVE_TK, "no display")
class TestNewPosts(unittest.TestCase):
    """NEW! is "since last read" OR "within three days"."""

    def setUp(self):
        from datetime import datetime, timezone
        from equpdater import app
        self.app = app
        self.day = lambda d: datetime(2026, 10, d, 12,
                                      tzinfo=timezone.utc).timestamp()
        self.iso = lambda d: f"2026-10-{d:02d}T08:00:00+00:00"

    def test_either_rule_is_enough(self):
        new = self.app.news_is_new
        now = self.day(30)
        self.assertTrue(new(self.iso(28), self.day(29), now))    # 3 days
        self.assertFalse(new(self.iso(26), self.day(29), now))   # neither
        # Four days old, but the launcher was last opened before it.
        self.assertTrue(new(self.iso(26), self.day(20), now))
        # Never opened before: only the three days count.
        self.assertTrue(new(self.iso(28), None, now))
        self.assertFalse(new(self.iso(20), None, now))
        self.assertFalse(new("", self.day(1), now))
        self.assertTrue(new("2026-10-28T08:00:00", None, now))   # no offset

    def test_the_line_goes_under_the_oldest_of_several(self):
        items = [{"id": str(d), "date": self.iso(d)} for d in (29, 24, 23, 12)]
        marks = self.app.news_marks(items, self.day(20), self.day(30))
        self.assertEqual(marks, {"29": (True, False), "24": (True, False),
                                 "23": (True, True)})
        one = self.app.news_marks(items, self.day(25), self.day(30))
        self.assertEqual(one, {"29": (True, False)})


class TestAnnouncementsList(unittest.TestCase):
    """The Announcements panel is every announcement, newest first, ten at
    a time, the newest open; each title opens and closes its post. The
    Changelog's entries open and close too, the newest open."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        from unittest import mock
        cls.tmp = tempfile.mkdtemp(prefix="equ-ann-")
        os.environ["LOCALAPPDATA"] = cls.tmp
        os.environ["XDG_DATA_HOME"] = cls.tmp
        os.environ["EQUPDATER_NO_LANGUAGE_PROMPT"] = "1"
        for mod in [m for m in list(sys.modules) if m.startswith("equpdater")]:
            del sys.modules[mod]
        from equpdater import app
        cls.m = app
        # No launch-time read of the real forum: it would land mid-test.
        cls.no_launch_news = mock.patch.object(app.EqUpdaterApp, "_load_news",
                                               lambda self: None)
        cls.no_launch_news.start()
        cls.app = app.EqUpdaterApp()
        for _ in range(8):
            cls.app.update()

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.app.destroy()
        cls.no_launch_news.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    NEWEST = {"id": "100", "title": "Topic 100", "author": "Kestrel",
              "date": "2026-10-24T08:00:00+00:00", "body": "Newest.",
              "html": "Newest.", "url": news.topic_url(100)}

    def setUp(self):
        from unittest import mock
        real = self.m.threading.Thread

        def thread(target=None, args=(), **k):
            if "._ann_" in getattr(target, "__qualname__", ""):
                return type("T", (), {"start": lambda self: target(*args)})()
            return real(target=target, args=args, **k)
        # Page one: 12 ordinary topics and an April pin; page two: 12 more,
        # older, and the same pin again (phpBB repeats it on every page).
        self.site = FakeSite({
            "start=25": forum_page(PINNED_APRIL + numbered_rows(88, 12, day0=12),
                                   starts=(0, 25)),
            "viewforum.php?f=2": forum_page(PINNED_APRIL + numbered_rows(100, 12),
                                            starts=(25,)),
            "viewtopic.php?t=99": topic_page(99, "Topic 99", "Opening post 99."),
        })
        self.patches = [mock.patch.object(self.m.threading, "Thread", thread),
                        mock.patch.object(self.m, "_news_open", self.site.opener)]
        for p in self.patches:
            p.start()
        self.app._ann_reset()
        self.app._render_featured(dict(self.NEWEST))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.app._ann_reset()

    def entries(self, key="ann"):
        """(title, open?, text shown) per entry, and the buttons under them."""
        self.app.update()
        canvas = self.app._acc_canvas[key]
        inner = canvas.nametowidget(canvas.itemcget(canvas.find_all()[0], "window"))
        rows, buttons = [], []
        for entry in inner.winfo_children():
            if isinstance(entry, self.m.tk.Label):
                buttons.append(entry.cget("text"))
                continue
            kids = entry.winfo_children()
            if not kids or not isinstance(kids[0], self.m.tk.Frame):
                continue
            head = kids[0].winfo_children()
            shown = [w for k in kids[1:]
                     for w in ([k] + list(k.winfo_children()))]
            text = [k.get("1.0", "end-1c") if isinstance(k, self.m.tk.Text)
                    else k.cget("text") for k in shown
                    if isinstance(k, (self.m.tk.Label, self.m.tk.Text))]
            # The title sits in a row of its own, with the NEW! after it.
            title = head[0].winfo_children()[0]
            rows.append((title.cget("text"), head[1].cget("text").endswith("▴"),
                         text[0] if text else ""))
        return rows, buttons

    def load(self):
        self.app._ann_load_page()
        self.app.update()

    def test_before_the_list_is_read_the_newest_is_shown_open(self):
        rows, _ = self.entries()
        self.assertEqual(rows, [("Topic 100", True, "Newest.")])

    def test_the_list_fills_the_panel_newest_open_the_rest_closed(self):
        self.load()
        rows, buttons = self.entries()
        self.assertEqual([r[0] for r in rows],
                         [f"Topic {t}" for t in range(100, 90, -1)])
        self.assertEqual(rows[0], ("Topic 100", True, "Newest."))
        self.assertFalse(any(r[1] for r in rows[1:]))
        self.assertEqual(buttons, ["Load more"])
        self.assertEqual(self.site.asked, [news.forum_url(2)])
        # The list is the whole panel under its header.
        canvas = self.app._acc_canvas["ann"]
        self.assertGreater(canvas.winfo_height(),
                           self.app._feat_frame.winfo_height() // 2)

    def test_a_title_opens_its_post_once_and_closes_it(self):
        self.load()
        self.app._ann_toggle("99")
        rows, _ = self.entries()
        self.assertEqual(rows[1], ("Topic 99", True, "Opening post 99."))
        self.app._ann_toggle("99")
        self.assertEqual(self.entries()[0][1], ("Topic 99", False, ""))
        asked = len(self.site.asked)
        self.app._ann_toggle("99")                     # read once
        self.assertEqual(len(self.site.asked), asked)
        self.app._ann_toggle("100")                    # the newest closes too
        self.assertFalse(self.entries()[0][0][1])

    def test_clicking_the_title_toggles(self):
        self.load()
        self.app.update()
        canvas = self.app._acc_canvas["ann"]
        inner = canvas.nametowidget(canvas.itemcget(canvas.find_all()[0], "window"))
        title = inner.winfo_children()[0].winfo_children()[0].winfo_children()[0]
        title.event_generate("<Button-1>")
        self.assertFalse(self.entries()[0][0][1])     # the newest closed

    def test_load_more_reads_the_next_page_and_holds_the_old_pin_back(self):
        self.load()
        self.app._ann_more()                 # 20: page one has only 12
        self.app.update()
        rows, _ = self.entries()
        self.assertEqual(self.site.asked, [news.forum_url(2),
                                           news.forum_url(2, start=25)])
        self.assertEqual([r[0] for r in rows],
                         [f"Topic {t}" for t in range(100, 80, -1)])
        self.app._ann_more()                 # the last page is read: all of it
        self.app.update()
        rows, buttons = self.entries()
        self.assertEqual(rows[-1][0], "Beta Test Info")
        self.assertEqual(len(rows), 25)
        self.assertEqual([r[0] for r in rows].count("Beta Test Info"), 1)
        self.assertEqual(buttons, [])

    def test_opening_and_closing_keep_the_scroll_position(self):
        """Every click redraws the list; the view must not jump to the top."""
        self.load()
        self.app._ann_more()
        self.app.update()
        canvas = self.app._acc_canvas["ann"]
        canvas.yview_moveto(0.5)
        self.app.update()
        top = self.app._acc_canvas["ann"].canvasy(0)
        self.assertGreater(top, 0)
        for iid in ("99", "99", "100"):           # open, close, close the newest
            self.app._ann_toggle(iid)
            self.app.update()
            self.assertEqual(self.app._acc_canvas["ann"].canvasy(0), top, iid)
        self.app._ann_more()                       # Load more too
        self.app.update()
        self.assertEqual(self.app._acc_canvas["ann"].canvasy(0), top)

    def test_links_in_an_open_post_are_clickable(self):
        self.site.pages["viewtopic.php?t=99"] = topic_page(99, "Topic 99", LINK_BODY)
        opened = []
        self.app._open_url, real = opened.append, self.app._open_url
        try:
            self.load()
            self.app._ann_toggle("99")
            self.app.update()
            canvas = self.app._acc_canvas["ann"]
            inner = canvas.nametowidget(canvas.itemcget(canvas.find_all()[0], "window"))
            entry = next(e for e in inner.winfo_children()
                         if e.winfo_children()
                         and isinstance(e.winfo_children()[0], self.m.tk.Frame)
                         and e.winfo_children()[0].winfo_children()[0]
                         .winfo_children()[0].cget("text") == "Topic 99")
            body = entry.winfo_children()[1]
            txt = next(w for w in body.winfo_children()
                       if isinstance(w, self.m.tk.Text))
            ranges = txt.tag_ranges("link0")
            self.assertEqual(txt.get(ranges[0], ranges[1]), "Click here to enter")
            # A click on the words opens the address.
            self.app.update()
            x, y, w, h = txt.bbox(f"{ranges[0]}+2c")
            txt.event_generate("<Motion>", x=x + 1, y=y + h // 2)   # Tk's "current"
            txt.event_generate("<Button-1>", x=x + 1, y=y + h // 2)
            self.app.update()
            self.assertEqual(opened,
                             ["https://octowow.st/forum/viewtopic.php?t=2742&sid=abc"])
            self.assertEqual(str(txt.cget("state")), "disabled")
            self.assertGreater(int(txt.cget("height")), 1)       # all of it shown
            labels = [w.cget("text") for w in body.winfo_children()
                      if isinstance(w, self.m.tk.Label)]
            self.assertIn("Open in Browser  ↗", labels)
            # What the link opens.
            self.app._news_text(entry, "Go here", [[3, 7, "https://x.example/a"]])
            self.app._news_text(entry, "Bad", [[0, 3, "javascript:alert(1)"]])
            last = [w for w in entry.winfo_children()
                    if isinstance(w, self.m.tk.Text)][-1]   # added to the entry
            self.assertEqual(last.tag_names(), ("sel",))           # not a link
        finally:
            self.app._open_url = real

    def test_a_click_changes_that_entry_only(self):
        """No rebuild on a click: the list, and every other entry's widgets,
        stay the very same ones -- through open, close, a post arriving and
        Load more. (A rebuild is what flickered.)"""
        self.load()
        canvas = self.app._acc_canvas["ann"]
        rows = self.app._acc_lists["ann"]["rows"]
        others = {iid: row["entry"] for iid, row in rows.items() if iid != "99"}
        self.app._ann_toggle("99")             # open: its post is read
        self.app.update()
        self.app._ann_toggle("99")             # close
        self.app._ann_more()                   # ten more, page two read
        self.app.update()
        self.assertIs(self.app._acc_canvas["ann"], canvas)
        for iid, entry in others.items():
            self.assertIs(self.app._acc_lists["ann"]["rows"][iid]["entry"], entry)
        self.assertEqual(len(self.app._acc_lists["ann"]["order"]), 20)
        rows, buttons = self.entries()
        self.assertEqual([r[0] for r in rows],
                         [f"Topic {t}" for t in range(100, 80, -1)])
        self.assertEqual(buttons, ["Load more"])           # footer made again

    def test_the_changelog_click_is_in_place_too(self):
        items = [{"id": str(t), "title": f"T{t}", "author": "A",
                  "date": "2026-10-24T08:00:00+00:00", "body": f"N{t}",
                  "html": "", "url": ""} for t in (3, 2, 1)]
        self.app._patch_open = None
        self.app._render_patch_notes(items)
        canvas = self.app._acc_canvas["patch"]
        self.app._patch_toggle("2")
        self.assertIs(self.app._acc_canvas["patch"], canvas)
        self.assertEqual([r[1] for r in self.entries("patch")[0]],
                         [True, True, False])

    def test_refresh_starts_over(self):
        self.load()
        self.app._ann_toggle("99")
        self.app._ann_reset()
        st = self.app._ann()
        self.assertEqual(st["rows"], {})
        self.assertIsNone(st["open"])

    def test_new_posts_say_so_with_a_line_under_the_oldest(self):
        """Posts since the Changelog was last read are NEW!; the oldest of
        them has the red line under it; the launch's last-read time holds
        all session (a read moves it on for the next launch only)."""
        from datetime import datetime, timezone
        from unittest import mock
        ts = lambda d: datetime(2026, 10, d, tzinfo=timezone.utc).timestamp()
        items = [{"id": str(t), "title": f"2026-10-{d:02d}", "author": "Archer",
                  "date": f"2026-10-{d:02d}T08:00:00+00:00",
                  "body": "", "html": "", "url": news.topic_url(t)}
                 for t, d in ((3100, 28), (3087, 24), (3074, 23), (2902, 12))]
        self.app._news_seen_before["patch"] = ts(20)
        try:
            with mock.patch.object(self.m.time, "time", lambda: ts(30)):
                self.app._patch_open = None
                self.app._render_patch_notes(items)
                self.app.update()
                rows = self.app._acc_lists["patch"]["rows"]
                self.assertEqual([rows[i]["badge"].winfo_ismapped()
                                  for i in ("3100", "3087", "3074", "2902")],
                                 [1, 1, 1, 0])
                self.assertEqual([rows[i]["divider"].cget("bg")
                                  for i in ("3100", "3074", "2902")],
                                 [self.m.C_DIVIDER, self.m.C_NEW,
                                  self.m.C_DIVIDER])
                # Read on the 25th: only the 28th is new -- alone, no line.
                self.app._news_seen_before["patch"] = ts(25)
                self.app._patch_toggle("3087")
                self.app.update()
                self.assertEqual([rows[i]["badge"].winfo_ismapped()
                                  for i in ("3100", "3087", "3074")], [1, 0, 0])
                self.assertEqual(rows["3074"]["divider"].cget("bg"),
                                 self.m.C_DIVIDER)
        finally:
            self.app._news_seen_before["patch"] = None
            self.app._patch_open = None

    def test_the_changelog_opens_and_closes(self):
        items = [{"id": str(t), "title": f"2026-10-{d:02d}", "author": "Archer",
                  "date": f"2026-10-{d:02d}T08:00:00+00:00",
                  "body": f"Notes {t}.", "html": "", "url": news.topic_url(t)}
                 for t, d in ((3087, 24), (3074, 23), (2902, 12))]
        self.app._patch_open = None
        self.app._render_patch_notes(items)
        rows, _ = self.entries("patch")
        self.assertEqual(rows, [("2026-10-24", True, "Notes 3087."),
                                ("2026-10-23", False, ""),
                                ("2026-10-12", False, "")])
        self.app._patch_toggle("3087")
        self.app._patch_toggle("2902")
        rows, _ = self.entries("patch")
        self.assertEqual([r[1] for r in rows], [False, False, True])
        self.assertEqual(rows[2][2], "Notes 2902.")
        self.app._render_patch_notes(items)            # a refresh keeps it
        self.assertEqual([r[1] for r in self.entries("patch")[0]],
                         [False, False, True])
