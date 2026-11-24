"""Selective MCP Apps surfaces for approval review and the tool catalog.

Each UI resource is a self-contained HTML document. It embeds a sandboxed
iframe, speaks JSON-RPC over ``postMessage``, carries a strict hash-based CSP,
and always ships a text/JSON fallback for clients that do not render HTML. Only
the two allowlisted ``ui://`` resources are exposed; no secrets or environment
values are rendered.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .database import Catalog
from .mcp_stateless import (
    POLICY_DENIED,
    Handler,
    MCPError,
    ResourceSpec,
)
from .redact import redact
from .skills import SkillRegistry
from .tasks import TaskStore

__all__ = [
    "APPROVAL_URI",
    "CATALOG_URI",
    "ALLOWED_URIS",
    "AppRuntime",
    "AppPolicy",
    "enabled",
    "build_runtime",
    "empty_runtime",
]

APPROVAL_URI = "ui://mcp-hub/approval-review"
CATALOG_URI = "ui://mcp-hub/tool-catalog"
ALLOWED_URIS: frozenset[str] = frozenset({APPROVAL_URI, CATALOG_URI})
RELAY_UI = "__mcpHubRelayUi"
RELAY_HOST = "__mcpHubRelayHost"

AppPolicy = Callable[[str, Mapping[str, Any]], None]

_SPECS: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        uri=APPROVAL_URI,
        name="Approval review",
        description="Review pinned skill approvals and tasks awaiting approval.",
        mime_type="text/html",
    ),
    ResourceSpec(
        uri=CATALOG_URI,
        name="Tool catalog",
        description="Browse the read-only tool contracts exposed by this server.",
        mime_type="text/html",
    ),
)


@dataclass(frozen=True, slots=True)
class AppRuntime:
    resource_specs: tuple[ResourceSpec, ...]
    resources: dict[str, Handler]
    policy: AppPolicy


def enabled() -> bool:
    return os.environ.get("MCP_HUB_APPS_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _hash(text: str) -> str:
    return "sha256-" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_json(data: Any) -> str:
    """Serialize without letting a value break out of an inline script tag."""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return redact(text).replace("<", "\\u003c")


def _csp(script_hashes: tuple[str, ...]) -> str:
    scripts = " ".join(f"'{item}'" for item in script_hashes)
    return (
        "default-src 'none'; "
        f"script-src {scripts}; "
        "connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; "
        "frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def _inner_script(request_method: str) -> str:
    return f"""\
(function () {{
  var out = document.getElementById('out');
  function render(message) {{
    if (!message) {{ out.textContent = 'No response from host.'; return; }}
    if (message.error) {{
      out.textContent = 'Error ' + message.error.code + ': ' + message.error.message;
      return;
    }}
    var result = message.result || {{}};
    var items = result.skills || result.tools || [];
    if (items.length) {{
      var names = items.map(function (item) {{ return item.name || item.uri || ''; }});
      out.textContent = names.join('\\n');
    }} else {{
      out.textContent = JSON.stringify(result, null, 2);
    }}
  }}
  window.addEventListener('message', function (event) {{
    var data = event.data;
    if (data && data.{RELAY_HOST}) {{ render(data.message); }}
  }});
  parent.postMessage({{
    {RELAY_UI}: true,
    message: {{ jsonrpc: '2.0', id: 1, method: '{request_method}', params: {{}} }}
  }}, '*');
  setTimeout(function () {{
    if (!out.textContent) {{ out.textContent = 'Host did not answer; showing embedded snapshot.'; }}
  }}, 800);
}})();
"""


def _outer_script() -> str:
    return f"""\
(function () {{
  var frame = document.getElementById('mcp-app');
  window.addEventListener('message', function (event) {{
    var data = event.data;
    if (!data) {{ return; }}
    if (event.source === frame.contentWindow && data.{RELAY_UI}) {{
      window.parent.postMessage(data.message, '*');
    }} else if (event.source === window.parent && data.jsonrpc) {{
      frame.contentWindow.postMessage({{ {RELAY_HOST}: true, message: data }}, '*');
    }}
  }});
}})();
"""


def _document(uri: str, title: str, snapshot: Mapping[str, Any], request_method: str) -> str:
    inner_script = _inner_script(request_method)
    outer_script = _outer_script()
    csp = _csp((_hash(inner_script), _hash(outer_script)))
    escaped_csp = html.escape(csp, quote=True)
    snapshot_json = _safe_json(snapshot)
    inner = f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{escaped_csp}">
<title>{html.escape(title)}</title>
</head>
<body>
<h1>{html.escape(title)}</h1>
<p>Read-only MCP App. No credentials are rendered.</p>
<pre id="snapshot">{html.escape(snapshot_json)}</pre>
<h2>Response</h2>
<pre id="out">Waiting for JSON-RPC response...</pre>
<noscript>JSON fallback is included in the MCP resource response.</noscript>
<script>{inner_script}</script>
</body>
</html>
"""
    outer = f"""\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{escaped_csp}">
<title>{html.escape(title)} (sandboxed)</title>
</head>
<body>
<iframe id="mcp-app" title="{html.escape(title)}" sandbox="allow-scripts"
        srcdoc="{html.escape(inner, quote=True)}"></iframe>
<script>{outer_script}</script>
</body>
</html>
"""
    _ = uri
    return outer


