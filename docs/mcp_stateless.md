# Stateless MCP endpoint

`mcp-hub` exposes a stateless JSON-RPC endpoint at `POST /mcp` that follows the
MCP `2026-07-28` revision. There is no `initialize` handshake and no
`Mcp-Session-Id`: every request carries its own version and client metadata.

## Required headers

Every POST must include:

| Header | Must match |
| --- | --- |
| `MCP-Protocol-Version` | `params._meta["io.modelcontextprotocol/protocolVersion"]` |
| `Mcp-Method` | the JSON-RPC `method` |
| `Mcp-Name` | `params.name` for `tools/call` and `prompts/get`; `params.uri` for `resources/read` |

Header names are case-insensitive; values are case-sensitive. When a value
cannot be represented safely in an HTTP header (non-ASCII, control characters,
leading/trailing whitespace, or a literal Base64 sentinel), clients MUST send
the Base64 sentinel form:

```text
Mcp-Name: =?base64?{base64-of-UTF-8-value}?=
```

The server decodes before comparing. A mismatch, a missing required header, or
a malformed value returns HTTP `400` with JSON-RPC error `-32020`
(`HeaderMismatch`). An unsupported version returns `-32022`.

Optional `Mcp-Param-{Name}` headers are validated when present: booleans use
`true`/`false`, numbers are compared numerically, and strings must match exactly.

## Example

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/list' \
  -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "curl", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {}
          }
        }
      }'
```

When `MCP_HUB_API_KEYS` or `MCP_HUB_API_KEY` is configured, `/mcp` requires a
key like the other gated routes.

## Anti-rug-pull manifest

The tool contracts are pinned by a SHA-256 digest in `catalog_tools.py`.
`tools/list` and `tools/call` recompute the digest and verify that handler names
match the pinned specs. Any silent change to a description, schema or tool name
returns application error `-10001` (`Tool manifest changed; approval required`)
instead of exposing unreviewed behaviour. `server/discover` publishes the
current digest so clients can detect drift.

List results carry `ttlMs` and `cacheScope` as required by the 2026 revision.
All catalog tools remain read-only.
