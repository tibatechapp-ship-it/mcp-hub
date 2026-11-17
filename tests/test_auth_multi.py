"""Multi-key authentication and read-only scopes (v1.3.1 P1.3)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mcp_hub.api import create_app
from mcp_hub.auth import ApiKey, add_auth_middleware, keys_from_env, parse_api_keys
from mcp_hub.database import Catalog


def _client(tmp_path, keys: list[ApiKey]) -> TestClient:
    catalog = Catalog(str(tmp_path / "k.sqlite3"))
    app = create_app(catalog=catalog)
    add_auth_middleware(app, keys=keys)
    return TestClient(app)


# --- parsing ---------------------------------------------------------------


def test_parse_named_keys_and_scope():
    keys = parse_api_keys("ci:abc123:ro,admin:def456")
    assert [(k.name, k.secret, k.read_only) for k in keys] == [
        ("ci", "abc123", True),
        ("admin", "def456", False),
    ]


def test_parse_bare_secret_gets_a_generated_name():
    keys = parse_api_keys("plain-secret")
    assert keys[0].name == "key1"
    assert keys[0].secret == "plain-secret"
    assert keys[0].read_only is False


def test_parse_keeps_colons_inside_the_secret():
    keys = parse_api_keys("svc:aa:bb:cc")
    assert keys[0].name == "svc"
    assert keys[0].secret == "aa:bb:cc"


def test_parse_ignores_empty_entries():
    assert parse_api_keys("") == []
    assert parse_api_keys("  ,  ,") == []
    assert [k.name for k in parse_api_keys("a:1,,b:2")] == ["a", "b"]


def test_single_key_env_still_wins_when_multi_is_absent(monkeypatch):
    monkeypatch.delenv("MCP_HUB_API_KEYS", raising=False)
    monkeypatch.setenv("MCP_HUB_API_KEY", "legacy")
    keys = keys_from_env()
    assert [k.secret for k in keys] == ["legacy"]
    assert keys[0].name == "default"


def test_multi_key_env_takes_precedence(monkeypatch):
    monkeypatch.setenv("MCP_HUB_API_KEYS", "a:one,b:two")
    monkeypatch.setenv("MCP_HUB_API_KEY", "legacy")
    assert [k.secret for k in keys_from_env()] == ["one", "two"]


# --- enforcement -----------------------------------------------------------


def test_each_key_authenticates(tmp_path):
    client = _client(tmp_path, [ApiKey("one", "k-one"), ApiKey("two", "k-two")])
    for secret in ("k-one", "k-two"):
        assert client.get("/api/stats", headers={"X-API-Key": secret}).status_code == 200


def test_bearer_header_is_accepted(tmp_path):
    client = _client(tmp_path, [ApiKey("one", "k-one")])
    r = client.get("/api/stats", headers={"Authorization": "Bearer k-one"})
    assert r.status_code == 200


def test_wrong_key_is_rejected(tmp_path):
    client = _client(tmp_path, [ApiKey("one", "k-one")])
    assert client.get("/api/stats", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/stats").status_code == 401


def test_read_only_key_cannot_write(tmp_path):
    client = _client(tmp_path, [ApiKey("ci", "ro-key", read_only=True)])
    assert client.get("/api/stats", headers={"X-API-Key": "ro-key"}).status_code == 200
    r = client.post(
        "/api/searches",
        headers={"X-API-Key": "ro-key"},
        json={"name": "n", "query": "q"},
    )
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]


def test_write_key_can_write(tmp_path):
    client = _client(tmp_path, [ApiKey("admin", "rw-key")])
    r = client.post(
        "/api/searches",
        headers={"X-API-Key": "rw-key"},
        json={"name": "n", "query": "q"},
    )
    assert r.status_code in (200, 201)


def test_health_stays_open_for_probes(tmp_path):
    client = _client(tmp_path, [ApiKey("one", "k-one")])
    assert client.get("/api/health").status_code == 200


def test_non_api_paths_are_not_key_gated(tmp_path):
    client = _client(tmp_path, [ApiKey("one", "k-one")])
    assert client.get("/").status_code == 200


def test_matching_is_constant_time_over_all_keys(tmp_path, monkeypatch):
    """Every configured key is compared, not just up to the first hit."""
    import mcp_hub.auth as auth_mod

    seen: list[str] = []
    real = auth_mod.hmac.compare_digest

    def spy(a, b):
        seen.append(b)
        return real(a, b)

    monkeypatch.setattr(auth_mod.hmac, "compare_digest", spy)
    client = _client(tmp_path, [ApiKey("a", "k-a"), ApiKey("b", "k-b"), ApiKey("c", "k-c")])
    client.get("/api/stats", headers={"X-API-Key": "k-a"})
    assert sorted(seen) == ["k-a", "k-b", "k-c"]


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer", "bearer   "])
def test_malformed_authorization_header_is_401(tmp_path, header):
    client = _client(tmp_path, [ApiKey("one", "k-one")])
    r = client.get("/api/stats", headers={"Authorization": header})
    assert r.status_code == 401
