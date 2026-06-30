#!/usr/bin/env python3
"""Regression test for the measuring tool's spatial-grid snapping (Snapper).

Two parts:
  * Correctness on a tiny, hand-verifiable synthetic layout (known corners,
    edges, and exact expected results at several query points/radii).
  * Correctness (against an independent, dead-simple pure-Python brute-force
    reference, on a sample of queries) and performance on a larger synthetic
    random layout -- catches both wrong-answer and got-slow-again regressions
    without depending on a large external DXF file (kept local-only).

    python tests/test_snap.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from viewer.snap import Snapper  # noqa: E402


def _check(name, ok, detail="") -> bool:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{('   ' + detail) if detail else ''}")
    return ok


def _layout(verts, poly_start, poly_count, poly_flags, circ=None):
    """A minimal duck-typed stand-in for DxfLayout -- Snapper only reads these
    five attributes, so a real parsed file isn't needed for this test."""
    ns = types.SimpleNamespace()
    ns.verts = np.asarray(verts, np.float32)
    ns.poly_start = np.asarray(poly_start, np.int64)
    ns.poly_count = np.asarray(poly_count, np.int64)
    ns.poly_flags = np.asarray(poly_flags, np.int64)
    ns.circ = np.asarray(circ if circ is not None else np.empty((0, 3)), np.float32)
    return ns


def test_known_shape() -> bool:
    """A 4-vertex closed square (0,0)-(10,0)-(10,10)-(0,10) plus one open
    2-vertex segment (20,0)-(20,10), and a circle at (30, 5) r=2."""
    verts = [(0, 0), (10, 0), (10, 10), (0, 10), (20, 0), (20, 10)]
    layout = _layout(
        verts, poly_start=[0, 4], poly_count=[4, 2], poly_flags=[1, 0],   # square closed, segment open
        circ=[(30.0, 5.0, 2.0)])
    snap = Snapper(layout)

    ok = True
    # Exactly on a corner.
    pt, kind = snap.snap(10.0, 10.0, 1.0)
    ok &= _check("corner exact hit", (pt, kind) == ((10.0, 10.0), "corner"), str((pt, kind)))

    # Near a corner, within radius -> snaps to it.
    pt, kind = snap.snap(10.3, 9.8, 1.0)
    ok &= _check("corner within radius", (pt, kind) == ((10.0, 10.0), "corner"), str((pt, kind)))

    # Midpoint of an edge, far from any corner -> snaps to the edge.
    pt, kind = snap.snap(5.0, 0.2, 1.0)
    close = kind == "edge" and pt is not None and abs(pt[0] - 5.0) < 1e-4 and abs(pt[1] - 0.0) < 1e-4
    ok &= _check("edge midpoint", close, str((pt, kind)))

    # The square's closing edge (vertex 3 -> vertex 0, since flags=1 means closed).
    pt, kind = snap.snap(0.0, 5.0, 1.0)
    close = kind == "edge" and pt is not None and abs(pt[0]) < 1e-4 and abs(pt[1] - 5.0) < 1e-4
    ok &= _check("closing edge of a closed polyline", close, str((pt, kind)))

    # The open segment must NOT have a closing edge back to its start.
    pt, kind = snap.snap(20.0, -5.0, 1.0)   # would only be near a (wrong) closing edge
    ok &= _check("open polyline has no closing edge", pt is None and kind is None, str((pt, kind)))

    # Circle center counts as a corner.
    pt, kind = snap.snap(30.1, 5.1, 1.0)
    ok &= _check("circle center is a corner", (pt, kind) == ((30.0, 5.0), "corner"), str((pt, kind)))

    # Nothing within radius.
    pt, kind = snap.snap(1000.0, 1000.0, 1.0)
    ok &= _check("nothing in range -> None", (pt, kind) == (None, None), str((pt, kind)))

    # Just outside the radius boundary -> no snap; just inside -> snaps.
    pt, kind = snap.snap(10.0 + 1.001, 10.0, 1.0)
    ok &= _check("just outside radius", pt is None, str((pt, kind)))
    pt, kind = snap.snap(10.0 + 0.999, 10.0, 1.0)
    ok &= _check("just inside radius", pt == (10.0, 10.0), str((pt, kind)))

    return ok


