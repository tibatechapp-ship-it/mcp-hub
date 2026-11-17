from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

# Methods a read-only key may use.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Health must stay reachable for probes and container healthchecks.
_OPEN_PATHS = frozenset({"/api/health", "/"})

# /metrics carries catalog counts, so it is key-gated like the API even though
# it does not live under /api/.
_PROTECTED_PREFIXES = ("/api/",)
_PROTECTED_EXACT = frozenset({"/metrics"})


def _is_protected(path: str) -> bool:
    return path in _PROTECTED_EXACT or path.startswith(_PROTECTED_PREFIXES)


@dataclass(frozen=True)
class ApiKey:
    """One configured key. `name` is only used for logs; never for auth."""

    name: str
    secret: str
    read_only: bool = False


def get_api_key_from_env() -> str | None:
    return os.environ.get("MCP_HUB_API_KEY", "").strip() or None


def parse_api_keys(raw: str | None) -> list[ApiKey]:
    """
    Parse `MCP_HUB_API_KEYS` into a key list.

    Format: ``name:secret[:ro]``, comma-separated. A key may itself contain
    colons (base64 does not, but URL-safe base64 padding does), so only the
    first colon separates the name and only a trailing ``:ro`` marks the scope.
    Entries without a name (``secret`` alone) are accepted and get the name
    ``key<N>``.
    """
    if not raw or not raw.strip():
        return []
    keys: list[ApiKey] = []
    for index, chunk in enumerate(raw.split(","), start=1):
        entry = chunk.strip()
        if not entry:
            continue
        read_only = False
        if entry.endswith(":ro"):
            read_only = True
            entry = entry[: -len(":ro")]
        if ":" in entry:
            name, secret = entry.split(":", 1)
        else:
            name, secret = f"key{index}", entry
        name, secret = name.strip(), secret.strip()
        if not secret:
            continue
        keys.append(ApiKey(name=name or f"key{index}", secret=secret, read_only=read_only))
    return keys


def keys_from_env(single: str | None = None) -> list[ApiKey]:
    """Multi-key config wins; the single-key variable stays supported."""
    keys = parse_api_keys(os.environ.get("MCP_HUB_API_KEYS"))
    if keys:
        return keys
    single = single or get_api_key_from_env()
    return [ApiKey(name="default", secret=single)] if single else []


def _extract_bearer(header: str | None) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def _match(provided: str, keys: list[ApiKey]) -> ApiKey | None:
    """
    Constant-time comparison against every configured key.

    All candidates are compared (no early return on a hit) so the response time
    does not reveal which key matched or how far down the list it sits.
    """
    if not provided:
        return None
    found: ApiKey | None = None
    for candidate in keys:
        if hmac.compare_digest(provided, candidate.secret) and found is None:
            found = candidate
    return found


def add_auth_middleware(
    app: FastAPI, api_key: str | None = None, keys: list[ApiKey] | None = None
) -> None:
    configured = keys if keys is not None else keys_from_env(api_key)
    if not configured:
        return

    @app.middleware("http")
    async def _check_key(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not _is_protected(path) or path in _OPEN_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-api-key") or _extract_bearer(
            request.headers.get("authorization")
        )
        matched = _match(provided, configured)
        if matched is None:
            return JSONResponse(status_code=401, content={"detail": "missing or invalid API key"})
        if matched.read_only and request.method not in _SAFE_METHODS:
            return JSONResponse(
                status_code=403,
                content={"detail": f"API key {matched.name!r} is read-only"},
            )
        request.state.api_key_name = matched.name
        return await call_next(request)
