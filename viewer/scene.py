"""Context-agnostic moderngl renderer for a DxfLayout.

Two passes, both batched and uploaded to the GPU once:

  * **Fill** — every closed polygon is triangulated (mapbox-earcut, cached to a
    sidecar file) into one indexed ``GL_TRIANGLES`` buffer; circles fill via one
    instanced triangle fan. Drawn first with alpha blending (translucent).
  * **Outline** — every polyline edge in one ``GL_LINES`` batch; every circle in
    one instanced ``GL_LINE_LOOP``. Drawn on top, opaque.

Each vertex / instance carries only a layer id; the vertex shader looks up that
layer's color and visibility from small uniform arrays, so showing/hiding a layer
(or recoloring it) is a uniform write with no buffer rebuild, and hidden geometry
is moved outside the clip volume in the shader rather than skipped on the CPU.

The same :class:`GLScene` drives both the interactive Qt widget and the headless
offscreen renderer; only the moderngl context and target framebuffer differ.
"""

from __future__ import annotations

import numpy as np

from .palette import layer_colors
from .triangulate import triangulate

_FRAG = """
#version 330
uniform float u_alpha;
in vec3 v_color;
out vec4 f_color;
void main() { f_color = vec4(v_color, u_alpha); }
"""

# Polyline outlines and polygon fills share this vertex shader (both feed a 2-D
# position + a layer id); only the primitive (LINES vs TRIANGLES) and u_alpha differ.
_VERT_POLY = """
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
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);   // outside clip -> culled
    else
        gl_Position = vec4(in_pos * u_scale + u_offset, 0.0, 1.0);
}
"""

# Circle outlines and circle fills share this one (template point + instance).
_VERT_CIRCLE = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_offset;
uniform vec3 u_color[MAXL];
uniform float u_visible[MAXL];
in vec2 in_unit;          // template point (line loop or triangle fan)
in vec3 in_circ;          // per-instance: cx, cy, r
in float in_clayer;       // per-instance layer id
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

_CIRCLE_SEGMENTS = 64
_DEFAULT_FILL_ALPHA = 0.22


