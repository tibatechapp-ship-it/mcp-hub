"""Central Governance Phase 2: tenant project init and global Git hooks."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from mcp_hub import governance, incremental
from mcp_hub.cli import EXIT_BAD_INPUT, EXIT_NOT_FOUND, EXIT_OK, main

_PLAYBOOK = "# watch: notes.txt\nليكن q = notes.txt\nابحث عن أداة ${q}\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "governance@example.invalid")
    _git(root, "config", "user.name", "Governance Test")
    return root


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("MCP_HUB_GOVERNANCE_HOME", str(tmp_path / "governance-home"))
    monkeypatch.delenv("MCP_HUB_LANG", raising=False)
    monkeypatch.delenv("MCP_HUB_GOVERNANCE_JSON", raising=False)
    monkeypatch.delenv("MCP_HUB_GOVERNANCE_LANG", raising=False)


def _bind(root: Path, *, playbook: str = ".arflow/security.arflow") -> governance.ProjectConfig:
    folder = root / ".arflow"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "security.arflow").write_text(_PLAYBOOK, encoding="utf-8")
    (folder / "notes.txt").write_text("version-1", encoding="utf-8")
    return governance.init_project(root, tenant="tayf-main", playbook=playbook)


def _audit(db: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(str(db)) as con:
        return con.execute(
            f"SELECT state, tenant_id, original FROM {incremental.AUDIT_TABLE} ORDER BY id"
        ).fetchall()


# --- validation and config --------------------------------------------------------------


def test_validate_id_accepts_dots_colons_and_at_signs() -> None:
    assert governance.validate_id("tayf:main@v1.2", "tenant") == "tayf:main@v1.2"


@pytest.mark.parametrize("bad", ["tenant١", "tenant۱", "bad tenant", "", "x" * 129, 42])
def test_validate_id_rejects_indic_digits_and_bad_shapes(bad: object) -> None:
    with pytest.raises(governance.GovernanceError) as exc:
        governance.validate_id(bad, "tenant")
    assert exc.value.code == EXIT_BAD_INPUT


@pytest.mark.parametrize(
    "bad",
    ["/tmp/x", "../x", ".arflow/../../x", "a\\b", "C:/x", "", "x" * 513, None],
)
def test_validate_relative_path_rejects_escapes(bad: object) -> None:
    with pytest.raises(governance.GovernanceError):
        governance.validate_relative_path(bad, "playbook")


def test_project_config_rejects_unknown_keys_and_versions() -> None:
    base = {
        "tenant": "t-x",
        "playbook": ".arflow/security.arflow",
        "audit_db": "audit.sqlite3",
    }
    with pytest.raises(governance.GovernanceError):
        governance.ProjectConfig.from_dict({**base, "version": 2})
    with pytest.raises(governance.GovernanceError):
        governance.ProjectConfig.from_dict({**base, "surprise": "x"})


def test_project_config_round_trip_and_context_lang() -> None:
    config = governance.ProjectConfig.from_dict(
        {
            "tenant": "t-x",
            "playbook": ".arflow/security.arflow",
            "audit_db": "audit.sqlite3",
        }
    )
    assert config.session == "git" and config.actor == "git"
    assert governance.ProjectConfig.from_dict(config.as_dict()) == config
    assert config.context("ar").lang == "ar"


# --- project init / show -----------------------------------------------------------------


def test_init_writes_binding_and_show_reads_it(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    payload = governance.init_project(root, tenant="tayf-main")
    assert payload["exit_code"] == EXIT_OK
    path = root / governance.CONFIG_FILE
    assert json.loads(path.read_text(encoding="utf-8"))["tenant"] == "tayf-main"
    assert governance.load_project(root) == governance.ProjectConfig.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )
    assert governance.show_project(root)["status"] == "ok"


def test_init_requires_git_repo_and_refuses_overwrite(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(governance.GovernanceError) as exc:
        governance.init_project(plain, tenant="t-x")
    assert exc.value.code == EXIT_BAD_INPUT

    root = _repo(tmp_path)
    governance.init_project(root, tenant="first")
    with pytest.raises(governance.GovernanceError):
        governance.init_project(root, tenant="second")
    replaced = governance.init_project(root, tenant="second", force=True)
    assert replaced["config"]["tenant"] == "second"


def test_show_unbound_repo_uses_runtime_exit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    payload = governance.show_project(root)
    assert payload["status"] == "not_bound"
    assert payload["exit_code"] == EXIT_NOT_FOUND


def test_cli_init_and_show_json_with_arabic(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    assert (
        main(["governance", "init", "--tenant", "tayf-main", "--json", "--lang", "ar"]) == EXIT_OK
    )
    first = json.loads(capsys.readouterr().out)
    assert first["config"]["tenant"] == "tayf-main"

    assert main(["governance", "show", "--json", "--lang", "ar"]) == EXIT_OK
    second = json.loads(capsys.readouterr().out)
    assert second["status"] == "ok"


def test_cli_init_rejects_indic_digits_with_arabic_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    rc = main(["governance", "init", "--tenant", "tayf١", "--json", "--lang", "ar"])
    assert rc == EXIT_BAD_INPUT
    error = json.loads(capsys.readouterr().err)
    assert error["exit_code"] == EXIT_BAD_INPUT
    assert "message_ar" in error


# --- global hook install / check / uninstall ---------------------------------------------


def test_install_check_and_uninstall_are_safe(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    installed = governance.install_hooks(root)
    assert installed["status"] == "installed"
    assert installed["previous_hooks_path"] is None

    hooks = Path(installed["hooks_path"])
    for event in governance.HOOK_EVENTS:
        script = hooks / event
        assert governance.MARKER in script.read_text(encoding="utf-8")
        assert os.access(script, os.X_OK)

    checked = governance.check_hooks(root)
    assert checked["status"] == "installed" and checked["exit_code"] == EXIT_OK

    foreign = hooks / "pre-push"
    foreign.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    removed = governance.uninstall_hooks(root)
    assert removed["status"] == "uninstalled"
    assert removed["removed"] == ["pre-commit"]
    assert removed["preserved"] == ["pre-push"]
    assert foreign.is_file()
    assert governance._global_hooks_path() is None
    assert not governance._global_config_path().exists()


def test_install_refuses_foreign_global_hooks_path(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    _git_foreign = subprocess.run(
        ["git", "config", "--global", "core.hooksPath", "/foreign/hooks"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _git_foreign.returncode == 0
    with pytest.raises(governance.GovernanceError) as exc:
        governance.install_hooks(root)
    assert exc.value.code == EXIT_BAD_INPUT


def test_install_is_idempotent_and_uninstall_clears_hooks_path(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    first = governance.install_hooks(root)
    second = governance.install_hooks(root)
    assert second["hooks_path"] == first["hooks_path"]
    assert second["previous_hooks_path"] is None
    assert governance.check_hooks(root)["status"] == "installed"
    governance.uninstall_hooks(root)
    assert governance._global_hooks_path() is None


def test_check_reports_not_installed_and_broken_state(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    missing = governance.check_hooks(root)
    assert missing["status"] == "not_installed"
    assert missing["exit_code"] == EXIT_NOT_FOUND

    governance.install_hooks(root)
    hooks = Path(governance._load_global().hooks_path)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho hijacked\n", encoding="utf-8")
    broken = governance.check_hooks(root)
    assert broken["status"] == "broken"
    assert broken["exit_code"] == EXIT_NOT_FOUND
    assert broken["files"]["pre-commit"] is False


def test_uninstall_without_global_config_skips(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    payload = governance.uninstall_hooks(root)
    assert payload["status"] == "skipped"
    assert payload["exit_code"] == EXIT_OK


def test_hook_command_resolves_explicit_env_and_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MCP_HUB_BIN", "/opt/bin/mcp-hub")
    assert governance._hook_command() == "/opt/bin/mcp-hub"
    monkeypatch.delenv("MCP_HUB_BIN")
    monkeypatch.setattr(governance.shutil, "which", lambda _name: None)
    command = governance._hook_command()
    assert "-m mcp_hub.cli" in command


# --- hook runner -------------------------------------------------------------------------


def test_run_hook_skips_outside_repo(tmp_path: Path) -> None:
    payload = governance.run_hook("pre-commit", repo=tmp_path / "elsewhere")
    assert payload["status"] == "skipped"
    assert payload["reason"] == "not_a_git_repo"
    assert payload["exit_code"] == EXIT_OK


def test_run_hook_skips_unbound_and_missing_playbook(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    unbound = governance.run_hook("pre-commit", repo=root)
    assert unbound["reason"] == "unbound_repository"

    governance.init_project(root, tenant="tayf-main")
    missing = governance.run_hook("pre-commit", repo=root)
    assert missing["reason"] == "no_playbook"


def test_run_hook_rejects_unknown_event(tmp_path: Path) -> None:
    with pytest.raises(governance.GovernanceError) as exc:
        governance.run_hook("post-merge", repo=_repo(tmp_path))
    assert exc.value.code == EXIT_BAD_INPUT


def test_pre_commit_runs_and_linked_change_forces_rerun(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _bind(root)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")

    (root / ".arflow" / "security.arflow").write_text(_PLAYBOOK + "\n# touched\n", encoding="utf-8")
    _git(root, "add", ".arflow/security.arflow")
    first = governance.run_hook("pre-commit", repo=root, lang="ar")
    assert first["exit_code"] == EXIT_OK
    assert first["status"] == "ok"
    assert first["tenant"] == "tayf-main"
    assert first["relevant_files"] == [".arflow/security.arflow"]

    (root / ".arflow" / "notes.txt").write_text("version-2", encoding="utf-8")
    _git(root, "add", ".arflow/notes.txt")
    second = governance.run_hook("pre-commit", repo=root, lang="ar")
    assert second["exit_code"] == EXIT_OK
    assert ".arflow/notes.txt" in second["relevant_files"]
    assert second["incremental"]["rerun"] == 1

    rows = _audit(root / ".mcp-hub-audit.sqlite3")
    assert len(rows) >= 2
    assert all(row[1] == "tayf-main" for row in rows)
    assert all("ابحث عن أداة" in row[2] for row in rows)


def test_pre_commit_skips_when_nothing_relevant_changed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _bind(root)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    (root / "unrelated.txt").write_text("x", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    payload = governance.run_hook("pre-commit", repo=root)
    assert payload["status"] == "skipped"
    assert payload["reason"] == "no_relevant_changes"
    assert "unrelated.txt" in payload["changed_files"]


def test_hook_reports_failed_playbook_check(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    folder = root / ".arflow"
    folder.mkdir()
    (folder / "security.arflow").write_text("ملف غير معروف\n", encoding="utf-8")
    governance.init_project(root, tenant="tayf-main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "bad playbook")
    (folder / "security.arflow").write_text("ملف غير معروف آخر\n", encoding="utf-8")
    _git(root, "add", ".arflow/security.arflow")
    payload = governance.run_hook("pre-commit", repo=root, lang="ar")
    assert payload["status"] == "failed"
    assert payload["reason"] == "playbook_check_failed"
    assert payload["exit_code"] == EXIT_NOT_FOUND


def test_hook_missing_linked_file_is_bad_input(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    folder = root / ".arflow"
    folder.mkdir()
    (folder / "security.arflow").write_text(
        "# watch: absent.txt\nاعرض الكتالوج\n", encoding="utf-8"
    )
    governance.init_project(root, tenant="tayf-main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "linked")
    (folder / "security.arflow").write_text(
        "# watch: absent.txt\nاعرض الكتالوج\n# changed\n", encoding="utf-8"
    )
    _git(root, "add", ".arflow/security.arflow")
    with pytest.raises(governance.GovernanceError) as exc:
        governance.run_hook("pre-commit", repo=root)
    assert exc.value.code in (EXIT_BAD_INPUT, EXIT_NOT_FOUND)


def test_pre_push_uses_upstream_range_when_available(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _bind(root)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "-u", "origin", "HEAD:refs/heads/main")
    (root / ".arflow" / "notes.txt").write_text("version-2", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "linked change")

    changed = governance._changed_paths(root, "pre-push")
    assert ".arflow/notes.txt" in changed


def test_cli_hook_json_skip_and_arabic_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    root = _repo(tmp_path)
    monkeypatch.chdir(root)
    assert (
        main(["governance", "hook", "--event", "pre-commit", "--json", "--lang", "ar"]) == EXIT_OK
    )
    skipped = json.loads(capsys.readouterr().out)
    assert skipped["reason"] == "unbound_repository"

    folder = root / ".arflow"
    folder.mkdir()
    (folder / "security.arflow").write_text("ملف غير معروف\n", encoding="utf-8")
    governance.init_project(root, tenant="tayf-main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "bad")
    (folder / "security.arflow").write_text("ملف غير معروف آخر\n", encoding="utf-8")
    _git(root, "add", ".arflow/security.arflow")
    rc = main(["governance", "hook", "--event", "pre-commit", "--json", "--lang", "ar"])
    assert rc == EXIT_NOT_FOUND
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "failed"
    assert failed["message_ar"].startswith("فشل فحص الحوكمة")


# --- defensive edges -------------------------------------------------------------------


def test_global_config_rejects_bad_fields() -> None:
    cases = [
        {"version": 2, "hooks_path": "/tmp/hooks"},
        {"hooks_path": ""},
        {"hooks_path": "/tmp/hooks", "previous_hooks_path": 7},
        {"hooks_path": "/tmp/hooks", "installed_events": ["bogus"]},
        {"hooks_path": "/tmp/hooks", "installed_events": "pre-commit"},
    ]
    for case in cases:
        with pytest.raises(governance.GovernanceError):
            governance.GlobalConfig.from_dict(case)


def test_read_json_rejects_oversize_malformed_and_non_object(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("x" * (governance.MAX_CONFIG_BYTES + 1), encoding="utf-8")
    with pytest.raises(governance.GovernanceError):
        governance._read_json(path, "config")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(governance.GovernanceError):
        governance._read_json(path, "config")
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(governance.GovernanceError):
        governance._read_json(path, "config")


def test_git_reports_missing_binary_and_command_failure(tmp_path: Path, monkeypatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("git missing")

    monkeypatch.setattr(governance.subprocess, "run", boom)
    with pytest.raises(governance.GovernanceError) as exc:
        governance._git(["status"])
    assert exc.value.code == EXIT_NOT_FOUND

    monkeypatch.undo()
    with pytest.raises(governance.GovernanceError) as exc:
        governance._git(["-C", str(tmp_path), "rev-parse"], check=True)
    assert exc.value.code == EXIT_NOT_FOUND


def test_load_project_outside_repo_returns_none(tmp_path: Path, monkeypatch) -> None:
    assert governance.load_project(tmp_path / "missing") is None
    monkeypatch.chdir(tmp_path)
    assert governance.load_project() is None


def test_global_hooks_path_and_unset_report_git_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        governance,
        "_git",
        lambda args, **kwargs: CompletedProcess(args, 2, "", "git config failed"),
    )
    with pytest.raises(governance.GovernanceError) as exc:
        governance._global_hooks_path()
    assert exc.value.code == EXIT_NOT_FOUND

    monkeypatch.setattr(
        governance,
        "_git",
        lambda args, **kwargs: CompletedProcess(args, 9, "", "unset failed"),
    )
    with pytest.raises(governance.GovernanceError) as exc:
        governance._set_global_hooks_path(None)
    assert exc.value.code == EXIT_NOT_FOUND


def test_is_managed_hook_handles_missing_and_unreadable(tmp_path: Path, monkeypatch) -> None:
    assert governance._is_managed_hook(tmp_path / "missing") is False

    path = tmp_path / "pre-push"
    path.write_text("foreign", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", boom)
    assert governance._is_managed_hook(path) is False


def test_install_rejects_different_recorded_global_config(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    governance.install_hooks(root)
    home = governance.governance_home()
    (home / governance.GLOBAL_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "hooks_path": str(home / "other-hooks"),
                "installed_events": list(governance.HOOK_EVENTS),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(governance.GovernanceError):
        governance.install_hooks(root)


def test_uninstall_skips_missing_hook_file(tmp_path: Path, monkeypatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root = _repo(tmp_path)
    installed = governance.install_hooks(root)
    hooks_path = Path(installed["hooks_path"])
    (hooks_path / "pre-push").unlink()
    result = governance.uninstall_hooks(root)
    assert result["removed"] == ["pre-commit"]


def test_diff_paths_reports_git_failure(tmp_path: Path) -> None:
    with pytest.raises(governance.GovernanceError) as exc:
        governance._diff_paths(tmp_path, "--cached")
    assert exc.value.code == EXIT_NOT_FOUND


def test_changed_paths_pre_push_parent_fallback(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _bind(root)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "first")
    (root / ".arflow" / "notes.txt").write_text("version-2", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "second")
    assert ".arflow/notes.txt" in governance._changed_paths(root, "pre-push")


def test_changed_paths_pre_push_staged_and_ls_files_fallbacks(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _bind(root)
    _git(root, "add", ".")
    changed = governance._changed_paths(root, "pre-push")
    assert ".mcp-hub-governance.json" in changed

    empty = _repo(tmp_path, "empty")
    (empty / "untracked.txt").write_text("x", encoding="utf-8")
    assert governance._changed_paths(empty, "pre-push") == []


def test_run_hook_wraps_catalog_errors(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    _bind(root)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    (root / ".arflow" / "notes.txt").write_text("version-2", encoding="utf-8")
    _git(root, "add", ".arflow/notes.txt")

    def fail_catalog(*args: object, **kwargs: object) -> None:
        raise OSError("database unavailable")

    monkeypatch.setattr(governance, "Catalog", fail_catalog)
    with pytest.raises(governance.GovernanceError) as exc:
        governance.run_hook("pre-commit", repo=root)
    assert exc.value.code == EXIT_NOT_FOUND
