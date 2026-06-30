# CLAUDE.md

Guidance for working in `linqs-layout`. Read this before editing.

## What this project is

Ultrafast tooling for **photonic integrated circuit layouts** in flattened DXF.
Two parts are done: **(1) an ultrafast DXF loader** that reads a large flattened
DXF and exposes its geometry as numpy, and **(2) a GPU layout viewer** built on
those arrays. Design-rule checks (DRC), by analogy to PCB/IC DRC, are future work.

## Architecture

```
dxfcore/dxf_parse.cpp   C++ core: mmap + single-pass parse -> Structure-of-Arrays.
                        C ABI (extern "C"); owns the parsed buffers via a DxfDoc*.
dxfcore/build.sh        clang++ -> libdxfcore.dylib
pydxf/loader.py         ctypes binding. Wraps the C buffers as ZERO-COPY numpy
                        views; DxfLayout keeps the handle alive, frees on close/GC.
pydxf/__init__.py       exports DxfLayout, load
inspect_dxf.py          CLI summarizer (counts, extent, per-layer table, --json)
view_dxf.py             CLI: interactive viewer / headless --png render
viewer/scene.py         moderngl renderer. Outlines: 1 GL_LINES batch + instanced
                        circle loops. Fill: NO triangulation — per-layer winding-
                        number fill (fan -> R32F additive -> cover pass) so concave
                        polygons fill correctly; ~70 ms single-core fan-index build,
                        real-time. Layer color+visibility in-shader. Context-agnostic.
viewer/camera.py        orthographic pan + zoom-at-cursor (world<->pixel mapping)
viewer/palette.py       distinct per-layer colors (vectorized HSV, no matplotlib)
viewer/offscreen.py     standalone-context render-to-PNG (headless; the render test)
viewer/qt_app.py        PySide6 window: QOpenGLWidget viewport + layer-panel sidebar
TOPO06.dxf              reference layout (220 MB; gitignored; not source — do not edit)
```

Data flows one way: C++ parses → fills `std::vector`s in `DxfDoc` → Python builds
numpy views over `.data()` pointers → the viewer copies those into GPU buffers
once. No per-entity Python objects.

## Commands

```bash
bash dxfcore/build.sh                       # rebuild native core after editing the .cpp
python3 inspect_dxf.py TOPO06.dxf           # human summary
python3 inspect_dxf.py TOPO06.dxf --json    # machine-readable
python3 view_dxf.py TOPO06.dxf              # interactive GPU viewer
python3 view_dxf.py TOPO06.dxf --png o.png  # headless render (works without a display)
```

There is no test runner yet; `inspect_dxf.py` on `TOPO06.dxf` is the smoke test.
Expected: ~290 ms parse, 163,447 polylines, 6,058,058 vertices, 83,190 circles,
33 layers, extent 14000×12000.

## Workflow

**Commit frequently.** After each self-contained, working increment, make a
small commit and push — the user strongly prefers many small commits over large
batched ones. Don't wait until a whole feature is done. Keep each commit
compiling and, where practical, verified (e.g. an offscreen render still works).

## Data model (specific to these files — verify before assuming it generalizes)

ASCII DXF R12 (`AC1009`): a strict stream of `(group-code line, value line)` pairs.

- Only `HEADER` + `ENTITIES` sections. **No BLOCKS/INSERT** → geometry is fully
  flattened; no block transforms to resolve.
- 2-D only: **no bulge (code 42), no Z (30), no per-vertex width**.
- Two primitives: `POLYLINE` (a `VERTEX` run ended by `SEQEND`; all are closed
  polygons here) and `CIRCLE` (center + radius, code 40).
- Relevant group codes: `0` entity type, `8` layer, `10`/`20` X/Y, `40` radius,
  `70` flags (bit0 = closed).

Structure-of-Arrays exposed by `DxfLayout`:

| array | shape | meaning |
|---|---|---|
| `verts` | (N,2) f64 | all polyline vertices concatenated |
| `poly_start`/`poly_count` | (P,) | CSR slice of `verts` per polyline |
| `poly_layer` | (P,) i32 | layer id per polyline |
| `poly_flags` | (P,) u8 | code-70 flags (bit0 = closed) |
| `circ` | (C,3) f64 | `[x,y,radius]` per circle |
| `circ_layer` | (C,) i32 | layer id per circle |
| `layers` | list[str] | names, indexed by layer id |

