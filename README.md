# mcp-hub

> ⚠️ **Security notice** — read before exposing this service
>
> `mcp-hub` binds to `127.0.0.1` by default. When you set `MCP_HUB_API_KEY`
> and bind to a network interface (`--host 0.0.0.0`), **always run behind a
> TLS-terminating reverse proxy** (Caddy, nginx, Cloudflare Tunnel).
> Plain HTTP leaks the API key to every device on the network.
>
> Full security model: [SECURITY.md](SECURITY.md).

[![Release](https://img.shields.io/github/v/release/tibatechapp-ship-it/mcp-hub)](https://github.com/tibatechapp-ship-it/mcp-hub/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://github.com/tibatechapp-ship-it/mcp-hub/releases/latest)
[![CI](https://github.com/tibatechapp-ship-it/mcp-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/tibatechapp-ship-it/mcp-hub/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://tibatechapp-ship-it.github.io/mcp-hub/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Container](https://img.shields.io/badge/ghcr.io-mcp--hub-blue.svg)](https://github.com/tibatechapp-ship-it/mcp-hub/pkgs/container/mcp-hub)

**Install note:** PyPI publishing is deferred; install from the PEP 503 index or
the signed GitHub Release assets. See [`docs/install.md`](docs/install.md).

Local-first catalog for MCP servers: sync from the MCP registry, enrich from GitHub, classify, search (LIKE/FTS5), save alerts/searches, and export to JSON/CSV/Markdown. Ships as a pure-Python + SQLite core with an optional FastAPI dashboard.

- **Version:** 1.8.0
- **DB:** SQLite (portable, single file)
- **API:** optional FastAPI + dashboard (`pip install "mcp-hub[api]"`)
- **MCP server:** `pip install "mcp-hub[mcp-server]"` → `mcp-hub-server`
- **Docker:** `ghcr.io/tibatechapp-ship-it/mcp-hub`
- **Docs:** <https://tibatechapp-ship-it.github.io/mcp-hub/> (source in [`docs/`](docs/index.md))
- **العربية:** [فهرس خوادم وأدوات MCP](docs/catalog_ar.md) — unified registry in [`registry/mcp_servers.json`](registry/mcp_servers.json)
- **Capability profiles:** five JSON task profiles in [`profiles/`](profiles/), documented in [Arabic](docs/task_profiles_ar.md); `tags`, `task_profiles`, `access_mode` and `requires_env` are additive metadata.
- **Arabic runtime:** opt-in `tools/list` localization with an upstream-only cache (`MCP_HUB_AR_RUNTIME=1`), stable `--lang ar` diagnostics, a read-only RTL approval UI, and one shared matcher for `route-task`/`query-catalog`. Tool names and `inputSchema` stay untouched, ids/actors/reasons keep their original text, and every failure fails open. See [`docs/arabic_runtime.md`](docs/arabic_runtime.md).
- **Arabic command DSL:** `mcp-hub arabic-dsl --text 'ابحث عن أداة البحث' --dry-run --json` translates Arabic commands and pipelines into existing MCP methods under the same policies; see [`docs/arabic_dsl.md`](docs/arabic_dsl.md).
- **Arabic playbooks:** `mcp-hub arflow check|run --file flow.arflow --json` compiles `.arflow` files with Arabic variables and optional steps into the same DSL commands, with a dedicated playbook audit table; see [`docs/arflow.md`](docs/arflow.md).
- **Incremental Arabic tests:** `mcp-hub arflow test --file flow.arflow --json` fingerprints compiled steps, policy and catalog state, adopts unchanged successful steps, re-runs after the first change and records verbatim Arabic text in `arflow_incremental_audit`; see [`docs/incremental_testing.md`](docs/incremental_testing.md).
- **Bounded `arflow` watch:** `mcp-hub arflow watch --file flow.arflow --once --json` watches the playbook and linked files, overlays SHA-256 file digests onto step fingerprints, reruns only affected steps and stops after a bounded number of cycles; see [`docs/arflow_watch.md`](docs/arflow_watch.md).

**New in 1.8.0** — Phases 7-9 Arabic DSL, playbooks and incremental tests: Arabic commands compile to the existing 27 MCP methods through the same gateway, `.arflow` files add variables and optional steps, and `arflow test` adopts unchanged steps while re-running changed ones with Arabic expectations and a fingerprinted audit. No protocol, manifest or Apps write surface changed. Verified with 627 tests at 95.82% coverage. Full list: [CHANGELOG.md](CHANGELOG.md).

**New in 1.7.0** — Phase 6 Arabic full: one shared `normalize_for_match()`
matcher for `route-task` and `query-catalog`, digit folding limited to query
and counter text, stable Arabic diagnostics for policy/task denial, skill
fingerprint mismatch and missing permission, and a read-only RTL Apps notice.
IDs, actors, reasons and audit text stay verbatim; pinned tool names and
`inputSchema` are unchanged. Verified with 499 tests at 95.21%-95.32%
coverage. Full list: [CHANGELOG.md](CHANGELOG.md).

Builds on the 1.5.0 Arabic registry, stateless MCP core, bilingual
policy/router layer, durable skills/tasks, persona simulator and optional
telemetry without changing the pinned tool manifest. See
[Phase 5](docs/phase5.md).

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
mcp-hub sync-arabic   # upstream-only Arabic registry cache refresh
mcp-hub query-catalog --profile code-review --access-mode read-only
mcp-hub query-catalog --tag rag-web-search --ready-only
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
| `GET /api/search?q=&fts=&limit=&offset=` | search servers, paged (`next_offset` is exact) |
| `GET /api/servers/{id}` | server details |
| `GET /api/servers/{id}/provenance` | provenance: `?mode=fields\|conflicts\|events&field=` |
| `GET/POST/DELETE /api/searches*` | saved searches |
| `POST /api/searches/run-all` | run all saved searches |
| `GET /api/scheduler/runs` | recent batch runs |
| `GET/POST /api/categories*` | custom categories & overrides |
| `GET/POST /api/alerts` | alert channels |
| `GET /metrics` | Prometheus text metrics (requests, refusals, latency, catalog size) |

When `MCP_HUB_API_KEY` (or `--api-key`) is set, everything except `/api/health` and `/` requires `X-API-Key` or `Authorization: Bearer <key>` — `/metrics` included. `MCP_HUB_API_KEYS="name:key,name2:key2:ro"` replaces it with a list; `:ro` keys are refused on any write.

**Paging.** `/api/search` takes `limit` (1–200) and `offset`. The response carries `offset`, `next_offset` (exactly `null` on the last page — the query fetches one extra row to know) and `count`.

**Tracing and metrics.** Every response carries `X-Request-ID`; send your own (up to 64 chars of `A-Za-z0-9._-`) and it is echoed back. `/metrics` renders Prometheus text: request counts by method/status/route template, latency sum and count, and catalog gauges.

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
| `query_catalog` | Filter servers/tools by category, tag, task profile, access mode or readiness |

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

- **SSRF filter** on every user-supplied URL (webhook alerts, endpoint probes, registry sync):
  HTTPS only, public IPs only, redirects re-validated, response size capped.
- **Secret redaction** in every error detail, log line and alert payload — provider tokens,
  bearer headers, PEM keys and `password=` values never leave the process.
- **API hardening**: `debug=False`, generic body on 500s, CORS limited to localhost origins
  (`MCP_HUB_CORS_ORIGINS` to widen), API key via `MCP_HUB_API_KEY`.
- Defaults are localhost-only and unauthenticated — enable the key and put TLS in front when
  exposing the service.

Full policy, blocked ranges and limits: [`SECURITY.md`](SECURITY.md).

## Development

```bash
pip install -e ".[dev,api]"
ruff check src tests
ruff format --check src tests
pytest --cov=mcp_hub --cov-report=term-missing
```

## License

Apache-2.0. See [LICENSE](LICENSE).
