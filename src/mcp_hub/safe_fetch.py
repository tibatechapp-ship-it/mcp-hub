"""SSRF-safe HTTP client (v1.3.0).

Every outbound request made on behalf of a user-supplied URL (webhook alerts,
issue-tracker style callbacks, future custom registry base URLs) must go through
:func:`safe_request`. It refuses anything that could pivot into the local host or
a cloud metadata endpoint:

* non-HTTPS schemes, credentials in the URL, missing hostname
* any resolved address inside a non-public range (RFC 1918, loopback,
  link-local — the AWS/GCP/Azure metadata IP ``169.254.169.254`` lives there —,
  CGNAT, multicast, reserved, IPv6 ULA/link-local, IPv4-mapped IPv6)
* redirects that land on a blocked destination
* responses larger than ``max_bytes`` (default 1 MiB)

Pure standard library: no new runtime dependency for the core package.

Errors are typed so callers can distinguish policy refusal from a network
failure — see :class:`UnsafeURLError`.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_TIMEOUT",
    "UnsafeURLError",
    "safe_request",
    "validate_url",
]

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_REDIRECTS = 3

# IANA special-purpose registry + RFC 1918/6598/4193. Anything here is refused
# before a socket is opened (literal IPs) or after DNS resolution (hostnames).
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)


class UnsafeURLError(ValueError):
    """Raised when a URL (or the address it resolves to) violates policy."""


def _is_blocked(ip: str) -> bool:
    """True when ``ip`` is non-public or unparseable (fail closed)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(addr in net for net in _BLOCKED_NETWORKS)


def _resolve_ips(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {host!r}: {exc}") from exc
    ips = sorted({str(info[4][0]) for info in infos})
    if not ips:
        raise UnsafeURLError(f"Host {host!r} has no addresses")
    return ips


def validate_url(url: str) -> None:
    """Raise :class:`UnsafeURLError` if ``url`` is not safe to fetch."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL is empty")

    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme.lower() != "https":
        raise UnsafeURLError(f"Only https:// URLs are allowed (got {parts.scheme or 'none'!r})")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in URL are not allowed")

    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no hostname")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeURLError(f"Invalid port in URL: {exc}") from exc

    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_literal = False
    else:
        is_literal = True

    if is_literal:
        if _is_blocked(host):
            raise UnsafeURLError(f"Host {host!r} is in a non-public range (SSRF blocked)")
        return

    for ip in _resolve_ips(host, port):
        if _is_blocked(ip):
            raise UnsafeURLError(
                f"Host {host!r} resolves to non-public address {ip} (SSRF blocked)"
            )


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect target before following it."""

    max_redirections = DEFAULT_MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _LimitedResponse:
    """File-like wrapper that aborts once the byte budget is exhausted."""

    def __init__(self, fp, max_bytes: int):
        self._fp = fp
        self._max = max_bytes
        self._read = 0

    def read(self, n: int = -1) -> bytes:
        remaining = self._max - self._read
        if remaining <= 0:
            raise UnsafeURLError(f"Response exceeds max size ({self._max} bytes)")
        chunk = self._fp.read(remaining if n < 0 or n > remaining else n)
        self._read += len(chunk)
        return chunk

    def __getattr__(self, name):
        return getattr(self._fp, name)


def safe_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> tuple[int, bytes]:
    """Perform one policy-checked HTTP request.

    Returns ``(status_code, body)``. Raises :class:`UnsafeURLError` on a policy
    violation (including an oversized body) and ``urllib.error.URLError`` on a
    network failure.
    """
    if max_redirects < 0:
        raise ValueError("max_redirects must be >= 0")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    validate_url(url)

    handler = _SafeRedirectHandler()
    handler.max_redirections = max_redirects
    opener = urllib.request.build_opener(handler)

    req_headers = {"User-Agent": "mcp-hub/safe-fetch"}
    if headers:
        req_headers.update(headers)

    request = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)
    with opener.open(request, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        if method.upper() == "HEAD":
            return status, b""
        body = _LimitedResponse(resp, max_bytes + 1).read()
        if len(body) > max_bytes:
            raise UnsafeURLError(f"Response exceeds max size ({max_bytes} bytes)")
        return status, body
