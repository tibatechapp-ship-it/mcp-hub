from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from .models import Server

REGISTRY_BASE = "https://registry.modelcontextprotocol.io/v0/servers"


def _http_get_json(url: str, retries: int = 3, timeout: int = 20) -> dict[str, Any]:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mcp-hub/1.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"registry fetch failed: {last_err}")


def _to_server(item: dict[str, Any]) -> Server:
    s = item.get("server", item) or {}
    meta = item.get("_meta", {}) or {}
    name = s.get("name") or s.get("title") or ""
    desc = s.get("description") or ""
    repo = ""
    r = s.get("repository") or {}
    if isinstance(r, dict):
        repo = r.get("url") or ""
    endpoint = ""
    remotes = s.get("remotes") or []
    if remotes and isinstance(remotes, list) and isinstance(remotes[0], dict):
        endpoint = remotes[0].get("url") or ""
    publisher = ""
    if "/" in name:
        # io.github.owner/repo style
        publisher = name.split("/", 1)[0].split(".")[-1]
    return Server(
        name=name,
        publisher=publisher,
        description=desc,
        source="registry",
        registry_id=s.get("name") or meta.get("id") or "",
        repository_url=repo,
        endpoint_url=endpoint,
        package_name=(s.get("packages") or [{}])[0].get("identifier", "") if s.get("packages") else "",
        raw_name=name,
    )


def fetch_servers(limit: int = 100, cursor: str | None = None) -> list[Server]:
    out: list[Server] = []
    next_cursor = cursor
    remaining = max(0, int(limit))
    while remaining > 0:
        page_size = min(remaining, 100)
        params = {"limit": page_size}
        if next_cursor:
            params["cursor"] = next_cursor
        url = REGISTRY_BASE + "?" + urllib.parse.urlencode(params)
        payload = _http_get_json(url)
        servers = payload.get("servers") or []
        for item in servers:
            try:
                out.append(_to_server(item))
            except Exception:
                continue
        remaining = limit - len(out)
        next_cursor = (payload.get("metadata") or {}).get("nextCursor")
        if not next_cursor or not servers:
            break
    return out[:limit]
