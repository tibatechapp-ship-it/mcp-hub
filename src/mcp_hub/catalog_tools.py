"""Pinned, read-only tool contracts for the stateless MCP endpoint.

The manifest digest below is the anti-rug-pull baseline: ``tools/list`` and
``tools/call`` refuse to run if the live specs or handler names drift from it.
Changing a tool description or schema therefore requires an explicit, reviewed
manifest pin update instead of silently changing what a client already approved.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import (
    catalog_query,
    description_smells,
    doctor,
    persona,
    policy_gateway,
    provenance,
    semantic_router,
    skills,
    taxonomy,
)
from .database import Catalog
from .mcp_stateless import (
    INVALID_PARAMS,
    POLICY_DENIED,
    TASK_DENIED,
    Handler,
    MCPError,
    ResourceSpec,
    ToolPolicy,
    ToolSpec,
)
from .tasks import TaskError, TaskStore

__all__ = [
    "TOOL_SPECS",
    "RESOURCE_SPECS",
    "PINNED_TOOL_MANIFEST_SHA256",
    "build_tools",
    "build_resources",
    "route_task",
    "description_smell_audit",
    "policy_snapshot",
    "Phase3Runtime",
    "build_phase3",
    "build_tool_policy",
]

PINNED_TOOL_MANIFEST_SHA256 = "6009656f1d4cc30fb9f079b8b6f2bdec01a04abfefc4099462f46998804d2857"


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_servers",
        description="Search the catalog by name, publisher, description, category, or topic.",
        input_schema=_object(
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "mode": {"type": "string", "enum": ["contains", "fts"]},
            },
            ["query"],
        ),
    ),
    ToolSpec(
        name="get_server",
        description="Get full details for one server by its database ID.",
        input_schema=_object({"server_id": {"type": "integer"}}, ["server_id"]),
    ),
    ToolSpec(
        name="list_categories",
        description="List all categories with their server counts.",
        input_schema=_object({}),
    ),
    ToolSpec(
        name="servers_by_category",
        description="List servers in a specific category.",
        input_schema=_object(
            {
                "category": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ["category"],
        ),
    ),
    ToolSpec(
        name="provenance_conflicts",
        description="Detect fields where different sources disagree for a given server.",
        input_schema=_object({"server_id": {"type": "integer"}}, ["server_id"]),
    ),
    ToolSpec(
        name="provenance_events",
        description="Show the audit trail (event history) for a server.",
        input_schema=_object(
            {
                "server_id": {"type": "integer"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["server_id"],
        ),
    ),
    ToolSpec(
        name="doctor_report",
        description="Run read-only health checks on the catalog (no repairs).",
        input_schema=_object({}),
    ),
    ToolSpec(
        name="suggest_category",
        description="Suggest categories for a server using the deterministic classifier.",
        input_schema=_object({"server_id": {"type": "integer"}}, ["server_id"]),
    ),
    ToolSpec(
        name="query_catalog",
        description="Query the catalog by category, tag, task profile, access mode or readiness.",
        input_schema=_object(
            {
                "kind": {"type": "string", "enum": ["all", "server", "tool"]},
                "category": {"type": "string"},
                "tag": {"type": "string"},
                "profile": {"type": "string"},
                "access_mode": {"type": "string", "enum": ["read-only", "read-write"]},
                "ready_only": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            }
        ),
    ),
    ToolSpec(
        name="route_task",
        description="Rank catalog tools for an Arabic or English task; compact mode saves tokens.",
        input_schema=_object(
            {
                "task": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "compact": {"type": "boolean"},
            },
            ["task"],
        ),
    ),
    ToolSpec(
        name="description_smell_audit",
        description="Audit bundled descriptions for six stable quality smells.",
        input_schema=_object({"limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
    ),
    ToolSpec(
        name="policy_snapshot",
        description="Show the active tool policy without secrets or audit paths.",
        input_schema=_object({}),
    ),
)

RESOURCE_SPECS: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        uri="mcp-hub://stats",
        name="mcp-hub statistics",
        description="Live catalog statistics (JSON).",
        mime_type="application/json",
    ),
)


def _string(args: Mapping[str, Any], key: str, default: str = "") -> str:
    value = args.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _integer(args: Mapping[str, Any], key: str, default: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _boolean(args: Mapping[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_string(args: Mapping[str, Any], key: str) -> str | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_integer(args: Mapping[str, Any], key: str) -> int | None:
    if key not in args or args[key] is None:
        return None
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _enriched(catalog: Catalog, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [int(row["id"]) for row in rows]
    placeholders = ",".join("?" for _ in ids)
    with catalog.connect() as con:
        extra_rows = con.execute(
            "SELECT server_id, stars, forks, license, pushed_at, topics FROM enrichments "
            f"WHERE server_id IN ({placeholders})",
            ids,
        ).fetchall()
    extra = {int(row["server_id"]): dict(row) for row in extra_rows}
    merged: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        enrichment = extra.get(int(data["id"]), {})
        data["stars"] = int(enrichment.get("stars") or 0)
        data["forks"] = int(enrichment.get("forks") or 0)
        data["license"] = enrichment.get("license") or ""
        data["pushed_at"] = enrichment.get("pushed_at") or ""
        data["topics"] = enrichment.get("topics") or ""
        merged.append(data)
    return merged


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "publisher": row.get("publisher", ""),
        "description": (row.get("description") or "")[:300],
        "categories": row.get("categories", ""),
        "stars": int(row.get("stars") or 0),
        "popularity": int(row.get("popularity") or 0),
        "endpoint_url": row.get("endpoint_url", ""),
        "repository_url": row.get("repository_url", ""),
    }


def search_servers(
    catalog: Catalog, query: str, limit: int = 20, mode: str = "contains"
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    rows = catalog.search_fts(query, limit) if mode == "fts" else catalog.search(query, limit)
    return [_summary(row) for row in _enriched(catalog, rows)]


def get_server(catalog: Catalog, server_id: int) -> dict[str, Any]:
    row = catalog.get_server(server_id)
    if not row:
        raise ValueError(f"server {server_id} not found")
    merged = _enriched(catalog, [dict(row)])[0]
    for key in ("created_at", "updated_at", "raw_name"):
        merged.pop(key, None)
    return merged


def list_categories(catalog: Catalog) -> list[dict[str, Any]]:
    counts: dict[str, int] = {category["name"]: 0 for category in catalog.custom_categories()}
    with catalog.connect() as con:
        rows = con.execute(
            "SELECT categories FROM servers WHERE archived=0 AND categories != ''"
        ).fetchall()
    for row in rows:
        for category in (row["categories"] or "").split(","):
            name = category.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"category": name, "count": count} for name, count in ordered]


def servers_by_category(catalog: Catalog, category: str, limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    name = (category or "").strip().lower()
    if not name:
        return []
    with catalog.connect() as con:
        rows = con.execute(
            "SELECT * FROM servers WHERE archived=0 "
            "AND (',' || categories || ',') LIKE ? "
            "ORDER BY popularity DESC, id DESC LIMIT ?",
            (f"%,{name},%", limit),
        ).fetchall()
    enriched = _enriched(catalog, [dict(row) for row in rows])
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "publisher": row.get("publisher", ""),
            "stars": int(row.get("stars") or 0),
            "description": (row.get("description") or "")[:200],
        }
        for row in enriched
    ]


def provenance_conflicts(catalog: Catalog, server_id: int) -> dict[str, Any]:
    if not catalog.get_server(server_id):
        raise ValueError(f"server {server_id} not found")
    return provenance.field_conflicts(catalog, server_id)


def provenance_events(catalog: Catalog, server_id: int, limit: int = 10) -> list[dict[str, Any]]:
    if not catalog.get_server(server_id):
        raise ValueError(f"server {server_id} not found")
    return provenance.event_history(catalog, server_id, max(1, min(limit, 50)))


def doctor_report(catalog: Catalog) -> dict[str, Any]:
    return doctor.run(catalog)


def suggest_category(catalog: Catalog, server_id: int) -> dict[str, Any]:
    row = catalog.get_server(server_id)
    if not row:
        raise ValueError(f"server {server_id} not found")
    custom = {item["name"]: item.get("terms", []) for item in catalog.custom_categories()}
    suggested = taxonomy.classify(dict(row), custom_rules=custom)
    return {
        "server_id": server_id,
        "name": row["name"],
        "current_categories": row.get("categories", ""),
        "suggested_categories": suggested,
    }


def query_catalog(args: Mapping[str, Any]) -> dict[str, Any]:
    return catalog_query.query_catalog(
        kind=_string(args, "kind", "all"),
        category=_string(args, "category") or None,
        tag=_string(args, "tag") or None,
        profile=_string(args, "profile") or None,
        access_mode=_string(args, "access_mode") or None,
        ready_only=_boolean(args, "ready_only"),
        limit=_integer(args, "limit", 50),
    )


def route_task(args: Mapping[str, Any]) -> dict[str, Any]:
    return semantic_router.route_task(
        _string(args, "task"),
        specs=TOOL_SPECS,
        limit=_integer(args, "limit", 5),
        compact=_boolean(args, "compact"),
    )


def description_smell_audit(args: Mapping[str, Any]) -> dict[str, Any]:
    return description_smells.audit_registry(limit=_integer(args, "limit", 200))


def policy_snapshot(_: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        return policy_gateway.PolicyGateway.from_env().snapshot()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MCPError(POLICY_DENIED, f"Policy configuration invalid: {exc}") from exc


def build_tools(catalog: Catalog) -> dict[str, Handler]:
    """Bind the pinned tool contracts to read-only catalog handlers."""

    def search(args: Mapping[str, Any]) -> Any:
        return search_servers(
            catalog,
            _string(args, "query"),
            _integer(args, "limit", 20),
            _string(args, "mode", "contains"),
        )

    def get(args: Mapping[str, Any]) -> Any:
        return get_server(catalog, _integer(args, "server_id", 0))

    def categories(_: Mapping[str, Any]) -> Any:
        return list_categories(catalog)

    def by_category(args: Mapping[str, Any]) -> Any:
        return servers_by_category(catalog, _string(args, "category"), _integer(args, "limit", 20))

    def conflicts(args: Mapping[str, Any]) -> Any:
        return provenance_conflicts(catalog, _integer(args, "server_id", 0))

    def events(args: Mapping[str, Any]) -> Any:
        return provenance_events(
            catalog, _integer(args, "server_id", 0), _integer(args, "limit", 10)
        )

    def doctor_handler(_: Mapping[str, Any]) -> Any:
        return doctor_report(catalog)

    def suggest(args: Mapping[str, Any]) -> Any:
        return suggest_category(catalog, _integer(args, "server_id", 0))

    return {
        "search_servers": search,
        "get_server": get,
        "list_categories": categories,
        "servers_by_category": by_category,
        "provenance_conflicts": conflicts,
        "provenance_events": events,
        "doctor_report": doctor_handler,
        "suggest_category": suggest,
        "query_catalog": query_catalog,
        "route_task": route_task,
        "description_smell_audit": description_smell_audit,
        "policy_snapshot": policy_snapshot,
    }


def build_resources(catalog: Catalog) -> dict[str, Handler]:
    def stats(_: Mapping[str, Any]) -> str:
        return json.dumps(catalog.stats(), ensure_ascii=False, sort_keys=True)

    return {"mcp-hub://stats": stats}


@dataclass(frozen=True, slots=True)
class Phase3Runtime:
    """Wired skills, tasks and persona surfaces for one catalog database."""

    skill_registry: skills.SkillRegistry
    skill_handlers: dict[str, Handler]
    skill_resources: dict[str, Handler]
    resource_specs: tuple[ResourceSpec, ...]
    skill_policy: ToolPolicy
    task_store: TaskStore
    task_handlers: dict[str, Handler]
    persona_handler: Handler


def build_tool_policy() -> ToolPolicy:
    """Bind the configured ZYKO-compatible gate to tool calls."""

    try:
        gateway = policy_gateway.PolicyGateway.from_env()
    except (policy_gateway.ConfigError, OSError, ValueError) as exc:
        message = f"Policy configuration invalid: {exc}"

        def deny_invalid(name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
            _ = name, args
            raise MCPError(POLICY_DENIED, message, {"policy": "invalid"})

        return deny_invalid

    def enforce(name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return gateway.enforce(name, args)
        except policy_gateway.PolicyDenied as exc:
            raise MCPError(POLICY_DENIED, exc.reason, {"rule": exc.rule}) from exc

    return enforce


def build_skill_policy() -> ToolPolicy:
    """Bind the ``skills`` section of the Phase 2 policy to skill reads."""

    try:
        gateway = policy_gateway.PolicyGateway.from_env()
    except (policy_gateway.ConfigError, OSError, ValueError) as exc:
        message = f"Policy configuration invalid: {exc}"

        def deny_invalid(name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
            _ = name, args
            raise MCPError(POLICY_DENIED, message, {"policy": "invalid"})

        return deny_invalid

    def enforce(name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            gateway.enforce_skill(name)
        except policy_gateway.PolicyDenied as exc:
            raise MCPError(POLICY_DENIED, exc.reason, {"rule": exc.rule}) from exc
        return dict(args)

    return enforce


def build_task_handlers(store: TaskStore) -> dict[str, Handler]:
    def get(args: Mapping[str, Any]) -> Any:
        try:
            return store.get(
                _string(args, "task_id"),
                _string(args, "tenant_id"),
                _string(args, "session_id"),
                include_events=_boolean(args, "include_events", True),
            )
        except (TaskError, ValueError) as exc:
            raise MCPError(TASK_DENIED, str(exc)) from exc

    def update(args: Mapping[str, Any]) -> Any:
        payload = args.get("payload")
        if payload is not None and not isinstance(payload, Mapping):
            raise MCPError(INVALID_PARAMS, "payload must be an object")
        try:
            return store.update(
                _string(args, "task_id"),
                _string(args, "tenant_id"),
                _string(args, "session_id"),
                status=_optional_string(args, "status"),
                payload=payload,
                result=args.get("result"),
                error=_optional_string(args, "error"),
                attempts=_optional_integer(args, "attempts"),
                lease_owner=_optional_string(args, "lease_owner"),
                lease_until=_optional_string(args, "lease_until"),
                next_run_at=_optional_string(args, "next_run_at"),
                event=_optional_string(args, "event"),
            )
        except (TaskError, ValueError) as exc:
            raise MCPError(TASK_DENIED, str(exc)) from exc

    def cancel(args: Mapping[str, Any]) -> Any:
        try:
            return store.cancel(
                _string(args, "task_id"),
                _string(args, "tenant_id"),
                _string(args, "session_id"),
                reason=_string(args, "reason", "cancelled by caller"),
            )
        except (TaskError, ValueError) as exc:
            raise MCPError(TASK_DENIED, str(exc)) from exc

    return {
        "tasks/get": get,
        "tasks/update": update,
        "tasks/cancel": cancel,
    }


def build_persona_handler(store: TaskStore, tool_policy: ToolPolicy) -> Handler:
    def simulate(args: Mapping[str, Any]) -> Any:
        scenario = _string(args, "scenario")
        task_id = _optional_string(args, "task_id")
        try:
            result = persona.PersonaSimulator(store, tool_policy).run(
                scenario,
                _string(args, "tenant_id"),
                _string(args, "session_id"),
                task_id=task_id,
            )
        except ValueError as exc:
            raise MCPError(INVALID_PARAMS, str(exc)) from exc
        except TaskError as exc:
            raise MCPError(TASK_DENIED, str(exc)) from exc
        return result.to_dict()

    return simulate


def build_phase3(catalog: Catalog, tool_policy: ToolPolicy) -> Phase3Runtime:
    try:
        registry = skills.SkillRegistry(catalog)
        store = TaskStore(catalog)
    except Exception:  # noqa: BLE001 - app startup must survive a broken DB for /health
        registry = skills.SkillRegistry(catalog, initialize=False)
        store = TaskStore(catalog, initialize=False)
    skill_handlers = skills.build_skill_handlers(registry)
    skill_resources = skills.build_skill_resources(registry)
    resource_specs = tuple(
        ResourceSpec(uri=uri, name=name, description="Read-only skill file", mime_type=mime)
        for uri, name, mime in registry.resource_specs()
    )
    return Phase3Runtime(
        skill_registry=registry,
        skill_handlers=skill_handlers,
        skill_resources=skill_resources,
        resource_specs=resource_specs,
        skill_policy=build_skill_policy(),
        task_store=store,
        task_handlers=build_task_handlers(store),
        persona_handler=build_persona_handler(store, tool_policy),
    )
