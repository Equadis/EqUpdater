"""The friends list: local, private, Qt-free.

A friend is an OctoWoW forum user name. The list lives on this computer only
(``<app data>/v3/social.json``); EqUpdater sends it nowhere. Lists saved by the
first beta held *characters* (name and realm); those entries are kept and shown
by their name, but new friends are forum user names only.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass

from ..i18n import tr

MAX_FRIENDS = 200
NAME_RE = re.compile(r"^[^\W\d_]{2,12}$")                  # a first-beta character entry
FORUM_RE = re.compile(r"^[\w.\- ]{3,32}$")

GROUPS = ("favourites", "online", "offline")


@dataclass
class Friend:
    id: str
    name: str = ""                 # character name (display case)
    realm: str = ""                # realm id, e.g. "nzoth"
    forum: str = ""                # forum user name, for an account friend
    favourite: bool = False
    added: float = 0.0

    @property
    def key(self) -> tuple:
        return (self.name.casefold(), self.realm, self.forum.casefold())

    @property
    def label(self) -> str:
        return self.name or self.forum

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "realm": self.realm, "forum": self.forum,
                "favourite": self.favourite, "added": self.added}

    @classmethod
    def from_json(cls, d) -> "Friend | None":
        if not isinstance(d, dict) or not isinstance(d.get("id"), str):
            return None
        name, forum = str(d.get("name") or ""), str(d.get("forum") or "")
        if not (NAME_RE.match(name) if name else FORUM_RE.match(forum)):
            return None
        return cls(d["id"], name, str(d.get("realm") or ""), forum, bool(d.get("favourite")),
                   float(d.get("added") or 0))


class FriendError(Exception):
    """The reason a friend was not added (an N_ message, formatted)."""


class FriendBook:
    def __init__(self, store, realms=None, now=time.time):
        """``store`` is a JsonStore with a ``friends`` list; ``realms`` a callable giving the valid
        realm ids (None: any non-empty id is accepted)."""
        self.store, self.realms, self.now = store, realms, now
        self.on_change = lambda: None
        self._friends: list = []
        for d in store.get("friends") or []:
            f = Friend.from_json(d)
            if f and f.key not in {x.key for x in self._friends}:
                self._friends.append(f)

    # -- reading ------------------------------------------------------------------------------
    def all(self) -> list:
        return list(self._friends)

    def get(self, friend_id: str) -> Friend | None:
        return next((f for f in self._friends if f.id == friend_id), None)

    def __len__(self):
        return len(self._friends)

    def search(self, text: str = "") -> list:
        q = (text or "").strip().casefold()
        return [f for f in self._friends if not q or q in f.name.casefold() or q in f.forum.casefold()]

    # -- changing ----------------------------------------------------------------------------------
    def _save(self):
        self.store.set("friends", [f.to_json() for f in self._friends])
        self.on_change()

    def add(self, username: str) -> Friend:
        """Add a friend by OctoWoW forum user name. Raises FriendError."""
        forum = (username or "").strip()
        if not FORUM_RE.match(forum):
            raise FriendError(tr("A forum user name is 3 to 32 letters, numbers, spaces, dots, dashes or underscores."))
        friend = Friend(uuid.uuid4().hex[:12], "", "", forum, False, self.now())
        if friend.key in {f.key for f in self._friends}:
            raise FriendError(tr("{name} is already in your friends list.", name=friend.label))
        if len(self._friends) >= MAX_FRIENDS:
            raise FriendError(tr("The friends list is full ({count}).", count=MAX_FRIENDS))
        self._friends.append(friend)
        self._save()
        return friend

    def remove(self, friend_id: str) -> bool:
        before = len(self._friends)
        self._friends = [f for f in self._friends if f.id != friend_id]
        if len(self._friends) != before:
            self._save()
            return True
        return False

    def set_favourite(self, friend_id: str, value: bool) -> bool:
        f = self.get(friend_id)
        if f is None or f.favourite == bool(value):
            return False
        f.favourite = bool(value)
        self._save()
        return True


# -- ordering and grouping (pure) ----------------------------------------------------------------

#: Playing first, then in EqUpdater, away, busy; then "unavailable" and last offline.
STATE_ORDER = {"in_game": 0, "launcher": 1, "away": 2, "dnd": 3, "unknown": 4, "offline": 5}


def group_of(friend: Friend, state: str) -> str:
    if friend.favourite:
        return "favourites"
    return "online" if state in ("in_game", "launcher", "away", "dnd") else "offline"


def sort_key(state_of):
    """By presence (the only order there is), then by name."""
    return lambda f: (STATE_ORDER.get(state_of(f), 9), f.label.casefold())
