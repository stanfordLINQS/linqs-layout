#!/usr/bin/env python3
"""Regression tests for single-instance IPC (viewer/app.py).

Opening several files should always land as tabs in one window, never spawn
separate windows -- including when each file arrives via a *separate* OS
process launch (selecting multiple files in Explorer and pressing Enter
spawns one process per file; double-clicking a second file while the app is
already running does too). ViewerApp.try_relay_to_running_instance /
start_ipc_server implement this: a new process first tries to hand its file
path(s) to whichever process already holds the IPC socket, and only opens a
window of its own if none does.

Two layers of test:
  * test_ipc_round_trip -- exercises the actual QLocalServer/QLocalSocket
    round trip within one process (a client connecting to its own process's
    server works identically to a real separate process; Qt doesn't
    distinguish). Deterministic and fast.
  * test_second_process_relays_and_exits_fast -- a real end-to-end check:
    launch two genuinely separate OS processes and confirm the second exits
    quickly (relayed) rather than sticking around running its own window.

    python tests/test_single_instance.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
APP_MAIN = os.path.join(ROOT, "app_main.py")
SAMPLE = os.path.join(ROOT, "tests", "sample.dxf")


def _check(name, ok, detail="") -> bool:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{('   ' + detail) if detail else ''}")
    return ok


def test_ipc_in_process() -> bool:
    """Exercises the server side (accept, buffer until the end marker, parse,
    open_path, ack) with a hand-driven client, plus the "nothing listening
    yet" case. QApplication is a per-process singleton, so both share one
    ViewerApp instance rather than each constructing their own.

    Deliberately does NOT call the real try_relay_to_running_instance here for
    the round-trip part: that method uses blocking waitFor* calls, which rely
    on the *other* process's independently-running event loop to service them
    -- true for any real launch (always a separate OS process) but not
    reproducible with a client and server sharing one thread/event loop in a
    single test process. The end-to-end subprocess test below is what
    actually validates that method; this test drives its own QLocalSocket
    with explicit processEvents() pumping instead, to check the server-side
    logic quickly and deterministically without needing a real subprocess."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket

    from viewer.app import ViewerApp

    app = ViewerApp([sys.argv[0]])
    ok = True
    try:
        relayed_early = app.try_relay_to_running_instance(["x.dxf"])
        ok &= _check("relay attempt fails cleanly when no instance is listening yet",
                     relayed_early is False)

        opened = []
        app.open_path = opened.append          # avoid a real DxfLayout parse; just record calls
        app.start_ipc_server()
        ok &= _check("IPC server starts listening", app._ipc_server.isListening())

        def hand_driven_relay(paths):
            sock = QLocalSocket()
            sock.connectToServer(app._ipc_server.serverName())
            for _ in range(50):
                QCoreApplication.processEvents()
                if sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
                    break
                time.sleep(0.02)
            sock.write(("\n".join(paths)).encode("utf-8") + app._IPC_END)
            sock.flush()
            data = b""
            for _ in range(50):
                QCoreApplication.processEvents()
                data = bytes(sock.readAll())
                if data:
                    break
                time.sleep(0.02)
            sock.disconnectFromServer()
            return data == app._IPC_ACK

        acked = hand_driven_relay(["a.dxf", "b.dxf"])
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        ok &= _check("hand-driven client gets acked and both paths were opened, in order",
                     acked and opened == ["a.dxf", "b.dxf"], f"acked={acked} got {opened!r}")

        # A second, later connection (simulating a third process opening yet
        # another file afterward) must also work -- not a one-shot server.
        opened.clear()
        acked2 = hand_driven_relay(["c.dxf"])
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        ok &= _check("a subsequent connection after the first also works",
                     acked2 and opened == ["c.dxf"], f"acked={acked2} got {opened!r}")
    finally:
        if app._ipc_server is not None:
            app._ipc_server.close()
    return ok


