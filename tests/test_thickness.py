#!/usr/bin/env python3
"""Tests for the optional film-thickness map overlay.

Two independent surfaces:

  * ``viewer.thickness`` -- the pure-numpy CSV parse + IDW gridding. Checks the
    field shape/normalization, that coverage masks cells with no nearby measured
    point, and that a clean regular grid (points on a lattice) is fully covered.
    No GL needed.
  * ``GLScene`` -- uploads the gridded field and draws the fullscreen colormap
    pass *behind* the geometry. A headless render must show plasma pixels inside
    the data bbox and none once the overlay is toggled off. Requires a real
    OpenGL 3.3+ context.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_csv(tmp, rows, header="x,y,thickness_nm"):
    p = os.path.join(tmp, "map.csv")
    with open(p, "w") as f:
        if header:
            f.write(header + "\n")
        for x, y, t in rows:
            f.write(f"{x},{y},{t}\n")
    return p


def test_gridding_basic(tmp_path):
    from viewer.thickness import load_thickness_csv

    # thickness ramps with x from 100 to 300 nm over a regular 11x11 lattice.
    rows = [(x, y, 100 + 2.0 * x) for x in range(0, 101, 10) for y in range(0, 101, 10)]
    m = load_thickness_csv(_write_csv(tmp_path, rows))

    assert m.field.ndim == 3 and m.field.shape[2] == 2
    assert m.n_points == len(rows)
    assert abs(m.vmin - 100.0) < 1e-6 and abs(m.vmax - 300.0) < 1e-6
    assert m.bbox == (0.0, 0.0, 100.0, 100.0)
    # A dense regular lattice should be (essentially) fully covered.
    assert m.field[:, :, 1].mean() > 0.98
    # Normalized value increases left->right (row 0 = ymin; columns are x).
    lo = m.field[:, :5, 0].mean()
    hi = m.field[:, -5:, 0].mean()
    assert hi > lo and 0.0 <= m.field[:, :, 0].min() and m.field[:, :, 0].max() <= 1.0


def test_coverage_masks_far_cells(tmp_path):
    from viewer.thickness import load_thickness_csv

    # Two tight point clusters far apart: the empty middle must be uncovered.
    rows = ([(x, y, 200.0) for x in range(0, 11) for y in range(0, 11)]
            + [(x, y, 260.0) for x in range(190, 201) for y in range(0, 11)])
    m = load_thickness_csv(_write_csv(tmp_path, rows))
    gh, gw, _ = m.field.shape
    assert m.field[gh // 2, gw // 2, 1] < 0.5      # gap between clusters: no data


def test_bad_csv_raises(tmp_path):
    from viewer.thickness import load_thickness_csv

    p = os.path.join(tmp_path, "empty.csv")
    with open(p, "w") as f:
        f.write("# only a comment\n\n")
    try:
        load_thickness_csv(p)
    except ValueError:
        return
    raise AssertionError("expected ValueError on a CSV with no data rows")


def _bbox(v):
    b = types.SimpleNamespace()
    b.xmin, b.xmax = float(v[:, 0].min()), float(v[:, 0].max())
    b.ymin, b.ymax = float(v[:, 1].min()), float(v[:, 1].max())
    b.width, b.height = b.xmax - b.xmin, b.ymax - b.ymin
    return b


def test_overlay_renders_headless(tmp_path):
    import moderngl

    from viewer.camera import Camera2D
    from viewer.scene import GLScene
    from viewer.thickness import load_thickness_csv

    verts = np.array([(0, 0), (100, 0), (100, 100), (0, 100)], np.float32)
    layout = types.SimpleNamespace(
        verts=verts, poly_start=np.array([0]), poly_count=np.array([4]),
        poly_layer=np.array([0]), poly_flags=np.array([1]), n_layers=1,
        circ=np.empty((0, 3), np.float32), circ_layer=np.array([], np.float32))

    rows = [(x, y, 100 + 2.0 * x) for x in range(0, 101, 10) for y in range(0, 101, 10)]
    m = load_thickness_csv(_write_csv(tmp_path, rows))

    ctx = moderngl.create_standalone_context(require=330)
    try:
        scene = GLScene(ctx, layout)
        scene.show_fill = scene.show_grid = False    # isolate the thickness pass
        scene.set_thickness(m)
        size = (400, 400)
        cam = Camera2D()
        cam.resize(*size)
        cam.fit(_bbox(verts))
        (sx, sy), (ox, oy) = cam.scale_offset()

        def render():
            fbo = ctx.simple_framebuffer(size)
            fbo.use()
            ctx.clear(0.0, 0.0, 0.0)
            scene.draw(fbo, (sx, sy), (ox, oy))
            data = fbo.read(components=3)
            return np.frombuffer(data, np.uint8).reshape(size[1], size[0], 3)

        # The quad's opaque outline always draws at the edges; sample the
        # interior (world ~(50,50) -> screen center), where only the overlay paints.
        cy, cx = size[1] // 2, size[0] // 2
        img = render()
        lit = (img.sum(axis=2) > 20).sum()
        assert lit > 1000, f"thickness overlay drew almost nothing ({lit} px)"
        assert img[cy, cx].sum() > 20, f"interior not colored by overlay: {img[cy, cx]}"

        scene.set_thickness_visible(False)
        off = render()
        assert off[cy, cx].sum() == 0, f"overlay still visible after toggle off: {off[cy, cx]}"

        scene.release()
    finally:
        ctx.release()
