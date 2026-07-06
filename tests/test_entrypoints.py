#!/usr/bin/env python3
"""File-open entry-point smoke test (WINDOWS_TEST_PLAN.md T6, scriptable part).

Drives the real ViewerApp/MainWindow/WelcomeWindow classes through a real Qt
event loop on a real GL surface (not the "offscreen" QPA platform -- per
WINDOWS_TEST_PLAN.md, software-GL-only targets aren't representative) to
check the two non-interactive open paths:

  * argv: ``app_main.py tests/sample.dxf`` opens that file directly.
  * no-arg: ``app_main.py`` shows the welcome screen.

Drag-and-drop needs a real OS drag gesture and stays a manual check (see the
plan's GUI checklist). Each scenario runs in its own subprocess (QApplication
is a process-wide singleton) with a watchdog timer so a GL/window-setup
failure reports FAIL instead of hanging:

    python tests/test_entrypoints.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.dxf")
WATCHDOG_MS = 8000


def _check(name, got, want) -> bool:
    ok = got == want
    tag = "ok  " if ok else "FAIL"
    extra = "" if ok else f"   (expected {want!r})"
    print(f"  [{tag}] {name}: {got!r}{extra}")
    return ok


def _run_argv_scenario() -> int:
    from PySide6.QtCore import QTimer

    from viewer.app import ViewerApp, _configure_format

    _configure_format()
    app = ViewerApp([sys.argv[0], SAMPLE])
    state = {"ok": False}

    def check_and_quit():
        try:
            ok = True
            ok &= _check("windows_open", app.windows_open, 1)
            win = app._main
            ok &= _check("main window created", win is not None, True)
            if win is not None:
                ok &= _check("tab count", win.tabs.count(), 1)
                ok &= _check("window title", win.windowTitle(), "LINQS Layout — sample.dxf")
            state["ok"] = ok
        except Exception as ex:  # noqa: BLE001 - report, don't crash the loop
            print(f"  [FAIL] exception during check: {ex!r}")
        finally:
            app.quit()

    for arg in app.arguments()[1:]:
        if arg.lower().endswith(".dxf"):
            app.open_path(arg)

    QTimer.singleShot(300, check_and_quit)
    QTimer.singleShot(WATCHDOG_MS, app.quit)
    app.exec()
    return 0 if state["ok"] else 1


def _run_noarg_scenario() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QLabel

    from viewer.app import ViewerApp, _configure_format

    _configure_format()
    app = ViewerApp([sys.argv[0]])
    state = {"ok": False}

    def show_if_idle():
        if app.windows_open == 0:
            app.show_welcome()

    from viewer import style

    open_label = style.key_label("O")          # '⌘O' on macOS, 'Ctrl+O' elsewhere

    def check_and_quit():
        try:
            ok = True
            ok &= _check("windows_open", app.windows_open, 0)
            w = app._welcome
            ok &= _check("welcome window shown", w is not None and w.isVisible(), True)
            if w is not None:
                text = " ".join(lbl.text() for lbl in w.findChildren(QLabel))
                ok &= _check(f"hint mentions {open_label}", open_label in text, True)
            state["ok"] = ok
        except Exception as ex:  # noqa: BLE001
            print(f"  [FAIL] exception during check: {ex!r}")
        finally:
            app.quit()

    QTimer.singleShot(250, show_if_idle)
    QTimer.singleShot(600, check_and_quit)
    QTimer.singleShot(WATCHDOG_MS, app.quit)
    app.exec()
    return 0 if state["ok"] else 1


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--scenario":
        return _run_argv_scenario() if sys.argv[2] == "argv" else _run_noarg_scenario()

    ok = True
    for label, scenario in (("argv-open", "argv"), ("no-arg welcome", "noarg")):
        print(f"[{label}]")
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--scenario", scenario],
                cwd=REPO_ROOT, timeout=30,
            )
            ok &= proc.returncode == 0
        except subprocess.TimeoutExpired:
            print("  [FAIL] subprocess timed out (window/GL setup likely hung)")
            ok = False

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
