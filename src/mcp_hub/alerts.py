from __future__ import annotations

import json
import urllib.error
from typing import Any

from .redact import redact
from .safe_fetch import DEFAULT_TIMEOUT, UnsafeURLError, safe_request

USER_AGENT = "mcp-hub/1.3"


def add_alert(db, channel: str, target: str, query: str = "") -> int:
    return db.add_alert(channel, target, query)


def list_alerts(db) -> list[dict[str, Any]]:
    return db.list_alerts()


def _post_webhook(url: str, payload: dict[str, Any], timeout: float = 15.0) -> bool:
    data = json.dumps(payload).encode("utf-8")
    status, _ = safe_request(
        url,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        data=data,
        timeout=timeout,
    )
    return 200 <= status < 300


def dispatch(db, search_name: str, new_count: int) -> dict[str, Any]:
    alerts = db.list_alerts()
    sent = 0
    failed = 0
    errors: list[dict[str, Any]] = []
    for a in alerts:
        if a.get("query") and a.get("query") != search_name:
            continue
        if a.get("channel") != "webhook":
            continue
        try:
            ok = _post_webhook(
                a.get("target", ""),
                {"search": search_name, "new_count": int(new_count)},
            )
            if ok:
                sent += 1
            else:
                failed += 1
        except UnsafeURLError as exc:
            failed += 1
            errors.append({"id": a.get("id"), "error": f"blocked: {redact(exc)}"})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failed += 1
            errors.append({"id": a.get("id"), "error": redact(exc)})
        except Exception as exc:  # noqa: BLE001 - one bad channel must not stop the rest
            failed += 1
            errors.append({"id": a.get("id"), "error": redact(exc)})
    out: dict[str, Any] = {"sent": sent, "failed": failed, "total": len(alerts)}
    if errors:
        out["errors"] = errors
    return out


__all__ = ["DEFAULT_TIMEOUT", "add_alert", "dispatch", "list_alerts"]
