#!/usr/bin/env python3
"""In-app updater logic smoke test (WINDOWS_TEST_PLAN.md T9, scriptable part).

Covers the network-handling logic in viewer/update.py with a mocked GitHub
API response (deterministic, no live network needed): version comparison,
picking the OS-appropriate asset by suffix, the "no matching asset for this
OS" case, and a network failure -- the three graceful-degradation paths the
plan's checklist calls out. Also makes one real (live) call to the public API
as a light integration check.

The GUI half of T9 (the prompt -> progress dialog -> quit -> installer ->
relaunch cycle) stays a manual check per the plan -- it needs a second,
newer release actually published with a Windows .exe asset to exercise
end-to-end, which this script does not do.

    python tests/test_updater.py

Exit code 0 = every check passed; 1 = something failed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _check(name, got, want) -> bool:
    ok = got == want
    tag = "ok  " if ok else "FAIL"
    extra = "" if ok else f"   (expected {want!r})"
    print(f"  [{tag}] {name}: {got!r}{extra}")
    return ok


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _release(tag: str, asset_names: list[str], **extra) -> dict:
    return {
        "tag_name": tag,
        "assets": [{"name": n, "browser_download_url": f"https://example/{n}"} for n in asset_names],
        **extra,        # e.g. draft=True / prerelease=True
    }


def test_mocked_responses() -> bool:
    """_fetch_latest now scans the releases *list* and returns the newest release
    that actually ships THIS OS's installer -- never one carrying only the other
    platform's asset. Mocks return a list (newest first), like the real API."""
    import urllib.request

    from viewer import update

    ok = True
    real_urlopen = urllib.request.urlopen
    suf = update._asset_suffix()                    # ".dmg" mac / ".exe" win / None
    ours = suf or ".dmg"                            # a concrete "our OS" asset name
    other = ".exe" if ours == ".dmg" else ".dmg"    # the *other* platform's asset

    def mock(releases):
        urllib.request.urlopen = lambda *a, **k: _FakeResponse(releases)

    # -- newest release ships our asset -> take it --------------------------
    mock([_release("v9.9.9", [f"setup{other}", f"LINQS-Layout{ours}"])])
    try:
        ver, url = update._fetch_latest()
        ok &= _check("newest has our asset: version", ver, "9.9.9")
        if suf is not None:
            ok &= _check("newest has our asset: url found", url is not None, True)
            if url is not None:
                ok &= _check("newest has our asset: url suffix", url.endswith(suf), True)
    finally:
        urllib.request.urlopen = real_urlopen

    # -- REGRESSION (v1.0.17): newest release lacks our asset, but an older -----
    #    one has it -> we must fall back to that older *installable* release,
    #    never fail with "could not download".
    mock([
        _release("v9.9.9", [f"setup-9.9.9{other}"]),          # newest, other OS only
        _release("v9.9.8", [f"setup-9.9.8{other}", f"LINQS-Layout{ours}"]),  # ours here
        _release("v9.9.7", [f"LINQS-Layout{ours}"]),
    ])
    try:
        ver, url = update._fetch_latest()
        if suf is not None:
            ok &= _check("fallback to newest installable: version", ver, "9.9.8")
            ok &= _check("fallback to newest installable: url is our OS's",
                         bool(url) and url.endswith(suf), True)
    finally:
        urllib.request.urlopen = real_urlopen

    # -- no release ships our asset -> (None, None), degrade gracefully -----
    mock([_release("v9.9.9", [f"setup{other}"])])
    try:
        ver, url = update._fetch_latest()
        if suf is not None:
            ok &= _check("no installable release: version is None", ver, None)
            ok &= _check("no installable release: url is None", url, None)
            # download helpers must return None (not raise) with no asset.
            ok &= _check("_win_download returns None gracefully", update._win_download(), None)
            ok &= _check("_mac_install returns False gracefully", update._mac_install(), False)
    finally:
        urllib.request.urlopen = real_urlopen

    # -- draft / prerelease entries are skipped -----------------------------
    mock([
        _release("v9.9.9", [f"setup-9.9.9{other}", f"LINQS-Layout{ours}"], draft=True),
        _release("v9.9.8", [f"setup-9.9.8{other}", f"LINQS-Layout{ours}"], prerelease=True),
        _release("v9.9.7", [f"setup-9.9.7{other}", f"LINQS-Layout{ours}"]),
    ])
    try:
        ver, _ = update._fetch_latest()
        if suf is not None:
            ok &= _check("draft/prerelease skipped: picks published", ver, "9.9.7")
    finally:
        urllib.request.urlopen = real_urlopen

    # -- network failure ------------------------------------------------------
    def _raise(*a, **k):
        raise urllib.error.URLError("simulated network failure")

    urllib.request.urlopen = _raise
    try:
        ver, url = update._fetch_latest()
        ok &= _check("network failure: version is None", ver, None)
        ok &= _check("network failure: url is None", url, None)
    finally:
        urllib.request.urlopen = real_urlopen

    # -- version comparison ----------------------------------------------------
    ok &= _check("_vt parses dotted versions", update._vt("v1.2.3"), (1, 2, 3))
    ok &= _check("current <= current is not an update", update._vt("1.0.9") <= update._vt("1.0.9"), True)
    ok &= _check("OS asset suffix matches platform", suf, ".dmg" if sys.platform == "darwin"
                 else ".exe" if sys.platform.startswith("win") else None)

    return ok


