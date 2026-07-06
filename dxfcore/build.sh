#!/usr/bin/env bash
# Build the ultrafast DXF parser into a shared library (macOS / Linux).
# On Windows use build.bat (MSVC) or cmake; see CMakeLists.txt.
set -euo pipefail
cd "$(dirname "$0")"

CXX="${CXX:-clang++}"
case "$(uname -s)" in
    Darwin) OUT="libdxfcore.dylib" ;;   # pydxf/loader.py expects this name
    *)      OUT="libdxfcore.so"    ;;   # Linux
esac

"$CXX" -std=c++17 -O3 -funroll-loops -fPIC -shared \
    -Wall -Wextra \
    -o "$OUT" dxf_parse.cpp

echo "built $(pwd)/$OUT"
