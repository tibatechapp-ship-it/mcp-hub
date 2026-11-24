"""Tests for Phase 3: skills, durable tasks and the local persona simulator."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from mcp_hub import catalog_tools, skills
from mcp_hub import mcp_stateless as ms
from mcp_hub.api import create_app
from mcp_hub.cli import main
from mcp_hub.database import Catalog
from mcp_hub.persona import PersonaSimulator
from mcp_hub.tasks import TaskError, TaskStore


def _write_skill(root, name: str, body: str = "Use read-only tools only.\n") -> str:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "version: 1.0.0\n"
        "description: A tiny local skill for tests\n"
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


def test_skill_loader_validates_and_digest_tracks_every_change(tmp_path):
    path = _write_skill(tmp_path, "demo-skill")
    package = skills.load_skill(path)
    assert package.name == "demo-skill"
    assert package.mentioned_tools == ("search_servers", "query_catalog")
    assert package.summary()["grants_execute"] is False
    first = package.digest
    (tmp_path / "demo-skill" / "SKILL.md").write_text(
        (tmp_path / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") + "extra\n",
        encoding="utf-8",
    )
    assert skills.load_skill(path).digest != first


def test_skill_rejects_oversize_symlink_and_secret_metadata(tmp_path):
    good = _write_skill(tmp_path, "good-skill")
    (tmp_path / "good-skill" / "huge.txt").write_text("x" * 70_000, encoding="utf-8")
    try:
        skills.load_skill(good)
    except skills.SkillError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("oversize file was accepted")

    small = _write_skill(tmp_path / "small", "symlink-skill")
    (tmp_path / "small" / "symlink-skill" / "link.txt").symlink_to(
        tmp_path / "small" / "symlink-skill" / "SKILL.md"
    )
    try:
        skills.load_skill(small)
    except skills.SkillError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlink was accepted")

    secret = "ghp" + "_" + "a" * 26
    secret_root = tmp_path / "secret-root"
    directory = secret_root / "secret-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: secret-skill\nversion: 1.0.0\ndescription: token={secret}\n---\nDo nothing.\n",
        encoding="utf-8",
    )
    try:
        skills.load_skill(directory)
    except skills.SkillError as exc:
        assert "secret-shaped" in str(exc)
    else:
        raise AssertionError("secret-shaped metadata was accepted")


def test_approval_is_invalidated_by_package_change(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "demo-skill")
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    registry = skills.SkillRegistry(catalog, root=root)
    registry.approve("demo-skill")
    assert registry.get("demo-skill").approved is True
    skill_md = root / "demo-skill" / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    registry.reload()
    assert registry.get("demo-skill").approved is False
    registry.revoke("demo-skill")
    assert registry.get("demo-skill").approved is False


def test_task_scope_isolation_update_cancel_requeue_and_cleanup(tmp_path):
    store = TaskStore(Catalog(tmp_path / "catalog.sqlite3"))
    task = store.create("acme", "session-1", payload={"step": 1})
    task_id = str(task["task_id"])
    try:
        store.get(task_id, "acme", "session-2")
    except TaskError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("cross-session task read was allowed")

    updated = store.update(task_id, "acme", "session-1", status="running", attempts=2)
    assert updated["status"] == "running"
    assert updated["payload"]["step"] == 1
    assert any(
        item["event"] == "running" for item in store.get(task_id, "acme", "session-1")["events"]
    )

    cancelled = store.cancel(task_id, "acme", "session-1", reason="user stopped")
    assert cancelled["status"] == "cancelled"
    assert store.cancel(task_id, "acme", "session-1")["status"] == "cancelled"

    stale = store.create("acme", "session-1")
    stale_id = str(stale["task_id"])
    store.update(
        stale_id, "acme", "session-1", status="running", lease_until="2000-01-01T00:00:00+00:00"
    )
    assert store.reconcile_interrupted("acme", "session-1") == 1
    assert store.get(stale_id, "acme", "session-1")["status"] == "queued"

    catalog = store.catalog
    with catalog.connect() as con:
        con.execute(
            "UPDATE agent_tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (task_id,),
        )
    assert store.cleanup("acme", "session-1", older_than_seconds=0) == 1
    try:
        store.get(task_id, "acme", "session-1")
    except TaskError:
        pass
    else:
        raise AssertionError("terminal task was not cleaned up")


def test_persona_scenarios_are_deterministic_and_safe(tmp_path):
    store = TaskStore(Catalog(tmp_path / "catalog.sqlite3"))
    simulator = PersonaSimulator(store)
    secret = "ghp" + "_" + "b" * 26

    safe = simulator.run("safe_chain", "acme", "s1", task_id="persona-safe")
    assert [step["tool"] for step in safe.transcript] == [
        "route_task",
        "search_servers",
        "get_server",
    ]
    assert safe.to_dict()["network_calls"] == 0
    assert safe.to_dict()["real_keys_loaded"] is False

    retry = simulator.run("retry_then_success", "acme", "s1", task_id="persona-retry")
    assert [step["status"] for step in retry.transcript] == ["failed", "ok"]

    leak = simulator.run("secret_leak", "acme", "s1", task_id="persona-leak")
    assert leak.redactions == ("secret",)
    assert secret not in json.dumps(leak.to_dict())

    bypass = simulator.run("permission_bypass", "acme", "s1", task_id="persona-bypass")
    assert bypass.status == "cancelled"
    assert bypass.transcript[0]["status"] == "blocked"
    assert "not executable" in bypass.transcript[0]["result"]

    replay = PersonaSimulator(store).run("safe_chain", "acme", "s2", task_id="persona-safe-2")
    assert replay.transcript == safe.transcript


def test_mcp_skill_methods_respect_policy_and_header_validation(tmp_path):
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    phase3 = catalog_tools.build_phase3(catalog, catalog_tools.build_tool_policy())
    empty_specs: tuple = ()
    options = {
        "tools": {},
        "tool_specs": empty_specs,
        "expected_manifest_hash": ms.tool_manifest_digest(empty_specs),
        "skill_handlers": phase3.skill_handlers,
        "resources": phase3.skill_resources,
        "resource_specs": phase3.resource_specs,
        "skill_policy": phase3.skill_policy,
        "task_handlers": phase3.task_handlers,
        "persona_handler": phase3.persona_handler,
    }

    status, body = ms.handle(_request("skills/list"), _headers("skills/list"), **options)
    assert status == 200
    assert body["result"]["skills"][0]["name"] == "mcp-hub-catalog"
    assert body["result"]["skills"][0]["grants_execute"] is False

    uri = "mcp-hub://skills/mcp-hub-catalog/SKILL.md"
    status, body = ms.handle(
        _request("resources/read", {"uri": uri}), _headers("resources/read", uri), **options
    )
    assert status == 200
    assert "Read-only workflow for discovering" in body["result"]["contents"][0]["text"]

    def deny(name: str, args):
        _ = args
        raise ms.MCPError(ms.POLICY_DENIED, f"blocked {name}")

    status, body = ms.handle(
        _request("skills/get", {"name": "mcp-hub-catalog"}),
        _headers("skills/get", "mcp-hub-catalog"),
        **{**options, "skill_policy": deny},
    )
    assert body["error"]["code"] == ms.POLICY_DENIED

    status, body = ms.handle(
        _request("skills/get", {"name": "mcp-hub-catalog"}),
        _headers("skills/get", "wrong-name"),
        **options,
    )
    assert status == 400
    assert body["error"]["code"] == ms.HEADER_MISMATCH


def test_api_wires_tasks_and_persona(tmp_path):
    client = TestClient(create_app(db_path=str(tmp_path / "catalog.sqlite3")))
    response = client.post(
        "/mcp",
        headers=_headers("persona/simulate", "safe_chain"),
        json=_request(
            "persona/simulate", {"scenario": "safe_chain", "tenant_id": "t", "session_id": "s"}
        ),
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["scenario"] == "safe_chain"
    assert result["network_calls"] == 0

    response = client.post(
        "/mcp",
        headers=_headers("tasks/get", "missing-task"),
        json=_request(
            "tasks/get", {"task_id": "missing-task", "tenant_id": "t", "session_id": "other"}
        ),
    )
    assert response.json()["error"]["code"] == ms.TASK_DENIED


def test_cli_phase3_commands_emit_json(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "catalog.sqlite3")
    monkeypatch.delenv("MCP_HUB_POLICY_JSON", raising=False)
    monkeypatch.delenv("MCP_HUB_POLICY_FILE", raising=False)
    assert main(["--db", db, "skill-list"]) == 0
    assert "mcp-hub-catalog" in capsys.readouterr().out
    assert main(["--db", db, "skill-approve", "mcp-hub-catalog"]) == 0
    assert "approved" in capsys.readouterr().out
    assert (
        main(["--db", db, "persona-simulate", "safe_chain", "--tenant", "t", "--session", "s"]) == 0
    )
    assert "transcript" in capsys.readouterr().out
    assert main(["--db", db, "skill-revoke", "mcp-hub-catalog"]) == 0
    assert "removed" in capsys.readouterr().out
