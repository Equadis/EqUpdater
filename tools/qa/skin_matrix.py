"""Render every skin with the account chip and the friends sidebar, for people to look at.

    python tools/qa/skin_matrix.py [--out bench-results/shots/matrix] [--width 1000]

Offscreen, a throwaway data folder, a pretend signed-in account (a stand-in provider, only to
show the signed-in chip) and some friends with made-up presence from the QA fixture. Writes one PNG
per skin and a contact sheet. Nothing here is a real account or real presence.
"""

import argparse
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "bench-results", "shots", "matrix"))
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--height", type=int, default=700)
    ap.add_argument("--scale", default="1")
    ap.add_argument("--page", default="mods")
    ap.add_argument("--open", default="", help="settings: open Settings before each shot")
    ap.add_argument("--skins", default="", help="comma separated skin ids (default: all)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    data = tempfile.mkdtemp(prefix="equ3-matrix-")
    pres = os.path.join(data, "presence.json")
    json.dump({"thrall": {"game": "in_game", "launcher": "online", "realm": "nzoth"},
               "jaina": {"game": "in_game", "launcher": "online", "realm": "yshaarj"},
               "sylvanas": {"launcher": "online"},
               "arthas": {"launcher": "away"},
               "illidan": {"launcher": "dnd"},
               "malfurion": {"game": "offline", "launcher": "offline"}}, open(pres, "w"))
    os.environ.update(EQU3_DATA_DIR=data, EQU3_NO_SETUP="1", EQUPDATER_NO_LANGUAGE_PROMPT="1", EQUPDATER_OFFLINE="1",
                      EQU3_OFFLINE="1", EQU3_PRESENCE_FIXTURE=pres, EQU3_CREDSTORE="memory",
                      QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=str(a.scale), EQU3_ALLOW_MULTI="1")
    os.environ.pop("QT_QUICK_BACKEND", None)
    os.environ["QT_QUICK_BACKEND"] = "software"
    from PySide6.QtCore import QCoreApplication, QUrl
    from equpdater.core import account as A
    from equpdater.qtui import main as m

    app, engine, p = m.build([])
    engine.load(QUrl.fromLocalFile(os.path.join(m.QML_DIR, "Main.qml")))
    win = engine.rootObjects()[0]
    win.setProperty("width", a.width)
    win.setProperty("height", a.height)

    def pump(ms):
        end = time.time() + ms / 1000
        while time.time() < end:
            QCoreApplication.processEvents()
            time.sleep(0.002)

    class Pretend(A.AuthProvider):
        id, name, available = "pretend", "Pretend", True

        def start(self, open_browser, cancel, timeout=180.0):
            return A.Tokens("a", "r", time.time() + 3600)

        def profile(self, tokens):
            return A.Profile("Thrallfan", "1", "")
    p["account_service"].provider = Pretend()
    p["account"].changed.emit()
    p["account"].signIn()
    pump(600)
    for n in ("Thrall", "Jaina", "Sylvanas", "Arthas", "Illidan", "Malfurion", "Tyrande"):
        p["social_service"].add_friend(n)
    p["social_service"].toggle_favourite(p["friend_book"].all()[0].id)
    p["social"].start()
    p["app"].tab = a.page
    paths = []
    for sid in (a.skins.split(",") if a.skins else sorted(p["manager"].skins)):
        p["settings_bridge"].setSkin(sid)
        if a.open:
            p["app"].debugAction.emit(a.open)
        p["presence_service"].poll()
        pump(900)
        path = os.path.join(a.out, f"{sid}.png")
        p["app"].screenshotRequested.emit(path)
        pump(500)
        paths.append(path)
        print(path)
    from PIL import Image, ImageDraw
    imgs = [Image.open(x).convert("RGB") for x in paths]
    w, h = imgs[0].size
    cols = 2
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows), (20, 20, 20))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
        ImageDraw.Draw(sheet).text(((i % cols) * w + 8, (i // cols) * h + 4), os.path.basename(paths[i])[:-4], fill=(255, 255, 0))
    sheet.save(os.path.join(a.out, "contact-sheet.png"))
    p["tasks"].shutdown()


if __name__ == "__main__":
    main()
