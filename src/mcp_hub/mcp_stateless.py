"""Stateless MCP core for the 2026-07-28 revision.

This module contains no transport, database or SDK imports. A caller passes a
JSON-RPC body, HTTP headers and a tool registry; the core validates that the
mirrored headers agree with the body, dispatches the method and returns an HTTP
status plus a JSON-RPC response.

The 2026 revision has no ``initialize`` handshake and no session id. Every
request carries its protocol version and client metadata in ``params._meta``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .telemetry import TelemetryAdapter, TraceContext, span_name, tool_attributes

__all__ = [
    "JSONRPC_VERSION",
    "MCP_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "META_PROTOCOL_VERSION",
    "META_CLIENT_INFO",
    "META_CLIENT_CAPABILITIES",
    "META_SERVER_INFO",
    "HEADER_MISMATCH",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "TOOL_MANIFEST_DRIFT",
    "POLICY_DENIED",
    "TASK_DENIED",
    "MCPError",
    "HeaderMismatchError",
    "ToolSpec",
    "ResourceSpec",
    "PromptSpec",
    "decode_header_value",
    "encode_header_value",
    "jsonrpc_error",
    "server_meta",
    "tool_manifest_digest",
    "verify_tool_manifest",
    "ToolPolicy",
    "AppPolicy",
    "APP_RESOURCE_PREFIX",
    "handle",
]

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (MCP_PROTOCOL_VERSION,)

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

HEADER_PROTOCOL_VERSION = "mcp-protocol-version"
HEADER_METHOD = "mcp-method"
HEADER_NAME = "mcp-name"
HEADER_PARAM_PREFIX = "mcp-param-"
SKILL_RESOURCE_PREFIX = "mcp-hub://skills/"
APP_RESOURCE_PREFIX = "ui://"

# Methods that must mirror their identifying parameter in ``Mcp-Name``.
NAME_HEADERS_PARAMS: Mapping[str, str] = MappingProxyType(
    {
        "tools/call": "name",
        "prompts/get": "name",
        "resources/read": "uri",
        "skills/get": "name",
        "skills/approve": "name",
        "skills/revoke": "name",
        "tasks/get": "task_id",
        "tasks/update": "task_id",
        "tasks/cancel": "task_id",
        "tasks/approve": "task_id",
        "persona/simulate": "scenario",
    }
)
NAME_HEADERS_METHODS = frozenset(NAME_HEADERS_PARAMS)

# Policy documents use dotted action names; MCP method names use slashes.
APPROVAL_ACTIONS: Mapping[str, str] = MappingProxyType(
    {
        "skills/approve": "skills.approve",
        "skills/revoke": "skills.revoke",
        "tasks/approve": "tasks.approve",
        "tasks/list": "tasks.list",
    }
)

SENTINEL_PREFIX = "=?base64?"
SENTINEL_SUFFIX = "?="

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022
# Application-defined: outside JSON-RPC's reserved -32768..-32000 block.
TOOL_MANIFEST_DRIFT = -10001
POLICY_DENIED = -10002
TASK_DENIED = -10003

# Mirroring these in headers would leak or smuggle credentials past body-based
# policy/redaction. They are rejected before any value comparison.
SENSITIVE_PARAM_TOKENS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
)

Handler = Callable[[Mapping[str, Any]], Any]
ToolPolicy = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
AppPolicy = Callable[[str, Mapping[str, Any]], None]


class MCPError(Exception):
    """An error that maps onto a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class HeaderMismatchError(MCPError):
    """Header/body disagreement; the HTTP response must be 400 Bad Request."""

    def __init__(self, message: str, data: Any = None) -> None:
        super().__init__(HEADER_MISMATCH, message, data)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The handler-free contract sent by ``tools/list``."""

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }
        if self.title:
            payload["title"] = self.title
        return payload


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = "text/plain"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name or self.uri,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass(frozen=True, slots=True)
class PromptSpec:
    name: str
    description: str = ""
    arguments: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.arguments:
            payload["arguments"] = [dict(arg) for arg in self.arguments]
        return payload


def _header_safe(value: str) -> bool:
    """RFC 9110 field-value characters, with no leading/trailing whitespace."""
    if value != value.strip(" \t"):
        return False
    return all(char == "\t" or 0x20 <= ord(char) <= 0x7E for char in value)


def decode_header_value(value: str) -> str:
    """Decode a mirrored header value, including the Base64 sentinel form."""
    if not isinstance(value, str):
        raise ValueError("header value must be a string")
    starts = value.startswith(SENTINEL_PREFIX)
    ends = value.endswith(SENTINEL_SUFFIX)
    if starts or ends:
        if not (starts and ends):
            raise ValueError("malformed Base64 sentinel")
        encoded = value[len(SENTINEL_PREFIX) : -len(SENTINEL_SUFFIX)]
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid Base64 sentinel") from exc
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid UTF-8 in Base64 sentinel") from exc
    if not _header_safe(value):
        raise ValueError("unsafe header value")
    return value


def encode_header_value(value: str) -> str:
    """Encode a parameter value for safe transport in an HTTP header."""
    if not isinstance(value, str):
        raise ValueError("header value must be a string")
    if _header_safe(value) and not (
        value.startswith(SENTINEL_PREFIX) and value.endswith(SENTINEL_SUFFIX)
    ):
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"{SENTINEL_PREFIX}{encoded}{SENTINEL_SUFFIX}"


def _scalar_header(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError("only scalar parameter values can be mirrored")


def _header_values_match(decoded: str, body_value: Any) -> bool:
    if isinstance(body_value, bool):
        return decoded == ("true" if body_value else "false")
    if isinstance(body_value, (int, float)):
        try:
            return float(decoded) == float(body_value)
        except ValueError:
            return False
    return isinstance(body_value, str) and decoded == body_value


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in (headers or {}).items():
        key = name.lower()
        if key in result and result[key] != value:
            raise HeaderMismatchError(f"Conflicting {name} headers")
        result[key] = value
    return result


def _meta(params: Mapping[str, Any]) -> Mapping[str, Any]:
    value = params.get("_meta")
    return value if isinstance(value, Mapping) else {}


def jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": error}


def jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def server_meta(name: str, version: str) -> dict[str, Any]:
    return {META_SERVER_INFO: {"name": name, "version": version}}


def tool_manifest_digest(specs: Sequence[ToolSpec]) -> str:
    """Canonical SHA-256 over the public tool contracts."""
    payload = [spec.to_dict() for spec in specs]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_tool_manifest(
    specs: Sequence[ToolSpec], handlers: Mapping[str, Handler], expected: str
) -> str:
    """Fail closed when the live tool list drifts from the approved manifest."""
    actual = tool_manifest_digest(specs)
    names = [spec.name for spec in specs]
    if actual != expected or len(names) != len(set(names)) or set(names) != set(handlers):
        raise MCPError(
            TOOL_MANIFEST_DRIFT,
            "Tool manifest changed; approval required",
            {"expected": expected, "actual": actual},
        )
    return actual


def _text_content(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error_content(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _normalize_tool_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping) and "content" in raw and "isError" in raw:
        return dict(raw)
    if isinstance(raw, str):
        return _text_content(raw)
    return _text_content(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str))


def validate_request(
    request: Mapping[str, Any], headers: Mapping[str, str] | None
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Validate mirrored headers against the JSON-RPC body.

    Returns ``(method, params, headers)``. Raises :class:`MCPError` on failure;
    header failures carry the -32020 code and require HTTP 400.
    """
    if not isinstance(request, Mapping):
        raise MCPError(INVALID_REQUEST, "request must be a JSON object")
    if request.get("jsonrpc") != JSONRPC_VERSION:
        raise MCPError(INVALID_REQUEST, "expected jsonrpc 2.0")
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise MCPError(INVALID_REQUEST, "missing method")
    raw_params = request.get("params", {})
    if raw_params is None:
        raw_params = {}
    if not isinstance(raw_params, Mapping):
        raise MCPError(INVALID_REQUEST, "params must be an object")
    params = dict(raw_params)
    hdrs = _headers(headers)

    header_version = hdrs.get(HEADER_PROTOCOL_VERSION)
    body_version = _meta(params).get(META_PROTOCOL_VERSION)
    if not isinstance(header_version, str) or not header_version:
        raise HeaderMismatchError("Missing MCP-Protocol-Version header")
    if body_version != header_version:
        raise HeaderMismatchError(
            "Header mismatch: MCP-Protocol-Version does not match request body"
        )
    if body_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise MCPError(
            UNSUPPORTED_PROTOCOL_VERSION,
            "Unsupported protocol version",
            {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": body_version},
        )

    header_method = hdrs.get(HEADER_METHOD)
    if not isinstance(header_method, str) or not header_method:
        raise HeaderMismatchError("Missing Mcp-Method header")
    if header_method != method:
        raise HeaderMismatchError(
            f"Header mismatch: Mcp-Method {header_method!r} does not match body {method!r}"
        )

    raw_name = hdrs.get(HEADER_NAME)
    if method in NAME_HEADERS_PARAMS:
        source = NAME_HEADERS_PARAMS[method]
        body_name = params.get(source)
        if not isinstance(body_name, str) or not body_name:
            raise MCPError(INVALID_PARAMS, f"{source} is required for {method}")
        if not isinstance(raw_name, str) or not raw_name:
            raise HeaderMismatchError("Missing Mcp-Name header")
        try:
            decoded = decode_header_value(raw_name)
        except ValueError as exc:
            raise HeaderMismatchError(f"Malformed Mcp-Name header: {exc}") from exc
        if decoded != body_name:
            raise HeaderMismatchError("Header mismatch: Mcp-Name does not match request body")
    elif raw_name is not None:
        raise HeaderMismatchError(f"Unexpected Mcp-Name header for {method}")

    arguments = params.get("arguments")
    args = arguments if isinstance(arguments, Mapping) else {}
    for name, raw in hdrs.items():
        if not name.startswith(HEADER_PARAM_PREFIX):
            continue
        key = name[len(HEADER_PARAM_PREFIX) :]
        if not key:
            raise HeaderMismatchError("Empty Mcp-Param header name")
        normalized_key = re.sub(r"[^a-z0-9]", "_", key.lower())
        if any(token in normalized_key for token in SENSITIVE_PARAM_TOKENS):
            raise HeaderMismatchError(f"Sensitive parameter cannot be mirrored in headers: {name}")
        if key not in args or args[key] is None:
            raise HeaderMismatchError(f"Header mismatch: no body value for {name}")
        try:
            decoded = decode_header_value(raw)
            expected = _scalar_header(args[key])
        except ValueError as exc:
            raise HeaderMismatchError(f"Malformed {name} header: {exc}") from exc
        if not _header_values_match(decoded, args[key]):
            raise HeaderMismatchError(f"Header mismatch: {name} does not match request body")
        _ = expected  # scalar conversion already proved the value is mirrorable
    return method, params, hdrs


def handle(
    request: Mapping[str, Any],
    headers: Mapping[str, str] | None,
    *,
    tools: Mapping[str, Handler],
    tool_specs: Sequence[ToolSpec],
    expected_manifest_hash: str,
    resource_specs: Sequence[ResourceSpec] = (),
    resources: Mapping[str, Handler] | None = None,
    prompt_specs: Sequence[PromptSpec] = (),
    prompts: Mapping[str, Handler] | None = None,
    server_info: Mapping[str, Any] | None = None,
    instructions: str = "",
    tool_policy: ToolPolicy | None = None,
    skill_handlers: Mapping[str, Handler] | None = None,
    skill_policy: ToolPolicy | None = None,
    skill_approval_handlers: Mapping[str, Handler] | None = None,
    approval_policy: ToolPolicy | None = None,
    task_handlers: Mapping[str, Handler] | None = None,
    persona_handler: Handler | None = None,
    telemetry: TelemetryAdapter | None = None,
    app_policy: AppPolicy | None = None,
    log_tool_io: bool | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Handle one stateless JSON-RPC request.

    Returns ``(http_status, body)``; ``body`` is ``None`` for notifications.
    """
    info = dict(server_info or {"name": "mcp-hub", "version": "unknown"})
    req_id = request.get("id") if isinstance(request, Mapping) else None
    notification = isinstance(request, Mapping) and request.get("id") is None

    try:
        method, params, _ = validate_request(request, headers)
    except MCPError as exc:
        status = 400 if exc.code == HEADER_MISMATCH else 200
        return status, jsonrpc_error(req_id, exc.code, exc.message, exc.data)

    context = TraceContext.from_meta(_meta(params))
    emit_io = (
        log_tool_io if log_tool_io is not None else bool(getattr(telemetry, "log_tool_io", False))
    )
    span_id = ""
    if telemetry is not None:
        try:
            span_id = telemetry.start(span_name(method), context)
        except Exception:  # noqa: BLE001 - telemetry must never fail the request
            span_id = ""

    def emit(event: str, attributes: Mapping[str, Any]) -> None:
        if telemetry is None:
            return
        payload: dict[str, Any] = dict(attributes)
        payload.setdefault("mcp.method", method)
        payload.setdefault("gen_ai.system", "mcp_hub")
        payload.setdefault("mcp.protocol.version", MCP_PROTOCOL_VERSION)
        if span_id:
            payload.setdefault("span.id", span_id)
        if context is not None:
            for key, value in context.to_dict().items():
                payload.setdefault(key, value)
            if context.trace_id:
                payload.setdefault("trace.id", context.trace_id)
        try:
            telemetry.emit(event, payload)
        except Exception:  # noqa: BLE001 - telemetry must never fail the request
            pass

    def with_trace(body: dict[str, Any]) -> dict[str, Any]:
        if context is None:
            return body
        result = dict(body)
        result["_meta"] = context.to_dict()
        return result

    def with_result_meta(result: dict[str, Any]) -> dict[str, Any]:
        output = dict(result)
        output_meta = dict(output.get("_meta") or {})
        if context is not None:
            output_meta.update(context.to_dict())
        output["_meta"] = output_meta
        return output

    def finish_span(status: str, error_code: int | None = None) -> None:
        if telemetry is None or not span_id:
            return
        try:
            telemetry.finish(span_id, status, error_code)
        except Exception:  # noqa: BLE001 - telemetry must never fail the request
            pass

    meta = server_meta(str(info.get("name", "mcp-hub")), str(info.get("version", "unknown")))

    def discover() -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False},
                "prompts": {},
                "skills": {"listChanged": False, "executionGranted": False},
                "approvals": {"skills": True, "tasks": True},
                "tasks": {
                    "get": True,
                    "list": True,
                    "update": True,
                    "cancel": True,
                    "approve": True,
                },
                "persona": {"simulate": True},
            },
            "serverInfo": info,
            "toolManifestSha256": tool_manifest_digest(tool_specs),
            "_meta": meta,
        }
        if instructions:
            payload["instructions"] = instructions
        return payload

    def list_tools() -> dict[str, Any]:
        verify_tool_manifest(tool_specs, tools, expected_manifest_hash)
        return {
            "tools": [spec.to_dict() for spec in tool_specs],
            "ttlMs": 0,
            "cacheScope": "public",
            "_meta": meta,
        }

    def call_tool() -> dict[str, Any]:
        verify_tool_manifest(tool_specs, tools, expected_manifest_hash)
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise MCPError(INVALID_PARAMS, "arguments must be an object")
        if not isinstance(name, str):
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")
        handler = tools.get(name)
        if handler is None:
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")
        checked = dict(arguments)
        if tool_policy is not None:
            checked = dict(tool_policy(name, checked))
        try:
            raw = handler(checked)
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 - a tool must not crash the dispatcher
            result = _error_content(f"{type(exc).__name__}: {exc}")
            error_attrs = tool_attributes(
                name, context, log_io=emit_io, arguments=checked, result=result
            )
            error_attrs["error.type"] = type(exc).__name__
            emit("mcp.tool.error", error_attrs)
        else:
            result = _normalize_tool_result(raw)
            emit(
                "mcp.tool.call",
                tool_attributes(name, context, log_io=emit_io, arguments=checked, result=result),
            )
        result["_meta"] = meta
        return result

    def list_resources() -> dict[str, Any]:
        return {
            "resources": [spec.to_dict() for spec in resource_specs],
            "ttlMs": 0,
            "cacheScope": "public",
            "_meta": meta,
        }

    def read_resource() -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise MCPError(INVALID_PARAMS, f"Unknown resource: {uri}")
        if uri.startswith(SKILL_RESOURCE_PREFIX):
            skill_name = uri[len(SKILL_RESOURCE_PREFIX) :].split("/", 1)[0]
            if skill_policy is not None and skill_name:
                try:
                    skill_policy(skill_name, {"uri": uri})
                except MCPError:
                    raise
                except Exception as exc:  # noqa: BLE001 - policy must fail closed
                    raise MCPError(POLICY_DENIED, f"Skill read denied: {exc}") from exc
        if uri.startswith(APP_RESOURCE_PREFIX) and app_policy is not None:
            try:
                app_policy(uri, params)
            except MCPError:
                raise
            except Exception as exc:  # noqa: BLE001 - policy must fail closed
                raise MCPError(POLICY_DENIED, f"UI read denied: {exc}") from exc
        handler = (resources or {}).get(uri)
        if handler is None:
            raise MCPError(INVALID_PARAMS, f"Unknown resource: {uri}")
        spec = next((item for item in resource_specs if item.uri == uri), None)
        try:
            raw = handler(dict(params))
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}") from exc
        if isinstance(raw, Mapping) and isinstance(raw.get("contents"), list):
            contents = list(raw["contents"])
        else:
            contents = [
                {
                    "uri": uri,
                    "mimeType": spec.mime_type if spec else "text/plain",
                    "text": raw if isinstance(raw, str) else json.dumps(raw, default=str),
                }
            ]
        payload: dict[str, Any] = {
            "contents": contents,
            "ttlMs": 0,
            "cacheScope": "public",
            "_meta": meta,
        }
        if isinstance(raw, Mapping) and "ui" in raw:
            payload["ui"] = raw["ui"]
        return payload

    def list_prompts() -> dict[str, Any]:
        return {
            "prompts": [spec.to_dict() for spec in prompt_specs],
            "ttlMs": 0,
            "cacheScope": "public",
            "_meta": meta,
        }

    def get_prompt() -> dict[str, Any]:
        name = params.get("name")
        handler = (prompts or {}).get(name) if isinstance(name, str) else None
        if handler is None:
            raise MCPError(INVALID_PARAMS, f"Unknown prompt: {name}")
        try:
            raw = handler(dict(params.get("arguments") or {}))
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}") from exc
        return {
            "description": next(
                (spec.description for spec in prompt_specs if spec.name == name), ""
            ),
            "messages": raw if isinstance(raw, list) else [],
            "_meta": meta,
        }

    def list_skills() -> dict[str, Any]:
        handler = (skill_handlers or {}).get("skills/list")
        if handler is None:
            raise MCPError(METHOD_NOT_FOUND, "Skill registry is not configured")
        try:
            raw = handler({})
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 - one handler must not kill the loop
            raise MCPError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}") from exc
        result = dict(raw) if isinstance(raw, Mapping) else {"skills": []}
        if skill_policy is not None:
            visible = []
            for item in result.get("skills", []):
                name = item.get("name") if isinstance(item, Mapping) else None
                if not isinstance(name, str):
                    continue
                try:
                    skill_policy(name, {"name": name})
                except MCPError:
                    continue
                except Exception:  # noqa: BLE001 - an invalid policy reveals nothing
                    continue
                visible.append(item)
            result["skills"] = visible
        result.update({"ttlMs": 0, "cacheScope": "public", "_meta": meta})
        return result

    def get_skill() -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise MCPError(INVALID_PARAMS, "name is required for skills/get")
        checked = dict(params)
        if skill_policy is not None:
            try:
                checked = dict(skill_policy(name, checked))
            except MCPError:
                raise
            except Exception as exc:  # noqa: BLE001 - policy must fail closed
                raise MCPError(POLICY_DENIED, f"Skill read denied: {exc}") from exc
        handler = (skill_handlers or {}).get("skills/get")
        if handler is None:
            raise MCPError(METHOD_NOT_FOUND, "Skill registry is not configured")
        try:
            raw = handler(checked)
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}") from exc
        result = dict(raw) if isinstance(raw, Mapping) else {"skill": raw}
        result["_meta"] = meta
        return result

    def approval_guard(method: str, checked: dict[str, Any]) -> dict[str, Any]:
        if approval_policy is None:
            raise MCPError(POLICY_DENIED, "Approval policy is not configured")
        action = APPROVAL_ACTIONS.get(method, method.replace("/", ".", 1))
        try:
            return dict(approval_policy(action, checked))
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001 - an invalid policy must fail closed
            raise MCPError(POLICY_DENIED, f"Approval policy failed: {type(exc).__name__}") from exc

    def skill_approval_operation(method: str) -> Callable[[], dict[str, Any]]:
        def run() -> dict[str, Any]:
            handler = (skill_approval_handlers or {}).get(method)
            if handler is None:
                raise MCPError(METHOD_NOT_FOUND, "Approval handlers are not configured")
            checked = approval_guard(method, dict(params))
            try:
                raw = handler(checked)
            except MCPError:
                raise
            except Exception as exc:  # noqa: BLE001 - never leak a failed approval write
                raise MCPError(
                    POLICY_DENIED, f"Approval operation failed: {type(exc).__name__}"
                ) from exc
            result = dict(raw) if isinstance(raw, Mapping) else {"approved": raw}
            result["_meta"] = meta
            return result

        return run

    def task_operation(method: str) -> Callable[[], dict[str, Any]]:
        def run() -> dict[str, Any]:
            handler = (task_handlers or {}).get(method)
            if handler is None:
                raise MCPError(METHOD_NOT_FOUND, "Task store is not configured")
            checked = (
                approval_guard(method, dict(params)) if method == "tasks/approve" else dict(params)
            )
            try:
                raw = handler(checked)
            except MCPError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolation failures map to -10003
                raise MCPError(TASK_DENIED, f"Task operation failed: {type(exc).__name__}") from exc
            result = dict(raw) if isinstance(raw, Mapping) else {"task": raw}
            result["_meta"] = meta
            return result

        return run

    def persona_simulate() -> dict[str, Any]:
        if persona_handler is None:
            raise MCPError(METHOD_NOT_FOUND, "Persona simulator is not configured")
        try:
            raw = persona_handler(dict(params))
        except MCPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPError(INVALID_PARAMS, f"Persona simulation failed: {exc}") from exc
        result = dict(raw) if isinstance(raw, Mapping) else {"simulation": raw}
        result["_meta"] = meta
        return result

    dispatch: dict[str, Callable[[], dict[str, Any]]] = {
        "server/discover": discover,
        "ping": lambda: {"_meta": meta},
        "tools/list": list_tools,
        "tools/call": call_tool,
        "resources/list": list_resources,
        "resources/read": read_resource,
        "prompts/list": list_prompts,
        "prompts/get": get_prompt,
        "skills/list": list_skills,
        "skills/get": get_skill,
        "skills/approve": skill_approval_operation("skills/approve"),
        "skills/revoke": skill_approval_operation("skills/revoke"),
        "tasks/get": task_operation("tasks/get"),
        "tasks/list": task_operation("tasks/list"),
        "tasks/update": task_operation("tasks/update"),
        "tasks/cancel": task_operation("tasks/cancel"),
        "tasks/approve": task_operation("tasks/approve"),
        "persona/simulate": persona_simulate,
    }
    handler = dispatch.get(method)
    if handler is None:
        emit("mcp.error", {"error.code": METHOD_NOT_FOUND, "error.type": "method_not_found"})
        finish_span("error", METHOD_NOT_FOUND)
        return 200, with_trace(
            jsonrpc_error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")
        )
    try:
        result = handler()
    except MCPError as exc:
        emit("mcp.error", {"error.code": exc.code, "error.type": type(exc).__name__})
        finish_span("error", exc.code)
        return 200, with_trace(jsonrpc_error(req_id, exc.code, exc.message, exc.data))
    except Exception as exc:  # noqa: BLE001 - the loop must survive any bug
        emit("mcp.error", {"error.code": INTERNAL_ERROR, "error.type": type(exc).__name__})
        finish_span("error", INTERNAL_ERROR)
        return 200, with_trace(
            jsonrpc_error(req_id, INTERNAL_ERROR, "Internal error", {"type": type(exc).__name__})
        )
    emit("mcp.response", {"mcp.status": "ok"})
    finish_span("ok")
    if notification:
        return 202, None
    return 200, jsonrpc_result(req_id, with_result_meta(result))
