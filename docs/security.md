# Security model

Full detail lives in [SECURITY.md](https://github.com/tibatechapp-ship-it/mcp-hub/blob/main/SECURITY.md)
and the audit notes in
[SECURITY_AUDIT.md](https://github.com/tibatechapp-ship-it/mcp-hub/blob/main/SECURITY_AUDIT.md).
This page is the summary.

## What is protected

| Risk | Control |
| --- | --- |
| Anyone on the network reading your catalog | Binds `127.0.0.1` by default; API key optional |
| Key leakage through timing | Constant-time compare over every configured key |
| Write access for a read-only integration | `:ro` keys → `403` on `POST`/`DELETE` |
| Oversized or slowloris bodies | 1 MiB cap enforced before the app runs, chunked bodies included |
| Brute force / scraping | 120 req/min per IP, `429` + `Retry-After`; SQLite store for multi-worker |
| SSRF through remote fetch | `safe_fetch.py` blocks private/link-local/metadata addresses and re-validates redirects |
| Secrets in error output | `redact.py` scrubs tokens from messages, URLs and 500 bodies |
| XSS in the dashboard | Nonce-based CSP, no inline event handlers |
| Clickjacking / MIME sniffing | `X-Frame-Options: DENY`, `nosniff` |

## What is **not** protected

- **No TLS.** Run behind Caddy/nginx/Cloudflare Tunnel. Plain HTTP exposes the
  API key to every device on the path.
- **No user accounts or audit trail per user.** One key list, no identity.
- **No encryption at rest.** The SQLite file is readable by anyone who can read
  the filesystem. Use disk encryption if that matters.
- **The dashboard keeps the key in `localStorage`.** Prefer putting the service
  behind a proxy that injects `X-API-Key`, and leave the prompt empty.
- **Rate limiting is per IP**, and the in-memory store is per process.

## Reporting a vulnerability

Open a private advisory at
<https://github.com/tibatechapp-ship-it/mcp-hub/security/advisories/new>.
Do not open a public issue for an unfixed vulnerability.
