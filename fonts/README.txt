EqUpdater fonts
===============

Settings -> Font offers Skin default, Arial, OpenDyslexic, Friz Quadrata and Object Sans.

Bundled in the installer
  OpenDyslexic-Regular.otf, -Bold.otf, -Italic.otf, -BoldItalic.otf
      OpenDyslexic 0.990 by Abbie Gonzalez. SIL Open Font License 1.1 -- see OpenDyslexic-OFL.txt, which must stay
      next to these files. Reserved Font Name: OpenDyslexic.
      On Windows they are registered privately for the EqUpdater process only (nothing is installed system-wide).

Not bundled: Arial (the system's)
  Arial is Microsoft's and part of every Windows and macOS install, so EqUpdater uses the installed copy. On a system
  without it (some Linux installs) Qt substitutes a similar sans-serif such as Liberation Sans.

Not bundled: downloaded on request into <app data>/v3/fonts (next to settings.json)
  Friz Quadrata  https://font.download/dl/font/friz-quadrata.zip
  Object Sans    https://font.download/dl/font/object-sans.zip
  Choosing one in Settings -> Font while it is missing shows "(download)": clicking it fetches the archive over HTTPS
  (host font.download only, 25 MB limit), keeps only the .ttf/.otf files, saves them in the fonts folder, registers
  them and switches to the font. Nothing is downloaded until the player clicks. Fonts already there, or installed on
  the system, are used without downloading. core/fontstore.py does this.

  These are not EqUpdater's to redistribute and their licences are the foundries' own. Object Sans (Pangram Pangram
  Foundry) is, per the foundry, free for personal use only; Friz Quadrata is URW's. The player downloads them for their
  own use; EqUpdater does not ship, host or re-publish them. Neither is in this repository's packages or in the
  installer.

Legacy: the 2.x (Tk) build still packs fonts/ as it always did, including FrizQuadrata-*.ttf. Those two files are kept in
the source tree for that build only; the 3.0 installer and the 3.0 app ignore them (tools/build_v3.py, qtui/main.py).