def test_second_process_relays_and_exits_fast() -> bool:
    """Real end-to-end check across two genuine OS processes. Exit code 0
    specifically means the server acknowledged the message -- try_relay_to_
    running_instance only returns True once it has read back the server's
    ack, and the server only sends that ack after actually calling
    open_path() for every relayed path (see the _IPC_END/_IPC_ACK handshake
    in app.py). So this isn't just "the connection succeeded"; it's proof
    process A really processed the file, not just that process B thought it
    handed it off successfully."""
    ok = True
    proc_a = subprocess.Popen([sys.executable, APP_MAIN, SAMPLE])
    try:
        time.sleep(3.0)   # let process A fully start (GL init + IPC server listening)
        t0 = time.time()
        proc_b = subprocess.Popen([sys.executable, APP_MAIN, SAMPLE])
        try:
            proc_b.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc_b.kill()
            proc_b.wait()
            return _check("second process relays to the first and exits promptly", False,
                          "process B never exited -- it opened its own window instead of relaying")
        dt = time.time() - t0
        ok &= _check("second process exits with code 0 (successful relay)",
                     proc_b.returncode == 0, f"exit code {proc_b.returncode}")
        # A relay is a quick local-socket round trip; a full second app launch
        # (GL context, scene build, window show) takes much longer than this.
        ok &= _check("second process exits quickly, consistent with relaying (not opening a window)",
                     dt < 3.0, f"{dt:.2f}s")
        ok &= _check("first process is still running (it, not B, holds the window)",
                     proc_a.poll() is None)
    finally:
        proc_a.terminate()
        try:
            proc_a.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc_a.kill()
            proc_a.wait()
    return ok


def test_simultaneous_launches_elect_exactly_one_primary() -> bool:
    """Regression test for a real bug: selecting 3 files in Explorer and
    pressing Enter launches 3 OS processes within the same instant, all of
    which can see "nothing is listening yet" on their first check. An
    earlier version decided who's primary by whether QLocalServer.listen()
    itself succeeded -- which is *not* exclusive on Windows: multiple
    independent processes can each successfully create their own named-pipe
    instance under the same name (unlike a Unix domain socket path, where a
    second bind() to the same path genuinely fails). Confirmed by hand:
    launching 3 processes within the same second all had isListening()
    return True, and all 3 opened separate windows. claim_primary uses
    QSharedMemory.create() instead, which *is* exclusive cross-platform.
    This test launches 3 real processes at once and asserts exactly one
    window survives."""
    procs = [subprocess.Popen([sys.executable, APP_MAIN, SAMPLE]) for _ in range(3)]
    try:
        exited = []
        # Generous: the losers' own retry budget in app.py's main() is 45s
        # (measured retry counts to relay successfully varied widely across
        # repeated real runs -- 19 to 77 attempts -- so this needs real
        # headroom above that budget, not just above the happy-path time).
        deadline = time.time() + 60
        while time.time() < deadline and len(exited) < 2:
            for p in procs:
                if p not in exited and p.poll() is not None:
                    exited.append(p)
            time.sleep(0.1)
        still_running = [p for p in procs if p.poll() is None]
        ok = _check("exactly 2 of the 3 processes relayed and exited",
                    len(exited) == 2, f"{len(exited)} exited")
        ok &= _check("exactly 1 process remains running (the elected primary)",
                     len(still_running) == 1, f"{len(still_running)} still running")
        ok &= _check("every exited process exited cleanly (code 0)",
                     all(p.returncode == 0 for p in exited),
                     f"codes: {[p.returncode for p in exited]}")
        return ok
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


def main() -> int:
    ok = True
    print("[IPC round trip within one process]")
    ok &= test_ipc_in_process()
    print("\n[two real processes: second relays into the first]")
    ok &= test_second_process_relays_and_exits_fast()
    print("\n[3 processes launched simultaneously: exactly one primary is elected]")
    ok &= test_simultaneous_launches_elect_exactly_one_primary()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
