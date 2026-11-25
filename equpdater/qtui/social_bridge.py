"""Qt bridges for the optional account and the social sidebar.

``AccountBridge`` is the header chip (signed out / signed in, status, sign-out);
``SocialBridge`` is the friends sidebar's model, its settings and its polling
timer. Both are thin: the rules live in ``core/account.py``, ``core/friends.py``,
``core/presence.py`` and ``core/social_service.py``. Nothing here blocks the GUI
thread: sign-in, session restore and presence polls run through the task manager.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from ..core import account as A
from ..core import presence as P
from ..core.friends import CLASSES, SORTS
from ..core.social_service import AUTO_AWAY_CHOICES, INTERVAL_CHOICES, STATUS_CHOICES, SocialService
from ..i18n import tr
from .models import KeyedListModel

_ROLES = ["key", "kind", "group", "title", "count", "id", "name", "realm", "realmName", "forum", "favourite",
          "state", "stateText", "stale", "dot", "charClass", "nameColour", "level", "guild", "zone", "detail"]


def _open_url(url: str) -> bool:
    from ..core.news_service import safe_url
    if safe_url(url):
        QDesktopServices.openUrl(QUrl(url))
        return True
    return False


class AccountBridge(QObject):
    changed = Signal()

    def __init__(self, service: A.AccountService, social: SocialService, tasks, dialog, dispatcher=None):
        super().__init__()
        self.service, self.social, self.tasks, self.dialog, self.dispatcher = service, social, tasks, dialog, dispatcher
        service.on_change = lambda: self._post(self.changed.emit)

    def _post(self, fn):
        if self.dispatcher is not None:
            self.dispatcher.post(fn)
        else:
            fn()

    # -- state -----------------------------------------------------------------------------------
    @Property(bool, notify=changed)
    def available(self):
        return self.service.provider.available

    @Property(str, notify=changed)
    def state(self):
        return self.service.state

    @Property(bool, notify=changed)
    def signedIn(self):
        return self.service.state == A.SIGNED_IN

    @Property(bool, notify=changed)
    def busy(self):
        return self.service.state == A.SIGNING_IN

    @Property(str, notify=changed)
    def username(self):
        return self.service.username

    @Property(str, notify=changed)
    def initial(self):
        return (self.service.username[:1] or "?").upper()

    @Property(str, notify=changed)
    def avatarUrl(self):
        """The avatar address, only if it is https on a host the sign-in service named."""
        url = self.service.avatar_url
        parts = QUrl(url)
        hosts = self.service.provider.avatar_hosts
        return url if url and parts.scheme() == "https" and parts.host() in hosts else ""

    @Property(str, notify=changed)
    def message(self):
        return self.service.message

    @Property(bool, notify=changed)
    def verified(self):
        return self.service.verified

    @Property(bool, notify=changed)
    def persists(self):
        return self.service.persists

    @Property(str, notify=changed)
    def unavailableReason(self):
        return self.service.provider.unavailable_reason

    @Property(str, notify=changed)
    def status(self):
        return self.social.status

    @Property(str, notify=changed)
    def statusDot(self):
        return {"online": P.COLOURS[P.LAUNCHER], "away": P.COLOURS[P.AWAY], "dnd": P.COLOURS[P.DND]}[self.social.status]

    @Property(str, notify=changed)
    def statusText(self):
        return {"online": tr("Online"), "away": tr("Away"), "dnd": tr("Do not disturb")}[self.social.status]

    # -- actions ----------------------------------------------------------------------------------
    @Slot()
    def signIn(self):
        if not self.service.provider.available:
            self.dialog.ask("info", tr("Sign in to OctoWoW"), self.service.provider.unavailable_reason)
            return

        def open_browser(url):
            self._post(lambda: _open_url(url) or None)
        self.tasks.submit("account:signin", lambda: self.service.sign_in(open_browser),
                          on_done=lambda ok: self._signed_in(ok), on_error=lambda e: self.changed.emit())

    def _signed_in(self, ok):
        self.changed.emit()
        if ok:
            self.social.publish()

    @Slot()
    def cancelSignIn(self):
        self.service.cancel_sign_in()

    @Slot()
    def signOut(self):
        self.tasks.submit("account:signout", self.service.sign_out, on_done=lambda _r: self.changed.emit(),
                          on_error=lambda e: self.changed.emit())

    @Slot()
    def createAccount(self):
        _open_url(A.SIGN_UP_URL)

    @Slot()
    def viewProfile(self):
        url = self.service.profile_url
        _open_url(url or "https://octowow.st/")

    @Slot(str)
    def setStatus(self, status):
        self.social.set_status(status)
        self.changed.emit()
        self.tasks.submit("social:publish", self.social.publish, on_done=lambda _r: None, on_error=lambda e: None)

    def restore(self):
        """At start, off the GUI thread."""
        if self.service.provider.available:
            self.tasks.submit("account:restore", self.service.restore, on_done=lambda _r: self.changed.emit(),
                              on_error=lambda e: self.changed.emit())


class SocialBridge(QObject):
    changed = Signal()
    settingsChanged = Signal()
    toast = Signal(str, str)

    def __init__(self, service: SocialService, tasks, realm_fetch=None):
        super().__init__()
        self.service, self.tasks = service, tasks
        self.lm = KeyedListModel(_ROLES)
        self._skin_expanded = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(lambda: service.window_idle(True))
        self._busy = False
        self._started = False
        service.on_change = self._refresh
        service.presence.on_change = self._refresh
        service.book.on_change = self._refresh
        service.realms.on_change = lambda: self.settingsChanged.emit()
        self._refresh()

    # -- model ---------------------------------------------------------------------------------------
    def _refresh(self):
        full = [{**{k: None for k in _ROLES}, **row} for row in self.service.rows()]
        self.lm.set_items(full)
        for title, text in self.service.take_toasts():
            self.toast.emit(title, text)
        self.changed.emit()

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    # -- state for the view -------------------------------------------------------------------------------
    @Property(bool, notify=settingsChanged)
    def enabled(self):
        return bool(self.service.get("social_enabled"))

    @Property(bool, notify=settingsChanged)
    def skinDefaultExpanded(self):
        return self._skin_expanded

    @skinDefaultExpanded.setter
    def skinDefaultExpanded(self, v):
        if bool(v) != self._skin_expanded:
            self._skin_expanded = bool(v)
            self.settingsChanged.emit()
            self._sidebar_changed()

    @Property(bool, notify=settingsChanged)
    def collapsed(self):
        saved = self.service.get("sidebar_collapsed")
        return (not self._skin_expanded) if saved is None else bool(saved)

    @Slot()
    def toggleCollapsed(self):
        self.service.set("sidebar_collapsed", not self.collapsed)
        self.settingsChanged.emit()
        self._sidebar_changed()

    def _sidebar_changed(self):
        self.service.presence.sidebar_open = self.enabled and not self.collapsed
        self._schedule(0.4 if self.service.presence.sidebar_open else None)
        self.changed.emit()

    @Property(str, notify=changed)
    def onlineText(self):
        n = self.service.online_count()
        return tr("{n} online", n=n)

    @Property(int, notify=changed)
    def friendCount(self):
        return len(self.service.book)

    @Property(str, notify=changed)
    def sourceNote(self):
        """Why statuses are unavailable, or how fresh they are."""
        return self.service.presence.status_text()

    @Property(bool, notify=changed)
    def presenceAvailable(self):
        return self.service.presence.provider.available

    @Property("QVariantList", notify=settingsChanged)
    def realms(self):
        return [{"id": r.id, "name": r.name, "kind": r.kind} for r in self.service.realms.all()]

    @Property(bool, notify=settingsChanged)
    def realmsAuthoritative(self):
        return self.service.realms.authoritative

    @Property(bool, constant=True)
    def accountFriends(self):
        return self.service.account_friends_supported()

    @Property(str, notify=settingsChanged)
    def sort(self):
        return self.service.get("friends_sort")

    @Property(str, notify=settingsChanged)
    def realmFilter(self):
        return self.service.get("friends_realm") or ""

    @Property("QVariantMap", notify=settingsChanged)
    def values(self):
        return {k: self.service.get(k) for k in ("social_enabled", "show_launcher_presence", "show_game_presence",
                                                  "show_character", "show_realm", "show_zone",
                                                  "friend_notifications", "auto_away_minutes", "default_status",
                                                  "presence_interval")}

    @Property("QVariantList", constant=True)
    def autoAwayChoices(self):
        return list(AUTO_AWAY_CHOICES)

    @Property("QVariantList", constant=True)
    def statusChoices(self):
        return list(STATUS_CHOICES)

    @Property("QVariantList", constant=True)
    def intervalChoices(self):
        return list(INTERVAL_CHOICES)

    # -- actions --------------------------------------------------------------------------------------------
    _add_open = False

    @Property(bool, notify=settingsChanged)
    def addOpen(self):
        return self._add_open

    @Slot()
    def openAdd(self):
        self._add_open = True
        self.settingsChanged.emit()

    @Slot()
    def closeAdd(self):
        self._add_open = False
        self.settingsChanged.emit()

    @Slot(str, "QVariant")
    def setSetting(self, key, value):
        try:
            self.service.set(key, value)
        except KeyError:
            return
        self.settingsChanged.emit()
        if key in ("social_enabled", "presence_interval"):
            self._sidebar_changed()
        if key == "auto_away_minutes":
            self.service.window_idle(False)

    @Slot(str)
    def setSearch(self, text):
        self.service.search = text or ""
        self._refresh()

    @Slot(str)
    def setSort(self, sort):
        if sort in SORTS:
            self.service.set("friends_sort", sort)
            self.settingsChanged.emit()
            self._refresh()

    @Slot(str)
    def setRealmFilter(self, realm):
        self.service.set("friends_realm", realm or "")
        self.settingsChanged.emit()
        self._refresh()

    @Slot(str, str, str, result=str)
    def addFriend(self, name, realm, forum):
        err = self.service.add_friend(name, realm, forum)
        if not err:
            self._schedule(0.4)
        return err

    @Slot(str)
    def removeFriend(self, friend_id):
        self.service.remove_friend(friend_id)

    @Slot(str)
    def toggleFavourite(self, friend_id):
        self.service.toggle_favourite(friend_id)

    @Slot(str, result=str)
    def copyText(self, friend_id):
        f = self.service.book.get(friend_id)
        return f.label if f else ""

    # -- polling -------------------------------------------------------------------------------------------------
    def start(self):
        if self._started:
            return
        self._started = True
        self.service.presence.sidebar_open = self.enabled and not self.collapsed
        if self.service.realms.due():
            self.tasks.submit("realms:refresh", self.service.realms.refresh, on_done=lambda _r: self.settingsChanged.emit(),
                              on_error=lambda e: None)
        self._schedule()

    def _schedule(self, delay=None):
        if not self._started:
            return
        if delay is None:
            delay = self.service.presence.delay()
        if delay is None:
            self._timer.stop()
            return
        self._timer.start(int(delay * 1000))

    def _tick(self):
        if self._busy or not self._started:
            return
        self._busy = True

        def done(_changed):
            self._busy = False
            self._refresh()
            self._schedule()
        self.tasks.submit("presence:poll", self.service.presence.poll, on_done=done, on_error=lambda e: done(False))

    def window_state(self, minimized: bool, active: bool):
        pres = self.service.presence
        was = pres.minimized
        pres.minimized = minimized
        mins = self.service.get("auto_away_minutes")
        if active and not minimized:
            self._idle.stop()
            self.service.window_idle(False)
        elif mins:
            self._idle.start(int(mins * 60 * 1000))
        if not self._started:
            return
        if minimized:
            self._timer.stop()
        elif was:
            self._schedule(0.5)
