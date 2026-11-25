# Incremental Arabic testing

Phase 9 turns an `.arflow` playbook into an incremental, auditable Arabic test
run. It reuses the existing Arabic DSL, policy gateway, tenant/session scope and
SHA-256 fingerprint guards; it introduces no new MCP method, tool spec, resource
spec or handler name.

## What is fingerprinted

`mcp_hub.incremental` derives deterministic SHA-256 digests from:

- the **compiled plan** (command, resolved params, approval flag, optional flag,
  expectation) — comments and insignificant whitespace are ignored;
- the active `catalog_tools.policy_snapshot()`;
- the catalog source tables (audit tables are excluded so test history does not
  invalidate fingerprints);
- the verbatim `tenant_id`, `session_id`, `actor` and resolved language.

Identifiers, actors, reasons and Arabic text are never normalized for the audit
record. Digit folding is used only for Arabic matching, not for audit data.

## Expectations

An expectation line applies to the **preceding** step and is part of that
step's fingerprint, so changing an assertion forces a re-run.

| Arabic | English alias | Meaning |
| --- | --- | --- |
| `توقع نجاح` | `expect success` | Step exits `0` with status `ok` |
| `توقع فشل` | `expect failure` | Step reports a non-zero exit / failed status |
| `توقع حالة ok` | `expect status ok` | Exact status name |
| `توقع رمز 2` | `expect code 2` | Exact exit code |
| `توقع رفض سياسة` | `expect policy denial` | Error code `-10002` |
| `توقع رفض مهمة` | `expect task denial` | Error code `-10003` |

Denial expectations accept an explicit ASCII integer code too
(`توقع رفض سياسة -10002`). Arabic-Indic digits are rejected in codes, so an
expectation cannot silently change meaning.

Example:

```text
# فحص الكتالوج
اعرض الكتالوج
توقع نجاح

? حالة المهمة t-missing
توقع فشل
```

## CLI

```bash
mcp-hub arflow run --file flow.arflow --incremental --json
mcp-hub arflow test --file flow.arflow --json
mcp-hub arflow test --file flow.arflow --force-full --json
mcp-hub arflow test --file flow.arflow --no-incremental --json
```

- `test` is incremental by default; `--no-incremental` is accepted only by
  `test` and falls back to the plain `arflow run` behaviour. Use
  `arflow run --incremental` to opt into adoption from `run`.
- `--changed-only` is an alias for incremental adoption in `run`.
- `--force-full` ignores stored results and re-runs every step.
- `--dry-run` stays a plan-only operation and never consults prior results.
- JSON and Arabic diagnostics keep the existing `0/1/2` exit contract.

The report includes `fingerprints`, `changed_from`, `adopted_count`,
`rerun_count`, `stopped_at` and a per-step `state` of `rerun`, `adopted`,
`failed` or `rejected`.

`incremental.with_linked_fingerprints()` can overlay SHA-256 digests of linked
files onto individual step fingerprints. `run_incremental(fingerprints=...)`
accepts that overlay unchanged; callers that supply no overlay keep the exact
Phase 9 behavior. [`arflow_watch.md`](arflow_watch.md) uses this seam to rerun
only steps that mention a changed file.

## Audit

Every executed or adopted step is appended to `arflow_incremental_audit` with
the fingerprint, state, status, exit code, full result JSON, original Arabic
line and the verbatim tenant/session/actor. Only the newest row for a step is
considered, and it can be adopted only when its own state is `rerun`/`adopted`
with status `ok`; a newer `failed`/`rejected` row forces a re-run even if an
older successful row has the same fingerprint. Audit tables are append-only:
there is no built-in retention or vacuum, so manage growth at the SQLite file
level.

## Invariants

- Fail-fast remains active for required steps; optional failures continue.
- The first changed step and every later step are re-run.
- Approval/policy denials stay `-10002`, task denials `-10003`.
- Apps `ui://` resources remain read-only RTL; no write control was added.
- The pinned tool manifest and all tool/resource specs are untouched.
