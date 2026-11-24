"""Fixtures for tests and benchmarks: news without a network.

A fixture is JSON::

    {"latency_ms": 0,
     "sections": {"announcements": [{"id": "9", "title": ..., "author": ...,
                                     "date": ISO, "url": ..., "kind": "normal",
                                     "body": "...", "links": [[s, e, url]]}],
                  "changelog": [...]}}

``FixtureFetcher`` answers the same calls as ``core.news_service.Fetcher`` and
counts them, so a test can assert "scrolling made no request".
"""

from __future__ import annotations

import json
import os
import time

from .. import news


class FixtureFetcher:
    def __init__(self, path_or_data):
        data = path_or_data
        if isinstance(path_or_data, str):
            with open(path_or_data, encoding="utf-8") as f:
                data = json.load(f)
        self.latency = data.get("latency_ms", 0) / 1000.0
        self.sections = data["sections"]
        self.requests = 0
        self.log = []
        self.page = int(data.get("page_size", 10))

    def _forum_key(self, forum):
        return "announcements" if forum == news.ANNOUNCEMENTS_FORUM_ID else "changelog"

    def listing(self, forum, start=0):
        self.requests += 1
        self.log.append(("list", forum, start))
        time.sleep(self.latency)
        rows = self.sections[self._forum_key(forum)]
        chunk = rows[start:start + self.page]
        nxt = start + self.page if start + self.page < len(rows) else None
        return [{k: r.get(k, "") for k in ("id", "title", "author", "date", "url")}
                | {"kind": r.get("kind", "normal"), "body": "", "html": ""}
                for r in chunk], nxt

    def first_post(self, entry):
        self.requests += 1
        self.log.append(("post", entry["id"]))
        time.sleep(self.latency)
        for rows in self.sections.values():
            for r in rows:
                if str(r["id"]) == str(entry["id"]):
                    return {"body": r.get("body", ""), "links": r.get("links", []),
                            "author": r.get("author"), "date": r.get("date")}
        raise OSError("no such topic")

    def feed(self):
        self.requests += 1
        return []


# ── a whole fake forum ───────────────────────────────────────────────────────
# Lives in tools/bench/fakesite.py (stdlib only, so the 2.x app can be run
# against the very same forum); loaded from there when a run asks for it.

def FakeSiteOpener(path):                                      # noqa: N802
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location(
        "fakesite", os.path.join(here, "tools", "bench", "fakesite.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.FakeSiteOpener(path)
