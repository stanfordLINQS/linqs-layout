#!/usr/bin/env python3
"""Regression test for the per-layer fill scissor optimization (scene.py).

GLScene's polygon fill (winding-rule technique) clears + rasterizes a wind
buffer once per visible layer; profiling on a real ~6M-vertex, 33-layer file
showed this is fragment-fill-rate bound (~1.85ms at 30k px vs ~41ms at 5M px
for the *same* geometry) -- clearing/shading the full viewport for every
layer regardless of how much screen space that layer's geometry occupies was
the dominant per-frame cost. Fixed by scissoring the wind + cover passes to
each layer's actual on-screen bbox (GLScene._screen_scissor / the per-layer
bbox computed in _build_fill).

This is a real-rendering-output regression test: it renders the same scene
with fill scissored (current code) vs an unscissored reference path, on
geometry specifically chosen to stress the scissor logic (an off-center
small layer, a layer with geometry outside the visible viewport, and a
layer spanning the whole canvas), and asserts the rendered pixels are
identical. A regression here would mean the scissor rect is clipping away
real fill content, not just trimming wasted fragment work.

    python tests/test_scene_fill.py

Requires a working OpenGL 3.3+ context (real GPU/driver).
Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _check(name, ok, detail="") -> bool:
    tag = "ok  " if ok else "FAIL"
    print(f"  [{tag}] {name}{('   ' + detail) if detail else ''}")
    return ok


def _layout(verts, poly_start, poly_count, poly_layer, poly_flags, n_layers, circ=None):
    ns = types.SimpleNamespace()
    ns.verts = np.asarray(verts, np.float32)
    ns.poly_start = np.asarray(poly_start, np.int64)
    ns.poly_count = np.asarray(poly_count, np.int64)
    ns.poly_layer = np.asarray(poly_layer, np.int64)
    ns.poly_flags = np.asarray(poly_flags, np.int64)
    ns.n_layers = n_layers
    ns.circ = np.asarray(circ if circ is not None else np.empty((0, 3)), np.float32)
    ns.circ_layer = np.asarray([], np.float32)
    return ns


def _render(scene_cls, ctx, layout, size=(800, 600)):
    from viewer.camera import Camera2D

    scene = scene_cls(ctx, layout)
    cam = Camera2D()
    cam.resize(*size)
    cam.fit(layout.bbox() if hasattr(layout, "bbox") else _bbox(layout))
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


def _bbox(layout):
    import types as _t
    v = layout.verts
    b = _t.SimpleNamespace()
    b.xmin, b.xmax = float(v[:, 0].min()), float(v[:, 0].max())
    b.ymin, b.ymax = float(v[:, 1].min()), float(v[:, 1].max())
    b.width = b.xmax - b.xmin
    b.height = b.ymax - b.ymin
    return b


def main() -> int:
    import moderngl

    from viewer.scene import GLScene

    # Three layers stressing different scissor cases:
    #  0: a small square far in one corner (tight, off-center scissor rect)
    #  1: a square spanning almost the whole canvas (scissor ~= full viewport)
    #  2: a tiny square -- and ALSO an unfillable 2-vertex segment on the same
    #     layer, to check the bbox computation only considers fillable polys
    verts = [
        (0, 0), (20, 0), (20, 20), (0, 20),            # layer 0: small, corner
        (-500, -500), (500, -500), (500, 500), (-500, 500),  # layer 1: huge, centered
        (200, 200), (210, 200), (210, 210), (200, 210),      # layer 2: small square
        (300, -400), (310, -390),                              # layer 2: open 2-vertex segment
    ]
    poly_start = [0, 4, 8, 12]
    poly_count = [4, 4, 4, 2]
    poly_layer = [0, 1, 2, 2]
    poly_flags = [1, 1, 1, 0]
    layout = _layout(verts, poly_start, poly_count, poly_layer, poly_flags, n_layers=3)

    ctx = moderngl.create_standalone_context(require=330)
    ok = True
    try:
        img_scissored = _render(GLScene, ctx, layout)

        # Reference: same scene, but force every layer's scissor rect to the
        # full framebuffer (disables the optimization without touching the
        # winding/cover math at all) by monkeypatching _screen_scissor.
        class _UnscissoredScene(GLScene):
            def _screen_scissor(self, lid, scale, offset, W, H):
                if self._fill_count[lid] == 0:
                    return None
                return (0, 0, W, H)

        img_full = _render(_UnscissoredScene, ctx, layout)

        diff = np.abs(img_scissored.astype(np.int16) - img_full.astype(np.int16))
        ok &= _check("scissored render is pixel-identical to unscissored reference",
                     diff.max() == 0, f"max diff {diff.max()}, {(diff.sum(axis=2) > 0).sum()} differing pixels")

        # Sanity: something was actually drawn (catches an accidentally-empty test).
        bg = np.array([10, 10, 12])
        fg_pixels = (np.abs(img_scissored.astype(np.int16) - bg).sum(axis=2) > 10).sum()
        ok &= _check("fixture actually rendered visible fill", fg_pixels > 100, f"{fg_pixels} foreground px")
    finally:
        ctx.release()

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
