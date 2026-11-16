from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse


def get_api_key_from_env() -> str | None:
    return os.environ.get("MCP_HUB_API_KEY", "").strip() or None


def _extract_bearer(header: str | None) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def add_auth_middleware(app, api_key: str | None) -> None:
    if not api_key:
        return

    @app.middleware("http")
    async def _check_key(request: Request, call_next):  # noqa: ANN001
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path == "/api/health":
            return await call_next(request)
        provided = request.headers.get("x-api-key") or _extract_bearer(
            request.headers.get("authorization")
        )
        if not provided or not hmac.compare_digest(provided, api_key):
            return JSONResponse(status_code=401, content={"detail": "missing or invalid API key"})
        return await call_next(request)
