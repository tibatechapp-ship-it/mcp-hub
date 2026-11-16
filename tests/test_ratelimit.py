"""Rate limiter + payload limit tests (P1.1, P2.2)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mcp_hub.api import create_app  # noqa: E402
from mcp_hub.database import Catalog  # noqa: E402
from mcp_hub.payload_limit import install_payload_limit  # noqa: E402
from mcp_hub.ratelimit import TokenBucket  # noqa: E402


def _client(tmp_path, monkeypatch, per_minute: int = 0, max_body: int | None = None):
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_PER_MIN", str(per_minute))
    if max_body is None:
        monkeypatch.delenv("MCP_HUB_MAX_BODY_BYTES", raising=False)
    else:
        monkeypatch.setenv("MCP_HUB_MAX_BODY_BYTES", str(max_body))
    catalog = Catalog(tmp_path / "db.sqlite3")
    return TestClient(create_app(catalog=catalog))


def test_health_not_rate_limited(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, per_minute=2)
    for _ in range(20):
        assert client.get("/api/health").status_code == 200


def test_rate_limit_enforced(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, per_minute=3)
    for _ in range(3):
        assert client.get("/api/stats").status_code == 200
    r = client.get("/api/stats")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert r.json()["detail"] == "rate limit exceeded"


def test_rate_limit_disabled(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, per_minute=0)
    for _ in range(50):
        assert client.get("/api/stats").status_code == 200


def test_rate_limit_skips_non_api_paths(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, per_minute=1)
    for _ in range(10):
        assert client.get("/").status_code == 200


def test_payload_too_large(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, max_body=100)
    r = client.post(
        "/api/searches",
        content=b"x" * 200,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert "payload too large" in r.json()["detail"]


def test_payload_ok(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post("/api/searches", json={"name": "n", "query": "q"})
    assert r.status_code == 200


def test_payload_limit_ignores_get(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, max_body=10)
    assert client.get("/api/stats").status_code == 200


def test_token_bucket_drains_and_rejects():
    bucket = TokenBucket(capacity=2, rate=0.001)
    assert bucket.take()
    assert bucket.take()
    assert not bucket.take()


def test_token_bucket_rejects_bad_config():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, rate=1.0)


def test_payload_limit_standalone_app():
    app = FastAPI()

    @app.post("/echo")
    def echo(payload: dict):
        return payload

    install_payload_limit(app, max_bytes=5)
    client = TestClient(app)
    assert client.post("/echo", json={"a": "0123456789"}).status_code == 413
