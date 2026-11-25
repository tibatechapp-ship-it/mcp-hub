# Central governance with mcp-hub

Phase 1 turns `mcp-hub` into a reusable **central governance verifier** for other
repositories. A caller such as `tayf` or `novamind-core` invokes
`governance-gate.yml`, which installs a pinned `mcp-hub` release from the
project's PEP 503 index and runs a read-only-first Arabic `.arflow` playbook
through the same policy gateway, tenant/session scope and SQLite audit used by
the CLI.

Nothing in the pinned tool manifest, tool/resource specs, handler names or
default policies changes. Apps `ui://` resources remain read-only RTL, and
identifiers, actors, reasons and Arabic text stay verbatim in the audit record.

## Inputs and outputs

The root [`action.yml`](https://github.com/tibatechapp-ship-it/mcp-hub/blob/main/action.yml) is a composite action with these inputs:

| Input | Default | Meaning |
| --- | --- | --- |
| `playbook-path` | required | UTF-8 `.arflow` file to `check`, `test` or `run` |
| `mode` | `test` | `check`, `test` or `run` |
| `incremental` | `true` | Reuse unchanged steps for `run`; `test` already defaults to true; ignored by `check` |
| `changed-only` | `false` | Alias for incremental adoption with fail-fast re-run after changes |
| `lang` | `ar` | Diagnostic language: `ar` or `en` |
| `tenant` | `local` | Tenant id passed verbatim to the audit record |
| `audit-db` | `mcp-hub-governance.sqlite3` | SQLite audit database path |
| `version` | `1.9.0` | mcp-hub version installed from PEP 503 |
| `index-url` | <https://tibatechapp-ship-it.github.io/mcp-hub/simple/> | PEP 503 simple index |

It outputs `exit-code` and a one-line AR/EN `summary`, and writes a bilingual
block to `$GITHUB_STEP_SUMMARY`.

The reusable workflow
[`governance-gate.yml`](https://github.com/tibatechapp-ship-it/mcp-hub/blob/main/.github/workflows/governance-gate.yml) adds a cached
SQLite audit database, an mcp-hub action checkout at `action-ref`, and a final
summary step. Its `workflow_call` inputs mirror the action inputs plus
`action-ref` (default `main`).

## Caller example

In `tayf` or `novamind-core`:

```yaml
name: governance
on:
  push:
    branches: [main]
  pull_request:

jobs:
  governance:
    uses: tibatechapp-ship-it/mcp-hub/.github/workflows/governance-gate.yml@main
    with:
      playbook-path: templates/arflow/security-check.arflow
      mode: test
      incremental: true
      lang: ar
      tenant: tayf-main
      audit-db: mcp-hub-governance.sqlite3
```

Copy a template from [`templates/arflow/`](https://github.com/tibatechapp-ship-it/mcp-hub/tree/main/templates/arflow) into the caller
repo before wiring the gate:

- `policy-check.arflow` — policy snapshot, resources, stats and optional read-only review UI.
- `security-check.arflow` — policy snapshot, health checks and an optional write that must be denied under default-deny.
- `release-readiness.arflow` — catalog and health required; approval-required task listing and missing task are advisory.

Use `mode: check` for a fast compile check, `mode: test` for an incremental
Arabic test with expectations, and `mode: run` for a full pass-through execution.
`run` accepts `--incremental`; `test` is incremental by default and can fall back
with `incremental: false`; `--no-incremental` is never passed to `run`.

## Making the gate mandatory

1. Add the caller workflow above to the linked repository.
2. In GitHub **Rulesets** for the protected branch, require the
   `governance / gate` check (or the job name used in that repo).
3. Keep the ruleset in place for admins too if the gate must be universal.
4. Use a stable `tenant` per repository so the cached audit DB does not mix
   tenants, and pin `version` when reproducibility matters more than auto-upgrades.

## Audit and cache behavior

The workflow caches the SQLite file named by `audit-db` with a key containing
the runner OS, tenant and a hash of the playbook. Audit tables are append-only;
the cache speeds CI but is not retention. Manage long-term growth at the SQLite
file level, and never write secrets into `tenant`, `actor`, `reason` or playbook
text.

Arabic diagnostics use the same `0/1/2` contract as the CLI: `0` success,
`1` not found/runtime failure, `2` bad input or policy/task denial under an
expectation.

For the local equivalent, see
[Global governance hooks](central_governance_hooks.md): `mcp-hub governance init`
binds a repository to a tenant/playbook/audit DB, and
`mcp-hub governance hooks install|check|uninstall` manages global
`pre-commit`/`pre-push` hooks on the same incremental path.
