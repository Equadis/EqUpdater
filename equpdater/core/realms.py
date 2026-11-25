"""OctoWoW's realms, from OctoBot's published list rather than from this file.

``realms.json`` (OctoBot, static, GitHub Pages) names every realm and the website-wide
aggregate ``all``. The aggregate is not a realm and is dropped. The list is fetched
rarely (daily), conditional GET, and cached; if it cannot be read the last good copy
is used, and if there never was one a **built-in fallback** is used and marked as such
(``authoritative`` False) so nothing claims to know more than it does.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

REALMS_URL = "https://equadis.github.io/OctoBot/realms.json"
REFRESH_S = 24 * 3600
MAX_BYTES = 32 * 1024

#: Used only when no list was ever fetched. Marked non-authoritative.
FALLBACK = (("nzoth", "N'Zoth", "Normal"), ("yshaarj", "Y'Shaarj", "PvP"), ("cthun", "C'Thun", "Hardcore"))


@dataclass(frozen=True)
class Realm:
    id: str
    name: str
    kind: str = ""
    authoritative: bool = True


def parse(obj) -> list:
    """Realms from ``realms.json``; malformed entries are skipped."""
    out = []
    if not isinstance(obj, list):
        return out
    for e in obj:
        if not isinstance(e, dict):
            continue
        rid = str(e.get("id") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{2,24}", rid) or rid == "all":
            continue
        raw = str(e.get("name") or rid)
        raw = re.sub(r"[\u2018\u2019\u00b4`]", "'", raw)
        m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", raw)
        name, kind = (m.group(1), m.group(2)) if m else (raw, "")
        name = re.sub(r"[^\w' \-]", "'", name).strip()[:32] or rid
        if rid not in {r.id for r in out}:
            out.append(Realm(rid, name, kind.strip()[:16]))
    return out


class RealmList:
    def __init__(self, store, fetch=None, now=time.time):
        self.store, self.fetch, self.now = store, fetch, now
        self.on_change = lambda: None
        self._realms: list = []
        for d in (store.get("realms") or []):
            if isinstance(d, dict):
                self._realms += parse([d])
        self.fetched_at = float(store.get("realms_at") or 0)

    def all(self) -> list:
        if self._realms:
            return list(self._realms)
        return [Realm(i, n, k, False) for i, n, k in FALLBACK]

    def ids(self) -> set:
        return {r.id for r in self.all()}

    def name(self, realm_id: str) -> str:
        return next((r.name for r in self.all() if r.id == realm_id), realm_id)

    @property
    def authoritative(self) -> bool:
        return bool(self._realms)

    def due(self) -> bool:
        return self.fetch is not None and self.now() - self.fetched_at > REFRESH_S

    def refresh(self) -> bool:
        """Fetch once (blocking: a worker). Keeps the old list on any failure."""
        if self.fetch is None:
            return False
        try:
            r = self.fetch(REALMS_URL, "")
            if r.status != 200 or len(r.body) > MAX_BYTES:
                return False
            realms = parse(json.loads(r.body.decode("utf-8", "replace")))
        except Exception:                                       # noqa: BLE001
            return False
        if not realms:
            return False
        self._realms = realms
        self.fetched_at = self.now()
        self.store.set("realms", [{"id": r.id, "name": r.name + (f" ({r.kind})" if r.kind else "")} for r in realms])
        self.store.set("realms_at", self.fetched_at)
        self.on_change()
        return True
