# Arabic playbooks (`.arflow`)

Phase 8 adds a thin playbook layer above the Arabic command DSL documented in
[`arabic_dsl.md`](arabic_dsl.md). A `.arflow` file is UTF-8 text with Arabic
comments, variable definitions and one DSL command per line. It introduces no
new MCP method, tool spec, resource spec, policy or approval state: every step
is compiled to an `arabic_dsl.Command` and dispatched through the same
`mcp_stateless.handle()` gateway as `POST /mcp`.

## Syntax

```text
# تعليق عربي
ليكن q = البحث
اعرض الكتالوج
ابحث عن أداة ${q}
? حالة المهمة t-missing
```

- Comments start with `#` or `//`.
- Definitions use `عَرِّف`, `ليكن`, `لتكن`, `set`, `let` or `var` with `=`.
  Values may be quoted; separators `;`, `؛`, newlines and carriage returns are
  rejected.
- `${name}` is accepted **only in free-text query/task arguments**. The compiler
  proves this with an ASCII digit sentinel parsed by the real DSL parser, so
  identifiers, skill/task names, actors, reasons and audit text are never
  substituted.
- Steps are numbered in order. `?` before a command, or `اختياري` / `optional`
  before or after it, marks the step optional: its failure is recorded in
  `optional_failures` and the run continues. Normal steps keep fail-fast
  behaviour and set `stopped_at`.
- More than one command on a line (`;` or `؛`) is rejected with `BAD_ARGUMENT`.

## Usage

```sh
mcp-hub --db catalog.sqlite3 arflow check --file flow.arflow --json --lang ar

mcp-hub --db catalog.sqlite3 arflow run --file flow.arflow \
  --var q=بديل --dry-run --json

mcp-hub --db catalog.sqlite3 arflow run --file flow.arflow \
  --tenant t-1 --session s-1 --actor المراجع --json
```

| Option | Meaning |
| --- | --- |
| `--file` | UTF-8 `.arflow` playbook (required) |
| `--var NAME=VALUE` | Override a definition; overrides win over in-file values |
| `--dry-run` | `run` only: print the translated plan, run no handlers |
| `--tenant`, `--session`, `--actor` | Context passed verbatim to every step |
| `--json` | Compact stdout; structured failures on stderr |
| `--lang ar` | Add `message_ar` to diagnostics |

`check` never executes handlers. It returns `status`, stable `issues` and the
resolved dry-run `plan`. `run` returns per-step status, `stopped_at`,
`optional_failures` and the final `exit_code`.

Exit codes: `0` success, `1` runtime error, `2` bad input or policy/task denial
(`-10002` / `-10003`), matching `arabic-dsl`.

## ID and digit limits

The same limits as the DSL apply: IDs match `[A-Za-z0-9._:@-]{1,128}`, and
Arabic-Indic digits (`٠-٩`, `۰-۹`) are rejected in task/skill names,
`tenant_id`, `session_id`, `actor` and server IDs with exit `2`. `${name}`
substitution is allowed only in free-text query/task arguments; digit folding
is used for matching, not for audit data. See
[`arabic_runtime.md`](arabic_runtime.md).

## Stable check codes

`EMPTY_PLAYBOOK`, `UNKNOWN_COMMAND`, `BAD_ARGUMENT`, `UNDEFINED_VARIABLE`,
`DUPLICATE_DEFINITION`, `INVALID_DEFINITION`, `INVALID_VARIABLE_VALUE`,
`SUBSTITUTION_OUTSIDE_QUERY`, `PLAYBOOK_TOO_LARGE`.

## Audit

Every `run` appends one playbook-level row to the `arflow_audit` SQLite table —
name, original raw text, resolved variables, plan, steps, status, exit code and
the verbatim tenant/session/actor/lang. Individual steps keep writing their
normal `arabic_dsl_audit` rows. Arabic text is stored unchanged; only query
matching uses `normalize_for_match()`. These audit tables are append-only and
have no built-in retention or vacuum; manage their growth at the SQLite file
level.

`ui://mcp-hub/*` resources stay read-only RTL surfaces: playbooks can display
them but never create write-method `postMessage` calls.
