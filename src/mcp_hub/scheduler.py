from __future__ import annotations

import json
import time

from . import alerts, saved_searches


def run_all(db) -> dict[str, object]:
    start = time.monotonic()
    searches = saved_searches.list_all(db)
    total_new = 0
    failed = 0
    details = []
    for s in searches:
        name = s.get("name")
        try:
            result = saved_searches.run(db, name)
            new_count = int(result.get("new_count", 0))
            total_new += new_count
            alerts.dispatch(db, name, new_count)
            details.append({"name": name, "new": new_count})
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append({"name": name, "error": str(e)})
    duration_ms = int((time.monotonic() - start) * 1000)
    detail = json.dumps(details, ensure_ascii=False)
    db.log_scheduler_run(len(searches), total_new, failed, detail=detail, duration_ms=duration_ms)
    return {
        "total_searches": len(searches),
        "total_new": total_new,
        "failed": failed,
        "duration_ms": duration_ms,
        "details": details,
    }
