#!/usr/bin/env bash
# Build the ultrafast DXF parser into a shared library.
set -euo pipefail
cd "$(dirname "$0")"

CXX="${CXX:-clang++}"
OUT="libdxfcore.dylib"

"$CXX" -std=c++17 -O3 -funroll-loops -fPIC -shared \
    -Wall -Wextra \
    -o "$OUT" dxf_parse.cpp

echo "built $(pwd)/$OUT"
