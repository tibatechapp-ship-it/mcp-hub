# Changelog

## [1.2.0] - 2026-11-16

### Added
- **MCP server** (`mcp-server` extra): 8 read-only tools exposing the catalog to
  Cursor and other MCP hosts via the official `mcp` SDK (works with `mcp` 1.x
  and 2.x).
  - `search_servers`, `get_server`, `list_categories`, `servers_by_category`
  - `provenance_conflicts`, `provenance_events`
  - `doctor_report`, `suggest_category`
- Resource `mcp-hub://stats` for live catalog statistics.
- Entry point `mcp-hub-server`.
- `examples/cursor-mcp.json` template.
- `tests/test_mcp_server.py` (18 tests, including a read-only contract test).
- CI job `mcp-server` runs the suite against `mcp` 1.x **and** 2.x.

### Changed
- `pyproject.toml`: new `[project.optional-dependencies].mcp-server`
  (`mcp>=1.27,<3`), `mcp-hub-server` script, pytest markers.

## [1.1.1] - 2026-11-16

### Added
- **Dual-layer provenance**: new `field_provenance` table indexed by
  `(server_id, field_name, source)` enables O(log n) field queries while the
  existing `provenance` table stays the audit trail.
- New APIs: `provenance.field_history()`, `provenance.field_conflicts()`,
  `provenance.event_history()`.
- CLI: `provenance <id>` with `--field <name>`, `--conflicts-only`, `--events`, `--limit`.
- REST: `GET /api/servers/{id}/provenance?mode=fields|conflicts|events&field=...`
  (`/api/server/{id}/provenance` kept as alias).
- Automatic, one-time backfill from existing `provenance` rows when opening an
  older database (guarded by `PRAGMA user_version`).
- Classification (`classify`) and category overrides now record `categories`
  changes in the provenance layer.

### Changed
- `provenance.history()` and `provenance.conflicts()` now read `field_provenance`
  (fast, indexed) — same output shape, backward compatible.
- Provenance writes from `add_server()` / `bulk_add()` / `save_enrichment()` run
  in the same transaction as the catalog write instead of a second connection
  (previously they could be dropped on `SQLITE_BUSY`).

### Fixed
- Audit rows are no longer silently lost during bulk ingestion.

## [1.1.0] - 2026-11-16

### Added
- Optional API-key auth for `/api/*` via `MCP_HUB_API_KEY` or `--api-key` (`/api/health` stays open)
- FastAPI dashboard + REST API with scheduler runs and category export/import endpoints
- `bulk_add()` for single-transaction batch ingestion
- Narrowed duplicate-candidate lookup and composite SQLite indexes
- `export-categories` / `import-categories` CLI commands
- `doctor --json` machine-readable diagnostics
- `duration_ms` tracking for scheduler runs
- Dockerfile, docker-compose, `.env.example`, CI/release workflows, pre-commit hooks
- Benchmark/load-test scripts and E2E/API/core test suite
- Project docs: README, CONTRIBUTING, SECURITY, LICENSE

### Changed
- `add_server()` duplicate detection avoids full-table scans by using indexed candidates
- `/api/health` now verifies database connectivity
- GitHub Actions updated to `actions/setup-python@v5`

### Fixed
- Safer import/normalization paths for categories and saved search state
- CLI/DB ergonomics for local-first usage and exports

## [1.0.0] - 2026-11-15
- Initial local-first MCP catalog baseline (sync, search, classify, export)
