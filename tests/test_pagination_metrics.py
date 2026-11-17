"""Pagination, /metrics and request ids (v1.3.1 P3.1, P3.2)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mcp_hub.api import create_app
from mcp_hub.database import Catalog
from mcp_hub.metrics import METRICS, Metrics
from mcp_hub.models import Server


def _catalog(tmp_path, count: int = 5) -> Catalog:
    catalog = Catalog(tmp_path / "p.sqlite3")
    catalog.bulk_add(
        [
            Server(name=f"srv-{i:02d}", publisher="acme", description=f"server {i}", source="t")
            for i in range(count)
        ]
    )
    return catalog


def _client(tmp_path, count: int = 5) -> TestClient:
    return TestClient(create_app(catalog=_catalog(tmp_path, count)))


# --- pagination ------------------------------------------------------------


def test_first_page_reports_a_next_offset(tmp_path):
    client = _client(tmp_path, count=5)
    body = client.get("/api/search", params={"q": "srv", "limit": 2}).json()
    assert body["count"] == 2
    assert body["offset"] == 0
    assert body["next_offset"] == 2


def test_pages_do_not_overlap(tmp_path):
    client = _client(tmp_path, count=5)
    seen: list[int] = []
    offset = 0
    while True:
        body = client.get("/api/search", params={"q": "srv", "limit": 2, "offset": offset}).json()
        seen.extend(r["id"] for r in body["results"])
        if body["next_offset"] is None:
            break
        offset = body["next_offset"]
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_last_page_has_no_next_offset(tmp_path):
    client = _client(tmp_path, count=3)
    body = client.get("/api/search", params={"q": "srv", "limit": 10}).json()
    assert body["count"] == 3
    assert body["next_offset"] is None


def test_empty_query_paginates_too(tmp_path):
    client = _client(tmp_path, count=4)
    first = client.get("/api/search", params={"limit": 2}).json()
    second = client.get("/api/search", params={"limit": 2, "offset": 2}).json()
    assert first["next_offset"] == 2
    assert second["next_offset"] is None
    assert {r["id"] for r in first["results"]}.isdisjoint({r["id"] for r in second["results"]})


def test_offset_beyond_the_end_is_empty(tmp_path):
    client = _client(tmp_path, count=2)
    body = client.get("/api/search", params={"q": "srv", "offset": 50}).json()
    assert body["results"] == []
    assert body["next_offset"] is None


def test_negative_offset_is_rejected(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/search", params={"offset": -1}).status_code == 422


def test_fts_search_paginates(tmp_path):
    client = _client(tmp_path, count=5)
    body = client.get("/api/search", params={"q": "srv", "limit": 2, "fts": True}).json()
    assert body["count"] == 2
    assert body["next_offset"] == 2


# --- metrics ---------------------------------------------------------------


def test_metrics_endpoint_is_prometheus_text(tmp_path):
    client = _client(tmp_path)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "mcp_hub_requests_total" in body
    assert "mcp_hub_request_duration_seconds_sum" in body
    assert "mcp_hub_records" in body


def test_requests_are_counted_by_status(tmp_path):
    registry = Metrics()
    app = create_app(catalog=_catalog(tmp_path))
    from mcp_hub.metrics import install_observability

    install_observability(app, metrics=registry)
    client = TestClient(app)
    client.get("/api/health")
    client.get("/api/servers/999999")
    assert (
        registry.value(
            "mcp_hub_requests_total", {"method": "GET", "status": "200", "path": "/api/health"}
        )
        == 1
    )
    assert (
        registry.value(
            "mcp_hub_requests_total",
            {"method": "GET", "status": "404", "path": "/api/servers/{server_id}"},
        )
        == 1
    )


def test_metrics_scrape_is_not_counted(tmp_path):
    client = _client(tmp_path)
    client.get("/metrics")
    body = client.get("/metrics").text
    assert 'path="/metrics"' not in body


def test_label_cardinality_uses_route_templates(tmp_path):
    client = _client(tmp_path, count=2)
    client.get("/api/servers/1")
    client.get("/api/servers/2")
    body = client.get("/metrics").text
    assert 'path="/api/servers/{server_id}"' in body
    assert 'path="/api/servers/1"' not in body


def test_metrics_survives_a_broken_catalog(tmp_path):
    class Broken:
        def stats(self):
            raise RuntimeError("boom")

    from mcp_hub.metrics import metrics_endpoint

    r = metrics_endpoint(Broken())
    assert r.status_code == 200
    assert "mcp_hub_requests_total" in r.body.decode()


# --- request ids -----------------------------------------------------------


def test_request_id_is_generated(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/health")
    assert len(r.headers["X-Request-ID"]) >= 16


def test_request_id_is_echoed_when_valid(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/health", headers={"X-Request-ID": "trace-abc.123"})
    assert r.headers["X-Request-ID"] == "trace-abc.123"


def test_request_id_with_illegal_characters_is_replaced(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/health", headers={"X-Request-ID": "bad id\nwith spaces"})
    assert r.headers["X-Request-ID"] != "bad id\nwith spaces"


def test_request_ids_are_unique(tmp_path):
    client = _client(tmp_path)
    ids = {client.get("/api/health").headers["X-Request-ID"] for _ in range(5)}
    assert len(ids) == 5


def test_global_registry_is_shared(tmp_path):
    """The module-level registry is what the /metrics route renders."""
    before = METRICS.value(
        "mcp_hub_requests_total", {"method": "GET", "status": "200", "path": "/api/health"}
    )
    client = _client(tmp_path)
    client.get("/api/health")
    after = METRICS.value(
        "mcp_hub_requests_total", {"method": "GET", "status": "200", "path": "/api/health"}
    )
    assert after > before
