#!/usr/bin/env bash
# Build the standalone "LINQS Layout.app" with PyInstaller.
#
#   bash packaging/build_app.sh            # build into dist/
#   FRESH_VENV=1 bash packaging/build_app.sh   # build in an isolated venv
#
# Produces: dist/LINQS Layout.app  (ad-hoc signed so it runs on Apple Silicon).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

echo "==> native core"
[ -f dxfcore/libdxfcore.dylib ] || bash dxfcore/build.sh

echo "==> icon"
[ -f packaging/LINQSLayout.icns ] || bash packaging/make_icns.sh

PY=python3
if [ "${FRESH_VENV:-0}" = "1" ]; then
  echo "==> isolated build venv"
  python3 -m venv .build-venv
  # shellcheck disable=SC1091
  source .build-venv/bin/activate
  pip install -q --upgrade pip
  pip install -q numpy moderngl PySide6 Pillow pyinstaller
  PY=python
else
  $PY -c "import PyInstaller" 2>/dev/null || pip install -q pyinstaller
fi

echo "==> pyinstaller"
rm -rf build "dist/LINQS Layout.app"
$PY -m PyInstaller --noconfirm --clean packaging/LINQSLayout.spec

echo "==> ad-hoc codesign (required to launch on Apple Silicon)"
codesign --force --deep --sign - "dist/LINQS Layout.app"

echo ""
echo "Built: dist/LINQS Layout.app"
echo "Run:   open 'dist/LINQS Layout.app'   (or drag it to /Applications)"
