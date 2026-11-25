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

### Text normalization

Two layers are deliberately separate:

- `normalize_arabic_text()` is for display and free-text queries. It converts
  Arabic-Indic digits (`٠-٩`) and Extended Arabic-Indic digits (`۰-۹`) to
  ASCII, removes tatweel, folds alef/ya variants and collapses whitespace.
- `normalize_for_match()` is the single shared matcher used by
  `semantic_router` and `catalog_query`. It also strips harakat and folds
  ta-marbuta to haa; `strip_al=True` drops a leading definite article.

Digit conversion applies only to query/counter text. `task_id`, `tenant_id`,
`session_id`, skill names, actors, reasons and audit text keep their original
bytes. A CLI call with Indic digits in an id fails with exit code `2` instead of
silently rewriting the id. ASCII inputs are unchanged.

### Arabic diagnostics

The five approval commands accept `--lang ar` (or `MCP_HUB_LANG=ar` /
`MCP_HUB_LANG=العربية`). English stays the default. Failures keep the stable
machine fields:

```json
{"command":"skill-approve","status":"failed","error":"...","exit_code":1,"message_ar":"غير موجود: ..."}
```

Arabic diagnostics also recognize policy denial (`-10002`), task denial
(`-10003`), skill fingerprint/digest mismatch and missing permission, mapping
them to stable phrases such as `رفضت السياسة العملية` and `عدم تطابق البصمة`.

`--json` stays compact, errors stay on stderr, and exit codes remain `0`, `1`
and `2`. `message_ar` is additive; clients that do not understand it can ignore
it.

### RTL review UI

The two allowlisted `ui://` resources accept `params.lang = "ar"` or
`MCP_HUB_UI_LANG=ar`. The HTML gets `lang="ar" dir="rtl"`, Arabic headings and
a strict re-computed script hash CSP. The JSON fallback reports `lang` and
`dir`. The UI is still read-only: it renders an explicit `للقراءة فقط` notice,
never emits buttons or write-method `postMessage` calls, and exposes
`available_actions` metadata only.

## Path 2 integration contract

Phase 7 implements this contract as `mcp-hub arabic-dsl`; see
[`arabic_dsl.md`](arabic_dsl.md). It is a thin translator, not a second security
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

Use `--json` and branch on `exit_code`. Pass ids, actors and reasons exactly as
stored; the translator must not normalize them.

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

The DSL never invents approval state or bypasses the default-deny `approvals`
policy. Its SQLite audit table records the original command and result for
observability; authorization still lives in the MCP gateway.
