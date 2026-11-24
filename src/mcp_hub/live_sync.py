"""Upstream-only live refresh for the Arabic registry cache.

The only accepted source of truth is the curated registry JSON on the default
branch of this repository. Local edits, forks, arbitrary URLs and branch names
are never accepted as an upstream source. Every fetch goes through
:func:`mcp_hub.safe_fetch.safe_request` so DNS rebinding and private-network
pivots stay blocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from .safe_fetch import safe_request

UPSTREAM_HOST = "raw.githubusercontent.com"
UPSTREAM_PATH = "/tibatechapp-ship-it/mcp-hub/main/registry/mcp_servers.json"
DEFAULT_UPSTREAM_URL = "https://" + UPSTREAM_HOST + UPSTREAM_PATH
DEFAULT_CACHE = Path.home() / ".cache" / "mcp-hub" / "arabic_runtime.json"


class UpstreamError(ValueError):
    """Raised when a refresh source or payload violates the upstream policy."""


class CacheError(ValueError):
    """Raised when the local cache file is corrupt."""


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one upstream refresh attempt."""

    status: str
    registry: dict[str, Any] | None
    source: str
    digest: str = ""
    reason: str = ""


def is_allowed_upstream(url: str) -> bool:
    """True only for the exact raw default-branch registry URL."""
    parts = urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == UPSTREAM_HOST
        and parts.path == UPSTREAM_PATH
        and port in (None, 443)
        and not parts.username
        and not parts.password
        and not parts.query
        and not parts.fragment
    )


def load_cache(path: Path) -> dict[str, Any]:
    """Read a cache file and return its envelope."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CacheError(f"cannot read Arabic cache: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("registry"), dict):
        raise CacheError("Arabic cache has no registry object")
    return cast(dict[str, Any], payload)


def save_cache(
    path: Path,
    registry: dict[str, Any],
    *,
    source: str,
    digest: str = "",
) -> None:
    """Atomically replace the cache with a freshly validated registry."""
    envelope = {
        "schema_version": 1,
        "source": source,
        "digest": digest,
        "synced_at": time.time(),
        "registry": registry,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _valid_registry(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    servers = value.get("mcpServers")
    return isinstance(servers, dict) and bool(servers)


def refresh_from_upstream(
    cache_path: Path,
    url: str = DEFAULT_UPSTREAM_URL,
    *,
    ttl: float = 0.0,
    force: bool = False,
) -> SyncResult:
    """Fetch the upstream registry if due and atomically refresh the cache.

    ``ttl`` is seconds; 0 means check on every call (a conditional request is
    cheap because an unchanged body is answered with 304). Failures raise so
    callers can decide whether to fall back to the current cache.
    """
    if not url:
        return SyncResult(status="disabled", registry=None, source="none")
    if not is_allowed_upstream(url):
        raise UpstreamError("refusing non-upstream registry URL")

    cached: dict[str, Any] | None = None
    try:
        cached = load_cache(cache_path)
    except CacheError:
        cached = None

    if not force and cached and ttl > 0:
        synced_at = cached.get("synced_at")
        if isinstance(synced_at, (int, float)) and (time.time() - synced_at) < ttl:
            registry = cached.get("registry")
            if _valid_registry(registry):
                return SyncResult(
                    status="fresh",
                    registry=cast(dict[str, Any], registry),
                    source=str(cached.get("source", "cache")),
                    digest=str(cached.get("digest", "")),
                    reason="ttl",
                )

    headers: dict[str, str] = {}
    if cached and cached.get("digest"):
        headers["If-None-Match"] = str(cached["digest"])

    status, body = safe_request(url, headers=headers, timeout=5.0, max_bytes=2 * 1024 * 1024)
    if status == 304 and cached:
        registry = cached.get("registry")
        if _valid_registry(registry):
            return SyncResult(
                status="not-modified",
                registry=cast(dict[str, Any], registry),
                source=str(cached.get("source", "cache")),
                digest=str(cached.get("digest", "")),
            )
    if status != 200:
        raise UpstreamError(f"upstream returned HTTP {status}")

    try:
        registry = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpstreamError(f"upstream registry is not valid UTF-8 JSON: {exc}") from exc
    if not _valid_registry(registry):
        raise UpstreamError("upstream registry has no mcpServers object")

    digest = hashlib.sha256(body).hexdigest()
    save_cache(cache_path, cast(dict[str, Any], registry), source="upstream", digest=digest)
    return SyncResult(
        status="updated",
        registry=cast(dict[str, Any], registry),
        source="upstream",
        digest=digest,
    )
