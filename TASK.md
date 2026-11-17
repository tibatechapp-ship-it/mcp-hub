# GOAL: execute DEVELOPMENT_PLAN.md phase by phase (P0 → P5), publishing (P6) LAST.
# Repo: tibatechapp-ship-it/mcp-hub. Rule: GitHub API only — branch → commits → PR → CI green → merge.
# Scratch dir for reading/editing/verifying: ~/mcp-hub-sec (copies, no clone). Verify with
#   PYTHONPATH=src python3 -m pytest -q --cov=mcp_hub ; ruff check . ; ruff format --check . ; mypy

## NOW
Phases 0–5 are merged and main is green. Phase 6 (publishing) is deliberately untouched — it
needs the user: a PyPI pending publisher, then a tag. Nothing else is open: 0 PRs, 0 issues.

## Done
- [x] P0 baseline truth — matrix 3.10–3.13 blocking + 3.14 advisory, mypy strict on the
      security core, coverage floor (80 → 88 in P4). #18 / 5bd3e860
- [x] P1 security depth — nonce CSP + headers, multi-key auth with `:ro`, ASGI body cap
      (413 for chunked too), SQLite token-bucket store + multi-worker warning. #19 / 3ceaf366
- [x] P2 data safety — `backup` / `verify-backup` / `restore` with SHA-256 + rollback,
      `doctor` journal/vacuum/backup hints, migration harness. Found and fixed a real
      upgrade bug: old databases could not be opened (`no such column: archived`). #20 / 327a3106
- [x] P3 product surface — exact `offset`/`next_offset` paging, `/metrics` Prometheus text,
      `X-Request-ID`. #21 / bc1665d0
- [x] P4 DX — `cli.py` and `api.py` at 100%, total 93.5%, floor 88, mkdocs site + `docs`
      CI job (`--strict`), CONTRIBUTING, CODE_OF_CONDUCT, issue templates, pre-commit. #22 / 5321d116
- [x] P5 dependency debt — checkout 7, setup-python 7, metadata 6, build-push 7,
      download-artifact 8 merged; #9 (`python:3.15-slim`) closed; base pinned to
      `python:3.13-slim`; dependabot grouping live. #23 / 4b73c656

## Phase 6 — publishing (needs the user, do not start unasked)
- [ ] PyPI pending publisher: project `mcp-hub`, owner `tibatechapp-ship-it`,
      workflow `publish.yml`, environment `pypi` (trusted publishing / OIDC — no token)
- [ ] dry run: `workflow_dispatch` on publish.yml → build job only, inspect the wheel
- [ ] bump to 1.4.0 + CHANGELOG, tag `v1.4.0` (a tag push *does* trigger workflows, unlike
      a release created with GITHUB_TOKEN)
- [ ] confirm the PyPI page, SBOM asset and provenance attestation
- [ ] docs site published (Pages) once the repo is public-facing

## Decisions
- Payload cap must be raw ASGI middleware; `BaseHTTPMiddleware` turns a 413 into a 500 once
  the response has started streaming.
- Coverage floor 88 with 93.5% measured — a 5-point cushion absorbs platform drift.
- `next_offset` is exact (fetch `limit + 1`), never a guess.
- Rate limit shared store uses wall-clock time (monotonic clocks are not comparable across
  processes).
- Dependabot groups minor+patch; majors stay single so they can be reverted alone.
- Publishing stays last: a published artifact is a promise, and the trust surface (CSP, auth,
  backup/restore, migration) had to be real first.
