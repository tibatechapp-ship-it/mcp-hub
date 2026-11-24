"""Phase 6 Path 1: native Arabic runtime inside mcp-hub."""

from __future__ import annotations

import html as html_mod
import json

from mcp_hub import apps as mcp_apps
from mcp_hub.arabic_runtime import arabic_error_fields, normalize_arabic_text, resolve_lang
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main
from mcp_hub.database import Catalog
from mcp_hub.tasks import TaskStore


def test_normalize_arabic_text_digits_and_variants() -> None:
    assert normalize_arabic_text("١٢٣ ۴۵۶") == "123 456"
    assert normalize_arabic_text("أإآـ ى") == "ااا ي"
    assert normalize_arabic_text("  فريق  ١  ") == "فريق 1"


def test_resolve_lang_defaults_to_english(monkeypatch) -> None:
    monkeypatch.delenv("MCP_HUB_LANG", raising=False)
    assert resolve_lang() == "en"
    assert resolve_lang("ar") == "ar"
    monkeypatch.setenv("MCP_HUB_LANG", "العربية")
    assert resolve_lang() == "ar"


def test_arabic_error_fields_keep_machine_shape() -> None:
    error = ValueError("bad value")
    english = arabic_error_fields("task-list", error, EXIT_BAD_INPUT, "en")
    assert english == {
        "command": "task-list",
        "status": "failed",
        "error": "bad value",
        "exit_code": EXIT_BAD_INPUT,
    }
    arabic = arabic_error_fields("task-list", error, EXIT_BAD_INPUT, "ar")
    assert arabic["message_ar"] == "مدخل غير صالح: bad value"
    assert arabic["exit_code"] == EXIT_BAD_INPUT


def test_arabic_cli_diagnostics_are_stable(tmp_path, capsys, monkeypatch) -> None:
    db = str(tmp_path / "catalog.sqlite3")

    assert (
        main(["--db", db, "skill-approve", "missing-skill", "--json", "--lang", "ar"])
        == EXIT_NOT_FOUND
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "skill-approve"
    assert failed["status"] == "failed"
    assert failed["exit_code"] == EXIT_NOT_FOUND
    assert failed["message_ar"].startswith("غير موجود:")
    assert failed["error"]

    monkeypatch.setenv("MCP_HUB_LANG", "arabic")
    assert main(["--db", db, "task-list", "--tenant", "acme", "--limit", "0", "--json"]) == (
        EXIT_BAD_INPUT
    )
    failed = json.loads(capsys.readouterr().err)
    assert failed["message_ar"].startswith("مدخل غير صالح:")
    assert "limit" in failed["error"]


def test_cli_preserves_identifiers_and_rejects_indic_digits(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    task = TaskStore(Catalog(db)).create("t1", "s1", "tool_chain")

    assert main(["--db", db, "task-list", "--tenant", "t1", "--session", "s1", "--json"]) == EXIT_OK
    listed = json.loads(capsys.readouterr().out)
    assert any(item["task_id"] == task["task_id"] for item in listed)

    assert main(["--db", db, "task-list", "--tenant", "t١", "--json"]) == EXIT_BAD_INPUT
    failed = json.loads(capsys.readouterr().err)
    assert failed["command"] == "task-list"
    assert "tenant_id" in failed["error"]


def test_arabic_app_ui_is_rtl_read_only_and_hash_locked(tmp_path, monkeypatch) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    runtime = mcp_apps.build_runtime(Catalog(db))
    uri = mcp_apps.APPROVAL_URI

    english = runtime.resources[uri]({})
    assert '<html lang="en" dir="ltr">' in english["contents"][0]["text"]

    arabic = runtime.resources[uri]({"lang": "ar"})
    page = arabic["contents"][0]["text"]
    assert '<html lang="ar" dir="rtl">' in page
    assert "مراجعة الاعتمادات" in page
    assert "تطبيق MCP للقراءة فقط" in page
    assert "لا أوامر تنفيذ في هذه الواجهة" in page
    assert "'sha256-" in html_mod.unescape(page)

    fallback = json.loads(arabic["contents"][1]["text"])
    assert fallback["lang"] == "ar"
    assert fallback["dir"] == "rtl"
    assert fallback["snapshot"]["read_only_ui"] is True

    monkeypatch.setenv(mcp_apps.ENV_UI_LANG, "arabic")
    from_env = runtime.resources[uri]({})
    assert '<html lang="ar" dir="rtl">' in from_env["contents"][0]["text"]
