"""Optional telemetry adapters for the stateless MCP endpoint.

The runtime never requires an OpenTelemetry installation. SQLite structured
events are the default sink; an OTel adapter is selected only when explicitly
configured and silently falls back to SQLite if the SDK is unavailable. Trace
context is passed through from ``params._meta`` with its original keys, and no
conversation id is ever synthesized by hashing user text.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .redact import redact

__all__ = [
    "TRACE_KEYS",
    "ENV_TELEMETRY",
    "ENV_LOG_TOOL_IO",
    "TelemetryAdapter",
    "TraceContext",
    "SQLiteTelemetry",
    "OTelTelemetry",
    "NullTelemetry",
    "TelemetryUnavailable",
    "build_telemetry",
    "log_tool_io_enabled",
    "span_name",
    "tool_attributes",
    "safe_attribute_value",
]

TRACE_KEYS: tuple[str, ...] = ("traceparent", "tracestate", "baggage")
CONVERSATION_KEY = "conversation_id"
ENV_TELEMETRY = "MCP_HUB_TELEMETRY"
ENV_LOG_TOOL_IO = "MCP_HUB_TELEMETRY_LOG_TOOL_IO"
DEFAULT_DB = "mcp_hub.sqlite3"
MAX_ATTRIBUTE_CHARS = 512


class TelemetryUnavailable(Exception):
    """Raised when an optional adapter cannot be constructed."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def log_tool_io_enabled() -> bool:
    return _env_flag(ENV_LOG_TOOL_IO)


