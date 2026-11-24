"""Automatic Arabic localization layer for MCP tool metadata.

Installed as a request-handler wrapper around ``ListTools``. Protocol tool
names and ``inputSchema`` objects are NEVER changed; only human-readable
descriptions gain an Arabic prefix plus the original English text. Any failure
in translation, cache I/O or upstream refresh fails open: the original response
is returned untouched.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .live_sync import (
    DEFAULT_CACHE,
    DEFAULT_UPSTREAM_URL,
    is_allowed_upstream,
    load_cache,
    refresh_from_upstream,
    save_cache,
)

ENV_RUNTIME = "MCP_HUB_AR_RUNTIME"
ENV_CACHE = "MCP_HUB_AR_CACHE"
ENV_UPSTREAM = "MCP_HUB_AR_UPSTREAM_URL"
ENV_LIVE = "MCP_HUB_AR_LIVE"
ENV_TTL = "MCP_HUB_AR_SYNC_TTL"

_TRUE = {"1", "true", "yes", "on"}
_PHRASES: tuple[tuple[str, str], ...] = (
    ("Search the catalog", "ابحث في الكتالوج"),
    ("Get full details", "اعرض التفاصيل الكاملة"),
    ("List all categories", "اعرض كل الفئات"),
    ("List servers", "اعرض الخوادم"),
    ("Detect fields", "اكشف الحقول"),
    ("Show the audit trail", "اعرض سجل التدقيق"),
    ("Run read-only health checks", "شغّل فحوصات صحية للقراءة فقط"),
    ("Suggest categories", "اقترح الفئات"),
    ("server", "الخادم"),
    ("category", "الفئة"),
    ("query", "الاستعلام"),
    ("limit", "الحد"),
    ("description", "الوصف"),
)
_PREFIX = "العربية: "
_warned: set[str] = set()
_lock = threading.Lock()
_STATE: dict[str, Any] = {
    "translations": {},
    "cache_path": DEFAULT_CACHE,
    "upstream": "",
}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def _warn(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    print(f"mcp-hub arabic runtime: {message}", file=sys.stderr)


def _bundled_registry_path() -> Path:
    package_dir = Path(__file__).resolve().parent
    in_wheel = package_dir / "registry" / "mcp_servers.json"
    if in_wheel.is_file():
        return in_wheel
    return package_dir.parents[1] / "registry" / "mcp_servers.json"


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load the curated registry JSON (bundled in the wheel or repo root)."""
    target = path or _bundled_registry_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        raise ValueError("registry has no mcpServers object")
    return payload


