"""The transport half of safe_fetch and the webhook success path.

The policy half lives in ``test_ssrf.py``; here the socket is faked so the
request/response plumbing (HEAD short-circuit, size accounting, redirect
handler) is exercised without leaving the process.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from mcp_hub import alerts as alerts_mod
from mcp_hub import safe_fetch
from mcp_hub.safe_fetch import UnsafeURLError, safe_request


class FakeResponse:
    def __init__(self, body: bytes = b"ok", status: int = 200) -> None:
        self._body = body
        self.status = status
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._body = self._body, b""
            return chunk
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False


def _install(monkeypatch, response: FakeResponse):
    seen: dict[str, object] = {}

    class FakeOpener:
        def open(self, request, timeout=None):
            seen["request"] = request
            seen["timeout"] = timeout
            return response

    monkeypatch.setattr(safe_fetch, "validate_url", lambda url: None)
    monkeypatch.setattr(safe_fetch.urllib.request, "build_opener", lambda *a, **kw: FakeOpener())
    return seen


def test_safe_request_returns_status_and_body(monkeypatch):
    seen = _install(monkeypatch, FakeResponse(b"hello"))

    status, body = safe_request("https://1.1.1.1/x", headers={"X-Test": "1"}, timeout=3)

    assert (status, body) == (200, b"hello")
    assert seen["timeout"] == 3
    request = seen["request"]
    assert request.get_header("X-test") == "1"
    assert request.get_header("User-agent") == "mcp-hub/safe-fetch"


def test_head_returns_no_body(monkeypatch):
    response = FakeResponse(b"should not be read")
    _install(monkeypatch, response)

    status, body = safe_request("https://1.1.1.1/x", method="head")

    assert (status, body) == (200, b"")
    assert response.closed


def test_safe_request_validates_its_own_limits(monkeypatch):
    _install(monkeypatch, FakeResponse())

    with pytest.raises(ValueError, match="max_redirects"):
        safe_request("https://1.1.1.1/", max_redirects=-1)
    with pytest.raises(ValueError, match="max_bytes"):
        safe_request("https://1.1.1.1/", max_bytes=0)


def test_limited_response_reports_exhaustion():
    limited = safe_fetch._LimitedResponse(FakeResponse(b"abcdef"), max_bytes=4)

    assert limited.read(2) == b"ab"
    assert limited.read(2) == b"cd"
    with pytest.raises(UnsafeURLError, match="max size"):
        limited.read(2)


def test_limited_response_proxies_other_attributes():
    limited = safe_fetch._LimitedResponse(FakeResponse(), max_bytes=4)
    assert limited.status == 200


def test_redirect_handler_follows_a_public_target():
    from mcp_hub.safe_fetch import _SafeRedirectHandler

    original = urllib.request.Request("https://1.1.1.1/")
    request = _SafeRedirectHandler().redirect_request(
        original, None, 302, "Found", {}, "https://8.8.8.8/"
    )

    assert request is not None
    assert request.full_url == "https://8.8.8.8/"


# ── webhook delivery ───────────────────────────────────────────────────────
def test_post_webhook_accepts_2xx(monkeypatch):
    monkeypatch.setattr(alerts_mod, "safe_request", lambda *a, **kw: (204, b""))
    assert alerts_mod._post_webhook("https://example.com/hook", {"search": "pg"}) is True


def test_post_webhook_rejects_non_2xx(monkeypatch):
    monkeypatch.setattr(alerts_mod, "safe_request", lambda *a, **kw: (500, b"nope"))
    assert alerts_mod._post_webhook("https://example.com/hook", {"search": "pg"}) is False


def test_post_webhook_sends_json(monkeypatch):
    captured: dict[str, object] = {}

    def fake(url, **kwargs):
        captured.update(url=url, **kwargs)
        return 200, b""

    monkeypatch.setattr(alerts_mod, "safe_request", fake)
    alerts_mod._post_webhook("https://example.com/hook", {"search": "pg", "new_count": 3})

    assert captured["method"] == "POST"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert b'"new_count": 3' in captured["data"]


class _StubDB:
    def __init__(self, rows):
        self._rows = rows

    def list_alerts(self):
        return self._rows


def test_dispatch_counts_a_delivered_webhook(monkeypatch):
    monkeypatch.setattr(alerts_mod, "safe_request", lambda *a, **kw: (200, b""))
    db = _StubDB([{"id": 1, "channel": "webhook", "target": "https://example.com/hook"}])

    assert alerts_mod.dispatch(db, "pg", 4) == {"sent": 1, "failed": 0, "total": 1}


def test_dispatch_counts_a_failed_delivery(monkeypatch):
    monkeypatch.setattr(alerts_mod, "safe_request", lambda *a, **kw: (500, b""))

    db = _StubDB([{"id": 1, "channel": "webhook", "target": "https://example.com/hook"}])

    assert alerts_mod.dispatch(db, "pg", 0)["failed"] == 1


def test_dispatch_survives_a_network_error(monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(alerts_mod, "safe_request", boom)
    db = _StubDB([{"id": 1, "channel": "webhook", "target": "https://example.com/hook"}])

    result = alerts_mod.dispatch(db, "pg", 1)

    assert result["failed"] == 1
    assert "connection refused" in result["errors"][0]["error"]


def test_dispatch_keeps_going_after_one_bad_channel(monkeypatch):
    def flaky(url, **kw):
        if url.endswith("bad"):
            raise RuntimeError("boom")
        return 200, b""

    monkeypatch.setattr(alerts_mod, "safe_request", flaky)
    db = _StubDB(
        [
            {"id": 1, "channel": "webhook", "target": "https://example.com/bad"},
            {"id": 2, "channel": "webhook", "target": "https://example.com/good"},
        ]
    )

    result = alerts_mod.dispatch(db, "pg", 1)

    assert (result["sent"], result["failed"]) == (1, 1)
