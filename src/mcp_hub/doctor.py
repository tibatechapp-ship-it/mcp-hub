from __future__ import annotations

import os
import sqlite3

from . import __version__


def run(db) -> dict[str, object]:
    checks: dict[str, object] = {}
    checks["version"] = __version__
    checks["db_path"] = str(db.path)
    checks["db_exists"] = db.path.exists()
    try:
        with db.connect() as con:
            con.execute("SELECT 1").fetchone()
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            idx = con.execute(
                "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='index'"
            ).fetchone()["c"]
        checks["db_ok"] = integrity == "ok"
        checks["integrity"] = integrity
        checks["indexes"] = "ok" if idx > 0 else "missing"
        checks["indexes_count"] = int(idx)
    except Exception as e:  # noqa: BLE001
        checks["db_ok"] = False
        checks["error"] = str(e)
    checks["fts_enabled"] = bool(getattr(db, "fts_enabled", False))
    checks["github_token_set"] = bool(os.environ.get("GITHUB_TOKEN"))
    checks["status"] = "ok" if checks.get("db_ok") else "error"
    return checks
