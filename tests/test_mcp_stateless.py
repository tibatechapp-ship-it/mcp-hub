"""Tests for the stateless MCP 2026-07-28 core."""

from __future__ import annotations

import pytest

from mcp_hub import mcp_stateless as ms


def _meta(version: str = ms.MCP_PROTOCOL_VERSION) -> dict:
    return {
        ms.META_PROTOCOL_VERSION: version,
        ms.META_CLIENT_INFO: {"name": "tests", "version": "1"},
        ms.META_CLIENT_CAPABILITIES: {},
    }


def _request(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    payload = dict(params or {})
    payload.setdefault("_meta", _meta())
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": payload}


def _headers(method: str, name: str | None = None, version: str = ms.MCP_PROTOCOL_VERSION) -> dict:
    headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _tool_specs() -> tuple[ms.ToolSpec, ...]:
    return (
        ms.ToolSpec(
            name="alpha",
            description="Return the x argument.",
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        ),
    )


def _handle(request: dict, headers: dict, **overrides):
    options = {
        "tools": {"alpha": lambda args: {"value": args.get("x")}},
        "tool_specs": _tool_specs(),
        "expected_manifest_hash": ms.tool_manifest_digest(_tool_specs()),
        "server_info": {"name": "test", "version": "9"},
    }
    options.update(overrides)
    return ms.handle(request, headers, **options)


def test_tools_list_is_stateless_and_cacheable():
    status, body = _handle(_request("tools/list"), _headers("tools/list"))
    assert status == 200
    result = body["result"]
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "public"
    assert result["tools"][0]["name"] == "alpha"
    assert body["id"] == 1


def test_missing_protocol_header_is_header_mismatch():
    status, body = _handle(_request("tools/list"), {"Mcp-Method": "tools/list"})
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_protocol_version_mismatch_is_header_mismatch():
    request = _request("tools/list")
    request["params"]["_meta"] = _meta("2025-11-25")
    status, body = _handle(request, _headers("tools/list", version=ms.MCP_PROTOCOL_VERSION))
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_unsupported_protocol_version_uses_spec_error():
    request = _request("tools/list")
    request["params"]["_meta"] = _meta("1999-01-01")
    status, body = _handle(request, _headers("tools/list", version="1999-01-01"))
    assert status == 200
    assert body["error"]["code"] == ms.UNSUPPORTED_PROTOCOL_VERSION


def test_missing_method_header_is_header_mismatch():
    status, body = _handle(
        _request("tools/list"), {"MCP-Protocol-Version": ms.MCP_PROTOCOL_VERSION}
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_method_header_mismatch_is_header_mismatch():
    status, body = _handle(_request("tools/list"), _headers("tools/call"))
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_missing_name_header_for_tools_call():
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 1}}),
        _headers("tools/call"),
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_name_header_mismatch_is_rejected():
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 1}}),
        _headers("tools/call", name="other"),
    )
    assert status == 400
    assert "Mcp-Name" in body["error"]["message"]


def test_unexpected_name_header_on_listing_is_rejected():
    status, body = _handle(_request("tools/list"), _headers("tools/list", name="alpha"))
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_tools_call_returns_text_result():
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 7}}),
        _headers("tools/call", name="alpha"),
    )
    assert status == 200
    assert body["result"]["isError"] is False
    assert '"value": 7' in body["result"]["content"][0]["text"]


def test_base64_sentinel_decodes_unicode_tool_name():
    specs = (ms.ToolSpec(name="أداة", description="Arabic tool", input_schema={}),)
    tools = {"أداة": lambda _: "ok"}
    status, body = _handle(
        _request("tools/call", {"name": "أداة", "arguments": {}}),
        _headers("tools/call", name=ms.encode_header_value("أداة")),
        tool_specs=specs,
        tools=tools,
        expected_manifest_hash=ms.tool_manifest_digest(specs),
    )
    assert status == 200
    assert body["result"]["content"][0]["text"] == "ok"


def test_sentinel_literal_must_be_encoded():
    literal = f"{ms.SENTINEL_PREFIX}literal{ms.SENTINEL_SUFFIX}"
    specs = (ms.ToolSpec(name=literal, description="sentinel", input_schema={}),)
    tools = {literal: lambda _: "ok"}
    parameters = {"name": literal, "arguments": {}}

    status, body = _handle(
        _request("tools/call", parameters),
        _headers("tools/call", name=literal),
        tool_specs=specs,
        tools=tools,
        expected_manifest_hash=ms.tool_manifest_digest(specs),
    )
    assert status == 400

    status, body = _handle(
        _request("tools/call", parameters),
        _headers("tools/call", name=ms.encode_header_value(literal)),
        tool_specs=specs,
        tools=tools,
        expected_manifest_hash=ms.tool_manifest_digest(specs),
    )
    assert status == 200


