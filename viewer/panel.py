"""The right-hand layer panel: clickable layer rows (color swatch + name + count)
plus the fill / grid / measure / light toggles."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from . import style
from .palette import layer_colors
from .viewport import GLViewport

_VIS_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_LID_ROLE = int(Qt.ItemDataRole.UserRole)
_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 2   # raw layer name (for reload matching)


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
    """Right column: clickable layer rows + fill / grid / measure / light toggles."""

    def __init__(self, layout, viewport: GLViewport, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self._vp = viewport
        cols = layer_colors(max(layout.n_layers, 1))
        self._qcolors = [QColor(int(r * 255), int(g * 255), int(b * 255))
                         for r, g, b in cols]

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 12, 10)
        root.setSpacing(9)

        head = QHBoxLayout()
        title = QLabel("LAYERS")
        hf = QFont(style.MONO_FAMILY, 12)
        hf.setBold(True)
        hf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        title.setFont(hf)
        self._count = QLabel(str(layout.n_layers))
        self._count.setStyleSheet("color: rgb(%d,%d,%d);" % style.MUTED)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._count)
        root.addLayout(head)
        root.addWidget(self._rule())

        self.list = QListWidget()
        self.list.itemClicked.connect(self._on_click)
        root.addWidget(self.list, 1)
        self._populate(layout)

        allnone = QHBoxLayout()
        allnone.setSpacing(4)
        b_all = QPushButton("all")
        b_none = QPushButton("none")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        slash = QLabel("/")
        slash.setStyleSheet("color: rgb(%d,%d,%d);" % style.DIM)
        for w in (b_all, slash, b_none):
            allnone.addWidget(w)
        allnone.addStretch(1)
        root.addLayout(allnone)
        root.addWidget(self._rule())

        self.fill_btn = QCheckBox("fill")
        self.fill_btn.setChecked(True)            # fill on by default
        self.fill_btn.toggled.connect(viewport.set_fill)
        self.grid_btn = QCheckBox("grid")
        self.grid_btn.setChecked(True)            # grid on by default
        self.grid_btn.toggled.connect(viewport.set_grid)
        self.measure_btn = QCheckBox("measure")
        self.measure_btn.toggled.connect(viewport.set_measure_mode)
        self.bg_btn = QCheckBox("light")
        self.bg_btn.toggled.connect(viewport.set_background)
        for b in (self.fill_btn, self.grid_btn, self.measure_btn, self.bg_btn):
            root.addWidget(b)

    def _populate(self, layout, visible_by_name=None):
        """(Re)fill the layer rows from ``layout``. ``visible_by_name`` restores
        per-layer visibility across a reload (layers not in the map default on)."""
        self.list.clear()
        for s in layout.layer_summary():
            vis = True if visible_by_name is None else visible_by_name.get(s.name, True)
            item = QListWidgetItem(f"{s.name.upper()}   {s.n_total:,}")
            item.setData(_LID_ROLE, s.layer_id)
            item.setData(_NAME_ROLE, s.name)
            item.setData(_VIS_ROLE, vis)
            self.list.addItem(item)
            self._restyle(item)

    def reload_layout(self, layout):
        """Rebuild the layer rows for a reloaded file, preserving which layers the
        user had hidden (matched by name), and push that visibility to the new
        scene. Call after the viewport has swapped in the new scene."""
        visible_by_name = {
            self.list.item(i).data(_NAME_ROLE): bool(self.list.item(i).data(_VIS_ROLE))
            for i in range(self.list.count())
        }
        cols = layer_colors(max(layout.n_layers, 1))
        self._qcolors = [QColor(int(r * 255), int(g * 255), int(b * 255))
                         for r, g, b in cols]
        self._count.setText(str(layout.n_layers))
        self._populate(layout, visible_by_name)
        scene = self._vp.scene
        if scene is not None:
            for i in range(self.list.count()):
                item = self.list.item(i)
                scene.set_layer_visible(item.data(_LID_ROLE), bool(item.data(_VIS_ROLE)))
            self._vp.update()

    def _rule(self) -> QFrame:
        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet("background: rgb(%d,%d,%d); border: none;" % style.HAIR)
        return f

    def _restyle(self, item: QListWidgetItem):
        lid = item.data(_LID_ROLE)
        vis = bool(item.data(_VIS_ROLE))
        item.setIcon(_swatch(self._qcolors[lid], vis))
        item.setForeground(style.qcolor(style.INK) if vis else style.qcolor(style.DIM))

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
