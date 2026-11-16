from __future__ import annotations

import json
from typing import Any


def record_row(catalog, server_id: int, source: str, payload: dict[str, Any]) -> None:
    try:
        with catalog.connect() as con:
            con.execute(
                "INSERT INTO provenance (server_id, source, payload_json) VALUES (?,?,?)",
                (int(server_id), source or "unknown", json.dumps(payload, ensure_ascii=False)),
            )
    except Exception:
        # provenance must never break ingestion
        return
