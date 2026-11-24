#!/usr/bin/env python3
"""Validate the unified Arabic MCP registry.

Checks native MCP config fields, Arabic metadata, uniqueness, and that no
secret values leaked into ``env``. Importable as a module for tests.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "registry" / "mcp_servers.json"
ARABIC = re.compile(r"[\u0600-\u06ff]")
TRANSPORTS = {"stdio", "sse", "streamable-http", "none"}
CORE_IDS = {
    "github",
    "git",
    "serena",
    "context7",
    "supabase",
    "postgres",
    "sqlite",
    "dbhub",
    "playwright",
    "chrome-devtools",
    "firecrawl",
    "fetch",
    "brave-search",
    "filesystem",
    "desktop-commander",
    "markitdown",
    "mcp-hub",
    "arabic-tools",
    "fast-anything",
}
ACCESS_MODES = {"read-only", "read-write"}
KNOWN_TAGS = {
    "action-execution",
    "browser-web",
    "diff",
    "eval-observability",
    "execution",
    "files-system",
    "localization",
    "memory-context",
    "rag-web-search",
    "security-scan",
    "static-analysis",
    "symbol-search",
}
PROFILE_IDS = {
    "ai-agent-builder",
    "automation-ops",
    "code-review",
    "docs-localization",
    "quality-security-audit",
}
DEFAULT_PROFILES_DIR = ROOT / "profiles"


def load(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    """Read the registry as strict UTF-8 (invalid bytes raise UnicodeDecodeError)."""
    text = path.read_text(encoding="utf-8")
    if "\ufffd" in text:
        raise ValueError("registry contains the Unicode replacement character")
    return json.loads(text)


def _arabic_errors(where: str, value: Any, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip() or not ARABIC.search(value):
        errors.append(f"{where}: missing Arabic text")


def load_profile_ids(path: Path = DEFAULT_PROFILES_DIR) -> set[str]:
    """Read profile ids from disk; fall back to the known ids in this script."""
    if not path.is_dir():
        return set(PROFILE_IDS)
    ids: set[str] = set()
    for file in sorted(path.glob("*.json")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            pid = payload.get("profile_id")
            if isinstance(pid, str) and pid.strip():
                ids.add(pid)
    return ids or set(PROFILE_IDS)


def validate_profiles(path: Path = DEFAULT_PROFILES_DIR) -> list[str]:
    """Validate the five JSON task profiles used by catalog capability fields."""
    errors: list[str] = []
    if not path.is_dir():
        return [f"profiles: directory missing: {path}"]
    files = sorted(path.glob("*.json"))
    if not files:
        return ["profiles: no JSON profiles found"]
    seen: set[str] = set()
    for file in files:
        prefix = f"profiles.{file.stem}"
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{prefix}: unreadable JSON: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        pid = payload.get("profile_id")
        if not isinstance(pid, str) or not pid.strip():
            errors.append(f"{prefix}.profile_id: required non-empty string")
        elif pid in seen:
            errors.append(f"{prefix}.profile_id: duplicate {pid}")
        else:
            seen.add(pid)
            if pid != file.stem:
                errors.append(f"{prefix}.profile_id: does not match filename")
        for key in ("title_ar", "description_ar"):
            if isinstance(payload.get(key), str):
                _arabic_errors(f"{prefix}.{key}", payload[key], errors)
            else:
                errors.append(f"{prefix}.{key}: required Arabic string")
        if payload.get("default_access_mode") not in ACCESS_MODES:
            errors.append(
                f"{prefix}.default_access_mode: unsupported value "
                f"{payload.get('default_access_mode')!r}"
            )
        for key in ("allowed_categories", "preferred_tags"):
            value = payload.get(key)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(v, str) and v.strip() for v in value)
            ):
                errors.append(f"{prefix}.{key}: must be a non-empty list of strings")
        tags = payload.get("preferred_tags")
        if isinstance(tags, list):
            unknown = sorted({t for t in tags if isinstance(t, str) and t not in KNOWN_TAGS})
            if unknown:
                errors.append(f"{prefix}.preferred_tags: unknown tags: {', '.join(unknown)}")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{prefix}.steps: must be a non-empty list")
        else:
            for index, step in enumerate(steps):
                sprefix = f"{prefix}.steps[{index}]"
                if not isinstance(step, dict):
                    errors.append(f"{sprefix}: expected an object")
                    continue
                if not isinstance(step.get("step_id"), str) or not step["step_id"].strip():
                    errors.append(f"{sprefix}.step_id: required non-empty string")
                if isinstance(step.get("title_ar"), str):
                    _arabic_errors(f"{sprefix}.title_ar", step["title_ar"], errors)
                else:
                    errors.append(f"{sprefix}.title_ar: required Arabic string")
                step_tags = step.get("tool_tags")
                if (
                    not isinstance(step_tags, list)
                    or not step_tags
                    or not all(isinstance(t, str) and t.strip() for t in step_tags)
                ):
                    errors.append(f"{sprefix}.tool_tags: must be a non-empty list of strings")
                if step.get("access_mode") is not None and step["access_mode"] not in ACCESS_MODES:
                    errors.append(f"{sprefix}.access_mode: unsupported value")
        if (
            payload.get("requires_explicit_approval") is True
            and payload.get("default_access_mode") != "read-write"
        ):
            errors.append(f"{prefix}: explicit approval requires read-write access mode")
    missing = sorted(PROFILE_IDS - seen)
    if missing:
        errors.append(f"profiles: missing required profiles: {', '.join(missing)}")
    return sorted(errors)


def _validate_capabilities(
    where: str,
    entry: dict[str, Any],
    env_keys: set[str],
    profile_ids: set[str],
    errors: list[str],
) -> None:
    mode = entry.get("access_mode")
    if mode is not None and mode not in ACCESS_MODES:
        errors.append(f"{where}.access_mode: unsupported value {mode!r}")
    for key, allowed in (("tags", KNOWN_TAGS), ("task_profiles", profile_ids)):
        value = entry.get(key)
        if value is None:
            continue
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(v, str) and v.strip() for v in value)
        ):
            errors.append(f"{where}.{key}: must be a non-empty list of strings")
            continue
        unknown = sorted({v for v in value if v not in allowed})
        if unknown:
            errors.append(f"{where}.{key}: unknown values: {', '.join(unknown)}")
    requires = entry.get("requires_env")
    if requires is None:
        return
    if not isinstance(requires, list) or not all(
        isinstance(v, str) and v.strip() for v in requires
    ):
        errors.append(f"{where}.requires_env: must be a list of strings")
        return
    unknown_env = sorted(set(requires) - env_keys)
    if unknown_env:
        errors.append(f"{where}.requires_env: keys not declared in env: {', '.join(unknown_env)}")


def validate(
    data: dict[str, Any],
    *,
    profile_ids: set[str] | None = None,
    check_profiles: bool = True,
) -> list[str]:
    """Return a sorted list of human-readable validation errors (empty = valid)."""
    errors: list[str] = []
    if check_profiles:
        errors.extend(validate_profiles())
    profile_ids = profile_ids or load_profile_ids()
    if not isinstance(data, dict):
        return ["root: expected a JSON object"]
    if not isinstance(data.get("schema_version"), str):
        errors.append("root: schema_version must be a string")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return errors + ["root: mcpServers must be a non-empty object"]

    seen_commands: dict[tuple[str, tuple[str, ...]], str] = {}
    seen_urls: dict[str, str] = {}
    seen_ports: dict[tuple[str, int], str] = {}

    for sid, entry in servers.items():
        prefix = f"servers.{sid}"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: expected an object")
            continue
        if entry.get("id") != sid:
            errors.append(f"{prefix}: id does not match key")

        for key in (
            "name_ar",
            "description_ar",
            "category",
            "category_ar",
            "source",
            "status",
            "transport",
        ):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                errors.append(f"{prefix}.{key}: required non-empty string")
        for key in ("name_ar", "description_ar", "category_ar"):
            if isinstance(entry.get(key), str):
                _arabic_errors(f"{prefix}.{key}", entry[key], errors)

        transport = entry.get("transport")
        if transport not in TRANSPORTS:
            errors.append(f"{prefix}.transport: unsupported value {transport!r}")

        if transport == "stdio":
            command = entry.get("command")
            args = entry.get("args")
            if not isinstance(command, str) or not command.strip():
                errors.append(f"{prefix}.command: required for stdio")
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                errors.append(f"{prefix}.args: must be a list of strings")
            if isinstance(command, str) and isinstance(args, list):
                signature = (command, tuple(args))
                if signature in seen_commands:
                    errors.append(
                        f"{prefix}: duplicate launch signature with {seen_commands[signature]}"
                    )
                else:
                    seen_commands[signature] = sid
        elif transport in {"sse", "streamable-http"}:
            url = entry.get("url", "")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                errors.append(f"{prefix}.url: required http(s) URL for {transport}")
            elif url in seen_urls:
                errors.append(f"{prefix}: duplicate URL with {seen_urls[url]}")
            else:
                seen_urls[url] = sid
                split = urlsplit(url)
                try:
                    port = split.port or (443 if split.scheme == "https" else 80)
                except ValueError:
                    errors.append(f"{prefix}.url: invalid port")
                else:
                    key = (split.hostname or "", port)
                    if key in seen_ports:
                        errors.append(
                            f"{prefix}: port conflict on {key[0]}:{key[1]} with {seen_ports[key]}"
                        )
                    else:
                        seen_ports[key] = sid

        env = entry.get("env")
        if not isinstance(env, dict):
            errors.append(f"{prefix}.env: must be an object")
        else:
            for key, value in env.items():
                if not isinstance(key, str) or not key.strip():
                    errors.append(f"{prefix}.env: empty key")
                if value != "":
                    errors.append(
                        f"{prefix}.env.{key}: value must be empty (no secrets in registry)"
                    )
            params = entry.get("params_ar") or {}
            if isinstance(params, dict):
                for key in env:
                    if key not in params:
                        errors.append(f"{prefix}.params_ar: missing explanation for env {key}")
        env_keys = set(env) if isinstance(env, dict) else set()
        _validate_capabilities(prefix, entry, env_keys, profile_ids, errors)

        tools = entry.get("tools")
        if not isinstance(tools, list) or not tools:
            errors.append(f"{prefix}.tools: must be a non-empty list")
            continue
        tool_names: set[str] = set()
        for index, tool in enumerate(tools):
            tprefix = f"{prefix}.tools[{index}]"
            if not isinstance(tool, dict):
                errors.append(f"{tprefix}: expected an object")
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{tprefix}.name: required")
            elif name in tool_names:
                errors.append(f"{prefix}: duplicate tool name {name!r}")
            else:
                tool_names.add(name)
            _arabic_errors(f"{tprefix}.name_ar", tool.get("name_ar"), errors)
            _arabic_errors(f"{tprefix}.description_ar", tool.get("description_ar"), errors)
            params = tool.get("params_ar")
            if params is not None and (not isinstance(params, dict) or not params):
                errors.append(f"{tprefix}.params_ar: must be a non-empty object when present")
            _validate_capabilities(tprefix, tool, env_keys, profile_ids, errors)

    missing_core = sorted(CORE_IDS - set(servers))
    if missing_core:
        errors.append(f"root: missing core servers: {', '.join(missing_core)}")
    return sorted(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()
    try:
        data = load(args.path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1
    errors = validate(data)
    report = {
        "valid": not errors,
        "servers": len(data.get("mcpServers", {})),
        "tools": sum(
            len(v.get("tools", []))
            for v in data.get("mcpServers", {}).values()
            if isinstance(v, dict)
        ),
        "profiles": len(load_profile_ids()),
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
