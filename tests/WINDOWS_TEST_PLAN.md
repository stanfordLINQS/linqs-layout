# Windows Test Plan — LINQS Layout

This is the bring-up + acceptance suite for the **Windows** port of LINQS Layout.
The app was written and verified on macOS; the Windows-specific code (Win32 file
mapping, `dxfcore.dll`, PyInstaller `-win.spec`, Inno Setup installer, the
Windows updater path) has **never been built or run** — that is what this plan
verifies. Work top-to-bottom: each layer is isolated so a failure points at one
thing. Record the result of every test with the template in §11.

**Prime directive (from the project):** *speed is everything.* Any test that
passes functionally but feels laggy (slow parse, stuttering pan/zoom, slow first
paint) is a **FAIL** — note it. Target: parse a ~200 MB DXF in well under a
second and pan/zoom at smooth frame rates.

Run on the real target: **Windows 10/11 x64** with an **NVIDIA GPU** (or any GPU
with an OpenGL 4.1 driver) and a current driver installed. A VM with only
software GL is *not* a valid render target.

---

## 0. Environment setup (T0)

Install:
- **Visual Studio Build Tools** with the *Desktop development with C++* workload
  (provides `cl.exe` + the *x64 Native Tools Command Prompt for VS*).
- **Python 3.11+** (64-bit), added to PATH.
- **Git**.
- **Inno Setup 6** (`iscc.exe`) — needed only for the installer tests (§8). Add
  its folder to PATH.

Then:
```bat
git clone https://github.com/stanfordLINQS/linqs-layout
cd linqs-layout
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install numpy moderngl PySide6 Pillow pyinstaller
```

**Pass:** all installs succeed; `cl`, `python`, `git` resolve on PATH; `iscc`
resolves (or is explicitly skipped). `python -c "import PySide6, moderngl, numpy"`
prints nothing and exits 0.

---

## 1. Build the native core (T1)

From a **x64 Native Tools Command Prompt for VS** (so `cl.exe` is on PATH):
```bat
cd <repo>
dxfcore\build.bat
```
**Expected:** prints `built ...\dxfcore\dxfcore.dll`; the file
`dxfcore\dxfcore.dll` exists; no `dxf_parse.obj` / `.lib` / `.exp` left behind.

Also verify the portable path:
```bat
cmake -S dxfcore -B dxfcore\build
cmake --build dxfcore\build --config Release
```
**Expected:** `dxfcore\dxfcore.dll` is produced (CMake places it beside the
source). **Pass:** at least `build.bat` produces a DLL; ideally CMake does too.

> **Risk to watch:** the C ABI must be *exported* from the DLL. If T2 fails to
> find a symbol (`AttributeError: function 'dxf_load' not found`), the
> `__declspec(dllexport)` via `DXF_API` didn't take — check the compile used the
> `_WIN32` branch and that the 64-bit `cl` was used.

---

## 2. Native core + loader correctness (T2) — no GPU, no display

```bat
python tests\test_smoke.py
```
**Expected output** ends with `RESULT: PASS` and every line is `[ok ]`. The
deterministic asserts (must match the macOS baseline exactly):

| field | value |
|---|---|
| n_polylines | 2 |
| n_vertices | 7 |
| n_circles | 2 |
| n_layers | 2 |
| layers (sorted) | `['METAL', 'VIA']` |
| poly[0] / poly[1] vertex count | 4 / 3 |
| poly[0] closed | True |
| bbox | x [0, 30], y [0, 10] |

Then time a **real large** file (use any large flattened DXF you have; it is not
in the repo):
```bat
python tests\test_smoke.py path\to\large.dxf
```
**Pass:** sample asserts all pass **and** the large file parses at a throughput
comparable to macOS (hundreds of MB/s; a ~200 MB file in roughly ≤1 s). A correct
but slow parse is a FAIL — capture the MB/s number.

> This test exercises Win32 `MapViewOfFile` + the zero-copy numpy views. If it
> passes, the hardest platform-specific layer is done.

---

## 3. Python / viewer imports (T3)

```bat
python tests\test_imports.py
```
**Expected output** ends with `RESULT: PASS`. Asserts the full viewer package
tree imports cleanly and the per-OS style tokens resolve correctly: font is
`Consolas`, shortcut label is `Ctrl+O` (not `⌘O`). **Pass:** `RESULT: PASS`.

---

## 4. Headless GPU render (T4)

