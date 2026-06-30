#!/usr/bin/env python3
"""Regression test: measure-mode snapping must be throttled, not recomputed
synchronously on every raw mouse-move event.

Background: Snapper.snap() is a full-geometry numpy scan -- a few ms on a
large real layout. It used to run inline inside GLViewport.mouseMoveEvent on
every single move event with no coalescing (unlike paint/update(), which Qt
naturally merges). On Windows, raw mouse-move events are typically delivered
faster/less-coalesced than on macOS, so the handler fell behind the OS event
rate and the measure-mode cursor visibly lagged and jumped. Fixed by
throttling the actual snap recompute to ~one per frame (_MEASURE_THROTTLE_MS
in viewer/viewport.py), always converging to the latest position.

This test doesn't need a large real DXF (kept local-only, not in the repo):
it monkeypatches Snapper.snap with an artificial delay to simulate realistic
slowness deterministically, against the tiny committed tests/sample.dxf.

    python tests/test_measure_throttle.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.dxf")
N_EVENTS = 300
SIMULATED_SNAP_MS = 5.0  # representative of a real ~400k-vertex layout


def _check(name, ok, detail="") -> bool:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{('   ' + detail) if detail else ''}")
    return ok


def main() -> int:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    import viewer.snap as snap_mod
    from viewer.app import ViewerApp, _configure_format

    calls = {"n": 0}
    real_snap = snap_mod.Snapper.snap

    def slow_snap(self, wx, wy, radius):
        calls["n"] += 1
        time.sleep(SIMULATED_SNAP_MS / 1000.0)
        return real_snap(self, wx, wy, radius)

    snap_mod.Snapper.snap = slow_snap

    ok = True
    try:
        _configure_format()
        app = ViewerApp([sys.argv[0], SAMPLE])
        for arg in app.arguments()[1:]:
            if arg.lower().endswith(".dxf"):
                app.open_path(arg)

        win = app._main
        vp = win._cur().viewport
        vp.set_measure_mode(True)

        # Simulate a burst of raw OS mouse-move events delivered back-to-back,
        # faster than the throttle window -- call the handler directly, the way
        # Qt would dispatch a backlog of queued events with no gap between them.
        t0 = time.perf_counter()
        last_pos = None
        for i in range(N_EVENTS):
            pos = QPointF(100 + i, 300)
            last_pos = pos
            ev = QMouseEvent(QEvent.Type.MouseMove, pos, pos, Qt.MouseButton.NoButton,
                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            vp.mouseMoveEvent(ev)
        dt = time.perf_counter() - t0

        unthrottled_estimate_ms = N_EVENTS * SIMULATED_SNAP_MS
        ok &= _check(
            f"{N_EVENTS} burst events processed without blocking for the unthrottled time",
            dt * 1000 < unthrottled_estimate_ms * 0.5,
            f"{dt*1000:.0f} ms (unthrottled would be ~{unthrottled_estimate_ms:.0f} ms)")
        ok &= _check(
            "snap() called far fewer times than events received (actually throttled)",
            calls["n"] < N_EVENTS * 0.1,
            f"{calls['n']} calls for {N_EVENTS} events")

        # Let the trailing-edge timer fire and apply the final pending position.
        QApplication.processEvents()
        time.sleep((_vp_throttle_ms(vp) + 20) / 1000.0)
        QApplication.processEvents()

        ok &= _check("pending move cleared after the throttle window", vp._pending_measure_move is None)
        # Ground truth: a fresh, untimed snap query for the exact last position (may
        # legitimately snap to nearby geometry, not just the raw cursor coordinate).
        expected, _kind = vp._snap(last_pos.x(), last_pos.y())
        got = vp.measure_cursor
        close = got is not None and abs(got[0] - expected[0]) < 1e-6 and abs(got[1] - expected[1]) < 1e-6
        ok &= _check("converged to the latest cursor position, not a stale one", close,
                     f"got {got}, want {expected}")

        app.quit()
    finally:
        snap_mod.Snapper.snap = real_snap

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _vp_throttle_ms(vp) -> int:
    return vp._measure_move_timer.interval()


if __name__ == "__main__":
    raise SystemExit(main())
