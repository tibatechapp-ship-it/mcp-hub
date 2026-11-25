"""Phase 7: Arabic DSL over the unified MCP gateway."""

from __future__ import annotations

import html
import json
import sqlite3

import pytest

from mcp_hub import apps as mcp_apps
from mcp_hub import arabic_dsl as dsl
from mcp_hub.catalog_tools import (
    PINNED_TOOL_MANIFEST_SHA256,
    RESOURCE_SPECS,
    TOOL_SPECS,
)
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main
from mcp_hub.database import Catalog
from mcp_hub.mcp_stateless import POLICY_DENIED


def _catalog(tmp_path) -> Catalog:
    return Catalog(str(tmp_path / "catalog.sqlite3"))


def _audit(tmp_path):
    with sqlite3.connect(str(tmp_path / "catalog.sqlite3")) as con:
        return con.execute(
            "SELECT step, method, raw_text, params_json, result_json, status, exit_code "
            "FROM arabic_dsl_audit ORDER BY id"
        ).fetchall()


def test_context_keeps_arabic_actor_but_rejects_indic_digits() -> None:
    ctx = dsl.DslContext(actor="المراجع", tenant_id="acme", session_id="s1", lang="ar")
    assert ctx.actor == "المراجع"
    assert ctx.lang == "ar"

    with pytest.raises(dsl.DslError) as exc:
        dsl.DslContext(actor="ر١")
    assert exc.value.code == EXIT_BAD_INPUT


@pytest.mark.parametrize(
    "kwargs",
    [
        {"actor": ""},
        {"tenant_id": "t" * 129},
        {"session_id": "s\nx"},
    ],
)
def test_context_validates_scope_fields(kwargs) -> None:
    with pytest.raises(dsl.DslError) as exc:
        dsl.DslContext(**kwargs)
    assert exc.value.code == EXIT_BAD_INPUT


def test_parse_covers_every_command_family() -> None:
    ctx = dsl.DslContext(actor="المراجع", lang="ar")
    text = (
        "اكتشف الخادم؛ اختبار الاتصال؛ اعرض الكتالوج؛ "
        "ابحث عن أداة البحث؛ تفاصيل الأداة البحث؛ تفاصيل الخادم 12؛ "
        "اعرض الفئات؛ اعرض سجل التدقيق 12؛ شغّل الفحوصات الصحية؛ "
        "اعرض بيانات السياسة؛ وجّه الطلب نموذج؛ "
        "اعرض الموارد؛ اعرض الإحصاءات؛ اعرض المحفزات؛ اعرض الموجه demo؛ "
        "اعرض المهارات؛ تفاصيل المهارة demo؛ اعتمد المهارة demo؛ "
        "الغِ اعتماد المهارة demo؛ "
        "اعرض المهام؛ حالة المهمة t-1؛ حدّث المهمة t-1 إلى done؛ "
        "الغِ المهمة t-1؛ وافق على المهمة t-1؛ "
        "حاكي الشخصية safe_chain؛ "
        "اعرض واجهة المراجعة؛ اعرض واجهة الكتالوج"
    )
    commands = dsl.parse(text, ctx)
    methods = [command.method for command in commands]
    assert methods == [
        "server/discover",
        "ping",
        "tools/list",
        "tools/call",
        "tools/call",
        "tools/call",
        "tools/call",
        "tools/call",
        "tools/call",
        "tools/call",
        "tools/call",
        "resources/list",
        "resources/read",
        "prompts/list",
        "prompts/get",
        "skills/list",
        "skills/get",
        "skills/approve",
        "skills/revoke",
        "tasks/list",
        "tasks/get",
        "tasks/update",
        "tasks/cancel",
        "tasks/approve",
        "persona/simulate",
        "resources/read",
        "resources/read",
    ]
    assert commands[0].step == 1
    assert commands[-1].step == len(commands)
    assert commands[3].params["arguments"]["query"] == "البحث"
    assert commands[4].params["name"] == "search_servers"
    assert commands[5].params["arguments"]["server_id"] == 12
    assert commands[10].params["arguments"]["task"] == "نموذج"
    assert commands[17].params["actor"] == "المراجع"
    assert commands[18].params["actor"] == "المراجع"
    assert commands[20].params["task_id"] == "t-1"
    assert commands[21].params["status"] == "done"
    assert commands[24].params["scenario"] == "safe_chain"
    assert commands[25].params["uri"] == "ui://mcp-hub/approval-review"
    assert commands[25].read_only_ui is True
    assert commands[26].params["uri"] == "ui://mcp-hub/tool-catalog"
    assert commands[26].params["lang"] == "ar"
    assert all(command.raw for command in commands)
    assert dsl.plan(commands)[25]["read_only_ui"] is True


