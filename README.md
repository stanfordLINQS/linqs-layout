<p align="center">
  <img src="packaging/icon.png" width="140" alt="LINQS Layout">
</p>

<h1 align="center">LINQS Layout</h1>

<p align="center">A fast desktop viewer for large DXF layouts.</p>

## Install

Download **LINQS-Layout.dmg** from the [latest release](../../releases/latest),
open it, and drag **LINQS Layout** into your **Applications** folder.

On first launch, right-click the app and choose **Open** to confirm it.

## Usage

Open a `.dxf` file with **File ▸ Open…**, by dragging it onto the window, or by
double-clicking it in Finder.

Keyboard shortcuts are listed in the app under **File ▸ Keybindings**.

## Requirements

macOS 12 or later (Apple Silicon).

## Build from source

```bash
bash packaging/build_app.sh        # -> dist/LINQS Layout.app
bash packaging/make_dmg.sh         # -> dist/LINQS-Layout.dmg
```
