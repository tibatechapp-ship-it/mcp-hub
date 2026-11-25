"""Phase 8: Arabic .arflow playbooks over the unified MCP gateway."""

from __future__ import annotations

import json
import sqlite3

import pytest

from mcp_hub import arabic_dsl, arflow
from mcp_hub.catalog_tools import PINNED_TOOL_MANIFEST_SHA256, RESOURCE_SPECS, TOOL_SPECS
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main
from mcp_hub.database import Catalog
from mcp_hub.mcp_stateless import POLICY_DENIED


def _catalog(tmp_path) -> Catalog:
    return Catalog(str(tmp_path / "catalog.sqlite3"))


def _playbook(path, text: str) -> str:
    target = path / "flow.arflow"
    target.write_text(text, encoding="utf-8")
    return str(target)


def _audit(path):
    with sqlite3.connect(str(path / "catalog.sqlite3")) as con:
        return con.execute(
            "SELECT playbook, raw_text, variables_json, plan_json, steps_json, "
            "status, exit_code, tenant_id, session_id, actor, lang "
            "FROM arflow_audit ORDER BY id"
        ).fetchall()


def _dsl_audit(path):
    with sqlite3.connect(str(path / "catalog.sqlite3")) as con:
        return con.execute("SELECT step, raw_text FROM arabic_dsl_audit ORDER BY id").fetchall()


# --- parser / comments / definitions -------------------------------------------------


def test_comments_blank_lines_and_variable_definitions() -> None:
    text = (
        '# تعليق عربي\n// another comment\n\nليكن الهدف = "البحث"\nset limit = 5\nاعرض الكتالوج\n'
    )
    compiled = arflow.build(text)
    assert compiled.ok
    assert compiled.playbook is not None
    assert compiled.playbook.variables == {"الهدف": "البحث", "limit": "5"}
    assert len(compiled.playbook.steps) == 1
    assert compiled.playbook.steps[0].line == 6
    assert compiled.playbook.steps[0].step == 1


def test_single_quotes_and_plain_values() -> None:
    compiled = arflow.build("عَرِّف s = 'نص البحث'\nset t = plain\nابحث عن أداة ${s}\n")
    assert compiled.ok
    assert compiled.playbook is not None
    assert compiled.playbook.variables == {"s": "نص البحث", "t": "plain"}
    assert compiled.playbook.steps[0].command.params["arguments"]["query"] == "نص البحث"


def test_variable_reused_twice_in_query() -> None:
    compiled = arflow.build("ليكن q = البحث\nابحث عن أداة ${q}\nتفاصيل الأداة ${q}\n")
    assert compiled.ok
    assert compiled.playbook is not None
    steps = compiled.playbook.steps
    assert steps[0].command.params["arguments"]["query"] == "البحث"
    assert steps[1].command.params["arguments"]["query"] == "البحث"
    assert steps[0].raw == "ابحث عن أداة البحث"
    assert steps[1].original == "تفاصيل الأداة ${q}"


def test_placeholder_with_surrounding_text() -> None:
    compiled = arflow.build("ليكن q = البحث\nابحث عن أداة ${q} في الكتالوج\n")
    assert compiled.playbook is not None
    assert compiled.playbook.steps[0].command.params["arguments"]["query"] == "البحث في الكتالوج"


def test_step_numbers_are_unique_for_audit() -> None:
    compiled = arflow.build("اعرض الكتالوج\nاعرض المهارات\nاعرض الفئات\n")
    steps = compiled.playbook.steps
    assert [step.command.step for step in steps] == [1, 2, 3]


def test_optional_markers_all_forms() -> None:
    text = "? اعرض الكتالوج\nاعرض المهارات ?\nاختياري اعرض الفئات\nاعرض الموارد optional\n"
    compiled = arflow.build(text)
    assert compiled.playbook is not None
    assert [step.optional for step in compiled.playbook.steps] == [True, True, True, True]
    assert [step.raw for step in compiled.playbook.steps] == [
        "اعرض الكتالوج",
        "اعرض المهارات",
        "اعرض الفئات",
        "اعرض الموارد",
    ]


