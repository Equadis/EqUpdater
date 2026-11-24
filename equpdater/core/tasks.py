"""Background task manager: bounded, de-duplicated, cancellable.

One place runs every expensive thing (network, scans, hashing) so the UI
thread never does, and so the same request is never made twice at once.

    tm = TaskManager(workers=4, deliver=ui_thread_call)
    tm.submit("news:2:listing", fetch, on_done=cb, on_error=eb)

* **Bounded**: a fixed pool; excess work queues, it does not spawn threads.
* **De-duplicated**: submitting a key that is already running attaches the new
  callbacks to the running job instead of starting another.
* **Cancellable**: the function may take a ``Cancel`` token as its first
  argument (``wants_cancel=True``); ``cancel(key)`` sets it and the result of
  a cancelled job is dropped.
* **Delivered on the caller's thread**: results go through ``deliver`` (the Qt
  front end passes a function that posts to the GUI thread). Tests pass none
  and get immediate delivery on the worker thread.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor


class Cancel:
    """A cooperative cancellation flag handed to long-running jobs."""

    def __init__(self):
        self._event = threading.Event()

    def set(self):
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self):
        if self._event.is_set():
            raise Cancelled()


class Cancelled(Exception):
    pass


class _Job:
    __slots__ = ("key", "cancel", "waiters", "started", "future", "done")

    def __init__(self, key):
        self.key = key
        self.cancel = Cancel()
        self.waiters = []            # (on_done, on_error)
        self.started = time.monotonic()
        self.future = None
        self.done = False


class TaskManager:
    def __init__(self, workers: int = 4, deliver=None):
        self._pool = ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="equ-task")
        self._deliver = deliver or (lambda fn: fn())
        self._lock = threading.Lock()
        self._jobs: dict = {}
        self.started_count = 0            # jobs actually run (tests, bench)
        self.deduped_count = 0

    # -- public ---------------------------------------------------------
    def submit(self, key, fn, *, on_done=None, on_error=None,
               wants_cancel: bool = False, timeout: float | None = None):
        """Run ``fn`` in the pool under ``key``. Returns True if a new job was
        started, False if it joined one already running."""
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and not job.done:
                job.waiters.append((on_done, on_error))
                self.deduped_count += 1
                return False
            job = _Job(key)
            job.waiters.append((on_done, on_error))
            self._jobs[key] = job
            self.started_count += 1
        job.future = self._pool.submit(self._run, job, fn, wants_cancel, timeout)
        return True

    def running(self, key) -> bool:
        with self._lock:
            job = self._jobs.get(key)
            return job is not None and not job.done

    def cancel(self, key) -> bool:
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.done:
                return False
            job.cancel.set()
            return True

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if not j.done)

    def shutdown(self):
        with self._lock:
            for job in self._jobs.values():
                job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- internals ------------------------------------------------------
    def _run(self, job: _Job, fn, wants_cancel: bool, timeout):
        result, error = None, None
        try:
            result = fn(job.cancel) if wants_cancel else fn()
        except BaseException as exc:         # delivered, never swallowed
            error = exc
        elapsed = time.monotonic() - job.started
        if timeout is not None and elapsed > timeout and error is None:
            error = TimeoutError(f"{job.key}: took {elapsed:.1f}s "
                                 f"(limit {timeout:g}s)")
        with self._lock:
            job.done = True
            waiters, job.waiters = job.waiters, []
            if self._jobs.get(job.key) is job:
                del self._jobs[job.key]
        if job.cancel.cancelled or isinstance(error, Cancelled):
            return                              # a cancelled job reports nothing
        for on_done, on_error in waiters:
            cb = (lambda r=result, f=on_done: f(r)) if error is None and on_done \
                else (lambda e=error, f=on_error: f(e)) if error is not None \
                and on_error else None
            if cb is not None:
                self._deliver(cb)
