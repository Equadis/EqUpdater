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


def fake_addon_status(n: int, seed: int = 3) -> dict:
    """``n`` synthetic addons in the shape ``_addons_verify`` produces, with a
    realistic mix of states (for the page benchmark and tests; no disk, no
    network). Half are installed, the rest are in the catalogue."""
    import random
    rng = random.Random(seed)
    colors = ["|cff33ff99", "|cffffd100", "|cff00ccff", ""]
    statuses = (["upToDate"] * 14 + ["updateAvailable"] * 3 + ["unmanaged"] * 2
                + ["modified", "localNewer", "unverifiable", "differentSource"])
    addons, available = {}, []
    words = "Bag Chat Cast Bar Frame Map Quest Raid Unit Aura Tooltip Mail Gear Loot Auction".split()
    for i in range(n):
        folder = "%s%s%d" % (rng.choice(words), rng.choice(words), i)
        desc = " ".join(rng.choice(words).lower() for _ in range(rng.randint(6, 30)))
        sha = "%040x" % rng.getrandbits(160)
        git = f"https://github.com/someone/{folder}.git"
        if i % 2 == 0:
            st = rng.choice(statuses)
            rec = {"folder": folder, "status": st, "git": git, "suggested_git": git,
                   "description": desc, "installed_sha": sha, "remote_sha": sha[::-1],
                   "toc": {"Title": rng.choice(colors) + folder + ("|r" if i % 4 == 0 else ""),
                           "Notes": desc, "Interface": "11200"}}
            if st == "unmanaged":
                rec["git"] = None
            if i % 17 == 0:
                rec["check_failed"] = "the source did not answer"
            addons[folder] = rec
        else:
            available.append({"folder": folder, "description": desc, "git": git,
                              "toc": {"Title": folder, "Notes": desc}})
    return {"state": "done", "addons": addons, "available": available}


def server_fixture_fetch(path):
    """A ``ServerFeed`` fetch that serves ``{"live": ..., "status": ..., "fail": bool}``
    from a JSON file (QA and tests): no network, honours If-None-Match."""
    from ..core import server as srv

    def fetch(url, etag="", timeout=8.0):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("fail"):
            raise OSError("fixture says the network is down")
        obj = {srv.LIVE_URL: data.get("live"), srv.STATUS_URL: data.get("status")}.get(url)
        if obj is None:
            return srv.Response(404)
        body = json.dumps(obj).encode()
        tag = '"%d"' % hash(body)
        if etag == tag:
            return srv.Response(304, b"", tag)
        return srv.Response(200, body, tag)
    return fetch


class FixturePresenceProvider:
    """QA only (``EQU3_PRESENCE_FIXTURE=<file>``): serves friends' presence from a JSON file so the
    sidebar can be seen with data. **Not a real service and never the default.** The file is
    ``{"<forum user name, lower case>": {"game": ..., "launcher": ..., "realm": ...}}``. Nothing else about a
    player exists in the data model (no character, class, level, guild or zone)."""
    from ..core import presence as _p

    id = "fixture"
    name = "fixture"
    available = True
    requires_account = False
    capabilities = frozenset({"game_presence", "launcher_presence", "publish"})
    max_batch = 50
    unavailable_reason = ""

    def __init__(self, path):
        self.path = path

    def lookup(self, friends, token):
        import time as _t
        from ..core.presence import Observation
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("__fail__"):
            raise OSError("fixture says the service is down")
        out = []
        for fr in friends:
            d = data.get(fr.label.lower())
            if d:
                out.append(Observation(fr.id, d.get("game", "unknown"), d.get("launcher", "unknown"),
                                       d.get("as_of") or _t.time(), d.get("realm", ""), "fixture"))
        return out

    published: list = []

    def publish(self, status, token):
        type(self).published.append(status)


def auth_config_from_env(path):
    """QA only (``EQU3_AUTH_CONFIG=<file>``): a sign-in configuration to try against a fake server."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
