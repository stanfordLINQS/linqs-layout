<p align="center">
  <img src="packaging/icon.png" width="160" alt="LINQS Layout icon">
</p>

<h1 align="center">LINQS Layout</h1>

<p align="center">A lightning-fast viewer for large photonic-IC layouts (flattened DXF).</p>

![overview](docs/overview.png)

LINQS Layout opens giant flattened DXF layouts in a few hundred milliseconds and
renders the whole chip on the GPU — millions of polygons and circles — while
staying interactive. It has translucent layer fills, per-layer show/hide, and a
snapping measuring tool.

## Install

### Option A — download the app
1. Download **`LINQS-Layout.dmg`** from the [latest release](../../releases/latest).
2. Open it and drag **LINQS Layout** into **Applications**.
3. First launch: the app is not notarized, so right-click it → **Open** →
   **Open** (only needed once). If macOS says it's "damaged", clear the quarantine
   flag: `xattr -dr com.apple.quarantine "/Applications/LINQS Layout.app"`.

### Option B — build it yourself
Requires macOS 12+ on Apple Silicon, the Xcode command-line tools (`clang++`), and
Python 3.11+.

```bash
git clone https://github.com/stanfordLINQS/linqs-layout.git
cd linqs-layout
bash packaging/build_app.sh            # -> dist/LINQS Layout.app
bash packaging/make_dmg.sh             # optional: -> dist/LINQS-Layout.dmg
```

Then drag `dist/LINQS Layout.app` to `/Applications`.

## Using it

- **Open a layout** — double-click a `.dxf` (after the app is installed it's the
  default opener), or **File ▸ Open…**, or drag a `.dxf` onto the window.
- **Pan / zoom** — drag to pan, scroll to zoom at the cursor, **R** to reset.
- **Layers** — the right panel lists every layer; click to show/hide, or use
  **Show all / Hide all**.
- **Measure** — click **Measure** (or **M**), then click two points; the distance
  and Δx/Δy appear. Points snap to the nearest corner (vertex/center) or edge;
  hold **Shift** to constrain to horizontal/vertical. **Esc** clears.
- **Fill** (**F**) toggles translucent polygon fill; **Light bg** (**B**) toggles
  the background.

| input | action |
|---|---|
| scroll | zoom at cursor |
| drag | pan |
| `R` | reset view |
| click layer | show / hide |
| `M` | measure tool |
| `Shift` (measure) | constrain to H / V |
| `F` | toggle fill |
| `B` | toggle light/dark |
| `Esc` | clear measurement |

## Requirements

macOS 12+ on Apple Silicon. The bundled app is self-contained (no Python install
needed). Very large reference DXFs (hundreds of MB) load in well under a second.

## Development

This is the production branch. For the loader internals, renderer design, and
contributor docs, see [`main`](../../tree/main) (`CLAUDE.md`, `FEATURES.md`).
