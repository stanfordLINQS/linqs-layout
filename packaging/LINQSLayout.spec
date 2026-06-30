# PyInstaller spec for the LINQS Layout macOS app.
#   pyinstaller --noconfirm --clean packaging/LINQSLayout.spec   (run from repo root)
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# Bundle the native DXF core next to the pydxf package (loader resolves it as a
# sibling 'dxfcore' dir), plus moderngl's GL-context backend.
datas = [(os.path.join(ROOT, "dxfcore", "libdxfcore.dylib"), "dxfcore")]
binaries = []
hiddenimports = ["glcontext", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"]
for pkg in ("moderngl", "glcontext"):
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
        "pytest", "mapbox_earcut",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LINQS Layout",
    console=False,
    argv_emulation=False,          # Qt handles macOS file-open via QFileOpenEvent
    target_arch=None,              # current arch (arm64)
)
coll = COLLECT(exe, a.binaries, a.datas, name="LINQS Layout")

app = BUNDLE(
    coll,
    name="LINQS Layout.app",
    icon=os.path.join(SPECPATH, "LINQSLayout.icns"),
    bundle_identifier="edu.stanford.linqs.layout",
    info_plist={
        "CFBundleName": "LINQS Layout",
        "CFBundleDisplayName": "LINQS Layout",
        "CFBundleShortVersionString": "1.0.3",
        "CFBundleVersion": "1.0.3",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "DXF Layout",
                "CFBundleTypeExtensions": ["dxf"],
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Default",
            }
        ],
    },
)
