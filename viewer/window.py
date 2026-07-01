"""Top-level windows: a tabbed :class:`MainWindow` (one open layout per tab), the
per-tab :class:`LayoutView`, and the startup :class:`WelcomeWindow`."""

from __future__ import annotations

import os

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMainWindow,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from pydxf import DxfLayout

from . import style
from .overlay import _mono
from .panel import LayerPanel
from .viewport import GLViewport


class LayoutView(QWidget):
    """One open layout (a single tab): GL viewport + layer panel.

    Watches the DXF on disk and reparses it when an external program (e.g. a
    layout script) rewrites the file, so the view always reflects what's on
    disk. Reloads are debounced and gated on the file's size+mtime holding
    steady, so a program that writes the file incrementally (row by row) is
    reparsed once it settles rather than on every partial write."""

    # Wait this long after the last change event before reparsing. Every change
    # event restarts the clock, and we only reload once the file's stat is
    # stable across a full interval -- so a still-writing file is never parsed
    # mid-write, however fast the writes arrive.
    _RELOAD_DEBOUNCE_MS = 400

    def __init__(self, layout):
        super().__init__()
        self.layout_obj = layout
        self.viewport = GLViewport(layout)
        self.panel = LayerPanel(layout, self.viewport)
        self.panel.setMinimumWidth(180)           # can't be dragged to nothing
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.viewport)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1160, 240])
        splitter.setHandleWidth(1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self._splitter = splitter
        self._panel_w = 240
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(splitter)

        # --- auto-reload on external file change ---------------------------
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(self._RELOAD_DEBOUNCE_MS)
        self._reload_timer.timeout.connect(self._maybe_reload)
        self._loaded_sig = self._file_sig()       # stat of what's on screen
        self._pending_sig = self._loaded_sig      # last stat seen while settling
        self._watcher = QFileSystemWatcher(self)
        self._arm_watch()
        self._watcher.fileChanged.connect(self._on_fs_change)
        self._watcher.directoryChanged.connect(self._on_fs_change)

    # -- file watching ----------------------------------------------------
    def _file_sig(self):
        """(size, mtime_ns) of the file, or None if it's momentarily gone."""
        try:
            st = os.stat(self.layout_obj.path)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def _arm_watch(self):
        """Watch the file and its directory. A truncate-in-place keeps the file
        watch live; an atomic rename (write-temp-then-replace) drops it, so we
        also watch the directory and re-add the file whenever it reappears."""
        path = self.layout_obj.path
        if os.path.exists(path) and path not in self._watcher.files():
            self._watcher.addPath(path)
        d = os.path.dirname(path) or "."
        if os.path.isdir(d) and d not in self._watcher.directories():
            self._watcher.addPath(d)

    def _on_fs_change(self, *_):
        self._arm_watch()                 # re-arm (rename replaces the inode)
        self._reload_timer.start()        # debounce: coalesce a burst of writes

    def _maybe_reload(self):
        self._arm_watch()
        sig = self._file_sig()
        if sig is None:                   # gone mid-rename; a later event retriggers
            return
        if sig != self._pending_sig:      # still changing -> wait for it to settle
            self._pending_sig = sig
            self._reload_timer.start()
            return
        if sig == self._loaded_sig:       # stable and identical to what we show
            return
        self.reload()
        self._loaded_sig = self._pending_sig = self._file_sig()

    def reload(self):
        """Reparse the file on disk and rebuild the view in place, preserving the
        camera, layer visibility, and display toggles. A parse failure (e.g. a
        half-written file slipped past the stability gate) is swallowed and the
        current view kept, so the viewer never blanks or crashes on a bad read."""
        path = self.layout_obj.path
        try:
            new_layout = DxfLayout(path)
        except Exception:                 # noqa: BLE001 - partial/locked file: keep current
            return
        old = self.layout_obj
        self.layout_obj = new_layout
        self.viewport.reload_layout(new_layout)   # rebuild GPU scene (keeps camera)
        self.panel.reload_layout(new_layout)      # rebuild rows + reapply visibility
        old.close()                               # free old C buffers (scene aliased them)

    def toggle_panel(self):
        """Show / hide the layer panel (L)."""
        if self.panel.isVisible():
            self._panel_w = max(self.panel.width(), 180)
            self.panel.hide()
        else:
            self.panel.show()
            self._splitter.setSizes(
                [max(self.width() - self._panel_w, 200), self._panel_w])


class MainWindow(QMainWindow):
    """Tabbed window: one open layout per tab. ``app`` (a ViewerApp) opens files."""

    def __init__(self, app=None):
        super().__init__()
        self._app = app
        self.setWindowTitle("LINQS Layout")
        self.setAcceptDrops(True)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)
        self.resize(1400, 1000)

        # Status strip: cursor x/y (left), filename (right).
        self._status = QLabel()
        self._status.setContentsMargins(14, 0, 8, 0)
        self._status_file = QLabel()
        self._status_file.setContentsMargins(8, 0, 14, 0)
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.addWidget(self._status, 1)
        sb.addPermanentWidget(self._status_file)

        self._build_menu()
        QShortcut(QKeySequence("Esc"), self,
                  lambda: self._cur() and self._cur().viewport.clear_measure())

    # -- tabs -------------------------------------------------------------
    def add_layout(self, layout) -> LayoutView:
        view = LayoutView(layout)
        view.viewport.status_sink = self._status.setText
        idx = self.tabs.addTab(view, os.path.basename(layout.path))
        self.tabs.setTabToolTip(idx, layout.path)
        self.tabs.setCurrentIndex(idx)
        return view

    def _cur(self):
        return self.tabs.currentWidget()

    def _tab_changed(self, idx):
        view = self.tabs.widget(idx)
        if view is None:
            self.setWindowTitle("LINQS Layout")
            self._status.clear()
            self._status_file.clear()
            return
        name = os.path.basename(view.layout_obj.path)
        self.setWindowTitle(f"LINQS Layout — {name}")
        self._status.clear()
        self._status_file.setText(name)
        # Deferred (not called synchronously here): a brand-new tab's viewport
        # may not have its final geometry laid out yet at this exact point, so
        # mapping the cursor position now could use a stale/zero size. Letting
        # this run on the next event-loop turn ensures layout has settled.
        QTimer.singleShot(0, view.viewport.emit_status_at_cursor)

    def _close_tab(self, idx):
        view = self.tabs.widget(idx)
        self.tabs.removeTab(idx)
        if view is not None:
            view.deleteLater()
        if self.tabs.count() == 0:
            self.close()

    # -- menu -------------------------------------------------------------
    def _build_menu(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open)
        file_menu.addAction(act_open)
        act_close = QAction("Close Tab", self)
        act_close.setShortcut(QKeySequence.StandardKey.Close)
        act_close.triggered.connect(lambda: self._close_tab(self.tabs.currentIndex()))
        file_menu.addAction(act_close)
        act_reload = QAction("Reload", self)
        act_reload.setShortcut(QKeySequence("Ctrl+R"))       # ⌘R on macOS
        act_reload.triggered.connect(lambda: self._cur() and self._cur().reload())
        file_menu.addAction(act_reload)
        file_menu.addSeparator()
        act_keys = QAction("Keybindings", self)
        act_keys.triggered.connect(self._show_keybindings)
        file_menu.addAction(act_keys)
        act_upd = QAction("Check for Updates…", self)
        act_upd.triggered.connect(self._check_updates)
        file_menu.addAction(act_upd)

        view_menu = bar.addMenu("View")

        def on_cur(fn):
            return lambda: (self._cur() is not None) and fn(self._cur())

        for key, label, fn in (
            ("R", "Reset View", lambda v: v.viewport.reset_view()),
            ("F", "Toggle Fill", lambda v: v.panel.fill_btn.toggle()),
            ("G", "Toggle Grid", lambda v: v.panel.grid_btn.toggle()),
            ("B", "Light / Dark", lambda v: v.panel.bg_btn.toggle()),
            ("L", "Toggle Layer Panel", lambda v: v.toggle_panel()),
            ("M", "Measure", lambda v: v.panel.measure_btn.toggle()),
        ):
            act = QAction(label, self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(on_cur(fn))
            view_menu.addAction(act)

    def _show_keybindings(self):
        rows = [
            ("scroll", "zoom at cursor"),
            ("drag", "pan"),
            ("R", "reset view"),
            ("click layer", "show / hide layer"),
            ("L", "toggle layer panel"),
            ("M", "measure tool"),
            ("shift (measure)", "constrain to horizontal / vertical"),
            ("F", "toggle fill"),
            ("G", "toggle grid"),
            ("B", "light / dark"),
            ("esc", "clear measurement"),
            (style.key_label("O"), "open file"),
            (style.key_label("W"), "close tab"),
        ]
        w = max(len(k) for k, _ in rows)
        text = "\n".join(f"{k.ljust(w)}    {v}" for k, v in rows)
        dlg = QDialog(self)
        dlg.setWindowTitle("Keybindings")
        dlg.setStyleSheet("background: rgb(%d,%d,%d);" % style.CANVAS)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 20, 22, 20)
        lbl = QLabel(text)
        lbl.setFont(_mono(13))
        lbl.setStyleSheet("color: rgb(%d,%d,%d);" % style.INK)
        lay.addWidget(lbl)
        dlg.exec()

    def _open(self):
        if self._app is not None:
            self._app.prompt_open()

    def _check_updates(self):
        from .update import check_for_updates
        check_for_updates(self)

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

        hint = QLabel(f"Press  {style.key_label('O')}  to open a DXF file")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: white; font-size: 20px;")
        v.addWidget(hint)

        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu("File")
        act_open = QAction("Open…", self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(lambda: self._app.prompt_open())
        file_menu.addAction(act_open)
        act_upd = QAction("Check for Updates…", self)
        act_upd.triggered.connect(self._check_updates)
        file_menu.addAction(act_upd)

    def _check_updates(self):
        from .update import check_for_updates
        check_for_updates(self)

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
