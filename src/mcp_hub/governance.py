"""Local project binding and global Git hooks for central governance.

This module keeps Phase 1 (`action.yml` / `governance-gate.yml`) and the later
local-hook workflow on one path: a project binds a tenant, a `.arflow` playbook
and a SQLite audit DB; global `pre-commit` / `pre-push` hooks then reuse the
Phase 9 incremental engine and the Phase 10 linked-file fingerprints. No MCP
handler, pinned manifest, tool spec, resource spec or default policy is changed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, arabic_dsl, arflow, incremental, watch
from .arabic_runtime import resolve_lang
from .database import Catalog

__all__ = [
    "CONFIG_FILE",
    "HOOK_EVENTS",
    "MARKER",
    "GovernanceError",
    "ProjectConfig",
    "GlobalConfig",
    "init_project",
    "show_project",
    "install_hooks",
    "check_hooks",
    "uninstall_hooks",
    "run_hook",
]

CONFIG_FILE = ".mcp-hub-governance.json"
GLOBAL_FILE = "governance-global.json"
HOOKS_DIR = "hooks"
HOOK_EVENTS = ("pre-commit", "pre-push")
MARKER = "# mcp-hub-central-governance:v2"
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_BAD_INPUT = 2
MAX_CONFIG_BYTES = 64 * 1024
MAX_PATH_LENGTH = 512
_ID_RE = re.compile(r"[A-Za-z0-9._:@-]{1,128}")
_INDIC_RE = re.compile(r"[\u0660-\u0669\u06f0-\u06f9]")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class GovernanceError(ValueError):
    """A binding/hook error with a stable CLI exit code."""

    def __init__(self, message: str, *, code: int = EXIT_BAD_INPUT) -> None:
        super().__init__(message)
        self.code = code


def validate_id(value: object, label: str = "id") -> str:
    """Validate a tenant/session/actor/id without normalizing its text."""
    if not isinstance(value, str):
        raise GovernanceError(f"invalid {label}")
    if _INDIC_RE.search(value) or _ID_RE.fullmatch(value) is None:
        raise GovernanceError(f"invalid {label}: {value}")
    return value


def validate_relative_path(value: object, label: str) -> str:
    """Keep playbook and audit paths inside the bound repository."""
    if not isinstance(value, str):
        raise GovernanceError(f"invalid {label}")
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > MAX_PATH_LENGTH
        or "\x00" in candidate
        or "\\" in candidate
        or _DRIVE_RE.match(candidate)
    ):
        raise GovernanceError(f"invalid {label}: {value}")
    path = Path(candidate)
    if path.is_absolute() or ".." in path.parts:
        raise GovernanceError(f"{label} must stay relative to the repository: {candidate}")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """The local binding written to ``.mcp-hub-governance.json``."""

    tenant: str
    playbook: str
    audit_db: str
    session: str = "git"
    actor: str = "git"
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tenant": self.tenant,
            "playbook": self.playbook,
            "audit_db": self.audit_db,
            "session": self.session,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProjectConfig:
        version = data.get("version", 1)
        if version != 1:
            raise GovernanceError(f"unsupported governance config version: {version}")
        allowed = {"version", "tenant", "playbook", "audit_db", "session", "actor"}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise GovernanceError(f"unknown governance config keys: {', '.join(unknown)}")
        return cls(
            tenant=validate_id(data.get("tenant"), "tenant"),
            playbook=validate_relative_path(data.get("playbook"), "playbook"),
            audit_db=validate_relative_path(data.get("audit_db"), "audit_db"),
            session=validate_id(data.get("session", "git"), "session"),
            actor=validate_id(data.get("actor", "git"), "actor"),
        )

    def context(self, lang: str | None = None) -> arabic_dsl.DslContext:
        return arabic_dsl.DslContext(
            tenant_id=self.tenant,
            session_id=self.session,
            actor=self.actor,
            lang=resolve_lang(lang),
        )


@dataclass(frozen=True, slots=True)
class GlobalConfig:
    """Bookkeeping for the installed global hook directory."""

    hooks_path: str
    previous_hooks_path: str | None
    installed_events: tuple[str, ...] = HOOK_EVENTS
    version: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "hooks_path": self.hooks_path,
            "previous_hooks_path": self.previous_hooks_path,
            "installed_events": list(self.installed_events),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> GlobalConfig:
        if data.get("version", 1) != 1:
            raise GovernanceError("unsupported global governance config version")
        path = data.get("hooks_path")
        if not isinstance(path, str) or not path:
            raise GovernanceError("invalid global hooks_path")
        previous = data.get("previous_hooks_path")
        if previous is not None and not isinstance(previous, str):
            raise GovernanceError("invalid previous_hooks_path")
        events_raw = data.get("installed_events", list(HOOK_EVENTS))
        if not isinstance(events_raw, list) or not all(item in HOOK_EVENTS for item in events_raw):
            raise GovernanceError("invalid installed_events")
        return cls(
            hooks_path=path,
            previous_hooks_path=previous,
            installed_events=tuple(str(item) for item in events_raw),
        )


def governance_home() -> Path:
    """Resolve the global config root; tests may override it with an env var."""
    override = os.environ.get("MCP_HUB_GOVERNANCE_HOME", "").strip()
    return Path(override) if override else Path.home() / ".config" / "mcp-hub"


def _global_config_path() -> Path:
    return governance_home() / GLOBAL_FILE


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise GovernanceError(f"{label} too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise GovernanceError(f"{label} must be a JSON object")
    return {str(key): value for key, value in data.items()}


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    with temp.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _git(
    args: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run git without a shell and turn failures into structured errors."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise GovernanceError(f"git unavailable: {exc}", code=EXIT_RUNTIME) from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise GovernanceError(f"git {args[0]} failed: {detail}", code=EXIT_RUNTIME)
    return proc


def find_repo_root(start: Path | None = None) -> Path | None:
    """Return the git worktree root, or ``None`` outside a repository."""
    base = start or Path.cwd()
    proc = _git(["-C", str(base), "rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return Path(value) if value else None


def _require_repo_root(start: Path | None = None) -> Path:
    root = find_repo_root(start)
    if root is None:
        raise GovernanceError("not a git repository")
    return root


def load_project(repo: Path | None = None) -> ProjectConfig | None:
    root = repo or find_repo_root()
    if root is None:
        return None
    path = root / CONFIG_FILE
    if not path.is_file():
        return None
    return ProjectConfig.from_dict(_read_json(path, "governance config"))


def init_project(
    repo: Path | None,
    *,
    tenant: str,
    playbook: str = ".arflow/security.arflow",
    audit_db: str = ".mcp-hub-audit.sqlite3",
    session: str = "git",
    actor: str = "git",
    force: bool = False,
) -> dict[str, Any]:
    """Bind the current repository to a tenant, playbook and audit DB."""
    root = _require_repo_root(repo)
    config = ProjectConfig(
        tenant=validate_id(tenant, "tenant"),
        playbook=validate_relative_path(playbook, "playbook"),
        audit_db=validate_relative_path(audit_db, "audit_db"),
        session=validate_id(session, "session"),
        actor=validate_id(actor, "actor"),
    )
    path = root / CONFIG_FILE
    if path.exists() and not force:
        raise GovernanceError("repository already bound; use --force to replace it")
    _atomic_write_text(path, json.dumps(config.as_dict(), ensure_ascii=False, indent=2) + "\n")
    return {
        "command": "governance-init",
        "status": "ok",
        "exit_code": EXIT_OK,
        "repo": str(root),
        "config_path": str(path),
        "config": config.as_dict(),
    }


def show_project(repo: Path | None = None) -> dict[str, Any]:
    """Show the current local binding."""
    root = _require_repo_root(repo)
    config = load_project(root)
    if config is None:
        return {
            "command": "governance-show",
            "status": "not_bound",
            "reason": "unbound_repository",
            "exit_code": EXIT_RUNTIME,
            "repo": str(root),
        }
    return {
        "command": "governance-show",
        "status": "ok",
        "exit_code": EXIT_OK,
        "repo": str(root),
        "config": config.as_dict(),
    }


def _global_hooks_path() -> str | None:
    proc = _git(["config", "--global", "--get", "core.hooksPath"], check=False)
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        raise GovernanceError(f"git config failed: {detail}", code=EXIT_RUNTIME)
    return proc.stdout.strip() or None


def _set_global_hooks_path(value: str | None) -> None:
    if value is None:
        proc = _git(["config", "--global", "--unset", "core.hooksPath"], check=False)
        if proc.returncode not in (0, 5):
            detail = proc.stderr.strip()
            raise GovernanceError(f"git config --unset failed: {detail}", code=EXIT_RUNTIME)
        return
    _git(["config", "--global", "core.hooksPath", value])


def _load_global() -> GlobalConfig | None:
    path = _global_config_path()
    if not path.is_file():
        return None
    return GlobalConfig.from_dict(_read_json(path, "global governance config"))


def _hook_command(mcp_bin: str | None = None) -> str:
    candidate = mcp_bin or os.environ.get("MCP_HUB_BIN") or shutil.which("mcp-hub")
    if candidate:
        return shlex.quote(candidate)
    return f"{shlex.quote(sys.executable)} -m mcp_hub.cli"


def _render_hook(event: str, command: str) -> str:
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "# Managed by `mcp-hub governance hooks install`; do not edit by hand.\n"
        'if [ "${MCP_HUB_GOVERNANCE_JSON:-0}" = "1" ]; then\n'
        '  set -- --json "$@"\n'
        "fi\n"
        'if [ -n "${MCP_HUB_GOVERNANCE_LANG:-}" ]; then\n'
        '  set -- --lang "$MCP_HUB_GOVERNANCE_LANG" "$@"\n'
        "fi\n"
        f'exec {command} governance hook --event {event} "$@"\n'
    )


def _is_managed_hook(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        return MARKER in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def install_hooks(repo: Path | None = None, *, mcp_bin: str | None = None) -> dict[str, Any]:
    """Install managed global hooks, refusing to overwrite a foreign hooks path."""
    root = _require_repo_root(repo)
    hooks_path = governance_home() / HOOKS_DIR
    hooks_value = str(hooks_path)
    current = _global_hooks_path()
    existing = _load_global()

    if existing is not None and existing.hooks_path != hooks_value:
        raise GovernanceError("a different mcp-hub hook installation is already recorded")
    if current not in (None, hooks_value) and (
        existing is None or existing.previous_hooks_path != current
    ):
        raise GovernanceError(
            f"global core.hooksPath is already set to {current}; uninstall or resolve it first"
        )

    previous = existing.previous_hooks_path if existing is not None else current
    hooks_path.mkdir(parents=True, exist_ok=True)
    command = _hook_command(mcp_bin)
    for event in HOOK_EVENTS:
        path = hooks_path / event
        _atomic_write_text(path, _render_hook(event, command))
        path.chmod(0o755)
    _set_global_hooks_path(hooks_value)
    config = GlobalConfig(hooks_path=hooks_value, previous_hooks_path=previous)
    _atomic_write_text(_global_config_path(), json.dumps(config.as_dict(), indent=2) + "\n")
    return {
        "command": "governance-hooks-install",
        "status": "installed",
        "exit_code": EXIT_OK,
        "repo": str(root),
        "hooks_path": hooks_value,
        "previous_hooks_path": previous,
        "events": list(HOOK_EVENTS),
    }


def check_hooks(repo: Path | None = None) -> dict[str, Any]:
    """Check that our global hooks are installed and still ours."""
    root = _require_repo_root(repo)
    config = _load_global()
    if config is None:
        return {
            "command": "governance-hooks-check",
            "status": "not_installed",
            "reason": "no_global_config",
            "exit_code": EXIT_RUNTIME,
            "repo": str(root),
        }
    current = _global_hooks_path()
    hooks_path = Path(config.hooks_path)
    files = {event: _is_managed_hook(hooks_path / event) for event in config.installed_events}
    healthy = current == config.hooks_path and all(files.values())
    return {
        "command": "governance-hooks-check",
        "status": "installed" if healthy else "broken",
        "exit_code": EXIT_OK if healthy else EXIT_RUNTIME,
        "repo": str(root),
        "hooks_path": config.hooks_path,
        "current_hooks_path": current,
        "files": files,
    }


def uninstall_hooks(repo: Path | None = None) -> dict[str, Any]:
    """Remove only our managed hook files and restore the previous hooks path."""
    root = _require_repo_root(repo)
    config = _load_global()
    if config is None:
        return {
            "command": "governance-hooks-uninstall",
            "status": "skipped",
            "reason": "no_global_config",
            "exit_code": EXIT_OK,
            "repo": str(root),
        }
    hooks_path = Path(config.hooks_path)
    removed: list[str] = []
    preserved: list[str] = []
    for event in HOOK_EVENTS:
        path = hooks_path / event
        if not path.exists():
            continue
        if _is_managed_hook(path):
            path.unlink()
            removed.append(event)
        else:
            preserved.append(event)
    current = _global_hooks_path()
    if current == config.hooks_path:
        _set_global_hooks_path(config.previous_hooks_path)
    try:
        hooks_path.rmdir()
    except OSError:
        pass
    _global_config_path().unlink(missing_ok=True)
    return {
        "command": "governance-hooks-uninstall",
        "status": "uninstalled",
        "exit_code": EXIT_OK,
        "repo": str(root),
        "removed": removed,
        "preserved": preserved,
        "restored_hooks_path": config.previous_hooks_path,
    }


def _diff_paths(repo: Path, *args: str) -> list[str]:
    proc = _git(["-C", str(repo), "diff", "--name-only", "-z", *args], check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        raise GovernanceError(f"git diff failed: {detail}", code=EXIT_RUNTIME)
    return [item for item in proc.stdout.split("\x00") if item]


def _changed_paths(repo: Path, event: str) -> list[str]:
    if event == "pre-commit":
        return _diff_paths(repo, "--cached")
    upstream = _git(
        ["-C", str(repo), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        check=False,
    )
    if upstream.returncode == 0 and upstream.stdout.strip():
        return _diff_paths(repo, f"{upstream.stdout.strip()}..HEAD")
    parent = _git(["-C", str(repo), "rev-parse", "--verify", "HEAD^"], check=False)
    if parent.returncode == 0:
        return _diff_paths(repo, "HEAD^", "HEAD")
    staged = _diff_paths(repo, "--cached")
    if staged:
        return staged
    proc = _git(["-C", str(repo), "ls-files", "-z"], check=False)
    return [item for item in proc.stdout.split("\x00") if item]


def _skip(event: str, reason: str, repo: Path | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "command": "governance-hook",
        "status": "skipped",
        "reason": reason,
        "exit_code": EXIT_OK,
        "event": event,
        "repo": str(repo) if repo is not None else None,
    }
    payload.update(extra)
    return payload


def run_hook(
    event: str,
    *,
    repo: Path | None = None,
    lang: str | None = None,
    mcp_version: str = __version__,
) -> dict[str, Any]:
    """Run one bounded incremental governance check for a Git hook event."""
    if event not in HOOK_EVENTS:
        raise GovernanceError(f"unsupported hook event: {event}")

    root = find_repo_root(repo)
    if root is None:
        return _skip(event, "not_a_git_repo", repo)
    config = load_project(root)
    if config is None:
        return _skip(event, "unbound_repository", root)

    playbook = root / config.playbook
    if not playbook.is_file():
        return _skip(event, "no_playbook", root, tenant=config.tenant)

    changed = _changed_paths(root, event)
    base = Path(config.playbook).parent
    try:
        linked = watch._linked_paths(playbook, playbook.read_text(encoding="utf-8"), ())
    except (watch.WatchError, OSError) as exc:
        code = getattr(exc, "code", EXIT_RUNTIME)
        raise GovernanceError(str(exc), code=int(code)) from exc
    relevant = {Path(config.playbook).as_posix()}
    for name in linked:
        relevant.add((base / name).as_posix())
        relevant.add(name)
    touched = sorted(set(changed) & relevant)
    if not touched:
        return _skip(
            event,
            "no_relevant_changes",
            root,
            tenant=config.tenant,
            changed_files=changed,
            relevant_files=sorted(relevant),
        )

    context = config.context(lang)
    try:
        text = playbook.read_text(encoding="utf-8")
        compiled = arflow.build(text, name=playbook.stem, context=context, overrides={})
        if not compiled.ok:
            return {
                "command": "governance-hook",
                "status": "failed",
                "reason": "playbook_check_failed",
                "exit_code": EXIT_RUNTIME,
                "event": event,
                "repo": str(root),
                "tenant": config.tenant,
                "issues": [issue.message for issue in compiled.issues],
            }
        catalog = Catalog(root / config.audit_db)
        digests = {name: watch._file_digest(path) for name, path in linked.items()}
        linked_map = watch._linked_fingerprints(compiled, digests)
        fingerprints = incremental.playbook_fingerprints(compiled, catalog)
        fingerprints = incremental.with_linked_fingerprints(fingerprints, linked_map)
        result = incremental.run_incremental(
            catalog,
            compiled,
            incremental=True,
            force_full=False,
            fingerprints=fingerprints,
        )
    except (watch.WatchError, arflow.ArflowError, arabic_dsl.DslError, OSError) as exc:
        code = getattr(exc, "code", EXIT_RUNTIME)
        raise GovernanceError(str(exc), code=int(code)) from exc

    return {
        "command": "governance-hook",
        "status": "ok" if result["exit_code"] == EXIT_OK else "failed",
        "exit_code": int(result["exit_code"]),
        "event": event,
        "repo": str(root),
        "tenant": config.tenant,
        "session": config.session,
        "actor": config.actor,
        "lang": resolve_lang(context.lang),
        "playbook": config.playbook,
        "changed_files": changed,
        "relevant_files": touched,
        "audit_db": config.audit_db,
        "mcp_hub_version": mcp_version,
        "incremental": {
            "adopted": sum(1 for step in result["steps"] if step.get("state") == "adopted"),
            "rerun": sum(1 for step in result["steps"] if step.get("state") == "rerun"),
            "status": result["status"],
        },
    }