def build_translation_map(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index Arabic tool metadata by the exact protocol tool name."""
    result: dict[str, dict[str, Any]] = {}
    servers = registry.get("mcpServers")
    if not isinstance(servers, dict):
        return result
    for entry in servers.values():
        if not isinstance(entry, dict):
            continue
        tools = entry.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not isinstance(name, str) or not name:
                continue
            params = tool.get("params_ar")
            result[name] = {
                "description_ar": str(tool.get("description_ar") or ""),
                "params_ar": params if isinstance(params, dict) else {},
            }
    return result


def translate_description(text: str) -> str:
    """Deterministic glossary translation for tools absent from the registry."""
    translated = text
    for english, arabic in _PHRASES:
        translated = re.sub(re.escape(english), arabic, translated, flags=re.IGNORECASE)
    return "ترجمة تلقائية (قاموس محلي): " + translated if translated != text else text


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _format_params(params: Any) -> str:
    if not isinstance(params, dict) or not params:
        return ""
    parts: list[str] = []
    for key, value in params.items():
        if isinstance(value, dict):
            value = value.get("description_ar") or value.get("description") or ""
        parts.append(f"{key}: {value}")
    return "، ".join(parts)


def _with_description(tool: Any, description: str) -> Any:
    if isinstance(tool, dict):
        updated = dict(tool)
        updated["description"] = description
        return updated
    model_copy = getattr(tool, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(update={"description": description})
        except TypeError:  # pragma: no cover - SDK versions with a different seam
            pass
    fallback: Any = copy.copy(tool)
    try:
        fallback.description = description
    except (AttributeError, TypeError):
        return tool
    return fallback


def localize_tool(tool: Any, translations: dict[str, dict[str, Any]]) -> Any:
    """Return a copy with Arabic metadata; never touch name or inputSchema."""
    name = _field(tool, "name")
    description = _field(tool, "description")
    if not isinstance(name, str) or not isinstance(description, str):
        return tool
    if description.startswith(_PREFIX):
        return tool

    metadata = translations.get(name, {})
    arabic = str(metadata.get("description_ar") or "") or translate_description(description)
    lines = [_PREFIX + arabic]
    params = _format_params(metadata.get("params_ar"))
    if params:
        lines.append("المعاملات: " + params)
    lines.append("الوصف الأصلي: " + description)
    return _with_description(tool, "\n".join(lines))


def localize_result(result: Any, translations: dict[str, dict[str, Any]]) -> Any:
    """Localize a ListToolsResult without mutating the SDK object."""
    tools = _field(result, "tools")
    if not isinstance(tools, list):
        return result
    localized = [localize_tool(tool, translations) for tool in tools]
    if isinstance(result, dict):
        updated = dict(result)
        updated["tools"] = localized
        return updated
    model_copy = getattr(result, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(update={"tools": localized})
        except TypeError:  # pragma: no cover - SDK versions with a different seam
            pass
    fallback: Any = copy.copy(result)
    try:
        fallback.tools = localized
    except (AttributeError, TypeError):
        return result
    return fallback


def _initialize() -> bool:
    cache_path = Path(os.environ.get(ENV_CACHE, str(DEFAULT_CACHE))).expanduser()
    upstream = os.environ.get(ENV_UPSTREAM, DEFAULT_UPSTREAM_URL).strip()
    if upstream and not is_allowed_upstream(upstream):
        _warn("non-upstream URL ignored; upstream-only policy enforced")
        upstream = ""

    registry: dict[str, Any] | None = None
    if upstream:
        try:
            synced = refresh_from_upstream(cache_path, upstream)
            registry = synced.registry
        except Exception as exc:  # noqa: BLE001 - fail open on any sync error
            _warn(f"upstream sync failed ({type(exc).__name__}); using cache")

    if registry is None:
        try:
            cached = load_cache(cache_path)
            candidate = cached.get("registry")
            if isinstance(candidate, dict):
                registry = candidate
        except Exception:  # noqa: BLE001 - corrupt cache is not fatal
            _warn("cache unreadable; rebuilding from bundled registry")

    if registry is None:
        try:
            registry = load_registry()
            save_cache(cache_path, registry, source="bundled")
        except Exception as exc:  # noqa: BLE001 - fail open with no translations
            _warn(f"bundled registry unavailable ({type(exc).__name__})")
            registry = {}

    translations = build_translation_map(registry)
    with _lock:
        _STATE["translations"] = translations
        _STATE["cache_path"] = cache_path
        _STATE["upstream"] = upstream
    return bool(translations)


def _maybe_refresh() -> None:
    if not _env_flag(ENV_LIVE):
        return
    upstream = str(_STATE.get("upstream") or "")
    if not upstream:
        return
    cache_path = _STATE.get("cache_path")
    if not isinstance(cache_path, Path):
        return
    try:
        ttl = float(os.environ.get(ENV_TTL, "0"))
    except ValueError:
        ttl = 0.0
    try:
        synced = refresh_from_upstream(cache_path, upstream, ttl=max(0.0, ttl))
        if synced.registry:
            with _lock:
                _STATE["translations"] = build_translation_map(synced.registry)
    except Exception as exc:  # noqa: BLE001 - live refresh never breaks list_tools
        _warn(f"live refresh failed ({type(exc).__name__}); keeping current cache")


def _wrap_handler(handler: Callable[..., Any]) -> Callable[..., Any]:
    async def localized_handler(message: Any) -> Any:
        result = await handler(message)
        try:
            _maybe_refresh()
            with _lock:
                translations = dict(_STATE.get("translations") or {})
            return localize_result(result, translations)
        except Exception as exc:  # noqa: BLE001 - fail open, never break MCP
            _warn(f"localization failed ({type(exc).__name__}); returning original result")
            return result

    marked: Any = localized_handler
    marked._mcp_hub_arabic = True
    return localized_handler


def install(mcp_app: Any) -> bool:
    """Wrap ListTools handlers on an MCP app; return False when unavailable."""
    if not _env_flag(ENV_RUNTIME):
        return False
    if not _initialize():
        return False

    low_level = getattr(mcp_app, "_mcp_server", None) or mcp_app
    handlers = getattr(low_level, "request_handlers", None)
    if handlers is None:
        handlers = getattr(low_level, "_request_handlers", None)
    if not isinstance(handlers, dict):
        _warn("no request_handlers found; leaving MCP server unchanged")
        return False

    wrapped = 0
    for key, handler in list(handlers.items()):
        key_name = getattr(key, "__name__", str(key))
        if "ListTools" not in key_name:
            continue
        if getattr(handler, "_mcp_hub_arabic", False):
            wrapped += 1
            continue
        handlers[key] = _wrap_handler(handler)
        wrapped += 1
    if not wrapped:
        _warn("no ListTools handler found; leaving MCP server unchanged")
    return wrapped > 0
