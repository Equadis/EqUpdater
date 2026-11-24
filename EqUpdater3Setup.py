"""Entry point of the EqUpdater 3 beta installer (EqUpdater3-vX-Windows-Installer.exe).

Built by ``python tools/build_v3.py --setup``; see equpdater/installer3.py.
"""

import sys

from equpdater.installer3 import main

if __name__ == "__main__":
    sys.exit(main())
