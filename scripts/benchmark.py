#!/usr/bin/env python3
"""Benchmark mcp-hub at multiple scales."""
from __future__ import annotations

import json
import os
import tempfile
import time

from mcp_hub import taxonomy
from mcp_hub.database import Catalog
from mcp_hub.models import Server


def bench_insert(n: int) -> dict:
    tmp = tempfile.mkdtemp()
    db = Catalog(os.path.join(tmp, "b.sqlite3"))
    servers = [
        Server(
            name=f"Tool-{i}",
            publisher=f"pub-{i % 100}",
            description=f"Description for tool {i} " * 3,
            repository_url=f"https://github.com/pub{i % 100}/tool{i}",
            source="bench",
        )
        for i in range(n)
    ]
    start = time.monotonic()
    result = db.bulk_add(servers)
    elapsed = time.monotonic() - start
    return {
        "n": n,
        "elapsed_s": round(elapsed, 3),
        "rows_per_sec": round(n / elapsed, 1) if elapsed else None,
        "new": result["new"],
        "skipped": result["skipped"],
    }


def bench_search(n: int, db: Catalog) -> dict:
    start = time.monotonic()
    for _ in range(100):
        db.search("tool", limit=50)
    return {"n": n, "query": "tool (100x)", "elapsed_s": round(time.monotonic() - start, 3)}


def bench_fts(n: int, db: Catalog) -> dict:
    if not db.fts_enabled:
        return {"n": n, "skipped": "fts5 unavailable"}
    start = time.monotonic()
    for _ in range(100):
        db.search_fts("tool", limit=50)
    return {"n": n, "query": "fts:tool (100x)", "elapsed_s": round(time.monotonic() - start, 3)}


def bench_classify(n: int, db: Catalog) -> dict:
    start = time.monotonic()
    taxonomy.classify_catalog(db, overwrite=True)
    return {"n": n, "elapsed_s": round(time.monotonic() - start, 3)}


def main() -> None:
    results = []
    for n in (100, 1000):
        tmp = tempfile.mkdtemp()
        db = Catalog(os.path.join(tmp, "b.sqlite3"))
        r_ins = bench_insert(n)
        db2 = Catalog(os.path.join(tmp, "b.sqlite3"))
        results.append(
            {
                "insert": r_ins,
                "search": bench_search(n, db2),
                "fts": bench_fts(n, db2),
                "classify": bench_classify(n, db2),
            }
        )
        print(json.dumps(results[-1], indent=2))
    with open("benchmark-results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("saved: benchmark-results.json")


if __name__ == "__main__":
    main()
