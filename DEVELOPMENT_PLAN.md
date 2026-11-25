# mcp-hub — development plan (post 1.3.1)

## Status (updated as phases land)

| Phase | State | PR |
| --- | --- | --- |
| 0 baseline truth | ✅ merged | #18 |
| 1 security depth | ✅ merged | #19 |
| 2 data safety | ✅ merged | #20 |
| 3 product surface | ✅ merged | #21 |
| 4 DX and community | ✅ merged | #22 |
| 5 dependency debt | ✅ merged | #23 |
| 6 publishing | ⏸ not started (deliberate) | — |

Measured at the end of Phase 4: 253 tests, **93.5%** coverage (floor 88), mypy strict clean on
six modules, docs site building under `--strict`, 0 open PRs / 0 open issues.

**Written:** 2026-11-17 · **Baseline:** `main` @ `0acee32c`, tag `v1.3.1` · **Rule of this plan:** nothing is published until Phase 6.

---

## 1. Where the project actually stands

### Solid
| Area | Evidence |
|---|---|
| Security posture | SSRF filter (`safe_fetch.py`), secret redaction, rate limit (120/min/IP, XFF only from loopback), 1 MiB body cap (streaming-enforced), single-key auth, `debug=False`, localhost CORS |
| Supply chain | every Action SHA-pinned, dependency upper bounds, Dependabot live, Trivy image gate at 0 findings, SPDX SBOM on the release, bandit/semgrep/pip-audit/licence gates |
| Audit trail | `SECURITY_AUDIT.md` — 33 commits swept, no leaked credential |
| Tests | 147 tests, **82% coverage** (measured 2026-11-17 on Python 3.14/Termux) |
| Ops | Dockerfile runs non-root with a healthcheck, `doctor` command, provenance index, scheduler runs |

### Weak — measured, not guessed
| Gap | Number / fact | Consequence |
|---|---|---|
| One Python version in CI | matrix absent, `requires-python = ">=3.10"` | 3.10/3.11/3.13/3.14 are claimed but never executed |
| No type checking | `py.typed` shipped, no mypy/pyright anywhere | the marker is an unverified promise |
| Thin coverage in the risky places | `cli.py` 58%, `payload_limit.py` 61% (the chunked-upload branch is untested), `api.py` 70%, `alerts.py` 73% | the newest security code has the least proof |
| Rate limit is per-process | in-memory buckets | N uvicorn workers or N replicas = N× the limit, silently |
| No security headers | no CSP / X-Frame-Options / Referrer-Policy | the embedded dashboard is frameable; no CSP defence-in-depth |
| No pagination | `/api/search` takes `limit` only | a 50k-record catalog cannot be paged |
| No observability | no `/metrics`, no request IDs, plain logs | nothing to alert on in production |
| No backup story | SQLite file, no `backup`/`restore` command | one bad migration = total data loss |
| 6 Dependabot majors open | #10 checkout 4→7, #13 setup-python 5→7, #11 build-push 5→7, #14 metadata 5→6, #12 download-artifact 4→8, #9 python 3.12→**3.15**-slim | the Python 3.15 base is a pre-release image; artifacts v8 changes the download API |
| Not on PyPI | `publish.yml` never ran (no pending publisher) | `pip install mcp-hub` does not work; the only install path is a clone |

---

## 2. Phases

Order is deliberate: correctness → security depth → data safety → product → DX → **publish last**.

### Phase 0 — baseline truth (≈1 day)
Make CI measure what the README claims.

- Python matrix `3.10, 3.11, 3.12, 3.13, 3.14` on `test` (3.14 allowed to fail until it is proven).
- `mypy --strict` **only** on `ratelimit.py`, `payload_limit.py`, `safe_fetch.py`, `redact.py` (the security core) — a bounded gate that cannot block the whole repo. Widen module by module.
- `pytest --cov-fail-under=82` as the floor, raised in Phase 4.
- Publish the real coverage table in the README badge (no inflated number).

