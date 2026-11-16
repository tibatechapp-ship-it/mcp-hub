from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

TRACKED_FIELDS = (
    "name",
    "publisher",
    "description",
    "repository_url",
    "endpoint_url",
    "popularity",
    "stars",
    "forks",
    "license",
    "last_pushed_at",
    "topics",
    "categories",
    "health_status",
)

_FIELD_UPSERT = """
INSERT INTO field_provenance (server_id, field_name, source, value)
VALUES (?,?,?,?)
ON CONFLICT(server_id, field_name, source) DO UPDATE SET
  value=excluded.value,
  set_at=CURRENT_TIMESTAMP
"""


@contextmanager
def _scope(target: Any) -> Iterator[Any]:
    """Accept either a Catalog (opens + commits) or an already-open connection.

    Passing an open connection from Catalog write paths keeps both provenance
    tables in the SAME transaction as the servers/enrichments write.
    """
    connect = getattr(target, "connect", None)
    if connect is None:
        yield target
        return
    with connect() as con:
        yield con


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def record(target: Any, server_id: int, field_name: str, source: str, value: Any) -> None:
    """Write a single field change to BOTH tables atomically.

    - provenance: audit trail (one row per event)
    - field_provenance: queryable index (one row per field + source)
    """
    if field_name not in TRACKED_FIELDS:
        return
    src = source or "unknown"
    text = _text(value)
    payload = json.dumps({field_name: text}, ensure_ascii=False)
    try:
        with _scope(target) as con:
            con.execute(
                "INSERT INTO provenance (server_id, source, payload_json) VALUES (?,?,?)",
                (int(server_id), src, payload),
            )
            con.execute(_FIELD_UPSERT, (int(server_id), field_name, src, text))
    except Exception:
        # provenance must never break ingestion
        return


def record_row(target: Any, server_id: int, source: str, payload: dict[str, Any]) -> None:
    """Write a full row (many fields) to BOTH tables atomically.

    provenance gets ONE audit row with every key of the payload;
    field_provenance gets one indexed row per tracked field.
    """
    if not payload:
        return
    src = source or "unknown"
    audit = {str(k): _text(v) for k, v in payload.items()}
    tracked = {k: v for k, v in audit.items() if k in TRACKED_FIELDS}
    try:
        with _scope(target) as con:
            con.execute(
                "INSERT INTO provenance (server_id, source, payload_json) VALUES (?,?,?)",
                (int(server_id), src, json.dumps(audit, ensure_ascii=False)),
            )
            for field, value in tracked.items():
                con.execute(_FIELD_UPSERT, (int(server_id), field, src, value))
    except Exception:
        return


# ---- read path -------------------------------------------------------------
def field_history(catalog, server_id: int, field_name: str | None = None) -> list[dict]:
    """Indexed field-level history for one server (optionally one field)."""
    with catalog.connect() as con:
        if field_name:
            rows = con.execute(
                "SELECT field_name, source, value, set_at FROM field_provenance "
                "WHERE server_id=? AND field_name=? ORDER BY set_at DESC, id DESC",
                (int(server_id), field_name),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT field_name, source, value, set_at FROM field_provenance "
                "WHERE server_id=? ORDER BY field_name ASC, set_at DESC, id DESC",
                (int(server_id),),
            ).fetchall()
    return [dict(r) for r in rows]


def field_conflicts(catalog, server_id: int) -> dict[str, list[dict]]:
    """Fields whose sources disagree: {field: [{source, value, set_at}, ...]}."""
    with catalog.connect() as con:
        rows = con.execute(
            "SELECT field_name, source, value, set_at FROM field_provenance "
            "WHERE server_id=? ORDER BY field_name, source",
            (int(server_id),),
        ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["field_name"], []).append(
            {"source": r["source"], "value": r["value"], "set_at": r["set_at"]}
        )
    return {
        field: entries
        for field, entries in grouped.items()
        if len({e["value"] for e in entries}) > 1
    }


def _timestamp_column(con) -> str:
    """v1.1.0 stored time in `created_at`; accept `set_at` schemas too."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(provenance)")}
    if "set_at" in cols:
        return "set_at"
    if "created_at" in cols:
        return "created_at"
    return "rowid"


def event_history(catalog, server_id: int, limit: int = 50) -> list[dict]:
    """Audit trail (provenance table) — grouped per event, newest first."""
    with catalog.connect() as con:
        ts = _timestamp_column(con)
        rows = con.execute(
            f"SELECT source, payload_json, {ts} AS set_at FROM provenance "
            "WHERE server_id=? ORDER BY set_at DESC, id DESC LIMIT ?",
            (int(server_id), int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append({"source": r["source"], "set_at": r["set_at"], "fields": payload})
    return out


# ---- legacy compatibility: history()/conflicts() now hit field_provenance --
def history(catalog, server_id: int) -> list[dict]:
    return field_history(catalog, server_id)


def conflicts(catalog, server_id: int) -> dict[str, list[dict]]:
    return field_conflicts(catalog, server_id)
