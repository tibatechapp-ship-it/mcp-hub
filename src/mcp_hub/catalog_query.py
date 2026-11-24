"""Query the bundled Arabic MCP capability catalog.

Pure standard library, read-only, and safe to import from both the CLI and the
optional MCP server. The registry/profiles are additive metadata: native MCP
fields are never rewritten here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent.parent


def _resource(relative: str) -> Path:
    bundled = _PACKAGE_DIR / relative
    if bundled.exists():
        return bundled
    return _REPO_ROOT / relative


DEFAULT_REGISTRY = _resource("registry/mcp_servers.json")
DEFAULT_PROFILES_DIR = _resource("profiles")


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    """Load the unified registry from an explicit path or the bundled copy."""
    target = Path(path).expanduser() if path else DEFAULT_REGISTRY
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers"), dict):
        raise ValueError("registry must contain an mcpServers object")
    return data


def load_profiles(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load task profiles keyed by profile_id (empty dict if none are bundled)."""
    directory = Path(path).expanduser() if path else DEFAULT_PROFILES_DIR
    profiles: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return profiles
    for file in sorted(directory.glob("*.json")):
        payload = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        pid = payload.get("profile_id")
        if isinstance(pid, str) and pid.strip():
            profiles[pid] = payload
    return profiles


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _entry_row(
    sid: str,
    server: dict[str, Any],
    entry: dict[str, Any],
    *,
    is_tool: bool,
    environ: Mapping[str, str],
    profiles: dict[str, dict[str, Any]],
    server_required: list[str],
    env_keys: set[str],
    server_ready: bool,
) -> dict[str, Any]:
    task_profiles = _string_list(entry.get("task_profiles"))
    mode = entry.get("access_mode")
    if not isinstance(mode, str) or mode not in {"read-only", "read-write"}:
        mode = "read-only"
    if is_tool and "requires_env" in entry:
        required = _string_list(entry.get("requires_env"))
    else:
        required = server_required
    unknown_env = sorted(set(required) - env_keys)
    missing = [key for key in required if not str(environ.get(key, "")).strip()]
    return {
        "kind": "tool" if is_tool else "server",
        "id": sid,
        "server_id": sid,
        "tool_name": entry.get("name") if is_tool else None,
        "server_name_ar": server.get("name_ar", ""),
        "name_ar": entry.get("name_ar") or server.get("name_ar", ""),
        "description_ar": entry.get("description_ar") or server.get("description_ar", ""),
        "category": server.get("category", ""),
        "category_ar": server.get("category_ar", ""),
        "tags": _string_list(entry.get("tags")),
        "task_profiles": task_profiles,
        "profile_titles_ar": [profiles.get(pid, {}).get("title_ar", pid) for pid in task_profiles],
        "access_mode": mode,
        "requires_env": required,
        "missing_env": missing,
        "invalid_env": unknown_env,
        "ready": server_ready and not unknown_env and not missing,
        "status": server.get("status", ""),
        "transport": server.get("transport", ""),
    }


def iter_entries(
    data: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    profiles_dir: str | Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield normalized server/tool rows, one row per catalog entry."""
    environ: Mapping[str, str] = os.environ if env is None else env
    profiles = load_profiles(profiles_dir)
    servers = data.get("mcpServers", {})

    for sid, server in servers.items():
        if not isinstance(server, dict):
            continue
        server_env = server.get("env")
        env_keys = set(server_env) if isinstance(server_env, dict) else set()
        server_required = _string_list(server.get("requires_env"))
        server_ready = server.get("status") not in {"fixture", "disabled"} and server.get(
            "transport"
        ) not in {"none", ""}
        yield _entry_row(
            sid,
            server,
            server,
            is_tool=False,
            environ=environ,
            profiles=profiles,
            server_required=server_required,
            env_keys=env_keys,
            server_ready=server_ready,
        )
        tools = server.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            row = _entry_row(
                sid,
                server,
                tool,
                is_tool=True,
                environ=environ,
                profiles=profiles,
                server_required=server_required,
                env_keys=env_keys,
                server_ready=server_ready,
            )
            tool_name = tool.get("name")
            row["id"] = f"{sid}.{tool_name}" if tool_name else sid
            yield row


def query_catalog(
    *,
    kind: str = "all",
    category: str | None = None,
    tag: str | None = None,
    profile: str | None = None,
    access_mode: str | None = None,
    ready_only: bool = False,
    limit: int = 50,
    env: Mapping[str, str] | None = None,
    registry_path: str | Path | None = None,
    profiles_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Filter servers/tools by capability metadata in the bundled registry."""
    if kind not in {"all", "server", "tool"}:
        raise ValueError("kind must be all, server or tool")
    if access_mode is not None and access_mode not in {"read-only", "read-write"}:
        raise ValueError("access_mode must be read-only or read-write")
    limit = max(1, min(int(limit), 200))
    data = load_registry(registry_path)
    category_value = category.strip().lower() if category else None
    tag_value = tag.strip().lower() if tag else None
    profile_value = profile.strip() if profile else None

    results: list[dict[str, Any]] = []
    for row in iter_entries(data, env=env, profiles_dir=profiles_dir):
        if kind != "all" and row["kind"] != kind:
            continue
        if category_value and str(row["category"]).lower() != category_value:
            continue
        if tag_value and tag_value not in row["tags"]:
            continue
        if profile_value and profile_value not in row["task_profiles"]:
            continue
        if access_mode and row["access_mode"] != access_mode:
            continue
        if ready_only and not row["ready"]:
            continue
        results.append(row)
        if len(results) >= limit:
            break
    return {"count": len(results), "results": results}
