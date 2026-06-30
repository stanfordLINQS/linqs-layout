"""Context-agnostic moderngl renderer for a DxfLayout.

All polyline outlines are flattened into one ``GL_LINES`` vertex buffer; all
circles are drawn with a single instanced ``GL_LINE_LOOP`` pass. Each vertex /
instance carries only a layer id; the vertex shader looks up that layer's color
and visibility from small uniform arrays, so:

  * recoloring or showing/hiding a layer never touches the big GPU buffers, and
  * hidden geometry is moved outside the clip volume in the shader (cheaply
    culled) rather than skipped on the CPU.

The same :class:`GLScene` drives both the interactive Qt widget and the headless
offscreen renderer; only the moderngl context and target framebuffer differ.
"""

from __future__ import annotations

import numpy as np

from .palette import layer_colors

_FRAG = """
#version 330
in vec3 v_color;
out vec4 f_color;
void main() { f_color = vec4(v_color, 1.0); }
"""

_VERT_LINES = """
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

_VERT_CIRCLES = """
#version 330
uniform vec2 u_scale;
uniform vec2 u_offset;
uniform vec3 u_color[MAXL];
uniform float u_visible[MAXL];
in vec2 in_unit;          // unit-circle template point
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


class GLScene:
    """GPU geometry + shaders for one layout. Construct inside an active context."""

    def __init__(self, ctx, layout):
        self.ctx = ctx
        self.n_layers = max(layout.n_layers, 1)
        self.colors = layer_colors(self.n_layers)
        self.visible = np.ones(self.n_layers, np.float32)

        maxl = str(self.n_layers)
        self.line_prog = ctx.program(
            vertex_shader=_VERT_LINES.replace("MAXL", maxl), fragment_shader=_FRAG)
        self.circ_prog = ctx.program(
            vertex_shader=_VERT_CIRCLES.replace("MAXL", maxl), fragment_shader=_FRAG)
        for prog in (self.line_prog, self.circ_prog):
            prog["u_color"].write(self.colors.tobytes())

        self._build_lines(ctx, layout)
        self._build_circles(ctx, layout)

    # -- geometry upload --------------------------------------------------
    def _build_lines(self, ctx, layout) -> None:
        verts = np.ascontiguousarray(layout.verts, np.float32)   # (N, 2)
        n = len(verts)
        self.n_line_verts = 2 * n
        if n == 0:
            self.line_vao = None
            return
        start = np.asarray(layout.poly_start, np.int64)
        count = np.asarray(layout.poly_count, np.int64)
        layer = np.asarray(layout.poly_layer, np.int64)
        flags = np.asarray(layout.poly_flags, np.int64)

        # nxt[i] = index of the vertex that vertex i connects to. Within a
        # polyline that's i+1; the last vertex wraps to the polyline start when
        # the polyline is closed, else forms a zero-length (invisible) segment.
        nxt = np.arange(1, n + 1, dtype=np.int64)
        last = start + count - 1
        closed = (flags & 1).astype(bool)
        nxt[last] = np.where(closed, start, last)

        seg = np.empty((n, 2, 2), np.float32)
        seg[:, 0, :] = verts
        seg[:, 1, :] = verts[nxt]
        seg = seg.reshape(-1, 2)                                 # (2N, 2)

        vert_layer = np.repeat(layer, count)                     # (N,)
        seg_layer = np.repeat(vert_layer, 2).astype(np.float32)  # (2N,)
        np.clip(seg_layer, 0, self.n_layers - 1, out=seg_layer)

        pos_buf = ctx.buffer(seg.tobytes())
        lay_buf = ctx.buffer(seg_layer.tobytes())
        self.line_vao = ctx.vertex_array(
            self.line_prog,
            [(pos_buf, "2f", "in_pos"), (lay_buf, "1f", "in_layer")])

    def _build_circles(self, ctx, layout) -> None:
        circ = np.asarray(layout.circ, np.float32)               # (C, 3)
        self.n_circ = len(circ)
        if self.n_circ == 0:
            self.circ_vao = None
            return
        clayer = np.asarray(layout.circ_layer, np.float32)
        inst = np.empty((self.n_circ, 4), np.float32)
        inst[:, :3] = circ
        inst[:, 3] = np.clip(clayer, 0, self.n_layers - 1)

        th = np.linspace(0.0, 2.0 * np.pi, _CIRCLE_SEGMENTS, endpoint=False)
        tmpl = np.stack([np.cos(th), np.sin(th)], axis=1).astype(np.float32)

        tmpl_buf = ctx.buffer(tmpl.tobytes())
        inst_buf = ctx.buffer(inst.tobytes())
        self.circ_vao = ctx.vertex_array(
            self.circ_prog,
            [(tmpl_buf, "2f", "in_unit"),
             (inst_buf, "3f 1f/i", "in_circ", "in_clayer")])

    # -- per-frame state --------------------------------------------------
    def set_layer_visible(self, layer_id: int, visible: bool) -> None:
        if 0 <= layer_id < self.n_layers:
            self.visible[layer_id] = 1.0 if visible else 0.0

    def set_all_visible(self, visible: bool) -> None:
        self.visible[:] = 1.0 if visible else 0.0

    def draw(self, scale, offset) -> None:
        """Issue draw calls into the currently-bound framebuffer."""
        import moderngl
        for prog in (self.line_prog, self.circ_prog):
            prog["u_scale"].value = (float(scale[0]), float(scale[1]))
            prog["u_offset"].value = (float(offset[0]), float(offset[1]))
            prog["u_visible"].write(self.visible.tobytes())
        if self.line_vao is not None:
            self.line_vao.render(moderngl.LINES)
        if self.circ_vao is not None:
            self.circ_vao.render(
                moderngl.LINE_LOOP, vertices=_CIRCLE_SEGMENTS, instances=self.n_circ)
