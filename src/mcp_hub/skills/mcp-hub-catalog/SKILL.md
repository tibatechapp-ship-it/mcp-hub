---
name: mcp-hub-catalog
version: 1.0.0
description: Read-only workflow for discovering MCP catalog tools safely
mentioned_tools: search_servers, get_server, query_catalog, route_task
---

# mcp-hub catalog skill

Use this skill only for read-only catalog discovery.

1. Call `route_task` with the user's Arabic or English task to shortlist tools.
2. Call `query_catalog` when readiness, tags or access mode matter.
3. Call `search_servers` or `get_server` for details.
4. Report the tool names and why they match. Do not invent results.

This skill documents tool names only. Reading it does not grant permission to
execute any tool; the configured Phase 2 policy gateway still decides every
`tools/call`.
