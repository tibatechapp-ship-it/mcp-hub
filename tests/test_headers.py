"""Security headers and CSP nonce (v1.3.1 P1.2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mcp_hub.api import create_app
from mcp_hub.database import Catalog
from mcp_hub.headers import CSP_NONCE_PLACEHOLDER, build_csp


def _client(tmp_path, **kwargs) -> TestClient:
    catalog = Catalog(str(tmp_path / "h.sqlite3"))
    return TestClient(create_app(catalog=catalog, **kwargs))


def test_headers_on_json_endpoint(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "geolocation=()" in r.headers["Permissions-Policy"]
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-inline" not in csp


def test_dashboard_gets_a_nonce_that_matches_the_header(tmp_path):
    client = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert CSP_NONCE_PLACEHOLDER not in body, "placeholder leaked into the response"
    csp = r.headers["Content-Security-Policy"]
    # Both inline blocks must carry the exact nonce advertised in the header.
    assert body.count('nonce="') == 2
    start = csp.index("'nonce-") + len("'nonce-")
    nonce = csp[start : csp.index("'", start)]
    assert nonce
    assert body.count(f'nonce="{nonce}"') == 2


def test_nonce_is_unique_per_request(tmp_path):
    client = _client(tmp_path)
    first = client.get("/").headers["Content-Security-Policy"]
    second = client.get("/").headers["Content-Security-Policy"]
    assert first != second


def test_hsts_only_over_tls(tmp_path):
    client = _client(tmp_path)
    assert "Strict-Transport-Security" not in client.get("/api/health").headers
    proxied = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=31536000" in proxied.headers["Strict-Transport-Security"]


def test_headers_present_on_rejected_requests(tmp_path):
    """A 401/429 must not be the one response that skips the header set."""
    client = _client(tmp_path, api_key="secret-key")
    r = client.get("/api/stats")
    assert r.status_code == 401
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in r.headers


def test_build_csp_injects_the_nonce():
    csp = build_csp("abc123")
    assert "'nonce-abc123'" in csp
    assert "{nonce}" not in csp
