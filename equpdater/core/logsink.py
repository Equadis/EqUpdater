"""One place core modules write their log lines.

2.x's ``app.log`` feeds the session log window; the Qt front end will install
its own sink. Until a sink is set, lines are kept in memory so nothing is lost
or raises. Thread-safe (``list.append`` and the callable's own rules)."""

from __future__ import annotations

_sink = None
_buffer: list = []


def set_sink(fn) -> None:
    """Install the sink and flush anything logged before it existed."""
    global _sink
    _sink = fn
    while _buffer:
        msg, tag = _buffer.pop(0)
        fn(msg, tag)


def log(msg: str, tag: str = "") -> None:
    if _sink is None:
        _buffer.append((msg, tag))
        del _buffer[:-500]
    else:
        _sink(msg, tag)
