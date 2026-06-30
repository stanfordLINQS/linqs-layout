#!/usr/bin/env python3
"""Entry point for the packaged LINQS Layout application.

Run directly (`python3 app_main.py [file.dxf]`) or as the PyInstaller entry
script. With no argument it shows an Open dialog; double-clicked DXF files and
"Open With" are handled via the macOS file-open event.
"""

import sys

from viewer.app import main

if __name__ == "__main__":
    sys.exit(main())
