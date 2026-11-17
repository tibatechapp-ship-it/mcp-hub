"""
Request payload size limiter (v1.3.0 P2.2, hardened in v1.3.1).

Rejects POST/PUT/PATCH requests larger than a configured cap (default 1 MiB).

Two paths:

* **Content-Length declared** — rejected with 413 before a single byte is read.
* **Chunked (no Content-Length)** — the body is buffered up to the cap; the
  request is rejected with 413 the moment the cap is crossed, before the
  application runs. Memory is bounded by the cap, not by the upload size.

Implemented as a raw ASGI middleware rather than a ``BaseHTTPMiddleware``:
a body error raised from inside the app task cannot be turned into a clean
response once the response has started streaming, so the check has to happen
before the app is called.

Configuration via env var:
  MCP_HUB_MAX_BODY_BYTES — default 1048576 (1 MiB)
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["install_payload_limit", "PayloadLimitMiddleware", "DEFAULT_MAX_BYTES"]

DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _max_bytes_from_env() -> int:
    try:
        return int(os.environ.get("MCP_HUB_MAX_BODY_BYTES", str(DEFAULT_MAX_BYTES)))
    except ValueError:
        return DEFAULT_MAX_BYTES


async def _send_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PayloadLimitMiddleware:
    """ASGI middleware enforcing a hard cap on request bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") not in BODY_METHODS:
            await self.app(scope, receive, send)
            return

        declared = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                declared = value.decode("latin-1")
                break

        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                await _send_error(send, 400, "invalid content-length")
                return
            if length > self.max_bytes:
                await _send_error(send, 413, f"payload too large (max {self.max_bytes} bytes)")
                return
            await self.app(scope, receive, send)
            return

        # Chunked upload: buffer up to the cap, reject the instant it is crossed.
        chunks: list[bytes] = []
        total = 0
        while True:
            message: Message = await receive()
            if message.get("type") == "http.disconnect":
                await self.app(scope, receive, send)
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await _send_error(send, 413, f"payload too large (max {self.max_bytes} bytes)")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        payload = b"".join(chunks)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}

        await self.app(scope, replay, send)


def install_payload_limit(app: FastAPI, max_bytes: int | None = None) -> None:
    """Install the body cap. `max_bytes` overrides MCP_HUB_MAX_BODY_BYTES."""
    app.add_middleware(
        PayloadLimitMiddleware,
        max_bytes=max_bytes if max_bytes is not None else _max_bytes_from_env(),
    )
