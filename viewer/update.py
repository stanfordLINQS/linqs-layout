"""In-app update: check the latest GitHub release and install it.

Uses the public GitHub REST API over plain HTTPS (``urllib``) — no GitHub CLI and
no token, so it works for anyone (the repo's releases are public). The check and
the download run on background threads.

Per-OS install strategy:
  * macOS   — download the release ``.dmg`` and replace ``/Applications/LINQS
              Layout.app`` in place (macOS lets you replace a running bundle),
              then offer to relaunch, reopening the layouts that were open.
  * Windows — download the release installer ``.exe`` and launch it; the running
              app quits so the installer can replace files (and relaunch).
  * other   — open the GitHub releases page in the browser.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from . import __version__

REPO = "stanfordLINQS/linqs-layout"
# We scan the releases *list* (newest first) rather than /releases/latest: mac and
# Windows builds are cut from separate machines and can't cross-compile, so a
# release can exist carrying only the *other* OS's asset (this has happened
# repeatedly). Trusting /releases/latest and then failing to find our asset is
# exactly what produced the "Could not download the update" dead-end. Instead we
# pick the newest release that actually ships THIS OS's installer, so an update is
# only ever offered when it can actually be installed.
API_RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

_IS_MAC = sys.platform == "darwin"
_IS_WIN = sys.platform.startswith("win")

MAC_INSTALL_PATH = "/Applications/LINQS Layout.app"


def current_version() -> str:
    return __version__


def _vt(s: str):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


def _asset_suffix() -> str | None:
    """The release asset filename suffix to install on this OS (None if unsupported)."""
    if _IS_MAC:
        return ".dmg"
    if _IS_WIN:
        return ".exe"
    return None


def _fetch_releases():
    """The repo's releases as a list, newest first (raises on network error)."""
    req = urllib.request.Request(
        API_RELEASES,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "linqs-layout"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _asset_url(release, suffix: str):
    """URL of ``release``'s first asset whose name ends with ``suffix``, else None."""
    return next((a.get("browser_download_url") for a in release.get("assets", [])
                 if str(a.get("name", "")).lower().endswith(suffix)), None)


class _Cancelled(Exception):
    """Raised inside a download when the user hits Cancel."""


def _download(url: str, dst: str, progress=None, cancelled=None,
              timeout: float = 30.0) -> None:
    """Stream ``url`` to ``dst`` with a per-read socket ``timeout``.

    Unlike ``urllib.request.urlretrieve`` (which passes *no* timeout, so a stalled
    connection hangs the app forever — the "hangs on the download step" bug), each
    socket read here is bounded by ``timeout``: a dead connection raises instead of
    blocking. ``progress(done, total)`` is called as bytes arrive (``total`` is 0
    if the server sends no Content-Length); ``cancelled()`` is polled so the user's
    Cancel takes effect promptly, raising :class:`_Cancelled`."""
    req = urllib.request.Request(url, headers={"User-Agent": "linqs-layout"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled is not None and cancelled():
                raise _Cancelled()
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)


def _fetch_latest():
    """Return (version, asset_url) for the newest *installable-on-this-OS* release,
    or (None, None) on a network error / when no release ships this OS's asset.

    On a supported OS this is the highest-version release that actually carries
    the OS-appropriate installer (``.dmg`` / ``.exe``) -- never a release that
    only has the other platform's asset (which could not be downloaded here). On
    an unsupported OS (no in-app installer) it reports the newest version overall
    so the caller can still point at the releases page. Draft/prerelease entries
    are skipped, so a half-published release is never targeted."""
    suf = _asset_suffix()
    try:
        releases = _fetch_releases()
    except Exception:
        return None, None

    newest_any = None                 # highest version overall (unsupported-OS path)
    best_ver, best_url, best_vt = None, None, ()
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        ver = (rel.get("tag_name") or "").lstrip("v") or None
        if not ver:
            continue
        vt = _vt(ver)
        if newest_any is None or vt > _vt(newest_any):
            newest_any = ver
        if suf is None:
            continue
        url = _asset_url(rel, suf)
        if url and vt > best_vt:       # newest version that ships our installer
            best_ver, best_url, best_vt = ver, url, vt

    if suf is None:
        return newest_any, None
    return best_ver, best_url


def latest_version() -> str | None:
    return _fetch_latest()[0]


# -- macOS: in-place install of the .dmg --------------------------------------
def _mac_install(progress=None, cancelled=None) -> bool:
    """Download the latest release DMG and install the .app into /Applications.

    Every external step is time-bounded (streamed download with a socket timeout,
    ``timeout=`` on each subprocess) so the updater can never hang indefinitely."""
    _ver, url = _fetch_latest()
    if not url:
        return False
    tmp = tempfile.mkdtemp()
    mnt = None
    try:
        dmg = os.path.join(tmp, "update.dmg")
        _download(url, dmg, progress, cancelled)
        att = subprocess.run(
            ["hdiutil", "attach", dmg, "-nobrowse", "-noverify", "-readonly"],
            capture_output=True, text=True, timeout=120)
        mnt = next((ln.split("\t")[-1].strip() for ln in att.stdout.splitlines()
                    if "/Volumes/" in ln), None)
        if not mnt:
            return False
        src = os.path.join(mnt, "LINQS Layout.app")
        if not os.path.isdir(src):
            return False
        subprocess.run(["rm", "-rf", MAC_INSTALL_PATH], timeout=60)
        subprocess.run(["ditto", src, MAC_INSTALL_PATH], check=True, timeout=300)
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", MAC_INSTALL_PATH],
                       timeout=60)
        return True
    except (_Cancelled, Exception):
        return False
    finally:
        if mnt:
            subprocess.run(["hdiutil", "detach", mnt, "-force"],
                           capture_output=True, timeout=60)
        shutil.rmtree(tmp, ignore_errors=True)


# -- Windows: download the installer .exe (run after we quit) -----------------
def _win_download(progress=None, cancelled=None) -> str | None:
    """Download the latest release installer to a temp file; return its path.

    The temp dir is intentionally NOT cleaned up here — the installer runs after
    the app quits, so the file must outlive this process."""
    _ver, url = _fetch_latest()
    if not url:
        return None
    try:
        tmp = tempfile.mkdtemp(prefix="linqs-update-")
        dst = os.path.join(tmp, os.path.basename(url) or "LINQS-Layout-Setup.exe")
        _download(url, dst, progress, cancelled)
        return dst
    except (_Cancelled, Exception):
        return None


def _ps_quote(s: str) -> str:
    """Quote a string as a PowerShell single-quoted literal (doubling embedded
    quotes is PowerShell's own escaping rule, distinct from cmd's)."""
    return "'" + s.replace("'", "''") + "'"


def _win_silent_install_and_relaunch(installer_path: str, reopen_paths: list[str]) -> None:
    """Run the downloaded installer silently, then relaunch once it finishes.

    A self-updater can't just run the installer and wait synchronously in this
    process: Inno's CloseApplications will (correctly) close *this* running app
    as part of replacing its own files, so anything blocking on the installer
    here would deadlock. Instead, spawn a small detached helper script that
    survives this process quitting -- it waits for the installer, then
    relaunches the app from its (now-updated) install location, reopening
    whatever was open before.

    The relaunch step uses PowerShell's Start-Process -ArgumentList (an actual
    array, not a hand-quoted string) rather than cmd's ``start`` -- ``start``
    has a long-documented quoting bug where a quoted program path followed by
    further quoted arguments (needed here: file paths can contain spaces) gets
    mis-tokenized into a single mangled, unrecognized command.
    """
    app_exe = sys.executable
    tmp = tempfile.mkdtemp(prefix="linqs-update-")
    bat = os.path.join(tmp, "apply_update.bat")
    arg_list = ", ".join(_ps_quote(p) for p in reopen_paths)
    relaunch = f"Start-Process -FilePath {_ps_quote(app_exe)}"
    if arg_list:
        relaunch += f" -ArgumentList @({arg_list})"
    with open(bat, "w") as f:
        f.write("@echo off\r\n")
        f.write(f'"{installer_path}" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES\r\n')
        f.write(f'powershell -NoProfile -NonInteractive -WindowStyle Hidden -Command "{relaunch}"\r\n')
    subprocess.Popen(
        ["cmd", "/c", bat],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)


def _open_paths(window) -> list[str]:
    """The layout file paths currently open in ``window`` (empty for the welcome
    screen), so relaunch can reopen them (macOS)."""
    tabs = getattr(window, "tabs", None)
    if tabs is None:
        return []
    paths = []
    for i in range(tabs.count()):
        p = getattr(getattr(tabs.widget(i), "layout_obj", None), "path", None)
        if p:
            paths.append(p)
    return paths


def _relaunch(paths=None):
    """Relaunch the freshly installed macOS app, reopening ``paths``."""
    args = ["open", "-n", MAC_INSTALL_PATH]
    if paths:
        args += ["--args", *paths]          # reopen the current layouts
    subprocess.Popen(args)
    QApplication.quit()


class _CheckThread(QThread):
    result = Signal(str)        # latest version or ""

    def run(self):
        self.result.emit(latest_version() or "")


class _InstallThread(QThread):
    # macOS: "ok" on success; Windows: the installer path; "" on failure/cancel.
    result = Signal(str)
    progress = Signal(int, int)     # (downloaded_bytes, total_bytes); total 0 if unknown

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _cancelled(self) -> bool:
        return self._cancel

    def _progress(self, done: int, total: int):
        self.progress.emit(done, total)

    def run(self):
        if _IS_MAC:
            self.result.emit("ok" if _mac_install(self._progress, self._cancelled) else "")
        elif _IS_WIN:
            self.result.emit(_win_download(self._progress, self._cancelled) or "")
        else:
            self.result.emit("")


def check_for_updates(window, silent: bool = False):
    """Check for a newer release; if found, offer to download + install it.

    ``silent`` suppresses the "you're up to date" / failure dialogs (used for the
    automatic check at startup)."""
    th = _CheckThread(window)
    window.__update_check = th        # keep a reference alive

    def got(latest):
        if not latest:
            if not silent:
                QMessageBox.information(
                    window, "Updates", "Could not reach the update server.")
            return
        if _vt(latest) <= _vt(current_version()):
            if not silent:
                QMessageBox.information(
                    window, "Up to date",
                    f"LINQS Layout {current_version()} is the latest version.")
            return
        # No in-app installer for this OS → send them to the releases page.
        if _asset_suffix() is None:
            if QMessageBox.question(
                    window, "Update available",
                    f"LINQS Layout {latest} is available — you have "
                    f"{current_version()}.\n\nOpen the releases page?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                webbrowser.open(RELEASES_PAGE)
            return
        if QMessageBox.question(
                window, "Update available",
                f"LINQS Layout {latest} is available — you have {current_version()}.\n\n"
                "Download and install it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            _install(window, latest)

    th.result.connect(got)
    th.start()


def _install(window, latest):
    msg = f"Downloading LINQS Layout {latest}…"
    dlg = QProgressDialog(msg, "Cancel", 0, 100, window)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    inst = _InstallThread(window)
    window.__update_install = inst    # keep a reference alive
    state = {"cancelled": False}

    def on_progress(done_b, total_b):
        if total_b > 0 and done_b < total_b:
            dlg.setRange(0, total_b)
            dlg.setValue(done_b)
            dlg.setLabelText(f"Downloading LINQS Layout {latest}…"
                             f"   {done_b >> 20} / {total_b >> 20} MB")
        else:                          # download done (or size unknown): now installing
            dlg.setRange(0, 0)         # busy spinner — the install phase has no %
            dlg.setLabelText("Installing…" if _IS_MAC else "Preparing installer…")

    def on_cancel():
        state["cancelled"] = True
        inst.cancel()

    inst.progress.connect(on_progress)
    dlg.canceled.connect(on_cancel)

    def done(result):
        dlg.close()
        if not result:
            if state["cancelled"]:
                return                 # user cancelled — no error dialog
            QMessageBox.warning(
                window, "Update failed",
                "Could not download the update. Try the releases page on GitHub.")
            return
        if _IS_MAC:
            if QMessageBox.question(
                    window, "Update installed",
                    f"Updated to {latest}. Relaunch now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                _relaunch(_open_paths(window))
        elif _IS_WIN:
            if QMessageBox.question(
                    window, "Install update",
                    f"The {latest} installer is ready. Install it now?\n\n"
                    "LINQS Layout will close, update silently in the background, "
                    "and relaunch automatically.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                try:
                    _win_silent_install_and_relaunch(result, _open_paths(window))
                except Exception:
                    webbrowser.open(RELEASES_PAGE)
                QApplication.quit()

    inst.result.connect(done)
    inst.start()
    dlg.show()
