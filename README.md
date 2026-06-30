<p align="center">
  <img src="packaging/icon.png" width="140" alt="LINQS Layout">
</p>

<h1 align="center">LINQS Layout</h1>

<p align="center">A fast desktop viewer for large DXF layouts.</p>

## Install

From the [latest release](../../releases/latest):

- **macOS** — download **LINQS-Layout.dmg**, open it, and drag **LINQS Layout**
  into your **Applications** folder. On first launch, right-click the app and
  choose **Open** to confirm it.
- **Windows** — download **LINQS-Layout-Setup-*.exe** and run it. It installs
  per-user (no admin prompt) and can update itself in place.

## Usage

Open a `.dxf` file with **File ▸ Open…**, by dragging it onto the window, or by
double-clicking it in your file manager.

Keyboard shortcuts are listed in the app under **File ▸ Keybindings**.

## Requirements

- **macOS** 12 or later (Apple Silicon).
- **Windows** 10/11 (x64), GPU with OpenGL 4.1 support (any modern NVIDIA / AMD /
  Intel GPU).

## Build from source

**macOS** (produces `dist/LINQS Layout.app` and a DMG):

```bash
bash packaging/build_app.sh        # -> dist/LINQS Layout.app
bash packaging/make_dmg.sh         # -> dist/LINQS-Layout.dmg
```

**Windows** — first build the native core, then the app + installer. Build the
DLL from a *x64 Native Tools Command Prompt for VS* (or with CMake), then run the
packager from any prompt with Python on PATH:

```bat
dxfcore\build.bat                  REM -> dxfcore\dxfcore.dll   (needs MSVC cl.exe)
packaging\build_win.bat            REM -> dist\LINQS Layout\ and the Setup .exe
```

`build_win.bat` runs PyInstaller (`packaging\LINQSLayout-win.spec`) and, if
[Inno Setup](https://jrsoftware.org/isinfo.php)'s `iscc` is on PATH, wraps the
result with `packaging\windows\installer.iss` into `dist\LINQS-Layout-Setup-*.exe`.
The native core also builds portably with CMake (`dxfcore/CMakeLists.txt`) on any
of the three platforms.
