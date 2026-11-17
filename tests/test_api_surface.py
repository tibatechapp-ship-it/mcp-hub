"""The endpoints a user actually touches: saved searches, categories, alerts.

Every mutating route gets its 400/404 path exercised too — an endpoint that only
has a happy-path test is an endpoint whose error contract is a guess.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mcp_hub.api import create_app  # noqa: E402
from mcp_hub.database import Catalog  # noqa: E402
from mcp_hub.models import Server  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="alpha", publisher="acme", description="alpha tool", source="t"))
    db.add_server(Server(name="beta", publisher="acme", description="beta tool", source="t"))
    return TestClient(create_app(catalog=db))


def test_saved_search_lifecycle(client):
    assert client.get("/api/searches").json()["searches"] == []

    r = client.post("/api/searches", json={"name": "alphas", "query": "alpha"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert [s["name"] for s in client.get("/api/searches").json()["searches"]] == ["alphas"]

    run = client.post("/api/searches/alphas/run")
    assert run.status_code == 200
    assert run.json()["match_count"] == 1
    assert run.json()["new_count"] == 1

    assert client.delete("/api/searches/alphas").status_code == 200
    assert client.get("/api/searches").json()["searches"] == []


def test_saved_search_validation(client):
    assert client.post("/api/searches", json={"name": "x"}).status_code == 400
    assert client.post("/api/searches", json={"query": "x"}).status_code == 400
    assert client.delete("/api/searches/missing").status_code == 404
    assert client.post("/api/searches/missing/run").status_code == 404


def test_run_all_and_scheduler_runs(client):
    client.post("/api/searches", json={"name": "alphas", "query": "alpha"})
    res = client.post("/api/searches/run-all")
    assert res.status_code == 200
    assert isinstance(res.json(), dict)

    runs = client.get("/api/scheduler/runs?limit=5")
    assert runs.status_code == 200
    assert isinstance(runs.json()["runs"], list)

    assert client.get("/api/scheduler/runs?limit=0").status_code == 422
    assert client.get("/api/scheduler/runs?limit=101").status_code == 422


def test_categories_roundtrip(client):
    body = client.get("/api/categories").json()
    assert set(body) == {"custom", "overrides"}

    r = client.post(
        "/api/categories",
        json={"name": "databases", "terms": ["postgres", "sqlite"], "notes": "db tools"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    names = [c["name"] for c in client.get("/api/categories").json()["custom"]]
    assert "databases" in names

    # a term list and a comma string must both be accepted
    r = client.post("/api/categories", json={"name": "vectors", "terms": "pinecone,qdrant"})
    assert r.status_code == 200

    # empty name is rejected by the catalog and surfaced as 400, not 500
    assert client.post("/api/categories", json={"name": ""}).status_code == 400

    classified = client.post("/api/categories/classify")
    assert classified.status_code == 200

    exported = client.get("/api/categories/export")
    assert exported.status_code == 200
    assert "databases" in [c["name"] for c in exported.json()["categories"]]

    imported = client.post(
        "/api/categories/import", json={"replace": True, "data": exported.json()}
    )
    assert imported.status_code == 200

    assert client.post("/api/categories/import", json={"data": "not-a-dict"}).status_code == 400


def test_alerts_roundtrip(client):
    assert client.get("/api/alerts").json()["alerts"] == []

    r = client.post(
        "/api/alerts", json={"channel": "webhook", "target": "https://example.com/h", "query": "x"}
    )
    assert r.status_code == 200 and r.json()["id"] > 0
    assert client.get("/api/alerts").json()["alerts"][0]["channel"] == "webhook"

    # no target -> 400 (a webhook without a URL would fail at dispatch time)
    assert client.post("/api/alerts", json={"channel": "webhook"}).status_code == 400


def test_cors_origins_come_from_the_environment(monkeypatch):
    from mcp_hub.api import DEFAULT_CORS_ORIGINS, _cors_origins

    monkeypatch.delenv("MCP_HUB_CORS_ORIGINS", raising=False)
    assert _cors_origins() == list(DEFAULT_CORS_ORIGINS)

    monkeypatch.setenv("MCP_HUB_CORS_ORIGINS", " https://a.example , ,https://b.example ")
    assert _cors_origins() == ["https://a.example", "https://b.example"]


def test_unhandled_error_is_generic_and_redacted(tmp_path, monkeypatch):
    db = Catalog(tmp_path / "db.sqlite3")

    def boom():
        raise ValueError("leaked ghp_abcdefghijklmnopqrstuvwxyz012345")

    monkeypatch.setattr(db, "stats", boom)
    client = TestClient(create_app(catalog=db), raise_server_exceptions=False)

    r = client.get("/api/stats")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "internal error"
    assert body["type"] == "ValueError"
    assert "ghp_" not in r.text


def test_health_reports_a_broken_database(tmp_path, monkeypatch):
    db = Catalog(tmp_path / "db.sqlite3")

    def boom():
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(db, "connect", boom)
    client = TestClient(create_app(catalog=db), raise_server_exceptions=False)

    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["status"] == "error"


def test_fts_page_past_the_end_is_empty(tmp_path):
    """FTS hits and LIKE hits are different sets; paging must not switch sets."""
    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="alpha", publisher="acme", description="alpha tool", source="t"))
    # LIKE matches "alpha" inside "alphabet", FTS tokenizes it as one word and
    # does not. So the FTS set is 1 row while the LIKE set is 2.
    db.add_server(
        Server(name="alphabet", publisher="acme", description="alphabet soup", source="t")
    )
    client = TestClient(create_app(catalog=db))

    page0 = client.get("/api/search?q=alpha&fts=1&limit=1&offset=0").json()
    # FTS matches the token "alpha" only; LIKE also matches inside "alphabet".
    assert [r["name"] for r in page0["results"]] == ["alpha"]
    assert page0["next_offset"] is None

    beyond = client.get("/api/search?q=alpha&fts=1&limit=1&offset=1").json()
    assert beyond["results"] == []
    assert beyond["next_offset"] is None

    # the LIKE path still sees both rows
    like = client.get("/api/search?q=alpha&limit=10").json()
    assert {r["name"] for r in like["results"]} == {"alpha", "alphabet"}
