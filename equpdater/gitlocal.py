"""What a git clone in an addon folder says about itself, without git.

OctoLauncher installs an addon by cloning its repository, so a folder it
installed names its own source and its exact commit. Unlike a version some tool
wrote in a settings file, that is the record git itself checked the files out
from -- and whether the files still match it can be tested, file by file,
against the hashes in .git/index.

Nothing here runs git or writes anything. A clone can name any URL: what it
says is shown to the player and goes through the same host check as any other
source before anything is downloaded from it."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalClone:
    remote: str
    sha: str | None
    branch: str | None


_SHA = re.compile(r"^[0-9a-f]{40}$")


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _strip_login(url: str) -> str:
    """A remote URL with any login taken out. A clone made with a token in
    the URL must not carry it into EqUpdater's config or log."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        if not host or (parts.username is None and parts.password is None):
            return url
        netloc = f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return ""
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def _remote_url(config: str) -> str | None:
    """The origin remote's URL, or the only remote's."""
    remotes, current = {}, None
    for raw in config.splitlines():
        line = raw.strip()
        section = re.match(r'^\[\s*remote\s+"([^"]+)"\s*\]$', line)
        if section:
            current = section.group(1)
        elif line.startswith("["):
            current = None
        elif current:
            m = re.match(r"^url\s*=\s*(.+)$", line)
            if m:
                remotes.setdefault(current, m.group(1).strip())
    if "origin" in remotes:
        return remotes["origin"]
    return next(iter(remotes.values())) if len(remotes) == 1 else None


def _resolve_ref(git_dir: str, ref: str) -> str | None:
    loose = _read_text(os.path.join(git_dir, *ref.split("/")))
    if loose and _SHA.match(loose.strip()):
        return loose.strip()
    for line in (_read_text(os.path.join(git_dir, "packed-refs")) or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref and _SHA.match(parts[0]):
            return parts[0]
    return None


def read_clone(folder: str) -> LocalClone | None:
    """The source, commit and branch of the clone in ``folder``, or None when
    it is not one, or names no usable remote. Cheap: reads no addon files."""
    git_dir = os.path.join(folder, ".git")
    if not os.path.isdir(git_dir):
        return None
    remote = _strip_login(_remote_url(_read_text(
        os.path.join(git_dir, "config")) or "") or "")
    if not remote:
        return None
    head = (_read_text(os.path.join(git_dir, "HEAD")) or "").strip()
    sha, branch = None, None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        sha = _resolve_ref(git_dir, ref)
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/"):]
    elif _SHA.match(head):
        sha = head
    return LocalClone(remote, sha, branch)


def _index_entries(path: str) -> list | None:
    """[(relative path, blob sha)] from .git/index versions 2 and 3, or None
    when the file cannot be read that way (version 4, a merge in progress)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"DIRC":
        return None
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):
        return None
    entries, pos = [], 12
    try:
        for _ in range(count):
            start = pos
            blob = data[start + 40:start + 60].hex()
            flags = struct.unpack(">H", data[start + 60:start + 62])[0]
            if flags & 0x3000:              # a stage: an unresolved merge
                return None
            pos = start + 62
            if flags & 0x4000:              # version 3's extended flags
                pos += 2
            end = data.index(b"\0", pos)
            entries.append((data[pos:end].decode("utf-8"), blob))
            # Entries are NUL-padded to a multiple of 8 bytes.
            pos = start + ((end - start + 8) // 8) * 8
    except (ValueError, struct.error, IndexError, UnicodeDecodeError):
        return None
    return entries


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def clone_is_clean(folder: str) -> bool:
    """Whether every file git tracks here still has the content git recorded.
    Files git does not track are not the commit's and do not count.

    False for anything else, an index that cannot be read included: a commit
    is only recorded for files proven to be that commit. A file differing
    only in CRLF line endings is the same content (git on Windows checks files
    out that way)."""
    entries = _index_entries(os.path.join(folder, ".git", "index"))
    if not entries:
        return False
    for rel, blob in entries:
        try:
            with open(os.path.join(folder, *rel.split("/")), "rb") as f:
                content = f.read()
        except OSError:
            return False
        if (_blob_sha(content) != blob
                and _blob_sha(content.replace(b"\r\n", b"\n")) != blob):
            return False
    return True
