"""Incremental Arabic testing for ``.arflow`` playbooks.

Fingerprints are derived from the compiled DSL plan (never from raw comments or
insignificant whitespace), the active policy snapshot, the catalog state and the
verbatim tenant/session/actor context. Unchanged prior steps can be adopted from
``arflow_incremental_audit``; the first changed step and every later step are
re-executed through the ordinary Arabic DSL gateway with fail-fast preserved.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__, arabic_dsl, arflow, catalog_tools, mcp_stateless
from .arabic_runtime import resolve_lang
from .database import Catalog

__all__ = [
    "AUDIT_TABLE",
    "Fingerprints",
    "STATES",
    "catalog_fingerprint",
    "policy_fingerprint",
    "step_fingerprint",
    "playbook_fingerprints",
    "with_linked_fingerprints",
    "load_previous",
    "run_incremental",
]

AUDIT_TABLE = "arflow_incremental_audit"
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_BAD_INPUT = 2
STATES = frozenset({"rerun", "adopted", "failed", "rejected"})
_DENIAL_CODES = frozenset({mcp_stateless.POLICY_DENIED, mcp_stateless.TASK_DENIED})
_CATALOG_TABLES = (
    "servers",
    "provenance",
    "field_provenance",
    "duplicate_candidates",
    "enrichments",
    "saved_searches",
    "custom_categories",
    "category_overrides",
    "alerts",
)


@dataclass(frozen=True, slots=True)
class Fingerprints:
    """Deterministic digests for one compiled playbook and its context."""

    playbook: str
    policy: str
    catalog: str
    steps: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "playbook": self.playbook,
            "policy": self.policy,
            "catalog": self.catalog,
            "steps": list(self.steps),
        }


def _sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def policy_fingerprint() -> str:
    return _sha256(catalog_tools.policy_snapshot())


def catalog_fingerprint(catalog: Catalog) -> str:
    """Hash source tables only; audit tables must not trigger re-runs."""
    parts: list[Any] = []
    with sqlite3.connect(str(catalog.path)) as con:
        for table in _CATALOG_TABLES:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                continue
            columns = [str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")]
            marker = (
                "updated_at"
                if "updated_at" in columns
                else "created_at"
                if "created_at" in columns
                else ""
            )
            if marker:
                count, latest = con.execute(
                    f"SELECT COUNT(*), COALESCE(MAX({marker}), '') FROM {table}"
                ).fetchone()
            else:
                count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                latest = ""
            parts.append([table, columns, int(count), str(latest)])
    return _sha256(parts)


def _canonical_step(step: arflow.Step) -> dict[str, Any]:
    plan = step.command.plan()
    return {
        "text": " ".join(step.raw.split()),
        "method": plan["method"],
        "params": plan["params"],
        "approval_required": bool(plan.get("approval_required", False)),
        "optional": step.optional,
        "expectation": step.expectation.as_dict() if step.expectation is not None else None,
    }


def step_fingerprint(
    step: arflow.Step,
    *,
    policy_fp: str,
    catalog_fp: str,
    context: arabic_dsl.DslContext,
) -> str:
    """Fingerprint one step against policy, catalog and verbatim scope."""
    return _sha256(
        {
            "step": _canonical_step(step),
            "context": {
                "tenant_id": context.tenant_id,
                "session_id": context.session_id,
                "actor": context.actor,
                "lang": resolve_lang(context.lang),
            },
            "policy": policy_fp,
            "catalog": catalog_fp,
        }
    )


def playbook_fingerprints(
    compiled: arflow.Compiled,
    catalog: Catalog,
) -> Fingerprints:
    """Fingerprint the whole playbook plus each step independently."""
    playbook = compiled.playbook
    if playbook is None:
        raise arflow.ArflowError("cannot fingerprint an invalid playbook")
    policy_fp = policy_fingerprint()
    catalog_fp = catalog_fingerprint(catalog)
    steps = tuple(
        step_fingerprint(step, policy_fp=policy_fp, catalog_fp=catalog_fp, context=playbook.context)
        for step in playbook.steps
    )
    playbook_fp = _sha256(
        {
            "name": playbook.name,
            "text": [" ".join(step.raw.split()) for step in playbook.steps],
            "variables": playbook.variables,
            "fingerprints": list(steps),
        }
    )
    return Fingerprints(playbook=playbook_fp, policy=policy_fp, catalog=catalog_fp, steps=steps)


def with_linked_fingerprints(
    fingerprints: Fingerprints,
    linked: Mapping[int, str],
) -> Fingerprints:
    """Overlay per-step digests from linked files onto Phase 9 fingerprints.

    Only steps that mention a linked file receive a different digest, so an
    unrelated file change does not invalidate the whole playbook.
    """
    if not linked:
        return fingerprints
    steps = tuple(
        _sha256({"step": base, "linked": linked[index + 1]}) if index + 1 in linked else base
        for index, base in enumerate(fingerprints.steps)
    )
    playbook = _sha256(
        {
            "base": fingerprints.playbook,
            "linked": [linked.get(index + 1, "") for index in range(len(steps))],
        }
    )
    return replace(fingerprints, playbook=playbook, steps=steps)


def _ensure_audit(con: sqlite3.Connection) -> None:
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "run_id INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, "
        "playbook TEXT NOT NULL, "
        "step_index INTEGER NOT NULL, "
        "fingerprint TEXT NOT NULL, "
        "state TEXT NOT NULL, "
        "status TEXT NOT NULL, "
        "exit_code INTEGER NOT NULL, "
        "result_json TEXT NOT NULL, "
        "original TEXT NOT NULL, "
        "tenant_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, "
        "actor TEXT NOT NULL, "
        "lang TEXT NOT NULL)"
    )
    con.execute(
        f"CREATE INDEX IF NOT EXISTS {AUDIT_TABLE}_lookup "
        f"ON {AUDIT_TABLE} (playbook, step_index, id DESC)"
    )


def load_previous(db_path: str, playbook: str) -> dict[int, dict[str, Any]]:
    """Load the newest successful state for each step of a playbook."""
    if not Path(db_path).exists():
        return {}
    previous: dict[int, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as con:
        _ensure_audit(con)
        rows = con.execute(
            f"SELECT step_index, fingerprint, state, status, exit_code, result_json "
            f"FROM {AUDIT_TABLE} WHERE playbook=? ORDER BY id DESC",
            (playbook,),
        ).fetchall()
    for row in rows:
        index = int(row[0])
        if index in previous:
            continue
        previous[index] = {
            "fingerprint": str(row[1]),
            "state": str(row[2]),
            "status": str(row[3]),
            "exit_code": int(row[4]),
            "result": json.loads(str(row[5])),
        }
    return previous


def _record_step(
    db_path: str,
    *,
    run_id: int,
    playbook: str,
    step_index: int,
    fingerprint: str,
    state: str,
    status: str,
    exit_code: int,
    result: dict[str, Any],
    original: str,
    context: arabic_dsl.DslContext,
) -> None:
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as con:
        _ensure_audit(con)
        con.execute(
            f"INSERT INTO {AUDIT_TABLE} "
            "(run_id, created_at, playbook, step_index, fingerprint, state, status, "
            "exit_code, result_json, original, tenant_id, session_id, actor, lang) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                created,
                playbook,
                step_index,
                fingerprint,
                state,
                status,
                exit_code,
                json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
                original,
                context.tenant_id,
                context.session_id,
                context.actor,
                resolve_lang(context.lang),
            ),
        )


def _error_code(entry: dict[str, Any]) -> int | None:
    error = entry.get("error")
    if not isinstance(error, dict):
        return None
    value = error.get("code")
    return (
        int(value) if isinstance(value, (int, str)) and str(value).lstrip("-").isdigit() else None
    )


def _is_denial(entry: dict[str, Any]) -> bool:
    return _error_code(entry) in _DENIAL_CODES


def _expectation_ok(step: arflow.Step, entry: dict[str, Any]) -> bool:
    expectation = step.expectation
    if expectation is None:
        return True
    exit_code = int(entry.get("exit_code", 0))
    status = str(entry.get("status", ""))
    kind = expectation.kind
    if kind == "success":
        return exit_code == EXIT_OK and status == "ok"
    if kind == "failure":
        return exit_code != EXIT_OK or status in {"failed", "optional_failed"}
    if kind == "status":
        return status == expectation.value
    if kind == "exit":
        return exit_code == int(expectation.value)
    if kind == "policy_denial":
        return (
            _error_code(entry) == mcp_stateless.POLICY_DENIED
            and int(expectation.value) == mcp_stateless.POLICY_DENIED
        )
    if kind == "task_denial":
        return (
            _error_code(entry) == mcp_stateless.TASK_DENIED
            and int(expectation.value) == mcp_stateless.TASK_DENIED
        )
    return False


def _adoptable(previous: dict[str, Any] | None, fingerprint: str) -> bool:
    return (
        previous is not None
        and previous.get("fingerprint") == fingerprint
        and previous.get("state") in {"rerun", "adopted"}
        and previous.get("status") == "ok"
    )


def run_incremental(
    catalog: Catalog,
    compiled: arflow.Compiled,
    *,
    incremental: bool = True,
    force_full: bool = False,
    fingerprints: Fingerprints | None = None,
) -> dict[str, Any]:
    """Run a compiled playbook, adopting unchanged successful prior steps."""
    playbook = compiled.playbook
    if not compiled.ok or playbook is None:
        raise arflow.ArflowError("cannot run a playbook with check issues")
    active_fingerprints = fingerprints or playbook_fingerprints(compiled, catalog)
    context = playbook.context
    previous = load_previous(str(catalog.path), playbook.name)
    adopt = incremental and not force_full
    first_changed = len(playbook.steps)
    if adopt:
        for index, fingerprint in enumerate(active_fingerprints.steps):
            if not _adoptable(previous.get(index + 1), fingerprint):
                first_changed = index
                break

    run_id = int(datetime.now(timezone.utc).timestamp() * 1000)
    steps: list[dict[str, Any]] = []
    optional_failures: list[int] = []
    adopted_count = 0
    rerun_count = 0
    exit_code = EXIT_OK
    stopped_at: int | None = None

    for index, step in enumerate(playbook.steps):
        fingerprint = active_fingerprints.steps[index]
        hard_stop = False
        if adopt and index < first_changed:
            entry = dict(previous[index + 1]["result"])
            state = "adopted"
            adopted_count += 1
        else:
            payload = arabic_dsl.execute([step.command], catalog)
            raw_exit = int(payload["exit_code"])
            entry = dict(payload["steps"][0])
            if raw_exit != EXIT_OK:
                state = "rejected" if _is_denial(entry) else "failed"
                if step.optional:
                    entry["status"] = "optional_failed"
                    optional_failures.append(step.step)
                else:
                    entry["status"] = "failed"
                    exit_code = raw_exit
                    stopped_at = step.step
                    hard_stop = True
            else:
                state = "rerun"
                rerun_count += 1

        entry.update(
            {
                "playbook_step": step.step,
                "line": step.line,
                "optional": step.optional,
                "original": step.original,
                "state": state,
            }
        )
        if not _expectation_ok(step, entry) and step.expectation is not None:
            state = "failed"
            entry["status"] = "failed"
            entry["expectation_error"] = f"expectation failed: {step.expectation.raw}"
            if not step.optional:
                exit_code = EXIT_RUNTIME
                stopped_at = step.step
                hard_stop = True
        entry["state"] = state
        _record_step(
            str(catalog.path),
            run_id=run_id,
            playbook=playbook.name,
            step_index=step.step,
            fingerprint=fingerprint,
            state=state,
            status=str(entry.get("status", "failed")),
            exit_code=int(entry.get("exit_code", exit_code)),
            result=entry,
            original=step.original,
            context=context,
        )
        steps.append(entry)
        if hard_stop:
            break

    return {
        "command": "arflow-incremental",
        "status": "ok" if exit_code == EXIT_OK else "failed",
        "playbook": playbook.name,
        "incremental": adopt,
        "force_full": force_full,
        "changed_from": None if first_changed >= len(playbook.steps) else first_changed + 1,
        "fingerprints": active_fingerprints.as_dict(),
        "steps": steps,
        "adopted_count": adopted_count,
        "rerun_count": rerun_count,
        "stopped_at": stopped_at,
        "optional_failures": optional_failures,
        "exit_code": exit_code,
        "version": __version__,
    }
