# HTTP API

Start it with `mcp-hub serve`. Default bind is `127.0.0.1:8000` — put a
TLS-terminating proxy in front before exposing it (see
[Security model](security.md)).

## Authentication

No key configured → the API is open (fine for localhost only).

| Config | Header | Scope |
| --- | --- | --- |
| `MCP_HUB_API_KEY=secret` | `X-API-Key: secret` or `Authorization: Bearer secret` | full |
| `MCP_HUB_API_KEYS="alice:k1,bob:k2:ro"` | same | `:ro` keys may only read |

`/api/health` and `/` never require a key, so a load balancer can probe the
service without credentials. Everything else is gated — including `/metrics`,
which carries catalog counts even though it does not live under `/api/`. A read-only key that tries a `POST`/`DELETE` gets
`403`. Every key is compared in constant time, and all configured keys are
compared (no early exit), so timing does not reveal which key matched.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/health` | `{"status":"ok","version":"…","db":"ok"}`; `503` if the DB is unusable |
| GET | `/api/stats` | Catalog counters |
| GET | `/api/search?q=&fts=&limit=&offset=` | Paged search |
| GET | `/api/servers/{id}` | One record |
| GET | `/api/servers/{id}/provenance` | `?mode=fields\|events`, `?field=` |
| GET/POST | `/api/searches` | List / save (`{"name","query"}`) |
| DELETE | `/api/searches/{name}` | Remove |
| POST | `/api/searches/{name}/run` | Re-run, returns `new_ids` |
| POST | `/api/searches/run-all` | Run every saved search |
| GET | `/api/scheduler/runs?limit=` | Recent runs (1–100) |
| GET/POST | `/api/categories` | Custom rules |
| POST | `/api/categories/classify?overwrite=` | Classify catalog |
| GET | `/api/categories/export`, POST `/api/categories/import` | Round-trip rules |
| GET/POST | `/api/alerts` | Webhook channels |
| GET | `/metrics` | Prometheus text (API key required when one is configured) |

## Paging

`limit` is 1–200 (default 50), `offset` ≥ 0. The response is:

```json
{"count": 50, "offset": 100, "next_offset": 150, "results": [...]}
```

`next_offset` is **exact**: the query fetches `limit + 1` rows, so the last page
returns `"next_offset": null` instead of a link to an empty page. Ordering is
`popularity DESC, id DESC` on both the LIKE and the FTS path, so pages cannot
shuffle between requests.

`fts=1` runs a real FTS5 query: whole-word tokens, so `alpha` does **not** match
`alphabet`. The default (LIKE) path is a substring match, so it does. Past the
end of the FTS result set the page is empty — the two result sets are never
mixed inside one paging session.

## Tracing and metrics

Every response carries `X-Request-ID`. Send your own (`A-Za-z0-9._-`, ≤64
chars) to correlate with your logs; anything else is replaced with a fresh
UUID, so the header cannot be used to inject log lines.

`/metrics` is Prometheus text format:

```
# TYPE mcp_hub_requests_total counter
mcp_hub_requests_total{method="GET",path="/api/search",status="200"} 42
# TYPE mcp_hub_request_duration_seconds summary
mcp_hub_request_duration_seconds_sum 1.204311
mcp_hub_request_duration_seconds_count 42
# TYPE mcp_hub_records gauge
mcp_hub_records 1284
```

Labels use the **route template** (`/api/servers/{server_id}`), not the raw
path, so cardinality stays bounded. Scraping `/metrics` is not counted.

## Errors

| Status | When |
| --- | --- |
| `400` | Invalid payload (missing `name`/`query`/`target`, unparsable category data) |
| `401` | Missing or wrong API key |
| `403` | Read-only key used on a write |
| `404` | Unknown server or saved search |
| `413` | Body over `MCP_HUB_MAX_BODY_BYTES` (also for chunked bodies) |
| `422` | Query parameter out of range (e.g. `limit=0`) |
| `429` | Rate limit hit; `Retry-After` tells you when to retry |
| `500` | Unhandled error — body is `{"detail":"internal error","type":"…"}`, never a traceback |

## Response headers

`Content-Security-Policy` (nonce-based), `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`,
`Cross-Origin-Opener-Policy`. `Strict-Transport-Security` is added only when
the request arrived over TLS.
