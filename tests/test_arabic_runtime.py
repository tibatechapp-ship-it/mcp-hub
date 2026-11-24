"""Tests for the Arabic runtime layer and upstream-only cache refresh."""

from __future__ import annotations

import asyncio
import json

import pytest

from mcp_hub import arabic_runtime, live_sync


class ListToolsRequest:
    pass


class FakeTool:
    def __init__(self, name: str, description: str, schema: dict | None = None):
        self.name = name
        self.description = description
        self.inputSchema = schema or {"type": "object"}


class FakeResult:
    def __init__(self, tools: list[FakeTool]):
        self.tools = tools


class FakeLowLevel:
    def __init__(self, handler):
        self.request_handlers = {ListToolsRequest: handler}


class FakeApp:
    def __init__(self, handler):
        self._mcp_server = FakeLowLevel(handler)


def _runtime_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MCP_HUB_AR_RUNTIME", "1")
    monkeypatch.setenv("MCP_HUB_AR_CACHE", str(tmp_path / "cache.json"))
    monkeypatch.setenv("MCP_HUB_AR_UPSTREAM_URL", "")
    monkeypatch.setenv("MCP_HUB_AR_LIVE", "0")


def test_localize_tool_preserves_protocol_fields() -> None:
    tool = {
        "name": "search_servers",
        "description": "Search the catalog by name.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    translations = {
        "search_servers": {
            "description_ar": "ابحث في الكتالوج بالاسم.",
            "params_ar": {"query": {"description_ar": "نص البحث"}},
        }
    }
    localized = arabic_runtime.localize_tool(tool, translations)
    assert localized["name"] == "search_servers"
    assert localized["inputSchema"] == tool["inputSchema"]
    assert "ابحث في الكتالوج بالاسم." in localized["description"]
    assert "المعاملات: query: نص البحث" in localized["description"]
    assert "الوصف الأصلي: Search the catalog by name." in localized["description"]
    assert tool["description"] == "Search the catalog by name."
    assert arabic_runtime.localize_tool(localized, translations) == localized


def test_glossary_translation_for_unknown_tool() -> None:
    translated = arabic_runtime.translate_description("Search the catalog by server")
    assert translated.startswith("ترجمة تلقائية (قاموس محلي):")
    assert "ابحث في الكتالوج" in translated


def test_bundled_registry_builds_translation_map() -> None:
    registry = arabic_runtime.load_registry()
    translations = arabic_runtime.build_translation_map(registry)
    assert "search_servers" in translations
    assert len(translations) >= 120


def test_install_wraps_list_tools_and_localizes(monkeypatch, tmp_path) -> None:
    _runtime_env(monkeypatch, tmp_path)

    async def original(message):
        return FakeResult(
            [FakeTool("search_servers", "Search the catalog by name, publisher, description.")]
        )

    app = FakeApp(original)
    assert arabic_runtime.install(app) is True
    wrapped = app._mcp_server.request_handlers[ListToolsRequest]
    assert getattr(wrapped, "_mcp_hub_arabic", False) is True
    result = asyncio.run(wrapped("message"))
    tool = result.tools[0]
    assert tool.name == "search_servers"
    assert tool.inputSchema == {"type": "object"}
    assert "يبحث في الفهرس" in tool.description
    assert "الوصف الأصلي:" in tool.description


def test_install_is_idempotent(monkeypatch, tmp_path) -> None:
    _runtime_env(monkeypatch, tmp_path)

    async def original(message):
        return FakeResult([])

    app = FakeApp(original)
    assert arabic_runtime.install(app) is True
    first = app._mcp_server.request_handlers[ListToolsRequest]
    assert arabic_runtime.install(app) is True
    assert app._mcp_server.request_handlers[ListToolsRequest] is first


def test_localization_failure_fails_open(monkeypatch, tmp_path) -> None:
    _runtime_env(monkeypatch, tmp_path)

    async def original(message):
        return FakeResult([FakeTool("unknown_tool", "Original English description")])

    app = FakeApp(original)
    arabic_runtime.install(app)
    wrapped = app._mcp_server.request_handlers[ListToolsRequest]

    def boom(*args, **kwargs):
        raise RuntimeError("translation down")

    monkeypatch.setattr(arabic_runtime, "localize_result", boom)
    result = asyncio.run(wrapped("message"))
    assert result.tools[0].description == "Original English description"


