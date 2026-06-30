"""Print the single-source __version__ from viewer/__init__.py.

Used by build scripts (notably build_win.bat) that need the version string but
can't easily parse it inline. Kept trivial and dependency-free.
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_ROOT, "viewer", "__init__.py")) as _f:
    print(re.search(r'__version__\s*=\s*"([^"]+)"', _f.read()).group(1))
