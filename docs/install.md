# Install

## pip

```bash
pip install mcp-hub               # core: CLI + SQLite, no web stack
pip install "mcp-hub[api]"        # + FastAPI, uvicorn, dashboard
pip install "mcp-hub[mcp-server]" # + MCP server entry point
```

Requires Python 3.10+. Tested on 3.10–3.14.

## Docker

```bash
docker run --rm -v "$PWD/data:/data" -p 127.0.0.1:8000:8000 \
  ghcr.io/tibatechapp-ship-it/mcp-hub:latest
```

The image runs as a non-root user, stores the database in `/data`, and has a
`HEALTHCHECK` on `/api/health`.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `MCP_HUB_DB` | `mcp_hub.sqlite3` | Database path used by the MCP server entry point |
| `MCP_HUB_API_KEY` | *(unset)* | Single API key. Unset = API is open |
| `MCP_HUB_API_KEYS` | *(unset)* | `name:key[:ro]` list; wins over `MCP_HUB_API_KEY` |
| `MCP_HUB_CORS_ORIGINS` | localhost:3000/5173 | Comma-separated allowed origins |
| `MCP_HUB_RATE_LIMIT_PER_MIN` | `120` | Requests per minute per client IP |
| `MCP_HUB_RATE_LIMIT_STORE` | `memory` | `sqlite` shares counters across workers |
| `MCP_HUB_RATE_LIMIT_DB` | `mcp_hub-ratelimit.sqlite3` | Store file for the sqlite backend |
| `MCP_HUB_MAX_BODY_BYTES` | `1048576` | Request body cap (1 MiB) |

!!! warning "Multi-worker rate limiting"
    The in-memory limiter counts per process. With `WEB_CONCURRENCY>1` (or
    several uvicorn workers) the effective limit is multiplied by the worker
    count — mcp-hub logs a warning at startup. Set
    `MCP_HUB_RATE_LIMIT_STORE=sqlite` when you run more than one worker.

## Verify the install

```bash
mcp-hub --version
mcp-hub --db /tmp/smoke.sqlite3 doctor
```
