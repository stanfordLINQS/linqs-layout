"""Application entry point: window management and file opening.

Handles three ways a DXF arrives:
  * a path on the command line / passed to :func:`run`,
  * the macOS "Open With" / drop-on-dock-icon event (``QFileOpenEvent``),
  * File ▸ Open… or dragging a file onto a window.

Every opened layout is a tab in the single shared window -- including one
opened from a *separate* process launch (e.g. selecting several files in
Explorer and hitting Enter spawns one OS process per file, or double-clicking
a second file while the app is already running). See ViewerApp.claim_primary
and try_relay_to_running_instance: a new process first tries to hand its
file(s) to whichever process already holds the IPC socket, and only opens
its own window if none does.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QSharedMemory, QTimer
from PySide6.QtGui import QFont, QSurfaceFormat
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from . import style
from .window import LoadingWindow, MainWindow, WelcomeWindow, _ParseThread

# Per-user names for single-instance coordination (relaying file opens to an
# already-running process instead of spawning a second window). Suffixed so
# neither can collide with an unrelated app of the same generic name.
_IPC_SERVER_NAME = "LINQSLayout-e6f2b8a1-ipc"
# A *separate* primitive from the IPC socket above decides who's primary:
# QLocalServer.listen() succeeding is not a safe signal of that on Windows,
# where multiple independent processes can each successfully create their own
# named-pipe instance under the very same name (unlike a Unix domain socket
# path, which a second bind() to the same path genuinely fails). Confirmed by
# hand: launching 3 processes within the same second all had isListening()
# return True and each opened its own window. QSharedMemory.create() *is*
# exclusive cross-platform (Windows releases the segment automatically when
# every attached process exits, so a crashed primary doesn't wedge it).
_IPC_LOCK_KEY = "LINQSLayout-e6f2b8a1-lock"


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
        self._main = None                       # the single tabbed window
        self._welcome = None
        self._update_checked = False            # auto update-check runs once
        self._ipc_server = None
        self._ipc_conns = []                    # keep incoming sockets alive until handled
        self._ipc_lock = None                   # QSharedMemory; held for our lifetime iff primary
        self._open_queue = []                   # paths waiting to be parsed
        self._open_thread = None                # current background parse thread
        self._opening = False                   # a parse is in flight
        self._loading = None                    # LoadingWindow shown while parsing

    # macOS delivers double-clicked / "Open With" files as a FileOpen event.
    def event(self, e):
        if e.type() == QEvent.Type.FileOpen:
            self.open_path(e.file())
            return True
        return super().event(e)

    # -- single-instance IPC ------------------------------------------------
    def claim_primary(self) -> bool:
        """Exclusively claim primary-instance status via a shared-memory
        segment (see _IPC_LOCK_KEY's comment for why this -- not
        QLocalServer.listen() -- is what has to be exclusive). True: this
        process won and should start the IPC server and open its own
        window(s). False: someone else already holds it; relay instead."""
        self._ipc_lock = QSharedMemory(_IPC_LOCK_KEY)
        if self._ipc_lock.create(1):
            return True
        # Didn't win. On Linux, a segment can outlive every process that held
        # it if the last one didn't detach cleanly (e.g. killed); attaching
        # once and releasing clears that stale case, matching Windows (which
        # always releases automatically) and macOS. Only relevant if the
        # first create() attempt above already failed, so it costs nothing
        # in the normal (someone else legitimately holds it) case.
        if self._ipc_lock.attach():
            self._ipc_lock.detach()
        return False

    # The message is terminated with an explicit marker and acknowledged with
    # a single "OK" byte string, rather than treating the client's disconnect
    # as the end-of-message signal. An earlier version disconnected right
    # after write()+flush(), which across two independent OS processes is a
    # real race: the client can tear down its end of the connection before
    # the server's event loop has gotten around to actually reading what was
    # already flushed, silently dropping the relayed file paths even though
    # the client-side connect+write+disconnect all reported success. Waiting
    # for an explicit ack closes that race -- the client now has proof the
    # server actually processed the message before it exits.
    _IPC_END = b"\n--END--\n"
    _IPC_ACK = b"OK"

    def try_relay_to_running_instance(self, paths: list[str]) -> bool:
        """If another instance already holds the IPC socket, hand it these
        paths (possibly none, just to raise its window) and report success --
        the caller should then exit without creating any window of its own.
        A short local-socket round trip; false only means no instance is
        currently listening (or the pipe was transiently busy -- see below),
        so the caller's retry loop should try again shortly rather than
        treat this as final."""
        sock = QLocalSocket()
        sock.connectToServer(_IPC_SERVER_NAME)
        if not sock.waitForConnected(250):
            return False
        payload = "\n".join(paths).encode("utf-8") + self._IPC_END
        sock.write(payload)
        # flush()'s return value (not waitForBytesWritten's) is what actually
        # matters here: waitForBytesWritten can legitimately return False even
        # on a fully successful write, if the OS already accepted the data
        # synchronously during flush() -- by the time waitForBytesWritten
        # starts waiting there's no *new* bytesWritten signal left to catch,
        # so it reports "nothing happened," not "the write failed." Confirmed
        # by hand: the server received the complete message even on a run
        # where waitForBytesWritten came back False. Treating that as failure
        # was silently discarding every relay.
        #
        # flush() itself CAN genuinely fail here under real contention, though
        # -- confirmed by hand with several processes launched at once: Qt's
        # named-pipe backend on Windows needs a moment to re-arm a fresh pipe
        # instance after each accepted connection, and a connect attempt
        # landing in that gap can report "connected" at the Win32 level while
        # the write immediately after fails (the server never even sees a
        # readyRead for it). It's transient -- a subsequent attempt with a
        # brand-new socket succeeds -- so the caller's retry loop is what
        # actually handles it, not anything to fix here.
        if not sock.flush():
            sock.disconnectFromServer()
            return False
        acked = sock.waitForReadyRead(1500) and bytes(sock.readAll()) == self._IPC_ACK
        sock.disconnectFromServer()
        return acked

    def start_ipc_server(self):
        """Become the primary instance: listen for file paths relayed from
        subsequent launches of the app. removeServer first clears a stale
        socket left behind if the previous primary instance crashed instead
        of shutting down cleanly (a stale entry would otherwise make listen()
        fail forever after)."""
        QLocalServer.removeServer(_IPC_SERVER_NAME)
        self._ipc_server = QLocalServer(self)
        self._ipc_server.newConnection.connect(self._on_ipc_connection)
        self._ipc_server.listen(_IPC_SERVER_NAME)

    def _on_ipc_connection(self):
        # newConnection can fire once even when several connections arrived
        # close together (e.g. selecting multiple files in Explorer spawns
        # several processes within the same instant) -- nextPendingConnection
        # must be drained in a loop, not called once per signal, or a second
        # concurrent connection is left sitting in the queue forever. Found
        # by hand: 3 processes launched at once, one became primary, one
        # relayed fine, and the third retried for 8 straight seconds without
        # ever getting in, because its connection was queued behind the one
        # this method had already accepted and never got dequeued.
        while True:
            sock = self._ipc_server.nextPendingConnection()
            if sock is None:
                break
            self._accept_ipc_connection(sock)

    def _accept_ipc_connection(self, sock):
        self._ipc_conns.append(sock)
        buf = bytearray()

        def on_ready():
            buf.extend(bytes(sock.readAll()))
            if not buf.endswith(self._IPC_END):
                return                          # more data (or the marker itself) still coming
            text = bytes(buf[:-len(self._IPC_END)]).decode("utf-8", "ignore")
            for p in text.splitlines():
                if p.strip():
                    self.open_path(p.strip())
            w = self._main or self._welcome
            if w is not None:
                w.show()
                w.raise_()
                w.activateWindow()
            # Write the ack and stop -- no blocking wait here. waitForBytesWritten
            # inside a slot connected to this same socket's readyRead is a real
            # deadlock risk (nested event-loop reentrancy on the same object);
            # the client will read this once it arrives, then disconnect on its
            # own, which is what actually tears the connection down.
            sock.write(self._IPC_ACK)
            sock.flush()

        def on_disconnected():
            if sock in self._ipc_conns:
                self._ipc_conns.remove(sock)
            sock.deleteLater()

        sock.readyRead.connect(on_ready)
        sock.disconnected.connect(on_disconnected)

    def _ensure_main(self):
        if self._main is None:
            self._main = MainWindow(app=self)
            self._main.show()
            self._main.destroyed.connect(self._forget_main)
            self._maybe_check_updates()
        return self._main

    def _maybe_check_updates(self):
        """Run the automatic update check once, from whichever window is up first
        (the welcome screen or the main window)."""
        if self._update_checked:
            return
        self._update_checked = True
        from .update import check_for_updates

        def fire():
            w = self._main or self._welcome
            if w is not None:
                check_for_updates(w, silent=True)

        QTimer.singleShot(1500, fire)

    def _forget_main(self, *_):
        self._main = None

    def add_layout(self, layout):
        """Open an already-parsed layout as a new tab in the (single) window."""
        view = self._ensure_main().add_layout(layout)
        self._main.show()
        self._main.raise_()
        self._main.activateWindow()
        if self._welcome is not None:           # a file opened -> retire the welcome screen
            self._welcome.close()
            self._welcome = None
        return view

    def open_path(self, path: str):
        """Queue a file to open. Parsing runs on a background thread behind a
        small "Opening…" window, so a large file or a slow (e.g. network-drive)
        read gives immediate feedback instead of freezing the UI. Queued files
        open one at a time -- also gentler on a busy disk / network mount."""
        self._open_queue.append(path)
        self._pump_open_queue()

    def _pump_open_queue(self):
        """Start the next queued parse, or tear the loading UI down when idle."""
        if self._opening:
            return
        if not self._open_queue:
            self._hide_loading()
            self._maybe_show_welcome()          # nothing opened -> fall back to welcome
            return
        path = self._open_queue.pop(0)
        self._opening = True
        self._show_loading(os.path.basename(path))
        self._open_thread = _ParseThread(path, self)
        self._open_thread.done.connect(
            lambda layout, p=path: self._on_open_parsed(p, layout))
        self._open_thread.finished.connect(self._open_thread.deleteLater)
        self._open_thread.start()

    def _on_open_parsed(self, path, layout):
        self._opening = False
        if layout is None:
            QMessageBox.critical(None, "Open failed", f"Could not open:\n{path}")
        else:
            self.add_layout(layout)             # shows the main window, retires welcome
        self._pump_open_queue()                 # next file, or hide loading + maybe welcome

    def _show_loading(self, name: str):
        if self._loading is None:
            self._loading = LoadingWindow()
        self._loading.set_name(name)
        self._loading.center()
        self._loading.show()
        self._loading.raise_()
        self._loading.activateWindow()

    def _hide_loading(self):
        if self._loading is not None:
            self._loading.hide()

    def _maybe_show_welcome(self):
        """Show the welcome screen only when truly idle: no window open, nothing
        parsing, and nothing queued (so a slow open never flashes it)."""
        if (self.windows_open == 0 and not self._opening and not self._open_queue
                and self._welcome is None):
            self.show_welcome()

    def show_welcome(self):
        if self._welcome is None:
            self._welcome = WelcomeWindow(self)
        self._welcome.show()
        self._welcome.raise_()
        self._welcome.activateWindow()
        self._maybe_check_updates()

    def prompt_open(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            None, "Open DXF layout", "", "DXF files (*.dxf);;All files (*)")
        if path:
            self.open_path(path)
        return bool(path)

    @property
    def windows_open(self) -> int:
        return 1 if self._main is not None else 0


def run(layout) -> int:
    """Open a window for an already-parsed :class:`DxfLayout` (programmatic use)."""
    _configure_format()
    app = QApplication.instance()
    if not isinstance(app, ViewerApp):
        app = ViewerApp(sys.argv[:1])
    app.add_layout(layout)
    return app.exec()


def main(argv=None) -> int:
    """Full application entry: open files from argv, else prompt; then run."""
    _configure_format()
    app = ViewerApp(list(argv) if argv is not None else sys.argv)
    paths = [a for a in app.arguments()[1:] if a.lower().endswith(".dxf")]

    # Single-instance: hand our file(s) to an already-running process (as new
    # tabs there) instead of opening a second window. Selecting several files
    # in Explorer and pressing Enter launches one OS process per file, so
    # this also covers "open multiple files at once", not just "the app
    # happened to already be open". claim_primary (a shared-memory lock)
    # decides who's primary -- not whether starting the IPC server below
    # succeeds; see _IPC_LOCK_KEY's comment.
    if app.claim_primary():
        app.start_ipc_server()
    else:
        relayed = app.try_relay_to_running_instance(paths)
        if not relayed:
            # The primary won the shared-memory claim a moment ago but may
            # not have started accepting connections yet (real app startup --
            # GL context, window construction -- can genuinely take a couple
            # of seconds under load), or this particular connection attempt
            # can simply fail transiently under real contention (confirmed by
            # hand: Qt's Windows named-pipe backend needs a moment to re-arm
            # a fresh pipe instance after each accepted connection, and a
            # connect landing in that gap can report "connected" while the
            # very next write fails). Both resolve on a retried, brand-new
            # connection -- but measured by hand across repeated real 3-
            # process races, how many retries that takes varies widely (19 to
            # 77 attempts / ~3-12s observed, not a tight distribution), so a
            # merely "generous" budget (15s) still occasionally wasn't enough
            # and one or more processes fell back to opening their own
            # window. This retry is silent and invisible to the user (no
            # window shown while it runs), so correctness -- never spawning a
            # duplicate window -- is worth a long budget; 45s comfortably
            # covers the worst observed case with real margin.
            import time
            deadline = time.monotonic() + 45.0
            while time.monotonic() < deadline:
                time.sleep(0.15)
                relayed = app.try_relay_to_running_instance(paths)
                if relayed:
                    break
        if relayed:
            return 0
        # Couldn't relay despite losing the primary claim (e.g. the primary
        # crashed between claiming and listening) -- fall through and open a
        # standalone window rather than exit having done nothing.

    for p in paths:
        app.open_path(p)

    # If nothing opened (and no macOS FileOpen event arrives shortly), show the
    # welcome screen. Closing it (without opening a file) quits the app.
    # _maybe_show_welcome holds off while a file is still parsing, so a slow
    # open shows the "Opening…" window rather than briefly flashing welcome.
    QTimer.singleShot(250, app._maybe_show_welcome)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
