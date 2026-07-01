#!/usr/bin/env python3
"""Fail if a release is missing a per-OS installer asset.

macOS and Windows builds are cut on separate machines (no cross-compile), so it
is easy to publish a release carrying only one platform's asset -- which silently
breaks the *other* platform's in-app updater (the "Could not download the update"
dead-end). This guard turns that into a loud, catchable error. Uses the public
GitHub REST API over urllib -- no gh, no token needed.

    python packaging/check_release_assets.py           # checks the current source version
    python packaging/check_release_assets.py v1.0.17   # checks a specific tag
    python packaging/check_release_assets.py latest     # checks GitHub's 'latest'

Exit 0 = the release ships both a .dmg and a .exe; 1 = one is missing / not found.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

REPO = "stanfordLINQS/linqs-layout"
REQUIRED = {".dmg": "macOS", ".exe": "Windows"}   # every release needs both


def _version_from_source() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(here))
    from viewer import __version__
    return __version__


def _api(url: str):
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "linqs-layout"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _fetch(tag: str):
    if tag == "latest":
        return _api(f"https://api.github.com/repos/{REPO}/releases/latest")
    return _api(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}")


def main(argv) -> int:
    tag = argv[1] if len(argv) > 1 else "v" + _version_from_source()
    try:
        rel = _fetch(tag)
    except Exception as e:  # noqa: BLE001 - report any lookup failure plainly
        print(f"ERROR: could not fetch release {tag!r}: {e}")
        return 1

    names = [a.get("name", "") for a in rel.get("assets", [])]
    missing = [f"{suf} ({os_name})" for suf, os_name in REQUIRED.items()
               if not any(n.lower().endswith(suf) for n in names)]
    print(f"release {rel.get('tag_name', tag)} assets: {', '.join(names) or '(none)'}")
    if missing:
        print("MISSING required installer asset(s): " + ", ".join(missing))
        print("Every release must ship BOTH a macOS .dmg and a Windows .exe, or the")
        print("missing platform's in-app updater breaks. Build the missing one and")
        print("attach it:  gh release upload <tag> <file>")
        return 1
    print("OK: both a .dmg and a .exe are attached.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
