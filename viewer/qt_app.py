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
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence, QPainter,
                           QPen, QPixmap, QShortcut)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from .camera import Camera2D
from .offscreen import BG_DARK, BG_LIGHT
from .palette import layer_colors
from .scene import GLScene, nice_grid_spacing
from .snap import Snapper

_VIS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_LID_ROLE = int(Qt.ItemDataRole.UserRole)


def _format_dist(v: float) -> str:
    """Format a layout distance (assumed microns) as µm / mm."""
    return f"{v / 1000.0:g} mm" if v >= 1000.0 else f"{v:g} µm"


class MeasureOverlay(QWidget):
    """Transparent HUD over the GL viewport: markers, the measured segment, and
    the distance readout. Mouse-transparent so the viewport still gets clicks."""

    def __init__(self, viewport):
        super().__init__(viewport)
        self._vp = viewport
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def _draw_scale_bar(self, p, light):
        """Bottom-left scale bar: one grid cell, labeled in µm/mm."""
        vp = self._vp
        scene = vp.scene
        if scene is None or vp.cam.upp <= 0:
            return
        g = getattr(scene, "grid_spacing", 0.0)
        if g <= 0:
            return
        length = g / vp.cam.upp                       # one grid cell, in logical px
        x0, y0 = 18.0, self.height() - 22.0
        label = _format_dist(g)

        f = QFont()
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        lw = p.fontMetrics().horizontalAdvance(label)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 180) if light else QColor(0, 0, 0, 140))
        p.drawRoundedRect(int(x0 - 8), int(y0 - 28), int(max(length, lw) + 18), 40, 5, 5)

        col = QColor(35, 38, 46) if light else QColor(228, 231, 238)
        p.setPen(QPen(col, 2.0))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + length, y0))
        p.drawLine(QPointF(x0, y0 - 5), QPointF(x0, y0 + 5))
        p.drawLine(QPointF(x0 + length, y0 - 5), QPointF(x0 + length, y0 + 5))
        p.drawText(QPointF(x0, y0 - 9), label)

    def paintEvent(self, _e):
        vp = self._vp
        cam = vp.cam
        light = vp.is_light()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._draw_scale_bar(p, light)

        chain = list(vp.measure_points)
        if len(chain) == 1 and vp.measure_cursor is not None:
            chain = [chain[0], vp.measure_cursor]
        show_snap = (vp.measure_mode and vp.snap_kind in ("corner", "edge")
                     and vp.measure_cursor is not None)
        if not chain and not show_snap:
            p.end()
            return

        accent = QColor(230, 120, 0) if light else QColor(255, 150, 30)
        snapcol = QColor(0, 150, 210) if light else QColor(60, 215, 255)

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
        # Checkboxes (not checkable push buttons): on macOS a checkable button
        # gives no visible checked state, so a default-on toggle looks off.
        self.measure_btn = QCheckBox("Measure")
        self.measure_btn.toggled.connect(viewport.set_measure_mode)
        self.fill_btn = QCheckBox("Fill")
        self.fill_btn.setChecked(True)            # fill is on by default
        self.fill_btn.toggled.connect(viewport.set_fill)
        self.grid_btn = QCheckBox("Grid")
        self.grid_btn.setChecked(True)            # grid is on by default
        self.grid_btn.toggled.connect(viewport.set_grid)
        self.bg_btn = QCheckBox("Light background")
        self.bg_btn.toggled.connect(viewport.set_background)
        for b in (self.measure_btn, self.fill_btn, self.grid_btn, self.bg_btn):
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


class MainWindow(QMainWindow):
    """One layout in one window: GL viewport + layer panel, a File/View menu, and
    drag-and-drop of .dxf files. ``app`` (a ViewerApp) handles opening new files."""

    def __init__(self, layout, app=None):
        super().__init__()
        self._layout = layout
        self._app = app
        self.setWindowTitle(f"LINQS Layout — {os.path.basename(layout.path)}")
        self.setAcceptDrops(True)

        self.viewport = GLViewport(layout)
        self.panel = LayerPanel(layout, self.viewport)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.viewport)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1140, 260])
        self.setCentralWidget(splitter)
        self.resize(1400, 1000)

        self._build_menu()
        QShortcut(QKeySequence("Esc"), self, self.viewport.clear_measure)

    def _build_menu(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open)
        file_menu.addAction(act_open)
        act_close = QAction("Close Window", self)
        act_close.setShortcut(QKeySequence.StandardKey.Close)
        act_close.triggered.connect(self.close)
        file_menu.addAction(act_close)

        view_menu = bar.addMenu("View")
        for key, label, fn in (
            ("R", "Reset View", self.viewport.reset_view),
            ("F", "Toggle Fill", self.panel.fill_btn.toggle),
            ("G", "Toggle Grid", self.panel.grid_btn.toggle),
            ("B", "Toggle Background", self.panel.bg_btn.toggle),
            ("M", "Measure", self.panel.measure_btn.toggle),
        ):
            act = QAction(label, self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(fn)
            view_menu.addAction(act)

    def _open(self):
        if self._app is not None:
            self._app.prompt_open()

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if self._app is not None and any(u.toLocalFile().lower().endswith(".dxf") for u in urls):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".dxf"):
                self._app.open_path(p)
                break


class WelcomeWindow(QMainWindow):
    """Default startup screen: prompts to open a DXF (⌘O), via dialog or drop."""

    def __init__(self, app):
        super().__init__()
        self._app = app
        self.setWindowTitle("LINQS Layout")
        self.setAcceptDrops(True)
        self.resize(700, 480)

        central = QWidget()
        central.setStyleSheet("background-color: black;")
        v = QVBoxLayout(central)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Press  ⌘O  to open a DXF file")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: white; font-size: 20px;")
        v.addWidget(hint)

        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu("File")
        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(lambda: self._app.prompt_open())
        file_menu.addAction(act_open)

    def dragEnterEvent(self, e):
        urls = e.mimeData().urls() if e.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith(".dxf") for u in urls):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".dxf"):
                self._app.open_path(p)
                break
