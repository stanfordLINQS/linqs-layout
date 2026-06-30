"""The GL viewport widget: a QOpenGLWidget hosting a GLScene, with pan,
zoom-at-cursor, the light/dark background toggle, and the snapping measuring tool.

Coordinates are kept in logical pixels; the camera only depends on the viewport
aspect ratio, so it renders correctly on Retina without explicit
devicePixelRatio handling."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from . import style
from .camera import Camera2D
from .offscreen import BG_DARK, BG_LIGHT
from .overlay import MeasureOverlay
from .scene import GLScene, nice_grid_spacing
from .snap import Snapper


class GLViewport(QOpenGLWidget):
    """QOpenGLWidget hosting a :class:`GLScene`, with pan, zoom-at-cursor, a
    light/dark background toggle, and a snapping measuring tool."""

    def __init__(self, layout, parent=None):
        super().__init__(parent)
        self._layout = layout
        self.cam = Camera2D()
        self.scene: GLScene | None = None
        self.ctx = None
        self._user_view = False        # True once the user has panned/zoomed
        self._last = None
        self.bg = BG_DARK
        self._light = False

        self.measure_mode = False
        self.measure_points: list[tuple[float, float]] = []
        self.measure_cursor = None
        self.snap_kind = None                   # 'corner' | 'edge' | None (live)
        self.snap: Snapper | None = None        # built lazily — keeps startup fast
        self.snap_px = 12

        self.status_sink = None                 # callable(str): bottom status strip

        self.overlay = MeasureOverlay(self)
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        self.overlay.raise_()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)              # live cursor coords for the status

    def is_light(self) -> bool:
        return self._light

    def _emit_status(self, px, py):
        if self.status_sink is None:
            return
        wx, wy = self.cam.screen_to_world(px, py)
        a = "#%02x%02x%02x" % style.ACCENT
        self.status_sink(
            f'<span style="color:{a}">x</span> {wx:,.1f}'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{a}">y</span> {wy:,.1f}')

    # -- GL lifecycle -----------------------------------------------------
    def initializeGL(self):
        import moderngl
        self.ctx = moderngl.create_context()
        self.scene = GLScene(self.ctx, self._layout)

    def resizeGL(self, w, h):
        self.cam.resize(self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        # Keep fitting until the user takes over, so the initial view matches the
        # final viewport size (and equals the R / reset view) rather than fitting
        # an early, smaller layout size.
        if self.scene is not None and not self._user_view:
            self.cam.fit(self._layout.bbox())

    def paintGL(self):
        fbo = self.ctx.detect_framebuffer()
        fbo.use()
        self.ctx.clear(*self.bg)
        if self.scene is not None:
            (sx, sy), (ox, oy) = self.cam.scale_offset()
            self.scene.draw(fbo, (sx, sy), (ox, oy),
                            grid_spacing=nice_grid_spacing(self.cam.upp))
        self.overlay.update()           # keep the HUD (scale bar, measurement) in sync

    def _refresh(self):
        self.update()
        self.overlay.update()

    def _snap(self, px, py):
        """Return (world_point, kind). kind is 'corner'/'edge'/None; the point is
        the snapped location, or the raw cursor world position when nothing snaps."""
        if self.snap is None:
            self.snap = Snapper(self._layout)
        wx, wy = self.cam.screen_to_world(px, py)
        pt, kind = self.snap.snap(wx, wy, self.snap_px * self.cam.upp)
        return (pt if pt is not None else (wx, wy)), kind

    def _measure_point(self, px, py, shift):
        """Point for the measuring tool. Snapping (corner/edge) always applies;
        with Shift held while placing the second point, the snapped point is then
        constrained to horizontal or vertical from the first point. The snap kind
        is preserved, so the snap indicator still shows on the constrained point."""
        pt, kind = self._snap(px, py)
        if shift and len(self.measure_points) == 1:
            x0, y0 = self.measure_points[0]
            sx, sy = pt
            pt = (sx, y0) if abs(sx - x0) >= abs(sy - y0) else (x0, sy)
        return pt, kind

    # -- interaction ------------------------------------------------------
    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            p = e.position()
            self.cam.zoom_at(p.x(), p.y(), 1.2 ** steps)
            self._user_view = True
            self._emit_status(p.x(), p.y())
            self._refresh()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        p = e.position()
        if self.measure_mode:
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            pt, kind = self._measure_point(p.x(), p.y(), shift)
            self.measure_cursor, self.snap_kind = pt, kind
            if len(self.measure_points) != 1:        # 0 or 2 -> start over
                self.measure_points = [pt]
            else:
                self.measure_points.append(pt)
            self._refresh()
        else:
            self._last = (p.x(), p.y())

    def mouseMoveEvent(self, e):
        p = e.position()
        self._emit_status(p.x(), p.y())
        if self.measure_mode:
            # Live snap / ortho-constraint indicator under the cursor.
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.measure_cursor, self.snap_kind = self._measure_point(p.x(), p.y(), shift)
            self.overlay.update()
            return
        if self._last is not None:
            self.cam.pan_pixels(p.x() - self._last[0], p.y() - self._last[1])
            self._last = (p.x(), p.y())
            self._user_view = True
            self._refresh()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._last = None

    # -- API for the panel / shortcuts -----------------------------------
    def set_measure_mode(self, on: bool):
        self.measure_mode = bool(on)
        if on and self.snap is None:          # build the snapper up front, once
            self.snap = Snapper(self._layout)
        if not on:
            self.snap_kind = None
        self.setMouseTracking(self.measure_mode)
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)
        self.overlay.update()

    def clear_measure(self):
        self.measure_points = []
        self.measure_cursor = None
        self.snap_kind = None
        self.overlay.update()

    def set_fill(self, on: bool):
        if self.scene is not None:
            self.scene.show_fill = bool(on)
            self.update()

    def set_grid(self, on: bool):
        if self.scene is not None:
            self.scene.show_grid = bool(on)
            self.update()

    def set_background(self, light: bool):
        self._light = bool(light)
        self.bg = BG_LIGHT if light else BG_DARK
        if self.scene is not None:
            self.scene.set_shade(0.55 if light else 1.0)
        self._refresh()

    def reset_view(self):
        self._user_view = False        # resume auto-fit (until the next pan/zoom)
        self.cam.fit(self._layout.bbox())
        self._refresh()
