#!/usr/bin/env python3
"""Interactive GUI smoke test (WINDOWS_TEST_PLAN.md T5).

Drives the real MainWindow with synthesized QTest key/mouse events on a real
window + real GL surface -- the same checklist as the plan's manual GUI
section, automated: zoom, pan, reset, layer-row toggle, panel show/hide,
fill/grid/background toggles, measure mode (snap, two points, esc), the
status bar, the Keybindings dialog, and tabs (add/switch/reorder/close,
including closing the last tab). Screenshots are written to a temp dir for
visual review.

    python tests/test_interactive.py

"Feels smooth, no stutter" is inherently a human judgment call and is not
assessed here -- everything else on the T5 checklist is.

Methodology note: QWidget.grab() on the window can return a stale backing
store frame for the QOpenGLWidget specifically (the rest of the window grabs
fine). Each shot forces a synchronous repaint first to minimize this; the
light/dark check additionally confirms the real GL framebuffer color via
QOpenGLWidget.grabFramebuffer(), which reads the framebuffer directly and is
the reliable way to verify GL canvas pixels.

Ctrl+O's native file-open dialog can't be driven headlessly, so the "second
file adds a tab" check goes through ViewerApp.open_path directly (the same
call the dialog's result would make); drag-and-drop similarly stays a manual
check (see WINDOWS_TEST_PLAN.md).

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.dxf")
WATCHDOG_MS = 20000
_VIS_ROLE = 257  # panel.py: int(Qt.ItemDataRole.UserRole) + 1


def _check(name, ok, detail="") -> bool:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{('   ' + detail) if detail else ''}")
    return ok


def main() -> int:
    from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QCursor, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog

    from viewer.app import ViewerApp, _configure_format

    def pump_until(cond, timeout=15.0):
        """Spin the event loop until ``cond()`` or timeout. open_path parses on a
        background thread now, so a tab appears a few event-loop turns later."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and not cond():
            QApplication.processEvents()
            time.sleep(0.01)
        return cond()

    _configure_format()
    app = ViewerApp([sys.argv[0], SAMPLE])
    for arg in app.arguments()[1:]:
        if arg.lower().endswith(".dxf"):
            app.open_path(arg)
    pump_until(lambda: app._main is not None and app._main.tabs.count() >= 1)

    out_dir = tempfile.mkdtemp(prefix="linqs-t5-")
    state = {"ok": True, "n": 0}

    def shot(name, widget=None):
        w = widget or app._main
        for _ in range(3):
            QApplication.processEvents()
            time.sleep(0.05)
        w.repaint()
        QApplication.processEvents()
        path = os.path.join(out_dir, f"{state['n']:02d}_{name}.png")
        state["n"] += 1
        w.grab().save(path)

    def ok(name, cond, detail=""):
        state["ok"] = bool(_check(name, cond, detail)) and state["ok"]

    def close_topmost_dialog():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                shot("keybindings_dialog", w)
                w.close()

    def run():
        try:
            win = app._main
            view = win._cur()
            vp, panel = view.viewport, view.panel

            print("[init]")
            ok("window title set", bool(win.windowTitle()))
            ok("title contains filename", "sample.dxf" in win.windowTitle())
            shot("initial")

            print("[scroll wheel zooms toward cursor]")
            before = vp.cam.scale_offset()
            pos = QPointF(vp.width() * 0.3, vp.height() * 0.3)
            we = QWheelEvent(pos, vp.mapToGlobal(pos.toPoint()).toPointF(),
                              QPoint(0, 0), QPoint(0, 600), Qt.MouseButton.NoButton,
                              Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(vp, we)
            QApplication.processEvents()
            after = vp.cam.scale_offset()
            ok("scroll wheel zooms", after[0] != before[0])
            shot("zoomed")

            print("[left-drag pans]")
            before_off = vp.cam.scale_offset()[1]
            QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
            QTest.mouseMove(vp, QPoint(260, 240))
            QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=QPoint(260, 240))
            QApplication.processEvents()
            after_off = vp.cam.scale_offset()[1]
            ok("left-drag pans", after_off != before_off)
            shot("panned")

            print("[R resets view]")
            QTest.keyClick(win, Qt.Key.Key_R)
            QApplication.processEvents()
            reset_off = vp.cam.scale_offset()[1]
            vp.cam.fit(vp._layout.bbox())
            ok("R resets to fit view", reset_off == vp.cam.scale_offset()[1])
            ok("R clears user-view flag", vp._user_view is False)
            shot("reset")

            print("[layer panel: click a row toggles visibility]")
            item0 = panel.list.item(0)
            vis_before = bool(item0.data(_VIS_ROLE))
            rect = panel.list.visualItemRect(item0)
            QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
            QApplication.processEvents()
            ok("clicking a layer row toggles visibility", bool(item0.data(_VIS_ROLE)) != vis_before)
            shot("layer_toggled")
            QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
            QApplication.processEvents()

            print("[L toggles layer panel]")
            vis_before = panel.isVisible()
            QTest.keyClick(win, Qt.Key.Key_L)
            QApplication.processEvents()
            vis_after = panel.isVisible()
            ok("L toggles layer panel", vis_after != vis_before)
            shot("panel_hidden" if not vis_after else "panel_shown")
            QTest.keyClick(win, Qt.Key.Key_L)
            QApplication.processEvents()

            print("[F toggles fill]")
            fill_before = panel.fill_btn.isChecked()
            ok("fill on by default", fill_before is True)
            QTest.keyClick(win, Qt.Key.Key_F)
            QApplication.processEvents()
            fill_after = panel.fill_btn.isChecked()
            ok("F toggles fill", fill_after != fill_before)
            ok("scene.show_fill matches checkbox", vp.scene.show_fill == fill_after)
            shot("fill_off")
            QTest.keyClick(win, Qt.Key.Key_F)
            QApplication.processEvents()

            print("[G toggles grid]")
            grid_before = panel.grid_btn.isChecked()
            QTest.keyClick(win, Qt.Key.Key_G)
            QApplication.processEvents()
            ok("G toggles grid", panel.grid_btn.isChecked() != grid_before)
            shot("grid_off")
            QTest.keyClick(win, Qt.Key.Key_G)
            QApplication.processEvents()

            print("[B toggles light/dark background]")
            light_before = vp.is_light()
            QTest.keyClick(win, Qt.Key.Key_B)
            QApplication.processEvents()
            light_after = vp.is_light()
            ok("B toggles light/dark background", light_after != light_before)
            # grab() can be stale for the GL canvas -- grabFramebuffer() reads it directly.
            fb = vp.grabFramebuffer()
            bg_px = fb.pixelColor(5, 5).getRgb()[:3]
            ok("light background actually renders light (real GL framebuffer)",
               sum(bg_px) > 600, f"corner pixel {bg_px}")
            shot("light_bg")
            QTest.keyClick(win, Qt.Key.Key_B)
            QApplication.processEvents()

            print("[M: measure mode, two points, esc clears]")
            QTest.keyClick(win, Qt.Key.Key_M)
            QApplication.processEvents()
            ok("M enters measure mode", vp.measure_mode is True)
            p1 = QPoint(int(vp.width() * 0.15), int(vp.height() * 0.45))
            p2 = QPoint(int(vp.width() * 0.15), int(vp.height() * 0.65))
            QTest.mouseMove(vp, p1)
            QApplication.processEvents()
            QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=p1)
            QApplication.processEvents()
            QTest.mouseMove(vp, p2)
            QApplication.processEvents()
            QTest.mouseClick(vp, Qt.MouseButton.LeftButton, pos=p2)
            QApplication.processEvents()
            ok("two measure points placed", len(vp.measure_points) == 2)
            shot("measured")
            QTest.keyClick(win, Qt.Key.Key_Escape)
            QApplication.processEvents()
            ok("esc clears measurement", len(vp.measure_points) == 0)
            QTest.keyClick(win, Qt.Key.Key_M)
            QApplication.processEvents()

            print("[status bar shows cursor x/y]")
            QTest.mouseMove(vp, QPoint(int(vp.width() * 0.5), int(vp.height() * 0.5)))
            QApplication.processEvents()
            status_text = win._status.text()
            ok("status bar shows cursor x/y", "x</span>" in status_text and "y</span>" in status_text,
               status_text[:80])

            print("[Keybindings dialog]")
            QTimer.singleShot(300, close_topmost_dialog)
            win._show_keybindings()  # blocks (QDialog.exec()) until the timer above closes it
            ok("keybindings dialog opens and closes without hanging", True)

            print("[opening a second file adds a tab]")
            tabs_before = win.tabs.count()
            app.open_path(SAMPLE)
            pump_until(lambda: win.tabs.count() == tabs_before + 1)
            ok("second tab added", win.tabs.count() == tabs_before + 1)
            shot("two_tabs")

            print("[status bar populates on tab-open even with a stationary cursor]")
            # Regression: a mouseMoveEvent only fires once the OS cursor
            # actually crosses into the widget. If a new tab appears under an
            # already-stationary cursor (the common real case: click Open, the
            # file loads, the cursor hasn't moved since), no such event ever
            # comes and the status bar used to sit blank until the user
            # happened to nudge the mouse. QTest.mouseMove only synthesizes a
            # Qt-level event, not a real OS cursor move, so QCursor.setPos is
            # used here to test the real code path (emit_status_at_cursor
            # reads the actual OS cursor position via QCursor.pos()).
            cur_view = win._cur()
            cur_vp = cur_view.viewport
            center_global = cur_vp.mapToGlobal(QPoint(cur_vp.width() // 2, cur_vp.height() // 2))
            QCursor.setPos(center_global)
            QApplication.processEvents()
            win._status.clear()
            win.tabs.setCurrentIndex(0 if win.tabs.currentIndex() != 0 else 1)  # trigger _tab_changed
            for _ in range(4):
                QApplication.processEvents()
                time.sleep(0.02)
            status_text = win._status.text()
            ok("status bar shows x/y without any mouse-move event, cursor already stationary",
               "x</span>" in status_text and "y</span>" in status_text, status_text[:80])

            print("[tabs: switch / reorder / close]")
            win.tabs.setCurrentIndex(0)
            QApplication.processEvents()
            ok("tab switch", win.tabs.currentIndex() == 0)
            win.tabs.tabBar().moveTab(0, 1)
            QApplication.processEvents()
            shot("reordered")
            count_before = win.tabs.count()
            win._close_tab(win.tabs.currentIndex())
            QApplication.processEvents()
            ok("tab close", win.tabs.count() == count_before - 1)
            shot("one_tab_left")

            print("[closing the last tab is sane]")
            win._close_tab(0)
            QApplication.processEvents()
            ok("closing the last tab closes the window", win.isVisible() is False)
        except Exception as ex:  # noqa: BLE001 - report, don't hang the loop
            print(f"  [FAIL] exception during run: {ex!r}")
            state["ok"] = False
        finally:
            app.quit()

    QTimer.singleShot(400, run)
    QTimer.singleShot(WATCHDOG_MS, app.quit)
    app.exec()

    print(f"\nScreenshots: {out_dir}")
    print("\nRESULT:", "PASS" if state["ok"] else "FAIL")
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
