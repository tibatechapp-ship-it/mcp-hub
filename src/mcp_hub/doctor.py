from __future__ import annotations

import os

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
            journal = con.execute("PRAGMA journal_mode").fetchone()[0]
            pages = con.execute("PRAGMA page_count").fetchone()[0]
            free = con.execute("PRAGMA freelist_count").fetchone()[0]
        checks["db_ok"] = integrity == "ok"
        checks["integrity"] = integrity
        checks["indexes"] = "ok" if idx > 0 else "missing"
        checks["indexes_count"] = int(idx)
        checks["journal_mode"] = str(journal)
        checks["pages"] = int(pages)
        # A high free-page ratio means a VACUUM would reclaim real space.
        checks["free_pages"] = int(free)
        checks["needs_vacuum"] = bool(pages and free / pages > 0.2)
    except Exception as e:  # noqa: BLE001
        checks["db_ok"] = False
        checks["error"] = str(e)
    checks["fts_enabled"] = bool(getattr(db, "fts_enabled", False))
    checks["backup_hint"] = f"mcp-hub --db {db.path} backup {db.path}.bak"
    checks["github_token_set"] = bool(os.environ.get("GITHUB_TOKEN"))
    checks["status"] = "ok" if checks.get("db_ok") else "error"
    return checks
