"""The GL viewport widget: a QOpenGLWidget hosting a GLScene, with pan,
zoom-at-cursor, the light/dark background toggle, and the snapping measuring tool.

Coordinates are kept in logical pixels; the camera only depends on the viewport
aspect ratio, so it renders correctly on Retina without explicit
devicePixelRatio handling."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from . import style
from .camera import Camera2D
from .offscreen import BG_DARK, BG_LIGHT
from .overlay import MeasureOverlay
from .scene import GLScene, nice_grid_spacing
from .snap import Snapper

# Snapping (Snapper.snap) is a full-geometry numpy scan -- a few ms on large
# layouts. Recomputing it synchronously on every raw mouse-move event (no
# coalescing, unlike paint/update()) can fall behind the OS's mouse-move rate
# and visibly lag/jump, especially where Windows delivers move events faster
# than macOS's more aggressively-coalesced ones. Throttle to one recompute
# per ~frame instead, always using the latest position.
_MEASURE_THROTTLE_MS = 16

# A left press+release that moves less than this (pixels) is treated as a click
# (select the polygon under it) rather than a pan.
_CLICK_SLOP_PX = 4


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
        self._press = None             # left-press pixel pos (for click-vs-drag)
        self._dragged = False          # moved far enough since press to count as a pan
        self.picker = None             # built lazily on the first pick
        self._selection: list[int] = []  # picked polygon ids (Shift adds to the set)
        self.bg = BG_DARK
        self._light = False

        self._thickness = None          # loaded ThicknessMap (re-applied on reload)

        self.measure_mode = False
        self.measure_points: list[tuple[float, float]] = []
        self.measure_cursor = None
        self.snap_kind = None                   # 'corner' | 'edge' | None (live)
        self.snap: Snapper | None = None        # built lazily — keeps startup fast
        self.snap_px = 12
        self._pending_measure_move = None       # (px, py, shift) awaiting throttled snap
        self._measure_move_timer = QTimer(self)
        self._measure_move_timer.setSingleShot(True)
        self._measure_move_timer.setInterval(_MEASURE_THROTTLE_MS)
        self._measure_move_timer.timeout.connect(self._on_measure_move_timeout)

        self.status_sink = None                 # callable(str): bottom status strip
        # Catch-all for emit_status_at_cursor: there is no single Qt event for
        # "this widget's content changed while the cursor was already sitting
        # on top of it and never physically moved" (new tab, resize settling,
        # etc. can all leave the status bar blank otherwise -- confirmed by
        # hand that hooking just tab-change + resize still missed a case).
        # Cheap (a position compare + an occasional label set), so it just
        # runs for the viewport's whole lifetime rather than trying to
        # enumerate every trigger moment.
        self._status_poll_timer = QTimer(self)
        self._status_poll_timer.setInterval(150)
        self._status_poll_timer.timeout.connect(self.emit_status_at_cursor)
        self._status_poll_timer.start()

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
        # The numeric values need their own explicit color: QStatusBar's
        # stylesheet rule (color: muted -- a dim gray, chosen for the
        # filename/static labels elsewhere in the strip) would otherwise be
        # the only color applied to this rich-text label, since only the
        # "x"/"y" letters get an inline color here. On a real monitor that
        # dim gray on the near-black canvas background is barely legible at
        # 11px -- reported as "the x/y letters are there but the numbers
        # aren't". Ink is the same bright color used for primary body text.
        ink = "#%02x%02x%02x" % style.INK
        self.status_sink(
            f'<span style="color:{a}">x</span> <span style="color:{ink}">{wx:,.1f}</span>'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{a}">y</span> <span style="color:{ink}">{wy:,.1f}</span>')

    def emit_status_at_cursor(self):
        """Populate the status bar from the cursor's current position, without
        waiting for a mouseMoveEvent. A move event only fires once the OS
        cursor actually crosses into the widget -- there is no Qt event for
        "this widget's content/visibility changed while the cursor was
        already sitting on top of it and never physically moved", which is
        the actual gap: opening a file, switching tabs, or a window being
        resized while the cursor happens to already be positioned over the
        canvas all leave the status bar blank indefinitely otherwise. Calling
        this from a few specific triggers (tab change, resize) narrows the
        window but doesn't close it (confirmed: a resize-then-cursor-move-
        with-no-further-trigger sequence still went unnoticed) -- see the
        periodic timer in __init__ for the actual fix; this method is also
        called directly at those trigger points for a faster response when
        they do line up."""
        # Background tabs share the active tab's geometry inside the QTabWidget,
        # so their rect() still "contains" the cursor -- without this guard an
        # inactive tab's poll timer would keep writing its own camera's coords
        # to the shared status label, and the coordinates would revert to
        # whichever tab won the race (usually the first one).
        if not self.isVisible():
            return
        from PySide6.QtGui import QCursor
        local = self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(local):
            self._emit_status(local.x(), local.y())

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
        # Belt-and-suspenders for emit_status_at_cursor: the very first time a
        # window appears, its on-screen geometry may not be final yet at the
        # point _tab_changed's deferred call runs, so a stationary cursor
        # could be (wrongly) judged as outside the widget. resizeGL fires
        # again once layout truly settles, so retry here too -- harmless if
        # the first attempt already got it right.
        self.emit_status_at_cursor()

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

    def _apply_pending_measure_move(self):
        """Run the throttled snap query for the latest pending cursor position."""
        if self._pending_measure_move is None:
            return
        px, py, shift = self._pending_measure_move
        self._pending_measure_move = None
        self.measure_cursor, self.snap_kind = self._measure_point(px, py, shift)
        self.overlay.update()

    def _on_measure_move_timeout(self):
        if self._pending_measure_move is not None:
            self._apply_pending_measure_move()
            self._measure_move_timer.start()    # keep throttling while moves keep coming

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
            self._press = (p.x(), p.y())
            self._dragged = False

    def mouseMoveEvent(self, e):
        p = e.position()
        self._emit_status(p.x(), p.y())
        if self.measure_mode:
            # Live snap / ortho-constraint indicator under the cursor. Throttled
            # (see _MEASURE_THROTTLE_MS) since the snap query is too expensive to
            # redo synchronously on every raw move event without falling behind.
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._pending_measure_move = (p.x(), p.y(), shift)
            if not self._measure_move_timer.isActive():
                self._apply_pending_measure_move()      # immediate for the first event
                self._measure_move_timer.start()        # then hold off briefly
            return
        if self._last is not None:
            if self._press is not None and (
                    abs(p.x() - self._press[0]) > _CLICK_SLOP_PX
                    or abs(p.y() - self._press[1]) > _CLICK_SLOP_PX):
                self._dragged = True         # a real pan, not a click
            self.cam.pan_pixels(p.x() - self._last[0], p.y() - self._last[1])
            self._last = (p.x(), p.y())
            self._user_view = True
            self._refresh()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        # A left click that didn't pan (and isn't the measuring tool) selects the
        # polygon under the cursor and highlights its edges + vertices.
        if not self.measure_mode and not self._dragged and self._press is not None:
            additive = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._pick_at(*self._press, additive=additive)
        self._last = None
        self._press = None
        self._dragged = False

    # -- selection --------------------------------------------------------
    def _pick_at(self, px, py, additive=False):
        """Select the polygon under screen pixel (px, py) and highlight it.

        A plain click replaces the selection (clearing it on a miss); a Shift
        click (``additive``) toggles that polygon in/out of the current set and
        leaves the set unchanged on a miss, so several polygons can be selected."""
        if self.scene is None or self.ctx is None:
            return
        from .pick import Picker
        if self.picker is None:
            self.picker = Picker(self._layout)
        wx, wy = self.cam.screen_to_world(px, py)
        visible = self.scene.visible > 0.5
        idx = self.picker.pick(wx, wy, visible)
        if additive:
            if idx is None:
                return                              # miss: keep the current set
            if idx in self._selection:
                self._selection.remove(idx)         # toggle off
            else:
                self._selection.append(idx)
        else:
            self._selection = [] if idx is None else [idx]
        self._apply_selection()

    def _apply_selection(self):
        """Push the current ``_selection`` set to the GPU highlight."""
        polys = [(self.picker.poly_verts(i), self.picker.is_closed(i))
                 for i in self._selection]
        self.makeCurrent()
        try:
            self.scene.set_selection(polys)
        finally:
            self.doneCurrent()
        self.update()

    def clear_selection(self):
        self._selection = []
        if self.scene is not None and self.ctx is not None and self.scene.has_selection():
            self.makeCurrent()
            try:
                self.scene.set_selection(None)
            finally:
                self.doneCurrent()
            self.update()

    # -- API for the panel / shortcuts -----------------------------------
    def set_measure_mode(self, on: bool):
        self.measure_mode = bool(on)
        if on:
            self.clear_selection()            # highlight is only shown outside measure mode
        if on and self.snap is None:          # build the snapper up front, once
            self.snap = Snapper(self._layout)
        if not on:
            self.snap_kind = None
            self._pending_measure_move = None
            self._measure_move_timer.stop()
        # NOTE: mouse tracking is left permanently on (set once in __init__) for the
        # always-live status-bar x/y -- it must not be tied to measure_mode here, or
        # turning measure mode off again disables hover-move events (and therefore
        # the status bar) for the rest of the session.
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

    def load_thickness_map(self, path: str):
        """Load a thickness CSV and upload it as the background colormap. Returns
        the :class:`~viewer.thickness.ThicknessMap` (for the legend). Raises on a
        bad/empty file; the caller surfaces the error."""
        from .thickness import load_thickness_csv
        tmap = load_thickness_csv(path)         # CPU gridding (may raise) before touching GL
        self._thickness = tmap
        if self.scene is not None and self.ctx is not None:
            self.makeCurrent()
            try:
                self.scene.set_thickness(tmap)
            finally:
                self.doneCurrent()
            self.update()
        return tmap

    def clear_thickness_map(self):
        self._thickness = None
        if self.scene is not None and self.ctx is not None:
            self.makeCurrent()
            try:
                self.scene.set_thickness(None)
            finally:
                self.doneCurrent()
            self.update()

    def set_thickness_visible(self, on: bool):
        if self.scene is not None:
            self.scene.set_thickness_visible(bool(on))
            self.update()

    def has_thickness_map(self) -> bool:
        return self._thickness is not None

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

    def reload_layout(self, layout):
        """Swap in a freshly-parsed layout (same file, changed on disk): rebuild
        the GPU scene from scratch, carrying over the current view and display
        state. The old scene's GL objects are released first so repeated reloads
        don't leak. The caller keeps the *new* layout alive and closes the old
        one only after this returns (the scene aliases the layout's arrays)."""
        self._layout = layout
        self.snap = None                # rebuilt lazily against the new geometry
        self.picker = None              # rebuilt lazily against the new geometry
        self._selection = []            # ids refer to old geometry; drop them
        self.clear_measure()            # old measurement refers to the old geometry
        # The new scene starts with no selection; the old one referred to old
        # geometry, so there's nothing to carry over (and nothing to clear on GPU).
        if self.ctx is None:
            return                      # GL not initialized yet; initializeGL will build it
        old = self.scene
        self.makeCurrent()
        try:
            scene = GLScene(self.ctx, layout)
            if old is not None:         # carry over view-independent display state
                scene.show_fill = old.show_fill
                scene.show_grid = old.show_grid
            if self._thickness is not None:     # re-upload the field for the new scene
                scene.set_thickness(self._thickness)
                scene.show_thickness = old.show_thickness if old is not None else True
            scene.set_shade(0.55 if self._light else 1.0)
            if old is not None:
                old.release()
            self.scene = scene
        finally:
            self.doneCurrent()
        if not self._user_view:         # keep the user's pan/zoom; refit only if untouched
            self.cam.fit(self._layout.bbox())
        self._refresh()
