# Features

Status of `linqs-layout` capabilities. ✅ done · 🚧 in progress · ⬜ planned.

## Loader (`pydxf`, `inspect_dxf.py`)

- ✅ **Ultrafast DXF parse** — C++ mmap + single-pass parser exposes geometry as
  zero-copy numpy Structure-of-Arrays. ~290 ms for the 220 MB / 6 M-vertex
  reference file (warm).
- ✅ **Layout inspection** — `inspect_dxf.py` prints counts, extent, and a
  per-layer table (`--json` for machine-readable).

## Viewer (`view_dxf.py`, `view_topo06.py`, `viewer/`)

- ✅ **GPU rendering** — all polyline outlines in one `GL_LINES` batch + instanced
  circle loops, uploaded once; redrawn every frame (moderngl, OpenGL 4.1 core).
- ✅ **Translucent polygon fill** — concave-correct, with **no triangulation**:
  per-layer winding-number fill (triangle fans → R32F additive → cover pass).
  Only CPU prep is a vectorized fan index (~70 ms, single core). `F` toggles it.
- ✅ **Pan & zoom** — scroll wheel zooms centered on the cursor; left-drag pans;
  `R` resets to fit.
- ✅ **Layer panel** — right-hand list; click a layer to show/hide; Show all /
  Hide all. Color/visibility resolved in-shader (toggle = one uniform write).
- ✅ **Measuring tool** (`M`) — click two points to read distance + Δx/Δy. A live
  snap indicator follows the cursor (square = corner, circle = edge); each point
  **snaps to the nearest DXF corner** (vertex/center), falling back to the nearest
  point on an **edge**. Hold **Shift** to constrain the second point to horizontal/
  vertical. Anchored in world space (tracks through pan/zoom); `Esc` clears.
- ✅ **Background dot grid** (`G`) — procedural CAD-style dot grid behind the
  geometry; spacing snaps to nice 1/2/5 values as you zoom.
- ✅ **Scale bar** — bottom-left, one grid cell long, labeled in µm/mm; tracks the
  grid spacing through zoom.
- ✅ **Status bar** — bottom strip with live cursor x/y, scale, layer count, and
  filename.
- ✅ **Brutalist UI** — one monospace face, hairline rules, monochrome chrome, and
  a single amber accent (tokens + QSS in `viewer/style.py`); the layout geometry is
  the only saturated color. Measurements read to 1 nm (3-decimal µm).
- ✅ **Light / dark background** (`B`) — toggles background; layer colors dim on
  light for contrast.
- ✅ **Headless render** — `view_dxf.py --png` / `viewer/offscreen.py` render to a
  PNG with no display (also the render smoke test).
- ✅ **One-click launch** — `view_topo06.py` opens the viewer on TOPO06 via the ▶
  Run button, no arguments.
- ✅ **Welcome screen** — launching the app with no file shows a start screen
  (icon + "Press ⌘O to open a DXF file") with Open dialog and drag-drop.

## Keyboard / mouse

| input | action |
|---|---|
| scroll | zoom in/out at cursor |
| left-drag | pan |
| click layer row | show / hide layer |
| `M` | toggle measuring tool |
| click ×2 (measure) | place two snapped points → distance |
| `Shift` (measure) | constrain 2nd point to horizontal / vertical |
| `Esc` | clear measurement |
| `F` | toggle polygon fill |
| `G` | toggle background grid |
| `B` | toggle light / dark background |
| `R` | reset view to fit |

## Planned

- ⬜ **Tabs** — opening more than one file opens each in its own tab within a
  single window (instead of a separate window per file), so multiple layouts are
  open at once and you can switch between them.
- ⬜ **Instant startup** — cache parsed geometry to a binary sidecar so launches
  skip re-parsing the 220 MB DXF.
- ⬜ **DRC** — per-layer `shapely.STRtree` spatial index → width / spacing /
  enclosure / min-area rules → violation reporting.
- ⬜ **Measurement options** — edge/midpoint snapping; persistent multi-segment
  measurements; unit labels.
