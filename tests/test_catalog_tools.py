"""Tests for the pinned read-only catalog tool handlers."""

from __future__ import annotations

import pytest

from mcp_hub import provenance
from mcp_hub.catalog_tools import (
    PINNED_TOOL_MANIFEST_SHA256,
    RESOURCE_SPECS,
    TOOL_SPECS,
    build_resources,
    build_tools,
)
from mcp_hub.database import Catalog
from mcp_hub.mcp_stateless import tool_manifest_digest
from mcp_hub.models import Server


def _catalog(tmp_path):
    catalog = Catalog(tmp_path / "db.sqlite3")
    sid = catalog.add_server(
        Server(
            name="Postgres Helper",
            publisher="dbco",
            description="PostgreSQL query runner",
            source="test",
            popularity=100,
            categories="database",
        )
    )
    catalog.save_enrichment(
        sid,
        {
            "stars": 50,
            "forks": 4,
            "license": "MIT",
            "pushed_at": "2026-01-01T00:00:00Z",
            "topics": "db,sql",
        },
    )
    provenance.record(catalog, sid, "description", "src-b", "different")
    return catalog, sid


def test_manifest_pin_matches_current_specs():
    assert tool_manifest_digest(TOOL_SPECS) == PINNED_TOOL_MANIFEST_SHA256
    assert [spec.name for spec in TOOL_SPECS] == [
        "search_servers",
        "get_server",
        "list_categories",
        "servers_by_category",
        "provenance_conflicts",
        "provenance_events",
        "doctor_report",
        "suggest_category",
        "query_catalog",
        "route_task",
        "description_smell_audit",
        "policy_snapshot",
    ]


def test_all_handlers_are_read_only(tmp_path):
    catalog, sid = _catalog(tmp_path)
    tools = build_tools(catalog)
    before = catalog.stats()["records"]

    assert tools["search_servers"]({"query": "postgres"})[0]["name"] == "Postgres Helper"
    assert tools["search_servers"]({"query": "postgres", "mode": "fts"})
    assert tools["get_server"]({"server_id": sid})["stars"] == 50
    assert any(row["category"] == "database" for row in tools["list_categories"]({}))
    assert tools["servers_by_category"]({"category": "database"})[0]["id"] == sid
    assert "description" in tools["provenance_conflicts"]({"server_id": sid})
    assert tools["provenance_events"]({"server_id": sid, "limit": 1})
    assert tools["doctor_report"]({})["db_ok"] is True
    assert tools["suggest_category"]({"server_id": sid})["server_id"] == sid
    assert tools["query_catalog"]({"kind": "tool", "limit": 3})["count"] <= 3

    assert catalog.stats()["records"] == before


def test_handlers_validate_argument_types(tmp_path):
    catalog, _ = _catalog(tmp_path)
    tools = build_tools(catalog)

    with pytest.raises(ValueError, match="server_id"):
        tools["get_server"]({"server_id": "not-an-int"})
    with pytest.raises(ValueError, match="category"):
        tools["servers_by_category"]({"category": 123})
    with pytest.raises(ValueError, match="ready_only"):
        tools["query_catalog"]({"ready_only": "yes"})


def test_missing_server_is_reported(tmp_path):
    catalog, _ = _catalog(tmp_path)
    tools = build_tools(catalog)
    with pytest.raises(ValueError, match="not found"):
        tools["get_server"]({"server_id": 99999})


def test_stats_resource_is_json(tmp_path):
    catalog, _ = _catalog(tmp_path)
    resources = build_resources(catalog)
    assert [spec.uri for spec in RESOURCE_SPECS] == ["mcp-hub://stats"]
    payload = resources["mcp-hub://stats"]({})
    assert '"records": 1' in payload
