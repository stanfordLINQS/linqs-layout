"""Orthographic 2-D camera: isotropic world<->screen mapping with zoom-at-cursor.

The camera works in *framebuffer pixels* (device pixels). World units are mapped
to OpenGL clip space [-1, 1] via a per-axis scale + offset (no full matrix), which
keeps the vertex shaders trivial. World +y is up; screen +y is down (handled in
:meth:`screen_to_world`).
"""

from __future__ import annotations


class Camera2D:
    def __init__(self) -> None:
        self.cx = 0.0          # world coord at viewport center
        self.cy = 0.0
        self.upp = 1.0         # world units per pixel (isotropic)
        self.vw = 1            # viewport size in pixels
        self.vh = 1

    def resize(self, w: int, h: int) -> None:
        self.vw = max(int(w), 1)
        self.vh = max(int(h), 1)

    def fit(self, bbox, margin: float = 0.04) -> None:
        """Frame ``bbox`` (a pydxf BBox) centered, with a small margin."""
        self.cx = 0.5 * (bbox.xmin + bbox.xmax)
        self.cy = 0.5 * (bbox.ymin + bbox.ymax)
        w = max(bbox.width, 1e-9)
        h = max(bbox.height, 1e-9)
        self.upp = max(w / self.vw, h / self.vh) / max(1.0 - margin, 1e-3)

    def screen_to_world(self, px: float, py: float) -> tuple[float, float]:
        wx = self.cx + (px - self.vw / 2.0) * self.upp
        wy = self.cy - (py - self.vh / 2.0) * self.upp
        return wx, wy

    def zoom_at(self, px: float, py: float, factor: float) -> None:
        """Zoom by ``factor`` (>1 zooms in) keeping the world point under the
        cursor pinned to the same pixel."""
        wx, wy = self.screen_to_world(px, py)
        self.upp /= factor
        self.cx = wx - (px - self.vw / 2.0) * self.upp
        self.cy = wy + (py - self.vh / 2.0) * self.upp

    def pan_pixels(self, dx: float, dy: float) -> None:
        """Pan so the content follows a drag of (dx, dy) screen pixels."""
        self.cx -= dx * self.upp
        self.cy += dy * self.upp

    def scale_offset(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Return ((sx, sy), (ox, oy)) such that clip = world * scale + offset."""
        sx = 2.0 / (self.vw * self.upp)
        sy = 2.0 / (self.vh * self.upp)
        return (sx, sy), (-self.cx * sx, -self.cy * sy)
