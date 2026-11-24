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


def _mcp_request(method: str, params: dict | None = None) -> dict:
    from mcp_hub.mcp_stateless import MCP_PROTOCOL_VERSION, META_PROTOCOL_VERSION

    payload = dict(params or {})
    payload.setdefault("_meta", {META_PROTOCOL_VERSION: MCP_PROTOCOL_VERSION})
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": payload}


def _mcp_headers(method: str) -> dict:
    from mcp_hub.mcp_stateless import MCP_PROTOCOL_VERSION

    return {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION, "Mcp-Method": method}


def test_mcp_endpoint_lists_pinned_tools(tmp_path):
    from mcp_hub.catalog_tools import TOOL_SPECS

    cat, _ = make_catalog(tmp_path)
    client = TestClient(create_app(catalog=cat))
    r = client.post("/mcp", headers=_mcp_headers("tools/list"), json=_mcp_request("tools/list"))
    assert r.status_code == 200
    names = [tool["name"] for tool in r.json()["result"]["tools"]]
    assert names == [spec.name for spec in TOOL_SPECS]


def test_mcp_endpoint_header_mismatch_is_400(tmp_path):
    cat, _ = make_catalog(tmp_path)
    client = TestClient(create_app(catalog=cat))
    headers = _mcp_headers("tools/list")
    headers["Mcp-Name"] = "wrong"
    r = client.post("/mcp", headers=headers, json=_mcp_request("tools/list"))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32020


def test_mcp_endpoint_is_key_gated(tmp_path):
    cat, _ = make_catalog(tmp_path)
    client = TestClient(create_app(catalog=cat, api_key="secret"))
    r = client.post("/mcp", headers=_mcp_headers("tools/list"), json=_mcp_request("tools/list"))
    assert r.status_code == 401
