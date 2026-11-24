"""Opt-in MCP endpoint health probes (v1.3.0).

`check_endpoint` is a pure function: it never raises and never contacts a
non-public address. Every request goes through :func:`mcp_hub.safe_fetch.safe_request`,
which validates the destination and pins the connection to the verified IP, so
a catalog entry pointing at cloud metadata or ``127.0.0.1`` is reported as
blocked instead of being fetched, even if DNS changes after validation.

Persisting the result and the ``mcp-hub health`` command land in a later
release — this module deliberately stays storage-free.
"""

from __future__ import annotations

import time
from typing import Any

from .redact import redact
from .safe_fetch import UnsafeURLError, safe_request

USER_AGENT = "mcp-hub-health/1.3"
DEFAULT_TIMEOUT = 10.0


def check_endpoint(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Probe one endpoint with a HEAD request.

    Returns ``{"status", "latency_ms", "detail"}`` where ``status`` is one of
    ``up``, ``down``, ``timeout``, ``error`` or ``skipped``.
    """
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        return {"status": "skipped", "latency_ms": 0, "detail": "not an https URL"}

    started = time.monotonic()
    try:
        status, _ = safe_request(
            url, method="HEAD", timeout=timeout, headers={"User-Agent": USER_AGENT}
        )
        latency = int((time.monotonic() - started) * 1000)
        return {
            "status": "up" if 200 <= status < 400 else "down",
            "latency_ms": latency,
            "detail": f"HTTP {status}",
        }
    except UnsafeURLError as exc:
        return {
            "status": "error",
            "latency_ms": 0,
            "detail": f"blocked by SSRF protection: {redact(exc)}",
        }
    except TimeoutError as exc:
        return {
            "status": "timeout",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "detail": redact(exc) or "timed out",
        }
    except Exception as exc:  # noqa: BLE001 - HTTPError, URLError, socket errors
        latency = int((time.monotonic() - started) * 1000)
        code = getattr(exc, "code", None)
        if code in (405, 501):
            # HEAD is not supported, but the endpoint answered: it is alive.
            return {"status": "up", "latency_ms": latency, "detail": f"HTTP {code} (HEAD refused)"}
        if code is not None:
            return {"status": "down", "latency_ms": latency, "detail": f"HTTP {code}"}
        return {"status": "error", "latency_ms": latency, "detail": redact(exc)}


__all__ = ["DEFAULT_TIMEOUT", "check_endpoint"]
