#!/usr/bin/env python3
"""Import + per-OS style smoke test (WINDOWS_TEST_PLAN.md T3).

Runs WITHOUT a display or GPU -- importing PySide6 widgets does not require a
window. Confirms the viewer package tree imports cleanly and that the
per-platform style tokens (monospace family, modifier-key label) resolve to
the values the current OS expects. Plain asserts, no pytest required:

    python tests/test_imports.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _check(name, got, want) -> bool:
    ok = got == want
    tag = "ok  " if ok else "FAIL"
    extra = "" if ok else f"   (expected {want!r})"
    print(f"  [{tag}] {name}: {got!r}{extra}")
    return ok


def test_imports() -> bool:
    import pydxf  # noqa: F401
    import viewer.app  # noqa: F401
    import viewer.style as style
    import viewer.update  # noqa: F401
    import viewer.window  # noqa: F401

    print("imports ok: pydxf, viewer.app, viewer.window, viewer.style, viewer.update")

    if sys.platform.startswith("win"):
        want_family, want_label = "Consolas", "Ctrl+O"
    elif sys.platform == "darwin":
        want_family, want_label = "Menlo", "⌘O"
    else:
        want_family, want_label = "DejaVu Sans Mono", "Ctrl+O"

    ok = True
    ok &= _check("MONO_FAMILY", style.MONO_FAMILY, want_family)
    ok &= _check("key_label('O')", style.key_label("O"), want_label)
    return ok


def main() -> int:
    ok = test_imports()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
