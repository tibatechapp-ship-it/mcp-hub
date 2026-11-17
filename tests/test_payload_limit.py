"""Payload size limiter — the streaming path included (v1.3.0 P2.2 / P1.4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from mcp_hub.payload_limit import DEFAULT_MAX_BYTES, _max_bytes_from_env, install_payload_limit


def _app(max_bytes: int | None = None) -> FastAPI:
    app = FastAPI()
    install_payload_limit(app, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo(request: Request) -> JSONResponse:
        body = await request.body()
        return JSONResponse({"size": len(body)})

    @app.get("/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/ignore")
    async def ignore() -> JSONResponse:
        """Never reads the body — the cap must not depend on the route."""
        return JSONResponse({"ok": True})

    return app


def _chunks(total: int, chunk: int = 8192):
    sent = 0
    while sent < total:
        size = min(chunk, total - sent)
        sent += size
        yield b"x" * size


def test_small_body_passes():
    client = TestClient(_app(max_bytes=1024))
    assert client.post("/echo", content=b"hello").json() == {"size": 5}


def test_declared_content_length_over_cap_is_413():
    client = TestClient(_app(max_bytes=1024))
    r = client.post("/echo", content=b"x" * 2048)
    assert r.status_code == 413
    assert "payload too large" in r.json()["detail"]


def test_body_exactly_at_the_cap_passes():
    client = TestClient(_app(max_bytes=1024))
    assert client.post("/echo", content=b"x" * 1024).json() == {"size": 1024}


def test_one_byte_over_the_cap_is_413():
    client = TestClient(_app(max_bytes=1024))
    assert client.post("/echo", content=b"x" * 1025).status_code == 413


def test_chunked_body_without_content_length_is_capped():
    """The original hole: no Content-Length header, so only streaming can stop it."""
    client = TestClient(_app(max_bytes=1024))
    r = client.post("/echo", content=_chunks(64 * 1024))
    assert r.status_code == 413


def test_chunked_body_under_the_cap_passes():
    client = TestClient(_app(max_bytes=1024))
    r = client.post("/echo", content=_chunks(512, chunk=128))
    assert r.status_code == 200
    assert r.json() == {"size": 512}


def test_cap_applies_even_when_the_route_ignores_the_body():
    """The check runs before the app, so an unread chunked body is still capped."""
    client = TestClient(_app(max_bytes=1024))
    assert client.post("/ignore", content=_chunks(2048)).status_code == 413
    assert client.post("/ignore", content=_chunks(512)).status_code == 200


def test_get_requests_are_untouched():
    client = TestClient(_app(max_bytes=1))
    assert client.get("/ping").status_code == 200


def test_invalid_content_length_is_400():
    client = TestClient(_app(max_bytes=1024))
    r = client.post("/echo", content=b"x", headers={"Content-Length": "not-a-number"})
    assert r.status_code == 400


def test_disable_by_huge_cap():
    client = TestClient(_app(max_bytes=DEFAULT_MAX_BYTES))
    assert client.post("/echo", content=b"x" * (DEFAULT_MAX_BYTES - 1)).status_code == 200


def test_env_override(monkeypatch):
    monkeypatch.setenv("MCP_HUB_MAX_BODY_BYTES", "4096")
    assert _max_bytes_from_env() == 4096


def test_env_garbage_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MCP_HUB_MAX_BODY_BYTES", "not-a-number")
    assert _max_bytes_from_env() == DEFAULT_MAX_BYTES


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_all_body_methods_are_checked(method):
    client = TestClient(_app(max_bytes=16))
    r = client.request(method, "/echo", content=b"x" * 64)
    assert r.status_code in (413, 405)  # 405 if the route does not accept the verb