def test_required_steps_are_not_optional() -> None:
    compiled = arflow.build("اعرض الكتالوج\n")
    assert compiled.playbook is not None
    assert compiled.playbook.steps[0].optional is False


def test_arabic_indic_digits_in_query_value_are_folded() -> None:
    compiled = arflow.build("ليكن q = بحث١٢٣\nابحث عن أداة ${q}\n")
    assert compiled.playbook is not None
    assert compiled.playbook.steps[0].command.params["arguments"]["query"] == "بحث123"


def test_arabic_context_is_kept_but_indic_ids_rejected() -> None:
    ctx = arabic_dsl.DslContext(actor="المراجع", tenant_id="acme", session_id="s1", lang="ar")
    compiled = arflow.build("اعرض الكتالوج\n", context=ctx)
    assert compiled.playbook is not None
    assert compiled.playbook.context.actor == "المراجع"
    with pytest.raises(arabic_dsl.DslError):
        arabic_dsl.DslContext(actor="ر١")


# --- check / issue codes -------------------------------------------------------------


def test_check_returns_plan_and_no_issues() -> None:
    report = arflow.check("اعرض الكتالوج\nاعرض المهارات\n")
    assert report["command"] == "arflow-check"
    assert report["status"] == "ok"
    assert report["issues"] == []
    assert [entry["method"] for entry in report["plan"]] == ["tools/list", "skills/list"]
    assert report["exit_code"] == EXIT_OK


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("لا يوجد أمر كهذا\n", "UNKNOWN_COMMAND"),
        ("ابحث عن أداة\n", "BAD_ARGUMENT"),
        ("ليكن x = a\nابحث عن أداة ${y}\n", "UNDEFINED_VARIABLE"),
        ("ليكن x = 1\nليكن x = 2\nاعرض الكتالوج\n", "DUPLICATE_DEFINITION"),
        ("ليكن ١x = 1\nاعرض الكتالوج\n", "INVALID_DEFINITION"),
        ("ليكن x = a;b\nاعرض الكتالوج\n", "INVALID_VARIABLE_VALUE"),
        ("ليكن x = t-1\nحالة المهمة ${x}\n", "SUBSTITUTION_OUTSIDE_QUERY"),
        ("ليكن x = 12\nتفاصيل الخادم ${x}\n", "SUBSTITUTION_OUTSIDE_QUERY"),
        ("ليكن x = demo\nتفاصيل المهارة ${x}\n", "SUBSTITUTION_OUTSIDE_QUERY"),
        ("ليكن x = a\nاعرض الكتالوج; ${x}\n", "BAD_ARGUMENT"),
        ("# فقط تعليق\n", "EMPTY_PLAYBOOK"),
        ("اعرض الكتالوج" * 200_000, "PLAYBOOK_TOO_LARGE"),
    ],
)
def test_check_reports_stable_issue_codes(text: str, code: str) -> None:
    report = arflow.check(text)
    assert report["status"] == "failed"
    assert report["plan"] == []
    assert report["exit_code"] == EXIT_BAD_INPUT
    assert report["issues"][0]["code"] == code
    assert set(arflow.ISSUE_CODES) >= {code}


def test_issue_as_dict_shape() -> None:
    issue = arflow.Issue(code="UNKNOWN_COMMAND", line=3, message="bad", raw="سطر")
    assert issue.as_dict() == {"code": "UNKNOWN_COMMAND", "line": 3, "message": "bad", "raw": "سطر"}


def test_override_validation() -> None:
    assert arflow.check("اعرض الكتالوج\n", overrides={"q": "abc"})["status"] == "ok"
    assert (
        arflow.check("اعرض الكتالوج\n", overrides={"bad-name": "x"})["issues"][0]["code"]
        == "INVALID_DEFINITION"
    )
    assert (
        arflow.check("اعرض الكتالوج\n", overrides={"x": "a\nb"})["issues"][0]["code"]
        == "INVALID_VARIABLE_VALUE"
    )


