"""Tests for Phase 4: telemetry adapters and selective MCP Apps resources."""

from __future__ import annotations

import html
import json
import sqlite3

from fastapi.testclient import TestClient

from mcp_hub import apps as mcp_apps
from mcp_hub import catalog_tools, telemetry
from mcp_hub import mcp_stateless as ms
from mcp_hub.api import create_app
from mcp_hub.cli import main
from mcp_hub.database import Catalog
from mcp_hub.redact import REDACTED


def _meta() -> dict:
    return {
        ms.META_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION,
        ms.META_CLIENT_INFO: {"name": "tests", "version": "1"},
        ms.META_CLIENT_CAPABILITIES: {},
    }


def _trace() -> dict[str, str]:
    return {
        "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        "tracestate": "vendor=phase4",
        "baggage": "tenant=acme",
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


def _events(db_path) -> list[tuple[str, dict]]:
    with sqlite3.connect(str(db_path)) as con:
        rows = con.execute("SELECT event, attributes_json FROM telemetry_events").fetchall()
    return [(str(row[0]), json.loads(str(row[1]))) for row in rows]


def test_trace_context_is_passed_through_without_prefix(tmp_path):
    adapter = telemetry.SQLiteTelemetry(str(tmp_path / "telemetry.sqlite3"))
    params = {"_meta": {**_meta(), **_trace()}}
    status, body = ms.handle(
        _request("ping", params),
        _headers("ping"),
        tools={},
        tool_specs=(),
        expected_manifest_hash=ms.tool_manifest_digest(()),
        telemetry=adapter,
    )
    assert status == 200
    meta = body["result"]["_meta"]
    assert meta["traceparent"] == _trace()["traceparent"]
    assert meta["tracestate"] == "vendor=phase4"
    assert meta["baggage"] == "tenant=acme"
    assert not [key for key in meta if key.startswith("mcp.") or key.startswith("trace.")]


def test_sqlite_telemetry_records_low_cardinality_events(tmp_path):
    db = tmp_path / "telemetry.sqlite3"
    adapter = telemetry.SQLiteTelemetry(str(db))
    context = telemetry.TraceContext.from_meta(_trace())
    assert context is not None
    span_id = adapter.start(telemetry.span_name("tools/call"), context)
    adapter.emit(
        "mcp.tool.call",
        telemetry.tool_attributes("query_catalog", context, arguments={"limit": 1}, result={}),
    )
    adapter.finish(span_id, "ok")
    events = _events(db)
    names = [event for event, _ in events]
    assert "mcp.span.start" in names
    assert "mcp.tool.call" in names
    assert "mcp.span.finish" in names
    assert telemetry.span_name("tools/call") == "mcp.tools.call"
    assert any(attrs.get("gen_ai.operation.name") == "execute_tool" for _, attrs in events)


def test_tool_io_logging_is_off_by_default_and_redacted_when_enabled(tmp_path):
    secret = "ghp" + "_" + "c" * 26
    spec = ms.ToolSpec(name="echo", description="Test tool", input_schema={})
    db = tmp_path / "telemetry.sqlite3"
    adapter = telemetry.SQLiteTelemetry(str(db), log_tool_io=False)
    options = {
        "tools": {"echo": lambda args: {"value": args.get("value", "")}},
        "tool_specs": (spec,),
        "expected_manifest_hash": ms.tool_manifest_digest((spec,)),
        "telemetry": adapter,
    }
    status, body = ms.handle(
        _request(
            "tools/call",
            {"name": "echo", "arguments": {"value": secret}, "_meta": _meta()},
        ),
        _headers("tools/call", "echo"),
        **options,
    )
    assert status == 200
    assert body["result"]["content"][0]["text"]
    events = _events(db)
    assert all("gen_ai.tool.call.arguments" not in attrs for _, attrs in events)
    assert all(secret not in json.dumps(attrs) for _, attrs in events)

    opt_in = telemetry.SQLiteTelemetry(str(tmp_path / "opt-in.sqlite3"), log_tool_io=True)
    status, _ = ms.handle(
        _request(
            "tools/call",
            {"name": "echo", "arguments": {"value": secret}, "_meta": _meta()},
        ),
        _headers("tools/call", "echo"),
        **{**options, "telemetry": opt_in},
    )
    assert status == 200
    opt_in_events = _events(tmp_path / "opt-in.sqlite3")
    serialized = json.dumps(opt_in_events)
    assert "gen_ai.tool.call.arguments" in serialized
    assert secret not in serialized
    assert REDACTED in serialized


def test_no_conversation_id_is_invented_from_text():
    assert telemetry.TraceContext.from_meta({"prompt": "free-form user text"}) is None
    context = telemetry.TraceContext.from_meta({"conversation_id": "caller-supplied"})
    assert context is not None
    assert context.to_dict() == {}
    attrs = telemetry.tool_attributes("query_catalog", context)
    assert attrs["conversation.id"] == "caller-supplied"


def test_otel_unavailable_falls_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_HUB_TELEMETRY", "otel")

    def broken_import(name: str):
        raise ImportError(name)

    monkeypatch.setattr(telemetry.importlib, "import_module", broken_import)
    db = tmp_path / "fallback.sqlite3"
    adapter = telemetry.build_telemetry(str(db))
    assert adapter.name == "sqlite"
    assert any(event == "telemetry.fallback" for event, _ in _events(db))


def test_otel_adapter_uses_genai_attributes_and_low_cardinality_span(tmp_path):
    class FakeSpan:
        def __init__(self, name: str) -> None:
            self.name = name
            self.attributes: dict[str, object] = {}
            self.events: list[str] = []
            self.ended = False

        def set_attribute(self, key: str, value: object) -> None:
            self.attributes[key] = value

        def add_event(self, event: str, attributes: dict) -> None:
            _ = attributes
            self.events.append(event)

        def end(self) -> None:
            self.ended = True

    class FakeTracer:
        def __init__(self) -> None:
            self.spans: list[FakeSpan] = []

        def start_span(self, name: str) -> FakeSpan:
            span = FakeSpan(name)
            self.spans.append(span)
            return span

    tracer = FakeTracer()
    adapter = telemetry.OTelTelemetry(tracer)
    context = telemetry.TraceContext.from_meta(_trace())
    span_id = adapter.start("tools/call", context)
    adapter.emit(
        "mcp.tool.call",
        {
            **telemetry.tool_attributes("query_catalog", context, result={"ok": True}),
            "span.id": span_id,
        },
    )
    adapter.finish(span_id, "ok")
    assert [span.name for span in tracer.spans] == ["mcp.tools.call"]
    span = tracer.spans[0]
    assert span.attributes["gen_ai.system"] == "mcp_hub"
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert span.attributes["gen_ai.tool.name"] == "query_catalog"
    assert "mcp.tool.call" in span.events
    assert span.ended is True


def _app_options(tmp_path):
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    phase3 = catalog_tools.build_phase3(catalog, catalog_tools.build_tool_policy())
    runtime = mcp_apps.build_runtime(catalog, phase3.skill_registry, phase3.task_store)
    return {
        "tools": {},
        "tool_specs": (),
        "expected_manifest_hash": ms.tool_manifest_digest(()),
        "resources": {**phase3.skill_resources, **runtime.resources},
        "resource_specs": (*phase3.resource_specs, *runtime.resource_specs),
        "skill_handlers": phase3.skill_handlers,
        "skill_policy": phase3.skill_policy,
        "task_handlers": phase3.task_handlers,
        "persona_handler": phase3.persona_handler,
        "app_policy": runtime.policy,
    }


def test_ui_resources_are_selective_sandboxed_and_redacted(tmp_path):
    secret = "ghp" + "_" + "d" * 26
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    store = catalog_tools.build_phase3(catalog, catalog_tools.build_tool_policy()).task_store
    task = store.create("acme", "s1", payload={"token": secret})
    store.update(str(task["task_id"]), "acme", "s1", status="awaiting_approval")

    options = _app_options(tmp_path)
    status, body = ms.handle(_request("resources/list"), _headers("resources/list"), **options)
    uris = [item["uri"] for item in body["result"]["resources"]]
    assert status == 200
    assert mcp_apps.APPROVAL_URI in uris
    assert mcp_apps.CATALOG_URI in uris
    assert len([uri for uri in uris if uri.startswith("ui://")]) == 2

    status, body = ms.handle(
        _request(
            "resources/read",
            {"uri": mcp_apps.APPROVAL_URI, "tenant_id": "acme", "session_id": "s1"},
        ),
        _headers("resources/read", mcp_apps.APPROVAL_URI),
        **options,
    )
    assert status == 200
    contents = body["result"]["contents"]
    assert [item["mimeType"] for item in contents] == ["text/html", "application/json"]
    html_text = contents[0]["text"]
    assert 'sandbox="allow-scripts"' in html_text
    assert "frame-ancestors 'none'" in html.unescape(html_text)
    assert "'sha256-" in html.unescape(html_text)
    assert "__mcpHubRelayUi" in html_text
    assert secret not in json.dumps(body)


def test_unknown_ui_resource_is_denied_by_policy(tmp_path):
    options = _app_options(tmp_path)
    uri = "ui://mcp-hub/never-allow"
    status, body = ms.handle(
        _request("resources/read", {"uri": uri}),
        _headers("resources/read", uri),
        **options,
    )
    assert status == 200
    assert body["error"]["code"] == ms.POLICY_DENIED


def test_api_wires_trace_context_and_ui_resources(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_HUB_POLICY_JSON", raising=False)
    monkeypatch.delenv("MCP_HUB_POLICY_FILE", raising=False)
    monkeypatch.setenv("MCP_HUB_TELEMETRY", "sqlite")
    monkeypatch.setenv("MCP_HUB_APPS_ENABLED", "1")
    client = TestClient(create_app(db_path=str(tmp_path / "catalog.sqlite3")))

    response = client.post(
        "/mcp",
        headers=_headers("ping"),
        json=_request("ping", {"_meta": {**_meta(), **_trace()}}),
    )
    assert response.status_code == 200
    assert response.json()["result"]["_meta"]["traceparent"] == _trace()["traceparent"]

    response = client.post(
        "/mcp",
        headers=_headers("resources/read", mcp_apps.CATALOG_URI),
        json=_request("resources/read", {"uri": mcp_apps.CATALOG_URI}),
    )
    assert response.status_code == 200
    assert response.json()["result"]["contents"][0]["mimeType"] == "text/html"


def test_disabled_apps_expose_no_ui_resources(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_HUB_POLICY_JSON", raising=False)
    monkeypatch.delenv("MCP_HUB_POLICY_FILE", raising=False)
    monkeypatch.setenv("MCP_HUB_APPS_ENABLED", "off")
    client = TestClient(create_app(db_path=str(tmp_path / "catalog.sqlite3")))
    response = client.post(
        "/mcp",
        headers=_headers("resources/list"),
        json=_request("resources/list"),
    )
    assert response.status_code == 200
    uris = [item["uri"] for item in response.json()["result"]["resources"]]
    assert not [uri for uri in uris if uri.startswith("ui://")]


def test_cli_phase4_commands_emit_json(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "catalog.sqlite3")
    monkeypatch.setenv("MCP_HUB_TELEMETRY", "sqlite")
    monkeypatch.setenv("MCP_HUB_APPS_ENABLED", "1")
    assert main(["--db", db, "telemetry-status"]) == 0
    assert "configured" in capsys.readouterr().out
    assert main(["--db", db, "app-resources"]) == 0
    assert mcp_apps.APPROVAL_URI in capsys.readouterr().out
