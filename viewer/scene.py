"""Context-agnostic moderngl renderer for a DxfLayout.

Geometry is uploaded once and redrawn every frame. Three kinds of primitive:

  * **Polygon fill** — *no triangulation*. Each polygon is drawn as a triangle
    fan; the GPU resolves the (possibly concave) interior by the winding-number
    rule: the fan is rendered additively into a single-channel float buffer with
    ``+1`` for front-facing and ``-1`` for back-facing fragments, then a cover
    pass fills every pixel whose accumulated winding is non-zero. This runs
    **per layer** so overlapping layers don't interfere. The only CPU prep is a
    vectorized fan index buffer (~70 ms single-core for 6 M vertices) — there is
    no earcut and no precompute cache.
  * **Outline** — every polyline edge in one ``GL_LINES`` batch; every circle in
    one instanced ``GL_LINE_LOOP``. Opaque, drawn on top.
  * **Circle fill** — circles are convex, so their instanced triangle fan fills
    correctly with plain alpha blending (no winding needed).

Each outline vertex carries a layer id; the vertex shader looks up that layer's
color and visibility from small uniform arrays, so showing/hiding or recoloring a
layer is a uniform write with no buffer rebuild.

The same :class:`GLScene` drives both the interactive Qt widget and the headless
offscreen renderer; only the moderngl context and target framebuffer differ.
"""

from __future__ import annotations

import moderngl
import numpy as np

from .palette import layer_colors

_CIRCLE_SEGMENTS = 64
_DEFAULT_FILL_ALPHA = 0.22

# Outlines + circles: per-vertex/instance layer -> color, with a visibility cull.
_FRAG_COLOR = """
#version 330
uniform float u_alpha;
in vec3 v_color;
out vec4 f_color;
void main() { f_color = vec4(v_color, u_alpha); }
"""

_VERT_OUTLINE = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_offset;
uniform vec3 u_color[MAXL];
uniform float u_visible[MAXL];
in vec2 in_pos;
in float in_layer;
out vec3 v_color;
void main() {
    int lid = int(in_layer + 0.5);
    v_color = u_color[lid];
    if (u_visible[lid] < 0.5)
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    else
        gl_Position = vec4(in_pos * u_scale + u_offset, 0.0, 1.0);
}
"""

_VERT_CIRCLE = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_offset;
uniform vec3 u_color[MAXL];
uniform float u_visible[MAXL];
in vec2 in_unit;
in vec3 in_circ;          // cx, cy, r
in float in_clayer;
out vec3 v_color;
void main() {
    int lid = int(in_clayer + 0.5);
    v_color = u_color[lid];
    vec2 world = in_circ.xy + in_unit * in_circ.z;
    if (u_visible[lid] < 0.5)
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    else
        gl_Position = vec4(world * u_scale + u_offset, 0.0, 1.0);
}
"""

# Winding pass: position only, output +/-1 by facing into an R32F target.
_VERT_WIND = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_offset;
in vec2 in_pos;
void main() { gl_Position = vec4(in_pos * u_scale + u_offset, 0.0, 1.0); }
"""

_FRAG_WIND = """
#version 330
layout(location = 0) out float w;
void main() { w = gl_FrontFacing ? 1.0 : -1.0; }
"""

# Cover pass: fullscreen; fill where this layer's winding buffer is non-zero.
_VERT_COVER = """
#version 330
in vec2 in_p;
void main() { gl_Position = vec4(in_p, 0.0, 1.0); }
"""

_FRAG_COVER = """
#version 330
uniform sampler2D u_wind;
uniform vec3 u_fill_color;
uniform float u_alpha;
out vec4 f_color;
void main() {
    float w = texelFetch(u_wind, ivec2(gl_FragCoord.xy), 0).r;
    if (abs(w) < 0.5) discard;          // exterior pixel
    f_color = vec4(u_fill_color, u_alpha);
}
"""

# Background grid: procedural dots at "nice" world-spaced nodes (fullscreen pass).
_FRAG_GRID = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_offset;
uniform vec2 u_viewport;
uniform float u_spacing;
uniform float u_upp;
uniform vec3 u_dot_color;
uniform float u_dot_alpha;
uniform float u_dot_px;
out vec4 f_color;
void main() {
    vec2 clip = 2.0 * gl_FragCoord.xy / u_viewport - 1.0;
    vec2 world = (clip - u_offset) / u_scale;
    vec2 node = floor(world / u_spacing + 0.5) * u_spacing;
    float dpx = length(world - node) / u_upp;       // distance to nearest node, px
    float a = 1.0 - smoothstep(u_dot_px - 0.75, u_dot_px + 0.75, dpx);
    if (a <= 0.0) discard;
    f_color = vec4(u_dot_color, a * u_dot_alpha);
}
"""

