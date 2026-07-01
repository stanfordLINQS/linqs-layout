"""HUD overlay drawn on top of the GL viewport: the scale bar, measurement
markers, and the distance readout. Mouse-transparent so the viewport still gets
clicks. Drawn with QPainter (the geometry is GL; this is just the overlay)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import style


def _format_dist(v: float) -> str:
    """Format a layout distance (assumed microns) as µm / mm."""
    return f"{v / 1000.0:g} mm" if v >= 1000.0 else f"{v:g} µm"


def _mono(size: int, bold: bool = False) -> QFont:
    f = QFont(style.MONO_FAMILY, size)
    f.setBold(bold)
    return f


class MeasureOverlay(QWidget):
    """Transparent HUD over the GL viewport: markers, the measured segment, and
    the distance readout. Mouse-transparent so the viewport still gets clicks."""

    def __init__(self, viewport):
        super().__init__(viewport)
        self._vp = viewport
        self.loading_text = None            # non-None -> show a "Reloading…" pill
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_loading(self, text):
        """Show (``text``) or hide (``None``) a top-center status pill, e.g. while
        the layout is being reparsed on a background thread."""
        self.loading_text = text or None
        self.update()

    def _draw_loading(self, p):
        txt = self.loading_text
        if not txt:
            return
        amber = style.qcolor(style.ACCENT)
        p.setFont(_mono(12, True))
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(txt) + 34, fm.height() + 16
        x, y = (self.width() - w) / 2.0, 18.0
        box = QRectF(x, y, w, h)
        p.setPen(QPen(amber, 1.0))
        p.setBrush(style.qcolor(style.CANVAS, 235))       # flat box, 1px amber border
        p.drawRoundedRect(box, 6, 6)
        p.setPen(amber)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, txt)

    def _draw_scale_bar(self, p):
        """Bottom-left scale bar: one grid cell, labeled in µm/mm (high contrast)."""
        vp = self._vp
        scene = vp.scene
        if scene is None or vp.cam.upp <= 0:
            return
        g = getattr(scene, "grid_spacing", 0.0)
        if g <= 0:
            return
        length = g / vp.cam.upp                       # one grid cell, in logical px
        x0, y0 = 18.0, self.height() - 18.0
        # Neutral, high-contrast against the background (not the amber accent).
        col = QColor(20, 20, 25) if vp.is_light() else style.qcolor(style.INK)
        p.setPen(QPen(col, 1.5))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + length, y0))
        p.drawLine(QPointF(x0, y0 - 4), QPointF(x0, y0 + 4))
        p.drawLine(QPointF(x0 + length, y0 - 4), QPointF(x0 + length, y0 + 4))
        p.setFont(_mono(10, True))
        p.setPen(col)
        p.drawText(QPointF(x0, y0 - 8), _format_dist(g))

    def paintEvent(self, _e):
        vp = self._vp
        cam = vp.cam
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        amber = style.qcolor(style.ACCENT)

        self._draw_scale_bar(p)
        self._draw_loading(p)

        chain = list(vp.measure_points)
        if len(chain) == 1 and vp.measure_cursor is not None:
            chain = [chain[0], vp.measure_cursor]
        show_snap = (vp.measure_mode and vp.snap_kind in ("corner", "edge")
                     and vp.measure_cursor is not None)
        if not chain and not show_snap:
            p.end()
            return

        def to_s(pt):
            sx, sy = cam.world_to_screen(pt[0], pt[1])
            return QPointF(sx, sy)

        # Live snap indicator under the cursor (square = corner, circle = edge).
        if show_snap:
            s = to_s(vp.measure_cursor)
            p.setPen(QPen(amber, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if vp.snap_kind == "corner":
                p.drawRect(int(s.x() - 6), int(s.y() - 6), 12, 12)
                p.setBrush(amber)
                p.drawEllipse(s, 1.6, 1.6)
            else:
                p.drawEllipse(s, 6, 6)

        if len(chain) == 2:
            pen = QPen(amber, 1.4)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(to_s(chain[0]), to_s(chain[1]))

        p.setPen(QPen(amber, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pt in chain:
            s = to_s(pt)
            p.drawLine(QPointF(s.x() - 6, s.y()), QPointF(s.x() + 6, s.y()))
            p.drawLine(QPointF(s.x(), s.y() - 6), QPointF(s.x(), s.y() + 6))

        if len(chain) == 2:
            (x0, y0), (x1, y1) = chain
            dx, dy = x1 - x0, y1 - y0
            dist = (dx * dx + dy * dy) ** 0.5
            # Measurements in µm at 1 nm resolution (3 decimals).
            txt = f"{dist:,.3f} µm   Δx {dx:,.3f}  Δy {dy:,.3f}"
            mid = to_s(((x0 + x1) / 2, (y0 + y1) / 2))
            p.setFont(_mono(11, True))
            fm = p.fontMetrics()
            tw, th = fm.horizontalAdvance(txt), fm.height()
            tx, ty = mid.x() + 10, mid.y() - 10
            p.setPen(QPen(amber, 1.0))
            p.setBrush(style.qcolor(style.CANVAS, 225))      # flat box, 1px amber border
            p.drawRect(int(tx - 6), int(ty - th), tw + 12, th + 6)
            p.setPen(amber)
            p.drawText(QPointF(tx, ty - 4), txt)
        p.end()
