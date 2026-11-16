"""
mcp-hub as an MCP server (v1.2.0).

Exposes the catalog as read-only tools that Cursor (and other MCP hosts) can
call directly. No writes, no network calls, no shell execution.

Run standalone:
    python -m mcp_hub.mcp_server

Register with Cursor: see README section "Use mcp-hub inside Cursor".
"""

from __future__ import annotations

import json
import os
from typing import Any

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _MCPApp
except ImportError:  # pragma: no cover - exercised by the mcp 1.x CI lane
    from mcp.server.fastmcp import FastMCP as _MCPApp

from . import doctor, provenance, taxonomy
from .database import Catalog

mcp = _MCPApp(
    name="mcp-hub",
    instructions=(
        "Local MCP catalog. Use these read-only tools to search, inspect, "
        "classify, and audit the MCP server registry. All tools are read-only."
    ),
)

_DB_PATH = os.environ.get("MCP_HUB_DB", "mcp_hub.sqlite3")
_catalog = Catalog(_DB_PATH)


# -- helpers (read-only) ----------------------------------------------------
def _enriched(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge enrichment columns (stars/forks/license/topics) into server rows."""
    if not rows:
        return []
    ids = [int(r["id"]) for r in rows]
    placeholders = ",".join("?" for _ in ids)
    with _catalog.connect() as con:
        extra_rows = con.execute(
            "SELECT server_id, stars, forks, license, pushed_at, topics FROM enrichments "
            f"WHERE server_id IN ({placeholders})",
            ids,
        ).fetchall()
    extra = {int(r["server_id"]): dict(r) for r in extra_rows}
    merged: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        enrichment = extra.get(int(data["id"]), {})
        data["stars"] = int(enrichment.get("stars") or 0)
        data["forks"] = int(enrichment.get("forks") or 0)
        data["license"] = enrichment.get("license") or ""
        data["pushed_at"] = enrichment.get("pushed_at") or ""
        data["topics"] = enrichment.get("topics") or ""
        merged.append(data)
    return merged


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "publisher": row.get("publisher", ""),
        "description": (row.get("description") or "")[:300],
        "categories": row.get("categories", ""),
        "stars": int(row.get("stars") or 0),
        "popularity": int(row.get("popularity") or 0),
        "endpoint_url": row.get("endpoint_url", ""),
        "repository_url": row.get("repository_url", ""),
    }


def _categories() -> list[dict[str, Any]]:
    counts: dict[str, int] = {c["name"]: 0 for c in _catalog.custom_categories()}
    with _catalog.connect() as con:
        rows = con.execute(
            "SELECT categories FROM servers WHERE archived=0 AND categories != ''"
        ).fetchall()
    for row in rows:
        for cat in (row["categories"] or "").split(","):
            name = cat.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"category": name, "count": count} for name, count in ordered]


# -- tools ------------------------------------------------------------------
@mcp.tool()
def search_servers(query: str, limit: int = 20, mode: str = "contains") -> list[dict]:
    """Search the catalog by name, publisher, description, category, or topic."""
    limit = max(1, min(limit, 100))
    rows = _catalog.search_fts(query, limit) if mode == "fts" else _catalog.search(query, limit)
    return [_summary(row) for row in _enriched(rows)]


@mcp.tool()
def get_server(server_id: int) -> dict:
    """Get full details for one server by its database ID."""
    row = _catalog.get_server(server_id)
    if not row:
        raise ValueError(f"server {server_id} not found")
    merged = _enriched([dict(row)])[0]
    for key in ("created_at", "updated_at", "raw_name"):
        merged.pop(key, None)
    return merged


@mcp.tool()
def list_categories() -> list[dict]:
    """List all categories with their server counts."""
    return _categories()


@mcp.tool()
def servers_by_category(category: str, limit: int = 20) -> list[dict]:
    """List servers in a specific category (e.g. 'database', 'search', 'devtools')."""
    limit = max(1, min(limit, 100))
    name = (category or "").strip().lower()
    if not name:
        return []
    with _catalog.connect() as con:
        rows = con.execute(
            "SELECT * FROM servers WHERE archived=0 "
            "AND (',' || categories || ',') LIKE ? "
            "ORDER BY popularity DESC, id DESC LIMIT ?",
            (f"%,{name},%", limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "publisher": r.get("publisher", ""),
            "stars": int(r.get("stars") or 0),
            "description": (r.get("description") or "")[:200],
        }
        for r in _enriched([dict(row) for row in rows])
    ]


@mcp.tool()
def provenance_conflicts(server_id: int) -> dict:
    """Detect fields where different sources disagree for a given server."""
    if not _catalog.get_server(server_id):
        raise ValueError(f"server {server_id} not found")
    return provenance.field_conflicts(_catalog, server_id)


@mcp.tool()
def provenance_events(server_id: int, limit: int = 10) -> list[dict]:
    """Show the audit trail (event history) for a server."""
    if not _catalog.get_server(server_id):
        raise ValueError(f"server {server_id} not found")
    limit = max(1, min(limit, 50))
    return provenance.event_history(_catalog, server_id, limit)


@mcp.tool()
def doctor_report() -> dict:
    """Run read-only health checks on the catalog (no repairs)."""
    return doctor.run(_catalog)


@mcp.tool()
def suggest_category(server_id: int) -> dict:
    """Suggest categories for a server using the deterministic classifier."""
    row = _catalog.get_server(server_id)
    if not row:
        raise ValueError(f"server {server_id} not found")
    custom = {c["name"]: c.get("terms", []) for c in _catalog.custom_categories()}
    suggested = taxonomy.classify(dict(row), custom_rules=custom)
    return {
        "server_id": server_id,
        "name": row["name"],
        "current_categories": row.get("categories", ""),
        "suggested_categories": suggested,
    }


# -- resource ---------------------------------------------------------------
@mcp.resource("mcp-hub://stats")
def stats_resource() -> str:
    """Live catalog statistics (JSON)."""
    return json.dumps(_catalog.stats(), ensure_ascii=False, indent=2)


# -- entry point ------------------------------------------------------------
def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
