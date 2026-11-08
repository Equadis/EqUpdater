# Classic (2004) theme artwork

Runtime artwork for the *Classic (2004)* theme (`equpdater/theme.py`,
`equpdater/skin.py`). Nothing here was generated: every file is a crop,
mirror, feather, slice or classical (non-generative) inpaint of the supplied
artwork pack, rebuilt by `tools/build_classic_assets.py`.

| File | Source | Treatment |
|---|---|---|
| `corner_*.png`, `frame_*_edge.png` | 2004 installer screenshot crops (`04_extracted_installer_details`) | trimmed to the frame rows/columns; corners get an alpha that fades into the scene |
| `button_*cap/center*.png`, `small_button_*` | `05_prototype_9slice` (from the installer's buttons) | unaltered; stretched as three slices at run time, states by recolouring |
| `blizzard_badge.png` | installer crop | unaltered, currently unused |
| `scene.png`, `side_left.png`, `side_right.png` | `03_visual_target` concept | UI/text painted out (OpenCV Telea inpaint + haze), sky mirrored, statues feathered |
| `plaque.png` | `03_visual_target` concept | wordmark/icon replaced by stone grain from the button sprite; the wordmark is drawn live |
| `stone_tile.png` | button stone grain | tiled, desaturated (the composition script's recipe) |

## Redistribution

Not cleared. These derive from Blizzard Entertainment's *World of Warcraft*
installer artwork (2004) and from a concept image of unknown provenance
(the scenery, statues and plaque). Get permission, or replace them with
licensed art, before publishing a release that bundles them. See the
"Remaining issues" section of the pull request.
