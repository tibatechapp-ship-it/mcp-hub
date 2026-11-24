"""CLI contract tests for Phase 5 approval commands (--json and exit codes)."""

from __future__ import annotations

import json

from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main
from mcp_hub.database import Catalog
from mcp_hub.tasks import TaskStore


def test_skill_approval_cli_json_contract(tmp_path, capsys):
    db = str(tmp_path / "catalog.sqlite3")

    assert main(["--db", db, "skill-list", "--json"]) == EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert any(item["name"] == "mcp-hub-catalog" for item in listed)

    assert (
        main(
            [
                "--db",
                db,
                "skill-approve",
                "mcp-hub-catalog",
                "--actor",
                "reviewer",
                "--json",
            ]
        )
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert '"approved":true' in out
    approved = json.loads(out)
    assert approved["name"] == "mcp-hub-catalog"
    assert approved["sha256"]

    assert main(["--db", db, "skill-approve", "missing-skill", "--json"]) == EXIT_NOT_FOUND
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "skill-approve"
    assert failed["status"] == "failed"
    assert failed["exit_code"] == EXIT_NOT_FOUND

    secret_shaped_actor = "ghp" + "_" + "A" * 36
    assert (
        main(
            [
                "--db",
                db,
                "skill-approve",
                "mcp-hub-catalog",
                "--actor",
                secret_shaped_actor,
                "--json",
            ]
        )
        == EXIT_BAD_INPUT
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["exit_code"] == EXIT_BAD_INPUT
    assert "secret-shaped" in failed["error"]

    assert main(["--db", db, "skill-revoke", "mcp-hub-catalog", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "name": "mcp-hub-catalog",
        "approved": False,
        "removed": True,
    }

    assert main(["--db", db, "skill-revoke", "mcp-hub-catalog", "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["removed"] is False


def test_task_approval_cli_json_contract(tmp_path, capsys):
    db = str(tmp_path / "catalog.sqlite3")
    store = TaskStore(Catalog(db))
    task = store.create("acme", "s1", "tool_chain")
    store.update(task["task_id"], "acme", "s1", status="awaiting_approval")

    assert (
        main(
            [
                "--db",
                db,
                "task-list",
                "--tenant",
                "acme",
                "--session",
                "s1",
                "--json",
            ]
        )
        == EXIT_OK
    )
    listed = json.loads(capsys.readouterr().out)
    assert any(item["task_id"] == task["task_id"] for item in listed)

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
                "--reason",
                "checked",
                "--json",
            ]
        )
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert '"status":"queued"' in out
    assert json.loads(out)["status"] == "queued"

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
                "--json",
            ]
        )
        == EXIT_BAD_INPUT
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "task-approve"
    assert failed["exit_code"] == EXIT_BAD_INPUT
    assert "not awaiting approval" in failed["error"]

    assert (
        main(
            [
                "--db",
                db,
                "task-approve",
                "missing-task",
                "--tenant",
                "acme",
                "--session",
                "s1",
                "--json",
            ]
        )
        == EXIT_NOT_FOUND
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["exit_code"] == EXIT_NOT_FOUND
    assert "not found" in failed["error"]

    assert (
        main(["--db", db, "task-list", "--tenant", "acme", "--limit", "0", "--json"])
        == EXIT_BAD_INPUT
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "task-list"
    assert failed["exit_code"] == EXIT_BAD_INPUT
    assert "limit" in failed["error"]
