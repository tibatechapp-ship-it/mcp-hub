"""Arabic playbooks (``.arflow``) over the existing Arabic DSL.

A playbook is a UTF-8 text file with Arabic comments, Arabic variable
definitions and the 27 commands already implemented by :mod:`arabic_dsl`.
This module is a thin compiler and runner: it never adds an MCP method, changes
a tool spec, re-implements policy or writes to the ``arabic_dsl_audit`` schema.

Variables are resolved through the real DSL parser with an ASCII sentinel. A
``${name}`` placeholder is accepted only when the parser places the sentinel in
a free-text ``query``/``task`` argument; identifiers, actors, reasons, tasks and
skill names are never substituted.
"""

from __future__ import annotations

import json
import re
import sqlite3
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from . import __version__, arabic_dsl
from .arabic_runtime import normalize_for_match, resolve_lang
from .database import Catalog

__all__ = [
    "AUDIT_TABLE",
    "ArflowError",
    "Issue",
    "Step",
    "Playbook",
    "Compiled",
    "build",
    "check",
    "run",
    "record_playbook_audit",
    "MAX_PLAYBOOK_BYTES",
    "ISSUE_CODES",
]

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_BAD_INPUT = 2

AUDIT_TABLE = "arflow_audit"
MAX_PLAYBOOK_BYTES = 1024 * 1024

ISSUE_CODES = frozenset(
    {
        "EMPTY_PLAYBOOK",
        "UNKNOWN_COMMAND",
        "BAD_ARGUMENT",
        "UNDEFINED_VARIABLE",
        "DUPLICATE_DEFINITION",
        "INVALID_DEFINITION",
        "INVALID_VARIABLE_VALUE",
        "SUBSTITUTION_OUTSIDE_QUERY",
        "PLAYBOOK_TOO_LARGE",
    }
)

_NAME_RE = re.compile(r"^[^\W\d][\w]*$", re.UNICODE)
_COMMENT_PREFIXES = ("#", "//")
_DEF_KEYWORDS = frozenset({"عرف", "ليكن", "لتكن", "set", "let", "var"})
_OPTIONAL_WORDS = ("اختياري", "optional")
_SEPARATORS = (";", "؛", "\n", "\r")


class ArflowError(ValueError):
    """A compilation or validation failure with a stable CLI exit code."""

    def __init__(self, message: str, *, code: int = EXIT_BAD_INPUT) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Issue:
    """One static-analysis finding, tied to a source line."""

    code: str
    line: int
    message: str
    raw: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "line": self.line,
            "message": self.message,
            "raw": self.raw,
        }


@dataclass(frozen=True, slots=True)
class Step:
    """One compiled command and its optional-step marker."""

    step: int
    line: int
    raw: str
    original: str
    optional: bool
    command: arabic_dsl.Command


@dataclass(frozen=True, slots=True)
class Playbook:
    """A validated playbook ready for ``arabic_dsl.execute``."""

    name: str
    raw_text: str
    variables: dict[str, str]
    steps: tuple[Step, ...]
    context: arabic_dsl.DslContext


@dataclass(frozen=True, slots=True)
class Compiled:
    """Result of a parse-only compilation; ``playbook`` is None on issues."""

    playbook: Playbook | None
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return self.playbook is not None and not self.issues


def _issue(code: str, line: int, message: str, raw: str) -> Issue:
    return Issue(code=code, line=line, message=message, raw=raw)


def _strip_marker(line: str) -> tuple[bool, str]:
    """Remove an optional-step marker and report whether it was present."""
    value = line.strip()
    if value.startswith("?"):
        return True, value[1:].strip()
    if value.endswith("?"):
        return True, value[:-1].strip()
    folded = normalize_for_match(value)
    for word in _OPTIONAL_WORDS:
        if folded.startswith(word + " "):
            return True, value.split(maxsplit=1)[1].strip()
        if folded.endswith(" " + word):
            return True, " ".join(value.split()[:-1]).strip()
    return False, value


def _definition(line: str) -> tuple[str, str] | None:
    """Parse ``عَرِّف <name> = <value>`` (and aliases) into name/value."""
    head, separator, tail = line.partition("=")
    if not separator:
        return None
    tokens = head.split()
    if len(tokens) < 2 or normalize_for_match(tokens[0]) not in _DEF_KEYWORDS:
        return None
    return tokens[-1], _unquote(tail.strip())


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _sentinel(index: int) -> str:
    """All-ASCII-digit token: parses anywhere, so the probe can classify it."""
    return f"9900{index:04d}0099"