def test_install_without_request_handlers_returns_false(monkeypatch, tmp_path) -> None:
    _runtime_env(monkeypatch, tmp_path)

    class NoHandlers:
        pass

    assert arabic_runtime.install(NoHandlers()) is False


def test_upstream_policy_is_exact() -> None:
    assert live_sync.is_allowed_upstream(live_sync.DEFAULT_UPSTREAM_URL)
    assert not live_sync.is_allowed_upstream(
        "https://raw.githubusercontent.com/tibatechapp-ship-it/mcp-hub/dev/registry/mcp_servers.json"
    )
    assert not live_sync.is_allowed_upstream("http://raw.githubusercontent.com/x")
    assert not live_sync.is_allowed_upstream(
        "https://api.github.com/repos/tibatechapp-ship-it/mcp-hub/contents/registry"
    )
    assert not live_sync.is_allowed_upstream(live_sync.DEFAULT_UPSTREAM_URL + "?ref=main")


def test_refresh_rejects_non_upstream_without_network(monkeypatch, tmp_path) -> None:
    def no_network(*args, **kwargs):
        pytest.fail("safe_request must not be called")

    monkeypatch.setattr(live_sync, "safe_request", no_network)
    with pytest.raises(live_sync.UpstreamError):
        live_sync.refresh_from_upstream(
            tmp_path / "cache.json",
            "https://raw.githubusercontent.com/tibatechapp-ship-it/mcp-hub/dev/registry/x.json",
        )


def test_refresh_from_upstream_updates_cache(monkeypatch, tmp_path) -> None:
    registry = {
        "schema_version": 1,
        "mcpServers": {"x": {"tools": [{"name": "t", "description_ar": "وصف"}]}},
    }
    body = json.dumps(registry).encode("utf-8")

    def fake_request(url, **kwargs):
        assert url == live_sync.DEFAULT_UPSTREAM_URL
        return 200, body

    monkeypatch.setattr(live_sync, "safe_request", fake_request)
    cache = tmp_path / "cache.json"
    result = live_sync.refresh_from_upstream(cache, force=True)
    assert result.status == "updated"
    assert result.registry == registry
    assert result.digest
    assert live_sync.load_cache(cache)["registry"] == registry


def test_refresh_handles_conditional_response(monkeypatch, tmp_path) -> None:
    registry = {"schema_version": 1, "mcpServers": {"x": {"tools": []}}}
    cache = tmp_path / "cache.json"
    live_sync.save_cache(cache, registry, source="upstream", digest="abc123", etag='W/"upstream-7"')
    seen: dict[str, str] = {}

    def fake_request(url, **kwargs):
        seen.update(kwargs.get("headers", {}))
        return 304, b""

    monkeypatch.setattr(live_sync, "safe_request", fake_request)
    result = live_sync.refresh_from_upstream(cache, force=True)
    assert result.status == "not-modified"
    assert result.registry == registry
    assert seen.get("If-None-Match") == 'W/"upstream-7"'
    assert seen.get("If-None-Match") != "abc123"


def test_refresh_stores_and_reuses_upstream_etag(monkeypatch, tmp_path) -> None:
    registry = {"schema_version": 1, "mcpServers": {"x": {"tools": []}}}
    body = json.dumps(registry).encode("utf-8")
    cache = tmp_path / "cache.json"

    def fake_200(url, **kwargs):
        rh = kwargs.get("response_headers")
        if rh is not None:
            rh["ETag"] = 'W/"v2"'
        return 200, body

    monkeypatch.setattr(live_sync, "safe_request", fake_200)
    first = live_sync.refresh_from_upstream(cache, force=True)
    assert first.status == "updated"
    assert live_sync.load_cache(cache)["etag"] == 'W/"v2"'

    seen: dict[str, str] = {}

    def fake_304(url, **kwargs):
        seen.update(kwargs.get("headers", {}))
        return 304, b""

    monkeypatch.setattr(live_sync, "safe_request", fake_304)
    second = live_sync.refresh_from_upstream(cache, force=True)
    assert second.status == "not-modified"
    assert seen["If-None-Match"] == 'W/"v2"'
