"""Snapping for the measuring tool.

Priority: nearest **corner** (polyline vertex or circle center); if none is within
the radius, fall back to the nearest point on a polyline **edge**. Both are
vectorized bbox-prefilter + exact scans over the full geometry (~6 ms corner,
~15 ms edge for 6 M vertices) — fast enough to snap live under the cursor.

Built lazily (only when the measuring tool is first used) so it stays off the
viewer's startup path.
"""

from __future__ import annotations

import numpy as np


class Snapper:
    def __init__(self, layout):
        v = np.ascontiguousarray(layout.verts, np.float32)
        n = len(v)
        # Corners: polyline vertices + circle centers.
        c = np.asarray(layout.circ, np.float32)
        cpts = v if len(c) == 0 else np.vstack([v, c[:, :2]])
        self.cx = np.ascontiguousarray(cpts[:, 0])
        self.cy = np.ascontiguousarray(cpts[:, 1])
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
        m = (np.abs(self.cx - wx) < r) & (np.abs(self.cy - wy) < r)
        if not m.any():
            return None
        xs, ys = self.cx[m], self.cy[m]
        d2 = (xs - wx) ** 2 + (ys - wy) ** 2
        i = int(np.argmin(d2))
        return (float(xs[i]), float(ys[i])) if d2[i] <= r * r else None

    def _edge(self, wx, wy, r):
        ax, ay, bx, by = self.ax, self.ay, self.bx, self.by
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
