"""In-app update: check the latest GitHub release and install it.

Uses the GitHub CLI (``gh``) so it works against the private repo for anyone with
access. The check and the download/install run on background threads; the prompts
are plain dialogs. Installing replaces ``/Applications/LINQS Layout.app`` in place
(macOS lets you replace a running bundle) and offers to relaunch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from . import __version__

REPO = "stanfordLINQS/linqs-layout"
INSTALL_PATH = "/Applications/LINQS Layout.app"


def current_version() -> str:
    return __version__


def _vt(s: str):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3])


def _gh() -> str | None:
    # A Finder-launched .app doesn't inherit the shell PATH, so also look in the
    # usual Homebrew locations where gh lives.
    return (shutil.which("gh")
            or next((p for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")
                     if os.path.exists(p)), None))


def latest_version() -> str | None:
    """Latest release version (e.g. '1.0.6'), or None if gh is unavailable/fails."""
    gh = _gh()
    if not gh:
        return None
    try:
        r = subprocess.run(
            [gh, "release", "view", "--repo", REPO, "--json", "tagName", "-q", ".tagName"],
            capture_output=True, text=True, timeout=20)
        return (r.stdout.strip().lstrip("v") or None) if r.returncode == 0 else None
    except Exception:
        return None


def _download_and_install() -> bool:
    """Download the latest release DMG and install the .app into /Applications."""
    gh = _gh()
    if not gh:
        return False
    tmp = tempfile.mkdtemp()
    try:
        r = subprocess.run(
            [gh, "release", "download", "--repo", REPO, "--pattern", "*.dmg",
             "--dir", tmp, "--clobber"],
            capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return False
        dmgs = [f for f in os.listdir(tmp) if f.lower().endswith(".dmg")]
        if not dmgs:
            return False
        att = subprocess.run(
            ["hdiutil", "attach", os.path.join(tmp, dmgs[0]), "-nobrowse", "-noverify", "-readonly"],
            capture_output=True, text=True)
        mnt = next((ln.split("\t")[-1].strip() for ln in att.stdout.splitlines()
                    if "/Volumes/" in ln), None)
        if not mnt:
            return False
        try:
            src = os.path.join(mnt, "LINQS Layout.app")
            if not os.path.isdir(src):
                return False
            subprocess.run(["rm", "-rf", INSTALL_PATH])
            subprocess.run(["ditto", src, INSTALL_PATH], check=True)
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", INSTALL_PATH])
            return True
        finally:
            subprocess.run(["hdiutil", "detach", mnt], capture_output=True)
    except Exception:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _open_paths(window) -> list[str]:
    """The layout file paths currently open in ``window`` (empty for the welcome
    screen), so relaunch can reopen them."""
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
    args = ["open", "-n", INSTALL_PATH]
    if paths:
        args += ["--args", *paths]          # reopen the current layouts
    subprocess.Popen(args)
    QApplication.quit()


class _CheckThread(QThread):
    result = Signal(str)        # latest version or ""

    def run(self):
        self.result.emit(latest_version() or "")


class _InstallThread(QThread):
    result = Signal(bool)

    def run(self):
        self.result.emit(_download_and_install())


def check_for_updates(window, silent: bool = False):
    """Check for a newer release; if found, offer to download + install it.

    ``silent`` suppresses the "you're up to date" / "gh missing" dialogs (used for
    the automatic check at startup)."""
    if not _gh():
        if not silent:
            QMessageBox.information(
                window, "Updates",
                "The GitHub CLI (gh) is required to check for updates.\n"
                "Install it with:  brew install gh  &&  gh auth login")
        return

    th = _CheckThread(window)
    window.__update_check = th        # keep a reference alive

    def got(latest):
        if not latest or _vt(latest) <= _vt(current_version()):
            if not silent:
                QMessageBox.information(
                    window, "Up to date",
                    f"LINQS Layout {current_version()} is the latest version.")
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
    dlg = QProgressDialog(f"Downloading LINQS Layout {latest}…", None, 0, 0, window)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    inst = _InstallThread(window)
    window.__update_install = inst    # keep a reference alive

    def done(ok):
        dlg.close()
        if ok and QMessageBox.question(
                window, "Update installed",
                f"Updated to {latest}. Relaunch now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            _relaunch(_open_paths(window))
        elif not ok:
            QMessageBox.warning(
                window, "Update failed",
                "Could not install the update. Try the releases page, "
                "or run `bash packaging/update.sh`.")

    inst.result.connect(done)
    inst.start()
    dlg.show()
