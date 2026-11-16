# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.1.x | Yes |
| < 1.1 | No |

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

## Reporting a vulnerability

Please do **not** open a public issue for security vulnerabilities.

Report privately via GitHub Security Advisories for this repository:
https://github.com/tibatechapp-ship-it/mcp-hub/security/advisories/new

Include reproduction steps, impact, and affected versions if known.
