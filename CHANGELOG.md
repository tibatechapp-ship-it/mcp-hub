# Changelog

## [1.3.1] — 2026-11-17

Security hardening release. No behaviour change for a default local
installation; every item below closes a gap that only opens when the API is
exposed beyond loopback.

### Added
- **Rate limiting** — `/api/*` (except `/api/health`, `/api/doctor`) is capped at
  120 req/min per client IP, `429` + `Retry-After`. `X-Forwarded-For` is honoured
  only from a loopback peer, so a remote caller cannot rotate buckets by
  spoofing the header. `MCP_HUB_RATE_LIMIT_PER_MIN` (0 disables).
- **Payload limits** — `POST`/`PUT`/`PATCH` bodies over 1 MiB get `413`, checked
  against `Content-Length` before reading and enforced while streaming for
  chunked uploads. `MCP_HUB_MAX_BODY_BYTES`.
- **Continuous security scanning in CI** — bandit (SAST, blocks HIGH), semgrep
  (`p/python`, `p/security-audit`), pip-audit (blocks any known CVE),
  pip-licenses (blocks GPL/AGPL), Trivy on the built image (blocks CRITICAL +
  HIGH with a fix available). Reports uploaded as artifacts; Trivy SARIF goes to
  the Security tab.
- **SBOM in the release** — `release.yml` publishes an SPDX JSON SBOM next to the
  container image.
- **Dependabot** — weekly PRs for pip, GitHub Actions and the Docker base image.
- **`SECURITY_AUDIT.md`** — committed record of the full git-history secret scan.
- README security notice (TLS reverse proxy requirement) and a SECURITY.md
  section covering rate limiting, scanning and the dependency policy.

### Changed
- Every GitHub Action pinned to a full commit SHA with a `# vX.Y.Z` comment;
  workflows carry scoped `permissions:` blocks.
- Runtime and dev dependencies carry explicit upper bounds (next major).
- `pytest >=9.0.3,<10` (PYSEC-2026-1845), `pytest-cov >=7.0,<8`.
- Dockerfile: applies Debian security updates at build time, purges the distro
  `python3-setuptools` that shadowed the pip-installed one, upgrades
  `pip`/`setuptools`/`wheel`, drops the stale vendored SBOM
  (`pip/_vendor/bom.cdx.json`) that made image scanners report phantom CVEs, and
  runs as the unprivileged `mcphub` user.

### Fixed
- Image scan findings: `zlib1g` (CVE-2026-85091), bundled `pip`/`setuptools`
  advisories.

## [1.3.0] - 2026-11-16

### Security (P0)
- **SSRF-safe HTTP client** (`mcp_hub.safe_fetch`): HTTPS-only, no credentials in
  URLs, DNS resolution pinned to public addresses (blocks loopback, RFC 1918,
  CGNAT, link-local / cloud metadata, IPv6 ULA, IPv4-mapped IPv6), redirect
  re-validation, 3-hop and body-size caps. Standard library only — no new
  runtime dependency.
- **Secret redaction** (`mcp_hub.redact`): provider tokens, bearer/auth headers,
  PEM blocks, connection-string passwords and URL query secrets are scrubbed
  from error details before they reach a user, a log or an alert payload.
- **Alerts**: webhook delivery now goes through `safe_request`; failures are
  reported per channel with redacted details (`errors` list on `dispatch()`).
- **Registry sync + GitHub enrichment**: policy-checked fetches, untrusted error
  text redacted; a policy refusal is not retried.
- **REST API**: `debug=False`, unhandled exceptions return a generic body,
  `detail=str(exc)` sites redacted, CORS restricted to localhost origins by
  default (`MCP_HUB_CORS_ORIGINS` to override).
- **Endpoint health probes** (`mcp_hub.health.check_endpoint`): new SSRF-safe,
  never-raising HEAD probe (storage/CLI wiring lands later).
- **Packaging hygiene**: `.dockerignore` now keeps databases, `.env` files and
  private keys out of image layers.

### Added
- `tests/test_ssrf.py` and `tests/test_redact.py` (82 tests, no network needed).

### Changed
- `alerts.dispatch()` result may include an `errors` list; counts are unchanged.
- `registry_api._http_get_json()` raises `RuntimeError` with a redacted message
  and stops retrying when the URL is refused by policy.

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
