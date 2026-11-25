"""Arabic command DSL over the existing stateless MCP gateway.

This module is a thin translator. It never adds MCP methods, changes tool
specs or re-implements policy: every parsed step is dispatched through
``mcp_stateless.handle`` with the same handlers and policies used by ``/mcp``.
Free-text queries go through ``arabic_runtime``; identifiers, actors and the
original command text are preserved verbatim in the SQLite audit table.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from . import __version__, apps, catalog_tools
from . import mcp_stateless as ms
from . import telemetry as telemetry_mod
from .arabic_runtime import normalize_for_match, resolve_lang
from .database import Catalog

__all__ = [
    "DslContext",
    "DslError",
    "Command",
    "AUDIT_TABLE",
    "parse",
    "plan",
    "execute",
    "record_audit",
    "build_runtime",
]

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_BAD_INPUT = 2

AUDIT_TABLE = "arabic_dsl_audit"
_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
_ARABIC_DIGITS_RE = re.compile(r"[٠-٩۰-۹]")
_ASCII_DIGITS_RE = re.compile(r"^[0-9]+$")
_STATUS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PERSONA_SCENARIOS = frozenset(
    {"safe_chain", "retry_then_success", "secret_leak", "permission_bypass"}
)
_APPROVAL_METHODS = frozenset(ms.APPROVAL_ACTIONS)
_DIACRITICS = r"[\u064b-\u0652\u0640]*"
_CHAR_VARIANTS = {"ا": "[اأإآ]", "ي": "[يى]", "ه": "[هة]"}


class DslError(ValueError):
    """A parser or validation failure with a stable CLI exit code."""

    def __init__(self, message: str, *, code: int = EXIT_BAD_INPUT) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DslContext:
    """Context passed unchanged to every step of a pipeline."""

    tenant_id: str = "local"
    session_id: str = "cli"
    actor: str = "local"
    lang: str = "en"

    def __post_init__(self) -> None:
        object.__setattr__(self, "lang", resolve_lang(self.lang))
        for field_name in ("tenant_id", "session_id", "actor"):
            value = getattr(self, field_name)
            if (
                not value
                or len(value) > 128
                or _ARABIC_DIGITS_RE.search(value)
                or any(char.isspace() and char != " " for char in value)
            ):
                raise DslError(f"invalid {field_name}", code=EXIT_BAD_INPUT)


@dataclass(frozen=True, slots=True)
class Command:
    """One parsed Arabic instruction."""

    step: int
    raw: str
    label: str
    method: str
    params: dict[str, Any]
    approval_required: bool = False
    read_only_ui: bool = False

    def plan(self) -> dict[str, Any]:
        """Return the stable, translated pre-execution plan for this step."""
        payload: dict[str, Any] = {
            "step": self.step,
            "raw": self.raw,
            "label": self.label,
            "method": self.method,
            "params": self.params,
            "approval_required": self.approval_required,
        }
        if self.read_only_ui:
            payload["read_only_ui"] = True
        return payload


@dataclass(frozen=True, slots=True)
class _Spec:
    aliases: tuple[str, ...]
    method: str
    kind: str = ""
    tool: str | None = None
    approval: bool = False
    read_only_ui: bool = False


# Longer phrases come first so a generic prefix never shadows a specific one.
_SPECS: tuple[_Spec, ...] = (
    _Spec(("الغِ اعتماد المهارة", "Revoke skill"), "skills/revoke", "name", approval=True),
    _Spec(("اعتمد المهارة", "Approve skill"), "skills/approve", "name", approval=True),
    _Spec(("تفاصيل المهارة", "Skill details"), "skills/get", "name"),
    _Spec(("اعرض المهارات", "List skills"), "skills/list"),
    _Spec(("وافق على المهمة", "Approve task"), "tasks/approve", "id", approval=True),
    _Spec(("حدّث المهمة", "Update task"), "tasks/update", "status_update"),
    _Spec(("حالة المهمة", "Task status"), "tasks/get", "id"),
    _Spec(("الغِ المهمة", "Cancel task"), "tasks/cancel", "id"),
    _Spec(("اعرض المهام", "List tasks"), "tasks/list", approval=True),
    _Spec(("حاكي الشخصية", "Simulate persona"), "persona/simulate", "scenario"),
    _Spec(
        ("اعرض واجهة المراجعة", "Show review UI"),
        "resources/read",
        "ui_approval",
        read_only_ui=True,
    ),
    _Spec(
        ("اعرض واجهة الكتالوج", "Show catalog UI"),
        "resources/read",
        "ui_catalog",
        read_only_ui=True,
    ),
    _Spec(("اعرض الإحصاءات", "Show statistics"), "resources/read", "stats"),
    _Spec(("اعرض سجل التدقيق", "Audit trail"), "tools/call", "server_id", "provenance_events"),
    _Spec(("تفاصيل الخادم", "Server details"), "tools/call", "server_id", "get_server"),
    _Spec(("تفاصيل الأداة", "Tool details"), "tools/call", "query", "search_servers"),
    _Spec(("شغّل الفحوصات الصحية", "Run health checks"), "tools/call", "empty", "doctor_report"),
    _Spec(("اعرض بيانات السياسة", "Policy snapshot"), "tools/call", "empty", "policy_snapshot"),
    _Spec(("اعرض الفئات", "List categories"), "tools/call", "empty", "list_categories"),
    _Spec(
        ("ابحث عن أداة", "ابحث في الكتالوج", "Search tool", "Search catalog"),
        "tools/call",
        "query",
        "search_servers",
    ),
    _Spec(("وجّه الطلب", "Route task"), "tools/call", "query", "route_task"),
    _Spec(("اعرض الموجه", "Get prompt"), "prompts/get", "name"),
    _Spec(("اعرض المحفزات", "List prompts"), "prompts/list"),
    _Spec(("اعرض الموارد", "List resources"), "resources/list"),
    _Spec(("اعرض الكتالوج", "اعرض الأدوات", "List tools", "List catalog"), "tools/list"),
    _Spec(("اكتشف الخادم", "Discover server"), "server/discover"),
    _Spec(("اختبار الاتصال", "Ping"), "ping"),
)


@lru_cache(maxsize=256)
def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Build a whitespace/diacritic tolerant regex from an alias phrase."""
    pieces = [r"\s*"]
    for char in phrase:
        if char.isspace():
            pieces.append(r"\s+")
        elif "\u0600" <= char <= "\u06ff":
            variants = _CHAR_VARIANTS.get(char, re.escape(char))
            pieces.append(f"(?:{variants}){_DIACRITICS}")
        else:
            pieces.append(re.escape(char))
    pieces.append(r"\s*(?P<arg>.*)$")
    return re.compile("".join(pieces), re.IGNORECASE)


