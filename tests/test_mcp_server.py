"""
Tests for the MCP server adapter (v1.2.0).

The `mcp` SDK is optional; this file skips cleanly when it is not installed.
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest

pytest.importorskip("mcp")

from mcp_hub import provenance  # noqa: E402
from mcp_hub.database import Catalog  # noqa: E402
from mcp_hub.models import Server  # noqa: E402


def _seed(db_path):
    cat = Catalog(db_path)
    pg = cat.add_server(
        Server(
            name="Postgres Helper",
            publisher="dbco",
            description="PostgreSQL query runner",
            source="t",
            popularity=100,
            categories="database",
        )
    )
    cat.save_enrichment(
        pg,
        {
            "stars": 50,
            "forks": 4,
            "license": "MIT",
            "pushed_at": "2026-01-01T00:00:00Z",
            "topics": "db,sql",
        },
    )
    se = cat.add_server(
        Server(
            name="Search Engine",
            publisher="webco",
            description="Web search API",
            source="t",
            popularity=80,
            categories="search",
        )
    )
    cat.save_enrichment(
        se,
        {
            "stars": 30,
            "forks": 1,
            "license": "Apache-2.0",
            "pushed_at": "2026-01-02T00:00:00Z",
            "topics": "search",
        },
    )
    gh = cat.add_server(
        Server(
            name="GitHub Tool",
            publisher="devco",
            description="GitHub repository manager",
            source="t",
            popularity=120,
            categories="development",
        )
    )
    cat.save_enrichment(
        gh,
        {
            "stars": 200,
            "forks": 9,
            "license": "MIT",
            "pushed_at": "2026-01-03T00:00:00Z",
            "topics": "git",
        },
    )
    return cat


@pytest.fixture
def mcp_module(tmp_path, monkeypatch):
    """Import the server module against a fresh, seeded test database."""
    db_path = tmp_path / "test.sqlite3"
    _seed(db_path)
    monkeypatch.setenv("MCP_HUB_DB", str(db_path))
    sys.modules.pop("mcp_hub.mcp_server", None)
    module = importlib.import_module("mcp_hub.mcp_server")
    yield module
    sys.modules.pop("mcp_hub.mcp_server", None)


# -- search_servers ---------------------------------------------------------
def test_search_servers_contains(mcp_module):
    results = mcp_module.search_servers("postgres")
    assert len(results) == 1
    assert results[0]["name"] == "Postgres Helper"
    assert results[0]["categories"] == "database"
    assert results[0]["stars"] == 50


def test_search_servers_fts(mcp_module):
    results = mcp_module.search_servers("postgres", mode="fts")
    assert results and results[0]["name"] == "Postgres Helper"


def test_search_servers_limit(mcp_module):
    assert len(mcp_module.search_servers("", limit=1)) == 1


def test_search_servers_limit_capped(mcp_module):
    assert len(mcp_module.search_servers("", limit=1000)) <= 100


# -- get_server -------------------------------------------------------------
def test_get_server_ok(mcp_module):
    sid = mcp_module.search_servers("postgres")[0]["id"]
    row = mcp_module.get_server(sid)
    assert row["name"] == "Postgres Helper"
    assert row["stars"] == 50
    assert row["license"] == "MIT"
    # internal columns are stripped
    assert "created_at" not in row
    assert "updated_at" not in row
    assert "raw_name" not in row


def test_get_server_missing(mcp_module):
    with pytest.raises(ValueError, match="not found"):
        mcp_module.get_server(99999)


# -- list_categories / servers_by_category ----------------------------------
def test_list_categories(mcp_module):
    names = {c["category"] for c in mcp_module.list_categories()}
    assert {"database", "search", "development"} <= names
    database = next(c for c in mcp_module.list_categories() if c["category"] == "database")
    assert database["count"] == 1


def test_servers_by_category_ok(mcp_module):
    results = mcp_module.servers_by_category("database")
    assert len(results) == 1
    assert results[0]["name"] == "Postgres Helper"
    assert results[0]["stars"] == 50


def test_servers_by_category_empty(mcp_module):
    assert mcp_module.servers_by_category("nonexistent") == []
    assert mcp_module.servers_by_category("") == []


# -- provenance tools -------------------------------------------------------
def test_provenance_conflicts_none(mcp_module):
    sid = mcp_module.search_servers("postgres")[0]["id"]
    assert mcp_module.provenance_conflicts(sid) == {}


def test_provenance_conflicts_detected(mcp_module):
    sid = mcp_module.search_servers("postgres")[0]["id"]
    provenance.record(mcp_module._catalog, sid, "description", "src-x", "different")
    conflicts = mcp_module.provenance_conflicts(sid)
    assert "description" in conflicts


def test_provenance_events(mcp_module):
    sid = mcp_module.search_servers("postgres")[0]["id"]
    events = mcp_module.provenance_events(sid)
    assert len(events) >= 1
    assert all("source" in e and "fields" in e for e in events)
    assert len(mcp_module.provenance_events(sid, limit=1)) == 1


def test_provenance_events_missing(mcp_module):
    with pytest.raises(ValueError, match="not found"):
        mcp_module.provenance_events(99999)


# -- doctor / classifier / resource ----------------------------------------
def test_doctor_report(mcp_module):
    report = mcp_module.doctor_report()
    assert report["status"] == "ok"
    assert report["db_ok"] is True
    assert "version" in report


def test_suggest_category(mcp_module):
    sid = mcp_module.search_servers("search engine")[0]["id"]
    suggestion = mcp_module.suggest_category(sid)
    assert suggestion["server_id"] == sid
    assert "search" in suggestion["suggested_categories"]


def test_suggest_category_missing(mcp_module):
    with pytest.raises(ValueError, match="not found"):
        mcp_module.suggest_category(99999)


def test_stats_resource(mcp_module):
    data = json.loads(mcp_module.stats_resource())
    assert data["records"] == 3


# -- read-only contract -----------------------------------------------------
def test_all_tools_are_read_only(mcp_module):
    """Every tool leaves the catalog row count unchanged."""
    before = mcp_module._catalog.stats()["records"]
    sid = mcp_module.search_servers("postgres")[0]["id"]
    mcp_module.search_servers("anything")
    mcp_module.search_servers("postgres", mode="fts")
    mcp_module.get_server(sid)
    mcp_module.list_categories()
    mcp_module.servers_by_category("database")
    mcp_module.provenance_conflicts(sid)
    mcp_module.provenance_events(sid)
    mcp_module.doctor_report()
    mcp_module.suggest_category(sid)
    mcp_module.stats_resource()
    after = mcp_module._catalog.stats()["records"]
    assert before == after
