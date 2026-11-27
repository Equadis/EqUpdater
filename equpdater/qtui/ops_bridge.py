"""Qt side of the Mods (and, next, Addons) pages: the host, the dialogs, models.

``QtOpsHost`` is the lifted logic's ``self``: its ``after`` posts to the GUI
thread, its repaint hooks become events, and its dialog hooks open the QML
dialog and wait in a nested event loop - the same modal behaviour the Tk
window had, without blocking painting or the workers.
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import (QCoreApplication, QEventLoop, QObject, QThread, QTimer,
                            Property, Signal, Slot)

from ..core.addons_service import AddonsService
from ..core.mods_service import ModsService
from ..core.diagnostics_service import DiagnosticsService, scan_lines
from ..core.custom_service import CustomSourceService
from .. import platforms
from ..core import install as I
from ..core.setup_service import SetupService
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

    @Slot(str)
    def openFolder(self, mod_id):
        """Mods are files in the game folder (DLLs, dlls.txt): that is the folder to show."""
        self.app.openFolder(self.host._game_path.get())

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
    def openFolder(self, folder):
        if folder and folder == os.path.basename(folder):             # a plain folder name, never a path
            self.app.openFolder(os.path.join(self.host._game_path.get(), "Interface", "AddOns", folder))

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
    def openFolder(self, file):
        self.app.openFolder(os.path.join(self.host._game_path.get(), "Data"))

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

    languageChanged = Signal()
    _lang_open = False

    @Property(bool, notify=languageChanged)
    def languagePromptOpen(self):
        return self._lang_open

    @Property("QVariantList", constant=True)
    def languageChoices(self):
        return self.service.language_choices()

    @Slot(result=bool)
    def promptLanguageIfNeeded(self):
        if self.service.needs_language_prompt():
            self._lang_open = True
            self.languageChanged.emit()
        return self._lang_open

    @Slot(str)
    def chooseLanguage(self, code):
        self._lang_open = False
        self.languageChanged.emit()
        self.service.choose_language(code)
        self.changed.emit()
        if self.afterLanguage:
            self.afterLanguage()

    afterLanguage = None

    @Property(bool, constant=True)
    def isWindows(self):
        return platforms.WINDOWS

    @Property(bool, constant=True)
    def hasLauncher(self):
        return self.service.launcher.available()

    @Property("QVariantMap", notify=changed)
    def launcher(self):
        return self.service.launcher.values()

    @Property(str, notify=changed)
    def runnerText(self):
        return self.service.launcher.runner_text()

    @Property(str, notify=changed)
    def envProblems(self):
        return self.service.launcher.env_problems(self.service.launcher.values()["env_vars"])

    @Slot(str, str)
    def setLauncher(self, key, value):
        self.service.launcher.set(key, value)
        self.changed.emit()

    @Slot(result=bool)
    def openLogsFolder(self):
        """Settings -> Troubleshooting: where crash reports are kept."""
        from ..core import crashlog
        d = crashlog.logs_dir()
        if not d or not os.path.isdir(d):
            return False
        try:
            platforms.open_folder(d)
        except OSError:
            return False
        return True

    @Property(int, notify=changed)
    def crashReports(self):
        from ..core import crashlog
        return len(crashlog.list_reports())

    @Slot(result=str)
    def excludeFromDefender(self):
        return self.service.exclude_from_defender()

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


_DIAG_ROLES = ["key", "id", "outcome", "outcomeText", "tone", "when"]


class DiagnosticsBridge(QObject):
    """Game Diagnostics, the folder scan ("Verify game files": read-only) and
    Login Doctor, in one overlay. Opening it never starts the game."""
    changed = Signal()
    openChanged = Signal()

    def __init__(self, service: DiagnosticsService, host: OpsHost, app_bridge):
        super().__init__()
        self.service, self.host, self.app = service, host, app_bridge
        self.lm = KeyedListModel(_DIAG_ROLES)
        self._open, self._view, self._selected = False, "attempt", None
        self._lines: list = []
        self._plan = None
        service.on_change = self._changed

    # -- state ---------------------------------------------------------------
    @Property(bool, notify=openChanged)
    def isOpen(self):
        return self._open

    @Property(str, notify=changed)
    def view(self):
        return self._view

    @Property(QObject, constant=True)
    def model(self):
        return self.lm

    @Property("QVariantList", notify=changed)
    def lines(self):
        return [{"text": t, "tone": tone} for t, tone in self._lines]

    @Property(str, notify=changed)
    def statusText(self):
        return self.service.status

    @Property(bool, notify=changed)
    def scanning(self):
        return self.service.scanning

    @Property(bool, notify=changed)
    def exporting(self):
        return self.service.exporting

    @Property(bool, notify=changed)
    def canLaunch(self):
        return self.host._btn_mode == "play"

    @Property(bool, notify=changed)
    def hasAttempts(self):
        return self.lm.rowCount() > 0

    @Property(bool, notify=changed)
    def hasScan(self):
        return self.service.latest_scan() is not None

    @Property(bool, notify=changed)
    def canRepair(self):
        return self.service.can_repair()

    @Property(str, constant=True)
    def priorityNote(self):
        return self.service.priority_note()

    @Property(str, notify=changed)
    def selectedId(self):
        return self._selected or ""

    def _changed(self):
        self.changed.emit()

    def _refresh_attempts(self):
        self.lm.set_items(self.service.attempts())
        if self._view == "attempt":
            ids = [self.lm.item_at(i)["id"] for i in range(self.lm.rowCount())]
            if self._selected not in ids:
                self._selected = ids[0] if ids else None
            self._lines = (self.service.attempt_detail(self._selected) if ids
                           else [(tr("No launch has been recorded yet."), "dim")])
        self.changed.emit()

    # -- opening ---------------------------------------------------------------
    @Slot(str)
    def openView(self, view):
        self._open, self._view = True, view
        self.openChanged.emit()
        if view == "doctor":
            self._lines = []
            self.service.doctor_run(self._doctor_changed)
        elif view == "scan":
            scan = self.service.latest_scan()
            self._lines = scan_lines(scan) if scan else [
                (tr("Verify game files"), "head"),
                (tr("Scanning reads every file in the game folder and compares it with the "
                    "client's manifest. Nothing is changed or downloaded."), "dim")]
            self.changed.emit()
        else:
            self._refresh_attempts()

    @Slot()
    def close(self):
        self._open = False
        self.openChanged.emit()

    def refresh_from_disk(self):
        """A watcher noticed a change in a recorded launch."""
        if self._open and self._view == "attempt":
            self._refresh_attempts()

    # -- launches --------------------------------------------------------------------
    @Slot(str)
    def select(self, attempt_id):
        self._selected = attempt_id
        self._view = "attempt"
        self._refresh_attempts()

    @Slot()
    def recheck(self):
        if self._selected:
            self.service.recheck(self._selected, self._refresh_attempts)

    @Slot()
    def launchWithDiagnostics(self):
        if not self.host.dialogs.ask_ok_cancel(tr("Launch with diagnostics"),
                                               self.service.diagnostic_note()):
            return
        self._selected = None
        self.service.launch_diagnostic()

    # -- scan --------------------------------------------------------------------------
    @Slot(bool, bool)
    def startScan(self, deep, include_mpq):
        self._view = "scan"
        if self.service.start_scan(deep, include_mpq, self._scan_done):
            self.changed.emit()

    def _scan_done(self, scan):
        self._view = "scan"
        self._lines = scan_lines(scan)
        self.changed.emit()

    @Slot()
    def cancelScan(self):
        self.service.cancel_scan()

    @Slot()
    def showScan(self):
        scan = self.service.latest_scan()
        if scan:
            self._view = "scan"
            self._lines = scan_lines(scan)
            self.changed.emit()

    # -- export ----------------------------------------------------------------------------
    @Slot(result=str)
    def exportDefaultName(self):
        return f"EqUpdater-support-{self._selected or 'report'}.zip"

    @Slot(result=bool)
    def confirmExport(self):
        if not self._selected:
            return False
        text, self._plan = self.service.export_preview(self._selected)
        return bool(self.host.dialogs.ask_ok_cancel(tr("Export support ZIP…"), text))

    @Slot(str)
    def exportTo(self, dest):
        if self._plan and dest:
            self.service.export(self._plan, dest, self._changed)

    # -- Login Doctor ---------------------------------------------------------------------------
    def _doctor_changed(self):
        self._lines = self.service.doctor_lines()
        self.changed.emit()

    @Slot()
    def doctorAgain(self):
        self._view = "doctor"
        self.service.doctor_run(self._doctor_changed)

    @Slot()
    def repairLogin(self):
        if self.service.doctor["running"] or not self.service.repair_plan():
            self.service.doctor_run(self._doctor_changed)
            return
        if not self.host.dialogs.ask_yes_no(tr("Repair login configuration"),
                                            self.service.repair_text()):
            return
        err = self.service.repair()
        if err:
            self.host.dialogs.error(tr("Repair login configuration"), err)
        self.service.doctor_run(self._doctor_changed)


class CustomBridge(QObject):
    """The "add custom git mod / addon" dialog. Nothing is installed until the
    player has confirmed the warning (a blocking question on the GUI thread)."""
    changed = Signal()
    openChanged = Signal()

    def __init__(self, service: CustomSourceService, host: OpsHost):
        super().__init__()
        self.service, self.host = service, host
        self._open, self._kind, self._choice, self._addon_msg = False, "mod", "", ("", "dim")
        service.on_change = self.changed.emit

    @Property(bool, notify=openChanged)
    def isOpen(self):
        return self._open

    @Property(str, notify=openChanged)
    def kind(self):
        return self._kind

    @Property(str, notify=changed)
    def statusText(self):
        return self.service.status[0] if self._kind == "mod" else self._addon_msg[0]

    @Property(str, notify=changed)
    def statusTone(self):
        return self.service.status[1] if self._kind == "mod" else self._addon_msg[1]

    @Property("QStringList", notify=changed)
    def choices(self):
        return list(self.service.choices)

    @Property(str, notify=changed)
    def choice(self):
        return self._choice

    @Property(bool, notify=changed)
    def found(self):
        return bool(self.service.src)

    @Property(bool, notify=changed)
    def busy(self):
        return self.service.busy

    @Property(str, constant=True)
    def hosts(self):
        return self.service.addon_hosts()

    @Slot(str)
    def open(self, kind):
        self.service.reset()
        self._kind, self._choice, self._addon_msg, self._open = kind, "", ("", "dim"), True
        self.openChanged.emit()
        self.changed.emit()

    @Slot()
    def close(self):
        self._open = False
        self.openChanged.emit()

    @Slot(str)
    def linkChanged(self, text):
        if self._kind == "addon":
            self._addon_msg = self.service.check_addon(text)
        else:
            self._choice = ""
            self.service.link_changed()
        self.changed.emit()

    @Slot(str)
    def choose(self, name):
        self._choice = name
        self.changed.emit()

    @Slot(str)
    def submit(self, link):
        """The one button: find the files, then install the chosen one."""
        if self._kind == "addon":
            err = self.service.install_addon(link)
            if err:
                self._addon_msg = (err, "err")
                self.changed.emit()
            else:
                self.close()
            return
        if not self.service.src:
            self.service.find(link)
            return
        name = self._choice or (self.service.choices[0] if self.service.choices else "")
        self.service.prepare(name, self._confirm)

    def _confirm(self, warning, plan):
        if not self.host.dialogs.ask_yes_no(tr("Install {name}?", name=plan["name"]), warning):
            return
        if self.service.add(plan):
            self.close()

    @Slot(str)
    def remove(self, mod_id):
        q = self.service.remove_text(mod_id)
        if q and self.host.dialogs.ask_yes_no(*q):
            self.service.remove(mod_id)


class SetupBridge(QObject):
    """"Find or add your game": folders already on this computer, or a ZIP/TAR
    the player downloaded. Never downloads the game."""
    changed = Signal()
    openChanged = Signal()

    def __init__(self, service: SetupService, app_bridge):
        super().__init__()
        self.service, self.app = service, app_bridge
        self._open, self._archive = False, ""
        service.on_change = self.changed.emit

    @Property(bool, notify=openChanged)
    def isOpen(self):
        return self._open

    @Property("QVariantList", notify=changed)
    def candidates(self):
        return [{**c, "sourceText": tr(I.SOURCE_NAMES.get(c["source"], c["source"])),
                 "stateText": tr(I.STATE_NAMES.get(c["state"], c["state"])),
                 "evidenceText": "; ".join(c["evidence"])}
                for c in self.service.candidates]

    @Property(str, notify=changed)
    def statusText(self):
        return self.service.status[0]

    @Property(str, notify=changed)
    def statusTone(self):
        return self.service.status[1]

    @Property(bool, notify=changed)
    def scanning(self):
        return self.service.scanning

    @Property(bool, notify=changed)
    def extracting(self):
        return self.service.extracting

    @Property(float, notify=changed)
    def progress(self):
        return self.service.progress

    @Property(str, notify=changed)
    def archive(self):
        return self._archive

    @Property(bool, notify=changed)
    def archiveOk(self):
        return bool(self._archive and self.service.plan)

    @Slot()
    def open(self):
        self._open = True
        self.openChanged.emit()
        self.service.scan()

    @Slot()
    def close(self):
        if self.service.extracting:
            return
        self.service.host.settings.set("setup_seen", True)
        self._open = False
        self.openChanged.emit()

    @Slot()
    def rescan(self):
        self.service.scan()

    @Slot(str)
    def use(self, path):
        if not self.service.use(path):
            self.close()

    @Slot(str)
    def pickArchive(self, path):
        self._archive = path
        self.service.inspect_archive(path)
        self.changed.emit()

    @Slot(str)
    def extractTo(self, dest):
        if self._archive and dest:
            self.service.extract(self._archive, dest, self._extracted)

    def _extracted(self, path):
        self.use(path)

    @Slot()
    def cancel(self):
        self.service.cancel()


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
