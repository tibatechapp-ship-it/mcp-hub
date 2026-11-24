"""Phase 6 Arabic full: shared matcher, preserved identifiers, stable diagnostics."""

from __future__ import annotations

import html as html_mod
import json

from mcp_hub import apps as mcp_apps
from mcp_hub import catalog_query, semantic_router
from mcp_hub.arabic_runtime import (
    arabic_diagnostic,
    arabic_error_fields,
    normalize_arabic_text,
    normalize_for_match,
    normalize_query_text,
)
from mcp_hub.catalog_tools import (
    PINNED_TOOL_MANIFEST_SHA256,
    RESOURCE_SPECS,
    TOOL_SPECS,
)
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main
from mcp_hub.database import Catalog
from mcp_hub.mcp_stateless import POLICY_DENIED, TASK_DENIED
from mcp_hub.tasks import TaskStore


class _Failure(Exception):
    def __init__(self, message: str, *, code: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if reason is not None:
            self.reason = reason


def test_shared_matcher_unifies_arabic_folding() -> None:
    assert normalize_for_match("الـمَشْرُوعَات") == "المشروعات"
    assert normalize_for_match("الـمَشْرُوعَات", strip_al=True) == "مشروعات"
    assert normalize_for_match("مَشْرُوعَة") == "مشروعه"
    assert normalize_for_match("١٢٣ ۴۵۶") == "123 456"
    assert normalize_query_text("الـمَشْرُوعَات") == "المشروعات"
    # Display normalization deliberately keeps harakat; matching removes them.
    assert normalize_arabic_text("مَشْرُوعَة") == "مَشْرُوعَة"
    assert "مشروعات" in semantic_router.tokenize("الـمَشْرُوعَات")


def test_catalog_filters_use_shared_matcher_without_rewriting_rows(tmp_path) -> None:
    registry = {
        "mcpServers": {
            "demo": {
                "name": "Demo",
                "name_ar": "عرض",
                "status": "ok",
                "transport": "stdio",
                "category": "الـفِئَة",
                "category_ar": "الفئه",
                "tools": [
                    {
                        "name": "demo_tool",
                        "name_ar": "اداة",
                        "description": "Local fixture tool",
                        "tags": ["تَاغ"],
                        "task_profiles": ["p١"],
                    }
                ],
            }
        }
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    result = catalog_query.query_catalog(
        registry_path=path,
        category="الـفِئَة",
        tag="الـتَّاغ",
        profile="p١",
        limit=10,
    )
    assert result["count"] >= 1
    row = next(item for item in result["results"] if item["kind"] == "tool")
    # Query filters are folded; stored metadata is returned verbatim.
    assert row["category"] == "الـفِئَة"
    assert row["tags"] == ["تَاغ"]
    assert row["task_profiles"] == ["p١"]


def test_cli_keeps_audit_text_and_rejects_indic_identifiers(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    store = TaskStore(Catalog(db))
    task = store.create("acme", "s1", "tool_chain")
    store.update(task["task_id"], "acme", "s1", status="awaiting_approval")

    actor = "المراجع"
    reason = "وافق ١٢٣ على المهارة"
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
                actor,
                "--reason",
                reason,
                "--json",
                "--lang",
                "ar",
            ]
        )
        == EXIT_OK
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "queued"

    events = store.get(task["task_id"], "acme", "s1")["events"]
    detail = json.loads(next(item for item in events if item["event"] == "approved")["detail"])
    assert detail == {"actor": actor, "reason": reason}

    assert (
        main(
            [
                "--db",
                db,
                "task-approve",
                "t١",
                "--tenant",
                "acme",
                "--session",
                "s1",
                "--json",
                "--lang",
                "ar",
            ]
        )
        == EXIT_BAD_INPUT
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "task-approve"
    assert failed["status"] == "failed"
    assert "task_id" in failed["error"]
    assert failed["message_ar"]


def test_arabic_diagnostic_covers_security_failures() -> None:
    cases = (
        (
            _Failure("blocked", code=POLICY_DENIED, reason="policy denies this tool"),
            EXIT_BAD_INPUT,
            "رفضت السياسة العملية",
        ),
        (
            _Failure("outside scope", code=TASK_DENIED, reason="task is outside tenant scope"),
            EXIT_BAD_INPUT,
            "رفضت المهمة",
        ),
        (
            _Failure("mismatch", reason="sha256 fingerprint mismatch"),
            EXIT_BAD_INPUT,
            "عدم تطابق البصمة",
        ),
        (
            _Failure("blocked", reason="permission denied for actor"),
            EXIT_BAD_INPUT,
            "الصلاحية غير متاحة",
        ),
    )
    for exc, code, expected in cases:
        assert arabic_diagnostic(exc, code).startswith(expected)
        fields = arabic_error_fields("task-approve", exc, code, "ar")
        assert set(fields) == {"command", "status", "error", "exit_code", "message_ar"}
        assert fields["message_ar"].startswith(expected)
        assert "message_ar" not in arabic_error_fields("task-approve", exc, code, "en")


def test_skill_not_found_keeps_stable_arabic_shape(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    assert main(["--db", db, "skill-approve", "missing", "--json", "--lang", "ar"]) == (
        EXIT_NOT_FOUND
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["exit_code"] == EXIT_NOT_FOUND
    assert failed["message_ar"].startswith("غير موجود")


def test_pinned_catalog_contract_is_unchanged() -> None:
    assert PINNED_TOOL_MANIFEST_SHA256 == (
        "6009656f1d4cc30fb9f079b8b6f2bdec01a04abfefc4099462f46998804d2857"
    )
    assert {spec.name for spec in TOOL_SPECS} == {
        "search_servers",
        "get_server",
        "list_categories",
        "servers_by_category",
        "provenance_conflicts",
        "provenance_events",
        "doctor_report",
        "suggest_category",
        "query_catalog",
        "route_task",
        "description_smell_audit",
        "policy_snapshot",
    }
    assert [spec.uri for spec in RESOURCE_SPECS] == ["mcp-hub://stats"]


def test_apps_ui_never_emits_write_controls(tmp_path) -> None:
    runtime = mcp_apps.build_runtime(Catalog(str(tmp_path / "catalog.sqlite3")))
    payload = runtime.resources[mcp_apps.APPROVAL_URI]({"lang": "ar"})
    page = payload["contents"][0]["text"]

    unescaped = html_mod.unescape(page)
    assert "<button" not in unescaped
    assert "method: 'skills/approve'" not in unescaped
    assert "method: 'tasks/approve'" not in unescaped
    assert "method: 'skills/list'" in unescaped
    assert "لا أوامر تنفيذ في هذه الواجهة" in unescaped
    assert "'sha256-" in unescaped

    fallback = json.loads(payload["contents"][1]["text"])
    assert fallback["snapshot"]["read_only_ui"] is True
    assert {item["method"] for item in fallback["snapshot"]["available_actions"]} == {
        "skills/approve",
        "skills/revoke",
        "tasks/approve",
        "tasks/list",
    }
