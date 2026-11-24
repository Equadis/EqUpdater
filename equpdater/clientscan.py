"""Scan a client folder: read-only, cancellable, and honest about coverage.

Every reachable file under the root is listed. Most are only listed: hashing a
multi-gigabyte client to find a typo is not a scan anyone runs twice. So:

- *quick*: walk everything, compare sizes with the client's manifest, read the
  headers of programs and archives, and check mod files against their receipts;
- *deep*: also verify the client's non-archive files against the manifest's
  piece hashes, and optionally the MPQ archives too.

Nothing is repaired or written in the client. The findings separate what is
established from what merely might matter, because a custom MPQ, an extra
addon or an unfamiliar DLL is usually something the player chose:

- ``confirmed``: a fact that stops something working;
- ``possible``: a conflict or mismatch worth a look, and it may be on purpose;
- ``info``: context.

One damaged file never ends the scan: each read failure is its own finding."""

from __future__ import annotations

import hashlib
import os
import struct
import time
import uuid

from .diagnostics import MACHINES, pe_machine

SCHEMA = 1
QUICK, DEEP = "quick", "deep"

#: The files launching depends on: duplicates of these, and recent changes to
#: them, are worth saying. Lower case.
CRITICAL = ("wow.exe", "vanillafixes.exe", "vfpatcher.dll", "dlls.txt",
            "d3d9.dll", "dxgi.dll", "realmlist.wtf", "config.wtf")
#: Where an empty file is abnormal.
_NO_EMPTY = (".exe", ".dll", ".mpq", ".toc", ".wtf")
#: Folders whose names can say who the player is. Listed as a count only.
_PRIVATE_TOP = ("wtf",)
#: Folders that are expected to be rewritten and full of noise.
_NOISY_TOP = ("wdb", "logs", "errors", "screenshots", "cache", "reshade-shaders")
#: Top-level folders the game or its mods use. Anything else in the game folder
#: (a backup, a tool's work files) is listed but not judged: a damaged file the
#: game never opens is not a problem with the game. The manifest's are added.
_CLIENT_TOP = {"data", "interface", "fonts", "mods", "wtf", "documentation",
               "customdata", "reshade"} | set(_NOISY_TOP)
#: A proxy DLL loaded in the game's place: only one thing can own each name.
_PROXIES = ("d3d9.dll", "dxgi.dll", "dinput8.dll", "version.dll", "winmm.dll")
_MPQ_MAGICS = (b"MPQ\x1a", b"MPQ\x1b")
#: How recent a change to a launch-critical file is called out.
RECENT_DAYS = 7

MPQ_HEADER = struct.Struct("<4sIIHH")


class Cancelled(Exception):
    pass


def finding(level: str, code: str, why: str, path: str = "", evidence: str = "") -> dict:
    return {"level": level, "code": code, "why": why, "path": path,
            "evidence": evidence}


def _private(rel: str) -> bool:
    """At any depth: a backup holds a copy of WTF under the same names."""
    parts = rel.replace("\\", "/").lower().split("/")
    return any(p in _PRIVATE_TOP or p == "savedvariables" for p in parts)


def _display(rel: str) -> str:
    """A path as a finding may name it. WTF's folder names are account and
    character names, so they are not repeated."""
    parts = rel.replace("\\", "/").split("/")
    low = [p.lower() for p in parts]
    for i, part in enumerate(low):
        if part in _PRIVATE_TOP and len(parts) > i + 2:
            return "/".join(parts[:i + 1]) + "/.../" + parts[-1]
    return "/".join(parts)


def _is_link(entry) -> bool:
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def walk(root: str, cancel=None, on_dir=None):
    """Every file under ``root``, never leaving it: (rel, entry) pairs, then
    what could not be entered. Symbolic links and junctions are not followed,
    so a loop or a link out of the client cannot be walked; they are reported."""
    links, errors = [], []
    pending = [""]

    def gen():
        while pending:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            rel_dir = pending.pop()
            full = os.path.join(root, rel_dir) if rel_dir else root
            if on_dir:
                on_dir(rel_dir)
            try:
                with os.scandir(full) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as e:
                errors.append((rel_dir or ".", str(e)))
                continue
            for entry in entries:
                rel = os.path.join(rel_dir, entry.name) if rel_dir else entry.name
                if _is_link(entry):
                    links.append(rel)
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(rel)
                    elif entry.is_file(follow_symlinks=False):
                        yield rel, entry
                except OSError as e:
                    errors.append((rel, str(e)))
    return gen(), links, errors


def check_mpq(path: str, size: int) -> str | None:
    """What is wrong with an MPQ's header, or None. Raises OSError."""
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:4] not in _MPQ_MAGICS:
        return "does not start like an MPQ archive"
    if head[:4] == b"MPQ\x1a" and len(head) == 16:
        _magic, _hsize, archive, _ver, _shift = MPQ_HEADER.unpack(head)
        if archive and archive > size:
            return (f"is cut short: its header says {archive} bytes, the file "
                    f"has {size}")
    return None


