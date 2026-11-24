"""One news service for Announcements and Changelog.

Qt-free. All state lives on the caller's (UI) thread; only the *fetch*
callables run on workers, return plain data, and are marshalled back by the
``TaskManager``. So there is no locking here and no way for a late response to
touch a list that has since been refreshed (each refresh bumps ``epoch``).

What it guarantees (the acceptance list for the News page):

* cached listing **and** post bodies are shown immediately, before any request;
* the listing refreshes in the background and entries are updated *in place*
  (``entries_changed``) or inserted (``list_changed`` with the index) - never
  a rebuild of the whole list;
* the same topic is never fetched twice at once (task keys), nor the same
  listing page twice during start-up;
* opening a post whose body is cached costs no request;
* nothing here is reachable from scrolling: only ``refresh``, ``load_more`` and
  ``toggle`` can cause a request;
* a failed refresh keeps the cached content and records ``error`` for the page
  to show next to it.

Parsing is ``equpdater.news`` unchanged.
"""

from __future__ import annotations

import html as _html
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..i18n import N_, tr

# Forum ids (news.ANNOUNCEMENTS_FORUM_ID / CHANGELOG_FORUM_ID, asserted equal in
# tests): literals here so importing this module does not import urllib.
ANNOUNCEMENTS_FORUM, CHANGELOG_FORUM = 2, 4

#: A News post this recent is NEW! whenever the launcher was last opened.
NEWS_NEW_DAYS = 3
#: A cached post body older than this is read again (quietly, in place).
BODY_TTL = 24 * 3600
#: Entries and bodies kept per section on disk.
KEEP = 60
#: Lines a changelog entry shows before "Read more".
PREVIEW_LINES = 14

_MONTHS = (N_("Jan"), N_("Feb"), N_("Mar"), N_("Apr"), N_("May"), N_("Jun"),
           N_("Jul"), N_("Aug"), N_("Sep"), N_("Oct"), N_("Nov"), N_("Dec"))


def format_date(iso: str) -> str:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return iso or ""
    return f"{d.day:02d} {tr(_MONTHS[d.month - 1])} {d.year}"


def timestamp(iso: str) -> float | None:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def is_new(iso: str, last_seen: float | None, now: float) -> bool:
    """Posted after the player last saw the list, or within NEWS_NEW_DAYS."""
    ts = timestamp(iso)
    if ts is None:
        return False
    return ((last_seen is not None and ts > last_seen)
            or ts >= now - NEWS_NEW_DAYS * 86400)


def safe_url(url: str) -> bool:
    return urlsplit(url or "").scheme in ("http", "https")


def body_to_html(body: str, links) -> str:
    """The post's text as a small HTML fragment: escaped, newlines as <br>,
    each (start, end, url) link as <a href>. Anything malformed or not
    http(s) is dropped, never rendered."""
    spans = []
    for link in links or []:
        try:
            start, end, url = int(link[0]), int(link[1]), str(link[2])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= start < end <= len(body) and safe_url(url):
            spans.append((start, end, url))
    spans.sort()
    out, pos = [], 0
    for start, end, url in spans:
        if start < pos:                      # overlapping: skip the later one
            continue
        out.append(_esc(body[pos:start]))
        out.append('<a href="%s">%s</a>' % (_html.escape(url, quote=True),
                                            _esc(body[start:end])))
        pos = end
    out.append(_esc(body[pos:]))
    return "".join(out)


def _esc(text: str) -> str:
    return _html.escape(text, quote=False).replace("\n", "<br>")


class Fetcher:
    """The network side, over ``equpdater.news``. Replaceable in tests."""

    def __init__(self, opener=None, user_agent: str | None = None,
                 timeout: int = 8):
        self._custom_opener = opener
        self._ua = user_agent
        self._timeout = timeout
        self.requests = 0

    # urllib, the forum parser and the TLS context are imported on the first
    # request (a worker thread), not at start-up.
    def _opener(self, req, timeout):
        if self._custom_opener is not None:
            return self._custom_opener(req, timeout)
        from .net import secure_urlopen
        return secure_urlopen(req, timeout=timeout, allowed_hosts={"octowow.st"})

    @property
    def _agent(self):
        if self._ua is None:
            from .. import branding
            self._ua = branding.user_agent()
        return self._ua

    def listing(self, forum_id: int, start: int = 0):
        self.requests += 1
        from .. import news
        rows, nxt = news.fetch_forum_page(forum_id, start, opener=self._opener,
                                          user_agent=self._agent,
                                          timeout=self._timeout)
        return [dict(r.to_item(), kind=r.kind) for r in rows], nxt

    def first_post(self, entry: dict):
        self.requests += 1
        from .. import news
        try:
            created = datetime.fromisoformat(entry["date"]) if entry.get("date") \
                else None
        except ValueError:
            created = None
        row = news.TopicRow(int(entry["id"]), entry.get("title", ""),
                            entry.get("author"), created, entry.get("url", ""),
                            entry.get("kind", "normal"))
        post = news.fetch_first_post(row, opener=self._opener,
                                     user_agent=self._agent, timeout=self._timeout)
        return {"body": post.content, "links": [list(l) for l in post.links],
                "author": post.author, "date": post.created_at}

    def feed(self):
        self.requests += 1
        from .. import news
        return news.fetch_news_feed(opener=self._opener, user_agent=self._agent,
                                    timeout=self._timeout)


