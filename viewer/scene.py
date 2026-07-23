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
  * **Outline** — every polyline edge in one ``GL_LINES`` batch; circles in
    instanced ``GL_LINE_LOOP`` batches. Opaque, drawn on top.
  * **Circle fill** — circles are convex, so their instanced triangle fan fills
    correctly with plain alpha blending (no winding needed).

Circles carry no ring geometry at all: the unit ring is generated in the vertex
shader from ``gl_VertexID``, so the segment count is a per-frame uniform picked
from the on-screen pixel radius. Instances are batched by radius octave (they
share that uniform) and, when zoomed in far enough for it to pay, culled to the
view each frame — see :class:`_CircleGroup`.

Each outline vertex carries a layer id; the vertex shader looks up that layer's
color and visibility from small uniform arrays, so showing/hiding or recoloring a
layer is a uniform write with no buffer rebuild.

The same :class:`GLScene` drives both the interactive Qt widget and the headless
offscreen renderer; only the moderngl context and target framebuffer differ.
"""

from __future__ import annotations

import math

import moderngl
import numpy as np

from .palette import layer_colors
from .thickness import colormap_lut

# Circle tessellation is chosen per frame from the on-screen pixel radius (see
# draw()); these bound it. _CIRCLE_TOL_PX is the max chord sagitta in pixels —
# below ~a quarter pixel the faceting is under the MSAA resolution.
_CIRCLE_MIN_SEG = 8
_CIRCLE_MAX_SEG = 256
_CIRCLE_TOL_PX = 0.25
# Per-frame view cull of circle instances (see _CircleGroup.visible) only pays
# for itself on a big, heavily tessellated batch — otherwise the numpy mask
# costs more than the vertices it saves.
_CIRCLE_CULL_MIN = 4096
_CIRCLE_CULL_SEG = 32
_DEFAULT_FILL_ALPHA = 0.22

# Outlines + circles: per-vertex/instance layer -> color, with a visibility cull.
_FRAG_COLOR = """
#version 330
uniform float u_alpha;
in vec3 v_color;
out vec4 f_color;
void main() { f_color = vec4(v_color, u_alpha); }
"""

# Every position-consuming shader transforms *relative to the camera center*
# (``u_org_hi`` + ``u_org_lo``) rather than with the algebraically equivalent
# ``world * scale + offset``. That old form is a large-minus-large subtraction in
# f32: at this layout's +/-7000 extent the f32 spacing is ~4.9e-4 world units, so
# once a pixel covers less than ~1e-3 units (a viewport a couple of microns wide)
# the surviving clip value carried a large fraction of a pixel of error and
# vertices visibly snapped to a grid -- smooth arcs turned into unequal chords.
# Splitting the center into an f32 ``hi`` plus the f64 remainder ``lo`` makes
# ``(in_pos - u_org_hi)`` an exact cancellation (both operands are close, so
# Sterbenz applies) and keeps full f32 mantissa on the small relative value.
# Cost: one extra vector subtract per vertex; the multiply-add it replaces is
# gone, so it is a wash.
_TRANSFORM = """
uniform vec2 u_scale;
uniform vec2 u_org_hi;    // camera center, f32 part
uniform vec2 u_org_lo;    // camera center, f64 remainder (tiny)
vec2 to_clip(vec2 world) { return ((world - u_org_hi) - u_org_lo) * u_scale; }
"""

_VERT_OUTLINE = """
#version 330
TRANSFORM
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
        gl_Position = vec4(to_clip(in_pos), 0.0, 1.0);
}
"""

# Circles are drawn attribute-less: the unit ring is generated from gl_VertexID
# and ``u_seg``, which draw() picks per frame from the largest circle's on-screen
# pixel radius. A fixed segment count cannot be right at both ends -- 64 was
# ~50 px per flat chord on a circle zoomed to fill the viewport (visibly a
# polygon) and 64 wasted vertices on a sub-pixel dot when zoomed out.
#   u_fan = 1: TRIANGLE_FAN, vertex 0 is the center, then u_seg+1 ring points
#              (the last repeats the first to close the fan).
#   u_fan = 0: LINE_LOOP over u_seg ring points.
# The ring offset is added *after* the origin subtraction so the radius keeps
# full f32 precision instead of being swamped by a far-from-origin center.
_VERT_CIRCLE = """
#version 330
TRANSFORM
uniform vec3 u_color[MAXL];
uniform float u_visible[MAXL];
uniform int u_seg;
uniform int u_fan;
in vec3 in_circ;          // cx, cy, r
in float in_clayer;
out vec3 v_color;
void main() {
    int lid = int(in_clayer + 0.5);
    v_color = u_color[lid];
    vec2 unit = vec2(0.0);
    if (gl_VertexID >= u_fan) {
        float a = 6.2831853071795864 * float(gl_VertexID - u_fan) / float(u_seg);
        unit = vec2(cos(a), sin(a));
    }
    if (u_visible[lid] < 0.5)
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
    else
        gl_Position = vec4(to_clip(in_circ.xy) + unit * in_circ.z * u_scale, 0.0, 1.0);
}
"""

# Winding pass: position only, output +/-1 by facing into an R32F target.
_VERT_WIND = """
#version 330
TRANSFORM
in vec2 in_pos;
void main() { gl_Position = vec4(to_clip(in_pos), 0.0, 1.0); }
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
uniform int u_downsample;     // wind buffer is rendered at 1/u_downsample resolution
out vec4 f_color;
void main() {
    float w = texelFetch(u_wind, ivec2(gl_FragCoord.xy) / u_downsample, 0).r;
    if (abs(w) < 0.5) discard;          // exterior pixel
    f_color = vec4(u_fill_color, u_alpha);
}
"""

# Background grid: procedural dots at "nice" world-spaced nodes (fullscreen pass).
# Everything stays in *view-relative* world units for the same f32 reason as
# _TRANSFORM: node positions are found from u_org_mod, the camera center reduced
# mod the grid spacing on the CPU in f64, so the shader never handles a large
# absolute coordinate (which would make the dots wobble or smear at deep zoom).
_FRAG_GRID = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_org_mod;       // camera center mod u_spacing (f64-reduced on the CPU)
uniform vec2 u_viewport;
uniform float u_spacing;
uniform float u_upp;
uniform vec3 u_dot_color;
uniform float u_dot_alpha;
uniform float u_dot_px;
out vec4 f_color;
void main() {
    vec2 clip = 2.0 * gl_FragCoord.xy / u_viewport - 1.0;
    vec2 rel = clip / u_scale;                      // world offset from view center
    vec2 p = rel + u_org_mod;                       // ...in the node lattice's phase
    vec2 node = floor(p / u_spacing + 0.5) * u_spacing;
    float dpx = length(p - node) / u_upp;           // distance to nearest node, px
    float a = 1.0 - smoothstep(u_dot_px - 0.75, u_dot_px + 0.75, dpx);
    if (a <= 0.0) discard;
    f_color = vec4(u_dot_color, a * u_dot_alpha);
}
"""

