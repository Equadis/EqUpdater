"""Finding a game the player already has, and putting an archive they already
downloaded in place. Qt-free, and it never downloads the game.

EqUpdater 3.0 does not fetch the OctoWoW client (the aria2/torrent downloader
is gone; ``docs/v3/ARIA2_AUDIT.md``). What it does instead:

* **find** a client that is already on this computer: the folder EqUpdater 2.x
  was using, the one OctoLauncher uses, a copy somebody made from the ZIP or
  the torrent, or a folder in a place players commonly keep one. Each is
  described by what is in it (complete, unfinished torrent, no ``WoW.exe``)
  and nothing is changed by looking;
* **extract** a ZIP or TAR the player downloaded themselves, into a folder they
  choose: entries that would land outside it are refused, so are links, the
  free space is checked first, nothing in an existing folder is overwritten,
  and the result is only moved into place once ``WoW.exe`` is there;
* **guard** against touching a folder while EqUpdater 2.x or the game has it
  open.

The files OctoLauncher keeps are read only through ``equpdater.octolauncher``
(its sign-in is never opened).
"""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field

from .. import config, octolauncher, platforms
from ..i18n import N_, tr

GAME_EXE = "WoW.exe"
#: A game folder smaller than this is not a client (the real one is gigabytes).
MIN_EXE_BYTES = 1_000_000
MIN_MPQ_FILES = 3
#: Leftovers of an unfinished torrent download.
PARTIAL_SUFFIXES = (".aria2", ".part", ".!ut", ".bc!", ".qbt", ".td")
#: Archive extraction limits: a download that expands past this is not a game.
MAX_ENTRIES = 200_000
MAX_RATIO = 200                     # expanded size / archive size
ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")


class InstallError(Exception):
    """A sentence the player can act on (an N_ message)."""


class Cancelled(Exception):
    pass


@dataclass
class Candidate:
    path: str
    source: str                     # equpdater, octolauncher, common, scan
    state: str                      # complete | unfinished | incomplete
    evidence: list = field(default_factory=list)
    mpqs: int = 0

    @property
    def usable(self) -> bool:
        return self.state in ("complete", "unfinished")

    def as_dict(self) -> dict:
        return {"path": self.path, "source": self.source, "state": self.state,
                "usable": self.usable, "evidence": list(self.evidence)}


SOURCE_NAMES = {
    "equpdater": N_("The folder EqUpdater 2 uses"),
    "octolauncher": N_("The folder OctoLauncher uses"),
    "common": N_("A usual place for the game"),
    "scan": N_("Found next to another game folder"),
}
STATE_NAMES = {
    "complete": N_("Complete: WoW.exe and the game's data are here"),
    "unfinished": N_("Unfinished download: some files are still being fetched"),
    "incomplete": N_("No game here: WoW.exe or the game's data are missing"),
}


# -- looking at a folder ---------------------------------------------------------

def _is_exe(path: str) -> bool:
    try:
        if os.path.getsize(path) < MIN_EXE_BYTES:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def inspect_folder(path: str, source: str = "scan") -> Candidate:
    """What is in ``path``, without changing it."""
    ev: list = []
    exe = os.path.join(path, GAME_EXE)
    have_exe = os.path.isfile(exe)
    good_exe = have_exe and _is_exe(exe)
    mpqs = 0
    data = os.path.join(path, "Data")
    try:
        for name in os.listdir(data) if os.path.isdir(data) else []:
            if name.lower().endswith(".mpq"):
                mpqs += 1
    except OSError:
        pass
    partial = []
    for root in (path, data):
        try:
            for name in os.listdir(root) if os.path.isdir(root) else []:
                if name.lower().endswith(PARTIAL_SUFFIXES):
                    partial.append(name)
        except OSError:
            pass
    if good_exe:
        ev.append(tr("WoW.exe found"))
    elif have_exe:
        ev.append(tr("WoW.exe is there but is not a game program (too small or damaged)"))
    else:
        ev.append(tr("WoW.exe not found"))
    ev.append(tr("{count} game data archives in Data", count=mpqs))
    if partial:
        ev.append(tr("{count} unfinished download files", count=len(partial)))
    if good_exe and mpqs >= MIN_MPQ_FILES:
        state = "unfinished" if partial else "complete"
    else:
        state = "incomplete"
    return Candidate(os.path.normpath(path), source, state, ev, mpqs)


