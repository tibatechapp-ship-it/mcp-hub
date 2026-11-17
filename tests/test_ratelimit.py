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


# --- shared (sqlite) store — v1.3.1 ---------------------------------------


def _sqlite_store(tmp_path, capacity=2, rate=1.0, name="rl.sqlite3"):
    from mcp_hub.ratelimit import SqliteTokenBucketStore

    return SqliteTokenBucketStore(str(tmp_path / name), capacity=capacity, rate=rate)


def test_sqlite_store_rejects_bad_config(tmp_path):
    from mcp_hub.ratelimit import SqliteTokenBucketStore

    with pytest.raises(ValueError):
        SqliteTokenBucketStore(str(tmp_path / "x.sqlite3"), capacity=0, rate=1.0)
    with pytest.raises(ValueError):
        SqliteTokenBucketStore(str(tmp_path / "x.sqlite3"), capacity=1, rate=0)


def test_sqlite_store_drains_and_rejects(tmp_path):
    store = _sqlite_store(tmp_path)
    assert store.take("1.2.3.4") is True
    assert store.take("1.2.3.4") is True
    assert store.take("1.2.3.4") is False


def test_sqlite_store_is_shared_between_instances(tmp_path):
    """Two workers, two store objects, one database file — one budget."""
    first = _sqlite_store(tmp_path)
    second = _sqlite_store(tmp_path)
    assert first.take("9.9.9.9") is True
    assert second.take("9.9.9.9") is True
    assert first.take("9.9.9.9") is False
    assert second.take("9.9.9.9") is False


def test_sqlite_store_keys_are_independent(tmp_path):
    store = _sqlite_store(tmp_path, capacity=1)
    assert store.take("a") is True
    assert store.take("b") is True
    assert store.take("a") is False


def test_sqlite_store_refills_over_time(tmp_path, monkeypatch):
    import mcp_hub.ratelimit as rl

    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    store = _sqlite_store(tmp_path, capacity=1, rate=0.5)  # one token / 2s
    assert store.take("ip") is True
    assert store.take("ip") is False
    clock[0] += 2.0
    assert store.take("ip") is True


def test_install_rate_limit_uses_the_sqlite_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_STORE", "sqlite")
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_DB", str(tmp_path / "shared.sqlite3"))
    catalog = Catalog(tmp_path / "db.sqlite3")
    app = create_app(catalog=catalog)
    from mcp_hub.ratelimit import install_rate_limit

    install_rate_limit(app, per_minute=2, store="sqlite", store_path=str(tmp_path / "s2.sqlite3"))
    client = TestClient(app)
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/stats").status_code == 429


def test_multiprocess_warning_is_logged(tmp_path, monkeypatch, caplog):
    import logging

    from mcp_hub.ratelimit import install_rate_limit

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    app = FastAPI()
    with caplog.at_level(logging.WARNING, logger="mcp_hub.ratelimit"):
        install_rate_limit(app, per_minute=60, store="memory")
    assert any("WEB_CONCURRENCY=4" in rec.getMessage() for rec in caplog.records)


def test_no_warning_with_a_single_worker(monkeypatch, caplog):
    import logging

    from mcp_hub.ratelimit import install_rate_limit

    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    with caplog.at_level(logging.WARNING, logger="mcp_hub.ratelimit"):
        install_rate_limit(FastAPI(), per_minute=60)
    assert not caplog.records


def test_forwarded_for_uses_the_proxy_appended_hop(tmp_path, monkeypatch):
    """A caller must not be able to pick its own bucket by spoofing XFF."""
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_PER_MIN", "2")
    catalog = Catalog(tmp_path / "db.sqlite3")
    client = TestClient(create_app(catalog=catalog), client=("127.0.0.1", 50000))

    xff = {"X-Forwarded-For": "1.1.1.1, 7.7.7.7"}
    assert client.get("/api/stats", headers=xff).status_code == 200
    assert client.get("/api/stats", headers=xff).status_code == 200
    # Same real hop, different client-supplied prefix: still the same bucket.
    spoofed = client.get("/api/stats", headers={"X-Forwarded-For": "2.2.2.2, 7.7.7.7"})
    assert spoofed.status_code == 429
    # A genuinely different peer gets its own bucket.
    assert client.get("/api/stats", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200


def test_forwarded_for_is_ignored_from_a_remote_peer(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HUB_RATE_LIMIT_PER_MIN", "1")
    catalog = Catalog(tmp_path / "db.sqlite3")
    client = TestClient(create_app(catalog=catalog), client=("203.0.113.9", 50000))

    assert client.get("/api/stats", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    # XFF from a non-loopback peer is not trusted, so the real socket address
    # is still the bucket key and the limit holds.
    assert client.get("/api/stats", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429
