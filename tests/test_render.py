#!/usr/bin/env python3
"""Headless GPU render smoke test (WINDOWS_TEST_PLAN.md T4).

Renders the committed synthetic fixture through the real moderngl
standalone-context path (no Qt, no window -- this is the same code path
view_dxf.py and viewer/offscreen.py use) and asserts the output is a
correctly-sized image with real (non-background) content, then writes a PNG
to prove the PIL encode path also works:

    python tests/test_render.py

Requires a working OpenGL 3.3+ context (a real GPU/driver -- see
WINDOWS_TEST_PLAN.md's note that software-GL-only VMs are not a valid
render target). Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.dxf")
SIZE = (640, 480)


def _check(name, got, want) -> bool:
    ok = got == want
    tag = "ok  " if ok else "FAIL"
    extra = "" if ok else f"   (expected {want!r})"
    print(f"  [{tag}] {name}: {got!r}{extra}")
    return ok


def _check_true(name, ok: bool, detail: str = "") -> bool:
    tag = "ok  " if ok else "FAIL"
    suffix = f"   ({detail})" if detail else ""
    print(f"  [{tag}] {name}{suffix}")
    return ok


def test_render() -> bool:
    import numpy as np

    from pydxf import DxfLayout
    from viewer.offscreen import BG_DARK, render_array, render_png

    layout = DxfLayout(SAMPLE)
    try:
        t0 = time.perf_counter()
        img = render_array(layout, size=SIZE)
        dt = time.perf_counter() - t0
        print(f"  rendered {SIZE[0]}x{SIZE[1]} in {dt * 1e3:.0f} ms")

        ok = True
        ok &= _check("dtype", str(img.dtype), "uint8")
        ok &= _check("shape", img.shape, (SIZE[1], SIZE[0], 3))

        bg = np.array([round(c * 255) for c in BG_DARK], dtype=np.int16)
        corner = img[2, 2].astype(np.int16)
        ok &= _check_true(
            "corner pixel matches dark background",
            bool(np.all(np.abs(corner - bg) <= 2)),
            f"got {tuple(int(v) for v in corner)}, want ~{tuple(int(v) for v in bg)}",
        )

        # The square + triangle + two circles must cover a non-trivial,
        # bounded fraction of the canvas -- catches both "drew nothing"
        # (blank canvas) and "drew garbage" (e.g. context not cleared).
        diff = np.abs(img.astype(np.int16) - bg).sum(axis=2)
        frac_fg = float((diff > 20).mean())
        ok &= _check_true(
            "foreground pixel fraction in expected range",
            0.05 < frac_fg < 0.6,
            f"{frac_fg:.1%} of pixels differ from background",
        )

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "sample.png")
            render_png(layout, out, size=SIZE)
            ok &= _check_true("PNG written", os.path.isfile(out) and os.path.getsize(out) > 0)

            from PIL import Image

            with Image.open(out) as im:
                ok &= _check("PNG dimensions", im.size, SIZE)

        return ok
    finally:
        layout.close()


def main() -> int:
    ok = test_render()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
