"""Bounded file watch for ``.arflow`` playbooks.

This layer watches the playbook plus explicitly linked files, maps each linked
file to the steps that mention it, and reuses the Phase 9 incremental engine to
re-run only affected steps. Every run still goes through the unified Arabic DSL
gateway; this module never calls an MCP handler directly and never changes the
pinned manifest, tool specs, resource specs or handler names.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__, arabic_dsl, arflow, incremental
from .arabic_runtime import resolve_lang
from .database import Catalog

__all__ = [
    "AUDIT_TABLE",
    "WatchError",
    "MAX_CYCLES",
    "MAX_WATCH_FILES",
    "MAX_FILE_BYTES",
    "run_watch",
]

AUDIT_TABLE = "arflow_watch_audit"
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_BAD_INPUT = 2
MAX_CYCLES = 1000
MAX_WATCH_FILES = 64
MAX_FILE_BYTES = 1024 * 1024
MAX_INTERVAL_SECONDS = 60.0
MAX_SETTLE_SECONDS = 10.0
MAX_REASON_LENGTH = 4096
_DIRECTIVE_RE = re.compile(r"^(?:watch|راقب)\s*[:：]\s*(.+)$", re.IGNORECASE)
_STOP_OK = "no_changes"
_STOP_FAILED = "failed"
_STOP_MAX = "max_cycles"


class WatchError(ValueError):
    """A watch configuration or file error with a stable CLI exit code."""

    def __init__(self, message: str, *, code: int = EXIT_BAD_INPUT) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """One consistent read of a playbook, its linked files and fingerprints."""

    path: Path
    text: str
    compiled: arflow.Compiled
    fingerprints: incremental.Fingerprints
    files: dict[str, str]
    linked: dict[int, str]


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _file_digest(path: Path) -> str:
    """Hash one watched file; size is bounded before the file is read."""
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise WatchError(f"watched file too large: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_relative(value: str) -> str:
    """Keep linked files relative to the playbook directory."""
    candidate = value.strip().strip("'\"")
    if not candidate or len(candidate) > 512 or "\x00" in candidate:
        raise WatchError("invalid watched file path")
    path = Path(candidate)
    if path.is_absolute() or ".." in path.parts:
        raise WatchError(f"watched file must stay relative to the playbook: {candidate}")
    return candidate


def _linked_paths(playbook: Path, text: str, explicit: tuple[str, ...]) -> dict[str, Path]:
    """Collect linked files from inline comment directives and CLI flags."""
    names: list[str] = []
    for source in text.splitlines():
        stripped = source.strip()
        if not stripped.startswith(("#", "//")):
            continue
        body = stripped.lstrip("#/").strip()
        match = _DIRECTIVE_RE.match(body)
        if match is not None:
            names.append(match.group(1))
    names.extend(explicit)

    unique = list(dict.fromkeys(_validate_relative(name) for name in names))
    if len(unique) > MAX_WATCH_FILES:
        raise WatchError(f"too many watched files (max {MAX_WATCH_FILES})")

    base = playbook.parent
    resolved: dict[str, Path] = {}
    for name in unique:
        target = base / name
        if not target.is_file():
            raise WatchError(f"watched file not found: {name}", code=EXIT_RUNTIME)
        resolved[name] = target
    return resolved


def _linked_fingerprints(compiled: arflow.Compiled, digests: dict[str, str]) -> dict[int, str]:
    """Map a linked-file digest to each step that mentions that file."""
    playbook = compiled.playbook
    if playbook is None:
        return {}
    linked: dict[int, str] = {}
    for step in playbook.steps:
        params = json.dumps(step.command.params, ensure_ascii=False, sort_keys=True, default=str)
        haystack = f"{step.raw}\n{params}"
        matched = sorted(
            digest
            for name, digest in digests.items()
            if name in haystack or Path(name).name in haystack
        )
        if matched:
            linked[step.step] = _sha256({"linked_files": matched})
    return linked


def _snapshot(
    catalog: Catalog,
    *,
    playbook: Path,
    explicit: tuple[str, ...],
    context: arabic_dsl.DslContext,
    overrides: dict[str, str],
) -> _Snapshot:
    """Read and fingerprint a playbook plus its linked files."""
    if not playbook.is_file():
        raise WatchError(f"playbook not found: {playbook}", code=EXIT_RUNTIME)
    text = playbook.read_text(encoding="utf-8")
    compiled = arflow.build(text, name=playbook.stem, context=context, overrides=overrides)
    if not compiled.ok:
        first = compiled.issues[0].message if compiled.issues else "playbook invalid"
        raise WatchError(first)

    files = _linked_paths(playbook, text, explicit)
    digests = {name: _file_digest(path) for name, path in files.items()}
    linked = _linked_fingerprints(compiled, digests)
    fingerprints = incremental.playbook_fingerprints(compiled, catalog)
    fingerprints = incremental.with_linked_fingerprints(fingerprints, linked)
    return _Snapshot(
        path=playbook,
        text=text,
        compiled=compiled,
        fingerprints=fingerprints,
        files=digests,
        linked=linked,
    )


def _changed_files(current: dict[str, str], previous: dict[str, str] | None) -> list[str]:
    if previous is None:
        return sorted(current)
    return sorted(name for name, digest in current.items() if previous.get(name) != digest)


def _ensure_audit(con: sqlite3.Connection) -> None:
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "run_id INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, "
        "cycle INTEGER NOT NULL, "
        "playbook TEXT NOT NULL, "
        "original TEXT NOT NULL, "
        "watched_files_json TEXT NOT NULL, "
        "linked_json TEXT NOT NULL, "
        "changed_json TEXT NOT NULL, "
        "report_json TEXT NOT NULL, "
        "ran INTEGER NOT NULL, "
        "status TEXT NOT NULL, "
        "exit_code INTEGER NOT NULL, "
        "tenant_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, "
        "actor TEXT NOT NULL, "
        "reason TEXT NOT NULL, "
        "lang TEXT NOT NULL)"
    )


def _record_cycle(
    db_path: str,
    *,
    run_id: int,
    cycle: int,
    snapshot: _Snapshot,
    changed: list[str],
    report: dict[str, Any] | None,
    ran: bool,
    status: str,
    exit_code: int,
    context: arabic_dsl.DslContext,
    reason: str,
) -> None:
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as con:
        _ensure_audit(con)
        con.execute(
            f"INSERT INTO {AUDIT_TABLE} "
            "(run_id, created_at, cycle, playbook, original, watched_files_json, "
            "linked_json, changed_json, report_json, ran, status, exit_code, "
            "tenant_id, session_id, actor, reason, lang) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                created,
                cycle,
                snapshot.path.stem,
                snapshot.text,
                json.dumps(snapshot.files, ensure_ascii=False, sort_keys=True),
                json.dumps(snapshot.linked, ensure_ascii=False, sort_keys=True),
                json.dumps(changed, ensure_ascii=False),
                json.dumps(report or {}, ensure_ascii=False, sort_keys=True, default=str),
                int(ran),
                status,
                exit_code,
                context.tenant_id,
                context.session_id,
                context.actor,
                reason,
                resolve_lang(context.lang),
            ),
        )


def _validate_bounds(max_cycles: int, interval: float, settle: float, reason: str) -> None:
    if not isinstance(max_cycles, int) or not 1 <= max_cycles <= MAX_CYCLES:
        raise WatchError(f"--max-cycles must be between 1 and {MAX_CYCLES}")
    if (
        not math.isfinite(interval)
        or not 0 <= interval <= MAX_INTERVAL_SECONDS
        or not math.isfinite(settle)
        or not 0 <= settle <= MAX_SETTLE_SECONDS
    ):
        raise WatchError("interval and settle must be finite non-negative numbers")
    if len(reason) > MAX_REASON_LENGTH:
        raise WatchError("reason is too long")


def run_watch(
    catalog: Catalog,
    *,
    playbook: str,
    watch_files: tuple[str, ...] | list[str] = (),
    context: arabic_dsl.DslContext | None = None,
    overrides: dict[str, str] | None = None,
    max_cycles: int = 1,
    interval: float = 0.0,
    settle: float = 0.0,
    reason: str = "arflow watch",
) -> dict[str, Any]:
    """Watch a playbook for bounded cycles and rerun only affected steps."""
    _validate_bounds(max_cycles, interval, settle, reason)
    watch_context = context or arabic_dsl.DslContext()
    watch_overrides = dict(overrides or {})
    path = Path(playbook)
    explicit = tuple(watch_files)
    run_id = int(datetime.now(timezone.utc).timestamp() * 1000)

    previous: _Snapshot | None = None
    cycles: list[dict[str, Any]] = []
    final_exit = EXIT_OK
    stop_reason = _STOP_MAX
    toctou: list[str] = []
    last_files: dict[str, str] = {}
    last_linked: dict[int, str] = {}

    for cycle in range(1, max_cycles + 1):
        if cycle > 1 and interval:
            time.sleep(interval)
        snapshot = _snapshot(
            catalog,
            playbook=path,
            explicit=explicit,
            context=watch_context,
            overrides=watch_overrides,
        )
        changed = _changed_files(snapshot.files, None if previous is None else previous.files)
        fingerprint_changed = (
            previous is None or snapshot.fingerprints.as_dict() != previous.fingerprints.as_dict()
        )
        if previous is not None and (changed or fingerprint_changed) and settle:
            time.sleep(settle)
            snapshot = _snapshot(
                catalog,
                playbook=path,
                explicit=explicit,
                context=watch_context,
                overrides=watch_overrides,
            )
            changed = _changed_files(snapshot.files, previous.files)
            fingerprint_changed = snapshot.fingerprints.as_dict() != previous.fingerprints.as_dict()

        last_files = snapshot.files
        last_linked = snapshot.linked
        ran = bool(changed or fingerprint_changed)
        report: dict[str, Any] | None = None
        cycle_status = "ok"
        cycle_exit = EXIT_OK

        if ran:
            report = incremental.run_incremental(
                catalog, snapshot.compiled, fingerprints=snapshot.fingerprints
            )
            try:
                after = _snapshot(
                    catalog,
                    playbook=path,
                    explicit=explicit,
                    context=watch_context,
                    overrides=watch_overrides,
                )
            except WatchError:
                toctou.append("playbook fingerprint changed during the run")
            else:
                if after.files != snapshot.files:
                    toctou.append("linked files changed during the run")
                if after.fingerprints.as_dict() != snapshot.fingerprints.as_dict():
                    toctou.append("playbook fingerprint changed during the run")
            if toctou:
                report["status"] = "failed"
                report["exit_code"] = EXIT_RUNTIME
                report["toctou_mismatch"] = list(toctou)
                cycle_status = "failed"
                cycle_exit = EXIT_RUNTIME
            else:
                cycle_exit = int(report.get("exit_code", EXIT_OK))
                cycle_status = "ok" if cycle_exit == EXIT_OK else "failed"

        _record_cycle(
            str(catalog.path),
            run_id=run_id,
            cycle=cycle,
            snapshot=snapshot,
            changed=changed,
            report=report,
            ran=ran,
            status=cycle_status,
            exit_code=cycle_exit,
            context=watch_context,
            reason=reason,
        )
        cycles.append(
            {
                "cycle": cycle,
                "changed_files": changed,
                "fingerprint_changed": fingerprint_changed,
                "ran": ran,
                "status": cycle_status,
                "exit_code": cycle_exit,
                "report": report,
            }
        )
        previous = snapshot

        if cycle_exit != EXIT_OK:
            final_exit = cycle_exit
            stop_reason = _STOP_FAILED
            break
        if not ran:
            stop_reason = _STOP_OK
            break

    return {
        "command": "arflow-watch",
        "status": "ok" if final_exit == EXIT_OK else "failed",
        "playbook": path.stem,
        "file": str(path),
        "cycle_count": len(cycles),
        "max_cycles": max_cycles,
        "interval": interval,
        "settle": settle,
        "reason": reason,
        "watched_files": dict(sorted(last_files.items())),
        "linked_fingerprints": {
            str(index): digest for index, digest in sorted(last_linked.items())
        },
        "stop_reason": stop_reason,
        "toctou_mismatch": toctou,
        "cycles": cycles,
        "exit_code": final_exit,
        "lang": resolve_lang(watch_context.lang),
        "version": __version__,
    }
