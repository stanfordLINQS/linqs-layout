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


def _release(tag: str, asset_names: list[str]) -> dict:
    return {
        "tag_name": tag,
        "assets": [{"name": n, "browser_download_url": f"https://example/{n}"} for n in asset_names],
    }


def test_mocked_responses() -> bool:
    import urllib.request

    from viewer import update

    ok = True
    real_urlopen = urllib.request.urlopen

    # -- newer version, with a matching .exe asset --------------------------
    urllib.request.urlopen = lambda *a, **k: _FakeResponse(
        _release("v9.9.9", ["LINQS-Layout-Setup-9.9.9.exe", "LINQS-Layout.dmg"]))
    try:
        ver, url = update._fetch_latest()
        ok &= _check("newer + matching asset: version", ver, "9.9.9")
        ok &= _check("newer + matching asset: url found", url is not None, True)
        if url is not None:
            ok &= _check("matching asset: url suffix", url.endswith(".exe"), True)
    finally:
        urllib.request.urlopen = real_urlopen

    # -- newer version, but no asset for this OS (the real v1.0.9 case) -----
    urllib.request.urlopen = lambda *a, **k: _FakeResponse(
        _release("v9.9.9", ["LINQS-Layout.dmg"]))
    try:
        ver, url = update._fetch_latest()
        ok &= _check("no matching asset: version still parsed", ver, "9.9.9")
        ok &= _check("no matching asset: url is None", url, None)
        # _win_download must fail gracefully (None), not raise, when there's no asset.
        try:
            result = update._win_download()
            ok &= _check("_win_download returns None gracefully", result, None)
        except Exception as ex:  # noqa: BLE001
            ok &= _check("_win_download raised (should not)", repr(ex), None)
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
    ok &= _check("OS asset suffix on Windows", update._asset_suffix(), ".exe")

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
    print("\n[live API]")
    ok &= test_live_api()
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
