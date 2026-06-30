#!/usr/bin/env bash
# Build LINQS Layout and install it into /Applications, in place — no DMG.
#
#   bash packaging/install.sh
#
# Use this to update your own machine after pulling changes. A locally-built app
# isn't quarantined, so it launches without the Gatekeeper right-click dance.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

FRESH_VENV=1 bash packaging/build_app.sh

DEST="/Applications/LINQS Layout.app"
echo "==> installing to $DEST"
rm -rf "$DEST"
ditto "dist/LINQS Layout.app" "$DEST"

echo ""
echo "Installed: $DEST"
echo "Launch:    open '$DEST'"
