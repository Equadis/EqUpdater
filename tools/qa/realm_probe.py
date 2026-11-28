"""Read-only look at what the launcher can learn about a running game client, and (with --learn) a way for a
TESTER to record which realm a world server belongs to.

    python tools/qa/realm_probe.py [--game D:/OctoWoW]
    python tools/qa/realm_probe.py --learn nzoth        # you are in the world on N'Zoth right now

Prints: whether WoW.exe is running, its established connections (remote address only), the login server's
addresses from realmlist.wtf, the world-server endpoints that follow, the realm the launcher would report, and for
comparison what WTF/Config.wtf says and when it was written. Nothing is changed in the game or its folder.
``--learn`` writes ``<data>/v3/realm_endpoints.json`` ({"host:port": realm id}) on this computer only: you are
asserting that the world server you are connected to is that realm.
"""

import argparse
import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from equpdater.core import gamewatch as G          # noqa: E402
from equpdater.core import realms as R             # noqa: E402
from equpdater.core import settings as S           # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="")
    ap.add_argument("--learn", default="", help="a realm id (nzoth, yshaarj, cthun)")
    a = ap.parse_args()
    game = a.game or S.open_settings().get("game_dir") or ""
    print("game folder:", game)
    print("running:", G.game_running(game))
    conns = G.game_connections(game)
    hosts = G.login_hosts_of(game)
    world = G.world_endpoints(conns, G.LOGIN_PORTS, hosts)
    print("established connections:", conns)
    print("login server addresses:", sorted(hosts))
    print("world-server endpoints:", world)
    known_path = os.path.join(S.data_dir(), "realm_endpoints.json")
    known = G.load_known(known_path)
    realms = [R.Realm(i, n) for i, n, _k in R.FALLBACK]
    print("known endpoints:", known)
    print("realm the launcher would report:", repr(G.realm_for(realms, world, known)) + "  (empty means: not known, not guessed)")
    cfg = os.path.join(game, "WTF", "Config.wtf")
    try:
        text = open(cfg, encoding="utf-8", errors="ignore").read()
        m = re.search(r'SET\s+realmName\s+"([^"]+)"', text)
        when = datetime.datetime.fromtimestamp(os.path.getmtime(cfg))
        print(f"for comparison only, Config.wtf realmName={m.group(1) if m else None!r}, last written {when:%Y-%m-%d %H:%M:%S} (not used)")
    except OSError:
        print("Config.wtf: not readable")
    if a.learn:
        if a.learn not in {r.id for r in realms}:
            raise SystemExit(f"unknown realm {a.learn!r}")
        if len(world) != 1:
            raise SystemExit(f"expected exactly one world-server endpoint, found {len(world)}: not recording anything")
        known[f"{world[0][0]}:{world[0][1]}"] = a.learn
        os.makedirs(os.path.dirname(known_path), exist_ok=True)
        with open(known_path, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1)
        print(f"recorded {world[0][0]}:{world[0][1]} as {a.learn} in {known_path}")


if __name__ == "__main__":
    main()
