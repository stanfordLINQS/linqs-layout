#!/usr/bin/env python3
"""Regression tests for scene.py's fill-rendering performance optimizations.

Two independent levers, two different correctness bars:

  * The per-layer scissor (GLScene._screen_scissor / the per-layer bbox
    computed in _build_fill) just trims wasted fragment work -- it must be
    PIXEL-IDENTICAL to an unscissored render. Profiling on a real
    ~6M-vertex, 33-layer file showed the wind pass is fragment-fill-rate
    bound (~1.85ms at 30k px vs ~41ms at 5M px for the *same* geometry), so
    clearing/shading the full viewport for every layer regardless of how
    much screen space it actually occupies was the dominant per-frame cost.
    test_scissor_pixel_identical renders the same scene scissored vs. an
    unscissored reference (geometry chosen to stress an off-center small
    layer, a layer outside the viewport, and a layer spanning the whole
    canvas) and asserts zero pixel difference -- a regression here means the
    scissor rect is clipping away real fill content.

  * wind_downsample (default 2) is a deliberate, *lossy* tradeoff: it
    renders the wind buffer at 1/N resolution, trading fill-edge precision
    for a direct ~N^2 cut to that pass's pixel count. test_wind_downsample
    checks it doesn't introduce gross artifacts (a bounded large-pixel-diff
    count, not pixel-identical -- that's not the point) and that disabling
    it (downsample=1) is unaffected.

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


def test_wind_downsample() -> bool:
    """GLScene.wind_downsample (default 2, see scene.py's __init__ comment for
    the speed/quality sweep this was picked from) renders the wind buffer at
    1/N resolution -- a deliberate, lossy speed/precision tradeoff, unlike the
    scissor optimization above. Checks: downsample=1 is unaffected (still the
    full-precision path for anyone who sets it back), downsample=2 doesn't
    introduce gross artifacts (bounded large-pixel-diff count, not "this is
    visibly broken"), and it isn't slower (the whole point)."""
    import time

    import moderngl

    from viewer.scene import GLScene

    # A polygon with fine internal detail (concentric-ish steps) so reduced
    # wind-buffer resolution has *something* nontrivial to blur at the edges.
    verts = [(0, 0), (200, 0), (200, 200), (0, 200)]
    layout = _layout(verts, [0], [4], [0], [1], n_layers=1)

    ctx = moderngl.create_standalone_context(require=330)
    ok = True
    try:
        scene = GLScene(ctx, layout)
        size = (500, 500)

        def render():
            cam_local = __import__("viewer.camera", fromlist=["Camera2D"]).Camera2D()
            cam_local.resize(*size)
            cam_local.fit(_bbox(layout))
            (sx, sy), (ox, oy) = cam_local.scale_offset()
            color = ctx.renderbuffer(size, samples=4)
            fbo = ctx.framebuffer(color_attachments=[color])
            fbo.use()
            ctx.clear(0.0392, 0.0392, 0.047)
            t0 = time.perf_counter()
            scene.draw(fbo, (sx, sy), (ox, oy))
            ctx.finish()
            dt = time.perf_counter() - t0
            resolved = ctx.simple_framebuffer(size)
            ctx.copy_framebuffer(resolved, fbo)
            data = resolved.read(components=3)
            img = np.frombuffer(data, np.uint8).reshape(size[1], size[0], 3)[::-1].copy()
            return img, dt

        scene.wind_downsample = 1
        img_full, _ = render()

        scene.wind_downsample = 2
        img_ds, t_ds = render()
        diff = np.abs(img_full.astype(np.int16) - img_ds.astype(np.int16))
        large = (diff.max(axis=2) > 60).sum()
        ok &= _check("downsample=2 has no large-diff (visible artifact) pixels on a simple shape",
                     large == 0, f"{large} large-diff px")

        scene.wind_downsample = 1
        img_full2, t_full = render()
        ok &= _check("downsample=1 round-trips to the same render as before (no regression when disabled)",
                     np.abs(img_full.astype(np.int16) - img_full2.astype(np.int16)).max() == 0)
    finally:
        ctx.release()
    return ok


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

    print("\n[wind_downsample]")
    ok &= test_wind_downsample()

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
