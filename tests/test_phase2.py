"""Tests for Phase 2: policy, routing, description smells and header guard."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mcp_hub import catalog_tools, description_smells, semantic_router
from mcp_hub import mcp_stateless as ms
from mcp_hub import policy_gateway as pg
from mcp_hub.api import create_app
from mcp_hub.cli import main


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


def _headers(method: str, name: str | None = None) -> dict:
    headers = {"MCP-Protocol-Version": ms.MCP_PROTOCOL_VERSION, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _specs() -> tuple[ms.ToolSpec, ...]:
    return (ms.ToolSpec(name="alpha", description="Return x", input_schema={}),)


def _handle(request: dict, headers: dict, **overrides):
    options = {
        "tools": {"alpha": lambda args: {"value": args.get("x")}},
        "tool_specs": _specs(),
        "expected_manifest_hash": ms.tool_manifest_digest(_specs()),
    }
    options.update(overrides)
    return ms.handle(request, headers, **options)


def test_default_policy_allows_and_redacts_arguments():
    gateway = pg.PolicyGateway.from_doc(pg.builtin_default_policy())
    decision = gateway.check("search_servers", {"query": "api" + "_key=" + "abcdefghij"})
    assert decision.allowed is True
    assert "REDACTED" in decision.arguments["query"]
    assert "abcdefghij" not in decision.arguments["query"]
    assert "secret" in decision.redactions


def test_policy_allow_deny_and_unsupported_controls_fail_closed():
    gateway = pg.PolicyGateway.from_doc(
        {
            "version": 1,
            "tools": {"allow": ["search_*"], "deny": ["search_secret"]},
        }
    )
    assert gateway.allows_tool("search_servers") is True
    assert gateway.allows_tool("search_secret") is False
    assert gateway.allows_tool("doctor_report") is False
    with pytest.raises(pg.ConfigError, match="unsupported policy controls"):
        pg.PolicyGateway.from_doc({"models": ["tiny"]})


def test_policy_inline_env_rejects_two_sources(monkeypatch):
    monkeypatch.setenv(pg.ENV_POLICY_JSON, json.dumps({"version": 1}))
    monkeypatch.setenv(pg.ENV_POLICY_FILE, "policy.json")
    with pytest.raises(pg.ConfigError, match="not both"):
        pg.PolicyGateway.from_env()


def test_custom_redaction_and_audit_are_written(tmp_path, monkeypatch):
    audit = tmp_path / "policy.jsonl"
    monkeypatch.setenv(pg.ENV_AUDIT_PATH, str(audit))
    gateway = pg.PolicyGateway.from_doc(
        {
            "version": 1,
            "name": "custom",
            "tools": {"allow": ["*"]},
            "privacy": {"custom": [{"pattern": r"ref-\d+", "replace": "[REF]"}]},
            "audit": {"enabled": True},
        }
    )
    args = gateway.enforce("alpha", {"q": ["ref-123", "ref-456"]})
    assert args == {"q": ["[REF]", "[REF]"]}
    lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["allowed"] is True
    assert record["policy"] == "custom"


def test_policy_snapshot_hides_paths(monkeypatch, tmp_path):
    monkeypatch.setenv(pg.ENV_POLICY_JSON, json.dumps({"version": 1, "name": "snapshot-test"}))
    snapshot = catalog_tools.policy_snapshot({})
    assert snapshot["policy"] == "snapshot-test"
    assert str(tmp_path) not in json.dumps(snapshot)


def test_semantic_router_ranks_arabic_task_and_compacts():
    result = semantic_router.route_task(
        "ابحث عن خادم sql", specs=catalog_tools.TOOL_SPECS, limit=3, compact=True
    )
    assert result["language"] in {"ar", "mixed"}
    assert 1 <= result["count"] <= len(catalog_tools.TOOL_SPECS)
    assert all("input_schema" not in hit for hit in result["results"])
    assert all(len(hit["description"]) <= 80 for hit in result["results"])


def test_semantic_router_full_mode_keeps_schema():
    result = semantic_router.route_task(
        "search servers", specs=catalog_tools.TOOL_SPECS, limit=1, compact=False
    )
    assert result["results"]
    assert "input_schema" in result["results"][0]
    assert "matched" in result["results"][0]


def test_description_smells_use_stable_codes():
    assert "S1_EMPTY" in description_smells.check_description("  ")["codes"]
    assert "S3_PLACEHOLDER" in description_smells.check_description("TODO write this")["codes"]
    assert (
        "S4_SECRET_SHAPED"
        in description_smells.check_description("token=" + "abcdefghijklmnopqrstuvwxyz")["codes"]
    )
    assert "S5_VAGUE" in description_smells.check_description("tool")["codes"]
    assert (
        "S6_MIXED_LANGUAGE" in description_smells.check_description("اداة用於 python xyz")["codes"]
    )
    assert (
        description_smells.check_description("Search the catalog by name and category.")["pass"]
        is True
    )


def test_registry_audit_is_bundled_and_diffable():
    report = description_smells.audit_registry(limit=5)
    assert report["checked"] == 5
    assert set(report["by_code"]) == set(description_smells.SMELL_CODES)
    assert report["clean"] + report["flagged"] == report["checked"]


def test_sensitive_mirrored_header_is_rejected():
    headers = _headers("tools/call", name="alpha")
    headers["Mcp-Param-api_key"] = "x"
    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"api_key": "x"}}), headers
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_tool_policy_hook_can_deny_with_policy_code():
    def deny(name, args):
        _ = name, args
        raise ms.MCPError(ms.POLICY_DENIED, "blocked", {"rule": "test"})

    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {}}),
        _headers("tools/call", name="alpha"),
        tool_policy=deny,
    )
    assert status == 200
    assert body["error"]["code"] == ms.POLICY_DENIED
    assert body["error"]["data"]["rule"] == "test"


def test_tool_policy_hook_passes_sanitized_arguments():
    seen = {}

    def handler(args):
        seen.update(args)
        return "ok"

    def scrub(name, args):
        _ = name
        return {"x": 99}

    status, body = _handle(
        _request("tools/call", {"name": "alpha", "arguments": {"x": 1}}),
        _headers("tools/call", name="alpha"),
        tools={"alpha": handler},
        tool_policy=scrub,
    )
    assert status == 200
    assert body["result"]["isError"] is False
    assert seen == {"x": 99}


def test_api_applies_policy_to_mcp_calls(tmp_path, monkeypatch):
    policy = {
        "version": 1,
        "name": "api-test",
        "tools": {"allow": ["*"], "deny": ["doctor_report"]},
    }
    monkeypatch.setenv(pg.ENV_POLICY_JSON, json.dumps(policy))
    client = TestClient(create_app(db_path=str(tmp_path / "mcp.sqlite3")))
    response = client.post(
        "/mcp",
        headers=_headers("tools/call", name="doctor_report"),
        json=_request("tools/call", {"name": "doctor_report", "arguments": {}}),
    )
    assert response.status_code == 200
    assert response.json()["error"]["code"] == ms.POLICY_DENIED


def test_cli_phase2_commands_emit_json(monkeypatch, capsys):
    monkeypatch.setenv(pg.ENV_POLICY_JSON, json.dumps({"version": 1, "name": "cli-test"}))
    assert main(["route-task", "ابحث عن خادم sql", "--compact", "--limit", "2"]) == 0
    assert "results" in capsys.readouterr().out
    assert main(["description-smells", "--limit", "2"]) == 0
    assert "by_code" in capsys.readouterr().out
    assert main(["policy-snapshot"]) == 0
    assert "cli-test" in capsys.readouterr().out
