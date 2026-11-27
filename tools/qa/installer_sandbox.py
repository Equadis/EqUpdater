"""Run the REAL built installer and the REAL installed program in a sandbox and check what they touch.

    python tools/qa/installer_sandbox.py --setup dist/EqUpdater3-v3.0.0-beta.1-Windows-Installer.exe \
        --out bench-results/installer-sandbox.json [--protect "C:\\path\\to\\a\\real\\game" ...]

The installer is the shipped .exe (not the Python module). It is pointed at a throwaway
``LOCALAPPDATA`` so nothing real is installed; its only shared side effect is the Apps & features
entry (a per-user registry key), which the run adds and removes and checks. ``--protect`` folders
(real game installations, the real settings folder) are *not given* to the installer; they are
fingerprinted (every file's size and modification time, plus a SHA-256 of the small files) before and
after, to show that nothing in them changed while all of this happened.

Steps, each recorded as pass/fail with detail:
  1. the real EqUpdater 2 guard: with the real environment and 2.x running, install is refused
  2. silent install (shortcut-less, no launch): files, marker, Apps & features entry
  3. the installed program passes its own headless self-test
  4. installing the same version again (upgrade path)
  5. install is refused while the installed program is running
  6. a sandbox "existing EqUpdater settings and game" is untouched by install, upgrade
  7. uninstall (setup exe) refuses a folder it did not create
  8. uninstall through the installed program (Apps & features command): folder removed after exit,
     entry removed, settings and game untouched
  9. uninstall through the setup exe
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EqUpdater3"


def fingerprint(path, small=2_000_000):
    out = {}
    for d, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            h = ""
            if st.st_size <= small:
                with open(p, "rb") as fh:
                    h = hashlib.sha256(fh.read()).hexdigest()
            out[p] = (st.st_size, int(st.st_mtime), h)
    return out


def registry_entry():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            return {n: winreg.QueryValueEx(k, n)[0] for n in ("DisplayName", "DisplayVersion", "InstallLocation", "UninstallString")}
    except OSError:
        return None


def run(cmd, env, timeout=300):
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--protect", nargs="*", default=[])
    a = ap.parse_args()
    setup = os.path.abspath(a.setup)
    box = tempfile.mkdtemp(prefix="equ3-sandbox-")
    local = os.path.join(box, "Local")
    prog = os.path.join(local, "Programs", "EqUpdater3")
    data = os.path.join(local, "EqUpdater")
    game = os.path.join(box, "Games", "OctoWoW")
    os.makedirs(os.path.join(data, "v3"))
    os.makedirs(os.path.join(game, "Data"))
    os.makedirs(os.path.join(game, "Interface", "AddOns", "pfUI"))
    for rel, body in (("EqUpdater/config.json", b'{"out_dir": "%s", "mods": {"x": {"enabled": true}}}' % game.replace("\\", "/").encode()),
                      ("EqUpdater/v3/settings.json", b'{"skin": "turtle-wow", "sidebar_collapsed": false}'),
                      ("EqUpdater/v3/social.json", b'{"friends": [{"id": "a1", "name": "Thrall", "realm": "nzoth"}]}')):
        with open(os.path.join(local, *rel.split("/")), "wb") as f:
            f.write(body)
    for rel in ("WoW.exe", "Data/patch-1.mpq", "Interface/AddOns/pfUI/pfUI.lua", "dlls.txt", "Config.wtf", "WTF/Account/X/SavedVariables/pfUI.lua"):
        p = os.path.join(game, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(os.urandom(500) + rel.encode())
    guard = [data, game] + [os.path.abspath(x) for x in a.protect]
    before = {g: fingerprint(g) for g in guard}
    report = {"setup": setup, "setup_mb": round(os.path.getsize(setup) / 1048576, 1), "sandbox": box, "steps": []}

    def changed(g):
        now = fingerprint(g)
        diff = sorted(k for k in set(now) | set(before[g]) if now.get(k) != before[g].get(k))
        return diff

    def step(name, ok, detail=""):
        report["steps"].append({"step": name, "pass": bool(ok), "detail": str(detail)[:600]})
        print(("PASS " if ok else "FAIL ") + name + ("  " + str(detail)[:160] if detail else ""), flush=True)

    sandbox_env = dict(os.environ, LOCALAPPDATA=local, TEMP=os.path.join(box, "tmp"), TMP=os.path.join(box, "tmp"))
    os.makedirs(sandbox_env["TEMP"], exist_ok=True)
    log = os.path.join(box, "log.txt")
    base = [setup, "--silent", "--no-shortcuts", "--no-launch", "--no-warm", "--log", log]

    # 1. the real 2.x guard, in the real environment
    from_real = subprocess.run([setup, "--silent", "--no-shortcuts", "--no-launch", "--no-warm", "--install-dir", prog,
                                "--no-register", "--log", log], capture_output=True, text=True, timeout=300)
    text = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
    two_x_running = subprocess.run(["tasklist", "/fi", "imagename eq EqUpdater.exe"], capture_output=True, text=True).stdout
    running = "EqUpdater.exe" in two_x_running
    if running:
        step("1. real environment, EqUpdater 2 running: install refused and nothing written",
             from_real.returncode == 1 and "EqUpdater 2 is running" in text and not os.path.exists(prog), text.strip())
    else:
        step("1. (skipped: EqUpdater 2 is not running on this machine)", True, "the guard is covered by unit tests")
        if os.path.exists(prog):
            shutil.rmtree(prog, ignore_errors=True)

    # 2. install
    code, out = run(base + ["--install-dir", prog], sandbox_env)
    text = open(log, encoding="utf-8").read()
    exe = os.path.join(prog, "EqUpdater3.exe")
    entry = registry_entry()
    step("2. silent install", code == 0 and os.path.isfile(exe) and os.path.isfile(os.path.join(prog, "install.json"))
         and os.path.isdir(os.path.join(prog, "_internal", "skins")) and os.path.isdir(os.path.join(prog, "_internal", "equpdater", "qtui", "qml")),
         text.strip())
    step("2b. Apps & features entry written for this user", bool(entry) and entry["InstallLocation"] == prog and "--uninstall" in entry["UninstallString"], entry)
    skins = sorted(n for n in os.listdir(os.path.join(prog, "_internal", "skins")) if os.path.isfile(os.path.join(prog, "_internal", "skins", n, "manifest.json")))
    step("2c. all six skins are in the package", len(skins) == 6, skins)

    # 3. self test of the installed program
    st = os.path.join(box, "selftest.json")
    env = dict(sandbox_env, QT_QPA_PLATFORM="offscreen", EQU3_DATA_DIR=os.path.join(box, "appdata"))
    code, out = run([exe, f"--self-test={st}", "--gui"], env, 240)
    rep = json.load(open(st)) if os.path.exists(st) else {}
    step("3. installed program self-test (all pages, all skins, offscreen)", code == 0 and not rep.get("problems") and rep.get("gui_loaded"), rep.get("problems") or rep.get("skins_switched"))

    # 4. same version again
    code, out = run(base + ["--install-dir", prog], sandbox_env)
    step("4. installing again (upgrade path)", code == 0 and os.path.isfile(exe) and not os.path.exists(prog + ".old") and not os.path.exists(prog + ".new"), open(log).read().strip())

    # 6a. nothing shared changed
    diffs = {g: changed(g)[:5] for g in guard if changed(g)}
    step("6a. settings, game and protected folders unchanged after install and upgrade", not diffs, diffs)

    # 5. refuses while running
    app = subprocess.Popen([exe], env=dict(env, EQU3_NO_SETUP="1", EQU3_OFFLINE="1", EQUPDATER_OFFLINE="1"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(6)
    code, out = run(base + ["--install-dir", prog], sandbox_env)
    text = open(log, encoding="utf-8").read()
    step("5. install refused while EqUpdater 3 is running", code == 1 and "is running" in text, text.strip())
    code, out = run(base + ["--uninstall", "--install-dir", prog], sandbox_env)
    text = open(log, encoding="utf-8").read()
    step("5b. uninstall refused while EqUpdater 3 is running", code == 1 and os.path.isdir(prog), text.strip())
    app.terminate()
    app.wait(20)
    time.sleep(1)

    # 7. refuses folders it did not make
    for victim in (game, data):
        code, out = run(base + ["--uninstall", "--install-dir", victim], sandbox_env)
        step(f"7. uninstall refuses a folder it did not make ({os.path.basename(victim)})", code == 1 and os.path.isdir(victim), open(log).read().strip())

    # 8. the installed program's own --uninstall (what Apps & features runs)
    code, out = run([exe, "--uninstall", "--silent"], dict(sandbox_env, QT_QPA_PLATFORM="offscreen"), 60)
    for _ in range(40):
        if not os.path.exists(prog):
            break
        time.sleep(0.5)
    step("8. uninstall from the installed program: folder gone, entry gone",
         code == 0 and not os.path.exists(prog) and registry_entry() is None, f"exit {code}, folder exists={os.path.exists(prog)}, entry={registry_entry()}")
    step("8b. settings and game unchanged after uninstall", all(fingerprint(g) == before[g] for g in guard))

    # 9. install again, then uninstall through the setup exe
    code, out = run(base + ["--install-dir", prog], sandbox_env)
    ok_install = code == 0 and os.path.isfile(exe)
    code, out = run([setup, "--silent", "--uninstall", "--no-shortcuts", "--install-dir", prog, "--log", log], sandbox_env)
    step("9. uninstall through the setup program", ok_install and code == 0 and not os.path.exists(prog) and registry_entry() is None, open(log).read().strip())
    step("9b. settings and game still unchanged", all(fingerprint(g) == before[g] for g in guard))
    if registry_entry() is not None:                           # never leave an entry behind
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
    report["passed"] = all(s["pass"] for s in report["steps"])
    report["protected_folders_checked"] = [{"path": g, "files": len(before[g])} for g in guard]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    shutil.rmtree(box, ignore_errors=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
