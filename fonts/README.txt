EqUpdater fonts
===============

These fonts ship with EqUpdater, so every font in Settings -> Font works on a
fresh install. On Windows they are registered privately for the EqUpdater
process only (AddFontResourceEx with FR_PRIVATE): nothing is installed
system-wide and no administrator rights are needed.

  OpenDyslexic-Regular.otf, -Bold.otf, -Italic.otf, -BoldItalic.otf
      OpenDyslexic 0.990 by Abbie Gonzalez.
      SIL Open Font License 1.1 -- see OpenDyslexic-OFL.txt, which must stay
      next to these files. Reserved Font Name: OpenDyslexic.

  FrizQuadrata-Regular.ttf, FrizQuadrata-BoldItalic.ttf
      Friz Quadrata (URW Software, Copyright 1994 by URW).
      Included with the EqUpdater maintainer's permission. It is not covered
      by the Octo Updater licence or by any licence of EqUpdater's own, and
      is not licensed for reuse outside EqUpdater.

  Arial
      Not included: it is Microsoft's, and part of every Windows install.

EqUpdater also still picks up font archives the user places beside it or in
Downloads / Desktop / Documents (friz*.zip, arial*.zip, opendyslexic*.zip),
copying their .ttf/.otf files into %LOCALAPPDATA%\EqUpdater\fonts.

  ObjectSansRegular.otf, ObjectSansSlanted.otf, ObjectSansHeavy.otf, ObjectSansHeavySlanted.otf
      Object Sans by Alex Slobzheninov, published by Pangram Pangram Foundry. **Not licensed for redistribution
      here.** The files supplied (Regular, Slanted, Heavy, Heavy Slanted) are the foundry's free trial set, which the
      foundry's FAQ and licence terms (https://pangrampangram.com/products/object-sans) allow for personal use
      only: shipping them inside a public installer, or in any commercial product, needs a paid licence from the
      foundry (a "Video Game" licence is listed separately). No licence was supplied with the files. So:
        * the build (tools/build_v3.py) leaves these files OUT of every package unless a licence text is placed
          beside them as ObjectSans-LICENSE.txt, or a LOCAL build is made with --with-unlicensed-fonts;
        * tools/beta_check.py FAILS if a package contains them without that file;
        * a player who holds a licence can put the files in the fonts folder (in the v3 folder) of EqUpdater's app data (next to settings.json): the font then
          appears in Settings > Font; without the files the choice is not offered.
      To distribute it: buy the appropriate licence, save its text as fonts/ObjectSans-LICENSE.txt, rebuild.