def _brute_force(verts, nxt, wx, wy, r):
    """Independent, deliberately naive pure-Python reference (no numpy
    tricks, no grid) to check the grid-indexed Snapper against."""
    best_d2, best_pt, best_kind = r * r, None, None
    for (x, y) in verts:
        d2 = (x - wx) ** 2 + (y - wy) ** 2
        if d2 <= best_d2:
            best_d2, best_pt, best_kind = d2, (x, y), "corner"
    if best_kind == "corner":
        return best_pt, best_kind
    for i, j in enumerate(nxt):
        ax, ay = verts[i]
        bx, by = verts[j]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((wx - ax) * dx + (wy - ay) * dy) / l2))
        px, py = ax + t * dx, ay + t * dy
        d2 = (px - wx) ** 2 + (py - wy) ** 2
        if d2 <= best_d2:
            best_d2, best_pt, best_kind = d2, (px, py), "edge"
    return (best_pt, best_kind) if best_kind else (None, None)


def test_random_layout() -> bool:
    random.seed(1234)
    n_poly = 4000
    verts, starts, counts, flags = [], [], [], []
    for _ in range(n_poly):
        cx, cy = random.uniform(0, 2000), random.uniform(0, 2000)
        n = random.choice([2, 2, 2, 3, 4])
        starts.append(len(verts))
        for k in range(n):
            verts.append((cx + random.uniform(-5, 5), cy + random.uniform(-5, 5)))
        counts.append(n)
        flags.append(random.choice([0, 1]))
    # A few deliberately long edges (e.g. a background bounding rectangle),
    # to exercise the "long edge" always-checked overflow path.
    starts.append(len(verts))
    verts += [(0, 0), (2000, 0), (2000, 2000), (0, 2000)]
    counts.append(4)
    flags.append(1)
    n_poly += 1

    layout = _layout(verts, starts, counts, flags)
    t0 = time.perf_counter()
    snap = Snapper(layout)
    build_ms = (time.perf_counter() - t0) * 1000
    print(f"  random layout: {len(verts):,} vertices, {n_poly:,} polylines, build {build_ms:.1f} ms")

    verts_arr = np.asarray(verts, np.float64)
    nxt = np.arange(1, len(verts) + 1)
    last = np.asarray(starts) + np.asarray(counts) - 1
    closed = np.asarray(flags).astype(bool)
    nxt[last] = np.where(closed, np.asarray(starts), last)

    random.seed(99)
    ok = True
    mismatches = 0
    n_queries = 250
    radii = [0.5, 3.0, 15.0]
    t_total = 0.0
    for _ in range(n_queries):
        wx, wy = random.uniform(-50, 2050), random.uniform(-50, 2050)
        r = random.choice(radii)
        t0 = time.perf_counter()
        got_pt, got_kind = snap.snap(wx, wy, r)
        t_total += time.perf_counter() - t0
        want_pt, want_kind = _brute_force(verts, nxt, wx, wy, r)
        if got_kind != want_kind:
            mismatches += 1
            continue
        if got_pt is None and want_pt is None:
            continue
        if got_pt is None or want_pt is None or \
                abs(got_pt[0] - want_pt[0]) > 1e-3 or abs(got_pt[1] - want_pt[1]) > 1e-3:
            mismatches += 1

    ok &= _check(f"{n_queries} randomized queries match an independent brute-force reference",
                 mismatches == 0, f"{mismatches} mismatches")
    avg_ms = t_total / n_queries * 1000
    ok &= _check("average query time is sub-millisecond (grid, not a full scan)",
                 avg_ms < 1.0, f"{avg_ms:.3f} ms/call avg")
    return ok


def main() -> int:
    print("[known shape]")
    ok = test_known_shape()
    print("\n[random layout]")
    ok &= test_random_layout()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