def _sentinel_in_query(command: arabic_dsl.Command, token: str) -> bool:
    """True only when the DSL parser put the sentinel in a query/task argument."""
    serialized = json.dumps(command.params, ensure_ascii=False, sort_keys=True)
    if token not in serialized:
        return False
    probe = deepcopy(command.params)
    arguments = probe.get("arguments")
    if isinstance(arguments, dict):
        for key in ("query", "task"):
            value = arguments.get(key)
            if isinstance(value, str):
                arguments[key] = value.replace(token, "")
    return token not in json.dumps(probe, ensure_ascii=False, sort_keys=True)


def _parse_error(line: int, raw: str, exc: arabic_dsl.DslError) -> Issue:
    code = "UNKNOWN_COMMAND" if "unrecognized command" in str(exc).lower() else "BAD_ARGUMENT"
    return _issue(code, line, str(exc), raw)


def _probe_error(line: int, raw: str, exc: arabic_dsl.DslError) -> Issue:
    """A digit sentinel that cannot parse is a non-query substitution."""
    return _issue(
        "SUBSTITUTION_OUTSIDE_QUERY",
        line,
        "variables are allowed only in free-text query arguments",
        raw,
    )


def _parse_step(
    line: int,
    raw: str,
    context: arabic_dsl.DslContext,
    variables: dict[str, str],
) -> tuple[str, arabic_dsl.Command | None, Issue | None]:
    """Resolve a single step; return its final text, command and any issue."""
    if ";" in raw or "؛" in raw:
        return (
            raw,
            None,
            _issue("BAD_ARGUMENT", line, "use one command per line in a playbook", raw),
        )
    placeholders = list(re.finditer(r"\$\{([^}]*)\}", raw))
    if not placeholders:
        try:
            return raw, arabic_dsl.parse(raw, context)[0], None
        except arabic_dsl.DslError as exc:
            return raw, None, _parse_error(line, raw, exc)

    resolved = raw
    tokens: list[tuple[str, str]] = []
    for index, match in enumerate(placeholders):
        name = match.group(1)
        if name not in variables:
            return raw, None, _issue("UNDEFINED_VARIABLE", line, f"undefined variable: {name}", raw)
        token = _sentinel(index)
        resolved = resolved.replace(match.group(0), token, 1)
        tokens.append((token, variables[name]))

    try:
        probe_command = arabic_dsl.parse(resolved, context)[0]
    except arabic_dsl.DslError as exc:
        return raw, None, _probe_error(line, raw, exc)

    for token, _ in tokens:
        if not _sentinel_in_query(probe_command, token):
            return (
                raw,
                None,
                _issue(
                    "SUBSTITUTION_OUTSIDE_QUERY",
                    line,
                    "variables are allowed only in free-text query arguments",
                    raw,
                ),
            )

    final_text = resolved
    for token, value in tokens:
        final_text = final_text.replace(token, value)
    try:
        return final_text, arabic_dsl.parse(final_text, context)[0], None
    except arabic_dsl.DslError as exc:
        return final_text, None, _parse_error(line, raw, exc)


def build(
    text: str,
    *,
    name: str = "inline",
    context: arabic_dsl.DslContext | None = None,
    overrides: dict[str, str] | None = None,
) -> Compiled:
    """Compile a playbook without running any MCP handler."""
    ctx = context or arabic_dsl.DslContext()
    if len(text.encode("utf-8")) > MAX_PLAYBOOK_BYTES:
        return Compiled(None, (_issue("PLAYBOOK_TOO_LARGE", 0, "playbook too large", ""),))

    override_values: dict[str, str] = {}
    for key, value in (overrides or {}).items():
        if not _NAME_RE.fullmatch(key):
            return Compiled(
                None, (_issue("INVALID_DEFINITION", 0, f"invalid variable: {key}", ""),)
            )
        if any(separator in value for separator in _SEPARATORS):
            return Compiled(
                None,
                (_issue("INVALID_VARIABLE_VALUE", 0, f"invalid value for {key}", ""),),
            )
        override_values[key] = value

    definitions: dict[str, str] = {}
    issues: list[Issue] = []
    steps: list[Step] = []
    for line_number, source in enumerate(text.splitlines(), 1):
        stripped = source.strip()
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue
        optional, command_text = _strip_marker(stripped)
        if not command_text:
            continue

        definition = _definition(command_text)
        if definition is not None:
            var_name, value = definition
            if not _NAME_RE.fullmatch(var_name):
                issues.append(
                    _issue(
                        "INVALID_DEFINITION",
                        line_number,
                        f"invalid variable: {var_name}",
                        source,
                    )
                )
                continue
            if any(separator in value for separator in _SEPARATORS):
                issues.append(
                    _issue(
                        "INVALID_VARIABLE_VALUE",
                        line_number,
                        f"invalid value for {var_name}",
                        source,
                    )
                )
                continue
            if var_name in definitions:
                issues.append(
                    _issue(
                        "DUPLICATE_DEFINITION",
                        line_number,
                        f"duplicate variable: {var_name}",
                        source,
                    )
                )
                continue
            definitions[var_name] = value
            continue

        resolved, command, issue = _parse_step(
            line_number, command_text, ctx, {**definitions, **override_values}
        )
        if issue is not None:
            issues.append(issue)
            continue
        assert command is not None  # noqa: S101 - _parse_step returns one or an issue
        step_number = len(steps) + 1
        steps.append(
            Step(
                step=step_number,
                line=line_number,
                raw=resolved,
                original=command_text,
                optional=optional,
                command=replace(command, step=step_number),
            )
        )

    if not steps and not issues:
        issues.append(_issue("EMPTY_PLAYBOOK", 0, "no commands in playbook", ""))
    if issues:
        return Compiled(None, tuple(issues))
    return Compiled(
        Playbook(
            name=name,
            raw_text=text,
            variables={**definitions, **override_values},
            steps=tuple(steps),
            context=ctx,
        ),
        (),
    )


