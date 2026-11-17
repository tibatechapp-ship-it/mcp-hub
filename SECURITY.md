# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.2.x | Yes |
| 1.3.x | Yes |
| < 1.2 | No |

## Threat model

mcp-hub is a **local-first** tool. Defaults assume trusted local use:
- Server binds to `127.0.0.1`
- No API key required
- SQLite DB stored on local disk
- Outbound requests go to HTTPS endpoints (MCP registry, GitHub)

## Hardening for exposed deployments

1. Set `MCP_HUB_API_KEY` (or `--api-key`) when binding to non-local addresses.
2. Run behind a reverse proxy with TLS (Caddy/nginx/Traefik).
3. Restrict network exposure (firewall, localhost port forward, VPN).
4. Mount `/data` on trusted storage and back it up.
5. Treat GitHub tokens as secrets; prefer read-only scopes.
6. Do not point enrichment/health checks at endpoints you do not own or trust.

## Rate limiting and payload limits (1.3.0+)

Every `/api/*` endpoint except `/api/health` and `/api/doctor` is
rate-limited to **120 requests per minute per client IP** by default
(override with `MCP_HUB_RATE_LIMIT_PER_MIN`, or `0` to disable). A refused
request gets `429` with `Retry-After: 60`.

`X-Forwarded-For` is trusted **only** when the direct peer is loopback — i.e.
when a reverse proxy runs on the same host. Any other client is keyed on its
socket address, so a remote caller cannot rotate its bucket by spoofing the
header.

`POST`/`PUT`/`PATCH` bodies larger than **1 MiB** are rejected with HTTP 413
(override with `MCP_HUB_MAX_BODY_BYTES`). A declared `Content-Length` is
checked before the body is read; a chunked upload without the header is
buffered only up to the cap and rejected the moment it is crossed, before the
application runs. Memory is bounded by the cap, never by the upload size.

**Multiple workers.** The default bucket store is in-process, so with N
`uvicorn` workers the effective limit is N × the configured value. The server
logs a warning when `WEB_CONCURRENCY > 1`. Set
`MCP_HUB_RATE_LIMIT_STORE=sqlite` (and optionally `MCP_HUB_RATE_LIMIT_DB`) to
share buckets between processes on one host.

## Authentication (1.3.1+)

`MCP_HUB_API_KEY` sets a single key. `MCP_HUB_API_KEYS` supersedes it with a
list — `name:secret` or `name:secret:ro`, comma-separated:

```
MCP_HUB_API_KEYS="ci:KEY_READ_ONLY:ro,admin:KEY_FULL"
```

A `:ro` key may use `GET`/`HEAD`/`OPTIONS` only; any write is answered with
`403`. Keys are compared with `hmac.compare_digest` against **every** configured
key (no early exit), so response time does not reveal which key matched.
`/api/health` stays open so container healthchecks and probes work without a
credential.

## Response headers (1.3.1+)

Every response carries `Content-Security-Policy` (nonce-based, no
`unsafe-inline`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, `Permissions-Policy` and
`Cross-Origin-Opener-Policy`. `Strict-Transport-Security` is added only when the
request arrived over TLS (directly or via `X-Forwarded-Proto: https`) — never on
plain HTTP, so a localhost session is not poisoned.

## Continuous security scanning (1.3.0+)

Every push and pull request runs:

| Tool | Scope | Blocks on |
|---|---|---|
| bandit | Python source (SAST) | HIGH severity |
| semgrep | `p/python` + `p/security-audit` | ERROR rules |
| pip-audit | Python dependencies | any known CVE |
| pip-licenses | dependency licences | GPL / AGPL |
| Trivy | Docker image (CRITICAL + HIGH, fixed CVEs only) | any finding |

`bandit.json` and `LICENSES.md` are uploaded as the `security-reports`
artifact on every run; the Trivy SARIF report lands in the repository's
Security tab (code scanning).

Dependabot opens weekly pull requests for pip dependencies, GitHub Actions
pins and the Docker base image. GitHub Secret Scanning (with push protection)
and CodeQL are enabled on the repository.

## Dependency policy

Runtime and development dependencies carry explicit upper bounds in
`pyproject.toml` (next major), and every GitHub Action is pinned to a full
commit SHA with a `# vX.Y.Z` comment. Dependabot rewrites both — a moving tag
is never trusted at build time.

## SSRF protection (1.3.0+)

Every outbound request made on behalf of a user-supplied URL goes through
`mcp_hub.safe_fetch.safe_request` (standard library only). This covers webhook
alerts and endpoint health probes, plus the registry sync and GitHub enrichment
paths.

### What is refused

- non-HTTPS schemes (`http://`, `ftp://`, `file://`, `gopher://`, …)
- URLs carrying credentials (`https://user:pass@host/`)
- hosts with no hostname, or an invalid port
- any URL whose resolved address is not public — loopback (`127.0.0.0/8`, `::1`),
  RFC 1918 (`10/8`, `172.16/12`, `192.168/16`), CGNAT (`100.64/10`),
  link-local (`169.254/16` — the AWS/GCP/Azure metadata range — and `fe80::/10`),
  unique-local (`fc00::/7`), multicast/reserved ranges, `TEST-NET-*`,
  IPv4-mapped IPv6 (`::ffff:0:0/96`) and IPv4/IPv6 translation prefixes
- redirects whose target fails the same checks
- response bodies larger than the cap (1 MiB by default, 8 MiB for registry pages)
- redirect chains longer than 3 hops

DNS is resolved **once per request**; if *any* returned address is non-public the
request is refused (fail closed). Unparseable addresses are treated as blocked.

### Secret redaction (1.3.0+)

Error details returned to users, logged, or embedded in alert payloads pass
through `mcp_hub.redact.redact`: provider tokens (`ghp_`, `github_pat_`, `AKIA`,
`xox[abprs]-`, `AIza`), `Authorization: Bearer …`, `X-API-Key`, PEM private-key
blocks, connection-string passwords and `?token=`/`&key=`-style query secrets.
The FastAPI app additionally runs with `debug=False` and returns a generic body
on unhandled exceptions, so a traceback never reaches a client.

### Not covered

- **DNS rebinding**: the address is checked once, before the connection. A
  malicious resolver could answer differently later. Use an egress firewall if
  you need a stronger guarantee.
- TLS validation is delegated to Python's `ssl` module and the system CA bundle.
- `alerts` webhooks remain HTTPS-only by policy — plain-HTTP receivers are
  rejected, not downgraded.

## Reporting a vulnerability

Please do **not** open a public issue for security vulnerabilities.

Report privately via GitHub Security Advisories for this repository:
https://github.com/tibatechapp-ship-it/mcp-hub/security/advisories/new

Include reproduction steps, impact, and affected versions if known.
