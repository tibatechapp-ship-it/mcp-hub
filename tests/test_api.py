from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mcp_hub import provenance  # noqa: E402
from mcp_hub.api import create_app  # noqa: E402
from mcp_hub.database import Catalog  # noqa: E402
from mcp_hub.models import Server  # noqa: E402


def make_catalog(tmp_path):
    cat = Catalog(tmp_path / "db.sqlite3")
    sid = cat.add_server(Server(name="X", publisher="p", description="alpha", source="src-a"))
    provenance.record(cat, sid, "description", "src-b", "beta")
    cat.save_enrichment(
        sid,
        {
            "stars": 3,
            "forks": 1,
            "license": "MIT",
            "pushed_at": "2026-01-01T00:00:00Z",
            "topics": "ai",
        },
    )
    return cat, sid


def test_provenance_endpoint_modes(tmp_path):
    cat, sid = make_catalog(tmp_path)
    client = TestClient(create_app(catalog=cat))

    # fields mode (default)
    r = client.get(f"/api/servers/{sid}/provenance")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "fields"
    assert "history" in body
    assert {h["field_name"] for h in body["history"]}

    # singular alias used by older clients
    r = client.get(f"/api/server/{sid}/provenance")
    assert r.status_code == 200
    assert r.json()["mode"] == "fields"

    # conflicts mode
    r = client.get(f"/api/servers/{sid}/provenance?mode=conflicts")
    assert "description" in r.json()["conflicts"]

    # events mode
    r = client.get(f"/api/servers/{sid}/provenance?mode=events&limit=1")
    events = r.json()["events"]
    assert r.json()["mode"] == "events"
    assert len(events) == 1

    # filter by field
    r = client.get(f"/api/servers/{sid}/provenance?field=description")
    history = r.json()["history"]
    assert history
    assert all(h["field_name"] == "description" for h in history)
    assert {h["value"] for h in history} == {"alpha", "beta"}


def test_provenance_endpoint_404(tmp_path):
    cat, _ = make_catalog(tmp_path)
    client = TestClient(create_app(catalog=cat))
    r = client.get("/api/servers/9999/provenance")
    assert r.status_code == 404
