"""Snapping for the measuring tool.

Priority: nearest **corner** (polyline vertex or circle center); if none is within
the radius, fall back to the nearest point on a polyline **edge**.

Both are backed by a uniform spatial grid (built once, lazily, when the
measuring tool is first used) so a query only touches the handful of points
near the cursor instead of scanning the full geometry -- a few hundred
microseconds even on a half-million-vertex layout, regardless of dataset
size. (An earlier version scanned the whole array on every query: vectorized
with numpy, but a real ~5 ms/call on a 435k-vertex layout is too slow to redo
synchronously on every mouse-move event without visibly lagging -- see
viewer/viewport.py's throttle, which this complements rather than replaces.)

Edges are bucketed by midpoint; the rare edge far longer than a typical cell
(e.g. a background bounding rectangle) would force an oversized grid for
everyone if bucketed the same way, so those are kept in a small
always-checked list instead -- see ``_edge_margin`` for the correctness
argument.
"""

from __future__ import annotations

import numpy as np


def _cell_size(x: np.ndarray, y: np.ndarray, target_per_cell: float = 4.0) -> float:
    """Pick a grid cell size so each cell holds ~target_per_cell points on
    average, assuming a roughly uniform spread over the bounding box."""
    n = len(x)
    if n == 0:
        return 1.0
    w = float(x.max() - x.min()) or 1.0
    h = float(y.max() - y.min()) or 1.0
    return max((w * h * target_per_cell / n) ** 0.5, 1e-9)


class _Grid:
    """Uniform spatial hash over 2D points: bucket once (vectorized), then a
    radius query only gathers points from the handful of cells it overlaps."""

    def __init__(self, x: np.ndarray, y: np.ndarray, cell: float):
        self.cell = float(cell) if cell > 0 else 1.0
        self.xmin = float(x.min()) if len(x) else 0.0
        self.ymin = float(y.min()) if len(y) else 0.0
        self._lookup: dict[tuple[int, int], tuple[int, int]] = {}
        self.order = np.empty(0, dtype=np.int64)
        if len(x) == 0:
            return
        cx = np.floor((x - self.xmin) / self.cell).astype(np.int64)
        cy = np.floor((y - self.ymin) / self.cell).astype(np.int64)
        ny = int(cy.max()) + 2                       # packs (cx, cy) into one sortable key
        key = cx * ny + cy
        order = np.argsort(key, kind="stable")
        key_sorted = key[order]
        starts = np.flatnonzero(np.r_[True, key_sorted[1:] != key_sorted[:-1]])
        ends = np.r_[starts[1:], len(order)]
        cx_at, cy_at = cx[order][starts], cy[order][starts]
        self._lookup = {(int(bx), int(by)): (int(s), int(e))
                         for bx, by, s, e in zip(cx_at, cy_at, starts, ends)}
        self.order = order

    def query(self, wx: float, wy: float, r: float, max_cells: int = 400):
        """Indices (into the arrays the grid was built from) of every point in
        a cell that could contain something within distance ``r`` of (wx, wy).

        Returns ``None`` instead if that would mean enumerating more than
        ``max_cells`` cells (e.g. ``r`` is huge relative to the cell size, at
        extreme zoom-out) -- past that point the per-cell dict-lookup
        overhead exceeds the cost of just scanning everything directly, so
        the caller should fall back to a brute-force check instead."""
        cx0 = int(np.floor((wx - r - self.xmin) / self.cell))
        cx1 = int(np.floor((wx + r - self.xmin) / self.cell))
        cy0 = int(np.floor((wy - r - self.ymin) / self.cell))
        cy1 = int(np.floor((wy + r - self.ymin) / self.cell))
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > max_cells:
            return None
        parts = []
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                rng = self._lookup.get((cx, cy))
                if rng is not None:
                    parts.append(self.order[rng[0]:rng[1]])
        if not parts:
            return np.empty(0, dtype=np.int64)
        return np.concatenate(parts)


