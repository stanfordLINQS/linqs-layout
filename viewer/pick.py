"""Polygon picking: given a world-space click, find the polygon under it.

A click is a stabbing query, not a radius query, so this is simpler than the
measuring-tool :class:`~viewer.snap.Snapper`: a vectorized per-polygon bbox test
(built once, lazily, like the Snapper) narrows 100k+ polygons to the handful
whose bounding box contains the point, then an exact ray-cast point-in-polygon
test runs on just those. When several polygons contain the point (nested
features, a big bounding rectangle), the one with the **smallest area** wins so a
click lands on the most specific feature rather than the frame around it.
"""

from __future__ import annotations

import numpy as np


def _point_in_poly(px: float, py: float, poly: np.ndarray) -> bool:
    """Even-odd ray-cast test: is (px, py) inside the closed polygon ``poly``
    (N, 2)? Horizontal edges contribute no crossing (masked out), so the /
    (y2 - y) they'd divide by is never used."""
    x, y = poly[:, 0], poly[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    straddles = (y > py) != (y2 > py)                 # edge crosses the ray's y
    with np.errstate(divide="ignore", invalid="ignore"):
        x_cross = x + (py - y) / (y2 - y) * (x2 - x)
    return bool(np.count_nonzero(straddles & (px < x_cross)) % 2 == 1)


def _poly_area(poly: np.ndarray) -> float:
    """Absolute shoelace area of a polygon (N, 2)."""
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


class Picker:
    """Point-in-polygon hit testing over a whole layout. Build once (lazily);
    each :meth:`pick` is a vectorized bbox prefilter + a few exact tests."""

    def __init__(self, layout):
        self.verts = np.ascontiguousarray(layout.verts, np.float64)
        self.start = np.asarray(layout.poly_start, np.int64)
        self.count = np.asarray(layout.poly_count, np.int64)
        self.layer = np.asarray(layout.poly_layer, np.int64)
        self.flags = np.asarray(layout.poly_flags, np.int64)
        if len(self.verts) == 0 or len(self.start) == 0:
            self.xmin = self.xmax = self.ymin = self.ymax = np.empty(0)
            return
        # Per-polygon bbox (contiguous CSR slices -> reduceat over the start
        # offsets, exactly as scene._build_fill computes its per-layer bboxes).
        v = self.verts
        self.xmin = np.minimum.reduceat(v[:, 0], self.start)
        self.xmax = np.maximum.reduceat(v[:, 0], self.start)
        self.ymin = np.minimum.reduceat(v[:, 1], self.start)
        self.ymax = np.maximum.reduceat(v[:, 1], self.start)

    def pick(self, wx: float, wy: float, visible=None) -> int | None:
        """Index of the smallest-area polygon containing (wx, wy), or None.

        ``visible`` (optional bool array indexed by layer id) restricts the hit
        test to shown layers, so a click can't select a hidden polygon."""
        if len(self.start) == 0:
            return None
        inbox = ((self.xmin <= wx) & (wx <= self.xmax)
                 & (self.ymin <= wy) & (wy <= self.ymax))
        if visible is not None:
            vis = np.asarray(visible, bool)
            lid = np.clip(self.layer, 0, len(vis) - 1)
            inbox &= vis[lid]
        best, best_area = None, np.inf
        for p in np.flatnonzero(inbox):
            poly = self.poly_verts(int(p))
            if _point_in_poly(wx, wy, poly):
                a = _poly_area(poly)
                if a < best_area:
                    best, best_area = int(p), a
        return best

    def poly_verts(self, idx: int) -> np.ndarray:
        """The (count, 2) vertex slice of polygon ``idx``."""
        s, c = int(self.start[idx]), int(self.count[idx])
        return self.verts[s:s + c]

    def is_closed(self, idx: int) -> bool:
        return bool(self.flags[idx] & 1)
