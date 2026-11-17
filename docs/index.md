# mcp-hub

Local-first catalog for MCP servers. Sync from the MCP registry, enrich from
GitHub, classify, search (LIKE or FTS5), save alerts and searches, export to
JSON/CSV/Markdown — then optionally serve it over HTTP with a dashboard.

Everything lives in **one SQLite file**. No server, no account, no telemetry.

## What it is good at

| Job | How |
| --- | --- |
| Keep an offline index of MCP servers | `mcp-hub sync-registry` |
| Find a server by capability | `mcp-hub search "postgres"` (FTS5 available) |
| Know where a field came from | `mcp-hub provenance <id>` |
| Watch for new matches | `mcp-hub save-search` + `run-search` + webhook alerts |
| Publish a curated list | `mcp-hub export markdown catalog.md` |
| Serve it to other tools | `mcp-hub serve` (FastAPI + dashboard) or `mcp-hub-server` (MCP) |

## Quick start

```bash
pip install "mcp-hub[api]"
mcp-hub --db ~/.local/share/mcp-hub/catalog.sqlite3 import-text servers.txt
mcp-hub --db ~/.local/share/mcp-hub/catalog.sqlite3 search postgres
mcp-hub --db ~/.local/share/mcp-hub/catalog.sqlite3 serve --port 8000
# open http://127.0.0.1:8000
```

`servers.txt` is one server per line, pipe-delimited:

```
name|publisher|description|repository_url
```

## Where to go next

- [Install](install.md) — extras, Docker, environment variables
- [CLI reference](cli.md) — every command and its exit codes
- [HTTP API](api.md) — endpoints, auth, paging, metrics
- [Security model](security.md) — what is protected and what is not
- [Operations](operations.md) — backup, restore, upgrades, monitoring
