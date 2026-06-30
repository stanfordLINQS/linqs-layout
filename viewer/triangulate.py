"""Real (non-overlapping) polygon triangulation, to reduce wind-pass overdraw.

scene.py's fill technique is a triangle *fan* resolved via the GPU winding
rule: the rule is what correctly handles arbitrary overlap (a single
concave/self-intersecting polygon's naively-overlapping fan triangles, *and*
two different polygons of the same layer overlapping each other -- both need
the same-layer color blended exactly once per pixel, which only the
layer-wide winding accumulation + single cover pass guarantees). A first cut
of this module replaced the whole wind/cover technique with one direct
alpha-blended draw of a real triangulation, which is wrong: it blends each
*polygon's* color independently, so two overlapping same-layer polygons get
double-blended where they overlap (confirmed: 53% of pixels differed on a
real 33-layer file, some by a large, real amount -- not just edge AA wobble).

So the winding rule + per-layer cover pass stays. What this module replaces
is only the *fan* feeding the wind pass: a non-convex fan can self-overlap
heavily (rasterizing the same pixels several times over for one polygon),
which is real, measured waste (~95% of all triangles in a real file came
from a small fraction of complex/concave polygons). A real triangulation has
no such self-overlap, so swapping it in cuts the wind pass's fragment count
for those polygons without changing what the winding rule or cover pass do
at all -- same correctness, less rasterization work.

Most polygons are convex, where a fan *is* already a valid non-overlapping
triangulation (no earcut needed). Earcut's cost scales with polygon
complexity, not just vertex count, and a handful of large/complex concave
polygons can dominate total triangulation time (measured ~3.7s for ~9k
concave polygons -- 94% of all triangles! -- on a real 6M-vertex file),
which is why this runs on a background thread (pure numpy/earcut, no GL or
Qt calls, so it's safe to call from one) rather than blocking initial load.
"""

from __future__ import annotations

import numpy as np


def _is_convex_batch(verts: np.ndarray, start: np.ndarray, count: np.ndarray) -> np.ndarray:
    """Vectorized convexity test, batched per distinct vertex count (so each
    batch's per-vertex arrays are rectangular and the cross-product check
    vectorizes across all polygons of that size at once)."""
    convex = np.ones(len(start), dtype=bool)
    for c in np.unique(count):
        if c < 3:
            continue
        idxs = np.flatnonzero(count == c)
        base = start[idxs]
        pts = np.stack([verts[base + k] for k in range(c)], axis=1)   # (m, c, 2)
        prev = np.roll(pts, 1, axis=1)
        nxt = np.roll(pts, -1, axis=1)
        e1, e2 = pts - prev, nxt - pts
        cross = e1[:, :, 0] * e2[:, :, 1] - e1[:, :, 1] * e2[:, :, 0]
        # Convex iff every interior angle turns the same way (allowing for
        # collinear/degenerate edges, where cross ~= 0, on either side).
        convex[idxs] = (cross >= -1e-9).all(axis=1) | (cross <= 1e-9).all(axis=1)
    return convex


def compute_real_fill(verts: np.ndarray, start: np.ndarray, count: np.ndarray,
                       layer: np.ndarray, n_layers: int):
    """Non-overlapping triangulation of every polygon with >= 3 vertices,
    grouped by layer (mirroring scene.py's existing ``_fill_off``/
    ``_fill_count``) so it's a drop-in replacement for the wind pass's
    per-layer triangle range -- the winding rule and cover pass are unchanged.

    Pure numpy + earcut -- no GL/Qt dependency, safe to call from a
    background thread. Returns ``(idx, fill_off, fill_count)``.
    """
    fillable = count >= 3
    if not fillable.any():
        empty = np.zeros(n_layers, np.int64)
        return np.empty(0, np.uint32), empty, empty
    idx_all = np.flatnonzero(fillable)
    convex = _is_convex_batch(verts, start[idx_all], count[idx_all])

    tris_per_poly = np.zeros(len(idx_all), np.int64)   # parallel to idx_all
    tri_parts = [None] * len(idx_all)

    # Convex: a fan is already a valid, non-overlapping triangulation.
    conv_pos = np.flatnonzero(convex)
    if len(conv_pos):
        conv_idx = idx_all[conv_pos]
        s, c = start[conv_idx], count[conv_idx]
        t = c - 2
        gid = np.repeat(np.arange(len(conv_idx)), t)
        k = np.arange(int(t.sum())) - np.repeat(np.cumsum(t) - t, t)
        ss = s[gid]
        fan = np.empty((int(t.sum()), 3), np.uint32)
        fan[:, 0] = ss
        fan[:, 1] = ss + k + 1
        fan[:, 2] = ss + k + 2
        fan = fan.reshape(-1)
        offs = np.r_[0, np.cumsum(t)]
        for j, p in enumerate(conv_pos):
            tri_parts[p] = fan[offs[j] * 3:offs[j + 1] * 3]
            tris_per_poly[p] = t[j]

    # Concave: needs a real triangulation -- this is the slow part.
    conc_pos = np.flatnonzero(~convex)
    if len(conc_pos):
        import mapbox_earcut as earcut

        for p in conc_pos:
            i = idx_all[p]
            s, c = int(start[i]), int(count[i])
            pts = np.ascontiguousarray(verts[s:s + c], np.float64)
            tris = earcut.triangulate_float64(pts, np.array([c], np.uint32)).astype(np.uint32) + s
            tri_parts[p] = tris
            tris_per_poly[p] = len(tris) // 3

    # Group by layer, same convention as scene.py's _build_fill.
    poly_layer = layer[idx_all]
    order = np.argsort(poly_layer, kind="stable")
    tri_per_layer = np.bincount(poly_layer[order], weights=tris_per_poly[order],
                                 minlength=n_layers).astype(np.int64)
    fill_count = tri_per_layer * 3
    fill_off = (np.cumsum(tri_per_layer) - tri_per_layer) * 3

    idx = np.concatenate([tri_parts[p] for p in order if tris_per_poly[p] > 0]) \
        if tris_per_poly.any() else np.empty(0, np.uint32)
    return idx.astype(np.uint32), fill_off, fill_count