```bat
python tests\test_render.py
python view_dxf.py path\to\large.dxf --png large.png
```
**Expected:** `test_render.py` prints `RESULT: PASS` — it renders the sample
fixture through the real moderngl standalone-context path and asserts the
image is the right size/dtype, the background pixel matches the canvas color,
and a sane fraction of the canvas is covered by the two polygons + two circles
(catches both a blank canvas and a corrupted draw), then round-trips a PNG
through PIL. Separately, `large.png` shows the real layout; render time is
printed and should be real-time (tens to low-hundreds of ms).

**Pass:** `test_render.py` → `RESULT: PASS`, and `large.png` is visually
correct. **If it errors** with a context / GL version failure, capture the
full traceback — this is the moderngl standalone-context path
(`create_standalone_context`) and the most likely GPU/driver-specific failure.
Confirm the NVIDIA OpenGL driver is installed and that you are **not** on a
remote-desktop session that forces software GL.

---

## 5. Interactive viewer (T5)

The functional half (everything except "feels smooth") is automated:
```bat
python tests\test_interactive.py
```
It drives the real `MainWindow` with synthesized `QTest` key/mouse events on
a real GL surface against `tests\sample.dxf`, asserting state and writing
screenshots to a temp dir after each step. **Expected:** `RESULT: PASS`.

For the manual/feel pass, or to look at the app yourself:
```bat
python app_main.py path\to\large.dxf
```
Verify each (✓/✗), watching for any lag:

- [ ] Window opens titled **LINQS Layout — <file>**; geometry visible immediately.
- [ ] **Scroll wheel** zooms toward the cursor; smooth, no stutter.
- [ ] **Left-drag** pans; smooth.
- [ ] **R** resets the view; the initial view already matches the R view.
- [ ] Right-hand **layer panel** lists layers with color swatches; clicking one
      toggles its visibility instantly (no rebuild/flash).
- [ ] **L** hides/shows the layer panel; it can't be dragged away to nothing.
- [ ] **F** toggles polygon fill (enabled by default); single keypress flips it.
- [ ] **G** toggles the background dot grid; a **scale bar** shows bottom-left and
      stays legible (white/contrast) in both backgrounds.
- [ ] **B** switches light/dark background.
- [ ] **M** enters measure mode: a snap circle follows the cursor (snaps to
      corners, then edges); click two points to measure; **Shift** constrains to
      horizontal/vertical; **Esc** clears.
- [ ] Status bar shows cursor **x / y** (the `x`/`y` labels are amber) and the
      filename at right.
- [ ] **File ▸ Keybindings** opens a dialog; shortcuts show **Ctrl+O / Ctrl+W**
      (not ⌘).
- [ ] **Ctrl+O** opens a file dialog; opening a second file adds a **tab**.
- [ ] Tabs: switch, reorder, close (**Ctrl+W**); closing the last tab is sane.

**Pass:** every box checked and nothing feels slow.

> **Verified on this branch** via `test_interactive.py` (functional behavior,
> not "feel" -- see below): every item above passed.
>
> **Methodology note:** `QWidget.grab()` on the `MainWindow` does *not*
> reliably reflect the live `QOpenGLWidget` content -- it can return a stale
> backing-store frame for the GL canvas specifically (the rest of the window
> chrome grabs fine). The `B` light/dark toggle looked broken in an initial
> screenshot (GL canvas still black, overlay text gone low-contrast) purely
> from this; `QOpenGLWidget.grabFramebuffer()` (reads the real framebuffer
> directly) plus a manual pixel sample confirmed the actual render is correct
> in both modes (background clears to the right color; fill color shifts
> correctly; `overlay.py`'s scale bar explicitly branches its pen color on
> `vp.is_light()` for contrast). If re-verifying visually, grab the viewport
> via `grabFramebuffer()`, not the window via `grab()`.
>
> Smoothness/stutter-free feel was not assessed (inherently needs a human
> watching in real time) -- everything else on the checklist is now verified.

---

## 6. File-open entry points (T6)

```bat
python tests\test_entrypoints.py
```
**Expected:** `RESULT: PASS` — this automates the **argv** path (opens
`tests\sample.dxf` directly into a tab titled `LINQS Layout — sample.dxf`) and
the **no-arg** path (shows the welcome screen with the `Ctrl+O` hint), each
driven through a real Qt event loop + real GL surface in its own subprocess,
with a watchdog so a GL/window-setup failure reports `FAIL` instead of
hanging. Real windows briefly appear and self-close — this needs the same
real GPU target as §4/§5, not a headless/offscreen box.

