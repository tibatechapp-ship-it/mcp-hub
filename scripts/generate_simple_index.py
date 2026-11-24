#!/usr/bin/env python3
"""Generate a PEP 503 simple index from GitHub Release assets.

Stateless by design: the only inputs are the repository slug and the public
Release API. Every emitted link carries a SHA-256 fragment, so pip refuses a
tampered asset even if a Release asset is later replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from mcp_hub.pep503 import Distribution, Release, build_index  # noqa: E402

_API = "https://api.github.com"
_UA = "mcp-hub-pep503-generator"


def _headers(token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_get(path: str, token: str | None) -> object:
    request = urllib.request.Request(_API + path, headers=_headers(token))
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _sha256_asset(asset_url: str, token: str | None) -> str:
    request = urllib.request.Request(
        asset_url, headers=_headers(token, accept="application/octet-stream")
    )
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as response:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requires_python() -> str:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("pyproject.toml has no requires-python")
    return match.group(1)


def _releases(repo: str, token: str | None, requires_python: str) -> list[Release]:
    payload = _api_get(f"/repos/{repo}/releases?per_page=100", token)
    if not isinstance(payload, list):
        raise RuntimeError("unexpected GitHub Releases response")
    releases: list[Release] = []
    for item in payload:
        tag = item.get("tag_name") or ""
        if item.get("draft") or item.get("prerelease") or not tag.startswith("v"):
            continue
        dists: list[Distribution] = []
        for asset in item.get("assets", []):
            name = asset.get("name") or ""
            if not (name.endswith(".whl") or name.endswith(".tar.gz")):
                continue
            dists.append(
                Distribution(
                    filename=name,
                    url=asset["browser_download_url"],
                    sha256=_sha256_asset(asset["url"], token),
                    requires_python=requires_python,
                )
            )
        if dists:
            releases.append(Release(version=tag[1:], distributions=tuple(dists)))
    return releases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "tibatechapp-ship-it/mcp-hub"),
        help="owner/name (default: GITHUB_REPOSITORY or tibatechapp-ship-it/mcp-hub)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_ROOT / "site" / "simple",
        help="directory for the generated simple/ tree",
    )
    parser.add_argument("--project", default="mcp-hub")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    releases = _releases(args.repo, token, _requires_python())
    if not releases:
        raise RuntimeError(f"no published release assets found for {args.repo}")

    for relative_path, html in build_index({args.project: releases}).items():
        target = args.output / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
