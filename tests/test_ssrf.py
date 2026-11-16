"""SSRF protection tests (v1.3.0)."""

from __future__ import annotations

import socket
import urllib.error

import pytest

from mcp_hub import alerts as alerts_mod
from mcp_hub import safe_fetch
from mcp_hub.safe_fetch import (
    DEFAULT_MAX_BYTES,
    UnsafeURLError,
    safe_request,
    validate_url,
)


def fake_dns(ip: str):
    def _fake(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    return _fake


# ── blocked targets ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://127.0.0.1:8000/",
        "https://127.1.2.3/",
        "https://localhost/",
        "https://169.254.169.254/latest/meta-data/",
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[fe80::1]/",
        "https://[fc00::1]/",
        "https://0.0.0.0/",
        "https://10.0.0.1/",
        "https://10.255.255.255/",
        "https://192.168.1.1/",
        "https://192.168.0.0/",
        "https://172.16.0.1/",
        "https://172.31.255.255/",
        "https://100.64.0.1/",
        "https://224.0.0.1/",
        "https://255.255.255.255/",
    ],
)
def test_validate_url_blocks_non_public(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url)


# ── malformed / disallowed input ───────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://example.com/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https://user:@example.com/",
        "https://",
        "not a url",
    ],
)
def test_validate_url_rejects_invalid(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url)


# ── accepted targets ───────────────────────────────────────────────────────
def test_validate_url_accepts_public_literal():
    validate_url("https://1.1.1.1/")  # literal IP: no DNS needed


def test_validate_url_accepts_public_domain(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("93.184.216.34"))
    validate_url("https://example.com/")


def test_validate_url_blocks_domain_that_resolves_private(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns("10.0.0.5"))
    with pytest.raises(UnsafeURLError, match="non-public"):
        validate_url("https://evil.example.com/")


def test_validate_url_fails_closed_on_dns_error(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeURLError, match="DNS"):
        validate_url("https://no-such-host.example/")


# ── safe_request policy ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/",
        "http://example.com/",
    ],
)
def test_safe_request_refuses_before_connecting(url):
    with pytest.raises(UnsafeURLError):
        safe_request(url)


def test_safe_request_rejects_oversized_body(monkeypatch):
    class FakeResponse:
        status = 200

        def read(self, n=-1):
            return b"x" * 2000

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(safe_fetch, "validate_url", lambda url: None)
    monkeypatch.setattr(safe_fetch.urllib.request, "build_opener", lambda *a, **kw: FakeOpener())

    with pytest.raises(UnsafeURLError, match="max size"):
        safe_request("https://1.1.1.1/", max_bytes=1000)


def test_redirect_to_private_address_is_blocked():
    from mcp_hub.safe_fetch import _SafeRedirectHandler

    class FakeRequest:
        full_url = "https://1.1.1.1/"
        headers: dict[str, str] = {}
        unverifiable = False
        origin_req_host = "1.1.1.1"

    with pytest.raises(UnsafeURLError):
        _SafeRedirectHandler().redirect_request(
            FakeRequest(), None, 302, "Found", {}, "https://127.0.0.1/"
        )


def test_default_max_bytes_is_1_mib():
    assert DEFAULT_MAX_BYTES == 1024 * 1024


# ── alerts channel is protected ────────────────────────────────────────────
class _StubDB:
    def __init__(self, rows):
        self._rows = rows

    def list_alerts(self):
        return self._rows


def test_webhook_post_refuses_metadata_endpoint():
    with pytest.raises(UnsafeURLError):
        alerts_mod._post_webhook("https://169.254.169.254/latest/meta-data/", {"search": "x"})


def test_dispatch_reports_blocked_webhook_without_leaking():
    db = _StubDB([{"id": 1, "channel": "webhook", "target": "https://127.0.0.1/hook"}])
    result = alerts_mod.dispatch(db, "pg", 2)

    assert result["sent"] == 0
    assert result["failed"] == 1
    assert "blocked" in result["errors"][0]["error"]


def test_dispatch_ignores_unrelated_channel_kinds():
    db = _StubDB([{"id": 2, "channel": "email", "target": "https://example.com/"}])
    result = alerts_mod.dispatch(db, "pg", 0)

    assert result["sent"] == 0
    assert result["failed"] == 0
    assert result["total"] == 1


def test_dispatch_skips_alerts_bound_to_other_queries():
    db = _StubDB(
        [{"id": 3, "channel": "webhook", "query": "redis", "target": "https://127.0.0.1/"}]
    )
    result = alerts_mod.dispatch(db, "pg", 1)

    assert result["sent"] == 0
    assert result["failed"] == 0


# ── registry + enrich never leak or retry a policy refusal ─────────────────
def test_registry_refusal_is_not_retried(monkeypatch):
    from mcp_hub import registry_api

    calls = {"n": 0}

    def fail(url, **kwargs):
        calls["n"] += 1
        raise UnsafeURLError("nope")

    monkeypatch.setattr(registry_api, "safe_request", fail)
    with pytest.raises(RuntimeError, match="refused"):
        registry_api._http_get_json("https://registry.example.invalid/x")
    assert calls["n"] == 1


def test_registry_http_error_is_redacted(monkeypatch):
    from mcp_hub import registry_api

    def fail(url, **kwargs):
        raise urllib.error.HTTPError(url, 403, "token=ghp_abcdefghijklmnopqrstuvwxyz0123", {}, None)

    monkeypatch.setattr(registry_api, "safe_request", fail)
    monkeypatch.setattr(registry_api.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as excinfo:
        registry_api._http_get_json("https://registry.example.invalid/x", retries=2)

    assert "ghp_" not in str(excinfo.value)


# ── health probes never reach a private address ────────────────────────────
def test_health_check_blocks_metadata_endpoint():
    from mcp_hub.health import check_endpoint

    result = check_endpoint("https://169.254.169.254/latest/meta-data/")
    assert result["status"] == "error"
    assert "SSRF" in result["detail"]


def test_health_check_blocks_loopback():
    from mcp_hub.health import check_endpoint

    assert check_endpoint("https://127.0.0.1/")["status"] == "error"


def test_health_check_skips_non_https():
    from mcp_hub.health import check_endpoint

    assert check_endpoint("http://example.com")["status"] == "skipped"
    assert check_endpoint("")["status"] == "skipped"


def test_health_check_reports_up_for_2xx(monkeypatch):
    from mcp_hub import health

    monkeypatch.setattr(health, "safe_request", lambda *a, **kw: (200, b""))
    result = health.check_endpoint("https://1.1.1.1/")
    assert result["status"] == "up"
    assert result["detail"] == "HTTP 200"


def test_health_check_treats_head_refusal_as_up(monkeypatch):
    from mcp_hub import health

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError("https://1.1.1.1/", 405, "Method Not Allowed", {}, None)

    monkeypatch.setattr(health, "safe_request", refuse)
    result = health.check_endpoint("https://1.1.1.1/")
    assert result["status"] == "up"
    assert "405" in result["detail"]