**Exit:** 5-version matrix green, mypy gate green on the 4 modules, coverage floor enforced in CI.

### Phase 1 — security depth (≈2–3 days)
- **Rate limit honesty:** detect `WEB_CONCURRENCY > 1` / multiple workers at startup and log a loud warning; add an optional SQLite-backed counter (`MCP_HUB_RATE_LIMIT_STORE=sqlite`) so limits hold across processes. Never advertise a limit that is silently multiplied.
- **Security headers middleware:** `Content-Security-Policy` (`default-src 'self'`; the dashboard inlines CSS/JS, so ship a nonce or move the assets out), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, HSTS only when behind TLS.
- **Multi-key auth:** `MCP_HUB_API_KEYS="name:key:scope"` comma-separated, constant-time compare (`hmac.compare_digest`), per-key rate-limit bucket key, `/api/health` stays open. Keeps the single-key path as a fallback.
- **Prove the streaming cap:** tests for chunked bodies without `Content-Length` (the current 61% gap), a body exactly at 1 MiB, and a client that disconnects mid-stream.

**Exit:** headers present on every response, 2-key config works end-to-end, `payload_limit.py` ≥ 95%, security docs updated.

### Phase 2 — data safety (≈2 days)
- `mcp-hub backup <path>` / `mcp-hub restore <path>` (SQLite online backup API, not `cp`), with a checksum.
- Migration harness: build a v0 schema fixture, migrate to head, assert data survives — the current `user_version` migration has no test.
- WAL + `synchronous=NORMAL` decision documented; `doctor` reports journal mode, page count, free pages, and whether a backup is stale.
- Scheduler idempotency: re-running `run-all` mid-crash must not duplicate alerts or double-count runs.

**Exit:** backup→restore round-trip test, v0→head migration test, `doctor` shows backup age.

### Phase 3 — product surface (≈3–4 days)
- **Pagination:** `offset` + `next_offset` on `/api/search` and `/api/searches` runs; stable ordering by `(score, id)` so pages do not shuffle.
- **Observability:** `/metrics` (Prometheus text format: requests, 429s, 413s, search latency, records), request-ID middleware echoed in `X-Request-ID` and in logs, optional JSON log format (`MCP_HUB_LOG_FORMAT=json`).
- **Dashboard hardening:** move the API key out of `localStorage` into an httpOnly session cookie, or drop the key prompt entirely and document "put it behind a proxy that injects the header".
- **MCP server parity:** expose `search_servers`, `get_server`, `list_categories` as tools; add `--transport http` only if the MCP SDK supports it cleanly.

**Exit:** paging works on a 10k-row fixture, `/metrics` scraped by a test, dashboard sets a cookie instead of `localStorage`.

### Phase 4 — DX and community (≈2 days)
- `cli.py` tests to ≥ 80% (it is the first thing a user touches and the least tested).
- Coverage floor 82 → **88%**; `api.py` error paths (401/413/429/404) explicitly tested.
- `CONTRIBUTING.md`, issue templates (bug / feature), `CODE_OF_CONDUCT.md`, `pre-commit` (ruff + ruff-format + mypy on the security core).
- `docs/` with mkdocs: install, CLI reference, API reference, security model, operations runbook (backup, metrics, upgrade).

**Exit:** docs site builds in CI, pre-commit passes on a clean checkout, coverage ≥ 88%.

### Phase 5 — dependency debt + actionable approvals (COMPLETE, 2026-11-25)
- Merge in this order once the Phase 0 matrix is green: checkout 7 → setup-python 7 → metadata 6 → build-push 7 → download-artifact 8 (verify the artifact API change against `security-scan` upload/download).
- **Close #9** (`python:3.15-slim`): 3.15 is not released; adopt a pre-release base image and the whole supply chain inherits an unpatched Debian snapshot. Pin `python:3.13-slim` (current stable line with the longest support) and let Dependabot propose 3.14 when it lands in the base image.
- Add `.github/dependabot.yml` grouping so patch bumps arrive as one PR, not five.

