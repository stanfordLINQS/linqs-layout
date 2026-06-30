#!/usr/bin/env bash
# Convert a square PNG (ideally 1024x1024) into a macOS .icns icon.
#
#   ./make_icns.sh [icon.png] [LINQSLayout.icns]
#
# Defaults to packaging/icon.png -> packaging/LINQSLayout.icns.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/icon.png}"
OUT="${2:-$HERE/LINQSLayout.icns}"

[ -f "$SRC" ] || { echo "source PNG not found: $SRC" >&2; exit 1; }

WORK="$(mktemp -d)/icon.iconset"
mkdir -p "$WORK"

# size:filename pairs required by iconutil (base + @2x retina variants)
for entry in \
    16:icon_16x16 32:icon_16x16@2x \
    32:icon_32x32 64:icon_32x32@2x \
    128:icon_128x128 256:icon_128x128@2x \
    256:icon_256x256 512:icon_256x256@2x \
    512:icon_512x512 1024:icon_512x512@2x ; do
  px="${entry%%:*}"; name="${entry##*:}"
  sips -z "$px" "$px" "$SRC" --out "$WORK/${name}.png" >/dev/null
done

iconutil -c icns "$WORK" -o "$OUT"
echo "wrote $OUT"
