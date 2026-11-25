# Arabic command DSL (Path 2)

Phase 7 implements the Path 2 contract documented in
[`arabic_runtime.md`](arabic_runtime.md). `mcp-hub arabic-dsl` translates Arabic
commands into existing MCP methods; it does not create a second protocol, a
second policy engine or a second approval state.

## Unified execution path

Every step goes through the same gateway as `POST /mcp`:

1. Free-text queries are folded with `arabic_runtime.normalize_for_match()`.
   Identifiers, actors, reasons and the original command text are untouched.
2. `mcp_stateless.handle()` dispatches the method with the active tool, skill,
   approval and Apps policies (default deny).
3. Skill and task approval paths keep their SHA-256 digest / TOCTOU guards and
   tenant/session isolation.
4. Each step is appended to the `arabic_dsl_audit` SQLite table with the
   original Arabic text, resolved method, parameters, result, status and exit
   code.

`ui://mcp-hub/*` resources remain read-only RTL surfaces. The DSL can display
them; it never creates buttons or write-method `postMessage` calls.

## Usage

```sh
mcp-hub --db catalog.sqlite3 arabic-dsl \
  --text 'ابحث عن أداة البحث؛ اعرض الكتالوج' --json

mcp-hub --db catalog.sqlite3 arabic-dsl \
  --file pipeline.txt --dry-run --json --lang ar
```

A pipeline is separated by newlines, `;` or `؛`. Execution stops at the first
failure (fail-fast) and reports `stopped_at`.

| Option | Meaning |
| --- | --- |
| `--text` | Inline command/pipeline (mutually exclusive with `--file`) |
| `--file` | UTF-8 file with one command per line |
| `--dry-run` | Print the translated plan only; no MCP handlers run |
| `--tenant`, `--session`, `--actor` | Context passed verbatim to every step |
| `--json` | Compact stdout; structured errors on stderr |
| `--lang ar` | Add `message_ar` to parse/validation failures |

Exit codes stay stable: `0` success, `1` runtime/parse error, `2` bad input or
policy/task denial (`-10002` / `-10003`).

## ID and digit limits

Identifiers and scope fields are passed verbatim, not normalized. IDs match
`[A-Za-z0-9._:@-]{1,128}` and `server_id` accepts ASCII digits only.
Arabic-Indic digits (`٠-٩`, `۰-۹`) in `task_id`, `server_id`, skill names,
`tenant_id`, `session_id` or `actor` fail with exit `2`. Digit folding applies
only to free-text query/counter matching; see [`arabic_runtime.md`](arabic_runtime.md).

The audit tables are append-only with no built-in retention or vacuum. Manage
their growth at the SQLite file level.

## Command families

| Arabic command | MCP method |
| --- | --- |
| `اكتشف الخادم` | `server/discover` |
| `اختبار الاتصال` | `ping` |
| `اعرض الكتالوج` | `tools/list` |
| `ابحث عن أداة <query>` | `tools/call` → `search_servers` |
| `تفاصيل الأداة <query>` | `tools/call` → `search_servers` |
| `تفاصيل الخادم <id>` | `tools/call` → `get_server` |
| `اعرض الفئات` | `tools/call` → `list_categories` |
| `اعرض سجل التدقيق <id>` | `tools/call` → `provenance_events` |
| `شغّل الفحوصات الصحية` | `tools/call` → `doctor_report` |
| `وجّه الطلب <task>` | `tools/call` → `route_task` |
| `اعرض بيانات السياسة` | `tools/call` → `policy_snapshot` |
| `اعرض الموارد` | `resources/list` |
| `اعرض الإحصاءات` | `resources/read` → `mcp-hub://stats` |
| `اعرض المحفزات` | `prompts/list` |
| `اعرض الموجه <name>` | `prompts/get` |
| `اعرض المهارات` | `skills/list` |
| `تفاصيل المهارة <name>` | `skills/get` |
| `اعتمد المهارة <name>` | `skills/approve` |
| `الغِ اعتماد المهارة <name>` | `skills/revoke` |
| `اعرض المهام` | `tasks/list` |
| `حالة المهمة <id>` | `tasks/get` |
| `حدّث المهمة <id> إلى <status>` | `tasks/update` |
| `الغِ المهمة <id>` | `tasks/cancel` |
| `وافق على المهمة <id>` | `tasks/approve` |
| `حاكي الشخصية <scenario>` | `persona/simulate` |
| `اعرض واجهة المراجعة` | `resources/read` → `ui://mcp-hub/approval-review` |
| `اعرض واجهة الكتالوج` | `resources/read` → `ui://mcp-hub/tool-catalog` |

English aliases are accepted for every phrase. Protocol method names, tool
names, resource URIs and `inputSchema` objects remain unchanged.

## Dry-run plan

`--dry-run` returns one entry per step with `step`, `raw`, `label`, `method`,
`params`, `approval_required` and, for UI resources, `read_only_ui`. No MCP
handler runs; the plan is also recorded with status `dry_run`. This makes it
safe to review approval requirements before any write.
