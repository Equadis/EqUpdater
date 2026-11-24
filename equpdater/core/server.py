"""OctoBot server status for the footer chip, Qt-free (docs/v3/OCTOBOT.md).

What is shown is *OctoBot's* data (population, and whether the website, the
realms, the TCP port and the login server answer), never something EqUpdater
measured. The client's job is to be a good citizen and to be honest about age:

* **One snapshot interface.** ``Snapshot`` is one object with a timestamp for
  each part. Today it is assembled from two static files on GitHub Pages
  (``live.json`` and ``status/latest.json``; no Worker, no per-request cost);
  when a combined static ``snapshot.json`` exists, set ``SNAPSHOT_URL`` and
  nothing else changes. **The Worker's ``/api/*`` endpoints are never called.**
* **Conditional GET** (ETag / If-None-Match): a 304 costs nothing to parse.
* **Cadence**: 60 s active (+-15 s jitter), 5 min unfocused, none minimised, one
  refresh on return if older than 30 s; failures back off 60 s -> 15 min and
  reset on success.
* **Never claims "offline" because *we* failed.** A failed fetch shows the last
  good snapshot, marked ``unavailable``; data older than 3x its source's
  cadence is ``stale``. Only OctoBot's own sample can say offline.
* The last good snapshot is kept in ``cache/server.json`` so the chip is not
  empty at start-up.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..i18n import tr

#: Where the data comes from. SNAPSHOT_URL (one combined static object) wins
#: when set; it is None until the owner approves producing one (OCTOBOT.md).
SNAPSHOT_URL: str | None = None
LIVE_URL = "https://equadis.github.io/OctoBot/live.json"
STATUS_URL = "https://equadis.github.io/OctoBot/status/latest.json"
ALLOWED_HOSTS = {"equadis.github.io"}

ACTIVE_S, UNFOCUSED_S, JITTER_S = 60.0, 300.0, 15.0
BACKOFF_FIRST_S, BACKOFF_MAX_S = 60.0, 900.0
RETURN_REFRESH_S = 30.0
#: How often the sources are refreshed (GitHub Actions: every 5 minutes, best
#: effort); a part is stale after three of these. A static snapshot says its own.
DEFAULT_CADENCE_S = 300.0
MAX_BYTES = 64 * 1024

REALMS = (("nzoth", "N'Zoth"), ("yshaarj", "Y'Shaarj"), ("cthun", "C'Thun"))

ONLINE, OFFLINE, DEGRADED, UNKNOWN = "online", "offline", "degraded", "unknown"


def parse_time(value) -> float | None:
    """Epoch seconds from an ISO-8601 string or a number (ms or s), else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value < 0 or value > 10_000_000:
        return None
    return int(value)


def _word(value, allowed) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else ""


@dataclass
class Population:
    online: int | None = None
    ts: float | None = None
    source: str = ""


@dataclass
class Snapshot:
    population: dict = field(default_factory=dict)      # realm id or "all" -> Population
    website: str = ""        # available | unavailable
    tcp: str = ""            # reachable | unreachable
    auth: str = ""           # responsive | unresponsive
    realms: dict = field(default_factory=dict)          # realm id -> online | offline
    latency_ms: int | None = None
    status_ts: float | None = None
    cadence_s: float = DEFAULT_CADENCE_S

    # -- reading ----------------------------------------------------------------
    def total(self) -> Population | None:
        """The all-realms figure: the website census, else the sum of realms."""
        p = self.population.get("all")
        if p and p.online is not None:
            return p
        parts = [self.population[r] for r, _n in REALMS
                 if r in self.population and self.population[r].online is not None]
        if parts:
            return Population(sum(p.online for p in parts),
                              max((p.ts or 0) for p in parts) or None, "realms")
        return None

    def state(self) -> str:
        """OctoBot's own verdict from its last probe of the login server."""
        if self.status_ts is None:
            return UNKNOWN
        if self.tcp == "unreachable" or self.auth == "unresponsive":
            return OFFLINE
        if self.tcp == "reachable" and self.auth == "responsive":
            return ONLINE if self.website in ("", "available") else DEGRADED
        if self.tcp or self.auth or self.website:
            return DEGRADED
        return UNKNOWN

    def to_json(self) -> dict:
        return {"v": 1, "website": self.website, "tcp": self.tcp, "auth": self.auth,
                "realms": self.realms, "latency_ms": self.latency_ms,
                "status_ts": self.status_ts, "cadence_s": self.cadence_s,
                "population": {k: {"online": p.online, "ts": p.ts, "source": p.source}
                               for k, p in self.population.items()}}

    @classmethod
    def from_json(cls, d) -> "Snapshot | None":
        if not isinstance(d, dict) or d.get("v") != 1:
            return None
        s = cls(website=_word(d.get("website"), ("available", "unavailable")),
                tcp=_word(d.get("tcp"), ("reachable", "unreachable")),
                auth=_word(d.get("auth"), ("responsive", "unresponsive")),
                latency_ms=_count(d.get("latency_ms")),
                status_ts=parse_time(d.get("status_ts")),
                cadence_s=_cadence(d.get("cadence_s")))
        for rid, v in (d.get("realms") or {}).items():
            if rid in dict(REALMS) and _word(v, (ONLINE, OFFLINE)):
                s.realms[rid] = _word(v, (ONLINE, OFFLINE))
        for rid, p in (d.get("population") or {}).items():
            if rid in ("all", *dict(REALMS)) and isinstance(p, dict):
                s.population[rid] = Population(_count(p.get("online")), parse_time(p.get("ts")),
                                               str(p.get("source") or "")[:60])
        return s


