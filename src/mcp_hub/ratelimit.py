"""
In-process token-bucket rate limiter (v1.3.0 P1.1).

Per-client-IP rate limit on all /api/* endpoints except /api/health and
/api/doctor. Uses a simple in-memory dict — no Redis, no external state. Fits
the local-first design: the API is normally bound to 127.0.0.1.

Configuration via env var:
  MCP_HUB_RATE_LIMIT_PER_MIN  — default 120 (2 req/sec sustained)

A value of 0 disables rate limiting entirely (useful for tests).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

__all__ = ["install_rate_limit", "TokenBucket"]

# Endpoints that must stay reachable even under load (probes, dashboards).
EXEMPT_PATHS = frozenset({"/api/health", "/api/doctor"})

# Guards the bucket table itself; each bucket has its own lock.
_MAX_BUCKETS = 10_000


class TokenBucket:
    """Thread-safe token bucket. Refills continuously at `rate` per second."""

    __slots__ = ("capacity", "rate", "_tokens", "_last", "_lock")

    def __init__(self, capacity: int, rate: float):
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity and rate must be > 0")
        self.capacity = float(capacity)
        self.rate = float(rate)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def take(self, n: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    @property
    def tokens(self) -> float:
        with self._lock:
            return self._tokens


def _client_ip(request: Request) -> str:
    """Best-effort client IP, honouring X-Forwarded-For only from a local proxy."""
    xff = request.headers.get("x-forwarded-for")
    if xff and request.client and request.client.host in {"127.0.0.1", "::1"}:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _per_minute_from_env() -> int:
    try:
        return int(os.environ.get("MCP_HUB_RATE_LIMIT_PER_MIN", "120"))
    except ValueError:
        return 120


def install_rate_limit(app: FastAPI, per_minute: int | None = None) -> None:
    """
    Install a token-bucket rate limiter on /api/* (except the exempt paths).

    `per_minute` overrides the env var. Pass 0 to disable.
    """
    if per_minute is None:
        per_minute = _per_minute_from_env()
    if per_minute <= 0:
        return  # disabled

    capacity = int(per_minute)
    rate = per_minute / 60.0
    buckets: dict[str, TokenBucket] = {}
    buckets_lock = threading.Lock()

    def _bucket(ip: str) -> TokenBucket:
        with buckets_lock:
            bucket = buckets.get(ip)
            if bucket is None:
                if len(buckets) >= _MAX_BUCKETS:
                    buckets.clear()  # bounded memory; refill cost is one token
                bucket = TokenBucket(capacity=capacity, rate=rate)
                buckets[ip] = bucket
            return bucket

    @app.middleware("http")
    async def _rate_limit_mw(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path in EXEMPT_PATHS:
            return await call_next(request)

        if not _bucket(_client_ip(request)).take():
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
