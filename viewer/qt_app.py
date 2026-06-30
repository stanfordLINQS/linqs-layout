"""Interactive PySide6 viewer: a moderngl GPU viewport + a right-side layer panel.

Controls
--------
* scroll wheel   zoom in / out, centered on the cursor
* left-drag      pan
* layer panel    click a layer row (right) to show / hide it
* R              reset view to fit
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QIcon, QKeySequence, QPainter, QPixmap,
                           QShortcut, QSurfaceFormat)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from .camera import Camera2D
from .offscreen import BG
from .palette import layer_colors
from .scene import GLScene

_VIS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_LID_ROLE = int(Qt.ItemDataRole.UserRole)


class GLViewport(QOpenGLWidget):
    """QOpenGLWidget hosting a :class:`GLScene`, with pan + zoom-at-cursor.

    All coordinates are kept in logical (device-independent) pixels; the camera
    only depends on the viewport aspect ratio, so it renders correctly on Retina
    displays without any explicit devicePixelRatio handling.
    """

    def __init__(self, layout, parent=None):
        super().__init__(parent)
        self._layout = layout
        self.cam = Camera2D()
        self.scene: GLScene | None = None
        self.ctx = None
        self._fitted = False
        self._last = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- GL lifecycle -----------------------------------------------------
    def initializeGL(self):
        import moderngl
        self.ctx = moderngl.create_context()
        self.scene = GLScene(self.ctx, self._layout)

    def resizeGL(self, w, h):
        self.cam.resize(self.width(), self.height())
        if not self._fitted and self.scene is not None:
            self.cam.fit(self._layout.bbox())
            self._fitted = True

    def paintGL(self):
        fbo = self.ctx.detect_framebuffer()
        fbo.use()
        self.ctx.clear(*BG)
        if self.scene is not None:
            (sx, sy), (ox, oy) = self.cam.scale_offset()
            self.scene.draw((sx, sy), (ox, oy))

    # -- interaction ------------------------------------------------------
    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        if steps:
            p = e.position()
            self.cam.zoom_at(p.x(), p.y(), 1.2 ** steps)
            self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            p = e.position()
            self._last = (p.x(), p.y())

    def mouseMoveEvent(self, e):
        if self._last is not None:
            p = e.position()
            self.cam.pan_pixels(p.x() - self._last[0], p.y() - self._last[1])
            self._last = (p.x(), p.y())
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._last = None

    # -- API for the layer panel -----------------------------------------
    def set_layer_visible(self, layer_id: int, visible: bool):
        if self.scene is not None:
            self.scene.set_layer_visible(layer_id, visible)
            self.update()

    def set_all_visible(self, visible: bool):
        if self.scene is not None:
            self.scene.set_all_visible(visible)
            self.update()

    def reset_view(self):
        self.cam.fit(self._layout.bbox())
        self.update()

    def toggle_fill(self):
        if self.scene is not None:
            self.scene.toggle_fill()
            self.update()


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
    """Right-hand column of clickable layer rows (color swatch + name + count)."""

    def __init__(self, layout, viewport: GLViewport, parent=None):
        super().__init__(parent)
        self._viewport = viewport
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

        for s in layout.layer_summary():            # sorted by object count desc
            item = QListWidgetItem(f"{s.name}    {s.n_total:,}")
            item.setData(_LID_ROLE, s.layer_id)
            item.setData(_VIS_ROLE, True)
            self.list.addItem(item)
            self._restyle(item)

    def _restyle(self, item: QListWidgetItem):
        lid = item.data(_LID_ROLE)
        vis = bool(item.data(_VIS_ROLE))
        item.setIcon(_swatch(self._qcolors[lid], vis))
        item.setForeground(QColor(235, 235, 235) if vis else QColor(120, 120, 120))

    def _on_click(self, item: QListWidgetItem):
        vis = not bool(item.data(_VIS_ROLE))
        item.setData(_VIS_ROLE, vis)
        self._restyle(item)
        self._viewport.set_layer_visible(item.data(_LID_ROLE), vis)

    def _set_all(self, vis: bool):
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setData(_VIS_ROLE, vis)
            self._restyle(item)
        self._viewport.set_all_visible(vis)


class MainWindow(QWidget):
    def __init__(self, layout):
        super().__init__()
        self.setWindowTitle(f"linqs-layout — {os.path.basename(layout.path)}")
        self._layout = layout

        self.viewport = GLViewport(layout)
        panel = LayerPanel(layout, self.viewport)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.viewport)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1140, 260])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)
        self.resize(1400, 1000)

        QShortcut(QKeySequence("R"), self, self.viewport.reset_view)
        QShortcut(QKeySequence("F"), self, self.viewport.toggle_fill)


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