def _resource_payload(
    uri: str, title: str, snapshot: Mapping[str, Any], request_method: str
) -> dict[str, Any]:
    html_text = _document(uri, title, snapshot, request_method)
    fallback = {
        "uri": uri,
        "app": title,
        "snapshot": dict(snapshot),
        "transport": "jsonrpc-over-postmessage",
        "rendered": False,
    }
    return {
        "contents": [
            {"uri": uri, "mimeType": "text/html", "text": html_text},
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(fallback, ensure_ascii=False, sort_keys=True),
            },
        ],
        "ui": {
            "uri": uri,
            "sandbox": "allow-scripts",
            "transport": "jsonrpc-over-postmessage",
            "fallbackMimeType": "application/json",
        },
    }


def _deny(uri: str, params: Mapping[str, Any]) -> None:
    _ = params
    raise MCPError(POLICY_DENIED, f"UI resource is not available: {uri}")


def _allow(uri: str, params: Mapping[str, Any]) -> None:
    _ = params
    if uri not in ALLOWED_URIS:
        raise MCPError(POLICY_DENIED, f"UI resource is not available: {uri}")


def empty_runtime() -> AppRuntime:
    return AppRuntime(resource_specs=(), resources={}, policy=_deny)


def build_runtime(
    catalog: Catalog,
    skill_registry: SkillRegistry | None = None,
    task_store: TaskStore | None = None,
) -> AppRuntime:
    """Build the two allowlisted UI resources from local, redacted data."""

    def approval_snapshot(params: Mapping[str, Any]) -> dict[str, Any]:
        skills_data: list[dict[str, Any]] = []
        if skill_registry is not None:
            try:
                skills_data = [item.summary() for item in skill_registry.list()]
            except Exception:  # noqa: BLE001 - a broken DB must not break the UI resource
                skills_data = []
        pending: list[dict[str, Any]] = []
        tenant = params.get("tenant_id")
        session = params.get("session_id")
        if task_store is not None and isinstance(tenant, str) and tenant:
            try:
                rows = task_store.list(
                    tenant,
                    session if isinstance(session, str) and session else None,
                    limit=20,
                )
            except Exception:  # noqa: BLE001
                rows = []
            pending = [
                {
                    "task_id": str(row.get("task_id", "")),
                    "status": str(row.get("status", "")),
                    "updated_at": str(row.get("updated_at", "")),
                }
                for row in rows
                if row.get("status") == "awaiting_approval"
            ]
        return {
            "skills": skills_data,
            "pending_approvals": pending,
            "available_actions": [
                {"method": "skills/approve", "name_param": "name", "write": True},
                {"method": "skills/revoke", "name_param": "name", "write": True},
                {"method": "tasks/approve", "name_param": "task_id", "write": True},
                {"method": "tasks/list", "name_param": None, "write": False},
            ],
            "read_only_ui": True,
        }

    def catalog_snapshot(params: Mapping[str, Any]) -> dict[str, Any]:
        _ = params
        try:
            stats = catalog.stats()
        except Exception:  # noqa: BLE001
            stats = {}
        return {
            "serverInfo": {"name": "mcp-hub", "readOnly": True},
            "catalogStats": stats,
            "uiOnly": sorted(ALLOWED_URIS),
        }

    def approval_ui(params: Mapping[str, Any]) -> dict[str, Any]:
        return _resource_payload(
            APPROVAL_URI, "Approval review", approval_snapshot(params), "skills/list"
        )

    def catalog_ui(params: Mapping[str, Any]) -> dict[str, Any]:
        return _resource_payload(
            CATALOG_URI, "Tool catalog", catalog_snapshot(params), "tools/list"
        )

    return AppRuntime(
        resource_specs=_SPECS,
        resources={APPROVAL_URI: approval_ui, CATALOG_URI: catalog_ui},
        policy=_allow,
    )
