"""Gate for handing out an EqUpdater 3 beta.

    python tools/beta_check.py [--tests] [--package]

Checks what can be checked mechanically and prints what only a person can:

  FAIL  stops the beta (exit 1)
  WARN  allowed, but the person handing it out must know
  ok    checked

Always run: version, required documents, 2.x release files untouched, no
secrets in tracked files, nothing released (not on main, no 3.x tag), skins'
licence notes. ``--tests`` runs the whole test suite. ``--package`` checks the
built installer and runs the packaged self-test (build with
``python tools/build_v3.py --setup`` first).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REQUIRED_DOCS = ("BETA.md", "KNOWN_ISSUES.md", "CHANGELOG-3.0.md", "QA_CHECKLIST.md", "PARITY_LEDGER.md",
                 "INSTALLATION.md", "MIGRATION.md", "SAFETY.md", "PERFORMANCE.md", "SKINNING.md",
                 "COMPONENTS.md", "SKIN_ASSETS.md", "OCTOBOT.md", "SOCIAL.md", "TESTING.md", "ARCHITECTURE.md", "LINUX.md",
                 "OCTOWOW_INTEGRATION_PROPOSAL.md", "QA_REPORT.md", "SOCIAL_TEST_BACKEND.md")
#: The 2.x release machinery: the beta must not change any of it.
TWO_X_FILES = ("build.py", "EqUpdater.py", "EqUpdaterSetup.py", "equpdater/installer.py",
               "equpdater/deploy.py", "install", ".github/workflows/release.yml")
SECRET = re.compile(r"(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|"
                    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|xox[abp]-[A-Za-z0-9-]{20,})")

results: list = []


def add(level: str, what: str, detail: str = "") -> None:
    results.append((level, what, detail))


def git(*args, check=False) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


def check_version():
    from equpdater.qtui.bridge import APP_VERSION_V3
    if re.fullmatch(r"3\.0\.0-beta\.\d+", APP_VERSION_V3):
        add("ok", f"version {APP_VERSION_V3}")
    else:
        add("FAIL", "version", f"{APP_VERSION_V3!r} is not 3.0.0-beta.N")
    for name in ("BETA.md",):
        p = os.path.join(ROOT, "docs", "v3", name)
        if os.path.isfile(p) and APP_VERSION_V3 not in open(p, encoding="utf-8").read():
            add("FAIL", f"{name} names another version", APP_VERSION_V3)


def check_docs():
    missing = [d for d in REQUIRED_DOCS
               if not os.path.isfile(os.path.join(ROOT, "docs", "v3", d))
               or os.path.getsize(os.path.join(ROOT, "docs", "v3", d)) < 200]
    add("FAIL" if missing else "ok", "required documents", ", ".join(missing) if missing else f"{len(REQUIRED_DOCS)} present")
    broken = []
    for d in REQUIRED_DOCS:
        p = os.path.join(ROOT, "docs", "v3", d)
        if not os.path.isfile(p):
            continue
        for ref in re.findall(r"`([A-Za-z0-9_\-]+\.md)`", open(p, encoding="utf-8").read()):
            if not os.path.isfile(os.path.join(ROOT, "docs", "v3", ref)) and not os.path.isfile(os.path.join(ROOT, ref)):
                broken.append(f"{d} -> {ref}")
    add("FAIL" if broken else "ok", "documents refer only to documents that exist", "; ".join(sorted(set(broken))[:6]))


#: v2.1.4, the 2.x release the beta is measured against (docs/v3/BASELINE.md).
#: The local `main` ref can be stale, so the release commit itself is the base.
BASELINE = "496fa677c217b5025bb2ecdee8a8f808764b8e7e"


def check_two_x_untouched():
    try:
        base = git("rev-parse", "--verify", BASELINE + "^{commit}", check=True)
    except RuntimeError:
        add("WARN", "2.x release files", "the v2.1.4 commit is not in this clone; cannot compare")
        return
    changed = git("diff", "--name-only", base, "HEAD", "--", *TWO_X_FILES).splitlines()
    dirty = git("status", "--porcelain", "--", *TWO_X_FILES).splitlines()
    if changed or dirty:
        add("FAIL", "2.x release files changed", ", ".join(changed + [d[3:] for d in dirty]))
    else:
        add("ok", "2.x build, installer and release workflow untouched since v2.1.4")


def check_not_released():
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("main", "master"):
        add("FAIL", "on the main branch", "the beta is built from its own branch")
    else:
        add("ok", f"on branch {branch}, not main")
    tags = [t for t in git("tag", "--list", "v3*", "3.*").splitlines() if t]
    add("FAIL" if tags else "ok", "no 3.x release tag", ", ".join(tags))


def check_secrets():
    hits = []
    for rel in git("ls-files").splitlines():
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):                           # deleted in the working tree, not committed yet
            continue
        if os.path.getsize(p) > 2_000_000 or rel.lower().endswith((".png", ".webp", ".gif", ".ico", ".ttf", ".otf",
                                                                   ".mpq", ".jpg", ".zip", ".pyc")):
            continue
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if SECRET.search(text):
            hits.append(rel)
    add("FAIL" if hits else "ok", "no secrets in tracked files", ", ".join(hits))


def check_skins():
    from equpdater.core import skin
    skins = skin.discover(os.path.join(ROOT, "skins"))
    bad = {k: v.problems for k, v in skins.items() if v.problems}
    add("FAIL" if bad else "ok", f"{len(skins)} official skins load cleanly", json.dumps(bad) if bad else "")
    for sid in skins:
        m = json.load(open(os.path.join(ROOT, "skins", sid, "manifest.json"), encoding="utf-8"))
        if "confirm" in m.get("license", "").lower():
            add("WARN", f"skin {sid}: artwork rights unconfirmed", m["license"])
    fonts = os.path.join(ROOT, "fonts")
    if any(n.startswith("ObjectSans") for n in os.listdir(fonts)) and not os.path.isfile(os.path.join(fonts, "ObjectSans-LICENSE.txt")):
        add("WARN", "font Object Sans: redistribution terms unconfirmed", "no ObjectSans-LICENSE.txt beside the font files; fonts/README.txt")


def check_package():
    from equpdater.qtui.bridge import APP_VERSION_V3
    dist = os.path.join(ROOT, "dist")
    setup = os.path.join(dist, f"EqUpdater3-v{APP_VERSION_V3}-Windows-Installer.exe")
    exe = os.path.join(dist, "EqUpdater3", "EqUpdater3.exe")
    if not os.path.isfile(setup):
        add("FAIL", "installer not built", setup)
        return
    mb = os.path.getsize(setup) / 1048576
    add("FAIL" if mb > 80 else "ok", f"installer {mb:.0f} MB", "over the 80 MB budget" if mb > 80 else "")
    if os.path.isfile(exe):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import build_v3
        rep = build_v3.self_test(exe)
        add("FAIL" if rep["problems"] or rep["exit_code"] else "ok", "packaged self-test (all pages, all skins)",
            "; ".join(rep["problems"]))
    else:
        add("WARN", "packaged folder not found, self-test skipped", exe)
    add("WARN", "installer is not code-signed", "SmartScreen will warn")


def check_tests():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from check import run_suite
    ok, report = run_suite(os.path.join(ROOT, "build", "beta-test-log.txt"))
    add("ok" if ok else "FAIL", "test suite", report.strip().splitlines()[-1] if report else "")


def human_items():
    return ["PLAY against a real game (QA_CHECKLIST.md section 1)",
            "installer on a clean Windows profile, SmartScreen/antivirus behaviour (section 0)",
            "Classic (2004) artwork redistribution rights (SKIN_ASSETS.md)",
            "OctoWoW's agreement on the User-Agent and OctoBot's on the static snapshot (KNOWN_ISSUES.md)",
            "GitHub Actions: tests.yml with PySide6 and beta.yml have never run",
            "Linux/Wine: nothing run"]


def main() -> int:
    check_version()
    check_docs()
    check_two_x_untouched()
    check_not_released()
    check_secrets()
    check_skins()
    if "--package" in sys.argv:
        check_package()
    if "--tests" in sys.argv:
        check_tests()
    width = max(len(w) for _l, w, _d in results)
    for level, what, detail in results:
        print(f"  {level:<4}  {what:<{width}}  {detail}")
    print("\nOnly a person can confirm:")
    for item in human_items():
        print(f"  [ ] {item}")
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{len(fails)} failure(s), {sum(1 for r in results if r[0] == 'WARN')} warning(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
