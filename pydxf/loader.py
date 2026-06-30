"""ctypes binding to the C++ DXF core, exposing a numpy Structure-of-Arrays view.

Memory model
------------
The C++ side owns the parsed buffers for the lifetime of the underlying ``DxfDoc``
handle. We build numpy arrays as *zero-copy views* over those buffers, so no large
data is duplicated. The handle is kept alive by the :class:`DxfLayout` object and
released in :meth:`DxfLayout.close` / ``__del__``. Do not use the arrays after the
layout is closed.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(os.path.dirname(_HERE), "dxfcore")


def _libname() -> str:
    if sys.platform == "darwin":
        return "libdxfcore.dylib"
    if sys.platform.startswith("win"):
        return "dxfcore.dll"
    return "libdxfcore.so"


def _load_lib() -> ctypes.CDLL:
    path = os.path.join(_LIB_DIR, _libname())
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"DXF core library not found at {path}.\n"
            f"Build it first:  bash {os.path.join(_LIB_DIR, 'build.sh')}"
        )
    lib = ctypes.CDLL(path)

    P = ctypes.c_void_p
    lib.dxf_load.restype = P
    lib.dxf_load.argtypes = [ctypes.c_char_p]
    lib.dxf_free.restype = None
    lib.dxf_free.argtypes = [P]

    for name in ("dxf_num_polylines", "dxf_num_vertices", "dxf_num_circles", "dxf_num_layers"):
        getattr(lib, name).restype = ctypes.c_int64
        getattr(lib, name).argtypes = [P]

    lib.dxf_verts.restype = ctypes.POINTER(ctypes.c_double)
    lib.dxf_verts.argtypes = [P]
    lib.dxf_poly_start.restype = ctypes.POINTER(ctypes.c_int64)
    lib.dxf_poly_start.argtypes = [P]
    lib.dxf_poly_count.restype = ctypes.POINTER(ctypes.c_int32)
    lib.dxf_poly_count.argtypes = [P]
    lib.dxf_poly_layer.restype = ctypes.POINTER(ctypes.c_int32)
    lib.dxf_poly_layer.argtypes = [P]
    lib.dxf_poly_flags.restype = ctypes.POINTER(ctypes.c_uint8)
    lib.dxf_poly_flags.argtypes = [P]
    lib.dxf_circ.restype = ctypes.POINTER(ctypes.c_double)
    lib.dxf_circ.argtypes = [P]
    lib.dxf_circ_layer.restype = ctypes.POINTER(ctypes.c_int32)
    lib.dxf_circ_layer.argtypes = [P]
    lib.dxf_layer_name.restype = ctypes.c_char_p
    lib.dxf_layer_name.argtypes = [P, ctypes.c_int64]
    return lib


_LIB = _load_lib()


def _view(ptr, count, ctype) -> np.ndarray:
    """Zero-copy numpy view over a C buffer of ``count`` elements of ``ctype``."""
    if count == 0:
        return np.empty(0, dtype=np.ctypeslib.as_ctypes_type(ctype))
    arr_type = ctype * count
    buf = arr_type.from_address(ctypes.cast(ptr, ctypes.c_void_p).value)
    return np.frombuffer(buf, dtype=ctype)


@dataclass
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    def __repr__(self) -> str:
        return (f"BBox(x=[{self.xmin:.3f}, {self.xmax:.3f}], "
                f"y=[{self.ymin:.3f}, {self.ymax:.3f}], "
                f"{self.width:.3f} x {self.height:.3f})")


class DxfLayout:
    """A parsed DXF layout exposed as numpy Structure-of-Arrays.

    Attributes
    ----------
    verts : (N, 2) float64
        All polyline vertices, concatenated. Polyline *i* owns the slice
        ``verts[poly_start[i] : poly_start[i] + poly_count[i]]``.
    poly_start, poly_count : (P,) int
        CSR-style offsets into ``verts`` for each polyline.
    poly_layer : (P,) int32        layer id of each polyline
    poly_flags : (P,) uint8        DXF code-70 flags (bit0 = closed)
    circ : (C, 3) float64          [x, y, radius] of each circle
    circ_layer : (C,) int32        layer id of each circle
    layers : list[str]             layer names, indexed by layer id
    """

    def __init__(self, path: str):
        self.path = path
        t0 = time.perf_counter()
        handle = _LIB.dxf_load(os.fsencode(path))
        if not handle:
            raise IOError(f"failed to parse DXF: {path}")
        self._handle = handle
        self.load_seconds = time.perf_counter() - t0

        n_poly = _LIB.dxf_num_polylines(handle)
        n_vert = _LIB.dxf_num_vertices(handle)
        n_circ = _LIB.dxf_num_circles(handle)
        n_layer = _LIB.dxf_num_layers(handle)

        self.verts = _view(_LIB.dxf_verts(handle), n_vert * 2, ctypes.c_double).reshape(-1, 2)
        self.poly_start = _view(_LIB.dxf_poly_start(handle), n_poly, ctypes.c_int64)
        self.poly_count = _view(_LIB.dxf_poly_count(handle), n_poly, ctypes.c_int32)
        self.poly_layer = _view(_LIB.dxf_poly_layer(handle), n_poly, ctypes.c_int32)
        self.poly_flags = _view(_LIB.dxf_poly_flags(handle), n_poly, ctypes.c_uint8)
        self.circ = _view(_LIB.dxf_circ(handle), n_circ * 3, ctypes.c_double).reshape(-1, 3)
        self.circ_layer = _view(_LIB.dxf_circ_layer(handle), n_circ, ctypes.c_int32)
        self.layers = [
            _LIB.dxf_layer_name(handle, i).decode("utf-8", "replace") for i in range(n_layer)
        ]

    # -- lifetime ---------------------------------------------------------
    def close(self) -> None:
        h = getattr(self, "_handle", None)
        if h:
            # Drop array views first; they alias C memory we are about to free.
            for a in ("verts", "poly_start", "poly_count", "poly_layer",
                      "poly_flags", "circ", "circ_layer"):
                setattr(self, a, None)
            _LIB.dxf_free(h)
            self._handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- counts -----------------------------------------------------------
    @property
    def n_polylines(self) -> int:
        return len(self.poly_start)

    @property
    def n_vertices(self) -> int:
        return len(self.verts)

    @property
    def n_circles(self) -> int:
        return len(self.circ)

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    # -- geometry queries -------------------------------------------------
    def polyline(self, i: int) -> np.ndarray:
        """Return the (n, 2) vertex array of polyline ``i`` (zero-copy slice)."""
        s = int(self.poly_start[i])
        return self.verts[s : s + int(self.poly_count[i])]

    def is_closed(self, i: int) -> bool:
        return bool(self.poly_flags[i] & 1)

    def bbox(self) -> BBox:
        """Overall bounding box across all geometry."""
        xs, ys = [], []
        if len(self.verts):
            xs.append(self.verts[:, 0].min()); xs.append(self.verts[:, 0].max())
            ys.append(self.verts[:, 1].min()); ys.append(self.verts[:, 1].max())
        if len(self.circ):
            cx, cy, cr = self.circ[:, 0], self.circ[:, 1], self.circ[:, 2]
            xs.append((cx - cr).min()); xs.append((cx + cr).max())
            ys.append((cy - cr).min()); ys.append((cy + cr).max())
        if not xs:
            return BBox(0, 0, 0, 0)
        return BBox(min(xs), min(ys), max(xs), max(ys))

    def layer_id(self, name: str) -> int:
        return self.layers.index(name)

    def layer_summary(self) -> list["LayerStats"]:
        """Per-layer counts and bounding boxes, sorted by total object count."""
        out = []
        # Vectorized per-layer vertex bbox via the polyline->layer map, expanded
        # to vertices, then reduced with np.add.reduceat-style grouping.
        pl = np.asarray(self.poly_layer, dtype=np.int64)
        cl = np.asarray(self.circ_layer, dtype=np.int64)
        # Expand polyline layer id to each of its vertices.
        if self.n_polylines:
            vert_layer = np.repeat(pl, np.asarray(self.poly_count, dtype=np.int64))
        else:
            vert_layer = np.empty(0, dtype=np.int64)

        for lid, name in enumerate(self.layers):
            pmask = pl == lid
            cmask = cl == lid
            n_poly = int(pmask.sum())
            n_circ = int(cmask.sum())
            xs, ys = [], []
            if n_poly:
                vmask = vert_layer == lid
                vx = self.verts[vmask, 0]; vy = self.verts[vmask, 1]
                if vx.size:
                    xs += [vx.min(), vx.max()]; ys += [vy.min(), vy.max()]
            if n_circ:
                c = self.circ[cmask]
                xs += [(c[:, 0] - c[:, 2]).min(), (c[:, 0] + c[:, 2]).max()]
                ys += [(c[:, 1] - c[:, 2]).min(), (c[:, 1] + c[:, 2]).max()]
            bb = BBox(min(xs), min(ys), max(xs), max(ys)) if xs else None
            out.append(LayerStats(lid, name, n_poly, n_circ, bb))
        out.sort(key=lambda s: s.n_poly + s.n_circ, reverse=True)
        return out


@dataclass
class LayerStats:
    layer_id: int
    name: str
    n_poly: int
    n_circ: int
    bbox: BBox | None

    @property
    def n_total(self) -> int:
        return self.n_poly + self.n_circ


def load(path: str) -> DxfLayout:
    """Parse ``path`` and return a :class:`DxfLayout`."""
    return DxfLayout(path)
