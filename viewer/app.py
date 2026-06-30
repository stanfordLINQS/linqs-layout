"""Application entry point: window management and file opening.

Handles three ways a DXF arrives:
  * a path on the command line / passed to :func:`run`,
  * the macOS "Open With" / drop-on-dock-icon event (``QFileOpenEvent``),
  * File ▸ Open… or dragging a file onto a window.

Each opened layout gets its own window.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QFont, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from pydxf import DxfLayout

from . import style
from .qt_app import MainWindow, WelcomeWindow


def _configure_format():
    """Request an OpenGL 4.1 core context (required by moderngl on macOS)."""
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    QSurfaceFormat.setDefaultFormat(fmt)


class ViewerApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName("LINQS Layout")
        self.setApplicationDisplayName("LINQS Layout")
        self.setOrganizationName("Stanford LINQS")
        self.setFont(QFont(style.MONO_FAMILY, 12))
        self.setStyleSheet(style.stylesheet())
        self._windows: list[MainWindow] = []
        self._welcome = None

    # macOS delivers double-clicked / "Open With" files as a FileOpen event.
    def event(self, e):
        if e.type() == QEvent.Type.FileOpen:
            self.open_path(e.file())
            return True
        return super().event(e)

    def open_path(self, path: str):
        try:
            layout = DxfLayout(path)
        except Exception as ex:  # noqa: BLE001 - surface any load failure to the user
            QMessageBox.critical(None, "Open failed", f"Could not open:\n{path}\n\n{ex}")
            return None
        win = MainWindow(layout, app=self)
        win.show()
        win.raise_()
        win.activateWindow()
        self._windows.append(win)
        win.destroyed.connect(lambda *_: self._forget(win))
        if self._welcome is not None:           # a file opened -> retire the welcome screen
            self._welcome.close()
            self._welcome = None
        return win

    def _forget(self, win):
        if win in self._windows:
            self._windows.remove(win)

    def show_welcome(self):
        if self._welcome is None:
            self._welcome = WelcomeWindow(self)
        self._welcome.show()
        self._welcome.raise_()
        self._welcome.activateWindow()

    def prompt_open(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            None, "Open DXF layout", "", "DXF files (*.dxf);;All files (*)")
        if path:
            self.open_path(path)
        return bool(path)

    @property
    def windows_open(self) -> int:
        return len(self._windows)


def run(layout) -> int:
    """Open a window for an already-parsed :class:`DxfLayout` (programmatic use)."""
    _configure_format()
    app = QApplication.instance()
    if not isinstance(app, ViewerApp):
        app = ViewerApp(sys.argv[:1])
    win = MainWindow(layout, app=app)
    win.show()
    win.raise_()
    app._windows.append(win)
    return app.exec()


def main(argv=None) -> int:
    """Full application entry: open files from argv, else prompt; then run."""
    _configure_format()
    app = ViewerApp(list(argv) if argv is not None else sys.argv)

    for arg in app.arguments()[1:]:
        if arg.lower().endswith(".dxf"):
            app.open_path(arg)

    # If nothing opened (and no macOS FileOpen event arrives shortly), show the
    # welcome screen. Closing it (without opening a file) quits the app.
    def _welcome_if_idle():
        if app.windows_open == 0:
            app.show_welcome()

    QTimer.singleShot(250, _welcome_if_idle)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