def test_override_wins_over_definition() -> None:
    compiled = arflow.build("ليكن q = أصلي\nابحث عن أداة ${q}\n", overrides={"q": "بديل"})
    assert compiled.playbook is not None
    assert compiled.playbook.steps[0].command.params["arguments"]["query"] == "بديل"


# --- run / audit / gateway -----------------------------------------------------------


def test_run_dry_run_writes_both_audits(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    path = tmp_path / "flow.arflow"
    text = "ليكن q = البحث\nاعرض الكتالوج\nابحث عن أداة ${q}\n"
    path.write_text(text, encoding="utf-8")
    compiled = arflow.build(text, name="flow", context=arabic_dsl.DslContext(lang="ar"))
    report = arflow.run(catalog, compiled, dry_run=True)
    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert [step["status"] for step in report["steps"]] == ["planned", "planned"]
    rows = _audit(tmp_path)
    assert len(rows) == 1
    assert rows[0][0] == "flow"
    assert rows[0][1] == text
    assert json.loads(rows[0][2]) == {"q": "البحث"}
    assert len(json.loads(rows[0][3])) == 2
    assert rows[0][5] == "ok"
    assert rows[0][10] == "ar"
    assert _dsl_audit(tmp_path)[0][1] == "اعرض الكتالوج"


def test_run_executes_and_records_tenant_scope(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    ctx = arabic_dsl.DslContext(tenant_id="t-1", session_id="s-1", actor="المراجع", lang="ar")
    compiled = arflow.build("اعرض الكتالوج\n", context=ctx)
    report = arflow.run(catalog, compiled)
    assert report["status"] == "ok"
    assert report["steps"][0]["http_status"] == 200
    row = _audit(tmp_path)[0]
    assert row[7:10] == ("t-1", "s-1", "المراجع")


def test_run_keeps_optional_failure_and_continues(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("? حالة المهمة t-missing\nاعرض الكتالوج\n")
    report = arflow.run(catalog, compiled)
    assert report["status"] == "ok"
    assert report["optional_failures"] == [1]
    assert [step["status"] for step in report["steps"]] == ["optional_failed", "ok"]


def test_run_fails_fast_on_required_failure(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("تفاصيل المهارة nope\nاعرض الكتالوج\n")
    report = arflow.run(catalog, compiled)
    assert report["status"] == "failed"
    assert report["exit_code"] == EXIT_NOT_FOUND
    assert report["stopped_at"] == 1
    assert len(report["steps"]) == 1
    assert report["steps"][0]["status"] == "failed"


def test_run_rejects_uncompiled_playbook(tmp_path) -> None:
    compiled = arflow.build("لا يوجد أمر\n")
    with pytest.raises(arflow.ArflowError):
        arflow.run(_catalog(tmp_path), compiled)


def test_run_preserves_read_only_ui_flag(tmp_path) -> None:
    compiled = arflow.build("اعرض واجهة المراجعة\n", context=arabic_dsl.DslContext(lang="ar"))
    report = arflow.run(_catalog(tmp_path), compiled, dry_run=True)
    assert report["steps"][0]["read_only_ui"] is True
    assert report["steps"][0]["params"]["lang"] == "ar"


def test_frozen_manifest_and_specs_unchanged() -> None:
    assert PINNED_TOOL_MANIFEST_SHA256
    assert TOOL_SPECS and RESOURCE_SPECS
    assert arflow.AUDIT_TABLE == "arflow_audit"
    assert arabic_dsl.AUDIT_TABLE == "arabic_dsl_audit"


def test_record_audit_is_idempotent_on_schema(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج\n")
    arflow.run(catalog, compiled, dry_run=True)
    arflow.run(catalog, compiled, dry_run=True)
    assert len(_audit(tmp_path)) == 2


# --- CLI -----------------------------------------------------------------------------


def test_cli_check_json(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج\n")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "check",
            "--file",
            path,
            "--json",
        ]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_cli_check_failure_json_on_stderr_with_arabic(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "ليكن x = t-1\nحالة المهمة ${x}\n")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "check",
            "--file",
            path,
            "--json",
            "--lang",
            "ar",
        ]
    )
    assert rc == EXIT_BAD_INPUT
    payload = json.loads(capsys.readouterr().err)
    assert payload["command"] == "arflow-check"
    assert payload["issues"][0]["code"] == "SUBSTITUTION_OUTSIDE_QUERY"
    assert payload["message_ar"]


def test_cli_run_dry_run_json(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "ليكن q = البحث\nابحث عن أداة ${q}\n")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "run",
            "--file",
            path,
            "--dry-run",
            "--json",
        ]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["steps"][0]["params"]["arguments"]["query"] == "البحث"


def test_cli_run_with_var_override(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "ابحث عن أداة ${q}\n")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "run",
            "--file",
            path,
            "--var",
            "q=بديل",
            "--dry-run",
            "--json",
        ]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["params"]["arguments"]["query"] == "بديل"


def test_cli_invalid_var(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج\n")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "run",
            "--file",
            path,
            "--var",
            "broken",
            "--json",
        ]
    )
    assert rc == EXIT_BAD_INPUT
    assert json.loads(capsys.readouterr().err)["command"] == "arflow"


