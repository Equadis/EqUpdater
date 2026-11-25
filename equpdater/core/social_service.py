"""The social sidebar's logic, Qt-free: rows to show, own status, notifications.

Everything optional and private: with ``social_enabled`` off nothing here runs a
request, and with no presence service (the situation today) the list is a local
friends list whose entries are shown with an unavailable status.
"""

from __future__ import annotations

import time

from ..i18n import tr
from . import friends as F
from . import presence as P
from .settings import SOCIAL_DEFAULTS

STATUS_CHOICES = ("online", "away", "dnd")
AUTO_AWAY_CHOICES = (0, 5, 10, 15, 30)
INTERVAL_CHOICES = (30, 60, 120, 300)

class SocialService:
    def __init__(self, settings, book: F.FriendBook, presence: P.PresenceService, account, realms, now=time.time):
        self.s, self.book, self.presence, self.account, self.realms, self.now = settings, book, presence, account, realms, now
        self.on_change = lambda: None
        self.toasts: list = []                    # (title, text) the bridge shows and clears
        self.search = ""
        self.auto_away = False
        self._published = None
        presence.on_friend_online = self._friend_online
        presence.set_interval(self.get("presence_interval"))
        presence.enabled = bool(self.get("social_enabled"))

    # -- settings ------------------------------------------------------------------------------
    def get(self, key):
        v = self.s.get(key)
        return SOCIAL_DEFAULTS[key] if v is None and key in SOCIAL_DEFAULTS and key != "sidebar_collapsed" else v

    def set(self, key, value):
        if key not in SOCIAL_DEFAULTS:
            raise KeyError(key)
        if key == "default_status" and value not in STATUS_CHOICES:
            return
        if key == "own_status" and value not in STATUS_CHOICES:
            return
        if key == "auto_away_minutes" and value not in AUTO_AWAY_CHOICES:
            return
        if key == "friends_sort" and value not in F.SORTS:
            return
        if key == "presence_interval":
            value = min(900, max(30, int(value)))
            self.presence.set_interval(value)
        self.s.set(key, value)
        if key == "social_enabled":
            self.presence.enabled = bool(value)
        self.on_change()

    # -- own status ------------------------------------------------------------------------------
    @property
    def status(self) -> str:
        """What the player's own presence is right now (auto-away only softens 'online')."""
        manual = self.get("own_status") or self.get("default_status")
        return "away" if (self.auto_away and manual == "online") else manual

    def set_status(self, status: str):
        if status in STATUS_CHOICES:
            self.auto_away = False
            self.set("own_status", status)
            self.publish()

    def reset_status_for_new_session(self):
        self.s.set("own_status", self.get("default_status"))

    def window_idle(self, idle: bool):
        """The window has been unfocused/minimised for the auto-away time (or is back)."""
        if self.get("auto_away_minutes") and idle != self.auto_away:
            self.auto_away = idle
            self.on_change()
            self.publish()

    def own(self) -> P.OwnStatus:
        return P.OwnStatus(self.status, bool(self.get("show_launcher_presence")), bool(self.get("show_game_presence")),
                           bool(self.get("show_character")), bool(self.get("show_realm")), bool(self.get("show_zone")))

    def publish(self) -> bool:
        """Tell the service (only if one exists, accepts it and the player is signed in and allows it).
        Blocking: a worker. Returns whether anything was sent."""
        prov = self.presence.provider
        if not (prov.available and "publish" in prov.capabilities and self.get("social_enabled")):
            return False
        token = self.account.valid_access() if self.account else None
        if self.account and prov.requires_account and token is None:
            return False
        own = self.own()
        if own == self._published:
            return False
        try:
            prov.publish(own, token)
        except Exception:                                            # noqa: BLE001 - presence is best effort
            return False
        self._published = own
        return True

    # -- notifications -----------------------------------------------------------------------------
    def _friend_online(self, friend, obs):
        if self.get("friend_notifications") and self.status != "dnd":
            where = tr("in game") if obs.game == "in_game" else tr("in EqUpdater")
            self.toasts.append((friend.label, tr("is online {where}", where=where)))
            self.on_change()

    def take_toasts(self) -> list:
        out, self.toasts = self.toasts, []
        return out

    # -- the list ---------------------------------------------------------------------------------------
    def online_count(self) -> int:
        return sum(1 for f in self.book.all() if self.presence.state_of(f)[0] in (P.IN_GAME, P.LAUNCHER, P.AWAY, P.DND))

    def rows(self) -> list:
        """Header and friend rows, grouped Favourites / Online / Offline, filtered and sorted."""
        realm = self.get("friends_realm") or ""
        shown = self.book.search(self.search, realm)
        state_of = lambda f: self.presence.state_of(f)[0]               # noqa: E731
        buckets = {g: [] for g in F.GROUPS}
        for f in sorted(shown, key=F.sort_key(self.get("friends_sort"), state_of)):
            buckets[F.group_of(f, state_of(f))].append(f)
        out = []
        titles = {"favourites": tr("Favourites"), "online": tr("Online"), "offline": tr("Offline")}
        for g in F.GROUPS:
            if not buckets[g]:
                continue
            title = titles[g]
            if g == "offline" and any(state_of(f) == P.UNKNOWN for f in buckets[g]):
                title = tr("Offline or unavailable")           # not "offline": nobody said they were
            out.append({"key": "h:" + g, "kind": "header", "group": g, "title": title, "count": len(buckets[g])})
            for f in buckets[g]:
                out.append(self._row(f))
        return out

    def _row(self, f: F.Friend) -> dict:
        state, stale = self.presence.state_of(f)
        obs = self.presence.observation(f)
        cls = obs.char_class.lower() if obs else ""
        sub = []
        if obs and obs.level:
            sub.append(tr("Level {n}", n=obs.level))
        if cls in F.CLASSES:
            sub.append(tr(F.CLASSES[cls][0]))
        extra = [x for x in ((obs.guild and f"<{obs.guild}>"), obs.zone) if x] if obs else []
        return {"key": "f:" + f.id, "kind": "friend", "group": F.group_of(f, state), "id": f.id,
                "name": f.label, "realm": f.realm, "realmName": self.realms.name(f.realm) if f.realm else "",
                "forum": f.forum, "favourite": f.favourite, "state": state, "stateText": P.label(state),
                "stale": stale, "dot": P.COLOURS[state],
                "charClass": cls if cls in F.CLASSES else "", "nameColour": F.class_colour(cls) if cls in F.CLASSES else "",
                "level": obs.level if obs and obs.level else 0, "guild": obs.guild if obs else "",
                "zone": obs.zone if obs else "", "detail": " · ".join(sub + extra),
                "title": "", "count": 0}

    # -- editing -------------------------------------------------------------------------------------------
    def add_friend(self, name: str, realm: str, forum: str = "") -> str:
        """"" on success, else the reason."""
        try:
            self.book.add(name, realm, forum)
        except F.FriendError as e:
            return str(e)
        self.on_change()
        return ""

    def remove_friend(self, friend_id: str):
        if self.book.remove(friend_id):
            self.presence.forget(friend_id)
            self.on_change()

    def toggle_favourite(self, friend_id: str):
        f = self.book.get(friend_id)
        if f:
            self.book.set_favourite(friend_id, not f.favourite)
            self.on_change()

    def account_friends_supported(self) -> bool:
        return "account_characters" in self.presence.provider.capabilities
