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

from .arabic_runtime import resolve_lang
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
    "ENV_UI_LANG",
    "AppRuntime",
    "AppPolicy",
    "enabled",
    "build_runtime",
    "empty_runtime",
]

APPROVAL_URI = "ui://mcp-hub/approval-review"
CATALOG_URI = "ui://mcp-hub/tool-catalog"
ALLOWED_URIS: frozenset[str] = frozenset({APPROVAL_URI, CATALOG_URI})
ENV_UI_LANG = "MCP_HUB_UI_LANG"
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


def _ui_lang(params: Mapping[str, Any]) -> str:
    requested = params.get("lang")
    return resolve_lang(requested if isinstance(requested, str) else None, ENV_UI_LANG)


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


def _inner_script(request_method: str, lang: str) -> str:
    labels = (
        {
            "no_response": "لا توجد استجابة من المضيف.",
            "error": "خطأ ",
            "waiting": "لم يجب المضيف؛ عرض اللقطة المضمنة.",
        }
        if lang == "ar"
        else {
            "no_response": "No response from host.",
            "error": "Error ",
            "waiting": "Host did not answer; showing embedded snapshot.",
        }
    )
    labels_json = _safe_json(labels)
    return f"""\
(function () {{
  var out = document.getElementById('out');
  var labels = {labels_json};
  function render(message) {{
    if (!message) {{ out.textContent = labels.no_response; return; }}
    if (message.error) {{
      out.textContent = labels.error + message.error.code + ': ' + message.error.message;
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
    if (!out.textContent) {{ out.textContent = labels.waiting; }}
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


def _document(
    uri: str, title: str, snapshot: Mapping[str, Any], request_method: str, lang: str = "en"
) -> str:
    arabic = lang == "ar"
    inner_script = _inner_script(request_method, lang)
    outer_script = _outer_script()
    csp = _csp((_hash(inner_script), _hash(outer_script)))
    escaped_csp = html.escape(csp, quote=True)
    snapshot_json = _safe_json(snapshot)
    direction = "rtl" if arabic else "ltr"
    notice = (
        "تطبيق MCP للقراءة فقط. لا يتم عرض أي بيانات حساسة."
        if arabic
        else "Read-only MCP App. No credentials are rendered."
    )
    response_label = "الاستجابة" if arabic else "Response"
    waiting = "في انتظار استجابة JSON-RPC..." if arabic else "Waiting for JSON-RPC response..."
    noscript = (
        "بدون JavaScript يتوفر بديل JSON في استجابة مورد MCP."
        if arabic
        else "JSON fallback is included in the MCP resource response."
    )
    sandbox_label = " (داخل إطار معزول)" if arabic else " (sandboxed)"
    inner = f"""\
<!doctype html>
<html lang="{html.escape(lang, quote=True)}" dir="{direction}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{escaped_csp}">
<title>{html.escape(title)}</title>
</head>
<body>
<h1>{html.escape(title)}</h1>
<p>{html.escape(notice)}</p>
<pre id="snapshot">{html.escape(snapshot_json)}</pre>
<h2>{html.escape(response_label)}</h2>
<pre id="out">{html.escape(waiting)}</pre>
<noscript>{html.escape(noscript)}</noscript>
<script>{inner_script}</script>
</body>
</html>
"""
    outer = f"""\
<!doctype html>
<html lang="{html.escape(lang, quote=True)}" dir="{direction}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{escaped_csp}">
<title>{html.escape(title)}{html.escape(sandbox_label)}</title>
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
    uri: str,
    title: str,
    snapshot: Mapping[str, Any],
    request_method: str,
    lang: str = "en",
) -> dict[str, Any]:
    html_text = _document(uri, title, snapshot, request_method, lang)
    direction = "rtl" if lang == "ar" else "ltr"
    fallback = {
        "uri": uri,
        "app": title,
        "snapshot": dict(snapshot),
        "transport": "jsonrpc-over-postmessage",
        "rendered": False,
        "lang": lang,
        "dir": direction,
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
            "lang": lang,
            "dir": direction,
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
        lang = _ui_lang(params)
        title = "مراجعة الاعتمادات" if lang == "ar" else "Approval review"
        return _resource_payload(
            APPROVAL_URI, title, approval_snapshot(params), "skills/list", lang
        )

    def catalog_ui(params: Mapping[str, Any]) -> dict[str, Any]:
        lang = _ui_lang(params)
        title = "كتالوج الأدوات" if lang == "ar" else "Tool catalog"
        return _resource_payload(CATALOG_URI, title, catalog_snapshot(params), "tools/list", lang)

    return AppRuntime(
        resource_specs=_SPECS,
        resources={APPROVAL_URI: approval_ui, CATALOG_URI: catalog_ui},
        policy=_allow,
    )
