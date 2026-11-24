# Phase 3 — skills, durable tasks and local persona

Phase 3 adds three deterministic surfaces to the stateless MCP endpoint. They
use only the Python standard library, never call the network, and never load a
real credential.

## Skills over MCP

A skill package is a directory containing `SKILL.md` with closed front matter:

```text
---
name: example-skill
version: 1.0.0
description: What this skill is for
mentioned_tools: search_servers, query_catalog
---

Instructions go here.
```

The loader validates:

- package size ≤ 256 KiB, at most 32 files, each file ≤ 64 KiB;
- UTF-8 text only, no symlinks, no absolute or `..` paths;
- exactly one `name`, `version` and `description`, no secret-shaped values;
- a canonical SHA-256 digest over metadata plus every file's path, digest and size.

New methods:

| Method | Purpose |
| --- | --- |
| `skills/list` | List validated skill metadata and approval status |
| `skills/get` | Read one skill's files by `name` |
| `resources/read` | Read one skill file through `mcp-hub://skills/{name}/{path}` |

Reading a skill **never** grants tool execution. The response always contains
`grants_execute: false`, and a tool named inside a skill is still decided by the
Phase 2 `tools` policy. Skill reads have their own `skills.allow` / `skills.deny`
rules; both sections default to `["*"]`, and deny wins over allow.

Approvals are pinned by digest in SQLite. `mcp-hub skill-approve NAME` stores the
current digest; any later metadata or file change makes `approved` flip to
`false` until the new digest is explicitly approved again.

## Durable tasks

Tasks live in SQLite and are scoped by both `tenant_id` and `session_id`. A
request with the wrong scope gets the same `-10003` (`TASK_DENIED`) error as a
missing task, so task existence cannot be probed across tenants.

| Method | Required parameters |
| --- | --- |
| `tasks/get` | `task_id`, `tenant_id`, `session_id` |
| `tasks/update` | `task_id`, `tenant_id`, `session_id` plus fields to change |
| `tasks/cancel` | `task_id`, `tenant_id`, `session_id`, optional `reason` |

Valid statuses are `queued`, `running`, `awaiting_approval`, `completed`,
`cancelled` and `failed`. Every update appends a redacted event to
`agent_task_events`. `TaskStore.reconcile_interrupted()` requeues running tasks
whose lease expired; `TaskStore.cleanup()` removes old terminal tasks and their
events. Both operations honour tenant/session scope.

## MCP-Persona simulator

`persona/simulate` replays a canned chain from local fixtures only:

| Scenario | Exercises |
| --- | --- |
| `safe_chain` | Three read-only tool calls in a deterministic order |
| `retry_then_success` | A failed attempt followed by a successful retry |
| `secret_leak` | A secret-shaped argument that must be redacted |
| `permission_bypass` | A tool named in skill text that is not executable |

The simulator returns a stable transcript, the persisted task id and explicit
`network_calls: 0` / `real_keys_loaded: false` flags. It is a policy test
harness, not an LLM and not a production agent runtime.

## CLI

```bash
mcp-hub skill-list
mcp-hub skill-get mcp-hub-catalog
mcp-hub skill-approve mcp-hub-catalog
mcp-hub skill-revoke mcp-hub-catalog
mcp-hub task-get TASK --tenant acme --session s1
mcp-hub task-cancel TASK --tenant acme --session s1 --reason "user stopped"
mcp-hub persona-simulate secret_leak --tenant acme --session s1
```
