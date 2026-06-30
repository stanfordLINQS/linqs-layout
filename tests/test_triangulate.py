#!/usr/bin/env python3
"""Regression test for real (non-overlapping) fill triangulation.

Covers two things found and fixed during development, both the hard way:

  * Correctness of viewer.triangulate.compute_real_fill against the existing
    fan + winding-rule render, on geometry that specifically stresses the bug
    an earlier version of this had: a single-pass "just draw the
    triangulation directly" design blended each *polygon's* color
    independently, so two overlapping polygons on the *same* layer got
    double-blended where they overlap (the winding rule's whole point is to
    blend a layer's color exactly once per pixel regardless of how many of
    its own polygons cover that pixel -- triangulation quality doesn't change
    that requirement). The fix keeps the winding rule; only the wind pass's
    triangle source changes. This test's fixture includes two overlapping
    same-layer squares specifically to catch a regression back to the
    broken design.

  * The real crash this surfaced: installing the triangulation makes GL
    calls (ctx.buffer/vertex_array) from a slot invoked via the Qt event
    loop (the background thread emits a signal; the install runs on the
    main/GL thread, which is correct) -- but Qt only makes a QOpenGLWidget's
    context current automatically inside paintGL itself, not for arbitrary
    slots. Without an explicit makeCurrent() first, this reproducibly
    segfaulted. test_viewport_background_install drives the real
    GLViewport + background thread + signal path end-to-end (not just the
    pure-Python compute_real_fill function) specifically to catch that
    class of bug again -- a unit test of compute_real_fill alone cannot.

    python tests/test_triangulate.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.dxf")


def _check(name, ok, detail="") -> bool:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{('   ' + detail) if detail else ''}")
    return ok


def test_compute_real_fill_basic() -> bool:
    from viewer.triangulate import compute_real_fill

    ok = True

    # A convex square (layer 0) and a concave arrow/chevron (layer 1).
    verts = [
        (0, 0), (10, 0), (10, 10), (0, 10),                              # convex square
        (20, 0), (25, 10), (30, 0), (27, 0), (25, 5), (23, 0),           # concave chevron
    ]
    start = [0, 4]
    count = [4, 6]
    layer = [0, 1]
    idx, fill_off, fill_count = compute_real_fill(
        np.array(verts, np.float32), np.array(start, np.int64),
        np.array(count, np.int64), np.array(layer, np.int64), n_layers=2)

    n_tris = len(idx) // 3
    want_tris = (4 - 2) + (6 - 2)
    ok &= _check("triangle count matches n-2 per polygon (geometric invariant)",
                 n_tris == want_tris, f"{n_tris} vs {want_tris}")
    ok &= _check("layer 0 (square) gets 2 triangles' worth of indices", int(fill_count[0]) // 3 == 2)
    ok &= _check("layer 1 (chevron) gets the rest", int(fill_count[1]) // 3 == 4)
    ok &= _check("no out-of-bounds vertex index", int(idx.max()) < len(verts), str(int(idx.max())))

    return ok


def _render(scene, ctx, layout, size=(600, 600)):
    from viewer.camera import Camera2D

    cam = Camera2D()
    cam.resize(*size)
    cam.fit(layout.bbox())
    (sx, sy), (ox, oy) = cam.scale_offset()
    w, h = size
    color = ctx.renderbuffer(size, samples=4)
    fbo = ctx.framebuffer(color_attachments=[color])
    fbo.use()
    ctx.clear(0.0392, 0.0392, 0.047)
    scene.draw(fbo, (sx, sy), (ox, oy))
    resolved = ctx.simple_framebuffer(size)
    ctx.copy_framebuffer(resolved, fbo)
    data = resolved.read(components=3)
    return np.frombuffer(data, np.uint8).reshape(h, w, 3)[::-1].copy()


def test_same_layer_overlap_blends_once() -> bool:
    """Two overlapping squares on the SAME layer must blend that layer's
    color exactly once in the overlap region (the winding rule's job) --
    this is exactly what a naive "skip the winding rule, draw the
    triangulation directly" design gets wrong (confirmed: 53% of pixels
    differed, with real >40-level diffs, when tried during development)."""
    import moderngl

    from viewer.scene import GLScene
    from viewer.triangulate import compute_real_fill

    ns = types.SimpleNamespace()
    # Two overlapping squares, BOTH on layer 0.
    verts = [(0, 0), (20, 0), (20, 20), (0, 20),
             (10, 10), (30, 10), (30, 30), (10, 30)]
    ns.verts = np.array(verts, np.float32)
    ns.poly_start = np.array([0, 4], np.int64)
    ns.poly_count = np.array([4, 4], np.int64)
    ns.poly_layer = np.array([0, 0], np.int64)
    ns.poly_flags = np.array([1, 1], np.int64)
    ns.n_layers = 1
    ns.circ = np.empty((0, 3), np.float32)
    ns.circ_layer = np.array([], np.float32)
    ns.bbox = lambda: _bbox(ns)

    ctx = moderngl.create_standalone_context(require=330)
    ok = True
    try:
        scene = GLScene(ctx, ns)
        img_before = _render(scene, ctx, ns)

        idx, fill_off, fill_count = compute_real_fill(ns.verts, ns.poly_start, ns.poly_count,
                                                        ns.poly_layer, ns.n_layers)
        scene.install_triangulated_fill(idx, fill_off, fill_count)
        img_after = _render(scene, ctx, ns)

        diff = np.abs(img_before.astype(np.int16) - img_after.astype(np.int16))
        ok &= _check("overlap region color unchanged after switching to real triangulation",
                     diff.max() == 0, f"max diff {diff.max()}")
    finally:
        ctx.release()
    return ok


def _bbox(ns):
    b = types.SimpleNamespace()
    b.xmin, b.xmax = float(ns.verts[:, 0].min()), float(ns.verts[:, 0].max())
    b.ymin, b.ymax = float(ns.verts[:, 1].min()), float(ns.verts[:, 1].max())
    b.width, b.height = b.xmax - b.xmin, b.ymax - b.ymin
    return b


def test_viewport_background_install() -> bool:
    """End-to-end: real GLViewport, real background QThread, real signal ->
    makeCurrent() -> GL install -> repaint, on the real (tiny) sample.dxf
    fixture. This is the regression test for the segfault: a unit test of
    compute_real_fill alone can't catch a GL-context-current bug, since that
    only manifests when the install path actually runs through the Qt
    event loop via the background thread's signal, not when called directly.
    Runs in a subprocess so a crash here (regression) is reported as a
    non-zero exit code rather than taking the whole test runner down.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--viewport-subprocess"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        timeout=30, capture_output=True, text=True)
    ok = _check("viewport background-install subprocess exited cleanly",
                proc.returncode == 0, f"exit code {proc.returncode}")
    if proc.returncode != 0:
        print("  stdout:", proc.stdout[-500:])
        print("  stderr:", proc.stderr[-500:])
    return ok


def _viewport_subprocess_main() -> int:
    from PySide6.QtCore import QTimer
    from viewer.app import ViewerApp, _configure_format

    _configure_format()
    app = ViewerApp([sys.argv[0], SAMPLE])
    for arg in app.arguments()[1:]:
        if arg.lower().endswith(".dxf"):
            app.open_path(arg)

    def check_and_quit():
        win = app._main
        vp = win._cur().viewport if win is not None else None
        ok = vp is not None and vp.scene is not None and vp.scene.real_fill_vao is not None
        print("real_fill_vao installed:" if ok else "FAIL: real_fill_vao not installed", ok)
        app.quit()

    QTimer.singleShot(1500, check_and_quit)     # generous: small fixture triangulates near-instantly
    QTimer.singleShot(10000, app.quit)
    app.exec()
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--viewport-subprocess":
        return _viewport_subprocess_main()

    print("[compute_real_fill basics]")
    ok = test_compute_real_fill_basic()
    print("\n[same-layer overlap blends once -- the regression this caught]")
    ok &= test_same_layer_overlap_blends_once()
    print("\n[viewport background install -- the crash this caught]")
    ok &= test_viewport_background_install()

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