SECTIONS = {
    "announcements": {"forum": ANNOUNCEMENTS_FORUM, "page": 10,
                      "title": N_("Announcements"), "prefetch": 1,
                      "fallback_feed": True},
    "changelog":     {"forum": CHANGELOG_FORUM, "page": 8,
                      "title": N_("Changelog"), "prefetch": 8,
                      "fallback_feed": False},
}


class Section:
    def __init__(self, key: str, spec: dict):
        self.key, self.spec = key, spec
        self.rows: dict = {}          # id -> entry dict (listing data)
        self.bodies: dict = {}        # id -> {body, links, ts, error?}
        self.order: list = []         # visible ids, newest first
        self.next_start = 0           # next listing offset, None = last page
        self.loaded = False           # at least one listing page is in
        self.loading = False          # a listing request is in flight
        self.error = ""
        self.note = ""
        self.shown = spec["page"]
        self.open: set = set()
        self.full: set = set()
        self.epoch = 0
        self.last_updated = 0.0
        self.last_seen: float | None = None
        self.default_open_done = False


class NewsService:
    """``listener`` receives:
        list_changed(key, first_changed_index)   rows inserted / removed
        entries_changed(key, ids)                these entries' data changed
        state_changed(key)                       loading / error / more
    """

    def __init__(self, store, tasks, fetcher, listener, now=time.time,
                 seen_store=None):
        self.store, self.tasks, self.fetch = store, tasks, fetcher
        self.listener, self.now = listener, now
        self.seen_store = seen_store          # settings: news_seen {section: ts}
        self.sections = {k: Section(k, v) for k, v in SECTIONS.items()}
        for sec in self.sections.values():
            seen = (self.seen_store.get("news_seen") or {}) if self.seen_store \
                else {}
            sec.last_seen = seen.get(sec.key)
            self._load_cache(sec)

    # -- cache ----------------------------------------------------------
    def _load_cache(self, sec: Section):
        data = (self.store.get("sections") or {}).get(sec.key) or {}
        for e in data.get("rows", []):
            if isinstance(e, dict) and "id" in e:
                sec.rows[str(e["id"])] = e
        for k, b in (data.get("bodies") or {}).items():
            if isinstance(b, dict) and b.get("body") is not None:
                sec.bodies[str(k)] = b
        sec.next_start = data.get("next_start", 0)
        sec.loaded = bool(sec.rows)
        sec.last_updated = float(data.get("last_updated") or 0)
        sec.order = self._visible(sec)
        self._default_open(sec)

    def _save_cache(self, sec: Section):
        keep = sec.order[:KEEP]

        def put(sections):
            sections[sec.key] = {
                "rows": [sec.rows[i] for i in keep if i in sec.rows],
                "bodies": {i: sec.bodies[i] for i in keep
                           if i in sec.bodies and not sec.bodies[i].get("error")},
                "next_start": sec.next_start,
                "last_updated": sec.last_updated}
        self.store.update("sections", put)

    # -- ordering -------------------------------------------------------
    @staticmethod
    def _sort_key(e):
        ts = timestamp(e.get("date"))
        try:
            n = int(e["id"])
        except (TypeError, ValueError):
            n = 0
        return (ts is not None, ts or 0, n)

    def _visible(self, sec: Section) -> list:
        """Newest-started first. A pinned topic sits on every page whatever
        its age, so while more pages exist one older than the oldest ordinary
        topic read is held back (as 2.x did)."""
        rows = sorted(sec.rows.values(), key=self._sort_key, reverse=True)
        if sec.next_start is not None:
            dated = [timestamp(r["date"]) for r in rows
                     if r.get("kind", "normal") == "normal" and r.get("date")]
            dated = [d for d in dated if d is not None]
            if dated:
                floor = min(dated)
                rows = [r for r in rows if r.get("kind", "normal") == "normal"
                        or ((timestamp(r.get("date")) or 0) >= floor)]
        return [str(r["id"]) for r in rows]

    def _default_open(self, sec: Section):
        """The latest post is expanded by default - once, not after every
        refresh (a refresh must not re-open what the player closed)."""
        if not sec.default_open_done and sec.order:
            sec.open.add(sec.order[0])
            sec.default_open_done = True

    # -- views for the model -------------------------------------------
    def visible_ids(self, key) -> list:
        sec = self.sections[key]
        return sec.order[:sec.shown]

    def entry(self, key, iid) -> dict:
        """Everything a delegate needs for one entry, from memory only."""
        sec = self.sections[key]
        row = sec.rows.get(iid, {})
        body = sec.bodies.get(iid)
        now = self.now()
        text = body.get("body") if body else None
        return {
            "id": iid, "title": row.get("title", ""),
            "author": row.get("author") or "",
            "date": row.get("date", ""),
            "dateText": format_date(row.get("date", "")),
            "url": row.get("url", ""),
            "isNew": is_new(row.get("date", ""), sec.last_seen, now),
            "expanded": iid in sec.open,
            "full": iid in sec.full,
            "hasBody": text is not None,
            "loadingBody": self.tasks.running(self._body_key(key, iid)),
            "bodyHtml": body_to_html(text, body.get("links")) if text else "",
            "error": (body or {}).get("error", ""),
            "clamp": key == "changelog",
        }

    def has_more(self, key) -> bool:
        sec = self.sections[key]
        return (not sec.loaded or len(sec.order) > sec.shown
                or sec.next_start is not None)

    # -- actions --------------------------------------------------------
    def start(self):
        """Show the cache (already loaded) and refresh both sections in the
        background. Bodies the cache lacks are read for what is open."""
        for sec in self.sections.values():
            self.refresh(sec.key)

    def refresh(self, key):
        sec = self.sections[key]
        if sec.loading:
            return
        sec.loading, sec.error = True, ""
        epoch = sec.epoch
        self.listener.state_changed(key)
        self.tasks.submit(
            f"news:{key}:listing:0",
            lambda: self.fetch.listing(sec.spec["forum"], 0),
            on_done=lambda res: self._listing_in(sec, epoch, 0, res, True),
            on_error=lambda exc: self._listing_failed(sec, epoch, exc))

    def load_more(self, key):
        sec = self.sections[key]
        if sec.error:
            sec.error = ""
        if len(sec.order) > sec.shown:
            sec.shown += sec.spec["page"]
            self.listener.list_changed(key, sec.shown - sec.spec["page"])
            self._ensure_bodies(sec)
        elif sec.next_start is not None and not sec.loading:
            start, epoch = sec.next_start, sec.epoch
            sec.loading = True
            sec.shown += sec.spec["page"]
            self.listener.state_changed(key)
            self.tasks.submit(
                f"news:{key}:listing:{start}",
                lambda: self.fetch.listing(sec.spec["forum"], start),
                on_done=lambda res: self._listing_in(sec, epoch, start, res,
                                                     False),
                on_error=lambda exc: self._listing_failed(sec, epoch, exc))
        self.listener.state_changed(key)

    def toggle(self, key, iid):
        sec = self.sections[key]
        if iid in sec.open:
            sec.open.discard(iid)
        else:
            sec.open.add(iid)
            b = sec.bodies.get(iid)
            if b is None or b.get("error") or self._stale(b):
                if b is not None and b.get("error"):
                    sec.bodies.pop(iid, None)
                self._read_body(sec, iid)
        self.listener.entries_changed(key, [iid])

    def toggle_full(self, key, iid):
        sec = self.sections[key]
        sec.full.symmetric_difference_update({iid})
        self.listener.entries_changed(key, [iid])

    def mark_seen(self, key):
        """The list was shown: the next launch compares against now."""
        sec = self.sections[key]
        stamp = self.now()
        if self.seen_store is not None:
            self.seen_store.update("news_seen", lambda d: d.__setitem__(key, stamp))

    # -- results --------------------------------------------------------
    def _listing_in(self, sec, epoch, start, result, is_refresh):
        if epoch != sec.epoch:
            return
        rows, nxt = result
        before = list(sec.order[:sec.shown])
        changed = []
        for r in rows:
            iid = str(r["id"])
            old = sec.rows.get(iid)
            if old != r:
                sec.rows[iid] = r
                if old is not None:
                    changed.append(iid)
        if is_refresh and start == 0 and sec.next_start in (0, None):
            sec.next_start = nxt
        elif not is_refresh:
            sec.next_start = nxt
        sec.loading, sec.loaded, sec.error, sec.note = False, True, "", ""
        sec.last_updated = self.now()
        sec.order = self._visible(sec)
        self._default_open(sec)
        self._notify_listing(sec, before, changed)
        self._ensure_bodies(sec)
        self._save_cache(sec)

    def _listing_failed(self, sec, epoch, exc):
        if epoch != sec.epoch:
            return
        sec.loading = False
        sec.error = getattr(exc, "short", None) or str(exc) or type(exc).__name__
        if sec.spec["fallback_feed"] and not sec.rows:
            self._try_feed(sec, exc)
            return
        self.listener.state_changed(sec.key)

    def _try_feed(self, sec, forum_exc):
        epoch = sec.epoch

        def done(items):
            if epoch != sec.epoch:
                return
            if items:
                item = max(items, key=lambda i: i["date"])
                iid = str(item["id"])
                sec.rows[iid] = {"id": iid, "title": item["title"],
                                 "author": item.get("author"),
                                 "date": item["date"], "url": item.get("url", ""),
                                 "kind": "normal"}
                sec.bodies[iid] = {"body": item.get("body", ""), "links": [],
                                   "ts": self.now()}
                sec.order = self._visible(sec)
                self._default_open(sec)
                sec.note = getattr(forum_exc, "short", "") or str(forum_exc)
                sec.error = ""
            self.listener.list_changed(sec.key, 0)
            self.listener.state_changed(sec.key)

        def failed(_exc):
            self.listener.state_changed(sec.key)
        self.tasks.submit(f"news:{sec.key}:feed", self.fetch.feed,
                          on_done=done, on_error=failed)

    def _notify_listing(self, sec, before, changed):
        after = sec.order[:sec.shown]
        if after[:len(before)] != before or len(after) != len(before):
            first = next((i for i, (a, b) in enumerate(zip(before, after))
                          if a != b), min(len(before), len(after)))
            self.listener.list_changed(sec.key, first)
        if changed:
            self.listener.entries_changed(sec.key, changed)
        self.listener.state_changed(sec.key)

    # -- bodies ---------------------------------------------------------
    @staticmethod
    def _body_key(key, iid):
        return f"news:{key}:body:{iid}"

    def _stale(self, body) -> bool:
        return self.now() - float(body.get("ts") or 0) >= BODY_TTL

    def _ensure_bodies(self, sec: Section):
        """Read what is open and what is to be prefetched, if not cached.
        Cached-but-stale bodies are refreshed quietly: the old text stays
        until the new one replaces it in place."""
        want = [i for i in sec.order[:sec.shown] if i in sec.open]
        want += sec.order[:sec.spec["prefetch"]]
        seen = set()
        for iid in want:
            if iid in seen:
                continue
            seen.add(iid)
            b = sec.bodies.get(iid)
            if b is None or (self._stale(b) and not b.get("error")):
                self._read_body(sec, iid)

    def _read_body(self, sec: Section, iid: str):
        entry = sec.rows.get(iid)
        if entry is None or iid.startswith("feed:"):
            return
        epoch = sec.epoch
        started = self.tasks.submit(
            self._body_key(sec.key, iid), lambda: self.fetch.first_post(entry),
            on_done=lambda res: self._body_in(sec, epoch, iid, res),
            on_error=lambda exc: self._body_failed(sec, epoch, iid, exc))
        if started:
            self.listener.entries_changed(sec.key, [iid])

    def _body_in(self, sec, epoch, iid, post):
        if epoch != sec.epoch:
            return
        sec.bodies[iid] = {"body": post["body"], "links": post["links"],
                           "ts": self.now()}
        row = sec.rows.get(iid)
        if row is not None:
            if post.get("author") and not row.get("author"):
                row["author"] = post["author"]
            if post.get("date") and not row.get("date"):
                row["date"] = post["date"]
        self.listener.entries_changed(sec.key, [iid])
        self._save_cache(sec)

    def _body_failed(self, sec, epoch, iid, exc):
        if epoch != sec.epoch:
            return
        old = sec.bodies.get(iid)
        reason = getattr(exc, "short", None) or str(exc)
        if old and old.get("body") is not None:
            old["error"] = ""              # keep showing the cached text
        else:
            sec.bodies[iid] = {"body": "", "links": [], "ts": 0,
                               "error": tr("{section} unavailable: {reason}",
                                           section=tr(sec.spec["title"]),
                                           reason=tr(reason))}
        self.listener.entries_changed(sec.key, [iid])
