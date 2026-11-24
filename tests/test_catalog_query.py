"""Tests for capability/task-profile catalog queries."""

from __future__ import annotations

import json

import pytest

from mcp_hub.catalog_query import (
    DEFAULT_PROFILES_DIR,
    DEFAULT_REGISTRY,
    load_profiles,
    load_registry,
    query_catalog,
)


def test_bundled_resources_are_available() -> None:
    assert DEFAULT_REGISTRY.is_file()
    assert DEFAULT_PROFILES_DIR.is_dir()
    assert set(load_profiles()) == {
        "ai-agent-builder",
        "automation-ops",
        "code-review",
        "docs-localization",
        "quality-security-audit",
    }
    assert len(load_registry()["mcpServers"]) == 20


def test_filter_by_profile() -> None:
    result = query_catalog(profile="code-review", env={})
    assert result["count"] > 0
    assert all("code-review" in row["task_profiles"] for row in result["results"])
    assert all(row["kind"] == "tool" or row["kind"] == "server" for row in result["results"])


def test_filter_by_tag_category_and_kind() -> None:
    result = query_catalog(kind="tool", tag="rag-web-search", category="search", env={}, limit=200)
    assert result["count"] > 0
    assert all(row["kind"] == "tool" for row in result["results"])
    assert all(row["category"] == "search" for row in result["results"])
    assert all("rag-web-search" in row["tags"] for row in result["results"])


def test_access_mode_filter() -> None:
    result = query_catalog(kind="tool", access_mode="read-write", env={}, limit=200)
    assert result["count"] > 0
    assert all(row["access_mode"] == "read-write" for row in result["results"])


def test_ready_only_respects_required_env() -> None:
    all_rows = query_catalog(kind="tool", category="code", env={}, limit=200, registry_path=None)[
        "results"
    ]
    github = [row for row in all_rows if row["server_id"] == "github"]
    assert github and not any(row["ready"] for row in github)

    missing = query_catalog(
        kind="tool", category="code", tag="execution", ready_only=True, env={}, limit=200
    )["results"]
    assert not any(row["server_id"] == "github" for row in missing)

    ready = query_catalog(
        kind="tool",
        category="code",
        ready_only=True,
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": "t"},
        limit=200,
    )["results"]
    assert any(row["server_id"] == "github" for row in ready)
    assert not any(row["status"] == "fixture" for row in ready)


def test_invalid_filters_raise() -> None:
    with pytest.raises(ValueError, match="kind"):
        query_catalog(kind="widget")
    with pytest.raises(ValueError, match="access_mode"):
        query_catalog(access_mode="admin")


def test_cli_query_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    from mcp_hub.cli import main

    rc = main(
        [
            "query-catalog",
            "--kind",
            "tool",
            "--profile",
            "automation-ops",
            "--ready-only",
            "--limit",
            "3",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["count"] <= 3
    assert all("automation-ops" in row["task_profiles"] for row in payload["results"])
    assert all(row["ready"] for row in payload["results"])