def receipt_findings(client: str, mods: dict, registry_names: dict, files_hash) -> list:
    """Installed mods against EqUpdater's own record of them."""
    out = []
    for mod_id, rec in (mods or {}).items():
        files = rec.get("installed_files") or []
        if not files or not rec.get("enabled", True):
            continue
        name = registry_names.get(mod_id, mod_id)
        missing = [f for f in files
                   if not os.path.exists(os.path.join(client, f))]
        if missing:
            out.append(finding(
                "confirmed", "mod_files_missing",
                f"{name} is enabled, and these files it installed are gone: "
                f"{', '.join(missing)}.", missing[0], "EqUpdater's mod record"))
            continue
        recorded = rec.get("content_hash")
        if recorded and files_hash(client, files) != recorded:
            out.append(finding(
                "possible", "mod_files_changed",
                f"{name}'s files differ from those recorded when "
                f"{rec.get('hash_baseline') or 'it was installed'}: another "
                f"launcher, an update by hand, or damage.",
                files[0], "EqUpdater's mod record"))
    return out


def piece_table(files):
    """The torrent's files laid end to end: [(parts, length, offset)]."""
    out, offset = [], 0
    for parts, length in files:
        out.append((parts, length, offset))
        offset += length
    return out


def verify_pieces(client: str, table: list, index: int, piece_length: int,
                  pieces: bytes, cancel=None) -> str:
    """Check one file against the manifest's piece hashes. "ok", "mismatch",
    "skip" when its pieces cannot be checked (too small to have a piece to
    itself, the only kind that can convict it alone), or "cancelled"."""
    parts, length, offset = table[index]
    if length == 0:
        return "ok"
    first = (offset + piece_length - 1) // piece_length
    last = (offset + length) // piece_length    # exclusive: pieces wholly inside
    if first >= last:
        return "skip"
    path = os.path.join(client, *parts)
    with open(path, "rb") as f:
        f.seek(first * piece_length - offset)
        for n in range(first, last):
            if cancel is not None and cancel.is_set():
                return "cancelled"
            data = f.read(piece_length)
            expected = pieces[n * 20:n * 20 + 20]
            if len(expected) != 20 or hashlib.sha1(data).digest() != expected:
                return "mismatch"
    return "ok"


