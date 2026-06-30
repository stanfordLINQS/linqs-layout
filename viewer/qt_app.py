"""Interactive PySide6 viewer: a moderngl GPU viewport + a right-side layer panel.

Controls
--------
* scroll wheel   zoom in / out, centered on the cursor
* left-drag      pan
* layer panel    click a layer row to show / hide it
* Measure (M)    click two points; shows the distance. Each point snaps to the
                 nearest DXF vertex/center if one is within ~12 px. Esc clears.
* Fill (F)       toggle the translucent polygon fill
* Light bg (B)   toggle light / dark background
* R              reset view to fit
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import (QColor, QFont, QIcon, QKeySequence, QPainter, QPen,
                           QPixmap, QShortcut, QSurfaceFormat)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from .camera import Camera2D
from .offscreen import BG_DARK, BG_LIGHT
from .palette import layer_colors
from .scene import GLScene
from .snap import Snapper

_VIS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_LID_ROLE = int(Qt.ItemDataRole.UserRole)


class MeasureOverlay(QWidget):
    """Transparent HUD over the GL viewport: markers, the measured segment, and
    the distance readout. Mouse-transparent so the viewport still gets clicks."""

    def __init__(self, viewport):
        super().__init__(viewport)
        self._vp = viewport
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, _e):
        vp = self._vp
        chain = list(vp.measure_points)
        if len(chain) == 1 and vp.measure_cursor is not None:
            chain = [chain[0], vp.measure_cursor]
        show_snap = (vp.measure_mode and vp.snap_kind in ("corner", "edge")
                     and vp.measure_cursor is not None)
        if not chain and not show_snap:
            return

        cam = vp.cam
        light = vp.is_light()
        accent = QColor(230, 120, 0) if light else QColor(255, 150, 30)
        snapcol = QColor(0, 150, 210) if light else QColor(60, 215, 255)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        def to_s(pt):
            sx, sy = cam.world_to_screen(pt[0], pt[1])
            return QPointF(sx, sy)

        # Live snap indicator under the cursor (square = corner, circle = edge).
        if show_snap:
            s = to_s(vp.measure_cursor)
            p.setPen(QPen(snapcol, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if vp.snap_kind == "corner":
                p.drawRect(int(s.x() - 6), int(s.y() - 6), 12, 12)
                p.setBrush(snapcol)
                p.drawEllipse(s, 1.6, 1.6)
            else:
                p.drawEllipse(s, 6, 6)

        if len(chain) == 2:
            pen = QPen(accent, 1.6)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(to_s(chain[0]), to_s(chain[1]))

        p.setPen(QPen(accent, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pt in chain:
            s = to_s(pt)
            p.drawLine(QPointF(s.x() - 6, s.y()), QPointF(s.x() + 6, s.y()))
            p.drawLine(QPointF(s.x(), s.y() - 6), QPointF(s.x(), s.y() + 6))
            p.drawEllipse(s, 4, 4)

        if len(chain) == 2:
            (x0, y0), (x1, y1) = chain
            dx, dy = x1 - x0, y1 - y0
            dist = (dx * dx + dy * dy) ** 0.5
            txt = f"{dist:,.2f}   (Δx {dx:,.2f}, Δy {dy:,.2f})"
            mid = to_s(((x0 + x1) / 2, (y0 + y1) / 2))
            f = QFont()
            f.setPointSize(11)
            f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            tw, th = fm.horizontalAdvance(txt), fm.height()
            tx, ty = mid.x() + 10, mid.y() - 10
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 225) if light else QColor(0, 0, 0, 190))
            p.drawRoundedRect(int(tx - 5), int(ty - th), tw + 10, th + 6, 4, 4)
            p.setPen(QColor(20, 20, 25) if light else QColor(245, 245, 250))
            p.drawText(QPointF(tx, ty - 3), txt)
        p.end()


class GLViewport(QOpenGLWidget):
    """QOpenGLWidget hosting a :class:`GLScene`, with pan, zoom-at-cursor, a
    light/dark background toggle, and a snapping measuring tool.

    Coordinates are kept in logical pixels; the camera only depends on the
    viewport aspect ratio, so it renders correctly on Retina without explicit
    devicePixelRatio handling."""

    def __init__(self, layout, parent=None):
        super().__init__(parent)
        self._layout = layout
        self.cam = Camera2D()
        self.scene: GLScene | None = None
        self.ctx = None
        self._fitted = False
        self._last = None
        self.bg = BG_DARK
        self._light = False

        self.measure_mode = False
        self.measure_points: list[tuple[float, float]] = []
        self.measure_cursor = None
        self.snap_kind = None                   # 'corner' | 'edge' | None (live)
        self.snap: Snapper | None = None        # built lazily — keeps startup fast
        self.snap_px = 12

        self.overlay = MeasureOverlay(self)
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        self.overlay.raise_()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def is_light(self) -> bool:
        return self._light

    # -- GL lifecycle -----------------------------------------------------
    def initializeGL(self):
        import moderngl
        self.ctx = moderngl.create_context()
        self.scene = GLScene(self.ctx, self._layout)

    def resizeGL(self, w, h):
        self.cam.resize(self.width(), self.height())
        self.overlay.setGeometry(0, 0, self.width(), self.height())
        if not self._fitted and self.scene is not None:
            self.cam.fit(self._layout.bbox())
            self._fitted = True

    def paintGL(self):
        fbo = self.ctx.detect_framebuffer()
        fbo.use()
        self.ctx.clear(*self.bg)
        if self.scene is not None:
            (sx, sy), (ox, oy) = self.cam.scale_offset()
            self.scene.draw(fbo, (sx, sy), (ox, oy))

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
        """Point for the measuring tool. With Shift held while placing the second
        point, constrain it to horizontal or vertical from the first point."""
        if shift and len(self.measure_points) == 1:
            wx, wy = self.cam.screen_to_world(px, py)
            x0, y0 = self.measure_points[0]
            if abs(wx - x0) >= abs(wy - y0):
                return (wx, y0), "ortho"          # horizontal lock
            return (x0, wy), "ortho"              # vertical lock
        return self._snap(px, py)

    # -- interaction ------------------------------------------------------
    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            p = e.position()
            self.cam.zoom_at(p.x(), p.y(), 1.2 ** steps)
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
        if self.measure_mode:
            # Live snap / ortho-constraint indicator under the cursor.
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.measure_cursor, self.snap_kind = self._measure_point(p.x(), p.y(), shift)
            self.overlay.update()
            return
        if self._last is not None:
            self.cam.pan_pixels(p.x() - self._last[0], p.y() - self._last[1])
            self._last = (p.x(), p.y())
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

    def set_background(self, light: bool):
        self._light = bool(light)
        self.bg = BG_LIGHT if light else BG_DARK
        if self.scene is not None:
            self.scene.set_shade(0.55 if light else 1.0)
        self._refresh()

    def reset_view(self):
        self.cam.fit(self._layout.bbox())
        self._refresh()


def _swatch(color: QColor, filled: bool) -> QIcon:
    pm = QPixmap(14, 14)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    if filled:
        p.fillRect(1, 1, 12, 12, color)
    else:
        p.setPen(QColor(110, 110, 115))
        p.drawRect(1, 1, 11, 11)
    p.end()
    return QIcon(pm)


class LayerPanel(QWidget):
    """Right column: clickable layer rows + Measure / Fill / Background controls."""

    def __init__(self, layout, viewport: GLViewport, parent=None):
        super().__init__(parent)
        self._vp = viewport
        cols = layer_colors(max(layout.n_layers, 1))
        self._qcolors = [QColor(int(r * 255), int(g * 255), int(b * 255))
                         for r, g, b in cols]

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        header = QLabel("Layers")
        header.setStyleSheet("font-weight: 600;")
        root.addWidget(header)

        btns = QHBoxLayout()
        show_all = QPushButton("Show all")
        hide_all = QPushButton("Hide all")
        show_all.clicked.connect(lambda: self._set_all(True))
        hide_all.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(show_all)
        btns.addWidget(hide_all)
        root.addLayout(btns)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemClicked.connect(self._on_click)
        root.addWidget(self.list, 1)
        for s in layout.layer_summary():
            item = QListWidgetItem(f"{s.name}    {s.n_total:,}")
            item.setData(_LID_ROLE, s.layer_id)
            item.setData(_VIS_ROLE, True)
            self.list.addItem(item)
            self._restyle(item)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        self.measure_btn = QPushButton("Measure")
        self.measure_btn.setCheckable(True)
        self.measure_btn.toggled.connect(viewport.set_measure_mode)
        self.fill_btn = QPushButton("Fill")
        self.fill_btn.setCheckable(True)
        self.fill_btn.setChecked(True)
        self.fill_btn.toggled.connect(viewport.set_fill)
        self.bg_btn = QPushButton("Light background")
        self.bg_btn.setCheckable(True)
        self.bg_btn.toggled.connect(viewport.set_background)
        for b in (self.measure_btn, self.fill_btn, self.bg_btn):
            root.addWidget(b)

        hint = QLabel("Measure: click two points (snaps to the\nnearest vertex). Esc clears.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(hint)

    def _restyle(self, item: QListWidgetItem):
        lid = item.data(_LID_ROLE)
        vis = bool(item.data(_VIS_ROLE))
        item.setIcon(_swatch(self._qcolors[lid], vis))
        item.setForeground(QColor(235, 235, 235) if vis else QColor(120, 120, 120))

    def _on_click(self, item: QListWidgetItem):
        vis = not bool(item.data(_VIS_ROLE))
        item.setData(_VIS_ROLE, vis)
        self._restyle(item)
        self._vp.scene.set_layer_visible(item.data(_LID_ROLE), vis)
        self._vp.update()

    def _set_all(self, vis: bool):
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setData(_VIS_ROLE, vis)
            self._restyle(item)
        if self._vp.scene is not None:
            self._vp.scene.set_all_visible(vis)
            self._vp.update()


class MainWindow(QWidget):
    def __init__(self, layout):
        super().__init__()
        self.setWindowTitle(f"linqs-layout — {os.path.basename(layout.path)}")
        self._layout = layout

        self.viewport = GLViewport(layout)
        self.panel = LayerPanel(layout, self.viewport)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.viewport)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1140, 260])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)
        self.resize(1400, 1000)

        QShortcut(QKeySequence("R"), self, self.viewport.reset_view)
        QShortcut(QKeySequence("M"), self, self.panel.measure_btn.toggle)
        QShortcut(QKeySequence("F"), self, self.panel.fill_btn.toggle)
        QShortcut(QKeySequence("B"), self, self.panel.bg_btn.toggle)
        QShortcut(QKeySequence("Esc"), self, self.viewport.clear_measure)


def run(layout) -> int:
    """Open the interactive viewer for ``layout`` and block until closed."""
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication.instance() or QApplication([])
    win = MainWindow(layout)
    win.show()
    return app.exec()