def _common_places() -> list:
    home = os.path.expanduser("~")
    places = [os.path.join(home, "Games", "OctoWoW"), os.path.join(home, "OctoWoW"),
              os.path.join(home, "Games", "OctoWoW Client")]
    if os.name == "nt":
        for drive in ("C", "D", "E"):
            places += [f"{drive}:\\Games\\OctoWoW", f"{drive}:\\OctoWoW",
                       f"{drive}:\\Games\\OctoWoW Client"]
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(env):
                places.append(os.path.join(os.environ[env], "OctoWoW"))
    return places


def find_installs(config_data: dict | None = None, launcher_settings: dict | None = None,
                  extra: list | None = None, places: list | None = None) -> list:
    """Every game folder this computer seems to have, best first. Nothing is
    created, moved or written. A folder is listed once, under the first source
    that named it."""
    cfg = config_data if config_data is not None else config.load_config()
    st = launcher_settings if launcher_settings is not None else octolauncher.read_settings()
    named: list = []                             # (path, source)
    if cfg.get("out_dir"):
        named.append((cfg["out_dir"], "equpdater"))
    for key in ("activeClientDir", "clientDir"):
        if st and st.get(key):
            named.append((st[key], "octolauncher"))
    for p in (places if places is not None else _common_places()):
        named.append((p, "common"))
    for p in extra or []:
        named.append((p, "scan"))
    seen, out = set(), []
    for path, source in named:
        if not isinstance(path, str) or not path.strip():
            continue
        key = os.path.normcase(os.path.normpath(os.path.abspath(path)))
        if key in seen or not os.path.isdir(path):
            continue
        seen.add(key)
        out.append(inspect_folder(path, source))
    order = {"complete": 0, "unfinished": 1, "incomplete": 2}
    out.sort(key=lambda c: order[c.state])         # stable: sources keep their order
    return out


def equpdater_2x_present() -> bool:
    """EqUpdater 2.x's settings exist, so 3.0 has something to import."""
    return os.path.isfile(config.CONFIG_FILE)


# -- not while something else has it ----------------------------------------------

def installed_2x_running(install_dir: str | None = None) -> bool:
    """Whether EqUpdater 2.x's program is running (Windows will not let a
    running program be written, which is how deploy asks)."""
    from .. import deploy
    folder = install_dir or deploy.default_install_dir()
    exe = os.path.join(folder, deploy.EXE)
    return os.path.exists(exe) and deploy._exe_in_use(folder)


def folder_busy(path: str) -> str:
    """"" when ``path`` can be changed, else why not (an N_ message)."""
    if os.path.isfile(os.path.join(path, GAME_EXE)) and platforms.client_in_use(path):
        return N_("The game is running from that folder. Close it first.")
    return ""


# -- local archives ---------------------------------------------------------------

def archive_kind(path: str) -> str:
    low = path.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(ARCHIVE_EXTS[1:]):
        return "tar"
    if low.endswith((".7z", ".rar")):
        raise InstallError(N_("7z and RAR archives cannot be opened here. Extract it "
                              "with your archive program, then choose the folder."))
    raise InstallError(N_("That is not a ZIP or TAR archive."))


def _safe_name(name: str) -> str:
    """The entry's path with ``/`` separators, or an InstallError if it could
    land outside the folder or is not an ordinary file name."""
    norm = name.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if (not parts or norm.startswith("/") or any(p == ".." for p in parts)
            or (len(norm) > 1 and norm[1] == ":")):
        raise InstallError(tr("The archive holds a path that would leave the folder: {name}", name=name))
    for p in parts:
        if any(c in p for c in '<>"|?*') or any(ord(c) < 32 for c in p):
            raise InstallError(tr("The archive holds a file name that cannot be used: {name}", name=name))
    return "/".join(parts)