def scan(client_dir: str, mode: str = QUICK, include_mpq: bool = False,
         manifest=None, mods=None, registry_names=None, files_hash=None,
         protected=(), doctor_checks=None, components=None,
         cancel=None, progress=None, inventory_path: str | None = None,
         clock=time.time) -> dict:
    """Scan ``client_dir``. ``manifest`` is {"name", "files": [(parts, length)],
    "piece_length", "pieces"} or None; ``protected`` is file names (lower case)
    the updater does not expect to match it (mods, patched WoW.exe)."""
    started = clock()
    root = os.path.realpath(client_dir)
    findings = []
    coverage = {"files": 0, "bytes": 0, "skipped": 0, "private_files": 0,
                "hashed": 0, "hash_skipped": 0, "unreadable": 0,
                "manifest": None, "manifest_files": 0, "mpq_verified": False,
                "cancelled": False}
    result = {"schema": SCHEMA, "id": time.strftime("%Y%m%d-%H%M%S-",
                                                    time.localtime(started))
              + uuid.uuid4().hex[:6],
              "client_root": client_dir, "mode": mode, "started_at": started,
              "coverage": coverage, "findings": findings,
              "components": components or [], "recent_changes": []}

    def say(text, fraction=None):
        if progress:
            progress(text, fraction)

    def done():
        result["finished_at"] = clock()
        return result

    if not os.path.isdir(root):
        findings.append(finding("confirmed", "no_client",
                                "The game folder does not exist."))
        return done()

    names_seen, by_dir_lower = {}, {}
    sizes = {}
    judged_critical = {}
    client_tops = set(_CLIENT_TOP)
    for parts, _length in (manifest or {}).get("files") or []:
        if len(parts) > 1:
            client_tops.add(parts[0].lower())
    other_folders = {}
    inventory = None
    if inventory_path:
        try:
            inventory = open(inventory_path, "w", encoding="utf-8")
        except OSError:
            inventory = None
    listed = {}
    gen, links, errors = walk(root, cancel)
    try:
        try:
            for rel, entry in gen:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    coverage["unreadable"] += 1
                    findings.append(finding("possible", "unreadable",
                                            f"Cannot be inspected: {e}", _display(rel)))
                    continue
                coverage["files"] += 1
                coverage["bytes"] += st.st_size
                low = rel.replace("\\", "/").lower()
                sizes[low] = st.st_size
                listed[low] = rel
                if coverage["files"] % 500 == 0:
                    say(f"Listed {coverage['files']} files", None)
                private = _private(rel)
                if private:
                    coverage["private_files"] += 1
                elif inventory is not None:
                    inventory.write(f"{st.st_size}\t{int(st.st_mtime)}\t{rel}\n")

                name = entry.name.lower()
                top = low.split("/", 1)[0]
                dir_key = os.path.dirname(low)
                by_dir_lower.setdefault(dir_key, {}).setdefault(name, []).append(rel)
                in_client = "/" not in low or top in client_tops
                if name in CRITICAL:
                    names_seen.setdefault(name, []).append(rel)
                    if in_client:
                        judged_critical.setdefault(name, []).append(rel)
                    if (in_client and not private
                            and clock() - st.st_mtime < RECENT_DAYS * 86400):
                        result["recent_changes"].append(
                            {"file": _display(rel), "modified": st.st_mtime})
                if not in_client:
                    other_folders[top] = other_folders.get(top, 0) + 1
                    continue
                if top in _NOISY_TOP or private:
                    continue

                # Contents, for the kinds that can be judged cheaply.
                ext = os.path.splitext(name)[1]
                if st.st_size == 0 and ext in _NO_EMPTY:
                    findings.append(finding(
                        "confirmed", "empty_file",
                        "This file is empty; a program, archive or addon "
                        "description never is. Usually an interrupted download.",
                        _display(rel)))
                    continue
                full = os.path.join(root, rel)
                try:
                    if ext in (".exe", ".dll"):
                        machine = pe_machine(full)
                        if machine is None:
                            findings.append(finding(
                                "confirmed", "bad_binary",
                                "Not a valid Windows program: a damaged or "
                                "partial file. Starting it gives Windows error 193.",
                                _display(rel)))
                        elif machine != "x86" and "/" not in low:
                            findings.append(finding(
                                "possible", "wrong_arch",
                                f"This is a {machine} program in the game's "
                                f"folder; the game is 32-bit and cannot load it.",
                                _display(rel), f"PE header: {machine}"))
                    elif ext == ".mpq":
                        problem = check_mpq(full, st.st_size)
                        if problem:
                            findings.append(finding(
                                "confirmed", "bad_archive",
                                f"This archive {problem}.", _display(rel)))
                        if "/" not in low or top != "data":
                            findings.append(finding(
                                "possible", "misplaced_mpq",
                                "The game only reads MPQ archives from Data "
                                "(and its locale folder), so this one is not used.",
                                _display(rel)))
                except OSError as e:
                    coverage["unreadable"] += 1
                    findings.append(finding("possible", "unreadable",
                                            f"Cannot be read: {e}", _display(rel)))
        except Cancelled:
            coverage["cancelled"] = True
    finally:
        if inventory is not None:
            inventory.close()

    coverage["skipped"] += len(links)
    if links:
        shown = [_display(r) for r in links if not _private(r)][:5]
        findings.append(finding(
            "info", "link_skipped",
            f"{len(links)} symbolic links or junctions were not followed, so "
            f"what they point at was not scanned.", "", ", ".join(shown)))
    if other_folders:
        coverage["other_folder_files"] = sum(other_folders.values())
        names = sorted(n for n in other_folders)
        findings.append(finding(
            "info", "other_folders",
            f"{coverage['other_folder_files']} files in folders the game does "
            f"not use were listed and not judged.", "",
            ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")))
    for rel, why in errors:
        coverage["skipped"] += 1
        findings.append(finding("possible", "unenumerable",
                                f"This folder could not be listed: {why}.",
                                _display(rel)))

    # Names only a case-sensitive system can tell apart.
    for dir_key, names in by_dir_lower.items():
        for name, rels in names.items():
            if len(rels) > 1:
                findings.append(finding(
                    "confirmed", "case_collision",
                    "Files differing only in upper and lower case: the game "
                    "sees one name and which file it gets is arbitrary.",
                    _display(rels[0]), ", ".join(_display(r) for r in rels)))

    # A critical file in a second place.
    for name, rels in judged_critical.items():
        if len(rels) > 1 and name not in ("realmlist.wtf", "config.wtf"):
            findings.append(finding(
                "possible", "duplicate_critical",
                f"{name} is here more than once. The game and VanillaFixes use "
                f"the one beside WoW.exe; the others do nothing, and are often "
                f"a sign of a client unpacked into a subfolder.",
                _display(rels[0]), ", ".join(_display(r) for r in rels)))
    if "wow.exe" not in sizes:
        nested = names_seen.get("wow.exe")
        findings.append(finding(
            "confirmed", "no_game_exe",
            "WoW.exe is not in the game folder"
            + (f"; one is at {_display(nested[0])}, so the folder setting probably "
               f"points one level too high." if nested else "."), "WoW.exe"))

    # Addons in the wrong depth: unpacked one folder too deep.
    for low, rel in listed.items():
        parts = low.split("/")
        if len(parts) == 5 and _private(rel):
            continue
        if (len(parts) == 5 and parts[:2] == ["interface", "addons"]
                and parts[4].endswith(".toc") and parts[2] != parts[3]
                and f"interface/addons/{parts[2]}/{parts[2]}.toc" not in sizes):
            findings.append(finding(
                "possible", "nested_addon",
                "This addon looks unpacked one folder too deep: the game only "
                "looks for Interface/AddOns/<name>/<name>.toc.", _display(rel)))

    # Two proxies owned by different things are told apart by the records.
    present_proxies = [n for n in _PROXIES if n in sizes]
    if len(present_proxies) > 1:
        findings.append(finding(
            "info", "dll_proxies",
            "More than one graphics or system proxy DLL is present. Fine when "
            "they chain to each other; if the game fails to start, try without "
            "one.", ", ".join(present_proxies)))
    if "reshade.ini" in sizes and "d3d9.dll" in sizes:
        findings.append(finding(
            "info", "reshade_and_d3d9",
            "ReShade is configured and a d3d9.dll is present. DXVK and ReShade "
            "both use that name, so only one of them is at the game's own "
            "d3d9.dll.", "d3d9.dll"))

    # The manifest.
    if manifest:
        coverage["manifest"] = manifest.get("name") or "client manifest"
        coverage["manifest_files"] = len(manifest.get("files") or [])
        for parts, length in manifest.get("files") or []:
            if cancel is not None and cancel.is_set():
                coverage["cancelled"] = True
                break
            low = "/".join(parts).lower()
            rel = "/".join(parts)
            protected_file = parts[-1].lower() in protected
            size = sizes.get(low)
            if size is None:
                if protected_file:
                    continue
                findings.append(finding(
                    "confirmed", "manifest_missing",
                    "Part of this client, and not here. Verify game files puts "
                    "it back.", rel, coverage["manifest"]))
            elif size != length and not protected_file:
                findings.append(finding(
                    "confirmed", "manifest_size",
                    f"Should be {length} bytes, and is {size}. Verify game files "
                    f"repairs it.", rel, coverage["manifest"]))
    else:
        findings.append(finding(
            "info", "no_manifest",
            "No manifest was available (it is read from the server), so missing "
            "or wrong-sized client files could not be checked."))

    # Deep: the manifest's own hashes.
    if (mode == DEEP and manifest and manifest.get("pieces")
            and not coverage["cancelled"]):
        table = piece_table(manifest["files"])
        wanted = []
        for i, (parts, length, _offset) in enumerate(table):
            name = parts[-1].lower()
            low = "/".join(parts).lower()
            if name in protected or sizes.get(low) != length:
                continue
            if name.endswith(".mpq") and not include_mpq:
                coverage["hash_skipped"] += 1
                continue
            wanted.append(i)
        coverage["mpq_verified"] = bool(include_mpq)
        total = sum(table[i][1] for i in wanted) or 1
        seen_bytes = 0
        for i in wanted:
            parts, length, _o = table[i]
            rel = "/".join(parts)
            say(f"Verifying {rel}", seen_bytes / total)
            try:
                answer = verify_pieces(root, table, i, manifest["piece_length"],
                                       manifest["pieces"], cancel)
            except OSError as e:
                coverage["unreadable"] += 1
                findings.append(finding("possible", "unreadable",
                                        f"Cannot be read: {e}", rel))
                continue
            seen_bytes += length
            if answer == "cancelled":
                coverage["cancelled"] = True
                break
            if answer == "skip":
                coverage["hash_skipped"] += 1
            elif answer == "ok":
                coverage["hashed"] += 1
            else:
                coverage["hashed"] += 1
                findings.append(finding(
                    "confirmed", "manifest_hash",
                    "Its contents do not match the client's manifest: damaged, "
                    "or replaced by something custom. Verify game files repairs "
                    "it.", rel, coverage["manifest"] + " piece hashes"))

    # Receipts and overlapping checks.
    if mods and files_hash:
        findings.extend(receipt_findings(root, mods, registry_names or {}, files_hash))
    for level, text, group in doctor_checks or []:
        findings.append(finding(level, f"login_{group}", text, "",
                                "Login Doctor"))
    return done()


def counts(result: dict) -> dict:
    out = {"confirmed": 0, "possible": 0, "info": 0}
    for f in result.get("findings", []):
        out[f.get("level", "info")] = out.get(f.get("level", "info"), 0) + 1
    return out


__all__ = ["scan", "counts", "finding", "walk", "check_mpq", "verify_pieces",
           "piece_table", "receipt_findings", "QUICK", "DEEP", "MACHINES"]
