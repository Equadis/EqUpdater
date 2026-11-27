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
      Object Sans, supplied by the EqUpdater maintainer (family name "Object Sans"; weights
      Regular 400 and Heavy 800, each with a Slanted style). No licence file came with the
      files and the redistribution terms have NOT been confirmed. Confirm the licence (and put
      its text beside the fonts as ObjectSans-LICENSE.txt) before any public installer ships
      them. tools/beta_check.py warns until that file is present.