def test_cli_missing_file(tmp_path, capsys) -> None:
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "run",
            "--file",
            str(tmp_path / "missing.arflow"),
            "--json",
        ]
    )
    assert rc == EXIT_NOT_FOUND
    assert json.loads(capsys.readouterr().err)["status"] == "failed"


def test_cli_pretty_output_default(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج\n")
    rc = main(["--db", str(tmp_path / "cli.sqlite3"), "arflow", "check", "--file", path])
    assert rc == EXIT_OK
    assert "\n" in capsys.readouterr().out


def test_cli_policy_denial_exit_code(tmp_path, capsys) -> None:
    """A real denied policy maps to exit 2, never a silent success."""
    path = _playbook(tmp_path, "اعتمد المهارة demo\n")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "run",
            "--file",
            path,
            "--json",
        ]
    )
    assert rc == EXIT_BAD_INPUT
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == EXIT_BAD_INPUT
    assert payload["steps"][0]["error"]["code"] == POLICY_DENIED


# --- defensive compiler edges ---------------------------------------------------------


def test_definition_without_keyword_is_a_command() -> None:
    report = arflow.check("x = 1\nاعرض الكتالوج\n")
    assert report["issues"][0]["code"] == "UNKNOWN_COMMAND"


def test_probe_failure_reports_non_query_substitution() -> None:
    compiled = arflow.build("ليكن x = safe_chain\nحاكي الشخصية ${x}\n")
    assert not compiled.ok
    assert compiled.issues[0].code == "SUBSTITUTION_OUTSIDE_QUERY"


def test_sentinel_absent_from_params_is_not_query() -> None:
    command = arabic_dsl.parse("اعرض الكتالوج", arabic_dsl.DslContext())[0]
    assert arflow._sentinel_in_query(command, arflow._sentinel(0)) is False


def test_marker_only_line_is_ignored() -> None:
    compiled = arflow.build("?\nاعرض الكتالوج\n")
    assert compiled.ok
    assert compiled.playbook is not None
    assert len(compiled.playbook.steps) == 1


def test_final_parse_failure_is_bad_argument(monkeypatch) -> None:
    original = arabic_dsl.parse

    def _parse(text: str, context=None):
        if "bad query" in text:
            raise arabic_dsl.DslError("invalid final text")
        return original(text, context)

    monkeypatch.setattr(arabic_dsl, "parse", _parse)
    compiled = arflow.build("ليكن q = bad query\nابحث عن أداة ${q}\n")
    assert not compiled.ok
    assert compiled.issues[0].code == "BAD_ARGUMENT"
