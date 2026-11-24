# Official skins: assets and how they are made

EqUpdater ships seven skins. `python tools/skins/build_poc_assets.py` (Bubbles, Minimal Dark) and `python tools/skins/build_official_skins.py` (Minimal, Minimal Light, Classic) rebuild every image and `theme.json` of the skins they own, so the art can be swapped and the skins regenerated. Players never run them: a skin is only files. Screenshots: `bench-results/shots/skin-*.png`.

| Skin | Look | Animated | Textures | Disk |
|---|---|---|---|---|
| **Bubbles** (default) | deep blue ocean, gold accent, green PLAY | ocean, 622 frames | buttons (9-slice) | about 4 MB |
| **Classic (2004)** | ornate bronze frame, steel buttons, gold Friz Quadrata type, the 2004 login scene | login scene, 288 frames | frame, 8 buttons, stone backdrop | about 5.6 MB |
| **Minimal** | graphite, teal accent | none | none (colours) | 40 KB |
| **Minimal Dark** | near-black blue, blue accent | none | none | 40 KB |
| **Minimal Light** | white and light grey, blue accent | none | none | 40 KB |

The Minimal skins are the proof that a skin can be colours and icons only. Their text contrast is checked by a test (body 7:1, dim 4.5:1, accent 3:1, white on the PLAY button 4.5:1).

## Classic (2004) assets

Source art lives in `tools/skins/source/classic/` (`border.png` 652×471, `button_border.png` 885×167, `blue.png`, `grey.png` 1920×1080, `wow2004.gif` 800×450). The builder:

| Output | From | Treatment |
|---|---|---|
| `panels/frame.png` 326×235 | `border.png` | the flat grey middle (flood-filled from the centre) becomes a translucent dark fill (`#0914 18` alpha 226); halved so a nine-slice pixel is a logical pixel; `slice` 56 on every side keeps the corner ornaments whole |
| `buttons/normal-<state>.png`, `play-<state>.png` 221×41 | `button_border.png` | the white inside (flood-filled) becomes a vertical gradient per state; hover gets a gold tint, pressed is darker, disabled is desaturated and dimmed; quartered; `slice` 10 |
| `backgrounds/stone-blue.webp` | `blue.png` | 1600×900, WebP q86, the still shown while the animation loads or is off |
| `backgrounds/login-2004.webp` | `wow2004.gif` | animated WebP q78, frame timings kept (GIF to WebP, same method as Bubbles), about 5 MB |
| `icons/*.png` | drawn by `build_poc_assets.py` in gold; Discord and coffee from the files 2.x ships | |

`grey.png` is in the source folder but not used by any skin yet.

**Licence of the Classic artwork.** The manifest says *"Artwork supplied by the project owner; confirm redistribution rights before release."* The frame, button frame and login scene look like game-derived art. They must be confirmed as redistributable (or replaced) before any public build; the skin is the only thing that needs to change.

## Sizes and limits (enforced by the loader)

PNG/WebP/JPEG images 12 MB each; animation 40 MB; fonts 6 MB; every path relative and inside the skin folder; the file's header must match its extension. A test keeps the five official skins under 14 MB together and every animation under 12 MB.

## Making a new skin from the starter

`docs/v3/starter-skin/` is a complete, colours-only skin (it loads with no problems; a test checks it). Copy it to `<app data>/v3/skins/<id>/`, change `id` and `name`, then edit `theme.json` (see `COMPONENTS.md`) and add images where you want textures.


## Turtle WoW and Battle.net

Built by `tools/skins/build_social_skins.py`. They take their *look* from the two launchers (colour, type, proportions, how navigation and buttons behave) but **none of either launcher's artwork is used**: the landscape (sky, moon, ridges, tower, forest), the glow background, the frames and the buttons are drawn from scratch in the script, so there is no redistribution question as there is for Classic (2004). Both are full skins: their own panel, dialog, sidebar, button (four states, ordinary and PLAY), input, checkbox, menu, tooltip, progress, scrollbar, divider and badge looks, their own type and spacing, and their own `layout`.

Layout keys they use beyond the common ones: `navAlign: "left"` and `navLeftMargin` (navigation beside the logo, Battle.net), `sidebarWidth` and `sidebarDefault: "expanded"|"collapsed"` (the social sidebar).

"Turtle WoW" and "Battle.net" name the look being echoed. They are other companies' products and names; EqUpdater is not affiliated with or endorsed by either, and both skins keep the EqUpdater name and logo.
