from __future__ import annotations

from .normalize import normalize_categories

DEFAULT_RULES: dict[str, list[str]] = {
    "database": ["postgres", "mysql", "sqlite", "mongodb", "redis", "sql", "database", "db"],
    "search": ["search", "serp", "index", "vector", "retrieval"],
    "web": ["browser", "http", "crawl", "scrape", "web"],
    "files": ["file", "filesystem", "storage", "s3", "drive"],
    "devtools": ["git", "github", "code", "developer", "repo", "ci", "build"],
    "ai": ["llm", "embedding", "inference", "model", "agent"],
    "communication": ["email", "slack", "discord", "chat", "message"],
}


def _text(server: dict) -> str:
    return " ".join(
        [
            server.get("name", ""),
            server.get("publisher", ""),
            server.get("description", ""),
            server.get("package_name", ""),
            server.get("categories", ""),
        ]
    ).lower()


def classify(server: dict, custom_rules: dict[str, list[str]] | None = None) -> str:
    rules = dict(DEFAULT_RULES)
    if custom_rules:
        for k, terms in custom_rules.items():
            rules[k.lower()] = [t.lower() for t in terms]
    hay = _text(server)
    hits = []
    for cat, terms in rules.items():
        if any(t in hay for t in terms):
            hits.append(cat)
    return ",".join(hits)


def classify_catalog(db, overwrite: bool = False) -> dict[str, int]:
    custom = {c["name"]: c.get("terms", []) for c in db.custom_categories()}
    overrides = {o["server_id"]: o["categories"] for o in db.category_overrides()}
    updated = 0
    total = 0
    with db.connect() as con:
        rows = con.execute("SELECT * FROM servers").fetchall()
        for row in rows:
            total += 1
            srv = dict(row)
            sid = int(srv["id"])
            if sid in overrides:
                cats = normalize_categories(overrides[sid])
            else:
                if srv.get("categories") and not overwrite:
                    continue
                cats = classify(srv, custom_rules=custom)
            if cats != (srv.get("categories") or ""):
                con.execute(
                    "UPDATE servers SET categories=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (cats, sid),
                )
                if db.fts_enabled:
                    try:
                        con.execute(
                            "UPDATE servers_fts SET categories=? WHERE rowid=?",
                            (cats, sid),
                        )
                    except Exception:
                        pass
                updated += 1
    return {"total": total, "updated": updated}
