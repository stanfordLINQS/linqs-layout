"""Polygon triangulation for the translucent fill pass.

Kept in its own module so the worker function is importable by spawned
processes. Three speed tricks, in order of impact:

  * **cache** — the triangle index array is saved next to the DXF as
    ``<path>.trifill.npz`` (keyed on vertex count + mtime). Reloading it is ~15 ms
    vs. ~3.3 s to retriangulate, so every launch after the first is instant.
  * **parallel** — the first build splits the polygons across processes that
    share the vertex buffer via shared memory (~3× on an 8-core box).
  * **off-thread** — the interactive viewer runs all of this in a background
    thread, so the window opens immediately and fills appear when ready.
"""

from __future__ import annotations

import os

import numpy as np


def _tri_range(verts: np.ndarray, starts, counts) -> np.ndarray:
    """Triangulate polygons ``starts[i]:starts[i]+counts[i]`` -> global indices."""
    import mapbox_earcut as earcut

    chunks = []
    one = np.empty(1, np.uint32)
    for s, c in zip(starts, counts):
        if c < 3:
            continue
        one[0] = c
        try:
            idx = earcut.triangulate_float64(verts[s:s + c], one)
        except Exception:
            continue                         # un-fillable polygon -> outline only
        if len(idx):
            chunks.append(idx.astype(np.uint32) + np.uint32(s))
    return np.concatenate(chunks) if chunks else np.zeros(0, np.uint32)


def _worker(args):
    """Process-pool entry: attach the shared vertex buffer, triangulate a slice."""
    from multiprocessing import shared_memory

    shm_name, n, starts, counts = args
    shm = shared_memory.SharedMemory(name=shm_name)
    try:
        verts = np.ndarray((n, 2), dtype=np.float64, buffer=shm.buf)
        return _tri_range(verts, starts, counts)
    finally:
        shm.close()


def _cache_path(layout) -> str:
    return layout.path + ".trifill.npz"


def _sig(layout) -> np.ndarray:
    return np.array([layout.n_vertices, int(os.path.getmtime(layout.path))], np.int64)


def load_cache(layout):
    path = _cache_path(layout)
    if os.path.exists(path):
        try:
            z = np.load(path)
            if z["sig"].tolist() == _sig(layout).tolist():
                return z["idx"]
        except Exception:
            pass
    return None


def save_cache(layout, idx) -> None:
    try:
        np.savez(_cache_path(layout), idx=idx, sig=_sig(layout))
    except Exception:
        pass


def _parallel(verts, n, start, count, workers) -> np.ndarray:
    import multiprocessing as mp
    from multiprocessing import shared_memory

    K = max(1, min(workers, mp.cpu_count()))
    ctx = mp.get_context("spawn")
    shm = shared_memory.SharedMemory(create=True, size=verts.nbytes)
    try:
        np.ndarray((n, 2), np.float64, buffer=shm.buf)[:] = verts
        P = len(start)
        b = np.linspace(0, P, K + 1).astype(int)
        tasks = [(shm.name, n, start[b[i]:b[i + 1]], count[b[i]:b[i + 1]])
                 for i in range(K)]
        with ctx.Pool(K) as pool:
            parts = pool.map(_worker, tasks)
        return np.concatenate(parts) if parts else np.zeros(0, np.uint32)
    finally:
        shm.close()
        try:
            shm.unlink()
        except Exception:
            pass


def triangulate(layout, parallel: bool = True, workers: int = 8,
                use_cache: bool = True) -> np.ndarray:
    """Return uint32 triangle vertex indices into ``layout.verts`` (cached)."""
    if use_cache:
        cached = load_cache(layout)
        if cached is not None:
            return cached

    verts = np.ascontiguousarray(layout.verts, np.float64)
    n = len(verts)
    start = np.asarray(layout.poly_start, np.int64).tolist()
    count = np.asarray(layout.poly_count, np.int64).tolist()

    if parallel and len(start) >= 5000:
        try:
            idx = _parallel(verts, n, start, count, workers)
        except Exception:
            idx = _tri_range(verts, start, count)   # graceful fallback
    else:
        idx = _tri_range(verts, start, count)

    if use_cache:
        save_cache(layout, idx)
    return idx
