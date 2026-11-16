#!/usr/bin/env python3
"""Simple async load test for /api/search."""
from __future__ import annotations

import asyncio
import time

import aiohttp


async def one(session: aiohttp.ClientSession, url: str, headers: dict) -> tuple[float, int]:
    t = time.monotonic()
    async with session.get(url, headers=headers) as r:
        await r.read()
        return time.monotonic() - t, r.status


async def main() -> None:
    url = "http://127.0.0.1:8000/api/search?q=tool&limit=20"
    headers = {}
    async with aiohttp.ClientSession() as session:
        start = time.monotonic()
        tasks = [one(session, url, headers) for _ in range(1000)]
        results = await asyncio.gather(*tasks)
        total = time.monotonic() - start
    latencies = sorted(r[0] for r in results)
    errors = sum(1 for _, s in results if s >= 400)
    print(f"requests: {len(results)}")
    print(f"duration: {total:.2f}s")
    print(f"rps:      {len(results)/total:.1f}")
    print(f"p50:      {latencies[len(latencies)//2]*1000:.0f}ms")
    print(f"p99:      {latencies[int(len(latencies)*0.99)]*1000:.0f}ms")
    print(f"errors:   {errors}")


if __name__ == "__main__":
    asyncio.run(main())
