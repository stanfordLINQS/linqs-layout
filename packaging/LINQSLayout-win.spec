# PyInstaller spec for the LINQS Layout Windows app (one-folder build).
#   pyinstaller --noconfirm --clean packaging/LINQSLayout-win.spec   (run from repo root)
#
# Produces: dist/LINQS Layout/LINQS Layout.exe  (+ the bundled runtime & dxfcore.dll)
# Wrap that folder into an installer with packaging/windows/installer.iss (Inno Setup).
import os
import re

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
with open(os.path.join(ROOT, "viewer", "__init__.py")) as _f:
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _f.read()).group(1)

# Bundle the native DXF core next to the pydxf package (loader resolves it as a
# sibling 'dxfcore' dir via sys._MEIPASS), plus moderngl's GL-context backend.
datas = [(os.path.join(ROOT, "dxfcore", "dxfcore.dll"), "dxfcore")]
binaries = []
hiddenimports = ["glcontext", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"]
for pkg in ("moderngl", "glcontext", "mapbox_earcut"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(ROOT, "app_main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "PyQt6", "PyQt5", "PySide2", "shiboken2",   # avoid clashing Qt bindings
        "matplotlib", "tkinter", "scipy", "pandas", "IPython",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LINQS Layout",
    console=False,                 # windowed (no console) GUI app
    icon=os.path.join(SPECPATH, "icon.ico"),
    version_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LINQS Layout")
