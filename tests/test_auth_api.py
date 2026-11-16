import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mcp_hub.api import create_app  # noqa: E402
from mcp_hub.database import Catalog  # noqa: E402
from mcp_hub.models import Server  # noqa: E402


def make_client(tmp_path, api_key=None):
    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="s1", publisher="p", description="postgres tool", source="t"))
    return TestClient(create_app(catalog=db, api_key=api_key))


def test_no_key_means_open(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/stats").status_code == 200
    assert client.get("/api/search?q=postgres").status_code == 200


def test_key_required_when_set(tmp_path):
    client = make_client(tmp_path, api_key="secret")
    assert client.get("/api/stats").status_code == 401


def test_valid_key_passes(tmp_path):
    client = make_client(tmp_path, api_key="secret")
    r = client.get("/api/stats", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


def test_bearer_also_works(tmp_path):
    client = make_client(tmp_path, api_key="secret")
    r = client.get("/api/stats", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_health_always_open(tmp_path):
    client = make_client(tmp_path, api_key="secret")
    assert client.get("/api/health").status_code == 200


def test_search_and_saved_search_api(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/api/search?q=postgres")
    assert r.status_code == 200
    assert r.json()["count"] >= 1

    r = client.post("/api/searches", json={"name": "pg", "query": "postgres"})
    assert r.status_code == 200
    r = client.get("/api/searches")
    assert r.json()["searches"]

    r = client.post("/api/searches/run-all")
    assert r.status_code == 200
    r = client.get("/api/scheduler/runs")
    assert r.status_code == 200
