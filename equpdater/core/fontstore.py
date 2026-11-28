"""Fonts EqUpdater does not ship: downloaded on request into <app data>/v3/fonts.

Only OpenDyslexic (SIL Open Font Licence) is bundled, and Arial is the system's own. Friz Quadrata and
Object Sans are not redistributed: the player clicks them in Settings > Font and this module fetches the
archive from the address below, over HTTPS from an allow-listed host, and unpacks only font files.
"""

from __future__ import annotations

import io
import os
import zipfile

from ..i18n import tr
from . import net

#: id -> where it comes from and what proves it is installed (file-name prefixes, lower case).
DOWNLOADS = {
    "friz": {"family": "Friz Quadrata", "url": "https://font.download/dl/font/friz-quadrata.zip",
             "prefixes": ("frizquadrata", "friz_quadrata", "friz-quadrata", "friz quadrata")},
    "objectsans": {"family": "Object Sans", "url": "https://font.download/dl/font/object-sans.zip",
                   "prefixes": ("objectsans", "object-sans", "object_sans", "object sans")},
}
ALLOWED_HOSTS = {"font.download"}
MAX_ARCHIVE = 25 * 1024 * 1024          # bytes; the two archives are well under 1 MB
MAX_FONT = 10 * 1024 * 1024
MAX_FILES = 40
FONT_EXT = (".ttf", ".otf")


class FontError(Exception):
    """A download that failed, with a message fit to show the player."""


def fonts_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "fonts")


def font_files(data_dir: str, font_id: str) -> list:
    """The installed files of a downloadable font, by file-name prefix."""
    spec = DOWNLOADS.get(font_id)
    d = fonts_dir(data_dir)
    if not spec or not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, n) for n in os.listdir(d)
                  if n.lower().endswith(FONT_EXT) and n.lower().startswith(spec["prefixes"]))


def is_installed(data_dir: str, font_id: str) -> bool:
    return bool(font_files(data_dir, font_id))


def _fetch(url: str, opener) -> bytes:
    try:
        with opener(url) as resp:
            data = resp.read(MAX_ARCHIVE + 1)
    except RuntimeError as exc:                       # refused by the HTTPS / host rules
        raise FontError(str(exc)) from exc
    except OSError as exc:
        raise FontError(tr("Could not download the font ({reason}).").format(reason=exc)) from exc
    if len(data) > MAX_ARCHIVE:
        raise FontError(tr("The download is larger than expected; it was discarded."))
    return data


def _default_opener(url: str):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "EqUpdater font download"})
    return net.secure_urlopen(req, timeout=30, allowed_hosts=ALLOWED_HOSTS)


def download(data_dir: str, font_id: str, opener=None, progress=None) -> list:
    """Download and unpack one font into the fonts folder. Returns the files written (full paths).
    ``opener(url)`` returns a file-like response and exists so tests need no network."""
    spec = DOWNLOADS.get(font_id)
    if not spec:
        raise FontError(tr("Unknown font."))
    if progress:
        progress(tr("Downloading {font}...").format(font=spec["family"]))
    data = _fetch(spec["url"], opener or _default_opener)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise FontError(tr("The download was not a font archive.")) from exc
    members = [i for i in archive.infolist()
               if not i.is_dir() and i.filename.lower().endswith(FONT_EXT)]
    if not members:
        raise FontError(tr("The archive contained no font files."))
    if len(members) > MAX_FILES:
        raise FontError(tr("The archive contained too many files; it was discarded."))
    target = fonts_dir(data_dir)
    os.makedirs(target, exist_ok=True)
    written = []
    for info in members:
        name = os.path.basename(info.filename.replace("\\", "/"))     # never a path from the archive
        if not name or name.startswith(".") or info.file_size > MAX_FONT:
            continue
        with archive.open(info) as src:
            blob = src.read(MAX_FONT + 1)
        if len(blob) > MAX_FONT:
            continue
        # the archive may name files freely: store them so the installed check recognises them
        if not name.lower().startswith(spec["prefixes"]):
            name = spec["prefixes"][0].replace(" ", "") + "-" + name
        path = os.path.join(target, name)
        tmp = path + ".part"
        with open(tmp, "wb") as out:
            out.write(blob)
        os.replace(tmp, path)
        written.append(path)
    if not written:
        raise FontError(tr("The archive contained no usable font files."))
    return written


def register(paths) -> list:
    """Make downloaded files known to Qt for this run. Returns the families added."""
    from PySide6.QtGui import QFontDatabase
    families = []
    for p in paths:
        fid = QFontDatabase.addApplicationFont(p)
        if fid >= 0:
            families += QFontDatabase.applicationFontFamilies(fid)
    return sorted(set(families))
