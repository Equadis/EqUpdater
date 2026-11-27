"""The friends sidebar's logic, Qt-free: rows to show and the player's own status.

There are no social preferences. The sidebar is always there; presence is published only
for a signed-in player and only as the chosen status allows; the realm is shown while in the game;
the character and the zone are never shown; the player goes Away after 15 minutes without using
the computer; the refresh is one batched request about every five seconds, and only while a
service exists and the player is signed in (``presence.PresenceService``).

With no presence service (the situation today) the list is a local friends list whose entries
are shown as unavailable, and nothing is requested.
"""

from __future__ import annotations

import time

from . import friends as F
from . import presence as P
from ..i18n import tr

STATUS_CHOICES = ("online", "away", "dnd", "invisible")
AUTO_AWAY_SECONDS = 15 * 60


class SocialService:
    def __init__(self, settings, book: F.FriendBook, presence: P.PresenceService, account, realms=None, now=time.time):
        self.s, self.book, self.presence, self.account, self.realms, self.now = settings, book, presence, account, realms, now
        self.on_change = lambda: None
        self.search = ""
        self.auto_away = False
        self.last_activity = now()
        self._published = None

    # -- own status ------------------------------------------------------------------------------
    @property
    def manual_status(self) -> str:
        v = self.s.get("own_status")
        return v if v in STATUS_CHOICES else "online"

    @property
    def status(self) -> str:
        """What the player is shown as. A manual Away, Do Not Disturb or Invisible always wins;
        the idle rule only softens Online to Away."""
        return "away" if (self.auto_away and self.manual_status == "online") else self.manual_status

    def set_status(self, status: str):
        if status in STATUS_CHOICES:
            self.auto_away = False
            self.last_activity = self.now()
            self.s.set("own_status", status)
            self.on_change()
            self.publish()

    def note_activity(self):
        """The player used the computer (a key, the mouse): cheap, called for every input event."""
        self.last_activity = self.now()

    def check_idle(self) -> bool:
        """Apply the 15-minute rule. Returns whether the shown status changed."""
        idle = self.now() - self.last_activity >= AUTO_AWAY_SECONDS
        if idle == self.auto_away:
            return False
        self.auto_away = idle
        self.on_change()
        self.publish()
        return True

    def own(self) -> P.OwnStatus:
        """What is shared. Invisible shares nothing: the player appears offline."""
        st = self.status
        shared = st != "invisible"
        return P.OwnStatus(st, share_game=shared, share_realm=shared)

    def publish(self) -> bool:
        """Tell the service (only if one exists and the player is signed in). Blocking: a worker.
        Returns whether anything was sent. Invisible is sent too, so the service can show the
        player as offline: only the service can hide a player from other people."""
        prov = self.presence.provider
        if not (prov.available and "publish" in prov.capabilities):
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

    # -- the list ---------------------------------------------------------------------------------------
    def online_count(self) -> int:
        return sum(1 for f in self.book.all() if self.presence.state_of(f)[0] in (P.IN_GAME, P.LAUNCHER, P.AWAY, P.DND))

    def rows(self) -> list:
        """Header and friend rows: Favourites first, then by presence (in game, online, away, busy),
        offline last; searched by name. There is no other filter or order."""
        shown = self.book.search(self.search)
        state_of = lambda f: self.presence.state_of(f)[0]               # noqa: E731
        buckets = {g: [] for g in F.GROUPS}
        for f in sorted(shown, key=F.sort_key(state_of)):
            buckets[F.group_of(f, state_of(f))].append(f)
        out = []
        titles = {"favourites": tr("Favourites"), "online": tr("Online"), "offline": tr("Offline")}
        for g in F.GROUPS:
            if not buckets[g]:
                continue
            title = titles[g]
            if g == "offline" and all(state_of(f) == P.UNKNOWN for f in buckets[g]):
                title = tr("Unavailable")                      # nobody said they were offline
            out.append({"key": "h:" + g, "kind": "header", "group": g, "title": title, "count": len(buckets[g])})
            for f in buckets[g]:
                out.append(self._row(f))
        return out

    def _row(self, f: F.Friend) -> dict:
        """Only: the forum user name, the presence, and the realm while in the game."""
        state, stale = self.presence.state_of(f)
        obs = self.presence.observation(f)
        realm = ""
        if state == P.IN_GAME and obs and obs.realm:
            realm = self.realms.name(obs.realm) if self.realms else obs.realm
        return {"key": "f:" + f.id, "kind": "friend", "group": F.group_of(f, state), "id": f.id,
                "name": f.label, "favourite": f.favourite, "state": state, "stale": stale,
                "dot": P.COLOURS[state], "statusLine": P.status_line(state, realm),
                "status": P.label(state), "realm": realm,
                "title": "", "count": 0}

    # -- editing -------------------------------------------------------------------------------------------
    def add_friend(self, username: str) -> str:
        """"" on success, else the reason."""
        try:
            self.book.add(username)
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
