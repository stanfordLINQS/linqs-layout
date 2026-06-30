#!/usr/bin/env python3
"""Lightning-fast layout viewer for flattened ASCII DXF layouts.

    python3 view_dxf.py layout.dxf              # interactive GPU viewer
    python3 view_dxf.py layout.dxf --png out.png   # headless render to PNG

Interactive controls: scroll to zoom at the cursor, left-drag to pan, click a
layer in the right-hand panel to show/hide it, press R to reset the view.
"""

from __future__ import annotations

import argparse
import sys
import time

from pydxf import DxfLayout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="path to a .dxf file")
    ap.add_argument("--png", metavar="OUT",
                    help="render to PNG headlessly (no window) and exit")
    ap.add_argument("-W", "--width", type=int, default=1600)
    ap.add_argument("-H", "--height", type=int, default=1400)
    args = ap.parse_args()

    t0 = time.perf_counter()
    layout = DxfLayout(args.path)
    print(f"loaded {layout.n_polylines:,} polylines + {layout.n_circles:,} circles, "
          f"{layout.n_layers} layers in {(time.perf_counter() - t0) * 1e3:.0f} ms")

    if args.png:
        from viewer.offscreen import render_png
        t1 = time.perf_counter()
        render_png(layout, args.png, size=(args.width, args.height))
        print(f"rendered {args.width}x{args.height} -> {args.png} "
              f"in {(time.perf_counter() - t1) * 1e3:.0f} ms")
        return 0

    from viewer.app import run
    return run(layout)


if __name__ == "__main__":
    sys.exit(main())