# Thickness map: fullscreen colormap of a gridded film-thickness field. World
# coords are reconstructed from gl_FragCoord (as in the grid pass), mapped into
# the field's world bbox, and the normalized thickness (r) is looked up in the
# plasma LUT. Cells outside the bbox or with no coverage (g < 0.5) are discarded.
_FRAG_THICK = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_org_bb;        // camera center - bbmin (f64-differenced on the CPU)
uniform vec2 u_viewport;
uniform vec2 u_bbmin;
uniform vec2 u_bbmax;
uniform float u_alpha;
uniform float u_vmin;
uniform float u_vmax;
uniform sampler2D u_field;
uniform sampler2D u_lut;
out vec4 f_color;
void main() {
    vec2 clip = 2.0 * gl_FragCoord.xy / u_viewport - 1.0;
    vec2 uv = (clip / u_scale + u_org_bb) / (u_bbmax - u_bbmin);
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
    vec2 s = texture(u_field, uv).rg;           // r = raw nm, g = coverage
    if (s.g < 0.5) discard;                      // no measured data here
    float t = (s.r - u_vmin) / max(u_vmax - u_vmin, 1e-9);
    vec3 c = texture(u_lut, vec2(clamp(t, 0.0, 1.0), 0.5)).rgb;
    f_color = vec4(c, u_alpha);
}
"""

# Selection highlight: the picked polygon's edges (LINE_LOOP) and vertices
# (round GL_POINTS) drawn on top of everything in the amber accent. One vertex
# shader serves both draws; it also writes gl_PointSize (ignored by the line
# draw). Colors are chosen per background so the highlight stays vivid in both.
_VERT_SEL = """
#version 330
TRANSFORM
uniform float u_point_size;
in vec2 in_pos;
void main() {
    gl_Position = vec4(to_clip(in_pos), 0.0, 1.0);
    gl_PointSize = u_point_size;
}
"""

_FRAG_SEL_LINE = """
#version 330
uniform vec3 u_color;
out vec4 f_color;
void main() { f_color = vec4(u_color, 1.0); }
"""

_FRAG_SEL_PT = """
#version 330
uniform vec3 u_color;
out vec4 f_color;
void main() {
    vec2 d = gl_PointCoord - vec2(0.5);
    if (dot(d, d) > 0.25) discard;      // clip the square point sprite to a disc
    f_color = vec4(u_color, 1.0);
}
"""

_SEL_COLOR_DARK = (1.0, 0.69, 0.0)      # amber accent (255,176,0) on the dark canvas
_SEL_COLOR_LIGHT = (0.80, 0.33, 0.0)    # deeper amber so it reads on the light canvas
_SEL_POINT_PX = 8.0                     # vertex marker diameter, pixels

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


class _CircleGroup:
    """One instanced circle batch: all members within a 2x radius band, so they
    can share a segment count (see GLScene._build_circles).

    Holds two GPU buffers over the same instances -- the full set, and a scratch
    buffer refilled per frame with only the instances inside the view. The
    scratch path exists because segment count is a *uniform*: zoomed deep into
    one circle every instance in the group pays that circle's tessellation, so
    83k off-screen vias were being shaded at 160 segments to draw the one on
    screen (measured: 4.3 -> 7.4 ms at a 0.5 um view). Culling is skipped
    entirely unless the group is both large and highly tessellated, which is
    exactly the zoomed-in case -- at a fit view the mask never runs.
    """

    def __init__(self, scene, ctx, inst):
        self.n = len(inst)
        self.xy = np.ascontiguousarray(inst[:, :2])       # for the per-frame cull
        self.r = np.ascontiguousarray(inst[:, 2])
        self.rmax = float(self.r.max())
        self._inst = np.ascontiguousarray(inst)
        fmt = "3f 1f/i", "in_circ", "in_clayer"
        buf = scene._own(ctx.buffer(self._inst.tobytes()))
        self.vao = scene._own(ctx.vertex_array(scene.circ_prog, [(buf, *fmt)]))
        self._cull_buf = self._cull_vao = None
        self._ctx, self._scene, self._fmt = ctx, scene, fmt

    def visible(self, origin, half_w, half_h, seg):
        """Return (vao, instance_count) to draw this frame."""
        if self.n < _CIRCLE_CULL_MIN or seg <= _CIRCLE_CULL_SEG:
            return self.vao, self.n            # cheap already; skip the mask
        ox, oy = origin
        m = ((np.abs(self.xy[:, 0] - ox) <= half_w + self.r) &
             (np.abs(self.xy[:, 1] - oy) <= half_h + self.r))
        k = int(m.sum())
        if k > self.n // 2:                    # most are on screen; not worth the upload
            return self.vao, self.n
        if self._cull_vao is None:             # allocate once, at full size
            self._cull_buf = self._scene._own(self._ctx.buffer(reserve=self._inst.nbytes))
            self._cull_vao = self._scene._own(self._ctx.vertex_array(
                self._scene.circ_prog, [(self._cull_buf, *self._fmt)]))
        if k:
            self._cull_buf.write(self._inst[m].tobytes())
        return self._cull_vao, k


def _circle_segments(r_px: float) -> int:
    """Ring segments for a circle of ``r_px`` on-screen pixel radius, so the
    chord sagitta stays under _CIRCLE_TOL_PX."""
    if r_px <= _CIRCLE_TOL_PX:
        return _CIRCLE_MIN_SEG
    n = math.pi / math.acos(max(1.0 - _CIRCLE_TOL_PX / r_px, -1.0))
    return int(min(max(math.ceil(n), _CIRCLE_MIN_SEG), _CIRCLE_MAX_SEG))


def nice_grid_spacing(upp: float) -> float:
    """Grid/scale-bar spacing (world units) for a given units-per-pixel."""
    return _nice_spacing(upp * _GRID_TARGET_PX)


class GLScene:
    """GPU geometry + shaders for one layout. Construct inside an active context."""

    def __init__(self, ctx, layout, fill_alpha: float = _DEFAULT_FILL_ALPHA):
        self.ctx = ctx
        # Every GL object we allocate, so release() can free them on reload.
        # moderngl does not free GL resources on Python GC by default, so a
        # rebuilt scene would otherwise leak GPU buffers/programs each reload.
        self._owned: list = []
        self.n_layers = max(layout.n_layers, 1)
        self.colors = layer_colors(self.n_layers)
        self.visible = np.ones(self.n_layers, np.float32)
        self.show_fill = True
        self.show_grid = True
        self.show_thickness = False   # only meaningful once a map is loaded
        self.grid_spacing = 1.0     # world units between grid nodes (set each draw)
        self.fill_alpha = float(fill_alpha)
        self._shade = 1.0          # color multiplier (dimmed in light-background mode)
        # Wind buffer resolution divisor: the wind pass's clear + rasterization
        # cost is fragment-fill-rate bound (profiled), so rendering it at
        # 1/N resolution cuts that cost by N^2 at the price of fill-boundary
        # precision (blockier edges, up to ~N screen px) -- masked somewhat by
        # the crisp outline pass drawn on top, but a real, deliberate tradeoff,
        # not a free optimization. Swept N=2,3,4,8 on a real 33-layer/
        # 6M-vertex file: N=2 gave a 2.19x full-draw speedup with zero
        # pixels differing by a large (>60/255) amount from the reference and
        # no visible difference on close inspection; N=4 started showing
        # ~2k large-diff pixels (visible artifacts at fine features) for only
        # marginally more speedup (2.67x). Set to 1 to disable entirely.
        self.wind_downsample = 2

        maxl = str(self.n_layers)

        def vs(src):
            """Expand the shared TRANSFORM helper + the layer-array size."""
            return src.replace("TRANSFORM", _TRANSFORM).replace("MAXL", maxl)

        self.outline_prog = self._own(ctx.program(
            vertex_shader=vs(_VERT_OUTLINE), fragment_shader=_FRAG_COLOR))
        self.circ_prog = self._own(ctx.program(
            vertex_shader=vs(_VERT_CIRCLE), fragment_shader=_FRAG_COLOR))
        self.wind_prog = self._own(ctx.program(vertex_shader=vs(_VERT_WIND), fragment_shader=_FRAG_WIND))
        self.cover_prog = self._own(ctx.program(vertex_shader=_VERT_COVER, fragment_shader=_FRAG_COVER))
        self.grid_prog = self._own(ctx.program(vertex_shader=_VERT_COVER, fragment_shader=_FRAG_GRID))
        self.thick_prog = self._own(ctx.program(vertex_shader=_VERT_COVER, fragment_shader=_FRAG_THICK))
        self.sel_line_prog = self._own(ctx.program(vertex_shader=vs(_VERT_SEL), fragment_shader=_FRAG_SEL_LINE))
        self.sel_pt_prog = self._own(ctx.program(vertex_shader=vs(_VERT_SEL), fragment_shader=_FRAG_SEL_PT))
        for prog in (self.outline_prog, self.circ_prog):
            prog["u_color"].write(self.colors.tobytes())

        fs = np.array([-1, -1, 3, -1, -1, 3], np.float32)      # fullscreen triangle
        fs_buf = self._own(ctx.buffer(fs.tobytes()))
        self.cover_vao = self._own(ctx.vertex_array(self.cover_prog, [(fs_buf, "2f", "in_p")]))
        self.grid_vao = self._own(ctx.vertex_array(self.grid_prog, [(fs_buf, "2f", "in_p")]))
        self.thick_vao = self._own(ctx.vertex_array(self.thick_prog, [(fs_buf, "2f", "in_p")]))

        # Plasma LUT (256x1 RGB), sampled linearly by normalized thickness.
        lut = colormap_lut(256)
        self._lut_tex = self.ctx.texture((256, 1), 3, lut.tobytes(), dtype="f4")
        self._lut_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self._lut_tex.repeat_x = self._lut_tex.repeat_y = False
        self._thick_tex = None            # the gridded field (set by set_thickness)
        self._thick_bbox = None
        self._thick_vmin = 0.0            # colormap range (nm); retunable in-shader
        self._thick_vmax = 1.0
        self.thickness_map = None         # ThicknessMap kept for the legend / reload

        # Selected-polygon highlight (set by set_selection; rebuilt each pick).
        # Supports many polygons at once: their vertices are concatenated into one
        # buffer, edges drawn as indexed GL_LINES, vertices as GL_POINTS.
        self._sel_buf = self._sel_idx_buf = self._sel_line_vao = self._sel_pt_vao = None
        self._sel_n = 0                # total vertex count (POINTS)
        self._sel_line_count = 0       # edge-index count (GL_LINES)

        self._wind_tex = self._wind_fbo = None
        self._wind_size = None
        self._raw_pos = None
        self.fill_vao = None
        self._build_polylines(ctx, layout)
        self._build_fill(ctx, layout)
        self._build_circles(ctx, layout)

    # -- lifetime ---------------------------------------------------------
    def _own(self, obj):
        """Track a GL object (buffer/program/VAO) so release() can free it."""
        self._owned.append(obj)
        return obj

    def release(self) -> None:
        """Free every GL object this scene allocated. Call inside an active
        context before dropping the scene (e.g. on reload) — moderngl does not
        release GL resources on garbage collection by default."""
        if self._wind_fbo is not None:
            self._wind_fbo.release()
            self._wind_fbo = None
        if self._wind_tex is not None:
            self._wind_tex.release()
            self._wind_tex = None
        if self._thick_tex is not None:
            self._thick_tex.release()
            self._thick_tex = None
        if self._lut_tex is not None:
            self._lut_tex.release()
            self._lut_tex = None
        for obj in (self._sel_line_vao, self._sel_pt_vao, self._sel_buf, self._sel_idx_buf):
            if obj is not None:
                obj.release()
        self._sel_buf = self._sel_idx_buf = self._sel_line_vao = self._sel_pt_vao = None
        for obj in self._owned:
            try:
                obj.release()
            except Exception:       # noqa: BLE001 - best-effort teardown
                pass
        self._owned = []

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
        self._pos_buf = self._own(ctx.buffer(verts.tobytes()))
        self._lay_buf = self._own(ctx.buffer(vert_layer.tobytes()))

        # Outline edges as GL_LINES element indices: (i, next(i)), wrapping the
        # last vertex of a closed polyline back to its start.
        nxt = np.arange(1, n + 1, dtype=np.int64)
        last = start + count - 1
        nxt[last] = np.where((flags & 1).astype(bool), start, last)
        line_idx = np.empty((n, 2), np.uint32)
        line_idx[:, 0] = np.arange(n, dtype=np.uint32)
        line_idx[:, 1] = nxt.astype(np.uint32)
        self.line_vao = self._own(ctx.vertex_array(
            self.outline_prog,
            [(self._pos_buf, "2f", "in_pos"), (self._lay_buf, "1f", "in_layer")],
            index_buffer=self._own(ctx.buffer(line_idx.reshape(-1).tobytes())),
            index_element_size=4))

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

        self.fill_vao = self._own(ctx.vertex_array(
            self.wind_prog, [(self._pos_buf, "2f", "in_pos")],   # shared vertex buffer
            index_buffer=self._own(ctx.buffer(idx.tobytes())), index_element_size=4))

    def _build_circles(self, ctx, layout) -> None:
        circ = np.asarray(layout.circ, np.float32)
        self.n_circ = len(circ)
        self.circ_groups = []
        if self.n_circ == 0:
            return
        clayer = np.asarray(layout.circ_layer, np.float32)
        r = circ[:, 2]

        # Segment count is a per-draw uniform, so every circle in one draw call
        # pays the largest member's tessellation. A single batch is therefore
        # badly mismatched on real layouts: this file is 83,154 vias at r~0.53
        # plus 36 pads at r~104, and one batch would give all 83k the pads'
        # 256 segments (measured: 4.6 -> 12.7 ms at a 20 um view). Grouping by
        # radius octave (floor(log2 r)) keeps each batch within 2x, so the vias
        # get ~26 segments and only the 36 pads get 256. Octaves are sparse, so
        # this is typically 1-3 draw calls, and each is one contiguous buffer.
        octave = np.floor(np.log2(np.maximum(r, 1e-30))).astype(np.int64)
        order = np.argsort(octave, kind="stable")
        inst = np.empty((self.n_circ, 4), np.float32)
        inst[:, :3] = circ[order]
        inst[:, 3] = np.clip(clayer[order], 0, self.n_layers - 1)

        bounds = np.flatnonzero(np.diff(octave[order])) + 1
        # No unit-ring vertex buffer: the ring is generated from gl_VertexID in
        # _VERT_CIRCLE, so a VAO carries instance attributes only and the segment
        # count is a free per-frame uniform instead of buffer geometry. One VAO
        # per group serves both the fan and the loop draw (they differ only in
        # primitive type, vertex count, and the u_fan uniform).
        for lo, hi in zip(np.r_[0, bounds], np.r_[bounds, self.n_circ]):
            self.circ_groups.append(_CircleGroup(self, ctx, inst[lo:hi]))

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

    def set_thickness(self, tmap) -> None:
        """Upload a gridded :class:`~viewer.thickness.ThicknessMap` (or ``None`` to
        clear) as the background colormap field. Turns the overlay on when a map
        is set. Must run inside an active context (releases the previous field)."""
        if self._thick_tex is not None:
            self._thick_tex.release()
            self._thick_tex = None
        self.thickness_map = tmap
        self._thick_bbox = None
        if tmap is None:
            self.show_thickness = False
            return
        gh, gw, _ = tmap.field.shape
        tex = self.ctx.texture((gw, gh), 2, tmap.field.tobytes(), dtype="f4")
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = tex.repeat_y = False
        self._thick_tex = tex
        self._thick_bbox = tmap.bbox
        self._thick_vmin = float(tmap.vmin)
        self._thick_vmax = float(tmap.vmax)
        self.show_thickness = True

    def set_thickness_range(self, vmin: float, vmax: float) -> None:
        """Retune the colormap's low/high thickness (nm). Pure uniform state — the
        raw field texture is untouched, so this is instant."""
        self._thick_vmin = float(vmin)
        self._thick_vmax = float(vmax)

    def set_thickness_visible(self, on: bool) -> None:
        self.show_thickness = bool(on)

    def set_selection(self, polys) -> None:
        """Highlight the edges + vertices of zero or more polygons.

        ``polys`` is an iterable of ``(verts, closed)`` where ``verts`` is an
        (N, 2) world-space vertex array; pass ``None`` or an empty list to clear.
        Their vertices are concatenated into one buffer — edges drawn as indexed
        GL_LINES (each polygon's loop wrapped independently), vertices as
        GL_POINTS. Must run inside an active context (allocates GL buffers/VAOs,
        releasing the previous)."""
        for obj in (self._sel_line_vao, self._sel_pt_vao, self._sel_buf, self._sel_idx_buf):
            if obj is not None:
                obj.release()
        self._sel_buf = self._sel_idx_buf = self._sel_line_vao = self._sel_pt_vao = None
        self._sel_n = 0
        self._sel_line_count = 0
        if not polys:
            return
        all_v, all_e, off = [], [], 0
        for verts, closed in polys:
            v = np.ascontiguousarray(verts, np.float32)
            n = len(v)
            if n == 0:
                continue
            all_v.append(v)
            i = np.arange(n, dtype=np.uint32)
            if closed:
                e = np.stack([off + i, off + (i + 1) % n], axis=1)      # wrap the loop
            else:
                e = np.stack([off + i[:-1], off + i[1:]], axis=1)       # open strip
            all_e.append(e)
            off += n
        if not all_v:
            return
        pos = np.concatenate(all_v, axis=0)
        idx = np.concatenate(all_e, axis=0).reshape(-1)
        self._sel_n = len(pos)
        self._sel_line_count = len(idx)
        self._sel_buf = self.ctx.buffer(pos.tobytes())
        self._sel_idx_buf = self.ctx.buffer(idx.tobytes())
        self._sel_line_vao = self.ctx.vertex_array(
            self.sel_line_prog, [(self._sel_buf, "2f", "in_pos")],
            index_buffer=self._sel_idx_buf, index_element_size=4)
        self._sel_pt_vao = self.ctx.vertex_array(
            self.sel_pt_prog, [(self._sel_buf, "2f", "in_pos")])

    def has_selection(self) -> bool:
        return self._sel_line_vao is not None

    def set_shade(self, shade: float) -> None:
        """Multiply all layer colors by ``shade`` (used to darken for light bg)."""
        self._shade = float(shade)
        dimmed = (self.colors * self._shade).astype(np.float32)
        for prog in (self.outline_prog, self.circ_prog):
            prog["u_color"].write(dimmed.tobytes())

    def _ensure_wind(self, main_size) -> None:
        ds = max(int(self.wind_downsample), 1)
        size = (max(1, main_size[0] // ds), max(1, main_size[1] // ds))
        if self._wind_size == size:
            return
        if self._wind_fbo is not None:
            self._wind_fbo.release()
            self._wind_tex.release()
        self._wind_tex = self.ctx.texture(size, 1, dtype="f4")
        self._wind_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self._wind_fbo = self.ctx.framebuffer(color_attachments=[self._wind_tex])
        self._wind_size = size

    def _screen_scissor(self, lid, scale, origin, W, H):
        """Pixel-space (x, y, w, h) scissor rect (GL bottom-left origin) for
        layer ``lid``'s on-screen bbox, clamped to a ``W``x``H`` framebuffer
        (pass the wind buffer's own, possibly downsampled, size to get a
        scissor rect for it instead of the full-resolution main framebuffer),
        or None if it's entirely off-screen. clip = (world - origin) * scale
        (same transform the vertex shaders use, in f64 here); world/clip +y is
        up, matching GL window coordinates, so no axis flip is needed."""
        xmin, xmax = self._layer_xmin[lid], self._layer_xmax[lid]
        ymin, ymax = self._layer_ymin[lid], self._layer_ymax[lid]
        if xmin > xmax:                                    # no fillable geometry
            return None
        sx, sy = scale
        ox, oy = origin
        cx0, cx1 = (xmin - ox) * sx, (xmax - ox) * sx
        cy0, cy1 = (ymin - oy) * sy, (ymax - oy) * sy
        x0 = int(np.floor((cx0 + 1.0) * 0.5 * W)) - 1       # -1px margin: AA/winding can
        x1 = int(np.ceil((cx1 + 1.0) * 0.5 * W)) + 1        # touch a pixel just outside
        y0 = int(np.floor((cy0 + 1.0) * 0.5 * H)) - 1       # the exact transformed bbox
        y1 = int(np.ceil((cy1 + 1.0) * 0.5 * H)) + 1
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, W), min(y1, H)
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1 - x0, y1 - y0)

    def draw(self, main_fbo, scale, origin, grid_spacing=None) -> None:
        """Render into ``main_fbo`` (already bound + cleared by the caller).

        ``origin`` is the camera center in world units (``cam.cx, cam.cy``);
        everything is transformed relative to it — see _TRANSFORM for why.

        ``grid_spacing`` overrides the grid/scale-bar spacing (world units); pass
        the camera-derived value so it matches the on-screen scale bar exactly.
        """
        ctx = self.ctx
        scale = (float(scale[0]), float(scale[1]))
        origin = (float(origin[0]), float(origin[1]))
        # f32 head + f64 tail of the camera center; the shader subtracts them in
        # that order so the large cancellation happens exactly.
        hi = (float(np.float32(origin[0])), float(np.float32(origin[1])))
        lo = (origin[0] - hi[0], origin[1] - hi[1])
        for prog in (self.outline_prog, self.circ_prog):
            prog["u_scale"].value = scale
            prog["u_org_hi"].value = hi
            prog["u_org_lo"].value = lo
            prog["u_visible"].write(self.visible.tobytes())
        self.wind_prog["u_scale"].value = scale
        self.wind_prog["u_org_hi"].value = hi
        self.wind_prog["u_org_lo"].value = lo

        # Grid spacing (also drives the on-screen scale bar) — computed every frame.
        W, H = main_fbo.size
        upp = 2.0 / (scale[0] * W)
        self.grid_spacing = float(grid_spacing) if grid_spacing else _nice_spacing(upp * _GRID_TARGET_PX)

        # Zoom-adaptive circle tessellation, per radius group (see
        # _build_circles): the fewest segments keeping the chord sagitta under
        # _CIRCLE_TOL_PX for that group's largest circle. sagitta =
        # r*(1 - cos(pi/n)) px, so n = pi / acos(1 - tol/r_px). Zoomed out this
        # bottoms out at _CIRCLE_MIN_SEG (fewer vertices than the old fixed 64);
        # zoomed in it rises until the arcs are smooth.
        # ...then drop the instances outside the view, so a deep zoom doesn't
        # shade the whole group at the on-screen circle's segment count.
        half_w, half_h = 0.5 * W * upp, 0.5 * H * upp
        circ_draws = []
        for g in self.circ_groups:
            seg = _circle_segments(g.rmax / upp)
            vao, n = g.visible(origin, half_w, half_h, seg)
            if n:
                circ_draws.append((vao, n, seg))

        # Pass -1: film-thickness colormap (behind everything, incl. the grid).
        if self.show_thickness and self._thick_tex is not None:
            tp = self.thick_prog
            tp["u_scale"].value = scale
            tp["u_org_bb"].value = (origin[0] - self._thick_bbox[0],
                                    origin[1] - self._thick_bbox[1])
            tp["u_viewport"].value = (float(W), float(H))
            tp["u_bbmin"].value = (self._thick_bbox[0], self._thick_bbox[1])
            tp["u_bbmax"].value = (self._thick_bbox[2], self._thick_bbox[3])
            tp["u_field"].value = 0
            tp["u_lut"].value = 1
            tp["u_vmin"].value = self._thick_vmin
            tp["u_vmax"].value = self._thick_vmax
            tp["u_alpha"].value = 0.55 if self._shade >= 1.0 else 0.40
            self._thick_tex.use(0)
            self._lut_tex.use(1)
            main_fbo.use()
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.thick_vao.render(moderngl.TRIANGLES, vertices=3)
            ctx.disable(moderngl.BLEND)

        # Pass 0: background dot grid (behind all geometry).
        if self.show_grid:
            gp = self.grid_prog
            gp["u_scale"].value = scale
            # Reduce the center mod the spacing in f64 so the shader only ever
            # sees a small lattice phase, never a 7000-unit absolute coordinate.
            gp["u_org_mod"].value = (math.fmod(origin[0], self.grid_spacing),
                                     math.fmod(origin[1], self.grid_spacing))
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
        # layer's on-screen bbox. Fill is a triangle *fan* per polygon resolved
        # by the GPU winding rule + a single per-layer cover pass -- no
        # triangulation; the winding rule is what makes a concave polygon's
        # self-overlapping fan and two overlapping same-layer polygons each
        # blend exactly once per pixel. The wind pass's clear + rasterization
        # cost is fragment-fill-rate bound (profiled: ~1.85ms at 30k px vs
        # ~41ms at 5M px for the same geometry), so scissoring to each layer's
        # bbox and wind_downsample (see __init__, which trades fill-edge
        # precision for a ~N^2 cut to this pass's pixel count) are the levers.
        if self.show_fill and self.fill_vao is not None:
            self._ensure_wind(main_fbo.size)
            self.cover_prog["u_wind"].value = 0
            self.cover_prog["u_alpha"].value = self.fill_alpha
            self.cover_prog["u_downsample"].value = max(int(self.wind_downsample), 1)
            self._wind_tex.use(0)
            ctx.enable(moderngl.BLEND)
            W, H = main_fbo.size
            wW, wH = self._wind_fbo.size
            for lid in range(self.n_layers):
                cnt, off = int(self._fill_count[lid]), int(self._fill_off[lid])
                if cnt == 0 or self.visible[lid] < 0.5:
                    continue
                rect = self._screen_scissor(lid, scale, origin, W, H)
                if rect is None:
                    continue                                          # entirely off-screen
                # The wind buffer may be downsampled relative to main_fbo (see
                # wind_downsample), so it needs its own scissor rect computed
                # against its own (smaller) size, not main_fbo's.
                wind_rect = self._screen_scissor(lid, scale, origin, wW, wH) if (wW, wH) != (W, H) else rect
                if wind_rect is None:
                    continue
                self._wind_fbo.scissor = wind_rect
                self._wind_fbo.use()
                ctx.clear(0.0)
                ctx.blend_func = moderngl.ONE, moderngl.ONE          # accumulate winding
                self.fill_vao.render(moderngl.TRIANGLES, vertices=cnt, first=off)

                main_fbo.scissor = rect
                main_fbo.use()
                ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
                self.cover_prog["u_fill_color"].value = tuple(
                    float(c * self._shade) for c in self.colors[lid])
                self.cover_vao.render(moderngl.TRIANGLES, vertices=3)
            main_fbo.scissor = None        # restore full viewport for the passes below

        # Pass 1b: convex circle fills (plain alpha).
        if self.show_fill and circ_draws:
            main_fbo.use()
            ctx.enable(moderngl.BLEND)
            ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.circ_prog["u_alpha"].value = self.fill_alpha
            self.circ_prog["u_fan"].value = 1              # vertex 0 = fan center
            for vao, n, seg in circ_draws:
                self.circ_prog["u_seg"].value = seg
                vao.render(moderngl.TRIANGLE_FAN, vertices=seg + 2, instances=n)

        # Pass 2: opaque outlines on top.
        main_fbo.use()
        ctx.disable(moderngl.BLEND)
        self.outline_prog["u_alpha"].value = 1.0
        if self.line_vao is not None:
            self.line_vao.render(moderngl.LINES)
        if circ_draws:
            self.circ_prog["u_alpha"].value = 1.0
            self.circ_prog["u_fan"].value = 0             # no center vertex
            for vao, n, seg in circ_draws:
                self.circ_prog["u_seg"].value = seg
                vao.render(moderngl.LINE_LOOP, vertices=seg, instances=n)

        # Pass 3: selected-polygon highlight (amber edges + vertex dots) on top.
        if self._sel_line_vao is not None and self._sel_n > 0:
            hl = _SEL_COLOR_DARK if self._shade >= 1.0 else _SEL_COLOR_LIGHT
            for prog in (self.sel_line_prog, self.sel_pt_prog):
                prog["u_scale"].value = scale
                prog["u_org_hi"].value = hi
                prog["u_org_lo"].value = lo
                prog["u_color"].value = hl
            ctx.disable(moderngl.BLEND)
            self._sel_line_vao.render(moderngl.LINES, vertices=self._sel_line_count)
            self.sel_pt_prog["u_point_size"].value = _SEL_POINT_PX
            ctx.enable(moderngl.PROGRAM_POINT_SIZE)
            self._sel_pt_vao.render(moderngl.POINTS, vertices=self._sel_n)
            ctx.disable(moderngl.PROGRAM_POINT_SIZE)
