# Changelog

## [Unreleased]

### Added
- **Phase 6 Path 1 — native Arabic runtime (PR #51)**: `normalize_arabic_text()` maps Arabic-Indic and Extended Arabic-Indic digits to ASCII, removes tatweel and folds alef/ya variants; `route-task` applies it to free-text queries.
- **Phase 6 Arabic full — shared matcher**: `normalize_for_match()` is the single folding rule for `semantic_router` and `catalog_query`, adding harakat removal, ta-marbuta folding and optional definite-article stripping without touching stored metadata.
- **Arabic diagnostics**: the five approval CLI commands accept `--lang ar` (or `MCP_HUB_LANG`) and add a stable `message_ar` field to JSON failures without changing the compact output, stderr routing or exit codes `0`/`1`/`2`. Diagnostics now cover policy denial (`-10002`), task denial (`-10003`), skill fingerprint mismatch and missing permission.
- **RTL read-only Apps UI**: `ui://` resources accept `params.lang = "ar"` or `MCP_HUB_UI_LANG`, render `lang="ar" dir="rtl"` with re-computed script hashes, show an explicit read-only notice, emit no buttons or write-method `postMessage` calls, and report `lang`/`dir` in the JSON fallback.
- **Path 2 interface contract**: `docs/arabic_runtime.md` documents how a future Arabic MCP Client / DSL translates phrases into existing MCP methods or `--json` CLI calls without duplicating policy, audit or approval state.

### Changed
- Digit folding now applies only to query/counter text. `task_id`, `tenant_id`, `session_id`, skill names, actors, reasons and audit text keep their original bytes; Indic digits in an id fail with exit code `2` instead of being silently rewritten.
- `semantic_router` no longer duplicates diacritics/tatweel/alef/ta-marbuta rules; it delegates to `arabic_runtime`. The pinned tool manifest, tool specs, resource specs and handler names are unchanged.

## [1.6.0] — 2026-11-25

Phase 5 actionable approvals line (PRs #49-#50). Builds on the 1.5.0
capability/protocol/observability base without changing its pinned tool
manifest or stateless protocol surface. Release gate: 485 tests passed,
95.11%-95.22% coverage, 12/12 CI/Docs checks green.

### Added
- **Phase 5 actionable approvals**: a default-deny `approvals` policy section separate from tool and skill reads, MCP methods `skills/approve`, `skills/revoke`, `tasks/approve` and scoped `tasks/list`, digest-bound redacted skill approval events with SHA-256 content fingerprints that close the TOCTOU gap between review and write, atomic task approval under `BEGIN IMMEDIATE`, CLI `task-list` / `task-approve`, and read-only `available_actions` metadata in the approval Apps resource. The pinned tool manifest is unchanged.
- **Approval CLI contract**: `skill-list`, `skill-approve`, `skill-revoke`, `task-list` and `task-approve` now accept `--json` for compact stdout and structured JSON failures on stderr, with documented exit codes (`0` success, `1` not found, `2` bad input or invalid state); default pretty output remains backward compatible.

### Verification
- 485 tests passed on Python 3.10-3.14; total coverage 95.11%-95.22%.
- Sensitive modules: `cli.py` 100%, `policy_gateway.py` 99%, `skills.py` 98%, `tasks.py` 99%, `mcp_stateless.py` 99%, `arabic_runtime.py` 100%.
- GitGuardian, Trivy, security-scan, image-scan, docs and both MCP SDK lanes green.

## [1.5.0] — 2026-11-24

Capability, protocol and observability line (PRs #40-#47). Builds on the 1.4.0
hardening without changing it: the MCP surface is stateless and pinned, the
catalog is Arabic-aware and capability-labelled, policy/routing/skills/tasks are
deterministic, and telemetry is optional with SQLite as the zero-dependency
default. Release gate: 425 tests passed, 90.43% coverage, 12/12 CI/Docs checks
green, PEP 503 index regenerated from the tagged artifacts.

### Added
- **Unified MCP registry** (`registry/mcp_servers.json`) covering 20 servers and 139 documented tools — the existing local/`mcp-hub` entries plus GitHub, Git, Serena, Context7, Supabase, Postgres, SQLite, DBHub, Playwright, Chrome DevTools, Firecrawl, Fetch, Brave Search, Filesystem, Desktop Commander and MarkItDown. Native `mcpServers` fields stay unchanged; Arabic `name_ar`, `description_ar`, `category_ar` and `params_ar` are display-only.
- **Arabic catalog** (`docs/catalog_ar.md`) generated from the registry, with per-category tables, parameter explanations and quick-start examples; linked from the README and MkDocs nav.
- **Registry validator** (`scripts/validate_registry.py`) and 8 tests (`tests/test_registry_ar.py`) checking strict UTF-8, Arabic metadata on every server/tool, unique ids/tools/URLs, no port or launch-signature conflicts, and empty `env` values (no secrets in the repo).
- **Arabic runtime layer** (`src/mcp_hub/arabic_runtime.py`): opt-in `MCP_HUB_AR_RUNTIME=1` wrapper around `tools/list` that adds Arabic descriptions from the registry plus a deterministic glossary fallback. Protocol tool names and `inputSchema` are never changed, and cache/translation/network failures fail open.
- **Upstream-only live sync** (`src/mcp_hub/live_sync.py`): exact default-branch raw registry URL allowlist, SSRF-checked fetch through `safe_fetch`, atomic cache replacement, conditional refreshes, and a `mcp-hub sync-arabic` CLI command. Forks, branches and arbitrary URLs are refused.
- **Capability/task profiles**: backward-compatible `tags`, `task_profiles`, `access_mode` (`read-only`|`read-write`) and `requires_env` on servers and tools; five JSON profiles under `profiles/`; a pure-stdlib `catalog_query` library, `mcp-hub query-catalog` CLI and read-only `query_catalog` MCP tool with `ready_only` filtering. All registered tools are capability-labelled; the validator checks profile ids, tag values and env declarations.
- **Stateless MCP core** (`mcp_stateless.py`) and `POST /mcp` for the 2026-07-28 revision: no handshake or sessions, mirrored `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` validation with `-32020` `HeaderMismatch`, Base64 sentinel decoding, optional `Mcp-Param-*` checks, `server/discover`, cacheable list results, and a SHA-256 pinned tool manifest that blocks silent tool-definition drift (`-10001`).
- **Phase 2 deterministic layer**: a ZYKO-compatible JSON tool policy with allow/deny, recursive redaction and JSONL audit; a bilingual (Arabic/English) semantic router with compact output; a six-code description-smell auditor; and a mirrored-token header guard. New tools `route_task`, `description_smell_audit`, `policy_snapshot` plus CLI commands `route-task`, `description-smells`, `policy-snapshot`.
- **Phase 3 skills/tasks/persona**: validated read-only `SKILL.md` packages with content-bound SHA-256 approvals, separate `skills` policy from tool execution, scoped durable tasks in SQLite (`tasks/get`, `tasks/update`, `tasks/cancel`) with lease recovery and cleanup, and a deterministic local persona simulator with safe-chain, retry, redaction and permission-bypass scenarios. New CLI commands `skill-list`, `skill-get`, `skill-approve`, `skill-revoke`, `task-get`, `task-cancel` and `persona-simulate`.
- **Phase 4 telemetry and MCP Apps**: dependency-free SQLite structured events by default, an opt-in OTel adapter with safe fallback, GenAI SemConv attributes, verbatim SEP-414 trace context in `params._meta`, opt-in redacted tool I/O logging, and two allowlisted sandboxed `ui://` resources (`approval-review`, `tool-catalog`) with JSON-RPC over `postMessage`, strict CSP and JSON fallback. New commands `telemetry-status` and `app-resources`.

## [1.4.0] — 2026-11-17

Release-hardening line. A default loopback install behaves as before; every item
below is about being trustworthy once the API leaves `127.0.0.1` — and about
being installable, testable and documented like a real project.

### Added
- **Nonce-based CSP and a full header set** on every response (`headers.py`):
  `default-src 'self'`, no `unsafe-inline`, `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP, and HSTS only when
  the request actually arrived over TLS.
- **Multi-key auth** — `MCP_HUB_API_KEYS="name:secret,name2:secret2:ro"`.
  `:ro` keys get `403` on any write; the compare is constant-time against every
  key (no early exit), so timing reveals neither a hit nor a position.
  `401` now carries `WWW-Authenticate: Bearer realm="mcp-hub"`.
- **Payload cap as raw ASGI middleware** — 1 MiB (`MCP_HUB_MAX_BODY_BYTES`),
  checked against `Content-Length` and enforced while streaming, so chunked
  uploads get `413` instead of a truncated `500`.
- **Rate limiting with a shared store** — 120 req/min per IP
  (`MCP_HUB_RATE_LIMIT_PER_MIN`), `429` + `Retry-After` derived from the refill
  rate. `MCP_HUB_RATE_LIMIT_STORE=sqlite` shares buckets across workers;
  `WEB_CONCURRENCY>1` with the memory store logs a warning at startup.
- **`backup` / `verify-backup` / `restore`** — SQLite online backup API,
  SHA-256 sidecar, `<db>.pre-restore*` rollback, `--force` required to replace an
  existing database. `restore` moves the `-wal`/`-shm` sidecars with the file.
- **`doctor`** — journal mode, WAL size, vacuum advice and backup freshness.
- **Exact pagination** — `/api/search` returns `offset`/`next_offset`; the query
  fetches `limit + 1` rows, so the last page is `null` rather than a link to an
  empty page.
- **`/metrics`** — Prometheus text: request counts by method/status/route
  template, latency sum and count, catalog gauges. Key-gated like the API.
- **`X-Request-ID`** — echoed when the caller supplies a safe one, generated
  otherwise, present on error responses too.
- **`mkdocs` documentation site** — install, CLI, HTTP API, security model,
  operations; built with `--strict` in CI and published to GitHub Pages.
- **CI hardening** — test matrix 3.10–3.13 blocking plus 3.14 advisory, mypy
  `strict` over the security core, coverage floor at 88 (measured 95), bandit,
  semgrep, pip-audit, pip-licenses, Trivy image gate with SARIF to the Security
  tab, SBOM in the release, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue
  templates, pre-commit.

### Changed
- `/api/*` **and** `/metrics` are gated when a key is configured; `/api/health`
  and `/` stay open for probes.
- `X-Forwarded-For` is honoured only from a loopback peer, and the **last** hop
  (the one the proxy appended) is the bucket key.
- Docker base pinned to `python:3.13-slim`; the image runs as the unprivileged
  `mcphub` user with a `HEALTHCHECK`.
- Dependabot: grouped minor/patch updates, majors single so they revert alone.

### Fixed
- **FTS5 search never ran.** `WHERE f MATCH ?` used the join alias, which SQLite
  rejects (`no such column: f`); a broad `except` swallowed it, so `fts=True`
  silently returned LIKE results. Now `servers_fts MATCH ?`, and a failure logs
  instead of hiding.
- **Old databases could not be opened** (`no such column: archived`) — the
  migration ran after the index creation that needed the column.
- **Dashboard buttons did nothing** — `onclick=` attributes are blocked by a
  nonce CSP (a nonce only whitelists `<script>` elements). Wired from the
  nonce'd script instead.
- **`restore` left a stale WAL** next to the restored database.
- **500 responses shipped without headers** — the exception handler runs in
  Starlette's `ServerErrorMiddleware`, outside the middleware stack.
- **`Retry-After: 60`** on a 120 req/min limit told clients to wait a minute for
  a one-second refill.
- Dashboard API key moved from `localStorage` to `sessionStorage` (per tab).
- Rate-limit buckets were keyed on the client-supplied XFF prefix, letting a
  caller pick its own bucket.

### Removed
- Nothing. `1.3.x` databases are opened in place and upgraded on first use.

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
