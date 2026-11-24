"""Deterministic local MCP-Persona simulator.

The simulator replays canned tool chains from in-memory fixtures only. It never
reads production data, loads credentials, or opens a network connection. Its job
is to exercise failure, retry, redaction and permission-bypass paths so a caller
can test policy wiring without touching a real tool.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import mcp_stateless as ms
from .policy_gateway import PolicyDenied
from .redact import redact
from .tasks import TaskStore

__all__ = ["PersonaSimulator", "PersonaResult", "SCENARIOS"]

ToolPolicy = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]

SCENARIOS = (
    "safe_chain",
    "retry_then_success",
    "secret_leak",
    "permission_bypass",
)

# Assembled at runtime so no credential-shaped literal is stored in the repo.
_FAKE_SECRET = "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz"

_LOCAL_FIXTURE_TOOLS = frozenset(
    {
        "route_task",
        "search_servers",
        "get_server",
        "query_catalog",
        "description_smell_audit",
        "policy_snapshot",
        "doctor_report",
    }
)


@dataclass(frozen=True, slots=True)
class _Step:
    tool: str
    arguments: dict[str, Any]
    result: str
    attempts: int = 1
    from_skill: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "result": self.result,
            "attempts": self.attempts,
            "from_skill": self.from_skill,
        }


@dataclass(frozen=True, slots=True)
class PersonaResult:
    """The stable output of one simulated persona run."""

    scenario: str
    task_id: str
    status: str
    transcript: tuple[dict[str, Any], ...]
    redactions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "task_id": self.task_id,
            "status": self.status,
            "transcript": list(self.transcript),
            "redactions": list(self.redactions),
            "network_calls": 0,
            "real_keys_loaded": False,
        }


def _redact_args(args: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str):
            cleaned[key] = redact(value)
        elif isinstance(value, list):
            cleaned[key] = [redact(item) if isinstance(item, str) else item for item in value]
        elif isinstance(value, dict):
            cleaned[key] = _redact_args(value)
        else:
            cleaned[key] = value
    return cleaned


def _steps(scenario: str) -> tuple[_Step, ...]:
    if scenario == "safe_chain":
        return (
            _Step("route_task", {"task": "ابحث عن خادم sql"}, "ranked: search_servers"),
            _Step("search_servers", {"query": "sql", "limit": 1}, "fixture: 1 server"),
            _Step("get_server", {"server_id": 1}, "fixture: sqlite"),
        )
    if scenario == "retry_then_success":
        return (
            _Step("search_servers", {"query": "retry"}, "transient failure", attempts=1),
            _Step("search_servers", {"query": "retry"}, "fixture: recovered", attempts=2),
        )
    if scenario == "secret_leak":
        return (
            _Step(
                "search_servers",
                {"query": "token=" + _FAKE_SECRET},
                "fixture: secret never echoed",
            ),
        )
    if scenario == "permission_bypass":
        return (
            _Step(
                "deploy_production",
                {"target": "prod"},
                "blocked: skill text is not executable permission",
                from_skill="mcp-hub-catalog",
            ),
        )
    raise ValueError(f"unknown persona scenario: {scenario}")


class PersonaSimulator:
    """Replay a canned scenario through the configured tool policy."""

    def __init__(self, tasks: TaskStore, tool_policy: ToolPolicy | None = None) -> None:
        self.tasks = tasks
        self.tool_policy = tool_policy

    def _policy_step(self, step: _Step) -> tuple[str, dict[str, Any], str, bool]:
        if step.tool not in _LOCAL_FIXTURE_TOOLS:
            return (
                "blocked",
                _redact_args(step.arguments),
                "blocked: tool is not executable from a skill read",
                False,
            )
        arguments = dict(step.arguments)
        if self.tool_policy is not None:
            try:
                checked = self.tool_policy(step.tool, arguments)
            except (ms.MCPError, PolicyDenied):
                return "blocked", _redact_args(arguments), "blocked by tool policy", False
            arguments = dict(checked)
        cleaned = _redact_args(arguments)
        redacted = json.dumps(cleaned, sort_keys=True) != json.dumps(arguments, sort_keys=True)
        status = "failed" if step.result == "transient failure" else "ok"
        return status, cleaned, step.result, redacted

    def run(
        self,
        scenario: str,
        tenant_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> PersonaResult:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown persona scenario: {scenario}")
        task = self.tasks.create(
            tenant_id,
            session_id,
            kind=f"persona:{scenario}",
            payload={"scenario": scenario},
            task_id=task_id,
        )
        identifier = str(task["task_id"])
        transcript: list[dict[str, Any]] = []
        redactions: list[str] = []
        blocked = False
        for index, step in enumerate(_steps(scenario), start=1):
            self.tasks.update(
                identifier,
                tenant_id,
                session_id,
                status="running",
                attempts=index,
                event="persona_step_start",
            )
            status, arguments, result, did_redact = self._policy_step(step)
            if did_redact:
                redactions.append("secret")
            if status == "blocked":
                blocked = True
            entry = step.to_dict()
            entry.update(
                {
                    "index": index,
                    "status": status,
                    "arguments": arguments,
                    "result": result,
                }
            )
            transcript.append(entry)
            self.tasks.update(
                identifier,
                tenant_id,
                session_id,
                status="running",
                result={"step": index, "status": status},
                event="persona_step_done",
            )
        final_status = "completed" if not blocked else "cancelled"
        self.tasks.update(
            identifier,
            tenant_id,
            session_id,
            status=final_status,
            result={"transcript_steps": len(transcript)},
            event="persona_finished",
        )
        return PersonaResult(
            scenario, identifier, final_status, tuple(transcript), tuple(redactions)
        )