def test_ps_quote() -> bool:
    """PowerShell single-quoted literal escaping: only ' needs doubling."""
    from viewer import update

    ok = True
    ok &= _check("plain string", update._ps_quote("C:\\a\\b.exe"), "'C:\\a\\b.exe'")
    ok &= _check("embedded single quote is doubled",
                 update._ps_quote("C:\\O'Brien\\f.dxf"), "'C:\\O''Brien\\f.dxf'")
    return ok


def test_silent_install_and_relaunch() -> bool:
    """End-to-end: spawn the real detached helper chain (a harmless real .exe
    standing in for the installer, a tiny generated .bat standing in for the
    relaunch target) and confirm it actually relaunches with the right args.

    This is the regression test for a real bug caught during development: the
    relaunch step originally used cmd's `start "" "<path>" "<arg>" ...`, which
    has a documented quoting bug that mis-tokenizes a quoted program path
    followed by further quoted arguments into one mangled, unrecognized
    command -- it silently never relaunched. Fixed by using PowerShell's
    Start-Process -ArgumentList (a real array) instead. Uses a real .exe
    (not .bat) as the installer stand-in deliberately: a batch file invoking
    another batch file without CALL never returns to the caller, which would
    falsely fail this test for a reason that doesn't apply to the real Inno
    Setup .exe installer.
    """
    import shutil
    import tempfile
    import time

    from viewer import update

    real_exe_installer = shutil.which("where.exe") or r"C:\Windows\System32\where.exe"
    if not os.path.isfile(real_exe_installer):
        print("  [skip] no stand-in exe found (where.exe missing?)")
        return True

    work = tempfile.mkdtemp(prefix="linqs-updater-test-")
    marker = os.path.join(work, "relaunched.flag")
    fake_app = os.path.join(work, "fake_app.bat")
    with open(fake_app, "w") as f:
        f.write("@echo off\r\n")
        f.write(f'echo relaunched with: %* > "{marker}"\r\n')

    spaced_dir = os.path.join(work, "dir with spaces")
    os.makedirs(spaced_dir, exist_ok=True)
    reopen_paths = [os.path.join(spaced_dir, "a layout.dxf")]

    real_executable = sys.executable
    sys.executable = fake_app
    try:
        update._win_silent_install_and_relaunch(real_exe_installer, reopen_paths)
    finally:
        sys.executable = real_executable

    for _ in range(20):
        if os.path.exists(marker):
            break
        time.sleep(0.25)

    ok = _check("detached helper relaunched the app", os.path.exists(marker), True)
    if os.path.exists(marker):
        with open(marker) as f:
            content = f.read()
        ok &= _check("reopen path (with spaces) passed through correctly",
                      reopen_paths[0] in content, True)
    return ok


def test_live_api() -> bool:
    """Light integration check against the real, public GitHub API."""
    from viewer import update

    print(f"  current_version() = {update.current_version()}")
    ver = update.latest_version()
    ok = _check("live latest_version() reachable", ver is not None, True)
    if ver is not None:
        print(f"  latest published release: {ver}")
    return ok


def main() -> int:
    print("[mocked responses]")
    ok = test_mocked_responses()
    print("\n[ps quoting]")
    ok &= test_ps_quote()
    print("\n[silent install + relaunch]")
    ok &= test_silent_install_and_relaunch()
    print("\n[live API]")
    ok &= test_live_api()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
