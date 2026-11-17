"""
Security response headers (v1.3.1 P1.2).

Defence in depth for the dashboard and the API: a strict CSP built around a
per-request nonce, no framing, no MIME sniffing, no referrer leakage. The
dashboard's inline <style>/<script> blocks carry the nonce, so the policy can
stay `'self'`-only with no `unsafe-inline`.

HSTS is emitted only when the request actually arrived over TLS (directly or
via a proxy that set `X-Forwarded-Proto: https`) — sending it on plain HTTP
would poison a localhost dev session for months.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

__all__ = ["install_security_headers", "CSP_NONCE_PLACEHOLDER", "build_csp"]

# Replaced at render time in dashboard.py by the per-request nonce.
CSP_NONCE_PLACEHOLDER = "__CSP_NONCE__"

_BASE_CSP = (
    "default-src 'self'",
    "script-src 'self' 'nonce-{nonce}'",
    "style-src 'self' 'nonce-{nonce}'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)


def build_csp(nonce: str) -> str:
    return "; ".join(_BASE_CSP).format(nonce=nonce)


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def install_security_headers(app: FastAPI) -> None:
    """Attach the header set to every response, HTML and JSON alike."""

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        nonce = secrets.token_urlsafe(16)
        # Routes that render HTML read the nonce from here.
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = build_csp(nonce)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if _is_https(request):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
