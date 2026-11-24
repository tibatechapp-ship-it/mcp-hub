"""Tests for Phase 5: default-deny approvals over MCP, CLI and Apps."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mcp_hub import apps, catalog_tools, skills
from mcp_hub import mcp_stateless as ms
from mcp_hub import policy_gateway as pg
from mcp_hub.api import create_app
from mcp_hub.cli import main
from mcp_hub.database import Catalog
from mcp_hub.tasks import TaskStore


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    monkeypatch.delenv(pg.ENV_POLICY_JSON, raising=False)
    monkeypatch.delenv(pg.ENV_POLICY_FILE, raising=False)


def _write_skill(root, name: str, body: str = "Use read-only tools only.\n") -> str:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "version: 1.0.0\n"
        "description: A tiny local skill for approval tests\n"
        "mentioned_tools: search_servers, query_catalog\n"
        "---\n" + body,
        encoding="utf-8",
    )
    return str(directory)


def _meta() -> dict:
    return {
        ms.META_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION,
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


def _options(**overrides) -> dict:
    specs: tuple = ()
    options = {
        "tools": {},
        "tool_specs": specs,
        "expected_manifest_hash": ms.tool_manifest_digest(specs),
    }
    options.update(overrides)
    return options


def _allow_approvals(monkeypatch, actions: list[str], deny: list[str] | None = None) -> None:
    monkeypatch.setenv(
        pg.ENV_POLICY_JSON,
        json.dumps(
            {
                "version": 1,
                "name": "phase5-tests",
                "tools": {"allow": ["*"], "deny": []},
                "skills": {"allow": ["*"], "deny": []},
                "approvals": {"allow": actions, "deny": deny or []},
            }
        ),
    )


def test_approval_policy_defaults_deny_and_snapshot_is_safe():
    gateway = pg.PolicyGateway.from_doc(pg.builtin_default_policy())
    assert gateway.allows_approval("skills.approve") is False
    assert gateway.allows_approval("tasks.approve") is False
    with pytest.raises(pg.PolicyDenied):
        gateway.enforce_approval("skills.approve")
    snapshot = gateway.snapshot()
    assert snapshot["approvals_allow"] == []
    assert snapshot["approvals_deny"] == []
    assert "audit_path" not in snapshot


def test_approval_policy_explicit_allow_and_deny_win(monkeypatch):
    _allow_approvals(
        monkeypatch,
        ["skills.approve", "skills.revoke", "tasks.approve"],
        deny=["skills.revoke"],
    )
    gateway = pg.PolicyGateway.from_env()
    assert gateway.allows_approval("skills.approve") is True
    assert gateway.allows_approval("skills.revoke") is False
    assert gateway.allows_approval("tasks.approve") is True
    assert gateway.allows_approval("tasks.list") is False
    assert catalog_tools.policy_snapshot({})["approvals_allow"] == [
        "skills.approve",
        "skills.revoke",
        "tasks.approve",
    ]
    with pytest.raises(pg.ConfigError, match="approvals.allow"):
        pg.PolicyGateway.from_doc({"approvals": {"allow": "skills.approve"}})


def test_invalid_approval_policy_fails_closed(monkeypatch):
    monkeypatch.setenv(pg.ENV_POLICY_JSON, "{not json")
    policy = catalog_tools.build_approval_policy()
    with pytest.raises(ms.MCPError) as exc:
        policy("skills.approve", {})
    assert exc.value.code == ms.POLICY_DENIED


def test_skill_approval_is_digest_bound_with_redacted_events(tmp_path):
    _write_skill(tmp_path, "approval-skill")
    catalog = Catalog(str(tmp_path / "catalog.sqlite3"))
    registry = skills.SkillRegistry(catalog, root=str(tmp_path))
    assert registry.get("approval-skill").approved is False

    first = registry.approve("approval-skill", "reviewer")
    assert registry.get("approval-skill").approved is True
    (tmp_path / "approval-skill" / "SKILL.md").write_text(
        (tmp_path / "approval-skill" / "SKILL.md").read_text(encoding="utf-8") + "changed\n",
        encoding="utf-8",
    )
    registry.reload()
    assert registry.get("approval-skill").approved is False
    second = registry.approve("approval-skill", "reviewer")
    assert second != first
    assert registry.revoke("approval-skill", "reviewer") is True

    events = registry.approval_events(limit=3)
    assert [item["action"] for item in events] == ["revoked", "approved", "approved"]
    assert events[0]["sha256"] == second

    raw = "api" + "_key=" + "abcdefghij"
    with catalog.connect() as con:
        registry._record_approval_event(con, "audit-skill", "digest", "approved", "reviewer", raw)
    detail = registry.approval_events("audit-skill", limit=1)[0]["detail"]
    assert raw not in detail
    assert "<REDACTED>" in detail
    with pytest.raises(skills.SkillError, match="limit"):
        registry.approval_events(limit=0)


def test_skill_approval_methods_gate_policy_and_headers(tmp_path, monkeypatch):
    _write_skill(tmp_path, "approval-skill")
    catalog = Catalog(str(tmp_path / "catalog.sqlite3"))
    registry = skills.SkillRegistry(catalog, root=str(tmp_path))
    options = _options(
        skill_approval_handlers=skills.build_skill_approval_handlers(registry),
        approval_policy=catalog_tools.build_approval_policy(),
    )
    name = "approval-skill"

    status, body = ms.handle(
        _request("skills/approve", {"name": name}),
        _headers("skills/approve", name),
        **options,
    )
    assert status == 200
    assert body["error"]["code"] == ms.POLICY_DENIED

    status, body = ms.handle(
        _request("skills/approve", {"name": name}),
        _headers("skills/approve", "wrong-name"),
        **options,
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH

    status, body = ms.handle(
        _request("skills/approve", {"name": name}),
        {"MCP-Protocol-Version": ms.MCP_PROTOCOL_VERSION, "Mcp-Method": "skills/approve"},
        **options,
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH

    _allow_approvals(monkeypatch, ["skills.*"], deny=["skills.revoke"])
    options["approval_policy"] = catalog_tools.build_approval_policy()
    status, body = ms.handle(
        _request("skills/approve", {"name": name, "actor": "reviewer"}),
        _headers("skills/approve", name),
        **options,
    )
    assert status == 200
    assert body["result"]["approved"] is True
    assert body["result"]["sha256"] == registry.get(name).digest

    status, body = ms.handle(
        _request("skills/revoke", {"name": name}),
        _headers("skills/revoke", name),
        **options,
    )
    assert body["error"]["code"] == ms.POLICY_DENIED
    assert registry.get(name).approved is True

    _allow_approvals(monkeypatch, ["skills.*"])
    options["approval_policy"] = catalog_tools.build_approval_policy()
    status, body = ms.handle(
        _request("skills/revoke", {"name": name}),
        _headers("skills/revoke", name),
        **options,
    )
    assert status == 200
    assert body["result"]["removed"] is True
    assert registry.get(name).approved is False

    assert ms.NAME_HEADERS_PARAMS["skills/approve"] == "name"
    assert ms.NAME_HEADERS_PARAMS["skills/revoke"] == "name"


def test_task_approve_is_scoped_atomic_and_redacts_events(tmp_path, monkeypatch):
    catalog = Catalog(str(tmp_path / "catalog.sqlite3"))
    store = TaskStore(catalog)
    task = store.create("acme", "s1", "tool_chain")
    store.update(
        task["task_id"], "acme", "s1", status="awaiting_approval", event="awaiting_approval"
    )
    _allow_approvals(monkeypatch, ["tasks.approve"])
    options = _options(
        task_handlers=catalog_tools.build_task_handlers(store),
        approval_policy=catalog_tools.build_approval_policy(),
    )
    task_id = task["task_id"]

    status, body = ms.handle(
        _request(
            "tasks/approve",
            {"task_id": task_id, "tenant_id": "acme", "session_id": "other"},
        ),
        _headers("tasks/approve", task_id),
        **options,
    )
    assert status == 200
    assert body["error"]["code"] == ms.TASK_DENIED

    status, body = ms.handle(
        _request("tasks/approve", {"task_id": task_id, "tenant_id": "acme", "session_id": "s1"}),
        _headers("tasks/approve", "wrong-task"),
        **options,
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH

    raw = "api" + "_key=" + "abcdefghij"
    status, body = ms.handle(
        _request(
            "tasks/approve",
            {
                "task_id": task_id,
                "tenant_id": "acme",
                "session_id": "s1",
                "actor": "reviewer",
                "reason": raw,
            },
        ),
        _headers("tasks/approve", task_id),
        **options,
    )
    assert status == 200
    assert body["result"]["status"] == "queued"
    detail = store.get(task_id, "acme", "s1")["events"][-1]["detail"]
    assert raw not in detail
    assert "<REDACTED>" in detail

    status, body = ms.handle(
        _request("tasks/approve", {"task_id": task_id, "tenant_id": "acme", "session_id": "s1"}),
        _headers("tasks/approve", task_id),
        **options,
    )
    assert body["error"]["code"] == ms.TASK_DENIED

    status, body = ms.handle(
        _request("tasks/list", {"tenant_id": "acme"}),
        _headers("tasks/list"),
        **options,
    )
    assert status == 200
    assert any(item["task_id"] == task_id for item in body["result"]["tasks"])

    status, body = ms.handle(
        _request("tasks/list", {"tenant_id": "other"}),
        _headers("tasks/list"),
        **options,
    )
    assert body["result"]["tasks"] == []

    status, body = ms.handle(
        _request("tasks/list", {"tenant_id": "acme"}),
        _headers("tasks/list", "unexpected"),
        **options,
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH

    with pytest.raises(Exception, match="not awaiting approval"):
        store.approve(task_id, "acme", "s1")


def test_discover_advertises_approval_capabilities(tmp_path):
    status, body = ms.handle(
        _request("server/discover"),
        {"MCP-Protocol-Version": ms.MCP_PROTOCOL_VERSION, "Mcp-Method": "server/discover"},
        **_options(),
    )
    assert status == 200
    caps = body["result"]["capabilities"]
    assert caps["approvals"] == {"skills": True, "tasks": True}
    assert caps["tasks"]["list"] is True
    assert caps["tasks"]["approve"] is True


def test_apps_approval_surface_exposes_metadata_only(tmp_path):
    catalog = Catalog(str(tmp_path / "catalog.sqlite3"))
    registry = skills.SkillRegistry(catalog)
    store = TaskStore(catalog)
    runtime = apps.build_runtime(catalog, registry, store)
    payload = runtime.resources[apps.APPROVAL_URI]({"tenant_id": "acme", "session_id": "s1"})
    snapshot = json.loads(payload["contents"][1]["text"])["snapshot"]
    assert snapshot["read_only_ui"] is True
    assert {item["method"] for item in snapshot["available_actions"]} == {
        "skills/approve",
        "skills/revoke",
        "tasks/approve",
        "tasks/list",
    }
    html = payload["contents"][0]["text"]
    assert "Content-Security-Policy" in html
    assert "onclick=" not in html
    assert "postMessage" in html


def test_cli_task_list_and_approve_report_errors(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "catalog.sqlite3")
    store = TaskStore(Catalog(db))
    task = store.create("acme", "s1", "tool_chain")
    store.update(task["task_id"], "acme", "s1", status="awaiting_approval")

    assert main(["--db", db, "task-list", "--tenant", "acme", "--session", "s1"]) == 0
    assert task["task_id"] in capsys.readouterr().out

    assert (
        main(
            [
                "--db",
                db,
                "task-approve",
                task["task_id"],
                "--tenant",
                "acme",
                "--session",
                "s1",
                "--actor",
                "reviewer",
            ]
        )
        == 0
    )
    assert '"status": "queued"' in capsys.readouterr().out

    assert main(["--db", db, "task-approve", "missing", "--tenant", "acme", "--session", "s1"]) == 2
    assert "failed" in capsys.readouterr().err


def test_api_wires_approval_policy(tmp_path, monkeypatch):
    db = str(tmp_path / "catalog.sqlite3")
    client = TestClient(create_app(db_path=db))
    name = "mcp-hub-catalog"
    response = client.post(
        "/mcp",
        headers=_headers("skills/approve", name),
        json=_request("skills/approve", {"name": name}),
    )
    assert response.json()["error"]["code"] == ms.POLICY_DENIED

    _allow_approvals(monkeypatch, ["skills.approve"])
    approved_client = TestClient(create_app(db_path=db))
    response = approved_client.post(
        "/mcp",
        headers=_headers("skills/approve", name),
        json=_request("skills/approve", {"name": name, "actor": "reviewer"}),
    )
    assert response.json()["result"]["approved"] is True
