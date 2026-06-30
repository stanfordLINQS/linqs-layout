#!/usr/bin/env bash
# Package dist/LINQS Layout.app into a drag-to-Applications DMG installer.
#
#   bash packaging/make_dmg.sh        # -> dist/LINQS-Layout.dmg
#
# Run packaging/build_app.sh first.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

APP="dist/LINQS Layout.app"
DMG="dist/LINQS-Layout.dmg"
[ -d "$APP" ] || { echo "missing $APP — run packaging/build_app.sh first" >&2; exit 1; }

STAGE="$(mktemp -d)/LINQS Layout"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"     # drag-to-install target

rm -f "$DMG"
hdiutil create -volname "LINQS Layout" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG"
echo ""
echo "Built: $DMG"
