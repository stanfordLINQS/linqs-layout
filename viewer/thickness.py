"""Optional film-thickness map overlay.

Loads a CSV of ``x, y, thickness(nm)`` triplets (measured points across a chip /
wafer, in the *same* world coordinates as the DXF) and resamples the scattered
points onto a regular grid via inverse-distance weighting (IDW). The grid is
uploaded once as a bilinear-filtered texture and drawn as a fullscreen colormap
pass behind the layout geometry (see ``GLScene``). No SciPy / matplotlib
dependency and no per-frame CPU work — the gridding happens once on load.

Coverage: cells with no measured point within a few nearest-neighbour spacings
are marked invalid so the colormap doesn't bleed across the whole bounding box
for a sparsely-sampled or non-rectangular map.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Longest grid side (texels). The GPU bilinearly upsamples this, so it only needs
# to resolve the measured field, not the screen — 256 is plenty and keeps the
# one-time IDW gridding fast even for a few thousand points.
_GRID_MAX = 256
_IDW_POWER = 2.0        # inverse-distance weighting exponent (1/d^power)

# Plasma colormap anchors (matplotlib's `plasma`, sampled at 0.0..1.0), lerped to
# a 256-entry LUT on load. Perceptually uniform; reads on dark and light canvases.
_PLASMA_ANCHORS = np.array([
    [0.050383, 0.029803, 0.527975],
    [0.287076, 0.010855, 0.627295],
    [0.417642, 0.000564, 0.658390],
    [0.562738, 0.051545, 0.641509],
    [0.692840, 0.165141, 0.564522],
    [0.798216, 0.280197, 0.469538],
    [0.881443, 0.392529, 0.383229],
    [0.949217, 0.517763, 0.295662],
    [0.988260, 0.652325, 0.211364],
    [0.988648, 0.809579, 0.145357],
    [0.940015, 0.975158, 0.131326],
], np.float32)


def colormap_lut(n: int = 256) -> np.ndarray:
    """(n, 3) float32 plasma LUT, linearly interpolated from the anchors."""
    xp = np.linspace(0.0, 1.0, len(_PLASMA_ANCHORS))
    x = np.linspace(0.0, 1.0, n)
    return np.stack([np.interp(x, xp, _PLASMA_ANCHORS[:, c]) for c in range(3)],
                    axis=1).astype(np.float32)


@dataclass
class ThicknessMap:
    """A gridded thickness field ready for GPU upload.

    ``field`` is (H, W, 2) float32: channel 0 = raw thickness in nm, channel 1 =
    coverage (1 valid, 0 invalid). The colormap normalization (``value ->
    [0,1]``) happens in the shader against adjustable ``u_vmin``/``u_vmax``
    uniforms, so the legend range can be retuned without re-gridding. Row 0
    is ``ymin`` so texture v maps world-y directly (no flip). ``bbox`` is
    ``(xmin, ymin, xmax, ymax)`` in world units; ``vmin``/``vmax`` are the data's
    raw thickness range in nm (the default legend range). ``n_points`` is the
    source point count.
    """

    field: np.ndarray
    bbox: tuple[float, float, float, float]
    vmin: float
    vmax: float
    n_points: int


def _read_xyz(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse a CSV of x,y,thickness rows -> (pts (N,2), values (N,)).

    Skips a header row if the first line isn't numeric, tolerates comment lines
    (``#``) and blank lines, and accepts comma or whitespace delimiters.
    """
    rows: list[tuple[float, float, float]] = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                x, y, t = float(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue                        # header or junk row -> skip
            rows.append((x, y, t))
    if not rows:
        raise ValueError(f"No x,y,thickness rows found in {path}")
    arr = np.asarray(rows, np.float64)
    return arr[:, :2], arr[:, 2]


def _median_nn(pts: np.ndarray) -> float:
    """Median nearest-neighbour spacing of the points (subsampled for large N)."""
    n = len(pts)
    if n < 2:
        return 1.0
    idx = np.arange(n)
    if n > 1500:                                # cap the O(n^2) probe
        idx = np.random.default_rng(0).choice(n, 1500, replace=False)
    probe = pts[idx]
    nn = np.empty(len(probe))
    for i in range(0, len(probe), 256):         # chunk to bound memory
        blk = probe[i:i + 256]
        d2 = ((blk[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
        np.put_along_axis(d2, np.argmin(d2, 1)[:, None], np.inf, 1)  # drop self
        nn[i:i + len(blk)] = np.sqrt(d2.min(1))
    m = float(np.median(nn))
    return m if m > 0 else 1.0


def load_thickness_csv(path: str) -> ThicknessMap:
    """Load and grid a thickness CSV into a :class:`ThicknessMap`."""
    pts, vals = _read_xyz(path)
    xmin, ymin = pts.min(0)
    xmax, ymax = pts.max(0)
    w = max(xmax - xmin, 1e-9)
    h = max(ymax - ymin, 1e-9)

    # Grid dimensions: longest side = _GRID_MAX, other by aspect (>= 2).
    if w >= h:
        gw = _GRID_MAX
        gh = max(int(round(_GRID_MAX * h / w)), 2)
    else:
        gh = _GRID_MAX
        gw = max(int(round(_GRID_MAX * w / h)), 2)

    gx = np.linspace(xmin, xmax, gw)
    gy = np.linspace(ymin, ymax, gh)           # row 0 = ymin (no v-flip on upload)
    GX, GY = np.meshgrid(gx, gy)               # (gh, gw)

    vmin, vmax = float(vals.min()), float(vals.max())

    # Coverage radius: mark a cell valid only if a measured point is within this
    # distance, so a non-rectangular / sparse map doesn't smear to the bbox edges.
    cover_r = 1.5 * _median_nn(pts)

    px, py = pts[:, 0], pts[:, 1]
    field = np.zeros((gh, gw, 2), np.float32)
    eps = (min(w / gw, h / gh) * 1e-3) ** 2    # avoid div-by-zero at exact hits
    for j in range(gh):                        # row-chunked IDW to bound memory
        d2 = (GX[j][:, None] - px[None, :]) ** 2 + (GY[j][:, None] - py[None, :]) ** 2
        wt = 1.0 / (d2 + eps) ** (_IDW_POWER / 2.0)
        val = (wt * vals[None, :]).sum(1) / wt.sum(1)
        field[j, :, 0] = val                       # raw nm; normalized in-shader
        field[j, :, 1] = (np.sqrt(d2.min(1)) <= cover_r).astype(np.float32)

    return ThicknessMap(field=field, bbox=(float(xmin), float(ymin), float(xmax),
                        float(ymax)), vmin=vmin, vmax=vmax, n_points=len(pts))