## Gotchas / conventions

- **Group codes are whitespace-padded** in much of the file (`  0` vs `0`). Always
  trim the code field — naive `code == "0"` matching silently undercounts (it
  hid ~5M vertices on the first pass). The C++ parser trims; preserve that.
- **Zero-copy lifetime:** numpy arrays alias C-owned memory. They are invalid
  after `DxfLayout.close()` / GC. For huge files use `with DxfLayout(path) as d:`
  or hold the object for as long as you use its arrays.
- After editing `dxf_parse.cpp` you **must** rerun `bash dxfcore/build.sh`; Python
  loads the prebuilt `.dylib`.
- Toolchain here: clang++ (C++17) is available; **Rust is not installed**. numpy
  2.4 and shapely 2.1 (incl. `STRtree`) are available for downstream DRC.
- Viewer stack: moderngl 5.12 (OpenGL **4.1 core** on Apple Silicon), PySide6,
  Pillow. The render core (`viewer/scene.py`) is context-agnostic, so it is tested
  **headlessly** via `moderngl.create_standalone_context(require=330)` rendering to
  an FBO — no window needed (`python3 -m viewer.offscreen TOPO06.dxf -o out.png`).
  The interactive window itself needs a real display and can't run under the Qt
  `offscreen` platform plugin. Layer show/hide is done in the vertex shader (a
  per-layer `u_visible` uniform), never by rebuilding the geometry buffers.
- Keep the design SoA + vectorized; avoid introducing per-entity Python objects in
  hot paths.

## Packaging & release (macOS app)

The viewer ships as a standalone `.app` (`app_main.py` → `viewer/app.py`, which
handles File▸Open, macOS "Open With" via `QFileOpenEvent`, drag-drop, and
multi-window). Everything lives in `packaging/`:

```bash
FRESH_VENV=1 bash packaging/build_app.sh   # -> dist/LINQS Layout.app
bash packaging/make_dmg.sh                  # -> dist/LINQS-Layout.dmg
```

- **Always build with `FRESH_VENV=1`.** The pyenv 3.13.1 env has the obsolete
  `pathlib` **backport** installed, which makes PyInstaller abort. The fresh venv
  also keeps the bundle small. (Permanent fix: `pip uninstall pathlib` in pyenv.)
- The spec (`packaging/LINQSLayout.spec`) collects `libdxfcore.dylib` into a
  `dxfcore/` dir beside the frozen modules; `pydxf/loader.py` resolves it via
  `sys._MEIPASS` when `sys.frozen`. Info.plist registers `.dxf` documents + icon.
- **Code signing on iCloud-synced folders (this repo is in iCloud Documents):**
  files get `com.apple.FinderInfo` / `com.apple.fileprovider.*` xattrs, and
  `codesign` rejects them with *"resource fork, Finder information, or similar
  detritus not allowed"*. `xattr -cr` can't fix it — the protected
  `com.apple.provenance` attr makes it bail. Workaround (build/dmg scripts do it):
  `ditto --noextattr --norsrc` a copy to `/tmp`, sign there, move back. The
  `dist/` copy re-acquires `FinderInfo` on iCloud (so `codesign --verify --strict`
  on it fails — harmless, it still runs); `make_dmg.sh` re-strips via `ditto`, so
  **the app *inside the DMG* is the clean, strict-valid artifact** — verify that
  one (mount the dmg), not the `dist/` copy.
- App is **ad-hoc signed** (`codesign -s -`) — required to launch on Apple
  Silicon, but unsigned/unnotarized, so Gatekeeper needs right-click → Open.
- **Branches:** `main` = dev (keeps CLAUDE.md, FEATURES.md, `view_topo06.py`,
  `docs/`). **`production`** = release: minimal chip-agnostic README, dev/chip
  files removed. Cut releases on `production` with the DMG via `gh release create`.

## Next steps toward DRC

Per-layer `shapely.STRtree` spatial index → width / spacing / enclosure / min-area
rules → inter-layer clearance rules → violation reporting with coordinates.
