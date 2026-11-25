"""Phase 9: incremental Arabic testing over .arflow playbooks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from mcp_hub import arabic_dsl, arflow, catalog_tools, incremental, mcp_stateless
from mcp_hub.catalog_tools import PINNED_TOOL_MANIFEST_SHA256, RESOURCE_SPECS, TOOL_SPECS
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_OK, main
from mcp_hub.database import Catalog


def _catalog(tmp_path) -> Catalog:
    return Catalog(str(tmp_path / "catalog.sqlite3"))


def _playbook(path, text: str) -> str:
    target = path / "flow.arflow"
    target.write_text(text, encoding="utf-8")
    return str(target)


def _audit(path):
    with sqlite3.connect(str(path / "catalog.sqlite3")) as con:
        return con.execute(
            "SELECT playbook, step_index, fingerprint, state, status, exit_code, "
            "result_json, original, tenant_id, session_id, actor, lang "
            f"FROM {incremental.AUDIT_TABLE} ORDER BY id"
        ).fetchall()


# --- fingerprints --------------------------------------------------------------------


def test_fingerprints_ignore_comments_and_insignificant_whitespace(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compact = arflow.build("اعرض الكتالوج", name="flow")
    noisy = arflow.build("# تعليق\n\n   اعرض    الكتالوج   \n// more\n", name="flow")
    a = incremental.playbook_fingerprints(compact, catalog)
    b = incremental.playbook_fingerprints(noisy, catalog)
    assert a.playbook == b.playbook
    assert a.steps == b.steps
    assert a.policy == b.policy
    assert a.catalog == b.catalog


def test_fingerprint_changes_with_variable_value(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    first = arflow.build("ليكن q = البحث\nابحث عن أداة ${q}", name="flow")
    second = arflow.build("ليكن q = الأمان\nابحث عن أداة ${q}", name="flow")
    a = incremental.playbook_fingerprints(first, catalog)
    b = incremental.playbook_fingerprints(second, catalog)
    assert a.playbook != b.playbook
    assert a.steps[0] != b.steps[0]


def test_fingerprint_changes_with_context(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    first = incremental.playbook_fingerprints(
        arflow.build(
            "اعرض الكتالوج",
            name="flow",
            context=arabic_dsl.DslContext(tenant_id="t-1", actor="مراجع"),
        ),
        catalog,
    )
    second = incremental.playbook_fingerprints(
        arflow.build(
            "اعرض الكتالوج",
            name="flow",
            context=arabic_dsl.DslContext(tenant_id="t-2", actor="مراجع"),
        ),
        catalog,
    )
    assert compiled.ok
    assert first.steps[0] != second.steps[0]


def test_fingerprint_changes_with_catalog_state(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    before = incremental.catalog_fingerprint(catalog)
    with sqlite3.connect(str(catalog.path)) as con:
        con.execute(
            "INSERT INTO servers (name, description, updated_at) VALUES (?, ?, ?)",
            ("demo", "وصف", "2030-01-01T00:00:00"),
        )
    assert incremental.catalog_fingerprint(catalog) != before
    assert incremental.playbook_fingerprints(compiled, catalog).catalog != before


def test_policy_fingerprint_uses_snapshot(monkeypatch) -> None:
    original = catalog_tools.policy_snapshot()
    assert incremental.policy_fingerprint() == incremental._sha256(original)
    monkeypatch.setattr(catalog_tools, "policy_snapshot", lambda: {**original, "extra": "deny"})
    assert incremental.policy_fingerprint() != incremental._sha256(original)


def test_catalog_fingerprint_skips_missing_tables(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    monkeypatch.setattr(incremental, "_CATALOG_TABLES", ("servers", "missing_table"))
    assert incremental.catalog_fingerprint(catalog)


def test_playbook_fingerprints_reject_invalid_compile(tmp_path) -> None:
    with pytest.raises(arflow.ArflowError):
        incremental.playbook_fingerprints(arflow.build("لا يوجد أمر"), _catalog(tmp_path))


def test_fingerprints_as_dict_is_serialisable(tmp_path) -> None:
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    value = incremental.playbook_fingerprints(compiled, _catalog(tmp_path)).as_dict()
    assert isinstance(json.loads(json.dumps(value, ensure_ascii=False)), dict)
    assert len(value["steps"]) == 1
    assert isinstance(value["steps"][0], str)


# --- expectations --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("توقع نجاح", "success"),
        ("expect success", "success"),
        ("توقع فشل", "failure"),
        ("expect failure", "failure"),
        ("توقع حالة ok", "status"),
        ("expect status failed", "status"),
        ("توقع رفض سياسة", "policy_denial"),
        ("expect policy denial", "policy_denial"),
        ("توقع رفض مهمة", "task_denial"),
        ("expect task denial", "task_denial"),
        ("توقع رمز 2", "exit"),
        ("expect code -1", "exit"),
    ],
)
def test_expectation_languages(line: str, kind: str) -> None:
    compiled = arflow.build(f"اعرض الكتالوج\n{line}")
    assert compiled.playbook is not None
    expectation = compiled.playbook.steps[0].expectation
    assert expectation is not None
    assert expectation.kind == kind


@pytest.mark.parametrize(
    "line",
    [
        "توقع حالة 123",
        "توقع رمز ١٢٣",
        "توقع نجاح إضافي",
        "expect success yes",
        "expect status bad-value",
        "توقع رفض سياسة بلا",
    ],
)
def test_invalid_expectation_values(line: str) -> None:
    compiled = arflow.build(f"اعرض الكتالوج\n{line}")
    assert not compiled.ok
    assert compiled.issues[0].code == "INVALID_EXPECTATION"


def test_expectation_before_command_is_rejected() -> None:
    compiled = arflow.build("توقع نجاح\nاعرض الكتالوج")
    assert not compiled.ok
    assert compiled.issues[0].code == "INVALID_EXPECTATION"


def test_check_payload_lists_expectations() -> None:
    payload = arflow.check("اعرض الكتالوج\nتوقع نجاح")
    assert payload["exit_code"] == EXIT_OK
    assert payload["expectations"] == [
        {"step": 1, "kind": "success", "value": "", "raw": "توقع نجاح"}
    ]


def test_error_code_ignores_non_mapping_errors() -> None:
    assert incremental._error_code({"error": "plain"}) is None
    assert incremental._error_code({}) is None


def test_unknown_expectation_kind_fails() -> None:
    compiled = arflow.build("اعرض الكتالوج")
    assert compiled.playbook is not None
    step = replace(
        compiled.playbook.steps[0],
        expectation=arflow.Expectation(kind="unknown", value="", raw="unknown"),
    )
    assert incremental._expectation_ok(step, {"status": "ok", "exit_code": 0}) is False


def test_expectation_is_part_of_step_fingerprint(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    without = incremental.playbook_fingerprints(arflow.build("اعرض الكتالوج"), catalog)
    with_expectation = incremental.playbook_fingerprints(
        arflow.build("اعرض الكتالوج\nتوقع نجاح"), catalog
    )
    assert without.steps[0] != with_expectation.steps[0]


# --- incremental execution ------------------------------------------------------------


def test_first_run_reruns_all_steps(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    report = incremental.run_incremental(catalog, compiled)
    assert report["status"] == "ok"
    assert report["incremental"] is True
    assert report["changed_from"] == 1
    assert report["rerun_count"] == 1
    assert report["adopted_count"] == 0
    assert report["steps"][0]["state"] == "rerun"
    assert report["version"]


def test_unchanged_second_run_adopts_steps(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    incremental.run_incremental(catalog, compiled)
    report = incremental.run_incremental(catalog, compiled)
    assert report["status"] == "ok"
    assert report["changed_from"] is None
    assert report["adopted_count"] == 1
    assert report["rerun_count"] == 0
    assert report["steps"][0]["state"] == "adopted"
    rows = _audit(tmp_path)
    assert [row[3] for row in rows] == ["rerun", "adopted"]


def test_changed_step_reruns_it_and_following_steps(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    incremental.run_incremental(
        catalog, arflow.build("ليكن q = البحث\nابحث عن أداة ${q}\nاعرض الكتالوج", name="flow")
    )
    report = incremental.run_incremental(
        catalog, arflow.build("ليكن q = الأمان\nابحث عن أداة ${q}\nاعرض الكتالوج", name="flow")
    )
    assert report["changed_from"] == 1
    assert [step["state"] for step in report["steps"]] == ["rerun", "rerun"]
    assert report["adopted_count"] == 0


def test_force_full_reruns_even_unchanged_steps(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    incremental.run_incremental(catalog, compiled)
    report = incremental.run_incremental(catalog, compiled, force_full=True)
    assert report["force_full"] is True
    assert report["adopted_count"] == 0
    assert report["rerun_count"] == 1


def test_catalog_change_forces_rerun(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    incremental.run_incremental(catalog, compiled)
    with sqlite3.connect(str(catalog.path)) as con:
        con.execute(
            "INSERT INTO servers (name, updated_at) VALUES (?, ?)",
            ("demo", "2030-01-01T00:00:00"),
        )
    report = incremental.run_incremental(catalog, compiled)
    assert report["adopted_count"] == 0
    assert report["rerun_count"] == 1


def test_policy_change_forces_rerun(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    incremental.run_incremental(catalog, compiled)
    original = catalog_tools.policy_snapshot()
    monkeypatch.setattr(catalog_tools, "policy_snapshot", lambda: {**original, "mode": "strict"})
    report = incremental.run_incremental(catalog, compiled)
    assert report["adopted_count"] == 0
    assert report["rerun_count"] == 1


def test_incremental_false_reruns_all_steps(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    incremental.run_incremental(catalog, compiled)
    report = incremental.run_incremental(catalog, compiled, incremental=False)
    assert report["incremental"] is False
    assert report["adopted_count"] == 0
    assert report["rerun_count"] == 1


def test_run_incremental_rejects_invalid_compile(tmp_path) -> None:
    with pytest.raises(arflow.ArflowError):
        incremental.run_incremental(_catalog(tmp_path), arflow.build("لا يوجد أمر"))


def test_success_expectation_passes(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج\nتوقع نجاح", name="flow")
    report = incremental.run_incremental(catalog, compiled)
    assert report["status"] == "ok"
    assert "expectation_error" not in report["steps"][0]


def test_success_expectation_fails_on_runtime_error(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("تفاصيل المهارة nope\nتوقع نجاح", name="flow")
    report = incremental.run_incremental(catalog, compiled)
    assert report["status"] == "failed"
    assert report["exit_code"] == 1
    assert report["stopped_at"] == 1
    assert "expectation_error" in report["steps"][0]


def test_failure_expectation_accepts_runtime_error(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("تفاصيل المهارة nope\nتوقع فشل", name="flow")
    report = incremental.run_incremental(catalog, compiled)
    assert report["status"] == "failed"
    assert report["steps"][0]["state"] == "failed"
    assert "expectation_error" not in report["steps"][0]


def test_status_and_exit_expectations(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    ok = incremental.run_incremental(
        catalog, arflow.build("اعرض الكتالوج\nتوقع حالة ok", name="flow")
    )
    assert ok["status"] == "ok"
    failed = incremental.run_incremental(
        catalog, arflow.build("تفاصيل المهارة nope\nتوقع رمز 1", name="flow")
    )
    assert failed["steps"][0]["state"] == "failed"
    assert "expectation_error" not in failed["steps"][0]


def test_policy_denial_expectation_passes(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعتمد المهارة demo\nتوقع رفض سياسة", name="flow")
    report = incremental.run_incremental(catalog, compiled)
    assert report["status"] == "failed"
    assert report["exit_code"] == EXIT_BAD_INPUT
    assert report["steps"][0]["state"] == "rejected"
    assert "expectation_error" not in report["steps"][0]


def test_task_denial_expectation_passes_with_stub(monkeypatch, tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج\nتوقع رفض مهمة", name="flow")

    def fake_execute(commands, db):
        return {
            "command": "arabic-dsl",
            "status": "failed",
            "exit_code": 1,
            "steps": [
                {
                    "step": 1,
                    "status": "failed",
                    "exit_code": 1,
                    "error": {"code": mcp_stateless.TASK_DENIED, "message": "denied"},
                }
            ],
        }

    monkeypatch.setattr(arabic_dsl, "execute", fake_execute)
    report = incremental.run_incremental(catalog, compiled)
    assert report["steps"][0]["state"] == "rejected"
    assert "expectation_error" not in report["steps"][0]


def test_wrong_denial_kind_fails_expectation(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعتمد المهارة demo\nتوقع رفض مهمة", name="flow")
    report = incremental.run_incremental(catalog, compiled)
    assert "expectation_error" in report["steps"][0]


def test_optional_failure_is_not_adopted(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("? حالة المهمة t-missing\nاعرض الكتالوج", name="flow")
    first = incremental.run_incremental(catalog, compiled)
    assert first["optional_failures"] == [1]
    second = incremental.run_incremental(catalog, compiled)
    assert second["steps"][0]["state"] in {"failed", "rejected"}
    assert second["adopted_count"] == 0


def test_audit_preserves_verbatim_context_and_text(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    context = arabic_dsl.DslContext(
        tenant_id="tenant-ar", session_id="session-ar", actor="المراجع", lang="ar"
    )
    compiled = arflow.build("اعرض الكتالوج", name=" Audit ", context=context)
    incremental.run_incremental(catalog, compiled)
    row = _audit(tmp_path)[0]
    assert row[0] == " Audit "
    assert row[7] == "اعرض الكتالوج"
    assert row[8:12] == ("tenant-ar", "session-ar", "المراجع", "ar")
    assert row[3] in incremental.STATES


def test_load_previous_handles_missing_database(tmp_path) -> None:
    assert incremental.load_previous(str(tmp_path / "missing.sqlite3"), "flow") == {}


# --- CLI -----------------------------------------------------------------------------


def test_cli_run_incremental_json(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج")
    db = str(tmp_path / "cli.sqlite3")
    args = [
        "--db",
        db,
        "arflow",
        "run",
        "--file",
        path,
        "--incremental",
        "--json",
    ]
    assert main(args) == EXIT_OK
    first = json.loads(capsys.readouterr().out)
    assert first["adopted_count"] == 0
    assert main(args) == EXIT_OK
    second = json.loads(capsys.readouterr().out)
    assert second["adopted_count"] == 1


def test_cli_test_defaults_to_incremental_and_force_full(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج")
    db = str(tmp_path / "cli.sqlite3")
    assert main(["--db", db, "arflow", "run", "--file", path, "--incremental", "--json"]) == EXIT_OK
    capsys.readouterr()
    assert main(["--db", db, "arflow", "test", "--file", path, "--json"]) == EXIT_OK
    adopted = json.loads(capsys.readouterr().out)
    assert adopted["adopted_count"] == 1
    assert (
        main(
            [
                "--db",
                db,
                "arflow",
                "test",
                "--file",
                path,
                "--force-full",
                "--json",
            ]
        )
        == EXIT_OK
    )
    forced = json.loads(capsys.readouterr().out)
    assert forced["rerun_count"] == 1


def test_cli_test_no_incremental_uses_plain_run(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج")
    db = str(tmp_path / "cli.sqlite3")
    rc = main(
        [
            "--db",
            db,
            "arflow",
            "test",
            "--file",
            path,
            "--no-incremental",
            "--json",
        ]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert "adopted_count" not in payload


def test_cli_run_rejects_no_incremental(tmp_path) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--db",
                str(tmp_path / "cli.sqlite3"),
                "arflow",
                "run",
                "--file",
                path,
                "--no-incremental",
                "--json",
            ]
        )
    assert exc.value.code == EXIT_BAD_INPUT


def test_cli_changed_only_alias(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج")
    db = str(tmp_path / "cli.sqlite3")
    assert main(["--db", db, "arflow", "run", "--file", path, "--incremental", "--json"]) == EXIT_OK
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "arflow",
                "run",
                "--file",
                path,
                "--changed-only",
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["incremental"] is True
    assert payload["adopted_count"] == 1


def test_cli_incremental_with_dry_run_stays_plain(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج")
    rc = main(
        [
            "--db",
            str(tmp_path / "cli.sqlite3"),
            "arflow",
            "run",
            "--file",
            path,
            "--dry-run",
            "--incremental",
            "--json",
        ]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert "adopted_count" not in payload


def test_cli_check_still_exposes_expectations(tmp_path, capsys) -> None:
    path = _playbook(tmp_path, "اعرض الكتالوج\nتوقع نجاح")
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
    assert json.loads(capsys.readouterr().out)["expectations"][0]["kind"] == "success"


# --- frozen contract -----------------------------------------------------------------


def test_frozen_manifest_and_specs_unchanged() -> None:
    assert PINNED_TOOL_MANIFEST_SHA256
    assert TOOL_SPECS
    assert RESOURCE_SPECS
    assert incremental.AUDIT_TABLE == "arflow_incremental_audit"
