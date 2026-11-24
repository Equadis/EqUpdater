"""Versioned JSON stores: atomic, debounced, corruption-tolerant.

3.0 keeps its own files under ``<app data>/v3/`` and never writes the 2.x
``config.json`` (migration reads it, copies forward, and leaves it alone).

* ``schema`` / ``migrations``: a store records ``_schema``; older files are
  walked forward by pure functions, and the pre-migration file is copied to
  ``<name>.v<N>.bak`` first.
* **Atomic**: temp file + ``os.replace`` (same guarantee as ``config``).
* **Debounced**: ``set`` marks dirty and one timer writes after ``delay``
  seconds, so a burst of changes is one write; ``flush()`` writes now and is
  registered with ``atexit``. No write at all when nothing changed.
* **Corrupt file**: kept as ``<name>.corrupt-<time>`` for inspection, defaults
  used. Never silently discarded.
"""

from __future__ import annotations

import atexit
import copy
import json
import os
import shutil
import threading
import time


class JsonStore:
    def __init__(self, path: str, defaults: dict, *, schema: int = 1,
                 migrations: dict | None = None, delay: float = 0.5):
        self.path = path
        self.schema = schema
        self.defaults = defaults
        self.migrations = migrations or {}     # {from_version: fn(dict) -> dict}
        self.delay = delay
        self._lock = threading.RLock()
        self._timer = None
        self._dirty = False
        self._listeners = []
        self.writes = 0                        # for tests and the bench
        self.data = self._load()
        atexit.register(self.flush)

    # -- loading --------------------------------------------------------
    def _load(self) -> dict:
        data = None
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            data = None
        except Exception:
            self._quarantine()
            data = None
        if data is None:
            out = copy.deepcopy(self.defaults)
            out["_schema"] = self.schema
            return out
        found = int(data.get("_schema", 1) or 1)
        if found < self.schema:
            self._backup(found)
            while found < self.schema:
                step = self.migrations.get(found)
                if step is not None:
                    data = step(data)
                found += 1
            data["_schema"] = self.schema
            self._dirty = True
        for k, v in self.defaults.items():
            data.setdefault(k, copy.deepcopy(v))
        return data

    def _quarantine(self):
        try:
            shutil.copyfile(self.path, f"{self.path}.corrupt-{int(time.time())}")
        except OSError:
            pass

    def _backup(self, version: int):
        try:
            shutil.copyfile(self.path, f"{self.path}.v{version}.bak")
        except OSError:
            pass

    # -- access ---------------------------------------------------------
    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self._lock:
            if self.data.get(key) == value and key in self.data:
                return                          # nothing changed: no write
            self.data[key] = value
            self._schedule()
        for fn in list(self._listeners):
            fn(key, value)

    def update(self, key, mutator):
        """Mutate a nested value under the lock (mutator edits in place)."""
        with self._lock:
            before = copy.deepcopy(self.data.get(key))
            cur = self.data.setdefault(key, {})
            mutator(cur)
            if cur == before:
                return
            self._schedule()

    def on_change(self, fn):
        self._listeners.append(fn)

    # -- writing --------------------------------------------------------
    def _schedule(self):
        self._dirty = True
        if self.delay <= 0:
            self.flush()
            return
        if self._timer is None:
            self._timer = threading.Timer(self.delay, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._dirty:
                return
            self._dirty = False
            text = json.dumps(self.data, indent=1, ensure_ascii=False)
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
                self.writes += 1
            except OSError:
                self._dirty = True              # try again next change
