"""DEVELOPMENT ONLY: run several launchers on one computer, each an isolated profile signed in under its own
username, all talking to one social test backend. For testing friends and presence without several PCs.

    python tools/qa/social_instances.py --names Alice Bobby            # also starts a local backend
    python tools/qa/social_instances.py --names Alice --url http://192.168.1.20:8787   # an existing backend
    python tools/qa/social_instances.py --set Alice=in_game:nzoth      # simulate: Alice is playing on N'Zoth
    python tools/qa/social_instances.py --set Alice=launcher           # ... and the game closes

Each profile lives in %TEMP%/equ3-social/<name> (settings, friends cache, an activity.json that stands in for a
running game). ``--set`` writes that file; the launcher reads it on its next presence round (within ~5 seconds).
The simulation exists only in a development run (EQU3_SIM_ACTIVITY is ignored by a packaged build) and is a
separate code path from the real game detection (equpdater/core/gamewatch.py).

Nothing here is used by, or shipped in, the installer.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.join(tempfile.gettempdir(), "equ3-social")


def profile_dir(name):
    return os.path.join(BASE, name)


def activity_file(name):
    return os.path.join(profile_dir(name), "activity.json")


def write_activity(name, spec):
    """spec: 'launcher' or 'in_game' or 'in_game:<realm>' (nzoth | yshaarj | cthun)."""
    kind, _, realm = spec.partition(":")
    if kind not in ("launcher", "in_game"):
        raise SystemExit(f"unknown activity {kind!r}: use launcher or in_game[:realm]")
    os.makedirs(profile_dir(name), exist_ok=True)
    with open(activity_file(name), "w", encoding="utf-8") as f:
        json.dump({"activity": kind, "realm": realm}, f)


def launcher_env(name, url):
    env = dict(os.environ)
    env.update(EQU3_DATA_DIR=profile_dir(name), EQU3_SOCIAL_URL=url, EQU3_DEV_LOGIN=name, EQU3_PROFILE=name,
               EQU3_SIM_ACTIVITY=activity_file(name), EQU3_ALLOW_MULTI="1", EQU3_NO_SETUP="1",
               EQUPDATER_NO_LANGUAGE_PROMPT="1")
    return env


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--names", nargs="*", default=[])
    ap.add_argument("--url", default="")
    ap.add_argument("--set", nargs="*", default=[], metavar="NAME=ACTIVITY")
    a = ap.parse_args()
    for item in a.set:
        who, _, spec = item.partition("=")
        write_activity(who, spec or "launcher")
        print(f"{who}: {spec or 'launcher'}  ({activity_file(who)})")
    if not a.names:
        return 0
    os.makedirs(BASE, exist_ok=True)
    procs, server = [], None
    url = a.url
    if not url:
        server = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "socialserver", "server.py"), "--port", "0",
                                   "--db", os.path.join(BASE, "server.sqlite")],
                                  stdout=subprocess.PIPE, text=True, cwd=ROOT)
        # the server prints its address on the first line
        line = server.stdout.readline().strip()
        url = line.split(" on ", 1)[1].split()[0] if " on " in line else ""
        print(line)
    for name in a.names:
        write_activity(name, "launcher")
        procs.append(subprocess.Popen([sys.executable, "-m", "equpdater.qtui"], env=launcher_env(name, url), cwd=ROOT))
        print(f"{name}: launcher started, profile {profile_dir(name)}")
        time.sleep(1.5)
    print("Close the launcher windows to finish. Use --set NAME=in_game:nzoth in another terminal to simulate a game.")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        if server:
            server.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
