# Native Arabic runtime and the Path 2 interface

Phase 6 tests two complementary designs without touching the stable `v1.6.0`
security core:

1. **Path 1 (this branch):** a native Arabic layer inside `mcp-hub` — input
   normalization, Arabic diagnostics and a read-only RTL approval UI.
2. **Path 2 (documented here, not implemented in this branch):** a lightweight
   Arabic MCP Client / DSL that translates Arabic commands into existing MCP
   methods or the `--json` CLI contract.

Nothing in this branch changes protocol names, `inputSchema` objects, the
pinned tool manifest, the default-deny policy, SQLite audit events or the
read-only nature of the Apps UI.

## What Path 1 adds

### Input normalization

`mcp_hub.arabic_runtime.normalize_arabic_text()` converts Arabic-Indic digits
(`٠-٩`) and Extended Arabic-Indic digits (`۰-۹`) to ASCII, removes tatweel,
folds alef/ya variants and collapses whitespace. The approval CLI applies it
to skill names, task ids, tenant/session ids, actors, reasons and `route-task`
input. ASCII inputs are unchanged.

### Arabic diagnostics

The five approval commands accept `--lang ar` (or `MCP_HUB_LANG=ar` /
`MCP_HUB_LANG=العربية`). English stays the default. Failures keep the stable
machine fields:

```json
{"command":"skill-approve","status":"failed","error":"...","exit_code":1,"message_ar":"غير موجود: ..."}
```

`--json` stays compact, errors stay on stderr, and exit codes remain `0`, `1`
and `2`. `message_ar` is additive; clients that do not understand it can ignore
it.

### RTL review UI

The two allowlisted `ui://` resources accept `params.lang = "ar"` or
`MCP_HUB_UI_LANG=ar`. The HTML gets `lang="ar" dir="rtl"`, Arabic headings and
a strict re-computed script hash CSP. The JSON fallback reports `lang` and
`dir`. The UI is still read-only and exposes `available_actions` metadata only.

## Path 2 integration contract

A future Arabic DSL client should be a thin translator, not a second security
implementation. It can call either transport:

### MCP transport

- Methods: `skills/list`, `skills/approve`, `skills/revoke`, `tasks/list`,
  `tasks/approve`.
- Method names and parameter names stay ASCII and exact: `name`, `task_id`,
  `tenant_id`, `session_id`.
- Read the approval resource with `{"uri": "ui://mcp-hub/approval-review",
  "lang": "ar"}` for an RTL review surface.
- Policy denial, SQLite audit, digest checks and task state transitions remain
  server-side.

### CLI transport

Use `--json` and branch on `exit_code`:

```sh
mcp-hub --db catalog.sqlite3 task-approve "$TASK" \
  --tenant acme --session s1 --actor reviewer --json --lang ar
```

| Exit code | Meaning | DSL wording example |
| --- | --- | --- |
| `0` | success | `تم` / command result |
| `1` | not found | `اعتمد المهارة ...` → `skill-approve` |
| `2` | bad input or invalid state | `نفّذ المهمة ...` → `task-approve` |

### Suggested phrase map (translator-only)

| Arabic phrase | Canonical call |
| --- | --- |
| `اعرض المهارات` | `skills/list` or `skill-list --json` |
| `اعتمد المهارة <name>` | `skills/approve` / `skill-approve <name>` |
| `الغِ اعتماد المهارة <name>` | `skills/revoke` / `skill-revoke <name>` |
| `اعرض المهام` | `tasks/list` / `task-list` |
| `وافق على المهمة <id>` | `tasks/approve` / `task-approve <id>` |

The DSL must never invent approval state, write its own audit log or bypass the
default-deny `approvals` policy. That is exactly the duplication this split is
designed to avoid.
