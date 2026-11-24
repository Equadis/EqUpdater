"""A fake octowow.st forum, stdlib only, shared by the 3.0 tests and the
benchmark wrappers so 2.x and 3.0 parse the same bytes.

``FakeSiteOpener(fixture)`` is called like ``secure_urlopen(req, timeout)`` and
returns phpBB-shaped listing and topic pages built from a fixture (see
make_news_fixture.py). It counts every request.
"""

import html as _html
import json
import time


def _esc(text):
    return _html.escape(text, quote=False).replace(chr(10), "<br>")


def body_to_html(body, links):
    spans = sorted((int(a), int(b), str(u)) for a, b, u in (links or [])
                   if 0 <= int(a) < int(b) <= len(body))
    out, pos = [], 0
    for a, b, u in spans:
        if a < pos:
            continue
        out.append(_esc(body[pos:a]))
        out.append('<a href="%s">%s</a>' % (_html.escape(u, quote=True), _esc(body[a:b])))
        pos = b
    out.append(_esc(body[pos:]))
    return "".join(out)


class _Headers(dict):
    def get_content_charset(self):
        return "utf-8"


class _Resp:
    def __init__(self, body: str):
        self._b = body.encode("utf-8")
        self.headers = _Headers()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_ROW = '''<li class="row bg1 {kind}"><dl class="row-item"><dt><div class="list-inner">
<a href="./viewtopic.php?t={tid}&amp;sid=0" class="topictitle">{title}</a><br />
<div class="topic-poster responsive-hide left-box">by <a href="./memberlist.php?u=2"
 class="username-coloured">{author}</a> &raquo; <time datetime="{created}">{created}</time>
</div></div></dt><dd class="posts">4</dd></dl></li>'''

_TOPIC = '''<!DOCTYPE html><html><body id="phpbb">
<div id="p{tid}" class="post has-profile bg2"><div class="inner"><div class="postbody">
<div id="post_content{tid}"><h3 class="first"><a href="#p{tid}">{title}</a></h3>
<p class="author"><span class="responsive-hide">by <strong><a href="./memberlist.php?u=2"
 class="username-coloured">{author}</a></strong> &raquo; </span>
<time datetime="{created}">{created}</time></p>
<div class="content">{body}</div></div></div></div></div></body></html>'''


class FakeSiteOpener:
    """Stands in for ``secure_urlopen`` for octowow.st: serves a phpBB-shaped
    forum built from a fixture. Both the 2.x and the 3.0 parsers read it, so
    the two apps do the same parsing work on the same bytes. ``requests`` and
    ``log`` record every call (the network-request counts of the benchmark)."""

    def __init__(self, path_or_data, latency_ms=None):
        self._html = body_to_html
        data = path_or_data
        if isinstance(path_or_data, str):
            with open(path_or_data, encoding="utf-8") as f:
                data = json.load(f)
        self.sections = data["sections"]
        self.page = int(data.get("page_size", 10))
        self.latency = (data.get("latency_ms", 0) if latency_ms is None
                        else latency_ms) / 1000.0
        self.requests, self.log = 0, []
        self._by_id = {str(r["id"]): r for rows in self.sections.values()
                       for r in rows}

    def __call__(self, req, timeout=None):
        import re
        url = req.full_url if hasattr(req, "full_url") else req
        self.requests += 1
        self.log.append(url)
        if self.latency:
            time.sleep(self.latency)
        if "news.json" in url:
            return _Resp("[]")
        m = re.search(r"viewtopic\.php\?t=(\d+)", url)
        if m:
            r = self._by_id.get(m.group(1))
            if r is None:
                raise OSError("404 " + url)
            return _Resp(_TOPIC.format(
                tid=r["id"], title=r["title"], author=r.get("author") or "Kestrel",
                created=r["date"], body=self._html(r.get("body", ""), r.get("links"))))
        m = re.search(r"viewforum\.php\?f=(\d+)(?:.*?start=(\d+))?", url)
        if m:
            key = "announcements" if m.group(1) == "2" else "changelog"
            start = int(m.group(2) or 0)
            rows = self.sections[key][start:start + self.page]
            nxt = start + self.page
            pages = (f'<li><a class="button" href="./viewforum.php?f={m.group(1)}'
                     f'&amp;start={nxt}">next</a></li>'
                     if nxt < len(self.sections[key]) else "")
            body = "".join(_ROW.format(kind="", tid=r["id"], title=r["title"],
                                       author=r.get("author") or "Kestrel",
                                       created=r["date"]) for r in rows)
            return _Resp(f'<html><body><div class="pagination"><ul>{pages}</ul></div>'
                         f'<ul class="topiclist topics">{body}</ul></body></html>')
        raise OSError("404 " + url)
