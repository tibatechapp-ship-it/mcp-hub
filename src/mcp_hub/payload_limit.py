"""
Request payload size limiter (v1.3.0 P2.2).

Rejects POST/PUT/PATCH requests larger than a configured cap (default 1 MiB).
The Content-Length header is checked first; when it is absent (chunked
transfer encoding), the request body is counted as it streams and the request
is aborted with HTTP 413 as soon as the cap is crossed.

Configuration via env var:
  MCP_HUB_MAX_BODY_BYTES — default 1048576 (1 MiB)
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

__all__ = ["install_payload_limit"]

DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class _PayloadTooLarge(Exception):
    """Raised from the receive wrapper when a chunked body crosses the cap."""


def _max_bytes_from_env() -> int:
    try:
        return int(os.environ.get("MCP_HUB_MAX_BODY_BYTES", str(DEFAULT_MAX_BYTES)))
    except ValueError:
        return DEFAULT_MAX_BYTES


def _too_large(max_bytes: int) -> JSONResponse:
    return JSONResponse(
        {"detail": f"payload too large (max {max_bytes} bytes)"},
        status_code=413,
    )


def install_payload_limit(app, max_bytes: int | None = None) -> None:
    if max_bytes is None:
        max_bytes = _max_bytes_from_env()

    @app.middleware("http")
    async def _payload_mw(request: Request, call_next):
        if request.method not in BODY_METHODS:
            return await call_next(request)

        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return JSONResponse({"detail": "invalid content-length"}, status_code=400)
            if length > max_bytes:
                return _too_large(max_bytes)
            return await call_next(request)

        # No Content-Length: chunked upload. Count bytes as the app reads them.
        received = 0
        receive = request._receive  # noqa: SLF001 — Starlette has no public seam here

        async def _counting_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise _PayloadTooLarge
            return message

        request._receive = _counting_receive  # noqa: SLF001
        try:
            return await call_next(request)
        except _PayloadTooLarge:
            return _too_large(max_bytes)

    return None
