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
from ..core.social_service import SocialService
from ..i18n import tr
from .models import KeyedListModel

_ROLES = ["key", "kind", "group", "title", "count", "id", "name", "favourite", "state", "stale", "dot", "statusLine", "statusName", "status", "realm"]


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
        return {"online": P.COLOURS[P.LAUNCHER], "away": P.COLOURS[P.AWAY], "dnd": P.COLOURS[P.DND],
                "invisible": P.COLOURS[P.OFFLINE]}[self.social.status]

    @Property(str, notify=changed)
    def statusText(self):
        return {"online": tr("Online"), "away": tr("Away"), "dnd": tr("Do Not Disturb"),
                "invisible": tr("Invisible")}[self.social.status]

    @Property("QVariantList", constant=True)
    def statusChoices(self):
        """The four choices of the presence menu, with their dot colours."""
        return [{"id": "online", "label": tr("Online"), "dot": P.COLOURS[P.LAUNCHER]},
                {"id": "away", "label": tr("Away"), "dot": P.COLOURS[P.AWAY]},
                {"id": "dnd", "label": tr("Do Not Disturb"), "dot": P.COLOURS[P.DND]},
                {"id": "invisible", "label": tr("Invisible"), "dot": P.COLOURS[P.OFFLINE]}]

    # -- actions ----------------------------------------------------------------------------------
    @Slot()
    def signIn(self):
        if self.usernameLogin:
            self.openLogin()
            return
        if not self.service.provider.available:
            self.dialog.ask("info", tr("Sign in to OctoWoW"), self.service.provider.unavailable_reason)
            return

        def open_browser(url):
            self._post(lambda: _open_url(url) or None)
        self.tasks.submit("account:signin", lambda: self.service.sign_in(open_browser),
                          on_done=lambda ok: self._signed_in(ok), on_error=lambda e: self.changed.emit())

    def _publish_later(self):
        self.tasks.submit("social:publish", self.social.publish, on_done=lambda _r: None, on_error=lambda e: None)

    def _signed_in(self, ok):
        self.changed.emit()
        if ok:
            self._publish_later()

    @Property(bool, constant=True)
    def usernameLogin(self):
        """The temporary test login: a username instead of a browser sign-in."""
        return bool(getattr(self.service.provider, "username_login", False))

    @Property(bool, notify=changed)
    def loginOpen(self):
        return self._login_open

    _login_open = False
    _requires_key = False
    _recovery = ""                       # a recovery code being shown (a new account's, or the current one on request)

    @Property(bool, notify=changed)
    def requiresKey(self):
        """The test backend wants a tester key to create an account (asked once, in the background)."""
        return self._requires_key

    @Property(str, notify=changed)
    def recoveryCode(self):
        return self._recovery

    @Slot()
    def openLogin(self):
        self._login_open = True
        self._recovery = ""
        self.changed.emit()
        probe = getattr(self.service.provider, "requires_key", None)
        if probe:
            def done(need):
                self._requires_key = bool(need)
                self.changed.emit()
            self.tasks.submit("account:info", probe, on_done=done, on_error=lambda e: None)

    @Slot()
    def closeLogin(self):
        self._login_open = False
        self._recovery = ""
        self.changed.emit()

    @Slot()
    def dismissRecovery(self):
        self.closeLogin()

    @Slot()
    def showRecovery(self):
        """Show this account's recovery code again (kept in the credential manager on this computer)."""
        vault = getattr(self.service.provider, "vault", None)
        code = vault.get("recovery", self.service.username) if vault else None
        if code:
            self._recovery, self._login_open = code, True
            self.changed.emit()

    @Slot(str, str, str)
    def signInAs(self, username, key="", recovery=""):
        """Test login. The answer (a refusal, say a taken name) arrives as ``message``; the dialog stays open then."""
        prov = self.service.provider
        prov.username, prov.key, prov.recovery = (username or "").strip(), (key or "").strip(), (recovery or "").strip()

        def done(ok):
            self.changed.emit()
            if ok:
                code = getattr(prov, "new_recovery", "")
                if code:
                    self._recovery = code                   # a new account: show its recovery code before closing
                else:
                    self._login_open = False
                self.changed.emit()
                self._publish_later()
        if self.service.state == A.SIGNED_IN:                      # switching accounts: end this session first
            def work():
                self.service.sign_out()
                self.social.presence.cache.clear()
                return self.service.sign_in(lambda url: None)
        else:
            def work():
                return self.service.sign_in(lambda url: None)
        self.tasks.submit("account:signin", work, on_done=done, on_error=lambda e: self.changed.emit())

    @Slot()
    def cancelSignIn(self):
        self.service.cancel_sign_in()

    @Slot()
    def signOut(self):
        def done(_r):
            self.social.presence.cache.clear()              # what was known about friends belonged to that session
            self.social.presence.failures, self.social.presence.error = 0, ""
            self.social.on_change()
            self.changed.emit()
        self.tasks.submit("account:signout", self.service.sign_out, on_done=done,
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

    dev_login = ""                  # development only (EQU3_DEV_LOGIN): sign in under this name if no session came back

    def restore(self):
        """At start, off the GUI thread."""
        def done(_r):
            self.changed.emit()
            if self.dev_login and self.usernameLogin and self.service.state != A.SIGNED_IN:
                self.signInAs(self.dev_login)
        if self.service.provider.available:
            self.tasks.submit("account:restore", self.service.restore, on_done=done,
                              on_error=lambda e: self.changed.emit())


class SocialBridge(QObject):
    changed = Signal()
    settingsChanged = Signal()
    refreshRequested = Signal()          # emitted from any thread; handled on the GUI thread
    addFinished = Signal(str)            # "" or the reason a friend was not added

    def __init__(self, service: SocialService, tasks, realm_fetch=None):
        super().__init__()
        self.service, self.tasks = service, tasks
        self.lm = KeyedListModel(_ROLES)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._idle = QTimer(self)              # the 15-minute Away rule: checked twice a minute, not per event
        self._idle.setInterval(30_000)
        self._idle.timeout.connect(self._check_idle)
        self._busy = False
        self._started = False
        self.refreshRequested.connect(self._refresh)
        service.on_change = self.refreshRequested.emit
        service.presence.on_change = self.refreshRequested.emit
        service.book.on_change = self.refreshRequested.emit
        service.presence.before_poll = service.refresh_activity
        if service.realms:
            service.realms.on_change = lambda: self.settingsChanged.emit()
        self._refresh()

    # -- model ---------------------------------------------------------------------------------------
    def _refresh(self):
        """A social failure must never reach the launcher: keep what is shown and carry on."""
        try:
            full = [{**{k: None for k in _ROLES}, **row} for row in self.service.rows()]
            self.lm.set_items(full)
        except Exception as e:                                          # noqa: BLE001
            from ..core.logsink import log
            log(f"Friends list could not be refreshed: {e}", "dim")
        self.changed.emit()

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    # -- state for the view -------------------------------------------------------------------------------
    @Property(str, notify=changed)
    def onlineText(self):
        n = self.service.online_count()
        return tr("{n} online", n=n)

    @Property(int, notify=changed)
    def friendCount(self):
        return self.service.friend_count()

    @Property(str, notify=changed)
    def sourceNote(self):
        """Why statuses are unavailable, or how fresh they are."""
        return self.service.presence.status_text()

    @Property(bool, notify=changed)
    def presenceAvailable(self):
        return self.service.presence.provider.available

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

    @Slot(str)
    def setSearch(self, text):
        self.service.search = text or ""
        self._refresh()

    @Slot(str, result=str)
    def addFriend(self, username):
        """Blocking when the service keeps the list: the interface uses addFriendAsync."""
        err = self.service.add_friend(username)
        if not err:
            self._schedule(0.4)
        return err

    @Slot(str)
    def addFriendAsync(self, username):
        def done(err):
            self.addFinished.emit(err)
            if not err:
                self._schedule(0.2)
        self.tasks.submit("social:add", lambda: self.service.add_friend(username), on_done=done,
                          on_error=lambda e: self.addFinished.emit(tr("The test service could not be reached.")))

    def _edit(self, key, fn):
        """Remove / favourite: instant for a local list, a worker when the service holds the list."""
        if not self.service._held():
            fn()
            return
        self.tasks.submit(key, fn, on_done=lambda err: (self._refresh(), self._schedule(0.2)),
                          on_error=lambda e: None)

    @Slot(str)
    def removeFriend(self, friend_id):
        self._edit("social:remove", lambda: self.service.remove_friend(friend_id))

    @Slot(str)
    def toggleFavourite(self, friend_id):
        self._edit("social:favourite", lambda: self.service.toggle_favourite(friend_id))

    @Slot(str, result=str)
    def copyText(self, friend_id):
        f = self.service.book.get(friend_id)
        return f.label if f else ""

    # -- polling -------------------------------------------------------------------------------------------------
    def start(self):
        if self._started:
            return
        self._started = True
        self.service.sync_link()
        self._idle.start()
        self.service.note_activity()
        if self.service.realms and self.service.realms.due():
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

    @Slot()
    def kick(self):
        """Something changed that decides whether presence flows (sign-in, sign-out): look again."""
        self.service.sync_link()
        if self._started and not self._busy:
            self._schedule(0.3 if self.service.presence.delay() is not None else None)

    def window_state(self, minimized: bool, active: bool):
        pres = self.service.presence
        was = pres.minimized
        pres.minimized = minimized
        if not self._started:
            return
        if minimized:
            self._timer.stop()
        elif was:
            self._schedule(0.5)

    # -- the 15-minute Away rule -----------------------------------------------------------------------------
    _INPUT = None

    def eventFilter(self, obj, event):
        """Any key, click, wheel or mouse movement is activity. Only a timestamp is stored."""
        if self._INPUT is None:
            from PySide6.QtCore import QEvent
            SocialBridge._INPUT = {QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
                                   QEvent.Type.Wheel, QEvent.Type.TouchBegin}
        if event.type() in self._INPUT:
            self.service.note_activity()
        return False

    def _check_idle(self):
        try:
            if self.service.check_idle():
                self.changed.emit()
                self.tasks.submit("social:publish", self.service.publish, on_done=lambda _r: None, on_error=lambda e: None)
        except Exception:                                              # noqa: BLE001 - never reaches the launcher
            pass
