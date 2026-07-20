"""The right-hand layer panel: clickable layer rows (color swatch + name + count)
plus the fill / grid / measure / light toggles."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QDoubleValidator, QFont, QIcon, QImage,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from . import style
from .palette import layer_colors
from .thickness import colormap_lut
from .viewport import GLViewport


class ThicknessLegend(QWidget):
    """A horizontal plasma colorbar with *editable* min/max thickness (nm) fields.
    Hidden until a map is loaded. ``on_loaded`` seeds the data range (and an
    ``auto`` reset target); editing either field — or hitting ``auto`` — emits
    :attr:`rangeChanged(vmin, vmax)`."""

    rangeChanged = Signal(float, float)

    _BAR_H = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        lut = (colormap_lut(256) * 255).astype("uint8")   # (256,3)
        img = QImage(256, 1, QImage.Format.Format_RGB888)
        for i, (r, g, b) in enumerate(lut):
            img.setPixelColor(i, 0, QColor(int(r), int(g), int(b)))
        self._grad = QPixmap.fromImage(img)
        self._dmin = self._dmax = None      # the data's own range (for `auto`)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(3)

        cap_row = QHBoxLayout()
        cap_row.setContentsMargins(0, 0, 0, 0)
        cap = QLabel("THICKNESS  (nm)")
        cf = QFont(style.MONO_FAMILY, 9)
        cf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        cap.setFont(cf)
        cap.setStyleSheet("color: rgb(%d,%d,%d);" % style.MUTED)
        self._auto = QPushButton("auto")
        self._auto.setToolTip("Reset the colorbar range to the data min/max")
        self._auto.clicked.connect(self._reset)
        cap_row.addWidget(cap)
        cap_row.addStretch(1)
        cap_row.addWidget(self._auto)
        v.addLayout(cap_row)

        self._bar = QLabel()
        self._bar.setFixedHeight(self._BAR_H)
        self._bar.setScaledContents(True)
        v.addWidget(self._bar)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._lo = self._field()
        self._hi = self._field()
        self._hi.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._lo.editingFinished.connect(self._emit)
        self._hi.editingFinished.connect(self._emit)
        row.addWidget(self._lo)
        row.addStretch(1)
        row.addWidget(self._hi)
        v.addLayout(row)

    def _field(self) -> QLineEdit:
        e = QLineEdit()
        e.setValidator(QDoubleValidator())
        e.setFixedWidth(66)
        e.setFont(QFont(style.MONO_FAMILY, 9))
        e.setStyleSheet(
            "QLineEdit { color: rgb(%d,%d,%d); background: transparent;"
            " border: 1px solid rgb(%d,%d,%d); padding: 1px 3px; }"
            % (style.INK + style.HAIR))
        return e

    def resizeEvent(self, e):
        self._bar.setPixmap(self._grad)     # QLabel scales it to width
        super().resizeEvent(e)

    def on_loaded(self, vmin: float, vmax: float):
        """Seed both fields (and the `auto` target) from a freshly loaded map."""
        self._dmin, self._dmax = vmin, vmax
        self._set_fields(vmin, vmax)

    def _reset(self):
        if self._dmin is None:
            return
        self._set_fields(self._dmin, self._dmax)
        self._emit()

    def _set_fields(self, vmin: float, vmax: float):
        self._bar.setPixmap(self._grad)
        self._lo.setText(f"{vmin:.1f}")
        self._hi.setText(f"{vmax:.1f}")

    def _emit(self):
        try:
            lo, hi = float(self._lo.text()), float(self._hi.text())
        except ValueError:
            return
        if hi <= lo:                        # keep the range non-degenerate
            return
        self.rangeChanged.emit(lo, hi)

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
        self.thick_btn = QCheckBox("thickness")
        self.thick_btn.setEnabled(False)          # no map loaded yet
        self.thick_btn.toggled.connect(viewport.set_thickness_visible)
        for b in (self.fill_btn, self.grid_btn, self.measure_btn, self.bg_btn,
                  self.thick_btn):
            root.addWidget(b)

        self.legend = ThicknessLegend()
        self.legend.hide()                        # shown once a map loads
        self.legend.rangeChanged.connect(viewport.set_thickness_range)
        root.addWidget(self.legend)

    def on_thickness_loaded(self, tmap):
        """Enable + check the thickness toggle and show the colorbar legend."""
        self.thick_btn.setEnabled(True)
        self.thick_btn.blockSignals(True)
        self.thick_btn.setChecked(True)           # loading a map turns it on
        self.thick_btn.blockSignals(False)
        self.legend.on_loaded(tmap.vmin, tmap.vmax)
        self.legend.show()

    def on_thickness_cleared(self):
        self.thick_btn.blockSignals(True)
        self.thick_btn.setChecked(False)
        self.thick_btn.blockSignals(False)
        self.thick_btn.setEnabled(False)
        self.legend.hide()

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
