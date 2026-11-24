"""Edge coverage for Phase 4 error paths and compatibility branches."""

from __future__ import annotations

import base64
import json

import pytest

from mcp_hub import apps as mcp_apps
from mcp_hub import catalog_tools, telemetry
from mcp_hub import mcp_stateless as ms
from mcp_hub.database import Catalog


def _meta() -> dict:
    return {
        ms.META_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION,
        ms.META_CLIENT_INFO: {"name": "edge-tests", "version": "1"},
        ms.META_CLIENT_CAPABILITIES: {},
    }


def _request(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    payload = dict(params or {})
    payload.setdefault("_meta", _meta())
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": payload}


def _headers(method: str, name: str | None = None) -> dict:
    headers = {"MCP-Protocol-Version": ms.MCP_PROTOCOL_VERSION, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _spec_options(**overrides):
    spec = ms.ToolSpec(name="echo", description="Test tool", input_schema={})
    options = {
        "tools": {"echo": lambda args: {"value": args.get("value", "")}},
        "tool_specs": (spec,),
        "expected_manifest_hash": ms.tool_manifest_digest((spec,)),
    }
    options.update(overrides)
    return options


@pytest.mark.parametrize(
    ("payload", "headers", "code"),
    [
        ({"method": "ping"}, _headers("ping"), ms.INVALID_REQUEST),
        ({"jsonrpc": "1.0", "method": "ping"}, _headers("ping"), ms.INVALID_REQUEST),
        ({"jsonrpc": "2.0"}, _headers("ping"), ms.INVALID_REQUEST),
        (
            {"jsonrpc": "2.0", "method": "ping", "params": []},
            _headers("ping"),
            ms.INVALID_REQUEST,
        ),
    ],
)
def test_validate_request_rejects_malformed_envelopes(payload, headers, code):
    with pytest.raises(ms.MCPError) as exc:
        ms.validate_request(payload, headers)
    assert exc.value.code == code


def test_validate_request_rejects_header_mismatches_and_sensitive_mirrors():
    bad_version = _headers("ping")
    bad_version["MCP-Protocol-Version"] = "1999-01-01"
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(_request("ping"), bad_version)

    unsupported = _headers("ping")
    unsupported["MCP-Protocol-Version"] = "1999-01-01"
    body = _request("ping")
    body["params"]["_meta"][ms.META_PROTOCOL_VERSION] = "1999-01-01"
    with pytest.raises(ms.MCPError) as exc:
        ms.validate_request(body, unsupported)
    assert exc.value.code == ms.UNSUPPORTED_PROTOCOL_VERSION

    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(_request("ping"), {**_headers("ping"), "Mcp-Name": "unexpected"})

    args = {"name": "echo", "arguments": {"api_key": "unused"}}
    headers = {**_headers("tools/call", "echo"), "Mcp-Param-api_key": "unused"}
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(_request("tools/call", args), headers)

    headers = {**_headers("tools/call", "echo"), "Mcp-Param-missing": "x"}
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(_request("tools/call", {"name": "echo", "arguments": {}}), headers)


def test_header_value_encoding_round_trip_and_rejects_bad_values():
    value = "مرحبا باي"
    assert ms.decode_header_value(ms.encode_header_value(value)) == value
    with pytest.raises(ValueError):
        ms.decode_header_value("bad\nvalue")
    malformed = ms.SENTINEL_PREFIX + "!!!?" + ms.SENTINEL_SUFFIX
    with pytest.raises(ValueError):
        ms.decode_header_value(malformed)
    invalid_utf8 = ms.SENTINEL_PREFIX + base64.b64encode(b"\xff").decode() + ms.SENTINEL_SUFFIX
    with pytest.raises(ValueError):
        ms.decode_header_value(invalid_utf8)


def test_scalar_header_mirroring_accepts_booleans_and_numbers():
    args = {"name": "echo", "arguments": {"flag": True, "count": 2}}
    headers = {
        **_headers("tools/call", "echo"),
        "Mcp-Param-flag": "true",
        "Mcp-Param-count": "2",
    }
    method, params, _ = ms.validate_request(_request("tools/call", args), headers)
    assert method == "tools/call"
    assert params["arguments"]["flag"] is True

    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(
            _request("tools/call", args),
            {**headers, "Mcp-Param-count": "3"},
        )


def test_manifest_drift_fails_closed():
    spec = ms.ToolSpec(name="echo")
    different = ms.ToolSpec(name="other")
    with pytest.raises(ms.MCPError) as exc:
        ms.verify_tool_manifest(
            (spec,), {"echo": lambda _: {}}, ms.tool_manifest_digest((different,))
        )
    assert exc.value.code == ms.TOOL_MANIFEST_DRIFT


def test_handle_reports_method_not_found_and_supports_notifications():
    status, body = ms.handle(
        _request("does/not/exist"), _headers("does/not/exist"), **_spec_options()
    )
    assert status == 200
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND

    status, body = ms.handle(_request("ping", req_id=None), _headers("ping"), **_spec_options())
    assert status == 202
    assert body is None


def test_handle_wires_prompts_and_plain_resource_fallback():
    prompt_spec = ms.PromptSpec(name="greet", description="Say hello")
    spec = ms.ResourceSpec(uri="mcp-hub://edge", mime_type="text/plain")
    options = _spec_options(
        prompt_specs=(prompt_spec,),
        prompts={"greet": lambda args: [{"role": "user", "content": str(args)}]},
        resource_specs=(spec,),
        resources={"mcp-hub://edge": lambda _: {"ok": True}},
    )
    status, body = ms.handle(_request("prompts/list"), _headers("prompts/list"), **options)
    assert body["result"]["prompts"][0]["name"] == "greet"

    status, body = ms.handle(
        _request("prompts/get", {"name": "greet"}),
        _headers("prompts/get", "greet"),
        **options,
    )
    assert body["result"]["messages"][0]["role"] == "user"

    status, body = ms.handle(
        _request("resources/read", {"uri": "mcp-hub://edge"}),
        _headers("resources/read", "mcp-hub://edge"),
        **options,
    )
    assert status == 200
    assert json.loads(body["result"]["contents"][0]["text"])["ok"] is True


def test_handle_contains_tool_exceptions_and_missing_phase3_handlers():
    def boom(args):
        _ = args
        raise RuntimeError("tool exploded")

    status, body = ms.handle(
        _request("tools/call", {"name": "echo", "arguments": {}}),
        _headers("tools/call", "echo"),
        **_spec_options(tools={"echo": boom}),
    )
    assert status == 200
    assert body["result"]["isError"] is True

    options = _spec_options(
        tools={},
        tool_specs=(),
        expected_manifest_hash=ms.tool_manifest_digest(()),
    )
    status, body = ms.handle(_request("skills/list"), _headers("skills/list"), **options)
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND
    status, body = ms.handle(
        _request("tasks/get", {"task_id": "x", "tenant_id": "t", "session_id": "s"}),
        _headers("tasks/get", "x"),
        **options,
    )
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND
    status, body = ms.handle(
        _request("persona/simulate", {"scenario": "safe_chain"}),
        _headers("persona/simulate", "safe_chain"),
        **options,
    )
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND


def test_broken_telemetry_adapter_never_breaks_a_request():
    class Broken:
        name = "broken"
        log_tool_io = False

        def start(self, method, context):
            _ = method, context
            raise RuntimeError("start failed")

        def emit(self, event, attributes):
            _ = event, attributes
            raise RuntimeError("emit failed")

        def finish(self, span_id, status, error_code=None):
            _ = span_id, status, error_code
            raise RuntimeError("finish failed")

    status, body = ms.handle(
        _request("ping"),
        _headers("ping"),
        **_spec_options(telemetry=Broken()),
    )
    assert status == 200
    assert "result" in body


def test_telemetry_mode_off_and_broken_db_fall_back_to_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HUB_TELEMETRY", "off")
    assert telemetry.build_telemetry(str(tmp_path / "off.sqlite3")).name == "off"

    monkeypatch.setenv("MCP_HUB_TELEMETRY", "sqlite")
    adapter = telemetry.build_telemetry(str(tmp_path / "not-a-dir" / "no.sqlite3"))
    assert adapter.name == "sqlite-unavailable"


def test_otel_failures_are_swallowed():
    class BrokenSpan:
        def set_attribute(self, key, value):
            _ = key, value
            raise RuntimeError("attribute failed")

        def add_event(self, event, attributes):
            _ = event, attributes
            raise RuntimeError("event failed")

        def end(self):
            raise RuntimeError("end failed")

    class BrokenTracer:
        def start_span(self, name):
            _ = name
            return BrokenSpan()

    class DeadTracer:
        def start_span(self, name):
            _ = name
            raise RuntimeError("span failed")

    adapter = telemetry.OTelTelemetry(BrokenTracer())
    span_id = adapter.start("ping", None)
    adapter.emit("mcp.response", {"span.id": span_id})
    adapter.finish(span_id, "ok")

    dead = telemetry.OTelTelemetry(DeadTracer())
    assert dead.start("ping", None) == ""
    dead.finish("missing", "ok")


def test_apps_empty_runtime_and_broken_snapshot_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HUB_APPS_ENABLED", "off")
    assert mcp_apps.enabled() is False
    runtime = mcp_apps.empty_runtime()
    assert runtime.resource_specs == ()
    with pytest.raises(ms.MCPError):
        runtime.policy(mcp_apps.APPROVAL_URI, {})

    class BrokenRegistry:
        def list(self):
            raise RuntimeError("broken registry")

    class BrokenStore:
        def list(self, tenant_id, session_id=None, limit=50):
            _ = tenant_id, session_id, limit
            raise RuntimeError("broken store")

    catalog = Catalog(tmp_path / "catalog.sqlite3")
    built = mcp_apps.build_runtime(catalog, BrokenRegistry(), BrokenStore())  # type: ignore[arg-type]
    payload = built.resources[mcp_apps.APPROVAL_URI]({"tenant_id": "acme", "session_id": "s1"})
    snapshot = json.loads(payload["contents"][1]["text"])["snapshot"]
    assert snapshot["skills"] == []
    assert snapshot["pending_approvals"] == []
    assert snapshot["read_only_ui"] is True
    assert {item["method"] for item in snapshot["available_actions"]} == {
        "skills/approve",
        "skills/revoke",
        "tasks/approve",
        "tasks/list",
    }
    assert catalog_tools.PINNED_TOOL_MANIFEST_SHA256


def test_dataclass_helpers_and_error_payloads():
    error = ms.MCPError(ms.INVALID_PARAMS, "bad", {"field": "name"})
    assert error.to_error() == {
        "code": ms.INVALID_PARAMS,
        "message": "bad",
        "data": {"field": "name"},
    }
    assert ms.ToolSpec(name="x", title="Title").to_dict()["title"] == "Title"
    assert ms.PromptSpec(name="p", arguments=({"name": "who"},)).to_dict()["arguments"]
    raw = {"content": [{"type": "text", "text": "ok"}], "isError": False}
    assert ms._normalize_tool_result(raw) == raw
    assert ms._normalize_tool_result("ok")["content"][0]["text"] == "ok"
    assert ms._header_safe("safe value") is True
    assert ms._header_safe(" spaced") is False
    assert ms._header_values_match("abc", "abc") is True
    with pytest.raises(ValueError):
        ms.decode_header_value(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ms.encode_header_value(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ms._scalar_header({})


def test_validate_request_remaining_guard_branches():
    with pytest.raises(ms.MCPError):
        ms.validate_request([], _headers("ping"))  # type: ignore[arg-type]
    no_version = _request("ping")
    no_version["params"]["_meta"].pop(ms.META_PROTOCOL_VERSION)
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(no_version, {"Mcp-Method": "ping"})
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(_request("ping"), {"MCP-Protocol-Version": 1, "Mcp-Method": "ping"})  # type: ignore[dict-item]
    with pytest.raises(ms.MCPError):
        ms.validate_request(
            _request("tools/call", {"arguments": {}}), _headers("tools/call", "echo")
        )
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(_request("ping"), {**_headers("ping"), "Mcp-Param-": "x"})
    with pytest.raises(ms.HeaderMismatchError):
        ms.validate_request(
            _request("tools/call", {"name": "echo", "arguments": {"flag": "x"}}),
            {**_headers("tools/call", "echo"), "Mcp-Param-flag": "bad\nvalue"},
        )


def test_handle_policy_and_handler_error_branches():
    options = _spec_options()

    trace = {"traceparent": "00-" + "c" * 32 + "-" + "d" * 16 + "-01"}
    status, body = ms.handle(
        _request("missing/method", {"_meta": {**_meta(), **trace}}),
        _headers("missing/method"),
        **options,
    )
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND
    assert body["_meta"]["traceparent"] == trace["traceparent"]

    status, body = ms.handle(
        _request("tools/call", {"name": "other", "arguments": {}}),
        _headers("tools/call", "other"),
        **options,
    )
    assert body["error"]["code"] == ms.INVALID_PARAMS

    def bad_policy(name, args):
        _ = name, args
        raise RuntimeError("policy down")

    uri = mcp_apps.APPROVAL_URI
    status, body = ms.handle(
        _request("resources/read", {"uri": uri}),
        _headers("resources/read", uri),
        **_spec_options(
            app_policy=bad_policy,
            resources={uri: lambda _: {"ok": True}},
            resource_specs=(ms.ResourceSpec(uri=uri),),
        ),
    )
    assert body["error"]["code"] == ms.POLICY_DENIED

    def broken_resource(args):
        _ = args
        raise RuntimeError("resource down")

    status, body = ms.handle(
        _request("resources/read", {"uri": uri}),
        _headers("resources/read", uri),
        **_spec_options(
            resources={uri: broken_resource},
            resource_specs=(ms.ResourceSpec(uri=uri),),
        ),
    )
    assert body["error"]["code"] == ms.INTERNAL_ERROR

    status, body = ms.handle(
        _request("prompts/get", {"name": "missing"}),
        _headers("prompts/get", "missing"),
        **options,
    )
    assert body["error"]["code"] == ms.INVALID_PARAMS

    def broken_prompt(args):
        _ = args
        raise RuntimeError("prompt down")

    status, body = ms.handle(
        _request("prompts/get", {"name": "p"}),
        _headers("prompts/get", "p"),
        **_spec_options(
            prompt_specs=(ms.PromptSpec(name="p"),),
            prompts={"p": broken_prompt},
        ),
    )
    assert body["error"]["code"] == ms.INTERNAL_ERROR

    status, body = ms.handle(
        _request("skills/get", {"name": "demo"}),
        _headers("skills/get", "demo"),
        **_spec_options(
            skill_handlers={
                "skills/get": lambda args: (lambda _: (_ for _ in ()).throw(RuntimeError()))(args)
            },
            skill_policy=lambda name, args: (lambda: (_ for _ in ()).throw(RuntimeError()))(),
        ),
    )
    assert body["error"]["code"] == ms.POLICY_DENIED

    def broken_task(args):
        _ = args
        raise RuntimeError("task down")

    status, body = ms.handle(
        _request("tasks/get", {"task_id": "t", "tenant_id": "a", "session_id": "s"}),
        _headers("tasks/get", "t"),
        **_spec_options(task_handlers={"tasks/get": broken_task}),
    )
    assert body["error"]["code"] == ms.TASK_DENIED

    def broken_persona(args):
        _ = args
        raise RuntimeError("persona down")

    status, body = ms.handle(
        _request("persona/simulate", {"scenario": "safe_chain"}),
        _headers("persona/simulate", "safe_chain"),
        **_spec_options(persona_handler=broken_persona),
    )
    assert body["error"]["code"] == ms.INVALID_PARAMS


def test_telemetry_finish_failure_and_empty_context_paths(tmp_path, monkeypatch):
    class FinishFails:
        name = "finish-fails"
        log_tool_io = False

        def start(self, method, context):
            _ = method, context
            return "span"

        def emit(self, event, attributes):
            _ = event, attributes

        def finish(self, span_id, status, error_code=None):
            _ = span_id, status, error_code
            raise RuntimeError("finish down")

    status, _ = ms.handle(
        _request("ping"), _headers("ping"), **_spec_options(telemetry=FinishFails())
    )
    assert status == 200

    adapter = telemetry.SQLiteTelemetry(str(tmp_path / "no-context.sqlite3"))
    assert adapter.start("ping", None)
    with telemetry.sqlite3.connect(str(tmp_path / "no-context.sqlite3")) as con:
        events = [row[0] for row in con.execute("SELECT event FROM telemetry_events")]
    assert "mcp.span.start" in events
    assert telemetry.NullTelemetry().name == "off"
    assert telemetry.TraceContext().to_dict() == {}

    monkeypatch.setenv("MCP_HUB_TELEMETRY", "otel")

    def broken_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr(telemetry.importlib, "import_module", broken_import)
    assert telemetry.build_telemetry(str(tmp_path / "missing" / "db.sqlite3")).name == (
        "otel-unavailable"
    )


def test_apps_catalog_snapshot_survives_a_broken_catalog():
    class BrokenCatalog:
        def stats(self):
            raise RuntimeError("stats down")

    runtime = mcp_apps.build_runtime(BrokenCatalog())  # type: ignore[arg-type]
    payload = runtime.resources[mcp_apps.CATALOG_URI]({})
    snapshot = json.loads(payload["contents"][1]["text"])["snapshot"]
    assert snapshot["catalogStats"] == {}
