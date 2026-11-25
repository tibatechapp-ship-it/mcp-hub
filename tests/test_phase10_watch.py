"""Phase 10: bounded arflow watch over playbooks and linked files."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from mcp_hub import arabic_dsl, arflow, incremental, watch
from mcp_hub.catalog_tools import PINNED_TOOL_MANIFEST_SHA256, RESOURCE_SPECS, TOOL_SPECS
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main
from mcp_hub.database import Catalog

_WATCHED_TEXT = "# watch: notes.txt\nليكن q = notes.txt\nابحث عن أداة ${q}\n"


def _catalog(tmp_path) -> Catalog:
    return Catalog(str(tmp_path / "catalog.sqlite3"))


def _playbook(tmp_path, text: str = _WATCHED_TEXT) -> str:
    target = tmp_path / "flow.arflow"
    target.write_text(text, encoding="utf-8")
    (tmp_path / "notes.txt").write_text("version-1", encoding="utf-8")
    return str(target)


def _watch_audit(db):
    with sqlite3.connect(str(db)) as con:
        return con.execute(
            "SELECT cycle, playbook, original, reason, ran, status, exit_code, "
            "tenant_id, session_id, actor, lang, watched_files_json, linked_json "
            f"FROM {watch.AUDIT_TABLE} ORDER BY id"
        ).fetchall()


# --- fingerprint overlay ----------------------------------------------------------------


def test_with_linked_fingerprints_changes_only_mapped_steps(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج\nاعرض الكتالوج", name="flow")
    base = incremental.playbook_fingerprints(compiled, catalog)
    assert incremental.with_linked_fingerprints(base, {}) == base

    overlaid = incremental.with_linked_fingerprints(base, {1: "digest"})
    assert overlaid.steps[0] != base.steps[0]
    assert overlaid.steps[1] == base.steps[1]
    assert overlaid.playbook != base.playbook
    assert overlaid.policy == base.policy
    assert overlaid.catalog == base.catalog


def test_run_incremental_accepts_overlay_explicitly(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    compiled = arflow.build("اعرض الكتالوج", name="flow")
    base = incremental.playbook_fingerprints(compiled, catalog)
    overlaid = incremental.with_linked_fingerprints(base, {1: "digest"})
    report = incremental.run_incremental(catalog, compiled, fingerprints=overlaid)
    assert report["fingerprints"] == overlaid.as_dict()
    assert report["rerun_count"] == 1


def test_linked_fingerprints_skip_compiled_without_playbook() -> None:
    compiled = dataclasses.replace(arflow.build("اعرض الكتالوج"), playbook=None)
    assert watch._linked_fingerprints(compiled, {"notes.txt": "digest"}) == {}


# --- linked files ------------------------------------------------------------------------


def test_linked_directive_is_resolved_and_mapped_to_step(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    snapshot = watch._snapshot(
        catalog,
        playbook=Path(playbook),
        explicit=(),
        context=arabic_dsl.DslContext(),
        overrides={},
    )
    assert list(snapshot.files) == ["notes.txt"]
    assert len(snapshot.files["notes.txt"]) == 64
    assert set(snapshot.linked) == {1}


def test_linked_file_change_forces_step_rerun(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    first = watch.run_watch(catalog, playbook=playbook)
    (tmp_path / "notes.txt").write_text("version-2", encoding="utf-8")
    second = watch.run_watch(catalog, playbook=playbook)

    assert first["cycles"][0]["report"]["rerun_count"] == 1
    assert second["cycles"][0]["changed_files"] == ["notes.txt"]
    assert second["cycles"][0]["report"]["adopted_count"] == 0
    assert second["cycles"][0]["report"]["rerun_count"] == 1


def test_unlinked_file_change_does_not_invalidate_step(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path, "اعرض الكتالوج\n")
    (tmp_path / "other.txt").write_text("one", encoding="utf-8")
    watch.run_watch(catalog, playbook=playbook, watch_files=("other.txt",))
    (tmp_path / "other.txt").write_text("two", encoding="utf-8")
    second = watch.run_watch(catalog, playbook=playbook, watch_files=("other.txt",))

    assert second["cycles"][0]["changed_files"] == ["other.txt"]
    assert second["cycles"][0]["report"]["adopted_count"] == 1
    assert second["cycles"][0]["report"]["rerun_count"] == 0


def test_playbook_comment_change_does_not_invalidate_step(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    target = tmp_path / "flow.arflow"
    changed = False

    def sleep_between_cycles(seconds: float) -> None:
        nonlocal changed
        if not changed:
            changed = True
            target.write_text("# another comment\n" + target.read_text(encoding="utf-8"))

    monkeypatch.setattr(watch.time, "sleep", sleep_between_cycles)
    result = watch.run_watch(catalog, playbook=playbook, max_cycles=2, interval=0.25)

    assert result["cycles"][1]["fingerprint_changed"] is False
    assert result["cycles"][1]["ran"] is False
    assert result["stop_reason"] == watch._STOP_OK


def test_relative_path_validation_refuses_traversal() -> None:
    assert watch._validate_relative("'notes.txt'") == "notes.txt"
    with pytest.raises(watch.WatchError):
        watch._validate_relative("../secret.txt")
    with pytest.raises(watch.WatchError):
        watch._validate_relative("/tmp/secret.txt")
    with pytest.raises(watch.WatchError):
        watch._validate_relative("")


def test_watch_file_limit_and_size_limit(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    playbook = Path(_playbook(tmp_path))
    monkeypatch.setattr(watch, "MAX_WATCH_FILES", 0)
    with pytest.raises(watch.WatchError):
        watch._linked_paths(playbook, "# watch: notes.txt\n", ())
    monkeypatch.setattr(watch, "MAX_FILE_BYTES", 0)
    with pytest.raises(watch.WatchError):
        watch._file_digest(tmp_path / "notes.txt")
    assert catalog is not None


def test_missing_playbook_and_invalid_playbook_fail_with_stable_codes(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    with pytest.raises(watch.WatchError) as missing:
        watch.run_watch(catalog, playbook=str(tmp_path / "absent.arflow"))
    assert missing.value.code == EXIT_NOT_FOUND

    target = tmp_path / "bad.arflow"
    target.write_text("لا يوجد أمر\n", encoding="utf-8")
    with pytest.raises(watch.WatchError) as invalid:
        watch.run_watch(catalog, playbook=str(target))
    assert invalid.value.code == EXIT_BAD_INPUT


def test_bounds_are_validated(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    with pytest.raises(watch.WatchError):
        watch.run_watch(catalog, playbook=playbook, max_cycles=0)
    with pytest.raises(watch.WatchError):
        watch.run_watch(catalog, playbook=playbook, interval=float("nan"))
    with pytest.raises(watch.WatchError):
        watch.run_watch(catalog, playbook=playbook, reason="x" * 5000)


# --- bounded loop and audit ----------------------------------------------------------------


def test_bounded_loop_stops_when_nothing_changes(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    result = watch.run_watch(catalog, playbook=playbook, max_cycles=5)
    assert result["cycle_count"] == 2
    assert result["stop_reason"] == watch._STOP_OK
    assert result["cycles"][1]["ran"] is False
    assert result["cycles"][1]["report"] is None


def test_toctou_mismatch_fails_cycle(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    notes = tmp_path / "notes.txt"
    original = incremental.run_incremental

    def changing_run(catalog_arg, compiled, **kwargs):
        notes.write_text("changed-during-run", encoding="utf-8")
        return original(catalog_arg, compiled, **kwargs)

    monkeypatch.setattr(incremental, "run_incremental", changing_run)
    result = watch.run_watch(catalog, playbook=playbook)
    assert result["status"] == "failed"
    assert result["exit_code"] == EXIT_NOT_FOUND
    assert result["stop_reason"] == watch._STOP_FAILED
    assert any("linked files changed" in item for item in result["toctou_mismatch"])


def test_settle_resnapshots_changed_files(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    notes = tmp_path / "notes.txt"
    sleeps = 0

    def sleep_with_change(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            notes.write_text("version-2", encoding="utf-8")

    monkeypatch.setattr(watch.time, "sleep", sleep_with_change)
    result = watch.run_watch(catalog, playbook=playbook, max_cycles=3, interval=0.01, settle=0.01)
    cycle = result["cycles"][1]

    assert sleeps >= 2
    assert cycle["changed_files"] == ["notes.txt"]
    assert cycle["fingerprint_changed"] is True
    assert cycle["ran"] is True
    assert result["watched_files"]["notes.txt"] == watch._file_digest(notes)


def test_after_run_snapshot_error_is_reported_as_toctou(tmp_path, monkeypatch) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    original = watch._snapshot
    calls = 0

    def flaky_snapshot(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise watch.WatchError("playbook changed during the run")
        return original(*args, **kwargs)

    monkeypatch.setattr(watch, "_snapshot", flaky_snapshot)
    result = watch.run_watch(catalog, playbook=playbook)

    assert result["status"] == "failed"
    assert result["exit_code"] == EXIT_NOT_FOUND
    assert "playbook fingerprint changed during the run" in result["toctou_mismatch"]


def test_watch_audit_preserves_verbatim_arabic_fields(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    playbook = _playbook(tmp_path)
    context = arabic_dsl.DslContext(
        tenant_id="tenant-ar", session_id="session-ar", actor="المراجع", lang="ar"
    )
    watch.run_watch(catalog, playbook=playbook, context=context, reason="سبب المراقبة")
    row = _watch_audit(catalog.path)[0]
    assert row[1] == "flow"
    assert row[2] == (tmp_path / "flow.arflow").read_text(encoding="utf-8")
    assert row[3] == "سبب المراقبة"
    assert row[4] == 1
    assert row[5] == "ok"
    assert row[6] == EXIT_OK
    assert row[7:11] == ("tenant-ar", "session-ar", "المراجع", "ar")
    assert json.loads(row[11])["notes.txt"]
    assert json.loads(row[12])["1"]


def test_frozen_contract_is_unchanged() -> None:
    assert PINNED_TOOL_MANIFEST_SHA256
    assert TOOL_SPECS
    assert RESOURCE_SPECS
    assert watch.AUDIT_TABLE == "arflow_watch_audit"
    assert watch.MAX_CYCLES == 1000


# --- CLI -----------------------------------------------------------------------------


def test_cli_watch_json_and_arabic_diagnostics(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    playbook = _playbook(tmp_path)
    rc = main(
        [
            "--db",
            db,
            "arflow",
            "watch",
            "--file",
            playbook,
            "--reason",
            "مراجعة عربية",
            "--once",
            "--json",
            "--lang",
            "ar",
        ]
    )
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "arflow-watch"
    assert payload["lang"] == "ar"
    assert payload["reason"] == "مراجعة عربية"
    assert payload["cycle_count"] == 1


def test_cli_watch_failed_report_adds_arabic_summary(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    playbook = _playbook(tmp_path, "تفاصيل المهارة nope\n")
    rc = main(["--db", db, "arflow", "watch", "--file", playbook, "--json", "--lang", "ar"])
    assert rc == EXIT_NOT_FOUND
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["message_ar"].startswith("فشلت مراقبة الخطة")


def test_cli_missing_linked_file_uses_json_stderr(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    playbook = _playbook(tmp_path, "# watch: absent.txt\nاعرض الكتالوج\n")
    rc = main(["--db", db, "arflow", "watch", "--file", playbook, "--json", "--lang", "ar"])
    assert rc == EXIT_NOT_FOUND
    fields = json.loads(capsys.readouterr().err)
    assert fields["command"] == "arflow-watch"
    assert fields["exit_code"] == EXIT_NOT_FOUND
    assert "message_ar" in fields


def test_cli_rejects_bad_bounds_and_once_conflict(tmp_path, capsys) -> None:
    db = str(tmp_path / "catalog.sqlite3")
    playbook = _playbook(tmp_path)

    rc = main(
        [
            "--db",
            db,
            "arflow",
            "watch",
            "--file",
            playbook,
            "--max-cycles",
            "0",
            "--json",
            "--lang",
            "ar",
        ]
    )
    assert rc == EXIT_BAD_INPUT
    fields = json.loads(capsys.readouterr().err)
    assert fields["exit_code"] == EXIT_BAD_INPUT
    assert "message_ar" in fields

    rc = main(
        [
            "--db",
            db,
            "arflow",
            "watch",
            "--file",
            playbook,
            "--once",
            "--max-cycles",
            "2",
            "--json",
        ]
    )
    assert rc == EXIT_BAD_INPUT
    capsys.readouterr()
