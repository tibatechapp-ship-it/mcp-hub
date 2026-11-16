# Changelog

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
