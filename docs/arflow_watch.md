# Bounded `arflow` watch

Phase 10 adds a bounded watch layer above the incremental Arabic testing engine
from [`incremental_testing.md`](incremental_testing.md). It watches one
`.arflow` playbook plus linked files, reuses Phase 9 step fingerprints and
re-runs only the steps affected by a linked-file change. It adds no MCP method,
tool spec, resource spec, handler name or write method to Apps `ui://`
resources.

## Linked files

A linked file is a UTF-8 or binary file under the playbook's directory. It can
be declared in a playbook comment or passed on the command line:

```text
# watch: notes.txt
// راقب: prompts/first.txt
ليكن q = notes.txt
ابحث عن أداة ${q}
```

```sh
mcp-hub --db catalog.sqlite3 arflow watch --file flow.arflow \
  --watch-file notes.txt --json --lang ar
```

- Paths must be relative to the playbook directory; `..` and absolute paths are
  refused.
- Each file is capped at 1 MiB; at most 64 files can be watched.
- A file is mapped to a step only when its relative path or basename appears in
  that step's resolved command text or parameters. An unrelated file change is
  recorded but does not invalidate that step.
- The playbook itself is watched through its compiled fingerprint: comments and
  insignificant whitespace do not cause a re-run.

## Bounded run contract

`arflow watch` is bounded by default and suitable for CI:

```sh
mcp-hub arflow watch --file flow.arflow --once --json
mcp-hub arflow watch --file flow.arflow --max-cycles 5 --interval 2 --settle 0.25 --json
```

- `--once` runs exactly one cycle.
- `--max-cycles N` caps the run at `N` cycles (`1`-`1000`, default `1`).
- `--interval SECONDS` waits between cycles (`0`-`60`, default `1.0`).
- `--settle SECONDS` re-reads changed files after a short debounce (`0`-`10`,
  default `0.1`).
- The loop stops early when nothing changed or when a required step fails. It
  never runs forever.

On each changed cycle the command computes Phase 9 fingerprints, overlays
SHA-256 digests of the affected linked files and calls
`incremental.run_incremental()`. Unchanged successful steps are adopted; the
first affected step and every later step are re-run through the unified Arabic
DSL gateway with fail-fast preserved. If a watched file or playbook fingerprint
changes while a run is in flight, the cycle is marked failed with
`toctou_mismatch`.

## CLI options

| Option | Meaning |
| --- | --- |
| `--file` | UTF-8 `.arflow` playbook (required) |
| `--watch-file PATH` | Extra linked file, relative to the playbook directory; repeatable |
| `--var NAME=VALUE` | Override a definition; overrides win over in-file values |
| `--tenant`, `--session`, `--actor` | Context passed verbatim to every step |
| `--reason` | Audit reason kept verbatim, including Arabic text |
| `--json` | Compact stdout; structured failures on stderr |
| `--lang ar` | Add `message_ar` to diagnostics and failure summaries |
| `--once`, `--max-cycles`, `--interval`, `--settle` | Bounded watch controls |

Exit codes stay `0` success, `1` runtime/not-found and `2` bad input. JSON error
keys and stderr routing match the other Arabic CLI commands. IDs match
`[A-Za-z0-9._:@-]{1,128}`; Arabic-Indic digits (`٠-٩`, `۰-۹`) are rejected in
IDs, actors and reasons. Only free-text queries use digit folding for matching.

## Report

The JSON report contains `cycle_count`, `stop_reason`, `watched_files`,
`linked_fingerprints`, `toctou_mismatch` and a `cycles` array. Each cycle
includes `changed_files`, `fingerprint_changed`, `ran`, `status`, `exit_code`
and the full Phase 9 `report` with its adopted/rerun step states.

## Audit

Every cycle appends one row to `arflow_watch_audit`: run id, cycle number,
playbook name, original playbook text, watched-file digests, linked step
fingerprints, changed files, full report, status, exit code and the verbatim
tenant/session/actor/reason/lang. Step-level results remain in
`arflow_incremental_audit` and `arabic_dsl_audit`. All audit tables are
append-only; manage growth at the SQLite file level.

## Invariants

- Default-deny policies, approval checks and SHA-256 guards are untouched.
- `PINNED_TOOL_MANIFEST_SHA256`, `TOOL_SPECS`, `RESOURCE_SPECS` and handler names
  are frozen.
- Apps `ui://` resources stay read-only RTL; watch adds no write control.
- The first affected step and every later step are re-run; optional failures
  continue, required failures stop the cycle.
