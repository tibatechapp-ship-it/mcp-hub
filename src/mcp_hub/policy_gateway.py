"""Zyko-compatible policy gateway for mcp-hub tool calls.

The gateway is deliberately a pure-stdlib subset of ZYKO's Policy-as-Code
schema: ``tools`` allow/deny, ``privacy`` redaction and ``audit``. Controls that
ZYKO understands but mcp-hub cannot enforce (models, providers, budgets,
rate_limit) are rejected instead of being silently ignored, so a policy file
cannot *look* protective while doing nothing here.

Policies are JSON (ZYKO's YAML shape maps 1:1 onto the same dictionaries) and
come from ``MCP_HUB_POLICY_FILE`` or inline ``MCP_HUB_POLICY_JSON``.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .redact import redact

__all__ = [
    "ENV_POLICY_FILE",
    "ENV_POLICY_JSON",
    "ENV_AUDIT_PATH",
    "ConfigError",
    "PolicyDenied",
    "PolicyDecision",
    "PolicyGateway",
    "builtin_default_policy",
]

ENV_POLICY_FILE = "MCP_HUB_POLICY_FILE"
ENV_POLICY_JSON = "MCP_HUB_POLICY_JSON"
ENV_AUDIT_PATH = "MCP_HUB_POLICY_AUDIT"

_SUPPORTED_TOP_LEVEL = frozenset({"version", "name", "tools", "skills", "privacy", "audit"})
_SUPPORTED_TOOLS = frozenset({"allow", "deny"})
_SUPPORTED_SKILLS = frozenset({"allow", "deny"})
_SUPPORTED_PRIVACY = frozenset({"redact", "custom"})
_SUPPORTED_AUDIT = frozenset({"enabled", "path"})

# Same names as ZYKO's builtins. `secret` is an mcp-hub extension that reuses
# the existing `redact.py` secret rules on every tool argument.
_BUILTIN_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    "email": (
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        "[EMAIL]",
    ),
    "ssn": (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    "iban": (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[IBAN]"),
    "phone": (re.compile(r"(?<![\w-])\+?\d[\d\s().\-]{8,}\d(?![\w-])"), "[PHONE]"),
}


class ConfigError(ValueError):
    """The policy document is invalid or asks for an unsupported control."""


class PolicyDenied(Exception):
    """A tool call was blocked by a policy rule."""

    def __init__(self, reason: str, rule: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule = rule


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    arguments: dict[str, Any] | None = None
    redactions: tuple[str, ...] = ()


def builtin_default_policy() -> dict[str, Any]:
    """Permissive tool gate that still redacts secrets from tool arguments."""
    return {
        "version": 1,
        "name": "default",
        "tools": {"allow": ["*"], "deny": []},
        "skills": {"allow": ["*"], "deny": []},
        "privacy": {"redact": ["secret"]},
        "audit": {"enabled": False},
    }


def _check_section(doc: dict[str, Any], key: str, allowed: frozenset[str]) -> None:
    value = doc.get(key)
    if value is None:
        return
    if not isinstance(value, dict):
        raise ConfigError(f"policy section '{key}' must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"unsupported keys in '{key}': {', '.join(unknown)}")


def _validate(doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        raise ConfigError("policy document must be an object")
    unknown = sorted(set(doc) - _SUPPORTED_TOP_LEVEL)
    if unknown:
        raise ConfigError(f"unsupported policy controls: {', '.join(unknown)}")
    version = doc.get("version", 1)
    if version != 1:
        raise ConfigError(f"unsupported policy schema version: {version!r} (expected 1)")
    _check_section(doc, "tools", _SUPPORTED_TOOLS)
    _check_section(doc, "skills", _SUPPORTED_SKILLS)
    _check_section(doc, "privacy", _SUPPORTED_PRIVACY)
    _check_section(doc, "audit", _SUPPORTED_AUDIT)


@dataclass(frozen=True, slots=True)
class PolicyGateway:
    """Evaluate a tool call against a validated ZYKO-compatible JSON policy."""

    name: str = "default"
    tool_allow: tuple[str, ...] = ("*",)
    tool_deny: tuple[str, ...] = ()
    skill_allow: tuple[str, ...] = ("*",)
    skill_deny: tuple[str, ...] = ()
    redact_names: tuple[str, ...] = ("secret",)
    custom_rules: tuple[tuple[re.Pattern[str], str], ...] = ()
    audit_enabled: bool = False
    audit_path: Path | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> PolicyGateway:
        _validate(doc)
        tools = doc.get("tools") or {}
        privacy = doc.get("privacy") or {}
        audit = doc.get("audit") or {}
        allow = tools.get("allow", ["*"])
        deny = tools.get("deny", [])
        if not isinstance(allow, list) or not all(isinstance(x, str) for x in allow):
            raise ConfigError("tools.allow must be a list of strings")
        if not isinstance(deny, list) or not all(isinstance(x, str) for x in deny):
            raise ConfigError("tools.deny must be a list of strings")
        skills = doc.get("skills") or {}
        skill_allow = skills.get("allow", ["*"])
        skill_deny = skills.get("deny", [])
        if not isinstance(skill_allow, list) or not all(isinstance(x, str) for x in skill_allow):
            raise ConfigError("skills.allow must be a list of strings")
        if not isinstance(skill_deny, list) or not all(isinstance(x, str) for x in skill_deny):
            raise ConfigError("skills.deny must be a list of strings")
        names = privacy.get("redact") or ["secret"]
        if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
            raise ConfigError("privacy.redact must be a list of strings")
        unknown = sorted(set(names) - set(_BUILTIN_PATTERNS) - {"secret"})
        if unknown:
            raise ConfigError(f"unknown redaction pattern: {', '.join(unknown)}")
        custom: list[tuple[re.Pattern[str], str]] = []
        for rule in privacy.get("custom") or []:
            if not isinstance(rule, dict) or "pattern" not in rule:
                raise ConfigError("privacy.custom entries need a pattern")
            try:
                compiled = re.compile(str(rule["pattern"]))
            except re.error as exc:
                raise ConfigError(f"invalid custom redaction pattern: {exc}") from exc
            custom.append((compiled, str(rule.get("replace", "[REDACTED]"))))
        if not isinstance(audit, dict):
            raise ConfigError("audit must be an object")
        enabled = bool(audit.get("enabled", False))
        path_value = os.environ.get(ENV_AUDIT_PATH) or audit.get("path")
        audit_path = Path(path_value).expanduser() if path_value else None
        if enabled and audit_path is None:
            audit_path = Path("~/.cache/mcp-hub-policy-audit.jsonl").expanduser()
        return cls(
            name=str(doc.get("name") or "default"),
            tool_allow=tuple(allow) or ("*",),
            tool_deny=tuple(deny),
            skill_allow=tuple(skill_allow) or ("*",),
            skill_deny=tuple(skill_deny),
            redact_names=tuple(names),
            custom_rules=tuple(custom),
            audit_enabled=enabled,
            audit_path=audit_path,
        )

    @classmethod
    def from_env(cls) -> PolicyGateway:
        inline = os.environ.get(ENV_POLICY_JSON, "").strip()
        path = os.environ.get(ENV_POLICY_FILE, "").strip()
        if inline and path:
            raise ConfigError(f"set either {ENV_POLICY_JSON} or {ENV_POLICY_FILE}, not both")
        if path:
            return cls.from_doc(json.loads(Path(path).expanduser().read_text(encoding="utf-8")))
        if inline:
            return cls.from_doc(json.loads(inline))
        return cls.from_doc(builtin_default_policy())

    def allows_tool(self, name: str) -> bool:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in self.tool_deny):
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.tool_allow)

    def allows_skill(self, name: str) -> bool:
        """Skill reading is governed separately from tool execution."""

        if any(fnmatch.fnmatchcase(name, pattern) for pattern in self.skill_deny):
            return False
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in self.skill_allow)

    def enforce_skill(self, name: str) -> None:
        if not self.allows_skill(name):
            raise PolicyDenied(f"Skill blocked by policy: {name}", f"skills.deny:{name}")

    def _scrub(self, value: Any, applied: list[str]) -> Any:
        if isinstance(value, str):
            cleaned = redact(value)
            if cleaned != value:
                applied.append("secret")
            for pattern, replacement in self.custom_rules:
                cleaned, count = pattern.subn(replacement, cleaned)
                if count:
                    applied.extend(["custom"] * count)
            for label in self.redact_names:
                if label == "secret" or label not in _BUILTIN_PATTERNS:
                    continue
                pattern, replacement = _BUILTIN_PATTERNS[label]
                cleaned, count = pattern.subn(replacement, cleaned)
                if count:
                    applied.extend([label] * count)
            return cleaned
        if isinstance(value, list):
            return [self._scrub(item, applied) for item in value]
        if isinstance(value, dict):
            return {key: self._scrub(item, applied) for key, item in value.items()}
        return value

    def _audit(self, name: str, decision: PolicyDecision) -> None:
        if not self.audit_enabled or self.audit_path is None:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "policy": self.name,
            "tool": name,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "redactions": list(decision.redactions),
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def check(self, name: str, args: Mapping[str, Any]) -> PolicyDecision:
        """Return sanitized arguments or a deny decision; never mutate inputs."""
        if not self.allows_tool(name):
            decision = PolicyDecision(False, f"Tool blocked by policy: {name}")
            self._audit(name, decision)
            return decision
        applied: list[str] = []
        sanitized = self._scrub(dict(args), applied)
        if not isinstance(sanitized, dict):  # pragma: no cover - dict args always scrub to dict
            sanitized = {}
        decision = PolicyDecision(True, "", sanitized, tuple(applied))
        self._audit(name, decision)
        return decision

    def enforce(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        """Like :meth:`check` but raise :class:`PolicyDenied` on a block."""
        decision = self.check(name, args)
        if not decision.allowed:
            raise PolicyDenied(decision.reason, f"tools.deny:{name}")
        return decision.arguments or {}

    def snapshot(self) -> dict[str, Any]:
        """Describe the active gate without exposing audit paths or regex bodies."""
        return {
            "policy": self.name,
            "tools_allow": list(self.tool_allow),
            "tools_deny": list(self.tool_deny),
            "skills_allow": list(self.skill_allow),
            "skills_deny": list(self.skill_deny),
            "redactions": list(self.redact_names),
            "custom_redaction_rules": len(self.custom_rules),
            "audit_enabled": self.audit_enabled,
        }