@lru_cache(maxsize=256)
def _normalized_alias(alias: str) -> str:
    return normalize_for_match(alias, strip_al=True)


def _require(arg: str, label: str) -> str:
    value = arg.strip()
    if not value:
        raise DslError(f"{label} is required", code=EXIT_BAD_INPUT)
    return value


def _require_id(arg: str, label: str) -> str:
    value = _require(arg, label)
    if not _ID_RE.fullmatch(value):
        raise DslError(f"invalid {label}", code=EXIT_BAD_INPUT)
    return value


def _server_id(arg: str) -> int:
    value = _require(arg, "server_id")
    if not _ASCII_DIGITS_RE.fullmatch(value):
        raise DslError("invalid server_id", code=EXIT_BAD_INPUT)
    return int(value)


def _status_update(arg: str) -> tuple[str, str]:
    value = _require(arg, "task_id and status")
    parts = re.split(r"\s+(?:إلى|الي|to)\s+", value, maxsplit=1, flags=re.IGNORECASE)
    task_id = _require_id(parts[0], "task_id")
    status = parts[1].strip() if len(parts) > 1 else "in_progress"
    if not _STATUS_RE.fullmatch(status):
        raise DslError("invalid status", code=EXIT_BAD_INPUT)
    return task_id, status


def _tool_args(spec: _Spec, arg: str) -> dict[str, Any]:
    if spec.kind == "query":
        query = normalize_for_match(_require(arg, "query"))
        if not query:
            raise DslError("query is required", code=EXIT_RUNTIME)
        return {
            "query": query,
            "limit": 5 if spec.tool == "route_task" else 20,
            "mode": "contains",
        }
    if spec.kind == "server_id":
        return {"server_id": _server_id(arg), "limit": 20}
    return {}


