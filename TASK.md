# GOAL: ship mcp-hub Phase 4 — swappable OTel adapter with GenAI SemConv + selective MCP Apps ui:// resources + coverage back to 90%+ — via GitHub API (branch -> PR -> green CI -> report; NO merge, NO tag until explicit approval).
# Plan pointer: user direction 2026-11-24 after PR #46 merge; constraints in chat + vault note 2026-11-24-mcp-hub-phase4-start.md.
# Repo: tibatechapp-ship-it/mcp-hub. Base main after PR #46 merge: 394fd1cf3376a36d3ea16909ba55dce5a057d5f0.
# Scratch: $TMPDIR/mcp-hub-phase4/tibatechapp-ship-it-mcp-hub-394fd1c. Verify: ruff format src tests && ruff check src tests && ruff format --check src tests && mypy && PYTHONPATH=src python3 -m pytest -m "not slow" --cov=mcp_hub --cov-report=term-missing -q && mkdocs build --strict && python3 -m pip wheel . --no-deps -w "$TMPDIR/phase4-wheel".

## Numbered plan
- [x] 1. Fresh scratch from main after PR #46 + create registry.
- [x] 2. Inspect current MCP core, telemetry seams, skill/task methods, API, CLI, DB helpers, docs, tests.
- [x] 3. Implement swappable telemetry adapter: interface + SQLite structured log default, optional OTel adapter, fail-open when disabled/unavailable.
- [x] 4. Add GenAI SemConv + SEP-414 passthrough for traceparent/tracestate/baggage in params._meta, low-cardinality span names.
- [x] 5. Enforce telemetry privacy: tool input/output logging opt-in and OFF by default; no synthetic conversation IDs from text hashing.
- [x] 6. Implement selective MCP Apps `ui://` resources (SEP-1865) for sensitive approval review + tool catalog: sandboxed iframe, JSON-RPC over postMessage, strict CSP, no secrets.
- [x] 7. Add graceful degradation to plain text / structured JSON for clients without UI support.
- [x] 8. Wire telemetry + apps into stateless MCP core, API, CLI, docs, packaging and mkdocs nav.
- [x] 9. Add edge tests for Skills, Tasks, telemetry adapter and MCP Apps; raise coverage to 90%+.
- [x] 10. Run all local gates (ruff check/format, mypy, pytest, mkdocs strict, wheel build); fix every failure.
- [x] 11. Push one atomic commit via Git Data API, open PR, watch CI to green, report readiness. DO NOT merge. DO NOT tag.
- [ ] 12. Wait for explicit approval before merge/Phase 5.

## Key decisions so far
- Pure stdlib runtime; OTel adapter is optional and must never be required for startup or requests.
- SQLite structured logs remain the default sink; OTel is selected only by explicit config and degrades safely.
- Trace context keys are passed through exactly as SEP-414 names in `params._meta`, without prefix or rewriting.
- Tool payload logging is opt-in and disabled by default; hashed text never becomes a conversation/session ID.
- UI resources are selective: approval review and tool catalog only; no generic or secret-bearing UI.
- UI is an enhancement, not a dependency: clients ignoring `ui://` still get text/structured JSON.
- No merge/tag until explicit "انجز" after PR review.
- `catalog_tools.RESOURCE_SPECS` and `PINNED_TOOL_MANIFEST_SHA256` stay unchanged; UI resources are merged in `api.py`.
- Telemetry failures are swallowed; they never fail an MCP request.
- Final local gates green: `GATES_EXIT=0`; coverage `90.43%` (target 90%+, project floor 88%).
- Gate output: `/data/data/com.termux/files/home/.local/share/zyvo/tool-output/tool_20ec055a1001bOSQ01MdLkgrA1`.
- PR #47: https://github.com/tibatechapp-ship-it/mcp-hub/pull/47 — branch `feat/phase4-otel-apps-coverage`, commit `4a7910985732555ef41c89655d0ab74730a9c470`, state OPEN, `mergeStateStatus=CLEAN`.
- PR CI run `45456868727`: 12/12 check-runs success; GitGuardian, Trivy, docs, test 3.10-3.13 + 3.14 advisory, mcp-server lanes and image-scan all pass.

## NOW
Steps 1-11 complete; PR #47 is green and ready for user review. Wait for explicit approval before merge/Phase 5. No merge and no tag.