def check(
    text: str,
    *,
    name: str = "inline",
    context: arabic_dsl.DslContext | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Static check: stable issue codes plus the resolved dry-run plan."""
    compiled = build(text, name=name, context=context, overrides=overrides)
    issues = [issue.as_dict() for issue in compiled.issues]
    payload: dict[str, Any] = {
        "command": "arflow-check",
        "status": "ok" if compiled.ok else "failed",
        "issues": issues,
        "plan": arabic_dsl.plan([step.command for step in compiled.playbook.steps])
        if compiled.playbook is not None
        else [],
        "exit_code": EXIT_OK if compiled.ok else EXIT_BAD_INPUT,
    }
    return payload


def record_playbook_audit(
    db_path: str,
    *,
    name: str,
    raw_text: str,
    variables: dict[str, str],
    plan: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    status: str,
    exit_code: int,
    context: arabic_dsl.DslContext,
) -> None:
    """Append a playbook-level row without touching the landed DSL schema."""
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as con:
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, "
            "playbook TEXT NOT NULL, "
            "raw_text TEXT NOT NULL, "
            "variables_json TEXT NOT NULL, "
            "plan_json TEXT NOT NULL, "
            "steps_json TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "exit_code INTEGER NOT NULL, "
            "tenant_id TEXT NOT NULL, "
            "session_id TEXT NOT NULL, "
            "actor TEXT NOT NULL, "
            "lang TEXT NOT NULL)"
        )
        con.execute(
            f"INSERT INTO {AUDIT_TABLE} "
            "(created_at, playbook, raw_text, variables_json, plan_json, steps_json, "
            "status, exit_code, tenant_id, session_id, actor, lang) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                created,
                name,
                raw_text,
                json.dumps(variables, ensure_ascii=False, sort_keys=True),
                json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str),
                json.dumps(steps, ensure_ascii=False, sort_keys=True, default=str),
                status,
                exit_code,
                context.tenant_id,
                context.session_id,
                context.actor,
                resolve_lang(context.lang),
            ),
        )


def run(
    catalog: Catalog,
    compiled: Compiled,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a compiled playbook only through ``arabic_dsl.execute``."""
    if not compiled.ok or compiled.playbook is None:
        raise ArflowError("cannot run a playbook with check issues")
    playbook = compiled.playbook
    context = playbook.context
    steps: list[dict[str, Any]] = []
    optional_failures: list[int] = []
    exit_code = EXIT_OK
    stopped_at: int | None = None

    for step in playbook.steps:
        payload = arabic_dsl.execute([step.command], catalog, dry_run=dry_run)
        entry = dict(payload["steps"][0])
        entry.update(
            {
                "playbook_step": step.step,
                "line": step.line,
                "optional": step.optional,
                "original": step.original,
            }
        )
        if payload["exit_code"] != EXIT_OK:
            if step.optional:
                entry["status"] = "optional_failed"
                optional_failures.append(step.step)
            else:
                entry["status"] = "failed"
                exit_code = int(payload["exit_code"])
                stopped_at = step.step
                steps.append(entry)
                break
        steps.append(entry)

    report: dict[str, Any] = {
        "command": "arflow-run",
        "status": "ok" if exit_code == EXIT_OK else "failed",
        "playbook": playbook.name,
        "dry_run": dry_run,
        "steps": steps,
        "stopped_at": stopped_at,
        "optional_failures": optional_failures,
        "exit_code": exit_code,
        "version": __version__,
    }
    record_playbook_audit(
        str(catalog.path),
        name=playbook.name,
        raw_text=playbook.raw_text,
        variables=playbook.variables,
        plan=arabic_dsl.plan([step.command for step in playbook.steps]),
        steps=steps,
        status=str(report["status"]),
        exit_code=exit_code,
        context=context,
    )
    return report
