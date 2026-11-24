# Phase 4 — telemetry and MCP Apps

Phase 4 adds optional observability and two small `ui://` resources. It does
not add a runtime dependency: SQLite structured events are the default sink,
OpenTelemetry is opt-in, and a broken or missing SDK never breaks an MCP
request.

## Telemetry adapters

`mcp_hub.telemetry` exposes one small interface: `start()`, `emit()` and
`finish()`. `build_telemetry()` selects the implementation from the environment:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCP_HUB_TELEMETRY` | `sqlite` | `off`, `sqlite`, `otel` or `auto` |
| `MCP_HUB_TELEMETRY_LOG_TOOL_IO` | `false` | Include redacted tool arguments/results |

- **SQLite** writes `telemetry_events` rows in the catalog database. This is
  the dependency-free default.
- **OTel** uses `opentelemetry.trace` only when configured. If the import or a
  span operation fails, the adapter falls back to SQLite or no-op; it never
  raises into the MCP loop.
- **Off** is a no-op sink.

Span names come from the bounded MCP method allowlist, for example
`mcp.tools.call` and `mcp.resources.read`. GenAI semantic-convention attributes
include `gen_ai.system`, `gen_ai.operation.name` and `gen_ai.tool.name`.

Tool arguments and results are **not** recorded unless
`MCP_HUB_TELEMETRY_LOG_TOOL_IO` is enabled. When it is enabled, values pass
through the existing redactor and are truncated. No conversation id is ever
invented: only an explicit caller-provided `conversation_id` in `params._meta`
is copied into telemetry.

## Trace context

SEP-414 trace fields stay verbatim inside `params._meta`:

```json
{
  "traceparent": "00-<32 hex>-<16 hex>-01",
  "tracestate": "vendor=value",
  "baggage": "tenant=acme"
}
```

The server echoes the same keys in `_meta` on JSON-RPC responses. It does not
rename them, add a vendor prefix, or require new HTTP headers.

## MCP Apps

Two `ui://` resources are exposed:

| URI | Purpose |
| --- | --- |
| `ui://mcp-hub/approval-review` | Review pinned skill approvals and tasks awaiting approval |
| `ui://mcp-hub/tool-catalog` | Browse the read-only tool contracts |

Each response contains:

1. `text/html` — a document with a strict hash-based CSP and a nested
   `sandbox="allow-scripts"` iframe. The UI sends `tools/list` or `skills/list`
   as JSON-RPC over `postMessage` and renders the answer.
2. `application/json` — a text fallback with the same local snapshot, for
   clients that do not render HTML.

Only those two URIs pass the allowlist policy; any other `ui://` URI returns
`POLICY_DENIED`. The documents never render environment variables, tokens or
task payloads. Set `MCP_HUB_APPS_ENABLED=off` to remove the resources entirely.

## CLI

```bash
mcp-hub telemetry-status
mcp-hub app-resources
```
