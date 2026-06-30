#!/usr/bin/env python3
"""Inspect a flattened ASCII DXF layout: what's inside and where it sits.

Usage:
    python3 inspect_dxf.py layout.dxf
    python3 inspect_dxf.py layout.dxf --json
"""

from __future__ import annotations

import argparse
import json
import os
import time

from pydxf import DxfLayout


def human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="path to a .dxf file")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    size = os.path.getsize(args.path)
    t0 = time.perf_counter()
    doc = DxfLayout(args.path)
    wall = time.perf_counter() - t0
    bb = doc.bbox()
    layers = doc.layer_summary()

    if args.json:
        out = {
            "path": args.path,
            "file_bytes": size,
            "load_seconds": doc.load_seconds,
            "wall_seconds": wall,
            "throughput_mb_s": (size / 1e6) / doc.load_seconds,
            "n_polylines": doc.n_polylines,
            "n_vertices": doc.n_vertices,
            "n_circles": doc.n_circles,
            "n_layers": doc.n_layers,
            "bbox": {"xmin": bb.xmin, "ymin": bb.ymin, "xmax": bb.xmax, "ymax": bb.ymax},
            "layers": [
                {
                    "name": s.name, "polylines": s.n_poly, "circles": s.n_circ,
                    "bbox": (None if s.bbox is None else
                             {"xmin": s.bbox.xmin, "ymin": s.bbox.ymin,
                              "xmax": s.bbox.xmax, "ymax": s.bbox.ymax}),
                }
                for s in layers
            ],
        }
        print(json.dumps(out, indent=2))
        return

    print(f"\n  File         {args.path}  ({human_bytes(size)})")
    print(f"  Parsed in    {doc.load_seconds*1000:.0f} ms "
          f"({(size/1e6)/doc.load_seconds:.0f} MB/s)   "
          f"[wall incl. numpy views: {wall*1000:.0f} ms]")
    print(f"\n  Geometry")
    print(f"    polylines  {doc.n_polylines:>12,}")
    print(f"    vertices   {doc.n_vertices:>12,}  (avg {doc.n_vertices/max(doc.n_polylines,1):.1f}/polyline)")
    print(f"    circles    {doc.n_circles:>12,}")
    print(f"    layers     {doc.n_layers:>12,}")
    print(f"\n  Extent       {bb}")

    name_w = max((len(s.name) for s in layers), default=5)
    print(f"\n  {'layer'.ljust(name_w)}   {'polylines':>10} {'circles':>9}   extent (x0,y0)–(x1,y1)")
    print("  " + "-" * (name_w + 60))
    for s in layers:
        if s.bbox is None:
            ext = ""
        else:
            ext = (f"({s.bbox.xmin:.1f}, {s.bbox.ymin:.1f}) – "
                   f"({s.bbox.xmax:.1f}, {s.bbox.ymax:.1f})")
        print(f"  {s.name.ljust(name_w)}   {s.n_poly:>10,} {s.n_circ:>9,}   {ext}")
    print()


if __name__ == "__main__":
    main()
