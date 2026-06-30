#!/usr/bin/env bash
# Update the installed LINQS Layout to the latest release — no manual DMG dance.
#
# For anyone with access to the (private) repo. Requires the GitHub CLI:
#     brew install gh && gh auth login
#
# Then, to update at any time:
#     bash <(gh api repos/stanfordLINQS/linqs-layout/contents/packaging/update.sh \
#            -H "Accept: application/vnd.github.raw")
#
# (or download this script once and run `bash update.sh`).
set -euo pipefail
REPO="stanfordLINQS/linqs-layout"
APP="/Applications/LINQS Layout.app"

command -v gh >/dev/null || {
    echo "GitHub CLI not found. Install it:  brew install gh && gh auth login" >&2
    exit 1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "==> downloading the latest LINQS Layout release…"
gh release download --repo "$REPO" --pattern '*.dmg' --dir "$TMP" --clobber

DMG="$(ls "$TMP"/*.dmg | head -1)"
echo "==> mounting $(basename "$DMG")…"
MNT="$(hdiutil attach "$DMG" -nobrowse -noverify -readonly | grep /Volumes | awk -F'\t' '{print $NF}')"

echo "==> installing to $APP…"
rm -rf "$APP"
ditto "$MNT/LINQS Layout.app" "$APP"
hdiutil detach "$MNT" >/dev/null
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true   # launch without the Gatekeeper prompt

VER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo '?')"
echo ""
echo "Updated to LINQS Layout $VER."
echo "Launch:  open '$APP'"
