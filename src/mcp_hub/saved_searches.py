from __future__ import annotations

import json
from typing import Any


def _as_int_set(value: Any) -> set[int]:
    if not value:
        return set()
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except Exception:
        return set()
    out: set[int] = set()
    if isinstance(data, list):
        for x in data:
            try:
                out.add(int(x))
            except Exception:
                continue
    return out


def save(db, name: str, query: str) -> None:
    db.save_search(name, query)


def delete(db, name: str) -> bool:
    return db.delete_saved_search(name)


def list_all(db) -> list[dict[str, Any]]:
    return db.list_saved_searches()


def run(db, name: str) -> dict[str, Any]:
    item = db.get_saved_search(name)
    if not item:
        raise KeyError(f"saved search not found: {name}")
    results = db.search(item["query"], limit=500)
    ids = [int(r["id"]) for r in results]
    prev = _as_int_set(item.get("last_new_ids"))
    now = set(ids)
    new_ids = sorted(now - prev)
    db.update_saved_search_run(name, ids)
    return {
        "name": name,
        "query": item["query"],
        "match_count": len(ids),
        "new_ids": new_ids,
        "new_count": len(new_ids),
        "results": results,
    }