def test_parse_is_diacritic_and_alias_tolerant() -> None:
    assert dsl.parse("اِبْحَثْ عَنْ أَدَاةٍ البحث", dsl.DslContext())[0].method == "tools/call"
    assert (
        dsl.parse("Search catalog: search", dsl.DslContext())[0].params["arguments"]["query"]
        == "search"
    )
    assert dsl.parse("اعرض الأدوات", dsl.DslContext())[0].method == "tools/list"


def test_parse_rejects_unknown_and_empty_commands() -> None:
    with pytest.raises(dsl.DslError) as exc:
        dsl.parse("أمر غير معروف")
    assert exc.value.code == EXIT_NOT_FOUND
    with pytest.raises(dsl.DslError) as exc:
        dsl.parse("  \n؛\n  ")
    assert exc.value.code == EXIT_NOT_FOUND


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("ابحث عن أداة", "query is required"),
        ("تفاصيل الخادم ١٢", "invalid server_id"),
        ("حالة المهمة t١", "invalid task_id"),
        ("حدّث المهمة t-1 إلى bad-status!", "invalid status"),
        ("حاكي الشخصية nope", "invalid persona scenario"),
        ("تفاصيل المهارة demo extra", "invalid name"),
    ],
)
def test_parse_validation_errors_are_stable(text: str, fragment: str) -> None:
    with pytest.raises(dsl.DslError) as exc:
        dsl.parse(text)
    assert fragment in str(exc.value)
    assert exc.value.code == EXIT_BAD_INPUT


def test_diacritic_only_query_is_a_runtime_error() -> None:
    with pytest.raises(dsl.DslError) as exc:
        dsl.parse("ابحث عن أداة َ")
    assert "query is required" in str(exc.value)
    assert exc.value.code == EXIT_NOT_FOUND


def test_status_update_defaults_to_in_progress() -> None:
    command = dsl.parse("حدّث المهمة t-1", dsl.DslContext())[0]
    assert command.params["status"] == "in_progress"


def test_language_aliases_are_accepted() -> None:
    ctx = dsl.DslContext(lang="ar")
    assert dsl.parse("List tools", ctx)[0].method == "tools/list"
    assert dsl.parse("Ping", ctx)[0].method == "ping"
    assert dsl.parse("Revoke skill demo", ctx)[0].method == "skills/revoke"