class Snapper:
    def __init__(self, layout):
        v = np.ascontiguousarray(layout.verts, np.float32)
        n = len(v)
        # Corners: polyline vertices + circle centers.
        c = np.asarray(layout.circ, np.float32)
        cpts = v if len(c) == 0 else np.vstack([v, c[:, :2]])
        self.cx = np.ascontiguousarray(cpts[:, 0])
        self.cy = np.ascontiguousarray(cpts[:, 1])
        self._corner_grid = _Grid(self.cx, self.cy, _cell_size(self.cx, self.cy))

        # Edges: polyline segments (closing edge included for closed polylines).
        start = np.asarray(layout.poly_start, np.int64)
        count = np.asarray(layout.poly_count, np.int64)
        flags = np.asarray(layout.poly_flags, np.int64)
        nxt = np.arange(1, n + 1, dtype=np.int64)
        last = start + count - 1
        closed = (flags & 1).astype(bool)
        nxt[last] = np.where(closed, start, last)
        self.ax = np.ascontiguousarray(v[:, 0])
        self.ay = np.ascontiguousarray(v[:, 1])
        self.bx = np.ascontiguousarray(v[nxt, 0])
        self.by = np.ascontiguousarray(v[nxt, 1])

        mx = (self.ax + self.bx) * 0.5
        my = (self.ay + self.by) * 0.5
        ehalf = 0.5 * np.hypot(self.bx - self.ax, self.by - self.ay)
        base_cell = _cell_size(mx, my)
        # Any edge longer than this would force an oversized grid for everyone if
        # midpoint-bucketed normally; keep the (typically rare) long ones in a
        # small always-checked list instead. _edge_margin bounds how far a SHORT
        # edge's bbox can reach from its own midpoint bucket, so a query expanded
        # by that margin can't miss one: by the triangle inequality, any short
        # edge whose bbox intersects the search circle (radius r) must have its
        # midpoint within r + _edge_margin of the query point.
        long_cut = base_cell * 3.0
        is_long = ehalf > long_cut
        self._long_edges = np.flatnonzero(is_long)
        self._short_edges = np.flatnonzero(~is_long)
        self._edge_margin = long_cut
        self._edge_grid = _Grid(mx[self._short_edges], my[self._short_edges], base_cell)

    def snap(self, wx, wy, radius):
        """Return ((x, y), kind) with kind 'corner' | 'edge', or (None, None)."""
        c = self._corner(wx, wy, radius)
        if c is not None:
            return c, "corner"
        e = self._edge(wx, wy, radius)
        if e is not None:
            return e, "edge"
        return None, None

    def _corner(self, wx, wy, r):
        idx = self._corner_grid.query(wx, wy, r)
        if idx is None:                          # radius spans too much of the grid
            idx = np.arange(len(self.cx))
        if len(idx) == 0:
            return None
        xs, ys = self.cx[idx], self.cy[idx]
        d2 = (xs - wx) ** 2 + (ys - wy) ** 2
        i = int(np.argmin(d2))
        return (float(xs[i]), float(ys[i])) if d2[i] <= r * r else None

    def _edge(self, wx, wy, r):
        near = self._edge_grid.query(wx, wy, r + self._edge_margin)
        if near is None:                         # radius spans too much of the grid
            cand = np.arange(len(self.ax))
        else:
            cand = self._short_edges[near] if len(near) else near
            if len(self._long_edges):
                cand = np.concatenate([cand, self._long_edges]) if len(cand) else self._long_edges
        if len(cand) == 0:
            return None
        ax, ay, bx, by = self.ax[cand], self.ay[cand], self.bx[cand], self.by[cand]
        # Exact bbox-prefilter + distance check on the (small) candidate set --
        # identical math to a full brute-force scan, just over far fewer edges.
        m = ((np.maximum(ax, bx) >= wx - r) & (np.minimum(ax, bx) <= wx + r) &
             (np.maximum(ay, by) >= wy - r) & (np.minimum(ay, by) <= wy + r))
        if not m.any():
            return None
        Ax, Ay, Bx, By = ax[m], ay[m], bx[m], by[m]
        dx, dy = Bx - Ax, By - Ay
        L2 = dx * dx + dy * dy
        t = np.clip(((wx - Ax) * dx + (wy - Ay) * dy) / np.where(L2 > 0, L2, 1.0), 0.0, 1.0)
        px, py = Ax + t * dx, Ay + t * dy
        d2 = (px - wx) ** 2 + (py - wy) ** 2
        i = int(np.argmin(d2))
        return (float(px[i]), float(py[i])) if d2[i] <= r * r else None
