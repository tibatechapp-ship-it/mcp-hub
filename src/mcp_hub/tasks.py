"""Durable local agent tasks in SQLite.

Tasks are never global: every read, update and cancel requires both a tenant id
and a session id. Terminal tasks are cleaned up explicitly, and running tasks
with an expired lease are reconciled back to ``queued`` so an interrupted worker
can resume them safely.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Catalog
from .redact import redact

__all__ = [
    "TaskError",
    "TaskStore",
    "TERMINAL_STATUSES",
    "VALID_STATUSES",
]

VALID_STATUSES = frozenset(
    {"queued", "running", "awaiting_approval", "completed", "cancelled", "failed"}
)
TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")


class TaskError(ValueError):
    """Invalid task input or a task outside the requested scope."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_id(label: str, value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise TaskError(f"{label} must be 1-128 safe characters")
    return value


def _canonical(value: Mapping[str, Any] | None) -> str:
    data = dict(value or {})
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _safe_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskError(f"{label} must be a string")
    if len(value) > 2000:
        raise TaskError(f"{label} is too long")
    return _no_secret(value)


def _no_secret(value: str) -> str:
    cleaned = redact(value)
    if cleaned != value:
        return cleaned
    return value


class TaskStore:
    """SQLite-backed task state with tenant/session isolation."""

    def __init__(self, catalog: Catalog, *, initialize: bool = True) -> None:
        self.catalog = catalog
        if initialize:
            with self.catalog.connect() as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS agent_tasks ("
                    "task_id TEXT PRIMARY KEY, "
                    "tenant_id TEXT NOT NULL, "
                    "session_id TEXT NOT NULL, "
                    "kind TEXT NOT NULL, "
                    "status TEXT NOT NULL, "
                    "payload_json TEXT NOT NULL DEFAULT '{}', "
                    "result_json TEXT, "
                    "error TEXT NOT NULL DEFAULT '', "
                    "attempts INTEGER NOT NULL DEFAULT 0, "
                    "max_attempts INTEGER NOT NULL DEFAULT 3, "
                    "lease_owner TEXT, "
                    "lease_until TEXT, "
                    "next_run_at TEXT, "
                    "created_at TEXT NOT NULL, "
                    "updated_at TEXT NOT NULL, "
                    "finished_at TEXT)"
                )
                con.execute(
                    "CREATE TABLE IF NOT EXISTS agent_task_events ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "task_id TEXT NOT NULL, "
                    "tenant_id TEXT NOT NULL, "
                    "session_id TEXT NOT NULL, "
                    "event TEXT NOT NULL, "
                    "detail TEXT NOT NULL DEFAULT '', "
                    "created_at TEXT NOT NULL)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_tasks_scope "
                    "ON agent_tasks(tenant_id, session_id, status)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_tasks_lease "
                    "ON agent_tasks(status, lease_until)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_task_events_scope "
                    "ON agent_task_events(tenant_id, session_id, task_id, id)"
                )

    @staticmethod
    def _record_event(
        con: Any, task_id: str, tenant_id: str, session_id: str, event: str, detail: str
    ) -> None:
        con.execute(
            "INSERT INTO agent_task_events "
            "(task_id,tenant_id,session_id,event,detail,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, tenant_id, session_id, event, _no_secret(detail)[:500], _utc_now()),
        )

    @staticmethod
    def _to_dict(row: Any, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
        result = data.pop("result_json", None)
        data["result"] = json.loads(result) if result else None
        if events is not None:
            data["events"] = events
        return data

    def create(
        self,
        tenant_id: str,
        session_id: str,
        kind: str = "tool_chain",
        payload: Mapping[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id)
        kind_value = _validate_id("kind", kind)
        identifier = _validate_id("task_id", task_id) if task_id else uuid.uuid4().hex
        now = _utc_now()
        with self.catalog.connect() as con:
            con.execute(
                "INSERT INTO agent_tasks "
                "(task_id,tenant_id,session_id,kind,status,payload_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (identifier, tenant, session, kind_value, "queued", _canonical(payload), now, now),
            )
            self._record_event(con, identifier, tenant, session, "created", kind_value)
            row = con.execute("SELECT * FROM agent_tasks WHERE task_id=?", (identifier,)).fetchone()
        return self._to_dict(row)

    def _scoped_row(self, con: Any, task_id: str, tenant_id: str, session_id: str) -> Any:
        row = con.execute(
            "SELECT * FROM agent_tasks WHERE task_id=? AND tenant_id=? AND session_id=?",
            (task_id, tenant_id, session_id),
        ).fetchone()
        if row is None:
            raise TaskError("task not found or outside tenant/session scope")
        return row

    def get(
        self, task_id: str, tenant_id: str, session_id: str, *, include_events: bool = True
    ) -> dict[str, Any]:
        identifier = _validate_id("task_id", task_id)
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id)
        with self.catalog.connect() as con:
            row = self._scoped_row(con, identifier, tenant, session)
            events: list[dict[str, Any]] = []
            if include_events:
                rows = con.execute(
                    "SELECT event,detail,created_at FROM agent_task_events "
                    "WHERE task_id=? AND tenant_id=? AND session_id=? ORDER BY id ASC",
                    (identifier, tenant, session),
                ).fetchall()
                events = [dict(item) for item in rows]
        return self._to_dict(row, events if include_events else None)

    def update(
        self,
        task_id: str,
        tenant_id: str,
        session_id: str,
        *,
        status: str | None = None,
        payload: Mapping[str, Any] | None = None,
        result: Any = None,
        error: str | None = None,
        attempts: int | None = None,
        lease_owner: str | None = None,
        lease_until: str | None = None,
        next_run_at: str | None = None,
        event: str | None = None,
    ) -> dict[str, Any]:
        identifier = _validate_id("task_id", task_id)
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id)
        if status is not None and status not in VALID_STATUSES:
            raise TaskError(f"invalid task status: {status}")
        if attempts is not None and (isinstance(attempts, bool) or attempts < 0):
            raise TaskError("attempts must be a non-negative integer")
        safe_error = _safe_text(error, "error")
        lease = _safe_text(lease_owner, "lease_owner")
        lease_time = _safe_text(lease_until, "lease_until")
        next_time = _safe_text(next_run_at, "next_run_at")
        event_name = _safe_text(event, "event") or status or "updated"
        result_json = _canonical(result) if result is not None else None
        now = _utc_now()
        with self.catalog.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = self._scoped_row(con, identifier, tenant, session)
            merged_payload = dict(json.loads(row["payload_json"] or "{}"))
            if payload:
                merged_payload.update(dict(payload))
            new_status = status or str(row["status"])
            finished_at = now if new_status in TERMINAL_STATUSES else None
            con.execute(
                "UPDATE agent_tasks SET status=?, payload_json=?, result_json=?, "
                "error=?, attempts=?, lease_owner=?, lease_until=?, next_run_at=?, "
                "updated_at=?, finished_at=COALESCE(finished_at, ?) "
                "WHERE task_id=? AND tenant_id=? AND session_id=?",
                (
                    new_status,
                    _canonical(merged_payload),
                    result_json if result is not None else row["result_json"],
                    safe_error if error is not None else row["error"],
                    attempts if attempts is not None else row["attempts"],
                    lease if lease_owner is not None else row["lease_owner"],
                    lease_time if lease_until is not None else row["lease_until"],
                    next_time if next_run_at is not None else row["next_run_at"],
                    now,
                    finished_at,
                    identifier,
                    tenant,
                    session,
                ),
            )
            self._record_event(con, identifier, tenant, session, event_name, result_json or "")
            updated = con.execute(
                "SELECT * FROM agent_tasks WHERE task_id=?", (identifier,)
            ).fetchone()
        return self._to_dict(updated)

    def cancel(
        self, task_id: str, tenant_id: str, session_id: str, reason: str = "cancelled by caller"
    ) -> dict[str, Any]:
        current = self.get(task_id, tenant_id, session_id, include_events=False)
        if current["status"] in TERMINAL_STATUSES:
            return current
        return self.update(
            task_id,
            tenant_id,
            session_id,
            status="cancelled",
            error=reason,
            lease_owner=None,
            lease_until=None,
            next_run_at=None,
            event="cancelled",
        )

    def approve(
        self,
        task_id: str,
        tenant_id: str,
        session_id: str,
        *,
        actor: str = "local",
        reason: str = "approved by caller",
    ) -> dict[str, Any]:
        """Move an awaiting-approval task back to ``queued`` exactly once."""

        identifier = _validate_id("task_id", task_id)
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id)
        safe_actor = (_safe_text(actor, "actor") or "local")[:64]
        safe_reason = _safe_text(reason, "reason") or "approved by caller"
        detail = _canonical({"actor": safe_actor, "reason": safe_reason})
        now = _utc_now()
        with self.catalog.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = self._scoped_row(con, identifier, tenant, session)
            if str(row["status"]) != "awaiting_approval":
                raise TaskError("task is not awaiting approval")
            cursor = con.execute(
                "UPDATE agent_tasks SET status='queued', lease_owner=NULL, lease_until=NULL, "
                "next_run_at=NULL, updated_at=?, error=? WHERE task_id=? AND tenant_id=? "
                "AND session_id=? AND status='awaiting_approval'",
                (now, row["error"], identifier, tenant, session),
            )
            if int(cursor.rowcount) != 1:
                raise TaskError("task approval lost a concurrent update")
            self._record_event(con, identifier, tenant, session, "approved", detail)
            updated = con.execute(
                "SELECT * FROM agent_tasks WHERE task_id=? AND tenant_id=? AND session_id=?",
                (identifier, tenant, session),
            ).fetchone()
        return self._to_dict(updated)

    def reconcile_interrupted(self, tenant_id: str, session_id: str | None = None) -> int:
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id) if session_id else None
        now = _utc_now()
        sql = (
            "UPDATE agent_tasks SET status='queued', lease_owner=NULL, lease_until=NULL, "
            "error='lease expired', updated_at=? WHERE tenant_id=? AND status='running' "
            "AND lease_until IS NOT NULL AND lease_until < ?"
        )
        params: list[Any] = [now, tenant, now]
        if session:
            sql += " AND session_id=?"
            params.append(session)
        with self.catalog.connect() as con:
            cursor = con.execute(sql, params)
            if cursor.rowcount:
                rows = con.execute(
                    "SELECT task_id, session_id FROM agent_tasks WHERE tenant_id=? "
                    "AND status='queued' AND error='lease expired' AND updated_at=?",
                    (tenant, now),
                ).fetchall()
                for row in rows:
                    self._record_event(
                        con,
                        str(row["task_id"]),
                        tenant,
                        str(row["session_id"]),
                        "interrupted",
                        "lease expired; requeued",
                    )
        return int(cursor.rowcount)

    def cleanup(
        self,
        tenant_id: str,
        session_id: str | None = None,
        *,
        older_than_seconds: int = 86_400,
        limit: int = 500,
    ) -> int:
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id) if session_id else None
        if older_than_seconds < 0 or limit < 1:
            raise TaskError("cleanup arguments are out of range")
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds))
            .replace(microsecond=0)
            .isoformat()
        )
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        where = f"tenant_id=? AND status IN ({placeholders}) AND updated_at < ?"
        params: list[Any] = [tenant, *sorted(TERMINAL_STATUSES), cutoff]
        if session:
            where += " AND session_id=?"
            params.append(session)
        with self.catalog.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            ids = [
                str(row["task_id"])
                for row in con.execute(
                    "SELECT task_id FROM agent_tasks WHERE "
                    + where
                    + " ORDER BY updated_at ASC LIMIT ?",
                    [*params, int(limit)],
                ).fetchall()
            ]
            if ids:
                marks = ",".join("?" for _ in ids)
                con.execute(
                    f"DELETE FROM agent_tasks WHERE tenant_id=? AND task_id IN ({marks})",
                    [tenant, *ids],
                )
                con.execute(
                    f"DELETE FROM agent_task_events WHERE tenant_id=? AND task_id IN ({marks})",
                    [tenant, *ids],
                )
        return len(ids)

    def list(
        self, tenant_id: str, session_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        tenant = _validate_id("tenant_id", tenant_id)
        session = _validate_id("session_id", session_id) if session_id else None
        if not 1 <= limit <= 200:
            raise TaskError("limit must be between 1 and 200")
        sql = "SELECT * FROM agent_tasks WHERE tenant_id=?"
        params: list[Any] = [tenant]
        if session:
            sql += " AND session_id=?"
            params.append(session)
        sql += " ORDER BY updated_at DESC, task_id DESC LIMIT ?"
        params.append(limit)
        with self.catalog.connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._to_dict(row) for row in rows]
