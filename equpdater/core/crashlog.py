"""What a beta tester can send when something goes wrong, Qt-free.

* A Python error nobody caught (main thread, worker thread, or one that was
  being cleaned up) is written to ``<logs>/crash-<time>.txt`` with the version,
  the platform, the traceback and the end of the session log, and is also put
  in the session log (so it reaches the support ZIP of Settings -> Game
  Diagnostics, which removes the user name, folder paths and passwords).
* A native crash (Qt, a driver) cannot run Python, so ``faulthandler`` keeps a
  file open and the next start finds it, files it as a crash report and says so.
* The newest ``KEEP`` reports are kept; older ones are removed.
* Nothing is sent anywhere. The player decides who gets the file.
"""

from __future__ import annotations

import faulthandler
import os
import platform
import sys
import threading
import time
import traceback

from . import logsink

KEEP = 10
NATIVE = "native-crash.txt"

_state = {"dir": "", "version": "", "native": None, "previous": None}


def _redact(text: str) -> str:
    home = os.path.expanduser("~")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if home and home not in ("~", "/"):
        text = text.replace(home, "~")
    if len(user) >= 3:
        text = text.replace(user, "<user>")
    return text


def _prune(directory: str) -> None:
    try:
        files = sorted(f for f in os.listdir(directory) if f.startswith("crash-") and f.endswith(".txt"))
    except OSError:
        return
    for name in files[:-KEEP]:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


def write_report(kind: str, body: str, directory: str | None = None, now=time.time) -> str:
    """Write one report and return its path ('' if it could not be written)."""
    directory = directory or _state["dir"]
    if not directory:
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
    path = os.path.join(directory, f"crash-{stamp}-{kind}.txt")
    tail = "\n".join(f"{t}  {m}" for t, m, _g in list(logsink.recent)[-100:])
    text = (f"EqUpdater {_state['version']} ({kind})\n"
            f"{platform.platform()}  Python {platform.python_version()}  frozen={getattr(sys, 'frozen', False)}\n"
            f"time {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now()))}\n\n"
            f"{body.rstrip()}\n\n--- end of the session log ---\n{tail}\n")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_redact(text))
    except OSError:
        return ""
    _prune(directory)
    return path


def _format(exc_type, exc, tb) -> str:
    return "".join(traceback.format_exception(exc_type, exc, tb))


def _on_exception(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    body = _format(exc_type, exc, tb)
    path = write_report("python", body)
    logsink.log("An unexpected error occurred: " + _redact(body.strip().splitlines()[-1])
                + (f"  (report: {os.path.basename(path)})" if path else ""), "err")


def _on_thread_exception(args):
    if args.exc_type is SystemExit:
        return
    body = f"in thread {args.thread.name if args.thread else '?'}:\n" + _format(
        args.exc_type, args.exc_value, args.exc_traceback)
    path = write_report("thread", body)
    logsink.log("An unexpected error occurred in a background task: "
                + _redact(body.strip().splitlines()[-1])
                + (f"  (report: {os.path.basename(path)})" if path else ""), "err")


def install(directory: str, version: str) -> str | None:
    """Hook the error paths. Returns the path of a native-crash report left by
    the previous run (now filed), or None."""
    _state.update(dir=directory, version=version)
    previous = None
    try:
        os.makedirs(directory, exist_ok=True)
        native = os.path.join(directory, NATIVE)
        if os.path.isfile(native) and os.path.getsize(native) > 0:
            with open(native, encoding="utf-8", errors="replace") as f:
                previous = write_report("native", "The program stopped without a Python error "
                                        "(a crash in Qt or a driver). faulthandler wrote:\n\n" + f.read())
        if _state["native"] is not None:
            try:
                _state["native"].close()
            except OSError:
                pass
        _state["native"] = open(native, "w", encoding="utf-8")
        faulthandler.enable(file=_state["native"], all_threads=True)
    except OSError:
        pass
    sys.excepthook = _on_exception
    threading.excepthook = _on_thread_exception
    _state["previous"] = previous or None
    if previous:
        logsink.log("EqUpdater stopped unexpectedly last time. A report was saved: "
                    + os.path.basename(previous), "err")
    return previous or None


def previous_crash() -> str | None:
    return _state["previous"]


def logs_dir() -> str:
    return _state["dir"]


def list_reports(directory: str | None = None) -> list:
    directory = directory or _state["dir"]
    try:
        return sorted(f for f in os.listdir(directory) if f.startswith("crash-"))
    except OSError:
        return []


def clean_exit() -> None:
    """Called at a normal shutdown: the native-crash file is emptied, so the
    next start does not mistake a clean exit for a crash."""
    f = _state.get("native")
    if f is not None:
        try:
            faulthandler.disable()
            f.close()
            with open(os.path.join(_state["dir"], NATIVE), "w", encoding="utf-8"):
                pass
        except OSError:
            pass
        _state["native"] = None
