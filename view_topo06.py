#!/usr/bin/env python3
"""Open the GPU layout viewer on TOPO06.dxf — just press ▶ Run.

No terminal arguments needed. This is the one-click equivalent of:

    python3 view_dxf.py TOPO06.dxf

Paths are resolved relative to this file, so it works regardless of the working
directory the Run button uses. Set RENDER_PNG below to a filename if you'd rather
produce a PNG than open a window.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DXF = HERE / "TOPO06.dxf"
RENDER_PNG: str | None = None        # e.g. "topo06.png" to render headlessly instead

# Ensure this folder's packages (pydxf, viewer) import no matter the cwd.
sys.path.insert(0, str(HERE))


def main() -> int:
    if not DXF.exists():
        print(f"Reference layout not found:\n  {DXF}")
        return 1

    from pydxf import DxfLayout

    print(f"loading {DXF.name} …")
    layout = DxfLayout(str(DXF))
    print(f"  {layout.n_polylines:,} polylines, {layout.n_circles:,} circles, "
          f"{layout.n_layers} layers")

    if RENDER_PNG:
        from viewer.offscreen import render_png
        out = str(HERE / RENDER_PNG)
        render_png(layout, out)
        print(f"  rendered -> {out}")
        return 0

    try:
        from viewer.qt_app import run
    except ImportError as e:
        print(f"\nViewer dependencies missing ({e}).\n"
              f"Install them with:\n  pip install -r {HERE / 'requirements-viewer.txt'}")
        return 1

    print("  opening viewer  (scroll = zoom at cursor, drag = pan, "
          "click layers on the right, R = reset)")
    return run(layout)


if __name__ == "__main__":
    raise SystemExit(main())
