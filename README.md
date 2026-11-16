# mcp-hub

[![PyPI](https://img.shields.io/pypi/v/mcp-hub.svg)](https://pypi.org/project/mcp-hub/)
[![Python](https://img.shields.io/pypi/pyversions/mcp-hub.svg)](https://pypi.org/project/mcp-hub/)
[![CI](https://github.com/tibatechapp-ship-it/mcp-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/tibatechapp-ship-it/mcp-hub/actions/workflows/ci.yml)

Local-first catalog for MCP servers: sync from the MCP registry, enrich from GitHub, classify, search (LIKE/FTS5), save alerts/searches, and export to JSON/CSV/Markdown. Ships as a pure-Python + SQLite core with an optional FastAPI dashboard.

- **Version:** 1.2.0
- **DB:** SQLite (portable, single file)
- **API:** optional FastAPI + dashboard (`pip install "mcp-hub[api]"`)
- **MCP server:** `pip install "mcp-hub[mcp-server]"` → `mcp-hub-server`
- **Docker:** `ghcr.io/tibatechapp-ship-it/mcp-hub`

## Why

For a solo dev or small team, you often want one local index of MCP servers you can query, diff, enrich, and publish — without running a heavy platform. `mcp-hub` stays local-first by default and scales to thousands of records with batch ingestion and indexed lookups.

## Quick start (Docker)

```bash
docker run --rm -v $PWD/data:/data -p 127.0.0.1:8000:8000 \
  ghcr.io/tibatechapp-ship-it/mcp-hub:latest
# open http://127.0.0.1:8000
```

Optional API key:

```bash
docker run --rm -v $PWD/data:/data -p 127.0.0.1:8000:8000 \
  -e MCP_HUB_API_KEY=secret \
  ghcr.io/tibatechapp-ship-it/mcp-hub:latest
```

## Quick start (pip)

```bash
python -m venv .venv && source .venv/bin/activate
pip install "mcp-hub[api]"

mcp-hub --db hub.sqlite3 sync-registry --limit 100
mcp-hub --db hub.sqlite3 enrich-github --limit 50
mcp-hub --db hub.sqlite3 classify
mcp-hub --db hub.sqlite3 search "postgres" --fts
mcp-hub --db hub.sqlite3 serve --host 127.0.0.1 --port 8000
```

Working from a checkout instead? `pip install -e ".[api,dev]"`.

## CLI highlights

```bash
mcp-hub stats
mcp-hub doctor --json
mcp-hub sync-registry --limit 500
mcp-hub import-text servers.txt --source curated
mcp-hub search "browser" --limit 20
mcp-hub enrich-github --limit 200
mcp-hub classify --overwrite
mcp-hub export markdown catalog.md
mcp-hub save-search pg "postgres"
mcp-hub run-all-searches
mcp-hub export-categories categories.json
mcp-hub import-categories categories.json
mcp-hub add-alert webhook https://example.com/hook --query pg
```

## Provenance (two-layer design)

`mcp-hub` tracks provenance in **two complementary tables**:

| Table | Purpose | Query pattern |
|---|---|---|
| `provenance` | audit trail — one row per event, full `payload_json` | "What changed when?" |
| `field_provenance` | queryable index — one row per `(field, source)` | "Who wrote this field?" |

Both tables are written **atomically inside the same transaction** as the catalog
write, so audit and index can never drift apart. `field_provenance` is backfilled
automatically from `provenance` when an older database is opened (one-time,
guarded by `PRAGMA user_version`).

```bash
# Fast field-level view (indexed, O(log n))
mcp-hub provenance 42

# Filter to one field
mcp-hub provenance 42 --field description

# Fast conflict detection (fields whose sources disagree)
mcp-hub provenance 42 --conflicts-only

# Audit trail (event history)
mcp-hub provenance 42 --events --limit 20
```

REST equivalent: `GET /api/servers/{id}/provenance?mode=fields|conflicts|events&field=...`
(`/api/server/{id}/provenance` is kept as an alias). `provenance.history()` and
`provenance.conflicts()` stay public and now read the indexed layer.

## API (optional)

| Endpoint | Description |
|---|---|
| `GET /api/health` | health + DB ping (always open) |
| `GET /api/stats` | catalog stats |
| `GET /api/search?q=&fts=` | search servers |
| `GET /api/servers/{id}` | server details |
| `GET /api/servers/{id}/provenance` | provenance: `?mode=fields\|conflicts\|events&field=` |
| `GET/POST/DELETE /api/searches*` | saved searches |
| `POST /api/searches/run-all` | run all saved searches |
| `GET /api/scheduler/runs` | recent batch runs |
| `GET/POST /api/categories*` | custom categories & overrides |
| `GET/POST /api/alerts` | alert channels |

When `MCP_HUB_API_KEY` (or `--api-key`) is set, all `/api/*` endpoints except `/api/health` require `X-API-Key` or `Authorization: Bearer <key>`.

## Use mcp-hub inside Cursor (MCP server)

`mcp-hub` can run as a local **MCP server**, exposing its catalog as read-only
tools that Cursor's Agent (and any MCP host) can call directly.

### Install

```bash
pip install -e ".[mcp-server]"
```

### Register with Cursor

Option A — manual. Add to `.cursor/mcp.json` (copy of `examples/cursor-mcp.json`):

```json
{
  "mcpServers": {
    "mcp-hub": {
      "command": "/absolute/path/to/your/venv/bin/python",
      "args": ["-m", "mcp_hub.mcp_server"],
      "env": { "MCP_HUB_DB": "/absolute/path/to/mcp_hub.sqlite3" }
    }
  }
}
```

Option B — if you also have the `fastmcp` CLI installed:

```bash
fastmcp install cursor src/mcp_hub/mcp_server.py --project .
```

### Available tools (all read-only)

| Tool | What it does |
|---|---|
| `search_servers` | Search by name, publisher, description, category, topic |
| `get_server` | Full details for one server (incl. enrichment) |
| `list_categories` | Category distribution |
| `servers_by_category` | Servers in a given category |
| `provenance_conflicts` | Fields where sources disagree |
| `provenance_events` | Audit trail for a server |
| `doctor_report` | Read-only health checks |
| `suggest_category` | Classifier suggestion for one server |

Resource: `mcp-hub://stats` returns live catalog statistics as JSON.

### Try it in Cursor

After registering, open Cursor's Agent chat and ask:

> *"Search the MCP catalog for PostgreSQL tools and show their publishers."*

> *"Check server 42 for provenance conflicts and show the audit trail."*

> *"Run doctor_report and tell me if there are any warnings."*

### Security

- All tools are **read-only**. No writes, no network calls, no shell.
- Database path is read from the `MCP_HUB_DB` env var (default `mcp_hub.sqlite3`).
- No tool accepts arbitrary file paths.

## Performance notes

v1.1 focuses on local-first scale:
- **Indexed provenance:** field-level and conflict queries hit `field_provenance`
  (indexed on `server_id`, `(server_id, field_name)`, `source`, `set_at`) instead of
  re-parsing every `payload_json` row.
- **Batch ingestion:** `bulk_add()` commits in one transaction instead of per-row.
- **Narrowed duplicate detection:** candidate lookup uses indexed identity columns (registry/repo/endpoint/publisher+name prefix) instead of scanning the full table.
- **Indexes:** composite indexes for common lookups and active listing.
- **FTS5:** optional virtual table for fast text search (falls back to LIKE if unavailable).

Run `python scripts/benchmark.py` to generate `benchmark-results.json` on your machine. Treat published numbers as indicative, not guarantees.

## Security

See [SECURITY.md](SECURITY.md). Defaults are localhost-only and no auth; enable `MCP_HUB_API_KEY` and run behind TLS when exposing the service.

## Development

```bash
pip install -e ".[dev,api]"
ruff check src tests
ruff format --check src tests
pytest --cov=mcp_hub --cov-report=term-missing
```

## License

Apache-2.0. See [LICENSE](LICENSE).