def _cadence(v) -> float:
    n = _count(v)
    return float(n) if n and 10 <= n <= 86400 else DEFAULT_CADENCE_S


def parse_live(obj) -> dict:
    """``live.json`` -> {realm id: Population}. Anything malformed is skipped."""
    out = {}
    if isinstance(obj, dict):
        for rid, p in obj.items():
            if rid in ("all", *dict(REALMS)) and isinstance(p, dict):
                out[rid] = Population(_count(p.get("online")), parse_time(p.get("timestamp")),
                                      str(p.get("source") or "")[:60])
    return out


def apply_status(snap: Snapshot, obj) -> None:
    """``status/latest.json`` -> the status fields of ``snap``."""
    sample = obj.get("sample") if isinstance(obj, dict) else None
    if not isinstance(sample, dict):
        return
    snap.status_ts = parse_time(sample.get("timestamp"))
    snap.website = _word(sample.get("website"), ("available", "unavailable"))
    snap.tcp = _word(sample.get("tcp"), ("reachable", "unreachable"))
    snap.auth = _word(sample.get("auth"), ("responsive", "unresponsive"))
    snap.latency_ms = _count(sample.get("latency_ms"))
    for rid, v in (sample.get("realms") or {}).items():
        if rid in dict(REALMS) and _word(v, (ONLINE, OFFLINE)):
            snap.realms[rid] = _word(v, (ONLINE, OFFLINE))


def parse_combined(obj) -> "Snapshot | None":
    """The future ``snapshot.json``:
    ``{schema:1, cadence_s, population:{...as live.json}, status:{...as status/latest.json}}``."""
    if not isinstance(obj, dict) or obj.get("schema") != 1:
        return None
    snap = Snapshot(population=parse_live(obj.get("population")),
                    cadence_s=_cadence(obj.get("cadence_s")))
    apply_status(snap, {"sample": obj.get("status")})
    return snap


# -- age -------------------------------------------------------------------------------

def freshest(snap: Snapshot) -> float | None:
    times = [t for t in [snap.status_ts] + [p.ts for p in snap.population.values()] if t]
    return max(times) if times else None


def is_stale(snap: Snapshot, now: float) -> bool:
    """Everything the snapshot knows is older than three of its source's cadences."""
    newest = freshest(snap)
    return newest is None or now - newest > 3 * snap.cadence_s


