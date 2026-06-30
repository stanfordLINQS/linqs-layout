# CLAUDE.md

Guidance for working in `photonics-drc`. Read this before editing.

## What this project is

Design-rule-check (DRC) tooling for **photonic integrated circuit layouts**, by
analogy to PCB/IC DRC. **Step 1 (done): an ultrafast DXF loader** that reads a
large flattened DXF and exposes its geometry as numpy for analysis. DRC rule
checks themselves are future work.

## Architecture

```
dxfcore/dxf_parse.cpp   C++ core: mmap + single-pass parse -> Structure-of-Arrays.
                        C ABI (extern "C"); owns the parsed buffers via a DxfDoc*.
dxfcore/build.sh        clang++ -> libdxfcore.dylib
pydxf/loader.py         ctypes binding. Wraps the C buffers as ZERO-COPY numpy
                        views; DxfLayout keeps the handle alive, frees on close/GC.
pydxf/__init__.py       exports DxfLayout, load
inspect_dxf.py          CLI summarizer (counts, extent, per-layer table, --json)
TOPO06.dxf              reference layout (220 MB; not source — do not edit)
```

Data flows one way: C++ parses → fills `std::vector`s in `DxfDoc` → Python builds
numpy views over `.data()` pointers. No per-entity Python objects.

## Commands

```bash
bash dxfcore/build.sh                       # rebuild native core after editing the .cpp
python3 inspect_dxf.py TOPO06.dxf           # human summary
python3 inspect_dxf.py TOPO06.dxf --json    # machine-readable
```

There is no test runner yet; `inspect_dxf.py` on `TOPO06.dxf` is the smoke test.
Expected: ~290 ms parse, 163,447 polylines, 6,058,058 vertices, 83,190 circles,
33 layers, extent 14000×12000.

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
- Keep the design SoA + vectorized; avoid introducing per-entity Python objects in
  hot paths.

## Next steps toward DRC

Per-layer `shapely.STRtree` spatial index → width / spacing / enclosure / min-area
rules → inter-layer clearance rules → violation reporting with coordinates.
