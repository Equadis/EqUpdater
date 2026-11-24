"""The Python <-> QML boundary.

QML sees exactly these objects (context properties) and nothing else:

    app       shell state: tab, status line, version, what may animate
    skin      the resolved theme (read-only data)
    settings  skin choice and animation options
    news      two sections, each a list model plus a few actions
    launch    what PLAY starts, and PLAY

Each is a thin QObject over a Qt-free core service. They hold no business
rules: they translate signals/slots to service calls, and service events to
Qt model notifications. All run on the GUI thread; core results arrive there
through ``Dispatcher`` (queued signal), so no locking is needed anywhere.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import (QAbstractListModel, QByteArray, QModelIndex, QObject,
                            QUrl, Property, Qt, Signal, Slot)
from PySide6.QtGui import QDesktopServices, QFontDatabase

from .. import branding
from ..core import settings as core_settings
from ..core import skin as core_skin
from ..core.launch import LaunchService
from ..core.news_service import NewsService, safe_url, SECTIONS
from ..core.tasks import TaskManager
from ..i18n import tr

APP_VERSION_V3 = "3.0.0-dev"


class Dispatcher(QObject):
    """Run a callable on the GUI thread, from any thread."""
    _call = Signal(object)

    def __init__(self):
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()

    def post(self, fn):
        self._call.emit(fn)


# ── news ─────────────────────────────────────────────────────────────────────

_ROLES = ["id", "title", "author", "dateText", "url", "isNew", "expanded", "full",
          "hasBody", "loadingBody", "bodyHtml", "error", "clamp"]


class NewsListModel(QAbstractListModel):
    """Rows are post ids; row data is read from the service and cached per id
    until the service says that entry changed. Insertions are reported as
    insertions (a new post at the top, 'Load more' at the bottom) so the view
    keeps its scroll position and no delegate is rebuilt unnecessarily."""

    def __init__(self, service: NewsService, key: str):
        super().__init__()
        self.service, self.key = service, key
        self._ids: list = []
        self._cache: dict = {}
        self.data_calls = 0

    def roleNames(self):
        return {Qt.ItemDataRole.UserRole + 1 + i: QByteArray(n.encode())
                for i, n in enumerate(_ROLES)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._ids)

    def data(self, index, role):
        if not index.isValid() or index.row() >= len(self._ids):
            return None
        self.data_calls += 1
        iid = self._ids[index.row()]
        e = self._cache.get(iid)
        if e is None:
            e = self._cache[iid] = self.service.entry(self.key, iid)
        return e.get(_ROLES[role - Qt.ItemDataRole.UserRole - 1])

    # -- driven by the service listener ---------------------------------
    def sync_list(self):
        new = self.service.visible_ids(self.key)
        old = self._ids
        if new == old:
            return
        # old is a contiguous run of new -> pure insertions around it
        k = next((i for i in range(len(new) - len(old) + 1)
                  if new[i:i + len(old)] == old), None) if old else 0
        if k is None:
            self.beginResetModel()
            self._ids, self._cache = list(new), {}
            self.endResetModel()
            return
        if k > 0:
            self.beginInsertRows(QModelIndex(), 0, k - 1)
            self._ids = new[:k] + self._ids
            self.endInsertRows()
        tail = new[k + len(old):]
        if tail:
            at = len(self._ids)
            self.beginInsertRows(QModelIndex(), at, at + len(tail) - 1)
            self._ids = self._ids + tail
            self.endInsertRows()

    def entries_changed(self, ids):
        for iid in ids:
            self._cache.pop(iid, None)
            if iid in self._ids:
                row = self._ids.index(iid)
                ix = self.index(row)
                self.dataChanged.emit(ix, ix)


class NewsSectionBridge(QObject):
    stateChanged = Signal()

    def __init__(self, service: NewsService, key: str):
        super().__init__()
        self.service, self.key = service, key
        self.lm = NewsListModel(service, key)

    # properties -------------------------------------------------------
    @Property(str, constant=True)
    def id(self):
        return self.key

    @Property(str, constant=True)
    def title(self):
        return SECTIONS[self.key]["title"]

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    @Property(bool, notify=stateChanged)
    def loading(self):
        return self.service.sections[self.key].loading

    @Property(str, notify=stateChanged)
    def error(self):
        return self.service.sections[self.key].error

    @Property(str, notify=stateChanged)
    def note(self):
        return self.service.sections[self.key].note

    @Property(bool, notify=stateChanged)
    def hasMore(self):
        return self.service.has_more(self.key)

    # actions ----------------------------------------------------------
    @Slot()
    def refresh(self):
        self.service.refresh(self.key)

    @Slot()
    def loadMore(self):
        self.service.load_more(self.key)

    @Slot(str)
    def toggle(self, iid):
        self.service.toggle(self.key, iid)

    @Slot(str)
    def toggleFull(self, iid):
        self.service.toggle_full(self.key, iid)

    @Slot(str)
    def openUrl(self, url):
        if safe_url(url):
            QDesktopServices.openUrl(QUrl(url))



class NewsBridge(QObject):
    """Also the listener the NewsService reports to."""

    def __init__(self, store, tasks, fetcher, settings_store, now=time.time):
        super().__init__()
        self.service = NewsService(store, tasks, fetcher, self, now=now,
                                   seen_store=settings_store)
        self._sections = {k: NewsSectionBridge(self.service, k) for k in SECTIONS}
        for sec in self._sections.values():
            sec.lm.sync_list()

    @Property(QObject, constant=True)
    def announcements(self):
        return self._sections["announcements"]

    @Property(QObject, constant=True)
    def changelog(self):
        return self._sections["changelog"]

    def start(self):
        self.service.start()

    # -- NewsService listener ------------------------------------------
    def list_changed(self, key, first):
        self._sections[key].lm.sync_list()

    def entries_changed(self, key, ids):
        self._sections[key].lm.entries_changed(ids)

    def state_changed(self, key):
        sec = self._sections[key]
        sec.lm.sync_list()          # a page may have been shown/loaded
        sec.stateChanged.emit()


# ── launch ───────────────────────────────────────────────────────────────────

class LaunchBridge(QObject):
    changed = Signal()

    def __init__(self, service: LaunchService, tasks: TaskManager, app_bridge):
        super().__init__()
        self.service, self.tasks, self.app = service, tasks, app_bridge
        self._options, self._target = [], None

    @Property(str, notify=changed)
    def playText(self):
        return tr("PLAY")

    @Property(bool, notify=changed)
    def canPlay(self):
        return self._target is not None

    @Property(str, notify=changed)
    def heading(self):
        return (tr("Default – {file}", file=self._target) if self._target
                else tr("No launch files found"))

    @Property("QVariantList", notify=changed)
    def entries(self):
        return [{"label": n, "checked": n == self._target} for n in self._options]

    @Slot()
    def refresh(self):
        """Detection runs on a worker: a slow disk never blocks the window."""
        def work():
            opts = self.service.options()
            return opts, self.service.target(opts)

        def done(res):
            self._options, self._target = res
            self.changed.emit()
        self.tasks.submit("launch:options", work, on_done=done)

    @Slot(str)
    def chooseDefault(self, name):
        if self.service.choose_default(name):
            self._target = self.service.target(self._options)
            self.changed.emit()

    @Slot()
    def play(self):
        self.app.setStatus(tr("Launching…"), False)

        def done(res):
            self.app.setStatus(tr(res.message) if not res.ok else res.message,
                               not res.ok)
        self.tasks.submit("launch:start", self.service.launch, on_done=done)


# ── skins ────────────────────────────────────────────────────────────────────

class SkinBridge(QObject):
    themeChanged = Signal()

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self._fonts = set()
        self._register_fonts()

    @Property("QVariantMap", notify=themeChanged)
    def theme(self):
        return self.manager.current.theme

    @Property(str, notify=themeChanged)
    def id(self):
        return self.manager.current.id

    def refresh(self):
        self._register_fonts()
        self.themeChanged.emit()

    def _register_fonts(self):
        for path in self.manager.current.fonts:
            if path not in self._fonts:
                QFontDatabase.addApplicationFont(path)
                self._fonts.add(path)


class SkinManager:
    """Finds skins (official folder + the player's folder), keeps the current
    one, and falls back: chosen skin -> bubbles -> built-in flat theme."""

    DEFAULT = "bubbles"

    def __init__(self, official_dir: str, user_dir: str, settings):
        self.official_dir, self.user_dir, self.settings = official_dir, user_dir, settings
        self.skins: dict = {}
        self.scan()

    def scan(self):
        official = core_skin.discover(self.official_dir)
        for s in official.values():
            s.official = True
        found = dict(official)
        for sid, s in core_skin.discover(self.user_dir).items():
            if sid in official:
                s.problems.append(f"id '{sid}' is an official skin's; this copy "
                                  f"is ignored (change 'id' in manifest.json)")
                continue
            found[sid] = s
        self.skins = found
        wanted = self.settings.get("skin")
        self.current = self.skins.get(wanted) or self.skins.get(self.DEFAULT) \
            or core_skin.builtin_skin()

    def select(self, sid: str) -> bool:
        if sid not in self.skins:
            return False
        self.settings.set("skin", sid)
        self.current = self.skins[sid]
        return True

    def restore_default(self):
        self.settings.set("skin", self.DEFAULT)
        self.scan()


class SettingsBridge(QObject):
    skinsChanged = Signal()
    optionsChanged = Signal()

    def __init__(self, settings, manager: SkinManager, skin_bridge: SkinBridge,
                 app_bridge):
        super().__init__()
        self.s, self.manager, self.skin_bridge, self.app = settings, manager, skin_bridge, app_bridge

    @Property("QVariantList", notify=skinsChanged)
    def skins(self):
        return [{"id": k, "name": v.name, "author": v.author,
                 "problems": list(v.problems), "official": v.official}
                for k, v in sorted(self.manager.skins.items(),
                                   key=lambda kv: (not kv[1].official, kv[1].name.lower()))]

    @Property(str, notify=skinsChanged)
    def currentSkin(self):
        return self.manager.current.id

    @Property("QStringList", notify=skinsChanged)
    def skinProblems(self):
        return list(self.manager.current.problems)

    @Property(bool, notify=optionsChanged)
    def animatedBackground(self):
        return bool(self.s.get("animated_background"))

    @animatedBackground.setter
    def animatedBackground(self, v):
        self.s.set("animated_background", bool(v))
        self.optionsChanged.emit()
        self.app.animationChanged.emit()

    @Property(bool, notify=optionsChanged)
    def pauseWhenUnfocused(self):
        return bool(self.s.get("pause_when_unfocused"))

    @pauseWhenUnfocused.setter
    def pauseWhenUnfocused(self, v):
        self.s.set("pause_when_unfocused", bool(v))
        self.optionsChanged.emit()
        self.app.animationChanged.emit()

    @Slot(str)
    def setSkin(self, sid):
        t0 = time.perf_counter()
        if self.manager.select(sid):
            self.skin_bridge.refresh()
            self.skinsChanged.emit()
            self.app.lastSkinSwitchMs = (time.perf_counter() - t0) * 1000

    @Slot()
    def rescanSkins(self):
        self.manager.scan()
        self.skin_bridge.refresh()
        self.skinsChanged.emit()

    @Slot()
    def restoreDefaultSkin(self):
        self.manager.restore_default()
        self.skin_bridge.refresh()
        self.skinsChanged.emit()

    @Slot()
    def openSkinsFolder(self):
        os.makedirs(self.manager.user_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.manager.user_dir))


# ── shell ────────────────────────────────────────────────────────────────────

class AppBridge(QObject):
    tabChanged = Signal()
    statusChanged = Signal()
    animationChanged = Signal()
    countsChanged = Signal()
    screenshotRequested = Signal(str)     # QA/docs only (EQU3_SCREENSHOT)

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self._tab = "news"
        self._visited = ["news"]
        self._status, self._status_err = "", False
        self._minimized, self._active = False, True
        self.lastSkinSwitchMs = 0.0
        self.on_window_shown = None

    # -- strings --------------------------------------------------------
    @Slot(str, result=str)
    def t(self, text):
        return tr(text)

    # -- properties -----------------------------------------------------
    @Property(str, constant=True)
    def version(self):
        return APP_VERSION_V3

    @Property(str, notify=tabChanged)
    def tab(self):
        return self._tab

    @tab.setter
    def tab(self, v):
        if v != self._tab:
            self._tab = v
            if v not in self._visited:
                self._visited = self._visited + [v]
            self.tabChanged.emit()

    @Property("QStringList", notify=tabChanged)
    def visited(self):
        return list(self._visited)

    @Property(str, notify=statusChanged)
    def statusText(self):
        return self._status

    @Property(bool, notify=statusChanged)
    def statusIsError(self):
        return self._status_err

    @Property(bool, constant=True)
    def busy(self):
        return False

    @Property(int, notify=countsChanged)
    def modUpdates(self):
        return 0

    @Property(int, notify=countsChanged)
    def addonUpdates(self):
        return 0

    @Property(str, constant=True)
    def discordUrl(self):
        return branding.DISCORD_URL

    @Property(str, constant=True)
    def supportUrl(self):
        return "https://ko-fi.com/equadis"

    @Property(bool, notify=animationChanged)
    def animationsActive(self):
        """Nothing animates while minimised; when 'pause when unfocused' is on,
        nothing animates while another window has focus."""
        if self._minimized:
            return False
        return self._active or not self.s.get("pause_when_unfocused")

    # -- slots ----------------------------------------------------------
    @Slot(bool, bool)
    def setWindowState(self, minimized, active):
        if (minimized, active) != (self._minimized, self._active):
            self._minimized, self._active = minimized, active
            self.animationChanged.emit()

    @Slot()
    def windowShown(self):
        if self.on_window_shown:
            self.on_window_shown()

    @Slot(str)
    def openUrl(self, url):
        if safe_url(url):
            QDesktopServices.openUrl(QUrl(url))

    def setStatus(self, text, error=False):
        self._status, self._status_err = text, bool(error)
        self.statusChanged.emit()
