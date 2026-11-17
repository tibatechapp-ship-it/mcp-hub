"""
Minimal in-process metrics + request IDs (v1.3.1 P3.1).

No dependency, no exporter: a small registry rendered in the Prometheus text
exposition format at `/metrics`. That is enough to answer the questions an
operator actually asks — how many requests, how many were refused, how slow is
search — without pulling `prometheus_client` into a local-first tool.

Counters are per process. With N workers, scrape each worker (the same caveat as
the rate limiter) or aggregate in the scraper.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from threading import Lock

from fastapi import FastAPI, Request
from starlette.responses import PlainTextResponse, Response

__all__ = ["Metrics", "METRICS", "install_observability", "REQUEST_ID_HEADER"]

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class Metrics:
    """Thread-safe counters + a latency histogram-free summary."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._duration_sum = 0.0
        self._duration_count = 0

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def observe(self, seconds: float) -> None:
        with self._lock:
            self._duration_sum += seconds
            self._duration_count += 1

    def value(self, name: str, labels: dict[str, str] | None = None) -> float:
        key = (name, tuple(sorted((labels or {}).items())))
        with self._lock:
            return self._counters.get(key, 0.0)

    def render(self, extra: dict[str, float] | None = None) -> str:
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            duration_sum = self._duration_sum
            duration_count = self._duration_count

        grouped: dict[str, list[tuple[tuple[tuple[str, str], ...], float]]] = {}
        for (name, labels), value in counters.items():
            grouped.setdefault(name, []).append((labels, value))

        for name in sorted(grouped):
            lines.append(f"# TYPE {name} counter")
            for labels, value in sorted(grouped[name]):
                rendered = ",".join(f'{k}="{v}"' for k, v in labels)
                suffix = f"{{{rendered}}}" if rendered else ""
                lines.append(f"{name}{suffix} {value:g}")

        lines.append("# TYPE mcp_hub_request_duration_seconds summary")
        lines.append(f"mcp_hub_request_duration_seconds_sum {duration_sum:.6f}")
        lines.append(f"mcp_hub_request_duration_seconds_count {duration_count}")

        for name, value in sorted((extra or {}).items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value:g}")

        return "\n".join(lines) + "\n"


# One registry per process; imported by the API and by tests.
METRICS = Metrics()


def _request_id(request: Request) -> str:
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    if supplied and _VALID_REQUEST_ID.match(supplied):
        return supplied
    return uuid.uuid4().hex


def install_observability(app: FastAPI, metrics: Metrics | None = None) -> None:
    """Record every request and echo a request id. `/metrics` is not counted."""
    registry = metrics or METRICS

    @app.middleware("http")
    async def _observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            registry.inc(
                "mcp_hub_requests_total",
                {"method": request.method, "status": "500", "path": _route(request)},
            )
            registry.observe(time.perf_counter() - started)
            raise
        elapsed = time.perf_counter() - started
        if request.url.path != "/metrics":
            registry.inc(
                "mcp_hub_requests_total",
                {
                    "method": request.method,
                    "status": str(response.status_code),
                    "path": _route(request),
                },
            )
            registry.observe(elapsed)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def _route(request: Request) -> str:
    """The matched route template, so label cardinality stays bounded."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def metrics_endpoint(catalog=None) -> PlainTextResponse:
    extra: dict[str, float] = {}
    if catalog is not None:
        try:
            stats = catalog.stats()
            extra["mcp_hub_records"] = float(stats.get("records", 0))
            extra["mcp_hub_duplicate_candidates"] = float(stats.get("duplicate_candidates", 0))
            extra["mcp_hub_enriched"] = float(stats.get("enriched", 0))
        except Exception:  # noqa: BLE001 — metrics must never break the endpoint
            pass
    return PlainTextResponse(METRICS.render(extra), media_type="text/plain; version=0.0.4")