def test_malformed_base64_sentinel_is_rejected():
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {}}),
        _headers("tools/call", name=f"{ms.SENTINEL_PREFIX}!!!{ms.SENTINEL_SUFFIX}"),
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_param_header_matches_body_numerically():
    headers = _headers("tools/call", name="alpha")
    headers["Mcp-Param-x"] = "7.0"
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 7}}),
        headers,
    )
    assert status == 200
    assert '"value": 7' in body["result"]["content"][0]["text"]


def test_param_header_mismatch_is_rejected():
    headers = _headers("tools/call", name="alpha")
    headers["Mcp-Param-x"] = "8"
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 7}}),
        headers,
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_header_names_are_case_insensitive():
    headers = {
        "mcp-protocol-version": ms.MCP_PROTOCOL_VERSION,
        "mcp-method": "tools/call",
        "mcp-name": "alpha",
    }
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 1}}), headers
    )
    assert status == 200


def test_conflicting_duplicate_headers_are_rejected():
    headers = _headers("tools/list")
    headers["MCP-METHOD"] = "tools/call"
    status, body = _handle(_request("tools/list"), headers)
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_handler_exception_becomes_tool_error():
    def boom(_):
        raise RuntimeError("kaboom")

    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {}}),
        _headers("tools/call", name="alpha"),
        tools={"alpha": boom},
    )
    assert status == 200
    assert body["result"]["isError"] is True
    assert "RuntimeError" in body["result"]["content"][0]["text"]


def test_manifest_drift_blocks_dispatch():
    called = []

    def handler(args):
        called.append(args)
        return "ok"

    drifted = (ms.ToolSpec(name="alpha", description="changed", input_schema={}),)
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {}}),
        _headers("tools/call", name="alpha"),
        tools={"alpha": handler},
        tool_specs=drifted,
        expected_manifest_hash=ms.tool_manifest_digest(_tool_specs()),
    )
    assert status == 200
    assert body["error"]["code"] == ms.TOOL_MANIFEST_DRIFT
    assert called == []


def test_manifest_drift_blocks_listing_when_handler_names_change():
    status, body = _handle(
        _request("tools/list"),
        _headers("tools/list"),
        tools={"beta": lambda _: "ok"},
    )
    assert status == 200
    assert body["error"]["code"] == ms.TOOL_MANIFEST_DRIFT


def test_discover_advertises_pin_and_capabilities():
    status, body = _handle(_request("server/discover"), _headers("server/discover"))
    assert status == 200
    result = body["result"]
    assert result["protocolVersion"] == ms.MCP_PROTOCOL_VERSION
    assert result["toolManifestSha256"] == ms.tool_manifest_digest(_tool_specs())
    assert result["capabilities"]["tools"]["listChanged"] is False


def test_resource_read_uses_mcp_name_for_uri():
    specs = (ms.ResourceSpec(uri="mcp-hub://stats", mime_type="application/json"),)
    status, body = _handle(
        _request("resources/read", {"uri": "mcp-hub://stats"}),
        _headers("resources/read", name="mcp-hub://stats"),
        resource_specs=specs,
        resources={"mcp-hub://stats": lambda _: '{"records":1}'},
    )
    assert status == 200
    assert body["result"]["contents"][0]["text"] == '{"records":1}'
    assert body["result"]["cacheScope"] == "public"


def test_unknown_resource_is_invalid_params():
    status, body = _handle(
        _request("resources/read", {"uri": "missing://x"}),
        _headers("resources/read", name="missing://x"),
    )
    assert status == 200
    assert body["error"]["code"] == ms.INVALID_PARAMS


def test_notification_has_no_response_body():
    status, body = _handle(
        _request("tools/list", req_id=None),
        _headers("tools/list"),
    )
    assert status == 202
    assert body is None


def test_invalid_envelope_is_rejected():
    status, body = _handle(
        {"jsonrpc": "1.0", "id": 1, "method": "tools/list"}, _headers("tools/list")
    )
    assert status == 200
    assert body["error"]["code"] == ms.INVALID_REQUEST


def test_unknown_method_is_method_not_found():
    status, body = _handle(_request("does/not/exist"), _headers("does/not/exist"))
    assert status == 200
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND


def test_header_safe_and_encoded_round_trip():
    assert ms.encode_header_value("us-west1") == "us-west1"
    assert ms.decode_header_value("us-west1") == "us-west1"
    with pytest.raises(ValueError):
        ms.decode_header_value(" padded ")
