#!/usr/bin/env python3
"""Tests for polygon picking (click-to-select) and its highlight render.

  * ``viewer.pick.Picker`` -- the pure-numpy bbox prefilter + point-in-polygon
    hit test: the smallest containing polygon wins, hidden layers are excluded,
    and a miss returns None. No GL.
  * ``GLScene.set_selection`` -- uploads the picked polygon and draws amber
    edges + vertex dots on top; a headless render must show the highlight and
    lose it after clearing. Requires a real OpenGL 3.3+ context.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _layout(n_layers=2):
    # A big frame (layer 0), a small square nested inside it (layer 1), and a
    # separate small square (layer 0).
    verts = np.array([
        (0, 0), (1000, 0), (1000, 1000), (0, 1000),        # 0: big frame
        (400, 400), (600, 400), (600, 600), (400, 600),    # 1: nested small square
        (50, 800), (150, 800), (150, 900), (50, 900),      # 2: separate square
    ], np.float64)
    return types.SimpleNamespace(
        verts=verts, poly_start=np.array([0, 4, 8]), poly_count=np.array([4, 4, 4]),
        poly_layer=np.array([0, 1, 0]), poly_flags=np.array([1, 1, 1]),
        n_layers=n_layers, circ=np.empty((0, 3), np.float32),
        circ_layer=np.array([], np.float32))


def test_pick_smallest_containing():
    from viewer.pick import Picker
    pk = Picker(_layout())
    assert pk.pick(500, 500) == 1        # nested square beats the enclosing frame
    assert pk.pick(100, 850) == 2        # only the separate square contains this
    assert pk.pick(900, 100) == 0        # only the frame reaches out here
    assert pk.pick(-5, -5) is None       # outside everything


def test_pick_respects_visibility():
    from viewer.pick import Picker
    pk = Picker(_layout())
    vis = np.array([True, False, True])  # hide layer 1 (the nested square)
    assert pk.pick(500, 500, vis) == 0   # falls through to the frame
    assert pk.pick(500, 500, np.array([False, False, False])) is None


def test_pick_geometry_accessors():
    from viewer.pick import Picker
    pk = Picker(_layout())
    assert pk.poly_verts(1).shape == (4, 2)
    assert pk.is_closed(1) is True


def test_pick_empty_layout():
    from viewer.pick import Picker
    empty = types.SimpleNamespace(
        verts=np.empty((0, 2)), poly_start=np.empty(0, np.int64),
        poly_count=np.empty(0, np.int64), poly_layer=np.empty(0, np.int64),
        poly_flags=np.empty(0, np.int64))
    assert Picker(empty).pick(0, 0) is None


def _bbox():
    b = types.SimpleNamespace()
    b.xmin, b.ymin, b.xmax, b.ymax = 0, 0, 1000, 1000
    b.width, b.height = 1000, 1000
    return b


def test_selection_highlight_renders_headless():
    import moderngl

    from viewer.camera import Camera2D
    from viewer.scene import GLScene

    layout = _layout()
    layout.verts = layout.verts.astype(np.float32)
    layout.bbox = _bbox

    ctx = moderngl.create_standalone_context(require=330)
    try:
        scene = GLScene(ctx, layout)
        size = (600, 600)
        cam = Camera2D()
        cam.resize(*size)
        cam.fit(_bbox())
        (sx, sy), (ox, oy) = cam.scale_offset()

        def amber_px():
            fbo = ctx.simple_framebuffer(size)
            fbo.use()
            ctx.clear(0.04, 0.04, 0.05)
            scene.draw(fbo, (sx, sy), (ox, oy))
            img = np.frombuffer(fbo.read(components=3), np.uint8).reshape(size[1], size[0], 3)
            return int(((img[:, :, 0] > 180) & (img[:, :, 1] > 110)
                        & (img[:, :, 1] < 210) & (img[:, :, 2] < 90)).sum())

        scene.set_selection([(layout.verts[4:8], True)])     # highlight nested square
        assert scene.has_selection()
        one = amber_px()
        assert one > 100, "selection highlight drew almost no amber"

        # Two polygons at once (nested square + separate square) -> more amber.
        scene.set_selection([(layout.verts[4:8], True), (layout.verts[8:12], True)])
        assert amber_px() > one, "second selected polygon added no highlight"

        scene.set_selection(None)
        assert not scene.has_selection()
        assert amber_px() == 0, "highlight still present after clearing selection"

        scene.release()
    finally:
        ctx.release()