class GLScene:
    """GPU geometry + shaders for one layout. Construct inside an active context.

    Pass ``defer_fill=True`` to skip the (slow, first-time) polygon triangulation
    so the window can open immediately; call :meth:`build_fill` later — with the
    GL context current — once the indices are ready (see ``viewer/triangulate.py``).
    """

    def __init__(self, ctx, layout, fill_alpha: float = _DEFAULT_FILL_ALPHA,
                 defer_fill: bool = False):
        self.ctx = ctx
        self.n_layers = max(layout.n_layers, 1)
        self.colors = layer_colors(self.n_layers)
        self.visible = np.ones(self.n_layers, np.float32)
        self.show_fill = True
        self.fill_alpha = float(fill_alpha)

        maxl = str(self.n_layers)
        self.poly_prog = ctx.program(
            vertex_shader=_VERT_POLY.replace("MAXL", maxl), fragment_shader=_FRAG)
        self.circ_prog = ctx.program(
            vertex_shader=_VERT_CIRCLE.replace("MAXL", maxl), fragment_shader=_FRAG)
        for prog in (self.poly_prog, self.circ_prog):
            prog["u_color"].write(self.colors.tobytes())

        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA

        self._vert_layer = None
        self._raw_pos = None
        self.fill_vao = None
        self._build_polylines(ctx, layout)
        if not defer_fill:
            self.build_fill(triangulate(layout))
        self._build_circles(ctx, layout)

    # -- geometry upload --------------------------------------------------
    def _build_polylines(self, ctx, layout) -> None:
        verts = np.ascontiguousarray(layout.verts, np.float32)   # (N, 2)
        n = len(verts)
        if n == 0:
            self.line_vao = None
            return
        start = np.asarray(layout.poly_start, np.int64)
        count = np.asarray(layout.poly_count, np.int64)
        layer = np.asarray(layout.poly_layer, np.int64)
        flags = np.asarray(layout.poly_flags, np.int64)

        # nxt[i] = vertex i connects to i+1, except the last vertex of a closed
        # polyline wraps to its start (open polylines form a zero-length segment).
        nxt = np.arange(1, n + 1, dtype=np.int64)
        last = start + count - 1
        closed = (flags & 1).astype(bool)
        nxt[last] = np.where(closed, start, last)

        seg = np.empty((n, 2, 2), np.float32)
        seg[:, 0, :] = verts
        seg[:, 1, :] = verts[nxt]
        seg = seg.reshape(-1, 2)                                 # (2N, 2)

        vert_layer = np.repeat(layer, count).astype(np.float32)  # (N,)
        np.clip(vert_layer, 0, self.n_layers - 1, out=vert_layer)
        self._vert_layer = vert_layer
        self._raw_pos = verts
        seg_layer = np.repeat(vert_layer, 2)                     # (2N,)

        pos_buf = ctx.buffer(seg.tobytes())
        lay_buf = ctx.buffer(seg_layer.tobytes())
        self.line_vao = ctx.vertex_array(
            self.poly_prog,
            [(pos_buf, "2f", "in_pos"), (lay_buf, "1f", "in_layer")])

    def build_fill(self, idx) -> None:
        """Upload the triangulated fill (``idx`` = triangle vertex indices into
        ``layout.verts``). Must run with this scene's GL context current."""
        self.fill_vao = None
        if self._vert_layer is None or idx is None or len(idx) == 0:
            return
        ctx = self.ctx
        raw_buf = ctx.buffer(self._raw_pos.tobytes())
        lay_buf = ctx.buffer(self._vert_layer.tobytes())
        ibo = ctx.buffer(np.ascontiguousarray(idx, np.uint32).tobytes())
        self.fill_vao = ctx.vertex_array(
            self.poly_prog,
            [(raw_buf, "2f", "in_pos"), (lay_buf, "1f", "in_layer")],
            index_buffer=ibo, index_element_size=4)

    def _build_circles(self, ctx, layout) -> None:
        circ = np.asarray(layout.circ, np.float32)               # (C, 3)
        self.n_circ = len(circ)
        if self.n_circ == 0:
            self.circ_loop_vao = self.circ_fan_vao = None
            return
        clayer = np.asarray(layout.circ_layer, np.float32)
        inst = np.empty((self.n_circ, 4), np.float32)
        inst[:, :3] = circ
        inst[:, 3] = np.clip(clayer, 0, self.n_layers - 1)
        inst_buf = ctx.buffer(inst.tobytes())

        th = np.linspace(0.0, 2.0 * np.pi, _CIRCLE_SEGMENTS, endpoint=False)
        ring = np.stack([np.cos(th), np.sin(th)], axis=1).astype(np.float32)
        # Outline: the ring as a line loop. Fill: a triangle fan (center + ring).
        fan = np.empty((_CIRCLE_SEGMENTS + 2, 2), np.float32)
        fan[0] = (0.0, 0.0)
        fan[1:_CIRCLE_SEGMENTS + 1] = ring
        fan[_CIRCLE_SEGMENTS + 1] = ring[0]
        self._fan_n = _CIRCLE_SEGMENTS + 2

        loop_buf = ctx.buffer(ring.tobytes())
        fan_buf = ctx.buffer(fan.tobytes())
        inst_fmt = (inst_buf, "3f 1f/i", "in_circ", "in_clayer")
        self.circ_loop_vao = ctx.vertex_array(
            self.circ_prog, [(loop_buf, "2f", "in_unit"), inst_fmt])
        self.circ_fan_vao = ctx.vertex_array(
            self.circ_prog, [(fan_buf, "2f", "in_unit"), inst_fmt])

    # -- per-frame state --------------------------------------------------
    def set_layer_visible(self, layer_id: int, visible: bool) -> None:
        if 0 <= layer_id < self.n_layers:
            self.visible[layer_id] = 1.0 if visible else 0.0

    def set_all_visible(self, visible: bool) -> None:
        self.visible[:] = 1.0 if visible else 0.0

    def toggle_fill(self) -> bool:
        self.show_fill = not self.show_fill
        return self.show_fill

    def draw(self, scale, offset) -> None:
        """Issue draw calls into the currently-bound framebuffer."""
        import moderngl
        for prog in (self.poly_prog, self.circ_prog):
            prog["u_scale"].value = (float(scale[0]), float(scale[1]))
            prog["u_offset"].value = (float(offset[0]), float(offset[1]))
            prog["u_visible"].write(self.visible.tobytes())

        # Pass 1: translucent fills.
        if self.show_fill and (self.fill_vao is not None or self.circ_fan_vao is not None):
            self.ctx.enable(moderngl.BLEND)
            self.poly_prog["u_alpha"].value = self.fill_alpha
            self.circ_prog["u_alpha"].value = self.fill_alpha
            if self.fill_vao is not None:
                self.fill_vao.render(moderngl.TRIANGLES)
            if self.circ_fan_vao is not None:
                self.circ_fan_vao.render(
                    moderngl.TRIANGLE_FAN, vertices=self._fan_n, instances=self.n_circ)
            self.ctx.disable(moderngl.BLEND)

        # Pass 2: opaque outlines on top.
        self.poly_prog["u_alpha"].value = 1.0
        self.circ_prog["u_alpha"].value = 1.0
        if self.line_vao is not None:
            self.line_vao.render(moderngl.LINES)
        if self.circ_loop_vao is not None:
            self.circ_loop_vao.render(
                moderngl.LINE_LOOP, vertices=_CIRCLE_SEGMENTS, instances=self.n_circ)