def test_dry_run_cli_records_original_arabic_text(tmp_path, capsys, monkeypatch) -> None:
    def _must_not_run(*args, **kwargs):
        raise AssertionError("dry-run must not call MCP handlers")

    monkeypatch.setattr(dsl.ms, "handle", _must_not_run)
    db = str(tmp_path / "catalog.sqlite3")
    Catalog(db)
    code = main(
        [
            "--db",
            db,
            "arabic-dsl",
            "--text",
            "ابحث عن أداة البحث؛ وافق على المهمة t-1",
            "--dry-run",
            "--tenant",
            "acme",
            "--session",
            "s1",
            "--actor",
            "المراجع",
            "--json",
            "--lang",
            "ar",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["stopped_at"] is None
    assert [step["method"] for step in payload["steps"]] == ["tools/call", "tasks/approve"]
    assert payload["steps"][1]["approval_required"] is True
    assert payload["steps"][1]["params"]["actor"] == "المراجع"

    rows = _audit(tmp_path)
    assert len(rows) == 2
    assert rows[0][2] == "ابحث عن أداة البحث"
    assert rows[1][2] == "وافق على المهمة t-1"
    assert all(row[5] == "dry_run" and row[6] == EXIT_OK for row in rows)


def test_cli_reads_pipeline_file(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    Catalog(db)
    path = tmp_path / "pipeline.txt"
    path.write_text("اعرض الكتالوج\nاعرض المهارات", encoding="utf-8")
    assert (
        main(
            [
                "--db",
                db,
                "arabic-dsl",
                "--file",
                str(path),
                "--dry-run",
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert [step["method"] for step in payload["steps"]] == ["tools/list", "skills/list"]


def test_cli_reports_unknown_command_with_arabic_error(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    Catalog(db)
    assert (
        main(
            [
                "--db",
                db,
                "arabic-dsl",
                "--text",
                "أمر غير معروف",
                "--json",
                "--lang",
                "ar",
            ]
        )
        == EXIT_NOT_FOUND
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "arabic-dsl"
    assert failed["exit_code"] == EXIT_NOT_FOUND
    assert failed["message_ar"]


def test_cli_rejects_indic_tenant(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    Catalog(db)
    assert (
        main(
            [
                "--db",
                db,
                "arabic-dsl",
                "--text",
                "اعرض المهام",
                "--tenant",
                "م١",
                "--json",
            ]
        )
        == EXIT_BAD_INPUT
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["exit_code"] == EXIT_BAD_INPUT


def test_execute_runs_through_gateway_with_name_headers(tmp_path, monkeypatch) -> None:
    seen = []

    def _handle(request, headers, **runtime):
        seen.append((request, dict(headers), runtime))
        return 200, {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}

    monkeypatch.setattr(dsl.ms, "handle", _handle)
    catalog = _catalog(tmp_path)
    commands = dsl.parse("اعتمد المهارة demo؛ اعرض الكتالوج", dsl.DslContext(actor="المراجع"))
    payload = dsl.execute(commands, catalog)
    assert payload["exit_code"] == EXIT_OK
    assert payload["status"] == "ok"
    assert [step["status"] for step in payload["steps"]] == ["ok", "ok"]
    assert seen[0][1]["Mcp-Method"] == "skills/approve"
    assert seen[0][1]["Mcp-Name"] == "demo"
    assert seen[0][0]["params"]["_meta"][dsl.ms.META_PROTOCOL_VERSION] == (
        dsl.ms.MCP_PROTOCOL_VERSION
    )
    assert "tool_specs" in seen[0][2]
    assert seen[0][2]["expected_manifest_hash"] == PINNED_TOOL_MANIFEST_SHA256
    assert _audit(tmp_path)[0][3] == json.dumps(
        {"name": "demo", "actor": "المراجع"}, ensure_ascii=False, sort_keys=True
    )


def test_execute_fails_fast_on_policy_denial(tmp_path, monkeypatch) -> None:
    calls = []

    def _handle(request, headers, **runtime):
        calls.append(request["method"])
        if request["method"] == "ping":
            return 200, {"jsonrpc": "2.0", "id": request["id"], "result": {"pong": True}}
        return 403, {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": POLICY_DENIED, "message": "blocked"},
        }

    monkeypatch.setattr(dsl.ms, "handle", _handle)
    commands = dsl.parse("اختبار الاتصال؛ اعرض المهارات؛ اعرض الكتالوج")
    payload = dsl.execute(commands, _catalog(tmp_path))
    assert calls == ["ping", "skills/list"]
    assert payload["status"] == "failed"
    assert payload["exit_code"] == EXIT_BAD_INPUT
    assert payload["stopped_at"] == 2
    assert [step["status"] for step in payload["steps"]] == ["ok", "failed"]
    rows = _audit(tmp_path)
    assert [row[5] for row in rows] == ["ok", "failed"]
    assert rows[1][6] == EXIT_BAD_INPUT


def test_execute_maps_other_errors_to_runtime_exit(tmp_path, monkeypatch) -> None:
    def _handle(request, headers, **runtime):
        return 400, {
            "jsonrpc": "2.0",
            "id": request["id"],
            "error": {"code": -32601, "message": "unknown method"},
        }

    monkeypatch.setattr(dsl.ms, "handle", _handle)
    payload = dsl.execute(dsl.parse("ping"), _catalog(tmp_path))
    assert payload["exit_code"] == EXIT_NOT_FOUND
    assert payload["steps"][0]["error"]["code"] == -32601


def test_execute_reads_apps_ui_as_read_only(tmp_path) -> None:
    payload = dsl.execute(
        dsl.parse("اعرض واجهة المراجعة", dsl.DslContext(lang="ar")), _catalog(tmp_path)
    )
    assert payload["exit_code"] == EXIT_OK
    result = payload["steps"][0]["result"]
    page = html.unescape(result["contents"][0]["text"])
    assert "<button" not in page
    assert "لا أوامر تنفيذ في هذه الواجهة" in page
    assert "method: 'skills/approve'" not in page
    fallback = json.loads(result["contents"][1]["text"])
    assert fallback["snapshot"]["read_only_ui"] is True


def test_build_runtime_honors_apps_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MCP_HUB_APPS_ENABLED", "0")
    runtime = dsl.build_runtime(_catalog(tmp_path))
    assert mcp_apps.APPROVAL_URI not in runtime["resources"]
    monkeypatch.setenv("MCP_HUB_APPS_ENABLED", "1")
    assert mcp_apps.APPROVAL_URI in dsl.build_runtime(_catalog(tmp_path))["resources"]


def test_pinned_contract_is_unchanged() -> None:
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
