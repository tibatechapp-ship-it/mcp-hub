# Operations

## Backup

`backup` uses SQLite's online backup API (not `cp`), so it is safe while the
service is running and produces a consistent snapshot even in WAL mode.

```bash
mcp-hub --db /data/catalog.sqlite3 backup /backups/catalog-$(date +%F).sqlite3
```

It writes a sidecar `<file>.sha256` and refuses to overwrite an existing file.

## Verify and restore

```bash
mcp-hub --db /data/catalog.sqlite3 verify-backup /backups/catalog-2026-01-01.sqlite3
mcp-hub --db /data/catalog.sqlite3 restore /backups/catalog-2026-01-01.sqlite3 --force
```

`restore` verifies the checksum **before** touching anything, moves the current
database to `<db>.pre-restore`, and rolls back if the restored file fails its
checksum. Without `--force` it refuses to replace an existing database.

## Health and diagnostics

```bash
curl -s localhost:8000/api/health   # {"status":"ok",...} or 503
mcp-hub --db /data/catalog.sqlite3 doctor
```

`doctor` reports `journal_mode`, `pages`, `free_pages`, `needs_vacuum` (when
more than 20% of pages are free) and a `backup_hint` — take a fresh snapshot
when it says the current one is stale.

## Monitoring

Scrape `/metrics` with Prometheus. Useful series:

- `mcp_hub_requests_total{status="429"}` — you are being throttled
- `mcp_hub_requests_total{status="413"}` — a client is sending oversized bodies
- `mcp_hub_request_duration_seconds_sum / _count` — mean latency
- `mcp_hub_records` — catalog size over time

## Upgrades

1. `mcp-hub backup` (see above).
2. Install the new version.
3. `mcp-hub doctor` — schema migrations run on open, and missing columns are
   added in place (`ALTER TABLE ... ADD COLUMN`), so databases created by older
   releases open without manual SQL.
4. If anything looks wrong: `mcp-hub restore <snapshot> --force`.

## Multi-worker deployments

```bash
MCP_HUB_RATE_LIMIT_STORE=sqlite \
MCP_HUB_RATE_LIMIT_DB=/data/ratelimit.sqlite3 \
uvicorn mcp_hub.api:create_app --factory --workers 4
```

Without the SQLite store each worker keeps its own counters and the effective
limit is `workers × MCP_HUB_RATE_LIMIT_PER_MIN`. mcp-hub logs a warning when it
detects `WEB_CONCURRENCY > 1`.