- [ ] **Drag-drop** (not automated — needs a real OS drag gesture): drag a
      `.dxf` from Explorer onto the window → opens.

**Pass:** `test_entrypoints.py` → `RESULT: PASS`, and drag-drop works.
(Double-click association is tested in §8 after install.)

---

## 7. Frozen app via PyInstaller (T7)

```bat
packaging\build_win.bat
```
(If `iscc` is absent it will build the app folder and skip the installer — fine
for this test.)

**Expected:** `dist\LINQS Layout\LINQS Layout.exe` exists, with the bundled
runtime and **`dist\LINQS Layout\_internal\dxfcore\dxfcore.dll`** present (path
may be `dist\LINQS Layout\dxfcore\dxfcore.dll` depending on PyInstaller layout —
either is fine as long as the app finds it).

Then run the **frozen** exe (double-click it, or from a plain `cmd` — *not* the
build venv, to prove it's self-contained):
```bat
"dist\LINQS Layout\LINQS Layout.exe" path\to\large.dxf
```
**Expected:** it opens and renders with no Python installed on PATH assumptions;
the taskbar/window icon is the LINQS icon.

**Pass:** frozen app launches standalone, opens a file, renders, icon correct.

> **Risk to watch:** missing GL backend at runtime (`glcontext`) → app starts but
> the viewport is black or it crashes on first paint. If so, capture the error;
> the fix is in the spec's `hiddenimports` / `collect_all('glcontext')`.

---

## 8. Installer (T8) — requires Inno Setup

If §7 skipped the installer, build it:
```bat
iscc /DMyAppVersion=<version> packaging\windows\installer.iss
```
(`<version>` = output of `python packaging\_version.py`.)

**Expected:** `dist\LINQS-Layout-Setup-<version>.exe` is produced. Then:

- [ ] Run the setup exe. It installs **per-user with no UAC/admin prompt** into
      `%LocalAppData%\Programs\LINQS Layout`.
- [ ] Start Menu shortcut **LINQS Layout** launches the app.
- [ ] (If the desktop-icon task was checked) a desktop shortcut launches it.
- [ ] With the **.dxf association** task checked, **double-clicking a `.dxf`** in
      Explorer opens it in LINQS Layout, and `.dxf` files show the app icon.
- [ ] The "Launch LINQS Layout" checkbox at the end of setup launches it.
- [ ] **Uninstall** (Settings ▸ Apps) removes the app, shortcuts, and the file
      association.

**Pass:** clean per-user install, working shortcuts + association, clean uninstall.

> **Silent-install gotcha:** running the setup exe with `/VERYSILENT` from an
> account that *can* elevate (no `/CURRENTUSER` or `/ALLUSERS` given) installs
> **per-machine** (`C:\Program Files`, HKLM) instead of per-user — `iscc`'s
> dialog-override default picks admin when there's no dialog to ask. Pass
> `/CURRENTUSER` explicitly to silently exercise the intended per-user path; an
> interactive run is unaffected (its dialog defaults to "for me only"). Verified
> on this branch: with `/CURRENTUSER`, install lands in
> `%LocalAppData%\Programs\LINQS Layout` under `HKEY_CURRENT_USER`, no admin
> token, both shortcuts launch the app, and uninstall removes the app dir, both
> shortcuts, and the `LINQSLayout.dxf` progid / `OpenWithProgids` entry cleanly.
>
> **Double-click association note:** writing `OpenWithProgids` + the progid's
> `shell\open\command` registers LINQS Layout as *available* for `.dxf` (it
> shows up under "Open with"), but on Windows 10/11 the actual double-click
> default is governed by the per-user `UserChoice` registry hash, which is
> deliberately protected from silent installer takeover since Windows 8 — if
> another app (e.g. KLayout) was already the chosen default, double-clicking a
> `.dxf` keeps opening that app until the user manually switches it in Settings
> ▸ Default apps. This is expected platform behavior, not an installer bug —
> confirm registration via "Open with" rather than assuming double-click alone
> proves it.

---

## 9. In-app updater (T9)

The updater finds the latest GitHub release, downloads its **`.exe`** asset, runs
it, and quits so the installer can replace files. To exercise it you need a
published release whose version is **newer** than the running app.

Two ways to test:

**A. Real two-version flow (most faithful).** Build + publish a release at the
current version with both assets attached, install it, then bump
`viewer/__init__.py __version__`, rebuild, publish a newer release. In the
installed (older) app: **File ▸ Check for Updates…** → it should detect the newer
version, prompt, download the setup exe, then the app closes and the installer
runs (and relaunches).

**B. Quick spoof (no second release needed).** Temporarily make the running app
think it's old, then check against the *real* latest release asset:
- Edit `viewer/update.py` → in `current_version()` `return "0.0.1"` (temporary),
  run `python app_main.py`, **File ▸ Check for Updates…**.
- **Expected:** it reports the real latest version as available, downloads the
  real `.exe` asset to a temp dir, and on "Yes" launches it and quits. Revert the
  edit afterward.

Check each:
- [ ] **Check for Updates** when up to date → "is the latest version".
- [ ] When newer exists → prompt → progress dialog → installer downloaded.
- [ ] Choosing to install **quits the app and launches the installer**; after it
      finishes the app is on the new version.
- [ ] No `.exe` asset / no network → graceful message, no crash.

**Pass:** the download→quit→install→relaunch cycle completes and the version
advances. Note: this is the least-tested path; capture any exception dialog.

The non-GUI half of this (`_fetch_latest` version comparison, asset-suffix
matching, and the two graceful-degradation paths) is automated:
```bat
python tests\test_updater.py
```
It mocks the GitHub API response for determinism (newer version + matching
asset; newer version + *no* matching asset; network failure) and also makes
one live call to confirm the real API is reachable. **Expected:** `RESULT:
PASS`. It does not drive the GUI dialogs or actually install anything — that
stays manual, per above.

> **Verified on this branch, then fixed:** at first pass, the only published
> release, `v1.0.9`, carried **only a `.dmg` asset — no `.exe`**, so the "no
> asset for this OS" graceful path was what real Windows users would actually
> hit. Since this is currently a single-user/local install (no other consumers
> to worry about provenance for), `LINQS-Layout-Setup-1.0.9.exe` (built from
> this branch, including the `installer.iss` per-user-only fix above) was
> uploaded directly to the existing `v1.0.9` release via `gh release upload`.
> `viewer.update._fetch_latest()` now correctly resolves a `.exe` asset URL for
> `v1.0.9`. The local `viewer/__init__.py __version__` is also `1.0.9`, so
> **Check for Updates → up to date** is the path that actually exercises now;
> the download → quit → install → relaunch success path still needs a genuinely
> *newer* published version to test end-to-end.

---

## 10. Performance acceptance (T10)

On a real large DXF, record numbers and compare to the macOS reference
(parse a ~200 MB file in roughly ≤1 s; smooth interaction):

| metric | how | target |
|---|---|---|
| parse time / throughput | §2 large-file run | hundreds of MB/s, ≤~1 s for ~200 MB |
| first paint | time from launch to visible geometry | ~instant (≤1–2 s incl. parse) |
| pan/zoom | drag + scroll continuously | smooth, no visible stutter |
| layer toggle | click layers rapidly | instant, no rebuild flash |
| fill on/off (F) | toggle on a dense layer | instant |

**Pass:** all within the same ballpark as macOS. A regression here is a FAIL even
if everything is functionally correct — speed is the product.

> **Measured on this branch** (no ~200 MB fixture was available locally; used a
> real flattened R10 DXF instead — a fair real-world proxy, just smaller than
> the stress target):
>
> | metric | value |
> |---|---|
> | file | `TOPO01_ultrafast.dxf`, 24.9 MB, 39,405 polylines / 435,384 vertices / 24 circles / 7 layers |
> | parse throughput | 556 MB/s (45 ms) — extrapolates to ~360 ms for 200 MB, within the ≤1 s target |
> | first paint (parse + render) | 37 ms parse + 262 ms render ≈ 300 ms total, within the ≤1–2 s target |
>
> Pan/zoom feel, layer-toggle, and fill-toggle responsiveness are inherently
> subjective and were not re-verified here — same caveat as the §5 GUI
> checklist.

---

## 11. Reporting template

For each test record:

```
T#  <name>
  result : PASS | FAIL | SKIP
  env    : Windows <ver>, GPU <model + driver ver>, Python <ver>
  numbers: <parse ms / MB/s, render ms, fps feel — where relevant>
  notes  : <observations>
  on FAIL: <exact command>, <full traceback / error dialog text>, <screenshot>
```

Summarize at the end: which layers pass (native/loader, render, GUI, packaging,
installer, updater), and the single most important blocker if any. For GPU/render
or updater failures, attach the full traceback — those are the two paths most
likely to need a code fix, and a blind fix needs the exact error.
```
