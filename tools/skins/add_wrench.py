"""Add the wrench icon (Troubleshooting) to the official skins without rebuilding anything else: drawn in each
skin's own icon colour (read from its settings icon) and registered as icon.wrench in its theme.json."""
import json
from collections import Counter
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_poc_assets as poc  # noqa: E402

for skin in sorted(os.listdir(poc.SK)):
    icons = os.path.join(poc.SK, skin, "icons")
    theme_path = os.path.join(poc.SK, skin, "theme.json")
    if not os.path.isfile(os.path.join(icons, "settings.png")) or not os.path.isfile(theme_path):
        continue
    px = Image.open(os.path.join(icons, "settings.png")).convert("RGBA")
    solid = Counter(tuple(px.getpixel((x, y))) for x in range(px.width) for y in range(px.height) if px.getpixel((x, y))[3] == 255)
    colour = solid.most_common(1)[0][0] if solid else (255, 255, 255, 255)      # the icon set is one flat colour
    poc.icon_wrench(colour).save(os.path.join(icons, "wrench.png"))
    with open(theme_path, encoding="utf-8") as f:
        raw = f.read()
    theme = json.loads(raw)
    theme["components"]["icon.wrench"] = {"icon": "icons/wrench.png"}
    with open(theme_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(theme, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(skin, colour)