def safe_attribute_value(value: Any, limit: int = MAX_ATTRIBUTE_CHARS) -> str | int | float | bool:
    """Return a redacted, low-risk value suitable for logs or OTel attributes."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 - a broken object must not break telemetry
            value = "<unprintable>"
    text = redact(value)
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


@dataclass(frozen=True, slots=True)
class TraceContext:
    """SEP-414 trace context carried verbatim inside ``params._meta``."""

    traceparent: str = ""
    tracestate: str = ""
    baggage: str = ""
    conversation_id: str = ""

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any]) -> TraceContext | None:
        values: dict[str, str] = {}
        for key in TRACE_KEYS:
            value = meta.get(key)
            if isinstance(value, str) and value:
                values[key] = value
        conversation = meta.get(CONVERSATION_KEY)
        if isinstance(conversation, str) and conversation:
            # Explicit caller-provided ids only; never hash text to invent one.
            values[CONVERSATION_KEY] = str(safe_attribute_value(conversation, 128))
        if not values:
            return None
        return cls(
            traceparent=values.get("traceparent", ""),
            tracestate=values.get("tracestate", ""),
            baggage=values.get("baggage", ""),
            conversation_id=values.get(CONVERSATION_KEY, ""),
        )

    def to_dict(self, *, include_conversation: bool = False) -> dict[str, str]:
        payload = {key: getattr(self, key) for key in TRACE_KEYS if getattr(self, key)}
        if include_conversation and self.conversation_id:
            payload[CONVERSATION_KEY] = self.conversation_id
        return payload

    @property
    def trace_id(self) -> str:
        parts = self.traceparent.split("-")
        return parts[1] if len(parts) >= 3 and parts[1] else ""

    @property
    def span_id(self) -> str:
        parts = self.traceparent.split("-")
        return parts[2] if len(parts) >= 3 and parts[2] else ""


class TelemetryAdapter(Protocol):
    name: str
    log_tool_io: bool

    def start(self, method: str, context: TraceContext | None) -> str: ...

    def emit(self, event: str, attributes: Mapping[str, Any]) -> None: ...

    def finish(self, span_id: str, status: str, error_code: int | None = None) -> None: ...


def span_name(method: str) -> str:
    """Low-cardinality span names from the bounded method allowlist."""
    return "mcp." + method.replace("/", ".")


def tool_attributes(
    name: str,
    context: TraceContext | None = None,
    *,
    log_io: bool = False,
    arguments: Mapping[str, Any] | None = None,
    result: Any = None,
) -> dict[str, Any]:
    """GenAI semantic-convention attributes for one read-only tool call."""
    attrs: dict[str, Any] = {
        "gen_ai.system": "mcp_hub",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "mcp.method": "tools/call",
    }
    if context is not None:
        attrs.update(context.to_dict())
        if context.trace_id:
            attrs["trace.id"] = context.trace_id
        if context.conversation_id:
            attrs["conversation.id"] = context.conversation_id
    if log_io:
        if arguments is not None:
            attrs["gen_ai.tool.call.arguments"] = safe_attribute_value(arguments)
        if result is not None:
            attrs["gen_ai.tool.call.result"] = safe_attribute_value(result)
    return attrs


def _clean_attributes(attributes: Mapping[str, Any]) -> dict[str, str | int | float | bool]:
    return {str(key): safe_attribute_value(value) for key, value in attributes.items()}


class NullTelemetry:
    """No-op sink used when telemetry is explicitly disabled or unavailable."""

    def __init__(self, name: str = "off") -> None:
        self.name = name
        self.log_tool_io = False

    def start(self, method: str, context: TraceContext | None) -> str:
        _ = method, context
        return ""

    def emit(self, event: str, attributes: Mapping[str, Any]) -> None:
        _ = event, attributes

    def finish(self, span_id: str, status: str, error_code: int | None = None) -> None:
        _ = span_id, status, error_code


class SQLiteTelemetry:
    """Dependency-free structured event sink."""

    def __init__(self, db_path: str = DEFAULT_DB, log_tool_io: bool = False) -> None:
        self.name = "sqlite"
        self.db_path = str(db_path)
        self.log_tool_io = bool(log_tool_io)
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    event TEXT NOT NULL,
                    method TEXT NOT NULL DEFAULT '',
                    trace_id TEXT NOT NULL DEFAULT '',
                    span_id TEXT NOT NULL DEFAULT '',
                    attributes_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def start(self, method: str, context: TraceContext | None) -> str:
        span_id = uuid.uuid4().hex[:16]
        attrs: dict[str, Any] = {"mcp.method": method, "span.id": span_id}
        if context is not None:
            attrs.update(context.to_dict())
            if context.trace_id:
                attrs["trace.id"] = context.trace_id
        self.emit("mcp.span.start", attrs)
        return span_id

    def emit(self, event: str, attributes: Mapping[str, Any]) -> None:
        attrs = _clean_attributes(attributes)
        method = str(attrs.get("mcp.method", ""))
        trace_id = str(attrs.get("trace.id", ""))
        span_id = str(attrs.get("span.id", ""))
        created = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                "INSERT INTO telemetry_events "
                "(created_at, adapter, event, method, trace_id, span_id, attributes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    created,
                    self.name,
                    event,
                    method,
                    trace_id,
                    span_id,
                    json.dumps(attrs, sort_keys=True),
                ),
            )

    def finish(self, span_id: str, status: str, error_code: int | None = None) -> None:
        attrs: dict[str, Any] = {"span.id": span_id, "mcp.status": status}
        if error_code is not None:
            attrs["error.code"] = error_code
        self.emit("mcp.span.finish", attrs)


def _load_tracer() -> Any | None:
    try:
        module = importlib.import_module("opentelemetry.trace")
        return module.get_tracer("mcp-hub")
    except Exception:  # noqa: BLE001 - OTel is optional and must never break startup
        return None


class OTelTelemetry:
    """Optional adapter backed by the OpenTelemetry trace API."""

    def __init__(self, tracer: Any, log_tool_io: bool = False) -> None:
        self.name = "otel"
        self.log_tool_io = bool(log_tool_io)
        self._tracer = tracer
        self._spans: dict[str, Any] = {}

    @classmethod
    def from_env(cls, log_tool_io: bool = False) -> OTelTelemetry:
        tracer = _load_tracer()
        if tracer is None:
            raise TelemetryUnavailable("opentelemetry.trace is not installed")
        return cls(tracer, log_tool_io)

    def start(self, method: str, context: TraceContext | None) -> str:
        try:
            span = self._tracer.start_span(span_name(method))
        except Exception:  # noqa: BLE001 - never fail a request for telemetry
            return ""
        span_id = uuid.uuid4().hex[:16]
        self._spans[span_id] = span
        try:
            span.set_attribute("mcp.method", method)
            span.set_attribute("gen_ai.system", "mcp_hub")
            if context is not None:
                for key, value in context.to_dict().items():
                    span.set_attribute(key, value)
                if context.trace_id:
                    span.set_attribute("trace.id", context.trace_id)
        except Exception:  # noqa: BLE001
            pass
        return span_id

    def emit(self, event: str, attributes: Mapping[str, Any]) -> None:
        span_id = str(attributes.get("span.id", ""))
        span = self._spans.get(span_id)
        if span is None:
            return
        try:
            for key, value in _clean_attributes(attributes).items():
                span.set_attribute(key, value)
            span.add_event(event, _clean_attributes(attributes))
        except Exception:  # noqa: BLE001
            pass

    def finish(self, span_id: str, status: str, error_code: int | None = None) -> None:
        span = self._spans.pop(span_id, None)
        if span is None:
            return
        try:
            span.set_attribute("mcp.status", status)
            if error_code is not None:
                span.set_attribute("error.code", error_code)
            span.end()
        except Exception:  # noqa: BLE001
            pass


def build_telemetry(
    db_path: str | None = None, log_tool_io: bool | None = None
) -> TelemetryAdapter:
    """Build the configured adapter, falling back to SQLite or no-op safely."""
    mode = os.environ.get(ENV_TELEMETRY, "sqlite").strip().lower() or "sqlite"
    emit_io = _env_flag(ENV_LOG_TOOL_IO) if log_tool_io is None else bool(log_tool_io)
    path = db_path or DEFAULT_DB
    if mode == "off":
        return NullTelemetry()
    if mode in {"otel", "auto"}:
        try:
            return OTelTelemetry.from_env(emit_io)
        except TelemetryUnavailable:
            try:
                fallback = SQLiteTelemetry(path, emit_io)
            except sqlite3.Error:
                return NullTelemetry("otel-unavailable")
            fallback.emit(
                "telemetry.fallback",
                {"reason": "otel_unavailable", "adapter": "sqlite"},
            )
            return fallback
    try:
        return SQLiteTelemetry(path, emit_io)
    except sqlite3.Error:
        return NullTelemetry("sqlite-unavailable")
