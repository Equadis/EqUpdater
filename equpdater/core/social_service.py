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
from .gamewatch import IN_GAME, Activity

STATUS_CHOICES = ("online", "away", "dnd", "invisible")
AUTO_AWAY_SECONDS = 15 * 60


class SocialService:
    def __init__(self, settings, book: F.FriendBook, presence: P.PresenceService, account, realms=None, now=time.time,
                 activity=None):
        self.s, self.book, self.presence, self.account, self.realms, self.now = settings, book, presence, account, realms, now
        self.activity_source = activity                # an object with read() -> gamewatch.Activity, or None
        self.activity = Activity()
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
        if st == "invisible":
            return P.OwnStatus(st, "launcher", "")
        a = self.activity
        return P.OwnStatus(st, "in_game" if a.kind == IN_GAME else "launcher", a.realm if a.kind == IN_GAME else "")

    def refresh_activity(self) -> bool:
        """Read what the player is doing (launcher open, or the game running and where). Runs on the worker
        just before each presence round, so the heartbeat carries it. Returns whether it changed."""
        if self.activity_source is None:
            return False
        try:
            now = self.activity_source.read()
        except Exception:                                            # noqa: BLE001 - never an error for presence
            return False
        changed = now != self.activity
        self.activity = now
        prov = self.presence.provider
        if hasattr(prov, "own"):
            prov.own = self.own()                                    # the heartbeat sends this
        if changed:
            self.on_change()
        return changed

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
    def _signed_out_held(self) -> bool:
        """The service keeps the list and nobody is signed in: there is no list to show."""
        return self._held() and not (self.account and self.account.state == "signed_in")

    def friend_count(self) -> int:
        return 0 if self._signed_out_held() else len(self.book)

    def online_count(self) -> int:
        if self._signed_out_held():
            return 0
        return sum(1 for f in self.book.all() if self.presence.state_of(f)[0] in (P.IN_GAME, P.LAUNCHER, P.AWAY, P.DND))

    def rows(self) -> list:
        """Header and friend rows: Favourites first, then by presence (in game, online, away, busy),
        offline last; searched by name. There is no other filter or order."""
        shown = [] if self._signed_out_held() else self.book.search(self.search)
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
        dot = P.COLOURS[state]
        if state in (P.IN_GAME, P.LAUNCHER) and obs and obs.launcher in ("away", "dnd"):
            dot = P.COLOURS[P.AWAY if obs.launcher == "away" else P.DND]       # Away and Do Not Disturb show in the dot
        status = P.AWAY if (obs and obs.launcher == "away" and state != P.OFFLINE) else             P.DND if (obs and obs.launcher == "dnd" and state != P.OFFLINE) else state
        return {"key": "f:" + f.id, "kind": "friend", "group": F.group_of(f, state), "id": f.id,
                "name": f.label, "favourite": f.favourite, "state": state, "stale": stale,
                "dot": dot, "statusLine": P.status_line(state, realm), "statusName": P.status_name(status),
                "status": P.label(state), "realm": realm, "title": "", "count": 0}

    # -- editing -------------------------------------------------------------------------------------------
    def _held(self) -> bool:
        """The service keeps the list (and so must be asked to change it)."""
        return bool(self.presence.provider.syncs_friends)

    def _token(self):
        return self.account.valid_access() if self.account else None

    def add_friend(self, username: str) -> str:
        """"" on success, else the reason. Blocking when the service keeps the list: run it on a worker."""
        if self._held():
            name = (username or "").strip()
            if not F.FORUM_RE.match(name):
                return tr("A forum user name is 3 to 32 letters, numbers, spaces, dots, dashes or underscores.")
            token = self._token()
            if not token:
                return tr("Sign in to add friends.")
            err = self.presence.provider.add_friend(token, name)
            if not err:
                self.presence.not_before = 0.0
            return err
        try:
            self.book.add(username)
        except F.FriendError as e:
            return str(e)
        self.on_change()
        return ""

    def remove_friend(self, friend_id: str) -> str:
        f = self.book.get(friend_id)
        if f and self._held():
            token = self._token()
            err = self.presence.provider.remove_friend(token, f.label) if token else tr("Sign in to change your friends.")
            if err:
                return err
        if self.book.remove(friend_id):
            self.presence.forget(friend_id)
            self.on_change()
        return ""

    def toggle_favourite(self, friend_id: str) -> str:
        f = self.book.get(friend_id)
        if not f:
            return ""
        if self._held():
            token = self._token()
            err = self.presence.provider.set_favourite(token, f.label, not f.favourite) if token else tr("Sign in to change your friends.")
            if err:
                return err
        self.book.set_favourite(friend_id, not f.favourite)
        self.on_change()
        return ""