**Exit (dependency debt):** zero open dependency PRs, base image pinned to a released Python, grouping live.

**Exit (product Phase 5):** shipped in v1.6.0 — default-deny `approvals` policy, actionable MCP approval methods, digest-bound TOCTOU guard on skill approvals, atomic task approval, the five approval CLI commands with `--json` and exit codes `0`/`1`/`2`, and read-only Apps action metadata. No new migration and no change to the pinned tool manifest. Verified with 485 tests at 95.11%-95.22% coverage.

**Exit (Phase 6 Arabic full):** shipped in v1.7.0 — shared `normalize_for_match()` matcher, query-only digit folding, stable Arabic diagnostics for policy/task denial, fingerprint mismatch and missing permission, a read-only RTL Apps notice, and a documented Path 2 DSL contract. IDs, actors, reasons and audit text stay verbatim. Verified with 499 tests at 95.21%-95.32% coverage.

### Phase 6 — publishing (LAST)
1. Create the PyPI **pending publisher**: project `mcp-hub`, owner `tibatechapp-ship-it`, workflow `publish.yml`, environment `pypi`.
2. `workflow_dispatch` run of `publish.yml` as a pure build dry-run (no publish) — confirms sdist/wheel contents.
3. Tag `v1.4.0` → trusted publishing (OIDC) pushes to PyPI → verify `pip install mcp-hub==1.4.0` in a clean container.
4. Release assets: SPDX SBOM (already working) + SLSA build provenance (`actions/attest-build-provenance`).
5. Publish the container: `ghcr.io/tibatechapp-ship-it/mcp-hub:1.4.0` + `:latest`, cosign-signed.
6. Docs go live (GitHub Pages from `docs/`), README install section switches from "clone" to `pip install mcp-hub` / `docker run`.
7. Post-release: enable Secret Scanning push protection, Dependabot alerts, CodeQL default setup, branch protection on `main` (the four manual items from `SECURITY_AUDIT.md`).

**Exit:** `pip install mcp-hub` works from PyPI in a clean environment, container pull works anonymously, release carries SBOM + provenance, all four repo security toggles on.

---

## 3. Sequencing and risk

```
Phase 0 ──▶ Phase 1 ──▶ Phase 2 ──▶ Phase 3 ──▶ Phase 4 ──▶ Phase 5 ──▶ Phase 6
(baseline)  (security)  (data)      (product)   (DX)        (deps)      (PUBLISH)
 1 day       2-3 days    2 days      3-4 days    2 days      1 day      1-2 days
```

- **Phase 0 first, always.** Every later gate depends on a matrix that actually runs the claimed versions. Without it, Phase 5 is a coin flip.
- **Phase 1 before Phase 3.** Adding pagination and metrics to an API whose body cap is 61%-tested puts new surface on unproven ground.
- **Phase 2 before any schema change.** There is currently no way back if a migration eats the database.
- **Phase 5 after Phase 0 and before Phase 6.** Publishing on a pre-release base image (3.15-slim) would ship an untested runtime to PyPI users.
- **Phase 6 strictly last.** Publishing is the one-way door: once a version is on PyPI it cannot be replaced, only yanked.

### Rollback rule per phase
Every phase lands as its own PR with CI green and a one-line revert path. If a phase's exit criteria are not met after 3 attempts, stop, write down what blocked it, and move to the next phase rather than weakening the gate.

---

## 4. What I would cut if time is short

Cut in this order: Phase 3 dashboard cookie work → Phase 3 MCP transport parity → Phase 4 docs site → Phase 5 grouping.
**Never cut:** Phase 0 (matrix), Phase 2 (backup/restore), the PyPI pending-publisher step, and the base-image decision in Phase 5.
