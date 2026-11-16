from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .redact import redact


def _repo_from_url(url: str) -> str | None:
    m = re.search(r"github\.com/([^/]+)/([^/]+)", url or "")
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    return f"{owner}/{repo}"


def _fetch_repo(repo: str, token: str | None = None) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}"
    headers = {"User-Agent": "mcp-hub/1.1", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def enrich_catalog(db, limit: int = 200, token: str | None = None) -> dict[str, Any]:
    token = token or os.environ.get("GITHUB_TOKEN")
    targets = db.servers_needing_enrichment(limit=limit)
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0
    last_error = ""
    for srv in targets:
        attempted += 1
        repo = _repo_from_url(srv.get("repository_url", ""))
        if not repo:
            skipped += 1
            continue
        try:
            data = _fetch_repo(repo, token=token)
            db.save_enrichment(
                int(srv["id"]),
                {
                    "stars": data.get("stargazers_count", 0),
                    "forks": data.get("forks_count", 0),
                    "license": (data.get("license") or {}).get("spdx_id") or "",
                    "pushed_at": data.get("pushed_at") or "",
                    "topics": ",".join(data.get("topics") or []),
                },
            )
            succeeded += 1
        except urllib.error.HTTPError as exc:
            failed += 1
            last_error = redact(f"HTTP {exc.code} for {repo}")
        except Exception as exc:  # noqa: BLE001 - surfaced redacted, never raw
            failed += 1
            last_error = redact(exc)
    result: dict[str, Any] = {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
    }
    if last_error:
        result["last_error"] = last_error
    return result
