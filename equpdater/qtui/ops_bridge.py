"""Qt side of the Mods (and, next, Addons) pages: the host, the dialogs, models.

``QtOpsHost`` is the lifted logic's ``self``: its ``after`` posts to the GUI
thread, its repaint hooks become events, and its dialog hooks open the QML
dialog and wait in a nested event loop - the same modal behaviour the Tk
window had, without blocking painting or the workers.
"""

from __future__ import annotations

from PySide6.QtCore import (QEventLoop, QObject, QTimer, Property, Signal, Slot)

from ..core.mods_service import ModsService
from ..core.ops_host import OpsHost
from ..i18n import tr
from .models import KeyedListModel


class DialogBridge(QObject):
    """One modal question at a time. ``ask`` returns when the player answers."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._open = False
        self._title = self._body = ""
        self._kind = "yes_no"
        self._loop = None
        self._result = False
        self.auto = None            # tests: a callable(kind, title, body) -> bool

    @Property(bool, notify=changed)
    def open(self):
        return self._open

    @Property(str, notify=changed)
    def title(self):
        return self._title

    @Property(str, notify=changed)
    def body(self):
        return self._body

    @Property(str, notify=changed)
    def kind(self):
        return self._kind

    def ask(self, kind: str, title: str, body: str) -> bool:
        if self.auto is not None:
            return bool(self.auto(kind, title, body))
        self._kind, self._title, self._body, self._open = kind, title, body, True
        self.changed.emit()
        loop = self._loop = QEventLoop()
        loop.exec()
        self._loop = None
        return self._result

    @Slot(bool)
    def answer(self, ok):
        self._result = bool(ok)
        self._open = False
        self.changed.emit()
        if self._loop is not None:
            self._loop.quit()


class QtDialogs:
    def __init__(self, bridge: DialogBridge):
        self.b = bridge

    def ask_yes_no(self, title, body, **kw):
        return self.b.ask("yes_no", title, body)

    def ask_ok_cancel(self, title, body, **kw):
        return self.b.ask("ok_cancel", title, body)

    def info(self, title, body, **kw):
        self.b.ask("info", title, body)

    def error(self, title, body, **kw):
        self.b.ask("error", title, body)


_MOD_ROLES = ["key", "kind", "id", "section", "name", "essential", "description",
              "version", "installedGreen", "hasError", "problem",
              "problemSeverity", "actionText", "actionMode", "actionTip",
              "ignore", "enabled", "here", "managed", "custom", "repoUrl",
              "busy", "title", "count", "open", "manageAll"]


class ModsBridge(QObject):
    changed = Signal()
    badgeChanged = Signal()

    def __init__(self, service: ModsService, host: OpsHost, app_bridge):
        super().__init__()
        self.service, self.host, self.app = service, host, app_bridge
        self.lm = KeyedListModel(_MOD_ROLES)
        self._opened = False
        service.on_rows = self._rows

    def _rows(self, items):
        full = [{**{k: None for k in _MOD_ROLES}, **it} for it in items]
        self.lm.set_items(full)
        self.changed.emit()

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    @Property(int, notify=badgeChanged)
    def updates(self):
        return self.host._mod_updates_count

    @Property(bool, notify=changed)
    def hasGame(self):
        return bool(self.host._game_path.get())

    @Slot()
    def open(self):
        """First visit: show what is on disk now, then ask the sources."""
        if not self._opened:
            self._opened = True
            self.service.rebuild()
            self.service.refresh()

    @Slot()
    def refresh(self):
        self.service.refresh()

    @Slot(str)
    def toggleSection(self, title):
        self.service.toggle_section(title)

    @Slot(str, bool)
    def setIgnore(self, mod_id, value):
        self.service.set_ignore(mod_id, value)

    @Slot(str)
    def install(self, mod_id):
        self.service.install(mod_id)

    @Slot(str)
    def uninstall(self, mod_id):
        self.service.uninstall(mod_id)

    @Slot(str)
    def release(self, mod_id):
        self.service.release(mod_id)

    @Slot(str, str)
    def clickAction(self, mod_id, mode):
        self.service.click_action(mod_id, mode)

    @Slot(str)
    def forceReplace(self, mod_id):
        self.service.force_replace(mod_id)

    @Slot()
    def manageAll(self):
        self.service.manage_all()

    @Slot(str)
    def openUrl(self, url):
        self.app.openUrl(url)


def make_host(settings, dialogs_bridge: DialogBridge, app_bridge, dispatcher):
    """The OpsHost wired to Qt: GUI-thread scheduling, events, dialogs."""
    def schedule(ms, fn, *args):
        if fn is None:
            return
        call = (lambda: fn(*args))
        if ms and ms > 0:
            dispatcher.post(lambda: QTimer.singleShot(int(ms), call))
        else:
            dispatcher.post(call)

    holder = {}

    def events(name, **kw):
        h = holder.get("handler")
        if h:
            h(name, kw)

    host = OpsHost(settings, schedule=schedule, events=events,
                   dialogs=QtDialogs(dialogs_bridge))
    host._status_var.set = lambda v: app_bridge.setStatus(v, False)
    return host, holder
