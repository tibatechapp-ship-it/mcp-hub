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
