"""SSRF-safe HTTP client (v1.3.0, hardened in 1.4.1).

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

DNS rebinding is closed by pinning: after validation, the TCP connection is made
to the already-validated IP while the original hostname is kept for ``Host``,
SNI and certificate verification. The lookup is never repeated by ``http.client``.

Pure standard library: no new runtime dependency for the core package.

Errors are typed so callers can distinguish policy refusal from a network
failure — see :class:`UnsafeURLError`.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import typing
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
# Typeshed does not expose this private stdlib sentinel.
_DEFAULT_SOCKET_TIMEOUT: typing.Any = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT", None)

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


def _validated_target(url: str) -> tuple[str, int, str]:
    """Return ``(host, port, pinned_ip)`` after the full SSRF policy check.

    Keeping the resolved IP alongside the hostname is the anti-rebinding step:
    callers must connect to ``pinned_ip`` instead of letting the HTTP stack
    resolve ``host`` a second time.
    """
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
        return host, port, host

    ips = _resolve_ips(host, port)
    for ip in ips:
        if _is_blocked(ip):
            raise UnsafeURLError(
                f"Host {host!r} resolves to non-public address {ip} (SSRF blocked)"
            )
    return host, port, ips[0]


def validate_url(url: str) -> None:
    """Raise :class:`UnsafeURLError` if ``url`` is not safe to fetch."""
    _validated_target(url)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect target before following it."""

    max_redirections = DEFAULT_MAX_REDIRECTS

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: typing.IO[bytes],
        code: int,
        msg: str,
        headers: typing.Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _LimitedResponse:
    """File-like wrapper that aborts once the byte budget is exhausted."""

    def __init__(self, fp: typing.IO[bytes], max_bytes: int) -> None:
        self._fp = fp
        self._max = max_bytes
        self._read = 0

    def read(self, n: int = -1) -> bytes:
        remaining = self._max - self._read
        if remaining <= 0:
            raise UnsafeURLError(f"Response exceeds max size ({self._max} bytes)")
        chunk: bytes = self._fp.read(remaining if n < 0 or n > remaining else n)
        self._read += len(chunk)
        return chunk

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self._fp, name)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection whose TCP socket goes to a pre-validated IP.

    ``host`` stays the original hostname, so urllib still sends the original
    ``Host`` header and TLS still verifies/SNI-matches that hostname.
    """

    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        pinned_ip: str,
        timeout: typing.Any = _DEFAULT_SOCKET_TIMEOUT,
        context: typing.Any = None,
        source_address: typing.Any = None,
        **kwargs: typing.Any,
    ) -> None:
        check_hostname = kwargs.pop("check_hostname", None)
        try:
            super().__init__(
                host,
                port,
                timeout=timeout,
                context=context,
                check_hostname=check_hostname,
                source_address=source_address,
                **kwargs,
            )
        except TypeError:
            # Python 3.14 removed the check_hostname argument; the SSLContext
            # already carries that policy (and is used for SNI/verification).
            super().__init__(
                host,
                port,
                timeout=timeout,
                context=context,
                source_address=source_address,
                **kwargs,
            )
        self._pinned_ip = pinned_ip
        # HTTPConnection.connect() calls this instance attribute. Replacing it
        # keeps the stdlib TLS flow intact (SNI still uses self.host) while
        # removing the second DNS lookup that enabled rebinding.
        self._create_connection = self._connect_pinned

    def _connect_pinned(
        self,
        address: tuple[str, int],
        timeout: typing.Any = None,
        source_address: typing.Any = None,
    ) -> socket.socket:
        _, port = address
        return socket.create_connection((self._pinned_ip, port), timeout, source_address)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Validate each HTTPS hop and pin its connection to the verified IP."""

    def https_open(self, req: urllib.request.Request) -> typing.Any:
        _, _, pinned_ip = _validated_target(req.full_url)
        conn_args: dict[str, typing.Any] = {"context": getattr(self, "_context", None)}
        check_hostname = getattr(self, "_check_hostname", None)
        if check_hostname is not None:
            conn_args["check_hostname"] = check_hostname
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(host, pinned_ip=pinned_ip, **kwargs),
            req,
            **conn_args,
        )


def safe_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    response_headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Perform one policy-checked HTTP request.

    Returns ``(status_code, body)``. Raises :class:`UnsafeURLError` on a policy
    violation (including an oversized body) and ``urllib.error.URLError`` on a
    network failure. ``response_headers`` is filled with the final response's
    headers when provided (used for conditional upstream refreshes).
    """
    if max_redirects < 0:
        raise ValueError("max_redirects must be >= 0")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    _validated_target(url)

    redirect_handler = _SafeRedirectHandler()
    # typeshed marks HTTPRedirectHandler.max_redirections as a ClassVar, so a
    # per-request value has to be set through the instance anyway.
    redirect_handler.max_redirections = max_redirects  # type: ignore[misc]
    # Supplying our HTTPSHandler prevents build_opener from adding the default
    # one; every hop (including redirects) is validated and pinned separately.
    opener = urllib.request.build_opener(redirect_handler, _PinnedHTTPSHandler())

    req_headers = {"User-Agent": "mcp-hub/safe-fetch"}
    if headers:
        req_headers.update(headers)

    request = urllib.request.Request(url, data=data, method=method.upper(), headers=req_headers)
    with opener.open(request, timeout=timeout) as resp:
        status = int(getattr(resp, "status", 200))
        if response_headers is not None:
            response_headers.update({str(key): str(value) for key, value in resp.headers.items()})
        if method.upper() == "HEAD":
            return status, b""
        body: bytes = _LimitedResponse(resp, max_bytes + 1).read()
        if len(body) > max_bytes:
            raise UnsafeURLError(f"Response exceeds max size ({max_bytes} bytes)")
        return status, body