def age_text(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return tr("just now")
    if s < 5400:
        return tr("{n} min ago", n=round(s / 60))
    if s < 172800:
        return tr("{n} h ago", n=round(s / 3600))
    return tr("{n} days ago", n=s // 86400)


# -- the feed ---------------------------------------------------------------------------

class Response:
    __slots__ = ("status", "body", "etag")

    def __init__(self, status: int, body: bytes = b"", etag: str = ""):
        self.status, self.body, self.etag = status, body, etag


def http_fetch(url: str, etag: str = "", timeout: float = 8.0) -> Response:
    """One conditional GET of a static file on an allowed host (never a Worker)."""
    import urllib.error
    import urllib.request

    from .. import branding
    from .net import secure_urlopen
    headers = {"User-Agent": branding.user_agent(), "Accept": "application/json"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with secure_urlopen(req, timeout=timeout, allowed_hosts=ALLOWED_HOSTS) as r:
            return Response(r.status, r.read(MAX_BYTES + 1), r.headers.get("ETag", ""))
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return Response(304, b"", etag)
        raise


class ServerFeed:
    """The poller's brain: when to fetch next, what a result means. The thread
    and timer belong to the caller (the bridge); ``fetch_once`` is blocking and
    runs on a worker."""

    def __init__(self, store, fetch=http_fetch, now=time.time, jitter=random.uniform,
                 snapshot_url=None):
        self.store, self.fetch, self.now, self.jitter = store, fetch, now, jitter
        self.snapshot_url = snapshot_url if snapshot_url is not None else SNAPSHOT_URL
        self.etags: dict = {}
        self.snapshot: Snapshot | None = Snapshot.from_json(store.get("snapshot"))
        self.fetched_at: float = float(store.get("fetched_at") or 0)
        self.failures = 0
        self.unavailable = False
        self.requests = 0
        self.focused, self.minimized = True, False

    # -- scheduling ----------------------------------------------------------------------
    def delay(self) -> float | None:
        """Seconds until the next fetch, or None while minimised."""
        if self.minimized:
            return None
        if self.failures:
            return min(BACKOFF_MAX_S, BACKOFF_FIRST_S * 2 ** (self.failures - 1))
        base = ACTIVE_S if self.focused else UNFOCUSED_S
        return max(5.0, base + self.jitter(-JITTER_S, JITTER_S))

    def wants_refresh_on_return(self) -> bool:
        return not self.fetched_at or self.now() - self.fetched_at > RETURN_REFRESH_S

    # -- fetching ------------------------------------------------------------------------
    def _get(self, url: str):
        self.requests += 1
        r = self.fetch(url, self.etags.get(url, ""))
        if r.status == 304:
            return None
        if r.status != 200 or len(r.body) > MAX_BYTES:
            raise ValueError(f"unexpected answer {r.status}")
        if r.etag:
            self.etags[url] = r.etag
        return json.loads(r.body.decode("utf-8"))

    def fetch_once(self) -> bool:
        """Fetch and fold in the result. Returns whether the snapshot changed.
        Never raises: a failure marks the feed ``unavailable`` and backs off."""
        try:
            changed = self._fetch_snapshot()
        except Exception:                      # noqa: BLE001 - any failure is "unavailable"
            self.failures += 1
            self.unavailable = True
            return False
        self.failures, self.unavailable = 0, False
        self.fetched_at = self.now()
        self.store.set("fetched_at", self.fetched_at)
        return changed

    def _fetch_snapshot(self) -> bool:
        if self.snapshot_url:
            obj = self._get(self.snapshot_url)
            if obj is None:
                return False
            snap = parse_combined(obj)
            if snap is None:
                raise ValueError("not a snapshot")
            return self._accept(snap)
        live, status = self._get(LIVE_URL), self._get(STATUS_URL)
        if live is None and status is None:
            return False                         # both unchanged (304)
        snap = Snapshot.from_json(self.snapshot.to_json()) if self.snapshot else Snapshot()
        if live is not None:
            pops = parse_live(live)
            if not pops:
                raise ValueError("no population in live.json")
            snap.population.update(pops)
        if status is not None:
            before = snap.status_ts
            apply_status(snap, status)
            if snap.status_ts is None and before is None:
                raise ValueError("no sample in status")
        return self._accept(snap)

    def _accept(self, snap: Snapshot) -> bool:
        changed = self.snapshot is None or snap.to_json() != self.snapshot.to_json()
        self.snapshot = snap
        self.store.set("snapshot", snap.to_json())
        return changed

    # -- what the chip shows -----------------------------------------------------------------
    def view(self) -> dict:
        """Everything a view needs, computed from the last good snapshot."""
        snap, now = self.snapshot, self.now()
        if snap is None:
            return {"state": UNKNOWN, "total": None, "stale": False,
                    "unavailable": self.unavailable, "age": None, "realms": [], "latency_ms": None,
                    "website": "", "has_data": False}
        total = snap.total()
        newest = freshest(snap)
        realms = []
        for rid, name in REALMS:
            p = snap.population.get(rid)
            realms.append({"id": rid, "name": name,
                           "online": p.online if p else None,
                           "state": snap.realms.get(rid, "")})
        return {"state": snap.state(), "total": total.online if total else None,
                "total_source": total.source if total else "",
                "stale": is_stale(snap, now), "unavailable": self.unavailable,
                "age": (now - newest) if newest else None, "realms": realms,
                "latency_ms": snap.latency_ms, "website": snap.website,
                "status_age": (now - snap.status_ts) if snap.status_ts else None,
                "status_stale": (snap.status_ts is None
                                 or now - snap.status_ts > 3 * snap.cadence_s),
                "has_data": True}