_GRID_TARGET_PX = 78.0      # aim for ~this on-screen spacing between dots
_GRID_DOT_PX = 1.6          # dot radius in pixels


def _nice_spacing(raw: float) -> float:
    """Round a raw world spacing up to a 1 / 2 / 5 x 10^k 'nice' value."""
    import math
    if raw <= 0:
        return 1.0
    base = 10.0 ** math.floor(math.log10(raw))
    m = raw / base
    nice = 1.0 if m < 1.5 else 2.0 if m < 3.5 else 5.0 if m < 7.5 else 10.0
    return nice * base


def nice_grid_spacing(upp: float) -> float:
    """Grid/scale-bar spacing (world units) for a given units-per-pixel."""
    return _nice_spacing(upp * _GRID_TARGET_PX)


class GLScene:
    """GPU geometry + shaders for one layout. Construct inside an active context."""

    def __init__(self, ctx, layout, fill_alpha: float = _DEFAULT_FILL_ALPHA):
        self.ctx = ctx
        self.n_layers = max(layout.n_layers, 1)
        self.colors = layer_colors(self.n_layers)
        self.visible = np.ones(self.n_layers, np.float32)
        self.show_fill = True
        self.show_grid = True
        self.grid_spacing = 1.0     # world units between grid nodes (set each draw)
        self.fill_alpha = float(fill_alpha)
        self._shade = 1.0          # color multiplier (dimmed in light-background mode)

        maxl = str(self.n_layers)
        self.outline_prog = ctx.program(
            vertex_shader=_VERT_OUTLINE.replace("MAXL", maxl), fragment_shader=_FRAG_COLOR)
        self.circ_prog = ctx.program(
            vertex_shader=_VERT_CIRCLE.replace("MAXL", maxl), fragment_shader=_FRAG_COLOR)
        self.wind_prog = ctx.program(vertex_shader=_VERT_WIND, fragment_shader=_FRAG_WIND)
        self.cover_prog = ctx.program(vertex_shader=_VERT_COVER, fragment_shader=_FRAG_COVER)
        self.grid_prog = ctx.program(vertex_shader=_VERT_COVER, fragment_shader=_FRAG_GRID)
        for prog in (self.outline_prog, self.circ_prog):
            prog["u_color"].write(self.colors.tobytes())

        fs = np.array([-1, -1, 3, -1, -1, 3], np.float32)      # fullscreen triangle
        fs_buf = ctx.buffer(fs.tobytes())
        self.cover_vao = ctx.vertex_array(self.cover_prog, [(fs_buf, "2f", "in_p")])
        self.grid_vao = ctx.vertex_array(self.grid_prog, [(fs_buf, "2f", "in_p")])

        self._wind_tex = self._wind_fbo = None
        self._wind_size = None
        self._raw_pos = None
        self.fill_vao = None
        self.real_fill_vao = None        # set by install_triangulated_fill, once ready
        self._build_polylines(ctx, layout)
        self._build_fill(ctx, layout)
        self._build_circles(ctx, layout)

    # -- geometry upload --------------------------------------------------
    def _build_polylines(self, ctx, layout) -> None:
        verts = np.ascontiguousarray(layout.verts, np.float32)   # (N, 2)
        n = len(verts)
        self.line_vao = None
        if n == 0:
            return
        start = np.asarray(layout.poly_start, np.int64)
        count = np.asarray(layout.poly_count, np.int64)
        layer = np.asarray(layout.poly_layer, np.int64)
        flags = np.asarray(layout.poly_flags, np.int64)
        self._raw_pos = verts
        self._start, self._count, self._layer = start, count, layer

        # Shared vertex buffers (positions + per-vertex layer). Both the outline
        # and the fill pass index into these, so the GPU assembles the primitives
        # and the CPU never expands a per-edge segment buffer or duplicates verts.
        vert_layer = np.clip(np.repeat(layer, count), 0, self.n_layers - 1).astype(np.float32)
        self._pos_buf = ctx.buffer(verts.tobytes())
        self._lay_buf = ctx.buffer(vert_layer.tobytes())

        # Outline edges as GL_LINES element indices: (i, next(i)), wrapping the
        # last vertex of a closed polyline back to its start.
        nxt = np.arange(1, n + 1, dtype=np.int64)
        last = start + count - 1
        nxt[last] = np.where((flags & 1).astype(bool), start, last)
        line_idx = np.empty((n, 2), np.uint32)
        line_idx[:, 0] = np.arange(n, dtype=np.uint32)
        line_idx[:, 1] = nxt.astype(np.uint32)
        self.line_vao = ctx.vertex_array(
            self.outline_prog,
            [(self._pos_buf, "2f", "in_pos"), (self._lay_buf, "1f", "in_layer")],
            index_buffer=ctx.buffer(line_idx.reshape(-1).tobytes()), index_element_size=4)

    def _build_fill(self, ctx, layout) -> None:
        """Build the layer-sorted triangle-fan index buffer + per-layer ranges."""
        self.fill_vao = None
        if self._raw_pos is None or len(self._raw_pos) == 0:
            return
        start, count, layer = self._start, self._count, self._layer

        order = np.argsort(layer, kind="stable")        # polygons grouped by layer
        t = np.clip(count - 2, 0, None)[order]           # fan triangles per polygon
        if t.sum() == 0:
            return
        gid = np.repeat(order, t)                        # polygon id per triangle
        k = np.arange(t.sum()) - np.repeat(np.cumsum(t) - t, t)   # fan tri index in poly
        s = start[gid]
        fan = np.empty((t.sum(), 3), np.uint32)
        fan[:, 0] = s
        fan[:, 1] = s + k + 1
        fan[:, 2] = s + k + 2
        idx = fan.reshape(-1)

        tri_per_layer = np.bincount(layer[order], weights=t, minlength=self.n_layers).astype(np.int64)
        self._fill_count = (tri_per_layer * 3).astype(np.int64)            # index counts
        self._fill_off = ((np.cumsum(tri_per_layer) - tri_per_layer) * 3).astype(np.int64)

        # Per-layer world-space bbox (fillable polygons only), for scissoring the
        # wind/cover passes in draw() to where each layer's geometry actually is
        # instead of the full viewport every layer -- the wind pass's clear +
        # rasterization cost is fragment-fill-rate bound (profiled: ~1.85ms at
        # 30k px vs ~41ms at 5M px for the same geometry), so this is the
        # dominant lever for layer-heavy files.
        v = self._raw_pos
        fillable = t > 0                                  # polygons contributing fill tris
        if fillable.any():
            poly_idx = order[fillable]
            poly_xmin = np.minimum.reduceat(v[:, 0], start)[poly_idx]
            poly_xmax = np.maximum.reduceat(v[:, 0], start)[poly_idx]
            poly_ymin = np.minimum.reduceat(v[:, 1], start)[poly_idx]
            poly_ymax = np.maximum.reduceat(v[:, 1], start)[poly_idx]
            poly_layer = layer[poly_idx]
            self._layer_xmin = np.full(self.n_layers, np.inf)
            self._layer_xmax = np.full(self.n_layers, -np.inf)
            self._layer_ymin = np.full(self.n_layers, np.inf)
            self._layer_ymax = np.full(self.n_layers, -np.inf)
            np.minimum.at(self._layer_xmin, poly_layer, poly_xmin)
            np.maximum.at(self._layer_xmax, poly_layer, poly_xmax)
            np.minimum.at(self._layer_ymin, poly_layer, poly_ymin)
            np.maximum.at(self._layer_ymax, poly_layer, poly_ymax)
        else:
            self._layer_xmin = self._layer_ymin = np.full(self.n_layers, np.inf)
            self._layer_xmax = self._layer_ymax = np.full(self.n_layers, -np.inf)

        self.fill_vao = ctx.vertex_array(
            self.wind_prog, [(self._pos_buf, "2f", "in_pos")],   # shared vertex buffer
            index_buffer=ctx.buffer(idx.tobytes()), index_element_size=4)

    def _build_circles(self, ctx, layout) -> None:
        circ = np.asarray(layout.circ, np.float32)
        self.n_circ = len(circ)
        self.circ_loop_vao = self.circ_fan_vao = None
        if self.n_circ == 0:
            return
        clayer = np.asarray(layout.circ_layer, np.float32)
        inst = np.empty((self.n_circ, 4), np.float32)
        inst[:, :3] = circ
        inst[:, 3] = np.clip(clayer, 0, self.n_layers - 1)
        inst_buf = ctx.buffer(inst.tobytes())

        th = np.linspace(0.0, 2.0 * np.pi, _CIRCLE_SEGMENTS, endpoint=False)
        ring = np.stack([np.cos(th), np.sin(th)], axis=1).astype(np.float32)
        fan = np.empty((_CIRCLE_SEGMENTS + 2, 2), np.float32)
        fan[0] = (0.0, 0.0)
        fan[1:_CIRCLE_SEGMENTS + 1] = ring
        fan[_CIRCLE_SEGMENTS + 1] = ring[0]
        self._fan_n = _CIRCLE_SEGMENTS + 2

        inst_fmt = (inst_buf, "3f 1f/i", "in_circ", "in_clayer")
        self.circ_loop_vao = ctx.vertex_array(
            self.circ_prog, [(ctx.buffer(ring.tobytes()), "2f", "in_unit"), inst_fmt])
        self.circ_fan_vao = ctx.vertex_array(
            self.circ_prog, [(ctx.buffer(fan.tobytes()), "2f", "in_unit"), inst_fmt])

    # -- per-frame state --------------------------------------------------
    def set_layer_visible(self, layer_id: int, visible: bool) -> None:
        if 0 <= layer_id < self.n_layers:
            self.visible[layer_id] = 1.0 if visible else 0.0

    def set_all_visible(self, visible: bool) -> None:
        self.visible[:] = 1.0 if visible else 0.0

    def toggle_fill(self) -> bool:
        self.show_fill = not self.show_fill
        return self.show_fill

    def set_grid(self, on: bool) -> None:
        self.show_grid = bool(on)

    def set_shade(self, shade: float) -> None:
        """Multiply all layer colors by ``shade`` (used to darken for light bg)."""
        self._shade = float(shade)
        dimmed = (self.colors * self._shade).astype(np.float32)
        for prog in (self.outline_prog, self.circ_prog):
            prog["u_color"].write(dimmed.tobytes())

    def _ensure_wind(self, size) -> None:
        if self._wind_size == size:
            return
        if self._wind_fbo is not None:
            self._wind_fbo.release()
            self._wind_tex.release()
        self._wind_tex = self.ctx.texture(size, 1, dtype="f4")
        self._wind_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._wind_fbo = self.ctx.framebuffer(color_attachments=[self._wind_tex])
        self._wind_size = size

    def _screen_scissor(self, lid, scale, offset, W, H):
        """Pixel-space (x, y, w, h) scissor rect (GL bottom-left origin) for
        layer ``lid``'s on-screen bbox, clamped to the framebuffer, or None if
        it's entirely off-screen. clip = world * scale + offset (same
        transform the vertex shaders use); world/clip +y is up, matching GL
        window coordinates, so no axis flip is needed."""
        xmin, xmax = self._layer_xmin[lid], self._layer_xmax[lid]
        ymin, ymax = self._layer_ymin[lid], self._layer_ymax[lid]
        if xmin > xmax:                                    # no fillable geometry
            return None
        sx, sy = scale
        ox, oy = offset
        cx0, cx1 = xmin * sx + ox, xmax * sx + ox
        cy0, cy1 = ymin * sy + oy, ymax * sy + oy
        x0 = int(np.floor((cx0 + 1.0) * 0.5 * W)) - 1       # -1px margin: AA/winding can
        x1 = int(np.ceil((cx1 + 1.0) * 0.5 * W)) + 1        # touch a pixel just outside
        y0 = int(np.floor((cy0 + 1.0) * 0.5 * H)) - 1       # the exact transformed bbox
        y1 = int(np.ceil((cy1 + 1.0) * 0.5 * H)) + 1
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, W), min(y1, H)
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1 - x0, y1 - y0)

    def install_triangulated_fill(self, idx, fill_off, fill_count) -> None:
        """Swap the wind pass's per-layer triangle source from the naive fan
        to a real (non-overlapping) triangulation, computed by
        ``viewer.triangulate.compute_real_fill`` -- typically on a background
        thread, since it's pure numpy/earcut with no GL calls, then handed to
        this method on the GL-owning thread once ready.

        The winding rule + cover pass are unchanged (still required for
        correctness: they're what makes two overlapping same-layer polygons
        blend exactly once per pixel, not the triangulation) -- only the
        triangle data the wind pass rasterizes changes, cutting its fragment
        count for polygons where a fan would have self-overlapped heavily.
        Until this is called, draw() keeps using the (always-correct, just
        more wind-pass overdraw on complex geometry) fan fallback.
        """
        idx = np.ascontiguousarray(idx, np.uint32)
        if self.real_fill_vao is not None:
            self.real_fill_vao.release()
        self.real_fill_vao = self.ctx.vertex_array(
            self.wind_prog, [(self._pos_buf, "2f", "in_pos")],
            index_buffer=self.ctx.buffer(idx.tobytes()), index_element_size=4)
        self._real_fill_off = np.asarray(fill_off, np.int64)
        self._real_fill_count = np.asarray(fill_count, np.int64)

    def draw(self, main_fbo, scale, offset, grid_spacing=None) -> None:
        """Render into ``main_fbo`` (already bound + cleared by the caller).

        ``grid_spacing`` overrides the grid/scale-bar spacing (world units); pass
        the camera-derived value so it matches the on-screen scale bar exactly.
        """
        ctx = self.ctx
        scale = (float(scale[0]), float(scale[1]))
        offset = (float(offset[0]), float(offset[1]))
        for prog in (self.outline_prog, self.circ_prog):
            prog["u_scale"].value = scale
            prog["u_offset"].value = offset
            prog["u_visible"].write(self.visible.tobytes())
        self.wind_prog["u_scale"].value = scale
        self.wind_prog["u_offset"].value = offset

        # Grid spacing (also drives the on-screen scale bar) — computed every frame.
        W, H = main_fbo.size
        upp = 2.0 / (scale[0] * W)
        self.grid_spacing = float(grid_spacing) if grid_spacing else _nice_spacing(upp * _GRID_TARGET_PX)

        # Pass 0: background dot grid (behind all geometry).
        if self.show_grid:
            gp = self.grid_prog
            gp["u_scale"].value = scale
            gp["u_offset"].value = offset
            gp["u_viewport"].value = (float(W), float(H))
            gp["u_spacing"].value = self.grid_spacing
            gp["u_upp"].value = upp
            gp["u_dot_color"].value = (0.46, 0.49, 0.57) if self._shade >= 1.0 else (0.42, 0.42, 0.50)
            gp["u_dot_alpha"].value = 0.7
            gp["u_dot_px"].value = _GRID_DOT_PX
            main_fbo.use()
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.grid_vao.render(moderngl.TRIANGLES, vertices=3)
            ctx.disable(moderngl.BLEND)

        # Pass 1a: per-layer winding fills for polygons, scissored to each
        # layer's on-screen bbox. The winding rule + cover pass are what make
        # two overlapping same-layer polygons blend exactly once per pixel --
        # that's still needed regardless of triangulation, so it always runs.
        # What changes per layer is which triangle source feeds the wind
        # pass: the real (non-overlapping) triangulation from
        # viewer.triangulate once install_triangulated_fill has set it up
        # (typically a few seconds after load -- computed on a background
        # thread since it's too slow on complex geometry to block initial
        # load), or the naive fan fallback before then / for layers it
        # doesn't cover. A non-convex fan can self-overlap heavily (the same
        # pixels rasterized several times for one polygon); the wind pass's
        # clear + rasterization cost is fragment-fill-rate bound (profiled:
        # ~1.85ms at 30k px vs ~41ms at 5M px for the same geometry), so
        # real triangulation's lack of self-overlap is a direct, measured win
        # (profiled ~6x faster on a real 6M-vertex file's full-chip view).
        if self.show_fill and self.fill_vao is not None:
            self._ensure_wind(main_fbo.size)
            self.cover_prog["u_wind"].value = 0
            self.cover_prog["u_alpha"].value = self.fill_alpha
            self._wind_tex.use(0)
            ctx.enable(moderngl.BLEND)
            W, H = main_fbo.size
            real_ready = self.real_fill_vao is not None
            for lid in range(self.n_layers):
                if real_ready:
                    vao, cnt, off = self.real_fill_vao, int(self._real_fill_count[lid]), int(self._real_fill_off[lid])
                else:
                    vao, cnt, off = self.fill_vao, int(self._fill_count[lid]), int(self._fill_off[lid])
                if cnt == 0 or self.visible[lid] < 0.5:
                    continue
                rect = self._screen_scissor(lid, scale, offset, W, H)
                if rect is None:
                    continue                                          # entirely off-screen
                self._wind_fbo.scissor = rect
                self._wind_fbo.use()
                ctx.clear(0.0)
                ctx.blend_func = moderngl.ONE, moderngl.ONE          # accumulate winding
                vao.render(moderngl.TRIANGLES, vertices=cnt, first=off)

                main_fbo.scissor = rect
                main_fbo.use()
                ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
                self.cover_prog["u_fill_color"].value = tuple(
                    float(c * self._shade) for c in self.colors[lid])
                self.cover_vao.render(moderngl.TRIANGLES, vertices=3)
            main_fbo.scissor = None        # restore full viewport for the passes below

        # Pass 1b: convex circle fills (plain alpha).
        if self.show_fill and self.circ_fan_vao is not None:
            main_fbo.use()
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.circ_prog["u_alpha"].value = self.fill_alpha
            self.circ_fan_vao.render(
                moderngl.TRIANGLE_FAN, vertices=self._fan_n, instances=self.n_circ)

        # Pass 2: opaque outlines on top.
        main_fbo.use()
        ctx.disable(moderngl.BLEND)
        self.outline_prog["u_alpha"].value = 1.0
        if self.line_vao is not None:
            self.line_vao.render(moderngl.LINES)
        if self.circ_loop_vao is not None:
            self.circ_prog["u_alpha"].value = 1.0
            self.circ_loop_vao.render(
                moderngl.LINE_LOOP, vertices=_CIRCLE_SEGMENTS, instances=self.n_circ)
