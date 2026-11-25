"""Presence: who is where, with the honesty a social list needs. Qt-free.

Two *different* things are called presence and they are never merged:

* **launcher** presence: the person is using EqUpdater (online / away / do not disturb);
* **game** presence: the *character* is in the world (in game / offline).

The six states a player sees (``Presence``) are derived from those by ``resolve``.
The one rule that matters: **not being in EqUpdater says nothing about whether a
character is in the game**. A friend is only shown *offline* when a service has
told us the *character* is offline; with no information they are *unknown*.
Aggregate census numbers are never used as an individual's presence.

``PresenceProvider`` is the data source and is replaceable. None exists today
(OctoWoW publishes no character-presence or account-to-character service;
``docs/v3/SOCIAL.md``), so the provider in use is ``UnavailablePresenceProvider``,
which returns nothing, and every friend is shown as *unknown*. It makes no
network requests. ``PresenceService`` is the machinery that will serve a real
provider: batching, caching, staleness, rate limits and backoff.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..i18n import tr

IN_GAME, LAUNCHER, AWAY, DND, OFFLINE, UNKNOWN = "in_game", "launcher", "away", "dnd", "offline", "unknown"
STATES = (IN_GAME, LAUNCHER, AWAY, DND, OFFLINE, UNKNOWN)

COLOURS = {IN_GAME: "#3ddc84", LAUNCHER: "#3b9cff", AWAY: "#f2c744", DND: "#ef5350",
           OFFLINE: "#7d8590", UNKNOWN: "#7d8590"}
GAME_CHANNEL = ("in_game", "offline", "unknown")
LAUNCHER_CHANNEL = ("online", "away", "dnd", "offline", "unknown")


def label(state: str) -> str:
    return {IN_GAME: tr("In game"), LAUNCHER: tr("Online in EqUpdater"), AWAY: tr("Away"),
            DND: tr("Do not disturb"), OFFLINE: tr("Offline"), UNKNOWN: tr("Status unavailable")}.get(state, "")


@dataclass
class Observation:
    """What a service said about one friend, and when."""
    friend_id: str
    game: str = "unknown"                 # in_game | offline | unknown
    launcher: str = "unknown"             # online | away | dnd | offline | unknown
    as_of: float | None = None
    char_class: str = ""
    level: int | None = None
    guild: str = ""
    zone: str = ""
    realm: str = ""
    source: str = ""

    def clean(self) -> "Observation":
        """Values from a service are not trusted: unknown words become unknown."""
        self.game = self.game if self.game in GAME_CHANNEL else "unknown"
        self.launcher = self.launcher if self.launcher in LAUNCHER_CHANNEL else "unknown"
        self.level = self.level if isinstance(self.level, int) and not isinstance(self.level, bool) \
            and 1 <= self.level <= 70 else None
        for k in ("guild", "zone", "char_class", "realm", "source"):
            v = getattr(self, k)
            setattr(self, k, v.strip()[:48] if isinstance(v, str) else "")
        return self


def resolve(obs: Observation | None, now: float, stale_after: float) -> tuple:
    """(state, stale) for one friend. ``stale``: the information is older than ``stale_after``."""
    if obs is None or obs.as_of is None:
        return UNKNOWN, False
    stale = now - obs.as_of > stale_after
    if obs.game == "in_game":
        return IN_GAME, stale
    if obs.launcher in ("online", "away", "dnd"):
        return {"online": LAUNCHER, "away": AWAY, "dnd": DND}[obs.launcher], stale
    if obs.game == "offline":
        return OFFLINE, stale                 # a service said the *character* is offline
    return UNKNOWN, stale                     # not in EqUpdater and nothing about the game: say nothing


class ProviderError(Exception):
    pass


class RateLimited(ProviderError):
    def __init__(self, retry_after: float = 60.0):
        super().__init__("rate limited")
        self.retry_after = max(1.0, float(retry_after))


@dataclass
class OwnStatus:
    """What this player allows to be shown to others."""
    status: str = "online"                # online | away | dnd
    show_launcher: bool = False
    show_game: bool = False
    show_character: bool = False
    show_realm: bool = False
    show_zone: bool = False


class PresenceProvider:
    id = "none"
    name = ""
    available = False
    requires_account = False
    capabilities: frozenset = frozenset()   # game_presence launcher_presence character_details account_characters publish
    max_batch = 50

    @property
    def unavailable_reason(self) -> str:
        return ""

    def lookup(self, friends: list, token: str | None) -> list:
        """``friends``: [Friend]. Returns [Observation]. Raises RateLimited / ProviderError."""
        raise ProviderError("no presence service")

    def publish(self, status: OwnStatus, token: str | None) -> None:
        raise ProviderError("no presence service")


class UnavailablePresenceProvider(PresenceProvider):
    id = "unavailable"
    name = "OctoWoW"

    @property
    def unavailable_reason(self) -> str:
        return tr("OctoWoW offers no service that says whether a character is online, so friends are shown "
                  "with an unavailable status. Your friends list is kept on this computer.")


# ── the service ──────────────────────────────────────────────────────────────────────────

MIN_INTERVAL, DEFAULT_INTERVAL, MAX_INTERVAL = 30.0, 60.0, 900.0
BACKOFF_FIRST, BACKOFF_MAX = 30.0, 600.0


class PresenceService:
    """Polls a provider politely and caches what it says.

    Nothing is requested unless **all** of these hold: a real provider is configured, the
    social sidebar is enabled and open, the window is not minimised, there is at least one
    friend, and (if the provider needs an account) the player is signed in."""

    def __init__(self, book, provider: PresenceProvider, account=None, now=time.time, settings=None):
        self.book, self.provider, self.account, self.now = book, provider, account, now
        self.settings = settings
        self.on_change = lambda: None
        self.on_friend_online = lambda friend, obs: None
        self.cache: dict = {}                       # friend id -> Observation
        self.interval = DEFAULT_INTERVAL
        self.not_before = 0.0
        self.failures = 0
        self.rate_limited = False
        self.error = ""
        self.last_ok = 0.0
        self.sidebar_open = False
        self.minimized = False
        self.enabled = True
        self.requests = 0
        self.last_try = 0.0
        self._baseline = False                      # first successful poll sets the baseline; no "came online" flood

    # -- when to poll --------------------------------------------------------------------------
    def set_interval(self, seconds: float):
        self.interval = min(MAX_INTERVAL, max(MIN_INTERVAL, float(seconds)))

    @property
    def stale_after(self) -> float:
        return 3 * self.interval

    def active(self) -> bool:
        if not (self.provider.available and self.enabled and self.sidebar_open and not self.minimized):
            return False
        if not len(self.book):
            return False
        if self.provider.requires_account and not (self.account and self.account.state == "signed_in"):
            return False
        return True

    def delay(self) -> float | None:
        """Seconds until the next poll, or None while nothing may be requested."""
        if not self.active():
            return None
        now = self.now()
        if self.failures:
            due = self.last_try + min(BACKOFF_MAX, BACKOFF_FIRST * 2 ** (self.failures - 1))
        else:
            due = (self.last_ok + self.interval) if self.last_ok else now + 0.5
        return max(0.5, max(due, self.not_before) - now)

    # -- polling (blocking; runs on a worker) ------------------------------------------------------------------
    def poll(self) -> bool:
        """One round: every friend in batches. Returns whether anything changed."""
        if not self.active():
            return False
        self.last_try = self.now()
        token = None
        if self.provider.requires_account:
            token = self.account.valid_access()
            if token is None:
                return False
        friends = self.book.all()
        changed = False
        try:
            for i in range(0, len(friends), self.provider.max_batch):
                batch = friends[i:i + self.provider.max_batch]
                self.requests += 1
                for obs in self.provider.lookup(batch, token):
                    obs.clean()
                    if obs.friend_id in {f.id for f in batch}:
                        if obs.as_of is None:
                            obs.as_of = self.now()
                        changed |= self._store(obs)
        except RateLimited as e:
            self.rate_limited = True
            self.not_before = self.now() + min(e.retry_after, 3600)
            self.error = tr("The presence service asked EqUpdater to slow down.")
            self.on_change()
            return changed
        except ProviderError as e:
            self.failures += 1
            self.error = str(e) or tr("The presence service could not be reached.")
            self.on_change()
            return changed
        except Exception as e:                                       # noqa: BLE001 - a network error is not a crash
            self.failures += 1
            self.error = tr("The presence service could not be reached.")
            self.on_change()
            return changed
        self.failures, self.rate_limited, self.error = 0, False, ""
        self.last_ok = self.now()
        self._baseline = True
        self.on_change()
        return changed

    def _store(self, obs: Observation) -> bool:
        old = self.cache.get(obs.friend_id)
        before = resolve(old, self.now(), self.stale_after)[0] if old else UNKNOWN
        self.cache[obs.friend_id] = obs
        after = resolve(obs, self.now(), self.stale_after)[0]
        if self._baseline and after in (IN_GAME, LAUNCHER) and before in (OFFLINE, UNKNOWN):
            friend = self.book.get(obs.friend_id)
            if friend:
                self.on_friend_online(friend, obs)
        return (old is None) or (old.game, old.launcher, old.zone, old.level, old.guild) != (
            obs.game, obs.launcher, obs.zone, obs.level, obs.guild)

    # -- reading ---------------------------------------------------------------------------------
    def state_of(self, friend) -> tuple:
        return resolve(self.cache.get(friend.id), self.now(), self.stale_after)

    def observation(self, friend) -> Observation | None:
        return self.cache.get(friend.id)

    def forget(self, friend_id: str):
        self.cache.pop(friend_id, None)

    def status_text(self) -> str:
        """One line for the sidebar about the data source."""
        if not self.provider.available:
            return self.provider.unavailable_reason
        if self.rate_limited:
            return tr("The presence service asked EqUpdater to slow down; trying again soon.")
        if self.error:
            return self.error + " " + tr("Showing the last known information.")
        if not self.last_ok:
            return tr("Waiting for the first update.")
        return ""