def _members(path: str, kind: str):
    """[(safe name, size, is_dir, opener)] for every entry, refusing links and
    anything but files and folders. Reads only the archive's index."""
    out = []
    if kind == "zip":
        zf = zipfile.ZipFile(path)
        for i in zf.infolist():
            mode = (i.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:
                raise InstallError(tr("The archive holds a link: {name}", name=i.filename))
            nm = _safe_name(i.filename)
            out.append((nm, i.file_size, i.is_dir(), (lambda i=i: zf.open(i))))
        return out, zf
    tf = tarfile.open(path)
    for m in tf.getmembers():
        if m.issym() or m.islnk():
            raise InstallError(tr("The archive holds a link: {name}", name=m.name))
        if not (m.isfile() or m.isdir()):
            raise InstallError(tr("The archive holds something that is not a file: {name}", name=m.name))
        nm = _safe_name(m.name)
        out.append((nm, m.size, m.isdir(), (lambda m=m: tf.extractfile(m))))
    return out, tf


def _flatten(members: list) -> tuple:
    """(entries, strip): when every entry sits under one folder that holds the
    game (``OctoWoW/WoW.exe``) the folder itself is dropped."""
    tops = {m[0].split("/")[0] for m in members}
    if len(tops) == 1:
        top = next(iter(tops))
        if any(m[0].lower() == f"{top}/wow.exe".lower() for m in members) and not any(
                m[0].lower() == "wow.exe" for m in members):
            return members, top + "/"
    return members, ""


@dataclass
class ArchivePlan:
    archive: str
    kind: str
    files: int
    total: int
    strip: str
    has_exe: bool

    def as_dict(self) -> dict:
        return {"archive": self.archive, "files": self.files, "bytes": self.total,
                "hasGame": self.has_exe}


def plan_archive(path: str) -> ArchivePlan:
    """Read the archive's index and judge it, extracting nothing."""
    kind = archive_kind(path)
    try:
        members, handle = _members(path, kind)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as e:
        raise InstallError(tr("The archive cannot be read: {error}", error=e)) from None
    try:
        if len(members) > MAX_ENTRIES:
            raise InstallError(N_("The archive holds too many files to be a game."))
        total = sum(m[1] for m in members if not m[2])
        size = max(1, os.path.getsize(path))
        if total / size > MAX_RATIO and total > 1 << 30:
            raise InstallError(N_("The archive expands far more than it should."))
        _entries, strip = _flatten(members)
        names = {m[0][len(strip):].lower() for m in members if not m[2]
                 and m[0].startswith(strip)}
        return ArchivePlan(path, kind, sum(1 for m in members if not m[2]), total, strip,
                           GAME_EXE.lower() in names)
    finally:
        handle.close()


def extract_archive(path: str, dest: str, progress=None, cancel=None,
                    free_space=None) -> str:
    """Extract ``path`` into ``dest`` and return the game folder.

    ``dest`` must not exist or must be empty: nothing already there is ever
    overwritten. Files are written under ``<dest>.extracting`` and moved to
    ``dest`` only when complete and holding ``WoW.exe``; cancelling or failing
    removes the partial folder. ``progress(done_bytes, total_bytes)``;
    ``cancel()`` is polled between chunks."""
    plan = plan_archive(path)
    dest = os.path.abspath(dest)
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise InstallError(N_("Choose an empty folder, or one that does not exist yet."))
    if not plan.has_exe:
        raise InstallError(N_("There is no WoW.exe in that archive, so it is not the game."))
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)
    free = (free_space or (lambda p: shutil.disk_usage(p).free))(parent)
    if free < plan.total + (64 << 20):
        raise InstallError(tr("There is not enough free space: {need} MB needed, {have} MB free.",
                              need=plan.total // (1 << 20) + 64, have=free // (1 << 20)))
    stage = dest + ".extracting"
    if os.path.exists(stage):
        shutil.rmtree(stage, ignore_errors=True)
    members, handle = _members(path, plan.kind)
    root = os.path.realpath(stage)
    done = 0
    try:
        os.makedirs(stage)
        for name, _size, is_dir, opener in members:
            if not name.startswith(plan.strip) or name == plan.strip.rstrip("/"):
                continue
            rel = name[len(plan.strip):]
            target = os.path.realpath(os.path.join(stage, rel))
            if target != root and not target.startswith(root + os.sep):
                raise InstallError(tr("The archive holds a path that would leave the folder: "
                                      "{name}", name=name))
            if is_dir:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = opener()
            with src, open(target, "wb") as out:
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, plan.total)
        if not _is_exe(os.path.join(stage, GAME_EXE)):
            raise InstallError(N_("The WoW.exe in that archive is not a game program."))
        if os.path.isdir(dest):
            os.rmdir(dest)
        os.replace(stage, dest)
        return dest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        handle.close()
