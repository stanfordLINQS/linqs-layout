#!/usr/bin/env python3
"""Deterministic smoke test for the native DXF core + ctypes loader.

Runs WITHOUT a display or GPU — it exercises only the C++ parser and the
zero-copy numpy views, so it is the first thing to run when bringing the build up
on a new platform (Windows in particular). Plain asserts, no pytest required:

    python tests/test_smoke.py                  # assert on the committed sample
    python tests/test_smoke.py path/to/big.dxf  # also: time a real large file

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydxf import DxfLayout, loader  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.dxf")


def _check(name, got, want) -> bool:
    ok = got == want
    tag = "ok  " if ok else "FAIL"
    extra = "" if ok else f"   (expected {want!r})"
    print(f"  [{tag}] {name}: {got!r}{extra}")
    return ok


def test_sample() -> bool:
    """Assert the exact geometry of the committed synthetic sample.dxf."""
    print(f"native core: {loader._libname()}  ({loader._LIB_DIR})")
    print(f"parsing {SAMPLE}")
    d = DxfLayout(SAMPLE)
    try:
        ok = True
        ok &= _check("n_polylines", d.n_polylines, 2)
        ok &= _check("n_vertices", d.n_vertices, 7)
        ok &= _check("n_circles", d.n_circles, 2)
        ok &= _check("n_layers", d.n_layers, 2)
        ok &= _check("layers (sorted)", sorted(d.layers), ["METAL", "VIA"])
        ok &= _check("poly[0] vertex count", int(d.poly_count[0]), 4)
        ok &= _check("poly[1] vertex count", int(d.poly_count[1]), 3)
        ok &= _check("poly[0] closed", d.is_closed(0), True)
        bb = d.bbox()
        ok &= _check("bbox xmin", bb.xmin, 0.0)
        ok &= _check("bbox xmax", bb.xmax, 30.0)
        ok &= _check("bbox ymin", bb.ymin, 0.0)
        ok &= _check("bbox ymax", bb.ymax, 10.0)
        # layer_summary must run and cover both layers.
        summ = d.layer_summary()
        ok &= _check("layer_summary rows", len(summ), 2)
        ok &= _check("total objects", sum(s.n_total for s in summ), 4)
        return ok
    finally:
        d.close()


def time_file(path: str) -> bool:
    """Parse a real file and report throughput (no correctness assertions)."""
    t0 = time.perf_counter()
    d = DxfLayout(path)
    dt = time.perf_counter() - t0
    mb = os.path.getsize(path) / 1e6
    print(f"\nlarge file: {path}")
    print(f"  {d.n_polylines:,} polylines, {d.n_vertices:,} vertices, "
          f"{d.n_circles:,} circles, {d.n_layers} layers")
    print(f"  parsed {mb:.1f} MB in {dt*1e3:.0f} ms ({mb/dt:.0f} MB/s)")
    d.close()
    return True


def main() -> int:
    ok = test_sample()
    for extra in sys.argv[1:]:
        ok &= time_file(extra)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
