"""Edge tests that keep the Phase 5 sensitive modules near full coverage."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_hub import (
    catalog_query,
    description_smells,
    live_sync,
    persona,
    semantic_router,
    skills,
)
from mcp_hub import mcp_stateless as ms
from mcp_hub import policy_gateway as pg
from mcp_hub.cli import EXIT_BAD_INPUT, main
from mcp_hub.database import Catalog
from mcp_hub.tasks import TaskError, TaskStore


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    monkeypatch.delenv(pg.ENV_POLICY_JSON, raising=False)
    monkeypatch.delenv(pg.ENV_POLICY_FILE, raising=False)
    monkeypatch.delenv(pg.ENV_AUDIT_PATH, raising=False)


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "policy document must be an object"),
        ({"version": 2}, "unsupported policy schema version"),
        ({"tools": []}, "policy section 'tools' must be an object"),
        ({"tools": {"allow": ["*"], "oops": []}}, "unsupported keys in 'tools'"),
        ({"tools": {"allow": "search_servers"}}, "tools.allow must be a list"),
        ({"tools": {"deny": "search_servers"}}, "tools.deny must be a list"),
        ({"skills": {"allow": "mcp-hub-catalog"}}, "skills.allow must be a list"),
        ({"skills": {"deny": "mcp-hub-catalog"}}, "skills.deny must be a list"),
        ({"approvals": {"deny": "skills.approve"}}, "approvals.deny must be a list"),
        ({"privacy": {"redact": "email"}}, "privacy.redact must be a list"),
        ({"privacy": {"redact": ["unknown"]}}, "unknown redaction pattern"),
        ({"privacy": {"custom": [{"replace": "X"}]}}, "need a pattern"),
        ({"privacy": {"custom": [{"pattern": "["}]}}, "invalid custom redaction pattern"),
        ({"audit": []}, "policy section 'audit' must be an object"),
    ],
)
def test_policy_config_errors_are_explicit(doc, message):
    with pytest.raises(pg.ConfigError, match=message):
        pg.PolicyGateway.from_doc(doc)


def test_policy_from_file_and_approval_allow(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "approvals": {"allow": ["skills.approve"], "deny": []},
                "skills": {"allow": ["*"], "deny": ["blocked-skill"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(pg.ENV_POLICY_FILE, str(path))
    gateway = pg.PolicyGateway.from_env()
    assert gateway.allows_approval("skills.approve")
    gateway.enforce_approval("skills.approve")
    assert not gateway.allows_skill("blocked-skill")
    with pytest.raises(pg.PolicyDenied, match="Skill blocked by policy"):
        gateway.enforce_skill("blocked-skill")

    monkeypatch.setenv(pg.ENV_POLICY_JSON, '{"version": 1}')
    with pytest.raises(pg.ConfigError, match="not both"):
        pg.PolicyGateway.from_env()


def test_policy_redaction_and_audit_are_real(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv(pg.ENV_AUDIT_PATH, str(audit))
    gateway = pg.PolicyGateway.from_doc(
        {
            "privacy": {
                "redact": ["email", "ssn", "iban", "phone"],
                "custom": [{"pattern": r"tok-\d+", "replace": "[TOKEN]"}],
            },
            "audit": {"enabled": True},
        }
    )
    decision = gateway.check(
        "search_servers",
        {
            "email": "user@example.com",
            "ssn": "123-45-6789",
            "token": "tok-12345",
            "nested": ["123-45-6789"],
        },
    )
    assert decision.allowed
    assert decision.arguments["token"] == "[TOKEN]"
    assert decision.arguments["nested"] == ["[SSN]"]
    assert decision.redactions
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "search_servers"
    assert records[0]["allowed"] is True

    gateway = pg.PolicyGateway.from_doc(
        {"tools": {"deny": ["search_servers"]}, "audit": {"enabled": True}}
    )
    with pytest.raises(pg.PolicyDenied, match="Tool blocked by policy"):
        gateway.enforce("search_servers", {})
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["allowed"] is False


def _skill_text(
    name="good-skill",
    version="1.0.0",
    description="A safe local skill",
    tools="search_servers",
    body="Do the thing.\n",
    extra=None,
):
    lines = ["---", f"name: {name}"]
    if version is not None:
        lines.append(f"version: {version}")
    lines.append(f"description: {description}")
    if tools is not None:
        lines.append(f"mentioned_tools: {tools}")
    if extra:
        lines.append(extra)
    return "\n".join([*lines, "---", body])


def _write_skill_dir(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "text",
    [
        "hello\n---\nbody\n",
        "---\nname: x\n",
        "---\nname\n---\nbody\n",
        _skill_text(extra="unknown: value"),
        _skill_text(extra="name: duplicate"),
        _skill_text(body=""),
        _skill_text(version=None),
        _skill_text(name="Bad Name"),
        _skill_text(version="v1"),
        _skill_text(description="x" * 501),
        _skill_text(tools="Bad Tool"),
    ],
)
def test_skill_frontmatter_validation(tmp_path, text):
    path = _write_skill_dir(tmp_path, "case", text)
    with pytest.raises(skills.SkillError):
        skills.load_skill(path)


def test_skill_loader_file_and_package_limits(tmp_path, monkeypatch):
    valid = _write_skill_dir(tmp_path, "valid", _skill_text())

    no_skill = tmp_path / "no-skill"
    no_skill.mkdir()
    (no_skill / "notes.txt").write_text("nothing", encoding="utf-8")
    with pytest.raises(skills.SkillError, match="requires SKILL.md"):
        skills.load_skill(no_skill)

    binary = _write_skill_dir(tmp_path / "binary", "case", _skill_text())
    (binary / "blob.bin").write_bytes(b"\xff\xfe")
    with pytest.raises(skills.SkillError, match="not UTF-8"):
        skills.load_skill(binary)

    monkeypatch.setattr(skills, "MAX_FILE_BYTES", 1)
    with pytest.raises(skills.SkillError, match="file too large"):
        skills.load_skill(valid)
    monkeypatch.setattr(skills, "MAX_FILE_BYTES", 65_536)
    monkeypatch.setattr(skills, "MAX_PACKAGE_BYTES", 1)
    with pytest.raises(skills.SkillError, match="exceeds the size limit"):
        skills.load_skill(valid)
    monkeypatch.setattr(skills, "MAX_PACKAGE_BYTES", 262_144)

    many = _write_skill_dir(tmp_path / "many", "case", _skill_text())
    for index in range(32):
        (many / f"extra-{index}.txt").write_text("x", encoding="utf-8")
    with pytest.raises(skills.SkillError, match="1 to 32 files"):
        skills.load_skill(many)

    with pytest.raises(skills.SkillError, match="must be a directory"):
        skills.load_skill(tmp_path / "missing")


def test_skill_registry_skips_noise_and_rejects_duplicates(tmp_path):
    db = str(tmp_path / "catalog.sqlite3")
    root = tmp_path / "skills"
    root.mkdir()
    (root / "notes.txt").write_text("ignored", encoding="utf-8")
    empty_dir = root / "empty"
    empty_dir.mkdir()
    _write_skill_dir(root, "one", _skill_text())
    _write_skill_dir(root, "two", _skill_text(body="Duplicate name.\n"))
    with pytest.raises(skills.SkillError, match="duplicate skill name"):
        skills.SkillRegistry(Catalog(db), root=root)


def test_skill_handlers_and_resource_errors(tmp_path):
    db = str(tmp_path / "catalog.sqlite3")
    registry = skills.SkillRegistry(Catalog(db))
    handlers = skills.build_skill_handlers(registry)
    with pytest.raises(skills.SkillError, match="name is required"):
        handlers["skills/get"]({})
    assert handlers["skills/get"]({"name": "mcp-hub-catalog"})["skill"]["files"]

    approvals = skills.build_skill_approval_handlers(registry)
    with pytest.raises(skills.SkillError, match="name is required"):
        approvals["skills/approve"]({})
    assert approvals["skills/approve"]({"name": "mcp-hub-catalog", "actor": ""})["approved"]
    assert approvals["skills/revoke"]({"name": "mcp-hub-catalog"})["removed"]

    with pytest.raises(skills.SkillError, match="unsupported skill resource URI"):
        registry.read_resource("mcp-hub://other/x")
    with pytest.raises(skills.SkillError, match="invalid skill resource URI"):
        registry.read_resource(f"{skills.SKILL_RESOURCE_PREFIX}mcp-hub-catalog/../etc/passwd")
    with pytest.raises(skills.SkillError, match="unknown skill resource"):
        registry.read_resource(f"{skills.SKILL_RESOURCE_PREFIX}mcp-hub-catalog/missing.md")
    with pytest.raises(skills.SkillError, match="limit must be between"):
        registry.approval_events(limit=0)
    with pytest.raises(KeyError, match="unknown skill"):
        registry.get("missing-skill")


def test_task_validation_and_transition_edges(tmp_path):
    db = str(tmp_path / "catalog.sqlite3")
    store = TaskStore(Catalog(db))
    task = store.create("acme", "s1", "tool_chain")

    with pytest.raises(TaskError, match="task_id must be"):
        store.get("bad id!", "acme", "s1")
    with pytest.raises(TaskError, match="error must be a string"):
        store.update(task["task_id"], "acme", "s1", error=123)
    with pytest.raises(TaskError, match="error is too long"):
        store.update(task["task_id"], "acme", "s1", error="x" * 2001)
    with pytest.raises(TaskError, match="invalid task status"):
        store.update(task["task_id"], "acme", "s1", status="bogus")
    with pytest.raises(TaskError, match="non-negative integer"):
        store.update(task["task_id"], "acme", "s1", attempts=-1)
    with pytest.raises(TaskError, match="cleanup arguments are out of range"):
        store.cleanup("acme", older_than_seconds=-1)

    merged = store.update(task["task_id"], "acme", "s1", payload={"stage": "assets"})
    assert merged["payload"]["stage"] == "assets"


def test_cli_error_branches_return_documented_codes(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "catalog.sqlite3")

    monkeypatch.setattr(
        live_sync,
        "refresh_from_upstream",
        lambda *_args, **_kwargs: SimpleNamespace(status="ok", source="upstream", digest="abc"),
    )
    assert main(["--db", db, "sync-arabic", "--cache", str(tmp_path / "cache.json")]) == 0
    assert "upstream" in capsys.readouterr().out

    def _boom(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(live_sync, "refresh_from_upstream", _boom)
    assert main(["--db", db, "sync-arabic"]) == EXIT_BAD_INPUT
    assert "arabic sync failed" in capsys.readouterr().err

    def _value_error(*_args, **_kwargs):
        raise ValueError("bad registry")

    monkeypatch.setattr(semantic_router, "route_task", _value_error)
    monkeypatch.setattr(description_smells, "audit_registry", _value_error)
    monkeypatch.setattr(catalog_query, "query_catalog", _value_error)
    assert main(["--db", db, "route-task", "find postgres"]) == EXIT_BAD_INPUT
    assert "route-task failed" in capsys.readouterr().err
    assert main(["--db", db, "description-smells"]) == EXIT_BAD_INPUT
    assert "description-smells failed" in capsys.readouterr().err
    assert main(["--db", db, "query-catalog"]) == EXIT_BAD_INPUT
    assert "query-catalog failed" in capsys.readouterr().err

    monkeypatch.setenv(pg.ENV_POLICY_JSON, "{not-json")
    assert main(["--db", db, "policy-snapshot"]) == EXIT_BAD_INPUT
    assert "policy-snapshot failed" in capsys.readouterr().err

    assert main(["--db", db, "skill-get", "mcp-hub-catalog"]) == 0
    assert "SKILL.md" in capsys.readouterr().out
    assert main(["--db", db, "skill-get", "missing-skill"]) == EXIT_BAD_INPUT
    assert "skill-get failed" in capsys.readouterr().err

    def _skill_revoke_boom(_self, _name, _actor):
        raise ValueError("bad actor")

    monkeypatch.setattr(skills.SkillRegistry, "revoke", _skill_revoke_boom)
    assert main(["--db", db, "skill-revoke", "mcp-hub-catalog", "--json"]) == EXIT_BAD_INPUT
    assert json.loads(capsys.readouterr().err)["exit_code"] == EXIT_BAD_INPUT
    monkeypatch.undo()

    store = TaskStore(Catalog(db))
    task = store.create("acme", "s1", "tool_chain")
    assert (
        main(["--db", db, "task-get", task["task_id"], "--tenant", "acme", "--session", "s1"]) == 0
    )
    assert task["task_id"] in capsys.readouterr().out
    assert (
        main(["--db", db, "task-get", "missing", "--tenant", "acme", "--session", "s1"])
        == EXIT_BAD_INPUT
    )
    assert "task-get failed" in capsys.readouterr().err
    assert (
        main(["--db", db, "task-cancel", task["task_id"], "--tenant", "acme", "--session", "s1"])
        == 0
    )
    assert "cancelled" in capsys.readouterr().out
    assert (
        main(["--db", db, "task-cancel", "missing", "--tenant", "acme", "--session", "s1"])
        == EXIT_BAD_INPUT
    )
    assert "task-cancel failed" in capsys.readouterr().err

    def _persona_boom(*_args, **_kwargs):
        raise TaskError("persona failed")

    monkeypatch.setattr(persona.PersonaSimulator, "run", _persona_boom)
    assert (
        main(["--db", db, "persona-simulate", "safe_chain", "--tenant", "acme", "--session", "s1"])
        == EXIT_BAD_INPUT
    )
    assert "persona-simulate failed" in capsys.readouterr().err


def _rpc_request(method, params=None, request_id=1):
    payload = {"_meta": {ms.META_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION}}
    payload.update(params or {})
    return {"jsonrpc": ms.JSONRPC_VERSION, "id": request_id, "method": method, "params": payload}


def _rpc_handle(method, params=None, *, tools=None, specs=(), **kwargs):
    tools = dict(tools or {})
    headers = {ms.HEADER_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION, ms.HEADER_METHOD: method}
    source = ms.NAME_HEADERS_PARAMS.get(method)
    if source and isinstance(params, dict):
        value = params.get(source)
        if isinstance(value, str) and value:
            headers[ms.HEADER_NAME] = value
    return ms.handle(
        _rpc_request(method, params),
        headers,
        tools=tools,
        tool_specs=specs,
        expected_manifest_hash=ms.tool_manifest_digest(specs),
        **kwargs,
    )


def test_stateless_header_codec_edges():
    with pytest.raises(ValueError, match="malformed Base64 sentinel"):
        ms.decode_header_value(ms.SENTINEL_PREFIX + "AAAA")
    with pytest.raises(ValueError, match="invalid Base64 sentinel"):
        ms.decode_header_value(ms.SENTINEL_PREFIX + "!!!" + ms.SENTINEL_SUFFIX)
    with pytest.raises(ValueError, match="invalid UTF-8"):
        ms.decode_header_value(ms.SENTINEL_PREFIX + "/w==" + ms.SENTINEL_SUFFIX)
    with pytest.raises(ValueError, match="unsafe header value"):
        ms.decode_header_value("bad\nvalue")
    assert ms.encode_header_value("bad\nvalue").startswith(ms.SENTINEL_PREFIX)
    with pytest.raises(ValueError, match="header value must be a string"):
        ms.encode_header_value(1)
    with pytest.raises(ValueError, match="only scalar"):
        ms._scalar_header([])
    assert ms._header_values_match("abc", 1.5) is False
    with pytest.raises(ms.HeaderMismatchError, match="Conflicting"):
        ms._headers({"Mcp-Method": "a", "mcp-method": "b"})


def test_stateless_request_validation_edges():
    headers = {ms.HEADER_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION, ms.HEADER_METHOD: "ping"}
    bad_requests = (
        None,
        {"jsonrpc": "1.0", "method": "ping"},
        {"jsonrpc": "2.0", "method": ""},
        {"jsonrpc": "2.0", "method": "ping", "params": []},
    )
    for request in bad_requests:
        with pytest.raises(ms.MCPError):
            ms.validate_request(request, headers)
    with pytest.raises(ms.HeaderMismatchError, match="MCP-Protocol-Version"):
        ms.validate_request({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": None}, headers)


def test_stateless_dispatch_failure_paths():
    _, body = _rpc_handle("unknown-method")
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND

    _, body = _rpc_handle("server/discover", instructions="Local note")
    assert body["result"]["instructions"] == "Local note"

    _, body = _rpc_handle("prompts/get", {"name": "p"}, prompts=object())
    assert body["error"]["code"] == ms.INTERNAL_ERROR

    spec = ms.ToolSpec(name="boom")

    def _tool_mcp_error(_args):
        raise ms.MCPError(-32000, "tool denied")

    def _tool_runtime_error(_args):
        raise RuntimeError("tool boom")

    _, body = _rpc_handle(
        "tools/call",
        {"name": "boom", "arguments": []},
        tools={"boom": _tool_runtime_error},
        specs=(spec,),
    )
    assert body["error"]["code"] == ms.INVALID_PARAMS
    _, body = _rpc_handle(
        "tools/call",
        {"name": "boom", "arguments": {}},
        tools={"boom": _tool_mcp_error},
        specs=(spec,),
    )
    assert body["error"]["code"] == -32000
    _, body = _rpc_handle(
        "tools/call",
        {"name": "boom", "arguments": {}},
        tools={"boom": _tool_runtime_error},
        specs=(spec,),
    )
    assert body["result"]["isError"] is True

    uri = "res://edge"
    resource_spec = ms.ResourceSpec(uri=uri)

    def _resource_mcp_error(_args):
        raise ms.MCPError(-32001, "resource denied")

    def _resource_runtime_error(_args):
        raise RuntimeError("resource boom")

    _, body = _rpc_handle(
        "resources/read",
        {"uri": uri},
        resources={uri: _resource_mcp_error},
        resource_specs=(resource_spec,),
    )
    assert body["error"]["code"] == -32001
    _, body = _rpc_handle(
        "resources/read",
        {"uri": uri},
        resources={uri: _resource_runtime_error},
        resource_specs=(resource_spec,),
    )
    assert body["error"]["code"] == ms.INTERNAL_ERROR


def test_stateless_skill_policy_and_approval_failure_paths():
    def _skill_policy(name, args):
        if name == "blocked":
            raise ms.MCPError(ms.POLICY_DENIED, "blocked")
        if name == "broken":
            raise RuntimeError("broken policy")
        return dict(args)

    handlers = {
        "skills/list": lambda _: {
            "skills": [{"name": "good"}, {"name": "blocked"}, {"name": "broken"}, {"oops": 1}]
        }
    }
    _, body = _rpc_handle("skills/list", skill_handlers=handlers, skill_policy=_skill_policy)
    assert [item["name"] for item in body["result"]["skills"]] == ["good"]

    def _broken_policy(_action, _args):
        raise RuntimeError("bad policy")

    _, body = _rpc_handle("skills/get", {"name": "broken"}, skill_policy=_broken_policy)
    assert body["error"]["code"] == ms.POLICY_DENIED
    _, body = _rpc_handle("skills/get", {"name": "unknown"})
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND

    def _broken_get(_args):
        raise RuntimeError("handler boom")

    _, body = _rpc_handle("skills/get", {"name": "x"}, skill_handlers={"skills/get": _broken_get})
    assert body["error"]["code"] == ms.INTERNAL_ERROR

    def _allow_policy(_action, args):
        return dict(args)

    def _mcp_approval(_args):
        raise ms.MCPError(-32002, "approval denied")

    def _broken_approval(_args):
        raise RuntimeError("approval boom")

    def _dummy_approval(_args):
        return {"approved": True}

    _, body = _rpc_handle(
        "skills/approve", {"name": "x"}, skill_approval_handlers={"skills/approve": _dummy_approval}
    )
    assert body["error"]["code"] == ms.POLICY_DENIED
    _, body = _rpc_handle(
        "skills/approve",
        {"name": "x"},
        approval_policy=_broken_policy,
        skill_approval_handlers={"skills/approve": _dummy_approval},
    )
    assert body["error"]["code"] == ms.POLICY_DENIED
    _, body = _rpc_handle("skills/approve", {"name": "x"}, approval_policy=_allow_policy)
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND
    _, body = _rpc_handle(
        "skills/approve",
        {"name": "x"},
        approval_policy=_allow_policy,
        skill_approval_handlers={"skills/approve": _mcp_approval},
    )
    assert body["error"]["code"] == -32002
    _, body = _rpc_handle(
        "skills/approve",
        {"name": "x"},
        approval_policy=_allow_policy,
        skill_approval_handlers={"skills/approve": _broken_approval},
    )
    assert body["error"]["code"] == ms.POLICY_DENIED


def test_stateless_task_and_persona_failure_paths():
    def _broken_task(_args):
        raise RuntimeError("task boom")

    _, body = _rpc_handle("tasks/get", {"task_id": "t1"}, task_handlers={"tasks/get": _broken_task})
    assert body["error"]["code"] == ms.TASK_DENIED

    _, body = _rpc_handle("persona/simulate", {"scenario": "safe_chain"})
    assert body["error"]["code"] == ms.METHOD_NOT_FOUND

    def _mcp_persona(_args):
        raise ms.MCPError(-32003, "persona denied")

    def _broken_persona(_args):
        raise RuntimeError("persona boom")

    _, body = _rpc_handle(
        "persona/simulate", {"scenario": "safe_chain"}, persona_handler=_mcp_persona
    )
    assert body["error"]["code"] == -32003
    _, body = _rpc_handle(
        "persona/simulate", {"scenario": "safe_chain"}, persona_handler=_broken_persona
    )
    assert body["error"]["code"] == ms.INVALID_PARAMS
