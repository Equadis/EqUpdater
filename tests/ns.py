"""Patching helpers for code that has moved out of ``equpdater.app``.

2.x tests wrote ``mock.patch.object(app, "load_config", fake)``. Once the
function that calls ``load_config`` lives in ``equpdater.core.mods`` it reads
*its own* global, so that patch silently stops working (the test would reach
the real config or network). ``patch_all`` patches the name in every namespace
that the target module's code actually runs in: the module itself and the
globals of each ``equpdater.core`` function it re-exports. That is what the old
single-namespace patch meant - and it follows the *target's* copy of the code,
so tests that reload the package keep working.

    with patch_all(app, "secure_urlopen", return_value=resp) as opener: ...
"""

import types
from unittest import mock

_UNSET = object()


def _namespaces(target, name):
    """The global dicts, starting with the target's own, that define ``name``
    and hold code that is part of the target."""
    spaces, seen = [], set()

    def add(d):
        if id(d) not in seen and name in d:
            seen.add(id(d))
            spaces.append(d)
    add(vars(target))
    for value in list(vars(target).values()):
        g = getattr(value, "__globals__", None)
        if g is not None and str(g.get("__name__", "")).startswith("equpdater.core"):
            add(g)
    return spaces


class _Multi:
    def __init__(self, spaces, name, new):
        self.spaces, self.name, self.new = spaces, name, new
        self.saved = []

    def start(self):
        for d in self.spaces:
            self.saved.append((d, d[self.name]))
            d[self.name] = self.new
        return self.new

    def stop(self):
        while self.saved:
            d, old = self.saved.pop()
            d[self.name] = old

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    def __call__(self, fn):          # decorator use
        import functools

        @functools.wraps(fn)
        def wrapper(*a, **k):
            with self:
                return fn(*a, **k)
        return wrapper


def patch_all(target, name=None, new=_UNSET, **kwargs):
    """Drop-in for ``mock.patch.object(target, name, new, **kw)``."""
    if not isinstance(target, types.ModuleType):
        # an instance or a class (the running Tk app, say): an ordinary patch
        args = () if new is _UNSET else (new,)
        return mock.patch.object(target, name, *args, **kwargs)
    special = {k: kwargs.pop(k) for k in ("create", "spec", "autospec",
                                          "spec_set", "new_callable") if k in kwargs}
    if special:
        args = () if new is _UNSET else (new,)
        return mock.patch.object(target, name, *args, **special, **kwargs)
    if new is _UNSET:
        new = mock.MagicMock(**kwargs)     # ONE mock, so call_args is seen
    spaces = _namespaces(target, name)
    if not spaces:
        raise AttributeError(f"{target.__name__} defines no {name!r}")
    return _Multi(spaces, name, new)
