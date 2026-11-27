# Skinning EqUpdater 3.0

EqUpdater is a launcher that happens to be skinnable. It is **not** a skin editor: there are no colour pickers, asset editors or marketplace. A skin is a folder you edit with your own tools.

**Making a skin**
1. Copy a folder from `skins/` (installed with EqUpdater) to your skins folder (Settings → Skin → *Open skins folder*; it is `<app data>/v3/skins/`).
2. Change `id` and `name` in `manifest.json`.
3. Replace images / edit `theme.json`.
4. Settings → Skin: pick it. Changes apply live; *Open skins folder → edit → reopen Settings* re-reads the folder. *Restore default skin* returns to Bubbles.

No CSS, QML, JavaScript or Python is read from a skin — only JSON, images, fonts and one background animation.

## Folder
```
my-skin/
  manifest.json   required
  theme.json      palette, fonts, metrics, components, background
  layout.json     optional: positions of the fixed layout (merged into theme.layout)
  preview.png     optional
  backgrounds/ buttons/ panels/ icons/ fonts/   (any folder names; paths are relative)
```
`manifest.json`: `{"id":"my-skin","name":"My Skin","version":"1.0","skin_api":1,"author":"…","license":"…","description":"…"}`. `id`: lowercase letters, digits, dashes. A skin needing a newer `skin_api` than the app reads is refused with a message. Anything *missing or wrong* inside a skin falls back **per item** to the built-in flat theme, and Settings lists what was wrong.

## theme.json
```json
{
  "palette":   { "bg": "#06132d", "accent": "#c99a3d", "panel": "#e6081a35" },
  "fonts":     { "body": {"family":"Arial","size":13}, "heading": {"family":"Arial","size":15,"bold":true} },
  "fontFiles": ["fonts/MyFont.ttf"],
  "metrics":   { "scrollbarWidth": 6, "animationMs": 120 },
  "background":{ "color": "$bg", "static": "backgrounds/still.png",
                 "animation": "backgrounds/loop.webp", "darken": 0.92,
                 "fadeTop": 120, "fadeBottom": 200 },
  "components":{ "button": { … see below … } }
}
```
* Colours: `#RRGGBB` or `#AARRGGBB` (alpha first, as Qt writes it). `$name` refers to the palette. 
* Sizes are logical pixels (HiDPI is automatic).
* Palette names the app itself uses (`bg panel dialog panelBorder text textDim accent accentLight link danger ok new scrim buttonFace buttonHover buttonPressed buttonDisabled primary primaryHover primaryPressed`) already have defaults; add your own names freely and refer to them with `$`.

### A component
Every visual element is a **component id** (below). Each takes the same optional keys:

| Key | Meaning |
|---|---|
| `textures` | `{state: "path.png"}` for `normal hover pressed disabled selected`. A missing state reuses `normal`. |
| `slice` | `[left, top, right, bottom]` nine-slice margins in *source pixels*: corners/edges keep their look, the middle stretches. |
| `colors` / `borderColors` / `textColors` | `{state: colour}`. Used when there is no texture (colours only is a complete skin) — and `textColors` always. |
| `borderWidth`, `radius`, `opacity` | for colour-drawn components |
| `padding` | `[left, top, right, bottom]` inside the component |
| `minHeight` | px |
| `font` | name from `fonts`, e.g. `"heading"` |
| `icon` | image path for `icon.*` components |
| `extends` | another component id to inherit from (defaults below) |

Inheritance defaults: `panel.header→panel`, `button.primary→button`, `button.play→button.primary`, `button.small→button`, `button.link→button` (no texture), `menu.item→button` (no texture), `splitbutton.arrow→button.play`. A child's keys override the parent's. A skin that sets only a palette is complete.

### Component ids (stable; additions only within a `skin_api`)
`window panel panel.header divider scrim button button.primary button.play button.small button.link splitbutton.arrow nav.tab checkbox menu menu.item progress scrollbar badge tooltip statusdot text.body text.dim text.heading text.title text.link text.error text.new text.ok icon.refresh icon.settings icon.arrow-down icon.discord icon.support icon.logo icon.external icon.update`

States, scaling and sizes — images
* PNG, WebP, JPEG for images; **PNG/WebP with alpha** for anything not rectangular. Max 12 MB each.
* Nine-slice textures: make the source about 2× the displayed size for crisp HiDPI, keep corners inside the `slice` margins, avoid gradients across the stretched centre unless vertical.
* Icons: square, 64×64 recommended.
* Fonts: `.ttf`/`.otf` ≤ 6 MB via `fontFiles`, then use the family name in `fonts`. Check the font licence.
* Background animation: animated **WebP** (recommended; 622-frame ocean = 2.7 MB at q85, 40 dB PSNR vs the old 22.6 MB GIF), GIF, or MP4/WebM video. ≤ 40 MB. It is streamed from the file (not decoded into memory) and pauses when the window is minimised (or unfocused, if the player chooses). Always provide `background.static` as the fallback shown when animation is off. Loop it seamlessly (first frame ≈ last frame).

## Safety
Files are only read if: path is relative and stays inside the skin folder (no `..`, absolute paths, drive letters, links to elsewhere); extension is allowed; size under the limit; images carry the right file header. Anything else is skipped and reported. A skin cannot run code, read the game folder, or reach the network.

## Layout
`layout.json` (or `theme.json → layout`) may set: `headerHeight logoSize navSpacing contentMargin panelGap footerHeight navRight`, `navAlign` (`left` puts the tabs beside the logo) with `navLeftMargin`, and `sidebarWidth` / `sidebarDefault` (`expanded` or `collapsed`) for the social sidebar. Positions of the main areas are otherwise fixed so every skin has the same features.

## Troubleshooting
Settings → Skin shows ⚠ and the list of problems for the selected skin. Typical: *file not found* (check case and folder), *not the image type its name says* (re-export), *leaves the skin folder* (use relative paths without `..`), *skin_api N* (the skin is newer than the app).

## More
* `COMPONENTS.md`: every component id, its keys, states and what uses it.
* `SKIN_ASSETS.md`: the six official skins, how their art is made, sizes and limits.
* `starter-skin/`: a complete colours-only skin to copy.
* The six official skins, in this order: Bubbles (default), Classic (2004), Blizzard, Turtle, Minimal (Dark), Minimal (Light) (ids `bubbles`, `classic-2004`, `battle-net`, `turtle-wow`, `minimal-dark`, `minimal-light`; the ids did not change when the names did, and the removed `minimal` becomes `minimal-dark`). Settings → Skin lists them first, then yours.
