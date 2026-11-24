"""Qt side of the Mods (and, next, Addons) pages: the host, the dialogs, models.

``QtOpsHost`` is the lifted logic's ``self``: its ``after`` posts to the GUI
thread, its repaint hooks become events, and its dialog hooks open the QML
dialog and wait in a nested event loop - the same modal behaviour the Tk
window had, without blocking painting or the workers.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import (QCoreApplication, QEventLoop, QObject, QThread, QTimer,
                            Property, Signal, Slot)

from ..core.addons_service import AddonsService
from ..core.mods_service import ModsService
from ..core.mpq_service import MpqService
from ..core.settings_service import GeneralService
from ..core.ops_host import OpsHost
from ..i18n import tr
from .models import KeyedListModel


class DialogBridge(QObject):
    """One modal question at a time. ``ask`` returns when the player answers."""
    changed = Signal()

    def __init__(self, dispatcher=None):
        super().__init__()
        self.dispatcher = dispatcher
        self._gui = QThread.currentThread()
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

    def ask(self, kind: str, title: str, body: str):
        """Ask on the GUI thread (a worker thread posts the question there
        and waits for the answer), blocking the caller until it is answered."""
        if QThread.currentThread() is not self._gui and self.dispatcher is not None:
            done, box = threading.Event(), []

            def run():
                try:
                    box.append(self._ask_here(kind, title, body))
                finally:
                    done.set()
            self.dispatcher.post(run)
            done.wait()
            return box[0] if box else False
        return self._ask_here(kind, title, body)

    def _ask_here(self, kind: str, title: str, body: str):
        if self.auto is not None:
            return self.auto(kind, title, body)
        self._kind, self._title, self._body, self._open = kind, title, body, True
        self.changed.emit()
        loop = self._loop = QEventLoop()
        loop.exec()
        self._loop = None
        return self._result

    @Slot()
    def cancel(self):
        """The third answer of a yes/no/cancel question: None."""
        self._result = None
        self._open = False
        self.changed.emit()
        if self._loop is not None:
            self._loop.quit()

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

    def ask_yes_no_cancel(self, title, body, **kw):
        return self.b.ask("yes_no_cancel", title, body)

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


_ADDON_ROLES = ["key", "kind", "id", "section", "titleHtml", "description",
                "recommended", "installed", "canRelease", "switch", "switchTip",
                "statusKind", "statusText", "statusAction", "statusTone",
                "statusTip", "offersReplace", "problem", "problemSeverity",
                "repoUrl", "title", "count", "open"]


class AddonsBridge(QObject):
    changed = Signal()
    footerChanged = Signal()
    badgeChanged = Signal()

    def __init__(self, service: AddonsService, host: OpsHost, app_bridge):
        super().__init__()
        self.service, self.host, self.app = service, host, app_bridge
        self.lm = KeyedListModel(_ADDON_ROLES)
        self._footer = ("", "info")
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)       # debounce typing
        self._filter_timer.timeout.connect(self._apply_filter)
        self._pending_filter = ""
        service.on_rows = self._rows
        service.on_footer = self._set_footer

    def _rows(self, items):
        self.lm.set_items([{**{k: None for k in _ADDON_ROLES}, **it} for it in items])
        self.changed.emit()

    def _set_footer(self, text, mode):
        if (text, mode) != self._footer:
            self._footer = (text, mode)
            self.footerChanged.emit()

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    @Property(str, notify=footerChanged)
    def footerText(self):
        return self._footer[0]

    @Property(str, notify=footerChanged)
    def footerMode(self):
        return self._footer[1]

    @Property(int, notify=badgeChanged)
    def updates(self):
        return self.host._addon_updates_count

    @Slot()
    def open(self):
        self.service.open()

    @Slot(str)
    def setFilter(self, text):
        self._pending_filter = text
        self._filter_timer.start()

    def _apply_filter(self):
        self.service.set_filter(self._pending_filter)

    @Slot()
    def recheck(self):
        self.service.verify(True)

    @Slot(str)
    def toggleSection(self, title):
        self.service.toggle_section(title)

    @Slot(str, str)
    def clickStatus(self, folder, action):
        self.service.click_status(folder, action)

    @Slot(str)
    def install(self, folder):
        self.service.install(folder)

    @Slot(str)
    def remove(self, folder):
        self.service.remove(folder)

    @Slot(str)
    def release(self, folder):
        self.service.release(folder)

    @Slot(str)
    def switchSource(self, folder):
        self.service.switch_source(folder)

    @Slot(str)
    def forceReplace(self, folder):
        self.service.force_replace(folder)

    @Slot()
    def updateAll(self):
        self.service.update_all()

    @Slot(str)
    def openUrl(self, url):
        self.app.openUrl(url)


_MPQ_ROLES = ["key", "kind", "section", "file", "name", "showFile", "description",
              "installed", "linked", "busy", "action", "actionText", "stateText",
              "stateTone", "stateTip", "offersReplace", "sourceText", "canLink",
              "linkTip", "author", "homepage", "title", "count", "open"]


class MpqBridge(QObject):
    changed = Signal()
    linkChanged = Signal()
    badgeChanged = Signal()

    def __init__(self, service: MpqService, host: OpsHost, app_bridge):
        super().__init__()
        self.service, self.host, self.app = service, host, app_bridge
        self.lm = KeyedListModel(_MPQ_ROLES)
        self._link_for = ""
        service.on_rows = self._rows

    def _rows(self, items):
        self.lm.set_items([{**{k: None for k in _MPQ_ROLES}, **it} for it in items])
        self.changed.emit()

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    @Property(int, notify=badgeChanged)
    def updates(self):
        return self.host._mpq_updates_count

    @Property(bool, notify=changed)
    def hasGame(self):
        return bool(self.host._game_path.get())

    @Property(bool, notify=changed)
    def ignoreSpeech(self):
        return self.service.ignore_speech()

    @ignoreSpeech.setter
    def ignoreSpeech(self, v):
        self.service.set_ignore_speech(v)
        self.changed.emit()

    @Property(str, notify=linkChanged)
    def linkFor(self):
        return self._link_for

    @Property("QVariantList", constant=True)
    def catalogue(self):
        return self.service.catalogue()

    @Slot()
    def open(self):
        self.service.open()

    @Slot()
    def recheck(self):
        self.service.check()

    @Slot(str)
    def toggleSection(self, title):
        self.service.toggle_section(title)

    @Slot(str, str)
    def click(self, file, action):
        self.service.click(file, action)

    @Slot(str)
    def replace(self, file):
        self.service.replace(file)

    @Slot(str)
    def uninstall(self, file):
        self.service.uninstall(file)

    @Slot(str)
    def unlink(self, file):
        self.service.unlink(file)

    @Slot(str)
    def openLink(self, file):
        self._link_for = file
        self.linkChanged.emit()

    @Slot()
    def closeLink(self):
        self._link_for = ""
        self.linkChanged.emit()

    @Slot(str, str, result=str)
    def link(self, choice, url):
        err = self.service.link(self._link_for, choice, url)
        if not err:
            self.closeLink()
        return err

    @Slot(str)
    def openUrl(self, url):
        self.app.openUrl(url)


class GeneralBridge(QObject):
    """Settings page: game folder, launch/auto-install toggles, font, language,
    session log. Skin and animation options stay on ``settings``."""
    changed = Signal()
    logChanged = Signal()

    def __init__(self, service: GeneralService, app_bridge, skin_bridge):
        super().__init__()
        self.service, self.app, self.skin = service, app_bridge, skin_bridge

    def _toggle(name, sig=changed):
        def getter(self):
            return self.service.get(name)

        def setter(self, v):
            self.service.set(name, v)
            self.changed.emit()
        return Property(bool, getter, setter, notify=sig)

    clearWdb = _toggle("clearWdb")
    closeOnLaunch = _toggle("closeOnLaunch")
    autoMods = _toggle("autoMods")
    autoAddons = _toggle("autoAddons")
    del _toggle

    @Property(str, notify=changed)
    def gameDir(self):
        return self.service.game_dir()

    @Property(str, notify=changed)
    def language(self):
        return self.service.language_label()

    @Property(str, notify=changed)
    def fontOverride(self):
        return self.service.font_override()

    @Property("QVariantList", constant=True)
    def fontChoices(self):
        return self.service.font_choices()

    @Property(str, notify=logChanged)
    def sessionLog(self):
        return self.service.session_log()

    @Slot(str)
    def setFontOverride(self, choice):
        self.service.set_font_override(choice)
        self.skin.refresh()
        self.changed.emit()

    @Slot(str, result=str)
    def changeGameDir(self, path):
        err = self.service.change_game_dir(path)
        self.changed.emit()
        return err

    @Slot()
    def openGameDir(self):
        self.service.open_game_dir()

    @Slot()
    def refreshLog(self):
        self.logChanged.emit()

    @Slot()
    def closed(self):
        self.service.close()


def make_host(settings, dialogs_bridge: DialogBridge, app_bridge, dispatcher):
    """The OpsHost wired to Qt: GUI-thread scheduling, events, dialogs.

    Everything that touches a Qt object runs on the GUI thread: events and
    status text raised from a worker are posted there, in order."""
    def schedule(ms, fn, *args):
        if fn is None:
            return
        call = (lambda: fn(*args))
        if ms and ms > 0:
            dispatcher.post(lambda: QTimer.singleShot(int(ms), call))
        else:
            dispatcher.post(call)

    holder = {}

    gui = QCoreApplication.instance().thread()

    def events(name, **kw):
        h = holder.get("handler")
        if not h:
            return
        if QThread.currentThread() is gui:
            h(name, kw)
        else:
            dispatcher.post(lambda: h(name, kw))

    host = OpsHost(settings, schedule=schedule, events=events,
                   dialogs=QtDialogs(dialogs_bridge))
    def set_status(v):
        if QThread.currentThread() is gui:
            app_bridge.setStatus(v, False)
        else:
            dispatcher.post(lambda: app_bridge.setStatus(v, False))
    host._status_var.set = set_status
    return host, holder