def _build_params(spec: _Spec, arg: str, ctx: DslContext) -> dict[str, Any]:
    if spec.method == "tools/call":
        assert spec.tool is not None  # noqa: S101 - every tools/call spec declares a tool
        arguments = dict(_tool_args(spec, arg))
        if spec.tool == "route_task":
            arguments = {
                "task": arguments["query"],
                "limit": arguments["limit"],
                "compact": True,
            }
        if spec.tool == "provenance_events":
            arguments = {
                "server_id": arguments["server_id"],
                "limit": arguments["limit"],
            }
        return {"name": spec.tool, "arguments": arguments}

    if spec.method in {"skills/get", "skills/approve", "skills/revoke", "prompts/get"}:
        name = _require_id(arg, "name")
        if spec.method == "skills/get":
            return {"name": name}
        return {"name": name, "actor": ctx.actor}

    if spec.method == "tasks/list":
        return {
            "tenant_id": ctx.tenant_id,
            "session_id": ctx.session_id,
            "limit": 50,
        }

    if spec.method in {"tasks/get", "tasks/cancel", "tasks/approve"}:
        task_id = _require_id(arg, "task_id")
        params = {
            "task_id": task_id,
            "tenant_id": ctx.tenant_id,
            "session_id": ctx.session_id,
        }
        if spec.method == "tasks/get":
            params["include_events"] = True
        elif spec.method == "tasks/cancel":
            params["reason"] = "cancelled by Arabic DSL"
        else:
            params["actor"] = ctx.actor
            params["reason"] = "approved by Arabic DSL"
        return params

    if spec.method == "tasks/update":
        task_id, status = _status_update(arg)
        return {
            "task_id": task_id,
            "tenant_id": ctx.tenant_id,
            "session_id": ctx.session_id,
            "status": status,
        }

    if spec.method == "persona/simulate":
        scenario = _require(arg, "scenario")
        if scenario not in _PERSONA_SCENARIOS:
            raise DslError("invalid persona scenario", code=EXIT_BAD_INPUT)
        return {
            "scenario": scenario,
            "tenant_id": ctx.tenant_id,
            "session_id": ctx.session_id,
        }

    if spec.method == "resources/read":
        uris = {
            "stats": "mcp-hub://stats",
            "ui_approval": "ui://mcp-hub/approval-review",
            "ui_catalog": "ui://mcp-hub/tool-catalog",
        }
        params: dict[str, Any] = {"uri": uris[spec.kind]}
        if spec.read_only_ui:
            params["lang"] = ctx.lang
        return params

    _ = arg
    return {}


def _command_text(line: str) -> str:
    """Treat an ASCII/full-width colon as a command/argument separator."""
    return line.replace(":", " ").replace("：", " ")


def _match(line: str) -> tuple[_Spec, str]:
    text = _command_text(line)
    normalized = normalize_for_match(text, strip_al=True)
    for spec in _SPECS:
        for alias in spec.aliases:
            alias_norm = _normalized_alias(alias)
            if normalized != alias_norm and not normalized.startswith(alias_norm + " "):
                continue
            match = _phrase_pattern(alias).match(text)
            if match is not None:
                return spec, match.group("arg").strip()
    raise DslError(f"unrecognized command: {line}", code=EXIT_RUNTIME)


def parse(text: str, context: DslContext | None = None) -> list[Command]:
    """Parse one command or a newline/semicolon separated pipeline."""
    ctx = context or DslContext()
    lines = [line.strip() for line in re.split(r"[\n;؛]+", text) if line.strip()]
    if not lines:
        raise DslError("no commands provided", code=EXIT_RUNTIME)
    commands: list[Command] = []
    for step, line in enumerate(lines, 1):
        spec, arg = _match(line)
        params = _build_params(spec, arg, ctx)
        commands.append(
            Command(
                step=step,
                raw=line,
                label=spec.aliases[0],
                method=spec.method,
                params=params,
                approval_required=spec.approval or spec.method in _APPROVAL_METHODS,
                read_only_ui=spec.read_only_ui,
            )
        )
    return commands


def plan(commands: list[Command]) -> list[dict[str, Any]]:
    """Return the translated execution plan without running any command."""
    return [command.plan() for command in commands]


def build_runtime(catalog: Catalog) -> dict[str, Any]:
    """Build the exact handler/policy set used by the HTTP ``/mcp`` endpoint."""
    tool_policy = catalog_tools.build_tool_policy()
    phase3 = catalog_tools.build_phase3(catalog, tool_policy)
    phase4 = (
        apps.build_runtime(catalog, phase3.skill_registry, phase3.task_store)
        if apps.enabled()
        else apps.empty_runtime()
    )
    resources = {
        **catalog_tools.build_resources(catalog),
        **phase3.skill_resources,
        **phase4.resources,
    }
    resource_specs = (
        *catalog_tools.RESOURCE_SPECS,
        *phase3.resource_specs,
        *phase4.resource_specs,
    )
    return {
        "tools": catalog_tools.build_tools(catalog),
        "tool_specs": catalog_tools.TOOL_SPECS,
        "expected_manifest_hash": catalog_tools.PINNED_TOOL_MANIFEST_SHA256,
        "resource_specs": resource_specs,
        "resources": resources,
        "server_info": {"name": "mcp-hub", "version": __version__},
        "instructions": ("Arabic DSL translations execute MCP tools under the active policy."),
        "tool_policy": tool_policy,
        "skill_handlers": phase3.skill_handlers,
        "skill_policy": phase3.skill_policy,
        "skill_approval_handlers": phase3.skill_approval_handlers,
        "approval_policy": phase3.approval_policy,
        "task_handlers": phase3.task_handlers,
        "persona_handler": phase3.persona_handler,
        "telemetry": telemetry_mod.build_telemetry(str(catalog.path)),
        "app_policy": phase4.policy,
    }


