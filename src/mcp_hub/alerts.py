from __future__ import annotations

import json
import urllib.request
from typing import Any


def add_alert(db, channel: str, target: str, query: str = "") -> int:
    return db.add_alert(channel, target, query)


def list_alerts(db) -> list[dict[str, Any]]:
    return db.list_alerts()


def _post_webhook(url: str, payload: dict[str, Any]) -> bool:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "mcp-hub/1.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return 200 <= resp.status < 300


def dispatch(db, search_name: str, new_count: int) -> dict[str, Any]:
    alerts = db.list_alerts()
    sent = 0
    failed = 0
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
        except Exception:
            failed += 1
    return {"sent": sent, "failed": failed, "total": len(alerts)}
