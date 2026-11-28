# Skin components (reference)

Every visual element of EqUpdater 3.0 is a **component id**. A skin may define any subset; every id always resolves (to the built-in flat theme at worst), so a skin that sets only a palette is complete. The ids are stable: new ones are added only within a `skin_api` (currently 1), never renamed or removed.

A component takes the optional keys below (`theme.json → components → <id>`).

| Key | Type | Meaning |
|---|---|---|
| `textures` | `{state: "path"}` | image per state: `normal hover pressed disabled selected`. A missing state reuses `normal` |
| `slice` | `[l, t, r, b]` | nine-slice margins in **source pixels**; the corners and edges keep their look, the middle stretches. Drawn 1:1, so author the texture at its display size (about 2× for HiDPI) |
| `colors` / `borderColors` / `textColors` | `{state: colour}` | used when there is no texture; `textColors` always applies |
| `borderWidth` `radius` `opacity` | number | colour-drawn components |
| `padding` | `[l, t, r, b]` | inside the component |
| `minHeight` | px | |
| `font` | name in `fonts` | e.g. `"heading"` |
| `icon` | path | for `icon.*` ids |
| `extends` | component id | inherit another component's keys |

Colours are `#RRGGBB` or `#AARRGGBB` (alpha first, as Qt writes it); `$name` refers to the palette.

## Containers

| id | What | Used by | Textures? |
|---|---|---|---|
| `window` | the window behind everything | main window | colour only |
| `panel` | the framed area of each page (extends nothing) | News, Tweaks, Mods, Addons, MPQ | nine-slice |
| `dialog` | Settings, Diagnostics, Setup, custom mod/addon, questions (extends `panel`) | overlays | nine-slice |
| `sidebar` | the social sidebar (extends `panel`) | friends list | nine-slice |
| `panel.header` | the title line of a panel (extends `panel`) | panel titles | text colour/font |
| `divider` | thin rule between rows and sections | everywhere | colour |
| `scrim` | the dim layer behind a dialog | overlays | colour (use alpha) |
| `menu` | pop-up menu body | Play menu | colour/nine-slice |
| `tooltip` | hover help | everywhere | colour |

## Controls

| id | What | Inherits | States |
|---|---|---|---|
| `button` | ordinary button | | all five |
| `button.primary` | the main action of a dialog | `button` | all |
| `button.play` | PLAY | `button.primary` | all |
| `button.small` | compact button (Close, Change, Browse) | `button` | all |
| `button.link` | text-only button (row actions) | `button` | colour only |
| `splitbutton.arrow` | the arrow beside PLAY | `button.play` | all |
| `menu.item` | a row in a menu | `button` | colour only |
| `nav.tab` | NEWS TWEAKS ADDONS MODS MPQ | | text colours: `normal hover selected` |
| `checkbox` | check boxes | | colours |
| `input` | text fields and the search box | | `normal hover selected disabled` |
| `progress` | progress bar | | colours |
| `scrollbar` | scroll bars | | colours; width from `metrics.scrollbarWidth` |
| `badge` | update counts on tabs | | colours |
| `statusdot` | small status lights | | colours |

## Text

`text.body` `text.dim` `text.heading` `text.title` `text.link` `text.error` `text.new` `text.ok`: use `textColors` and `font`. `text.error` is for failures, `text.ok` for confirmations, `text.new` for the NEW mark, `text.link` for links and attention lines.

## Icons

`icon.logo icon.refresh icon.settings icon.arrow-down icon.discord icon.support icon.download icon.remove icon.release icon.link icon.check icon.update icon.external icon.folder icon.close icon.wrench`. PNG, square, 64×64 recommended (the logo 128×128). A skin that omits one gets the built-in icon.

## Palette names the app uses

`bg panel dialog panelBorder text textDim accent accentLight link danger ok new scrim buttonFace buttonHover buttonPressed buttonDisabled primary primaryHover primaryPressed`. Each has a default. Any other name you add is yours, referenced with `$`.

## Fonts

Names: `body dim heading title button nav`, each `{family, size, bold?}`. Add files with `fontFiles` (`.ttf`/`.otf`, 6 MB each). EqUpdater bundles OpenDyslexic and uses the system's Arial; Friz Quadrata and Object Sans are downloaded on request from Settings > Font (a skin that names them needs them downloaded first or ships its own `fontFiles`). The player can override the font from Settings.

## Layout

`layout.json` (or `theme.json → layout`): `headerHeight logoSize navSpacing contentMargin panelGap footerHeight navRight`. The position of the main areas is otherwise fixed, so every skin has the same features in the same places.

## Background

`background`: `color`, `static` (always give one: it is shown while the animation loads and when it is off), `animation` (animated WebP recommended, GIF or MP4/WebM), `darken` (0 to 1, dims the picture behind the panels), `fadeTop` `fadeBottom` (px). Streamed from the file, paused while minimised.
