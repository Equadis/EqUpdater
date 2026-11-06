"""What OctoLauncher wrote down about the mods it installed -- read, never
written, and never trusted on its own.

OctoLauncher keeps, in its settings.json, a version and a file list for each
mod it installed, and a SHA-256 of each file "as last written or verified".
That looks like proof of which release is on disk. It is not: the hashes are
refreshed whenever the launcher re-verifies a file, the version only when it
installs one. On a real install (2026-11-01) the record said nampower v4.6.2
while both the file and the launcher's own hash were v4.6.3's release asset.

So the version here is a *hint*: which release to compare the files against.
Only an exact match between the files on disk and that release's files
identifies anything (see identify_installed_release in app.py).

Only settings.json is read, and only the fields below. The launcher's sign-in
(auth.json) is never opened."""

from __future__ import annotations

import json
import os
import sys

#: OctoLauncher's mod id -> (EqUpdater's mod id, the repository both install
#: it from). Listed only where the two install the same project's releases:
#: OctoLauncher's dxvk is the dxvk-gplasync fork, EqUpdater's is upstream
#: DXVK, so a version of one says nothing about the other. From OctoLauncher
#: 1.4.3's mod list.
LAUNCHER_MODS = {
    "vanillaFixes":    ("VanillaFixes", "github.com/hannesmann/vanillafixes"),
    "nampower":        ("nampower", "github.com/emyrk/nampower"),
    "classicApi":      ("ClassicAPI", "github.com/brues-code/classicapi"),
    "superWow":        ("SuperWoW", "github.com/balakethelock/superwow"),
    "transmogFix":     ("transmogfix", "codeberg.org/marcelinevq/weirdutils"),
    "unitXp":          ("UnitXP_SP3", "codeberg.org/konaka/unitxp_sp3"),
    "vanillaHelpers":  ("VanillaHelpers", "github.com/isfir/vanillahelpers"),
    "multiMonitorFix": ("VanillaMultiMonitorFix",
                        "github.com/mates1500/vanillamultimonitorfix"),
}


def settings_path() -> str | None:
    """Where OctoLauncher (an Electron app named octo-launcher) keeps its
    settings on this system."""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.expanduser("~/.config"))
    return os.path.join(base, "octo-launcher", "settings.json") if base else None


def _repo_key(url: str | None) -> str:
    u = (url or "").strip().lower().rstrip("/")
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
    return u[:-4] if u.endswith(".git") else u


def _same_dir(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(p)))
    return norm(a) == norm(b)


def read_settings(path: str | None = None) -> dict | None:
    """The fields of OctoLauncher's settings this module uses, or None when
    there is no readable file. Everything else in it is dropped unread."""
    path = path or settings_path()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in
            ("mods", "clientDir", "activeClientDir")}


def version_hints(client_dir: str, registry, settings: dict | None = None,
                  path: str | None = None) -> dict:
    """{EqUpdater mod id: version OctoLauncher says it installed} for the
    mods it installed into *this* game folder, from the same repository,
    tracking the same files.

    A hint, not a fact: see the module docstring."""
    st = settings if settings is not None else read_settings(path)
    if not st or not isinstance(st.get("mods"), dict):
        return {}
    if not (_same_dir(st.get("clientDir"), client_dir)
            or _same_dir(st.get("activeClientDir"), client_dir)):
        return {}
    by_id = {m["id"]: m for m in registry}
    out = {}
    for launcher_id, rec in st["mods"].items():
        if launcher_id not in LAUNCHER_MODS or not isinstance(rec, dict):
            continue
        mod_id, repo = LAUNCHER_MODS[launcher_id]
        mod = by_id.get(mod_id)
        version = rec.get("installedVersion")
        if not mod or not isinstance(version, str) or not version.strip():
            continue
        if _repo_key(mod.get("repo_url")) != repo:
            continue
        theirs = {str(f).lower() for f in rec.get("installedFiles") or []}
        ours = {f.lower() for f in mod.get("installed_files") or []}
        if not theirs or not theirs <= ours:
            continue
        out[mod_id] = version.strip()
    return out
