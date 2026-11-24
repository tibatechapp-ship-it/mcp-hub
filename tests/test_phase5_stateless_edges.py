"""Additional fail-closed branches for the stateless MCP dispatcher."""

from __future__ import annotations

from typing import Any

from mcp_hub import mcp_stateless as ms


def _rpc(method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    request = {
        "jsonrpc": ms.JSONRPC_VERSION,
        "id": 1,
        "method": method,
        "params": {"_meta": {ms.META_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION}, **params},
    }
    headers = {ms.HEADER_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION, ms.HEADER_METHOD: method}
    source = ms.NAME_HEADERS_PARAMS.get(method)
    if source and isinstance(params.get(source), str):
        headers[ms.HEADER_NAME] = params[source]
    tool_specs = kwargs.pop("tool_specs", ())
    _, body = ms.handle(
        request,
        headers,
        tools=kwargs.pop("tools", {}),
        tool_specs=tool_specs,
        expected_manifest_hash=ms.tool_manifest_digest(tool_specs),
        **kwargs,
    )
    assert body is not None
    return body


def test_scalar_header_string_and_invalid_tool_name() -> None:
    assert ms._scalar_header("value") == "value"

    body = _rpc("tools/call", {"name": 42, "arguments": {}})
    assert body["error"]["code"] == ms.INVALID_PARAMS


def test_skill_read_policy_fail_closed() -> None:
    def _boom_policy(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("policy down")

    body = _rpc(
        "resources/read",
        {"uri": "mcp-hub://skills/demo/SKILL.md"},
        skill_policy=_boom_policy,
    )
    assert body["error"]["code"] == ms.POLICY_DENIED


def test_handler_exception_translation() -> None:
    def _mcp_boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise ms.MCPError(ms.INVALID_PARAMS, "prompt rejected")

    def _runtime_boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("prompt boom")

    body = _rpc("prompts/get", {"name": "p1"}, prompts={"p1": _mcp_boom})
    assert body["error"]["code"] == ms.INVALID_PARAMS
    body = _rpc("prompts/get", {"name": "p1"}, prompts={"p1": _runtime_boom})
    assert body["error"]["code"] == ms.INTERNAL_ERROR


def test_skill_handler_exception_translation() -> None:
    def _mcp_boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise ms.MCPError(ms.INVALID_PARAMS, "skills rejected")

    def _runtime_boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("skills boom")

    body = _rpc("skills/list", {}, skill_handlers={"skills/list": _mcp_boom})
    assert body["error"]["code"] == ms.INVALID_PARAMS
    body = _rpc("skills/list", {}, skill_handlers={"skills/list": _runtime_boom})
    assert body["error"]["code"] == ms.INTERNAL_ERROR


def test_skill_get_edges() -> None:
    body = _rpc("skills/get", {"name": ""})
    assert body["error"]["code"] == ms.INVALID_PARAMS

    def _boom_policy(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("policy down")

    body = _rpc("skills/get", {"name": "demo"}, skill_policy=_boom_policy)
    assert body["error"]["code"] == ms.POLICY_DENIED

    def _mcp_boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise ms.MCPError(ms.INVALID_PARAMS, "skill rejected")

    def _runtime_boom(_args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("skill boom")

    body = _rpc("skills/get", {"name": "demo"}, skill_handlers={"skills/get": _mcp_boom})
    assert body["error"]["code"] == ms.INVALID_PARAMS
    body = _rpc("skills/get", {"name": "demo"}, skill_handlers={"skills/get": _runtime_boom})
    assert body["error"]["code"] == ms.INTERNAL_ERROR

    body = _rpc("skills/get", {"name": "demo"}, skill_handlers={"skills/get": lambda _a: "raw"})
    assert body["result"]["skill"] == "raw"
