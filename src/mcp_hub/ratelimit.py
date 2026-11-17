"""
In-process token-bucket rate limiter (v1.3.0 P1.1).

Per-client-IP rate limit on all /api/* endpoints except /api/health and
/api/doctor. Uses a simple in-memory dict — no Redis, no external state. Fits
the local-first design: the API is normally bound to 127.0.0.1.

Configuration via env var:
  MCP_HUB_RATE_LIMIT_PER_MIN  — default 120 (2 req/sec sustained)
  MCP_HUB_RATE_LIMIT_STORE    — ``memory`` (default) or ``sqlite``
  MCP_HUB_RATE_LIMIT_DB       — sqlite store path (default
                                ``mcp_hub-ratelimit.sqlite3``)

A value of 0 disables rate limiting entirely (useful for tests).

Honesty note: the in-memory store is per process. With N uvicorn workers the
effective limit is N x the configured value. Set ``MCP_HUB_RATE_LIMIT_STORE=sqlite``
to share buckets between processes on one host, or run a single worker behind a
reverse proxy that enforces its own limit.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

__all__ = ["install_rate_limit", "TokenBucket", "SqliteTokenBucketStore"]

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


class SqliteTokenBucketStore:
    """
    Token buckets in SQLite, shared across processes on the same host.

    Each `take` runs inside ``BEGIN IMMEDIATE`` so two workers cannot both
    spend the same token. Uses wall-clock time (monotonic clocks are not
    comparable across processes).
    """

    def __init__(self, path: str, capacity: int, rate: float) -> None:
        if capacity <= 0 or rate <= 0:
            raise ValueError("capacity and rate must be > 0")
        self.path = path
        self.capacity = float(capacity)
        self.rate = float(rate)
        self._lock = threading.Lock()
        con = sqlite3.connect(path, timeout=5)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "CREATE TABLE IF NOT EXISTS buckets ("
                "key TEXT PRIMARY KEY, tokens REAL NOT NULL, last REAL NOT NULL)"
            )
            con.commit()
        finally:
            con.close()

    def take(self, key: str, n: float = 1.0) -> bool:
        now = time.time()
        with self._lock:
            con = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            try:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT tokens, last FROM buckets WHERE key=?", (key,)).fetchone()
                if row is None:
                    tokens = self.capacity
                else:
                    tokens, last = float(row[0]), float(row[1])
                    tokens = min(self.capacity, tokens + max(0.0, now - last) * self.rate)
                allowed = tokens >= n
                if allowed:
                    tokens -= n
                con.execute(
                    "INSERT INTO buckets(key, tokens, last) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET tokens=excluded.tokens, last=excluded.last",
                    (key, tokens, now),
                )
                con.execute("COMMIT")
                return allowed
            except Exception:
                con.execute("ROLLBACK")
                raise
            finally:
                con.close()


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


def _warn_if_multiprocess(store: str) -> None:
    """A per-process limit silently multiplies; say so at startup."""
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        workers = 1
    if workers > 1 and store == "memory":
        logging.getLogger(__name__).warning(
            "rate limit is in-memory but WEB_CONCURRENCY=%d: the effective limit is "
            "%d x MCP_HUB_RATE_LIMIT_PER_MIN. Set MCP_HUB_RATE_LIMIT_STORE=sqlite "
            "to share buckets between workers.",
            workers,
            workers,
        )


def install_rate_limit(
    app: FastAPI,
    per_minute: int | None = None,
    store: str | None = None,
    store_path: str | None = None,
) -> None:
    """
    Install a token-bucket rate limiter on /api/* (except the exempt paths).

    `per_minute` overrides the env var. Pass 0 to disable. `store` is
    ``memory`` (default) or ``sqlite``.
    """
    if per_minute is None:
        per_minute = _per_minute_from_env()
    if per_minute <= 0:
        return  # disabled

    store = (store or os.environ.get("MCP_HUB_RATE_LIMIT_STORE", "memory")).strip().lower()
    capacity = int(per_minute)
    rate = per_minute / 60.0

    sqlite_store: SqliteTokenBucketStore | None = None
    if store == "sqlite":
        path = store_path or os.environ.get("MCP_HUB_RATE_LIMIT_DB", "mcp_hub-ratelimit.sqlite3")
        sqlite_store = SqliteTokenBucketStore(path, capacity=capacity, rate=rate)
    else:
        _warn_if_multiprocess(store)

    buckets: dict[str, TokenBucket] = {}
    buckets_lock = threading.Lock()

    def _take(ip: str) -> bool:
        if sqlite_store is not None:
            return sqlite_store.take(ip)
        with buckets_lock:
            bucket = buckets.get(ip)
            if bucket is None:
                if len(buckets) >= _MAX_BUCKETS:
                    buckets.clear()  # bounded memory; refill cost is one token
                bucket = TokenBucket(capacity=capacity, rate=rate)
                buckets[ip] = bucket
        return bucket.take()

    @app.middleware("http")
    async def _rate_limit_mw(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path in EXEMPT_PATHS:
            return await call_next(request)

        if not _take(_client_ip(request)):
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
