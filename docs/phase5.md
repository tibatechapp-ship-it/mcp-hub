# Phase 5 — actionable approvals

Phase 5 closes the gap between approval data and the MCP surface. The approval
writes that already existed in SQLite and the CLI are now reachable through
explicit JSON-RPC methods, behind a separate policy section. Everything stays
local, deterministic and dependency-free.

## Default-deny `approvals` policy

`skills.allow: ["*"]` only grants **reads**. Approval writes use a new top-level
section:

```json
{
  "version": 1,
  "name": "office-laptop",
  "tools": {"allow": ["search_*"], "deny": []},
  "skills": {"allow": ["*"], "deny": []},
  "approvals": {"allow": ["skills.approve", "tasks.approve"], "deny": ["skills.revoke"]}
}
```

- `approvals.allow` defaults to `[]`; no approval action is permitted unless it
  is explicitly listed.
- `approvals.deny` wins over `allow`; both use shell-style `fnmatch` patterns.
- An invalid policy fails closed with JSON-RPC `-10002` (`POLICY_DENIED`).
- `mcp-hub policy-snapshot` now includes `approvals_allow` and
  `approvals_deny`, never audit paths or regex bodies.

The governed actions are `skills.approve`, `skills.revoke` and `tasks.approve`.
`tasks/list` is a scoped read and is **not** an approval write.

## MCP methods

| Method | Identifying header | Purpose |
| --- | --- | --- |
| `skills/approve` | `Mcp-Name: <name>` | Pin the current skill SHA-256 digest |
| `skills/revoke` | `Mcp-Name: <name>` | Remove the pinned digest |
| `tasks/approve` | `Mcp-Name: <task_id>` | Move `awaiting_approval` → `queued` once |
| `tasks/list` | none | List tasks in a tenant/session scope |

All four require `MCP-Protocol-Version` and `Mcp-Method` headers, like the rest
of the stateless endpoint. `skills/approve`, `skills/revoke` and `tasks/approve`
also mirror `params.name` / `params.task_id` in `Mcp-Name`. `tasks/list` rejects
an unexpected `Mcp-Name` header.

Approval attempts with no configured `approvals` allow rule return
`-10002`. Task isolation failures still return `-10003` (`TASK_DENIED`), so a
wrong tenant/session cannot be distinguished from a missing task.

## Digests and audit

Skill approvals remain bound to a canonical SHA-256 digest: editing a metadata
field or any file makes `approved` flip to `false` until the new digest is
approved again. Every approve/revoke appends a redacted row to
`skill_approval_events` (created with `CREATE TABLE IF NOT EXISTS` — no
migration, no new dependency). Task approvals append a redacted `approved`
event to `agent_task_events` and use a conditional `UPDATE` under
`BEGIN IMMEDIATE`, so two concurrent approvers cannot both win.

## MCP Apps stay read-only

`ui://mcp-hub/approval-review` now includes `available_actions` metadata and a
`read_only_ui: true` flag. The HTML still has a sandboxed iframe, strict CSP,
JSON-RPC over `postMessage` for reads, and a JSON fallback. This slice adds no
buttons and no write path through the UI.

## CLI

```bash
mcp-hub policy-snapshot
mcp-hub skill-approve mcp-hub-catalog --actor reviewer
mcp-hub skill-revoke mcp-hub-catalog --actor reviewer
mcp-hub task-list --tenant acme --session s1 --limit 20
mcp-hub task-approve TASK --tenant acme --session s1 --actor reviewer --reason "checked"
```

The pinned tool manifest is unchanged: these are MCP methods, not new tool
definitions.