def _request(command: Command) -> dict[str, Any]:
    params: dict[str, Any] = dict(command.params)
    params["_meta"] = {
        ms.META_PROTOCOL_VERSION: ms.MCP_PROTOCOL_VERSION,
        ms.META_CLIENT_INFO: {"name": "mcp-hub-arabic-dsl", "version": __version__},
        ms.META_CLIENT_CAPABILITIES: {},
    }
    return {
        "jsonrpc": ms.JSONRPC_VERSION,
        "id": command.step,
        "method": command.method,
        "params": params,
    }


def _headers(command: Command) -> dict[str, str]:
    headers = {
        "MCP-Protocol-Version": ms.MCP_PROTOCOL_VERSION,
        "Mcp-Method": command.method,
    }
    name_param = ms.NAME_HEADERS_PARAMS.get(command.method)
    if name_param is not None:
        value = command.params.get(name_param)
        if isinstance(value, str) and value:
            headers["Mcp-Name"] = ms.encode_header_value(value)
    return headers


def _exit_code_for(error: Mapping[str, Any]) -> int:
    code = error.get("code")
    return EXIT_BAD_INPUT if code in {ms.POLICY_DENIED, ms.TASK_DENIED} else EXIT_RUNTIME


def record_audit(
    db_path: str,
    *,
    step: int,
    method: str,
    label: str,
    raw: str,
    params: Mapping[str, Any],
    result: Any,
    status: str,
    exit_code: int,
) -> None:
    """Append the original Arabic text and result to a dedicated SQLite table."""
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as con:
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, "
            "step INTEGER NOT NULL, "
            "method TEXT NOT NULL, "
            "label TEXT NOT NULL, "
            "raw_text TEXT NOT NULL, "
            "params_json TEXT NOT NULL, "
            "result_json TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "exit_code INTEGER NOT NULL)"
        )
        con.execute(
            f"INSERT INTO {AUDIT_TABLE} "
            "(created_at, step, method, label, raw_text, params_json, "
            "result_json, status, exit_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                created,
                step,
                method,
                label,
                raw,
                json.dumps(dict(params), ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
                status,
                exit_code,
            ),
        )


def execute(commands: list[Command], catalog: Catalog, *, dry_run: bool = False) -> dict[str, Any]:
    """Run a parsed pipeline through the unified MCP gateway, failing fast."""
    runtime: dict[str, Any] = {}
    if not dry_run:
        runtime = build_runtime(catalog)
    steps: list[dict[str, Any]] = []
    exit_code = EXIT_OK
    stopped_at: int | None = None

    for command in commands:
        if dry_run:
            entry = command.plan()
            entry["status"] = "planned"
            steps.append(entry)
            record_audit(
                str(catalog.path),
                step=command.step,
                method=command.method,
                label=command.label,
                raw=command.raw,
                params=command.params,
                result=plan([command]),
                status="dry_run",
                exit_code=EXIT_OK,
            )
            continue

        status, body = ms.handle(_request(command), _headers(command), **runtime)
        error = body.get("error") if isinstance(body, Mapping) else None
        if error is not None:
            step_code = _exit_code_for(error)
            entry = command.plan()
            entry.update(
                {
                    "status": "failed",
                    "http_status": status,
                    "error": dict(error),
                    "exit_code": step_code,
                }
            )
            steps.append(entry)
            exit_code = step_code
            stopped_at = command.step
            record_audit(
                str(catalog.path),
                step=command.step,
                method=command.method,
                label=command.label,
                raw=command.raw,
                params=command.params,
                result={"error": dict(error)},
                status="failed",
                exit_code=step_code,
            )
            break

        result = body.get("result") if isinstance(body, Mapping) else None
        entry = command.plan()
        entry.update({"status": "ok", "http_status": status, "result": result})
        steps.append(entry)
        record_audit(
            str(catalog.path),
            step=command.step,
            method=command.method,
            label=command.label,
            raw=command.raw,
            params=command.params,
            result=result,
            status="ok",
            exit_code=EXIT_OK,
        )

    return {
        "command": "arabic-dsl",
        "status": "ok" if exit_code == EXIT_OK else "failed",
        "dry_run": dry_run,
        "steps": steps,
        "stopped_at": stopped_at,
        "exit_code": exit_code,
    }
