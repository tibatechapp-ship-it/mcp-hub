"""Refusals and crashes must still carry the security header set.

The 500 path is the interesting one: Starlette's ``ServerErrorMiddleware`` sits
outside every ``@app.middleware``, so a response built by an exception handler
never travels back through the header middleware. These tests fail if the error
path starts shipping bare responses again.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mcp_hub.api import create_app
from mcp_hub.database import Catalog
from mcp_hub.metrics import REQUEST_ID_HEADER

HEADERS = (
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
)


@pytest.fixture()
def app(tmp_path):
    catalog = Catalog(tmp_path / "db.sqlite3")
    return create_app(catalog=catalog)


def _assert_headers(response):
    for name in HEADERS:
        assert response.headers.get(name), f"{name} missing on {response.status_code}"


def test_500_response_carries_security_headers(app):
    @app.get("/api/boom")
    def boom():
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/boom", headers={REQUEST_ID_HEADER: "trace-1"})

    assert response.status_code == 500
    assert response.json()["detail"] == "internal error"
    assert "kaboom" not in response.text
    _assert_headers(response)
    # The id is assigned by the observability middleware before the crash, so
    # the caller can still correlate the failure with its own logs.
    assert response.headers[REQUEST_ID_HEADER] == "trace-1"


def test_404_response_carries_security_headers(app):
    client = TestClient(app)
    response = client.get("/api/servers/999999")
    assert response.status_code == 404
    _assert_headers(response)


def test_401_advertises_the_auth_scheme(tmp_path):
    catalog = Catalog(tmp_path / "db.sqlite3")
    client = TestClient(create_app(catalog=catalog, api_key="secret"))
    response = client.get("/api/stats")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="mcp-hub"'
    _assert_headers(response)


def test_429_retry_after_matches_the_refill_rate(tmp_path, monkeypatch):
    """6 req/min refills one token every 10 s — not the old flat 60."""
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_PER_MIN", "6")
    catalog = Catalog(tmp_path / "db.sqlite3")
    client = TestClient(create_app(catalog=catalog))
    for _ in range(6):
        assert client.get("/api/stats").status_code == 200

    response = client.get("/api/stats")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "10"


def test_retry_after_scales_with_a_slow_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_PER_MIN", "10")
    catalog = Catalog(tmp_path / "db.sqlite3")
    app = create_app(catalog=catalog)
    client = TestClient(app)
    for _ in range(11):
        response = client.get("/api/stats")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "6"  # ceil(60 / 10)
