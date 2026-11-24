"""Write a file whole or not at all.

Everything that replaces a file the game or the player may depend on goes
through here: the new content is written to a temporary file in the same
folder, flushed to disk, and only then moved over the old file. An interrupted
write, a full disk or a crash leaves the previous file exactly as it was, and
never a truncated one. (Same-folder temp + ``os.replace`` is atomic on
Windows and POSIX.)

``open_text`` keeps the caller's mode and encoding, so line endings and
encodings are exactly what a plain ``open(path, "w")`` would have written.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

TMP_SUFFIX = ".equpdater-new"


@contextmanager
def replacing(path: str, mode: str = "wb", **open_kw):
    """``with replacing(path, "w", encoding="utf-8") as f: f.write(...)``.
    The old file is replaced only if the block finishes without an error."""
    tmp = path + TMP_SUFFIX
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    f = open(tmp, mode, **open_kw)
    try:
        with f:
            yield f
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_text(path: str, text: str, **open_kw):
    with replacing(path, "w", **open_kw) as f:
        f.write(text)


def write_bytes(path: str, data: bytes):
    with replacing(path, "wb") as f:
        f.write(data)
