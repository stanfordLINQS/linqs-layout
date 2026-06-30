"""Headless render of a layout to a PNG — works without a display.

Used both as a screenshot/thumbnail CLI and as the renderer smoke test (no Qt,
no window: a moderngl standalone context renders into an off-screen framebuffer).
"""

from __future__ import annotations

import numpy as np

from .camera import Camera2D
from .scene import GLScene

# Dark background so the bright per-layer outlines pop.
BG = (0.06, 0.06, 0.08)


def render_array(layout, size=(1600, 1400), visible=None, bg=BG) -> np.ndarray:
    """Render ``layout`` to an ``(H, W, 3)`` uint8 RGB array (top row first)."""
    import moderngl

    w, h = int(size[0]), int(size[1])
    ctx = moderngl.create_standalone_context(require=330)
    try:
        ctx.multisample = True
        scene = GLScene(ctx, layout)
        if visible is not None:
            for lid, vis in enumerate(visible):
                scene.set_layer_visible(lid, bool(vis))

        cam = Camera2D()
        cam.resize(w, h)
        cam.fit(layout.bbox())
        (sx, sy), (ox, oy) = cam.scale_offset()

        # 4x MSAA render target, resolved into a plain framebuffer for readback.
        color = ctx.renderbuffer((w, h), samples=4)
        msaa = ctx.framebuffer(color_attachments=[color])
        msaa.use()
        ctx.clear(*bg)
        scene.draw((sx, sy), (ox, oy))

        resolved = ctx.simple_framebuffer((w, h))
        ctx.copy_framebuffer(resolved, msaa)
        data = resolved.read(components=3)
        img = np.frombuffer(data, np.uint8).reshape(h, w, 3)[::-1]  # flip y
        return np.ascontiguousarray(img)
    finally:
        ctx.release()


def render_png(layout, out_path: str, size=(1600, 1400), visible=None, bg=BG) -> str:
    from PIL import Image

    img = render_array(layout, size=size, visible=visible, bg=bg)
    Image.fromarray(img).save(out_path)
    return out_path


def main() -> None:
    import argparse
    import time

    from pydxf import DxfLayout

    ap = argparse.ArgumentParser(description="Render a DXF layout to PNG (headless).")
    ap.add_argument("path", help="path to a .dxf file")
    ap.add_argument("-o", "--out", default="layout.png", help="output PNG path")
    ap.add_argument("-W", "--width", type=int, default=1600)
    ap.add_argument("-H", "--height", type=int, default=1400)
    args = ap.parse_args()

    t0 = time.perf_counter()
    layout = DxfLayout(args.path)
    t1 = time.perf_counter()
    render_png(layout, args.out, size=(args.width, args.height))
    t2 = time.perf_counter()
    print(f"parsed {layout.n_polylines:,} polylines + {layout.n_circles:,} circles "
          f"in {(t1 - t0) * 1e3:.0f} ms; rendered {args.width}x{args.height} -> "
          f"{args.out} in {(t2 - t1) * 1e3:.0f} ms")


if __name__ == "__main__":
    main()
