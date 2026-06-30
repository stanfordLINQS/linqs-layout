"""Distinct per-layer colors, generated vectorized (no matplotlib at runtime)."""

from __future__ import annotations

import numpy as np


def _hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    h6 = (h % 1.0) * 6.0
    i = np.floor(h6).astype(np.int64) % 6
    f = h6 - np.floor(h6)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def layer_colors(n: int) -> np.ndarray:
    """Return ``(n, 3)`` float32 RGB in [0, 1], visually distinct per layer.

    Hues are spaced by the golden ratio so neighbouring layer ids stay far apart
    on the color wheel; saturation/value are nudged in a short cycle so adjacent
    hues that happen to land close still differ in tone.
    """
    if n <= 0:
        return np.zeros((0, 3), np.float32)
    i = np.arange(n)
    h = (i * 0.6180339887498949) % 1.0
    s = 0.55 + 0.15 * (i % 3)          # 0.55 / 0.70 / 0.85
    v = 1.0 - 0.12 * (i % 2)           # 1.00 / 0.88
    return _hsv_to_rgb(h, s, v).astype(np.float32)
