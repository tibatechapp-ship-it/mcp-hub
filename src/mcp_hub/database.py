from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import provenance as _prov
from .dedupe import compare
from .models import Server
from .normalize import normalize_categories, normalize_name, normalize_repo_url, normalize_url


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Bumped when provenance needs a one-time backfill (see _backfill_field_provenance).
_SCHEMA_VERSION = 1

_PROVENANCE_TRACKED_FIELDS = frozenset(
    {
        "name",
        "publisher",
        "description",
        "repository_url",
        "endpoint_url",
        "popularity",
        "stars",
        "forks",
        "license",
        "last_pushed_at",
        "topics",
        "categories",
        "health_status",
    }
)


class Catalog:
    def __init__(self, path: str | Path = "mcp_hub.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_enabled = False
        self._init()

    @contextmanager
    def connect(self):
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init(self) -> None:
        with self.connect() as con:
            self._migrate(con)
            self._ensure_fts(con)

    def _migrate(self, con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                registry_id TEXT NOT NULL DEFAULT '',
                repository_url TEXT NOT NULL DEFAULT '',
                endpoint_url TEXT NOT NULL DEFAULT '',
                package_name TEXT NOT NULL DEFAULT '',
                categories TEXT NOT NULL DEFAULT '',
                raw_name TEXT NOT NULL DEFAULT '',
                popularity INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                source TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(server_id) REFERENCES servers(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS field_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL DEFAULT '',
                set_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_id, field_name, source)
            );
            CREATE TABLE IF NOT EXISTS duplicate_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_a INTEGER NOT NULL,
                server_b INTEGER NOT NULL,
                classification TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_a, server_b)
            );
            CREATE TABLE IF NOT EXISTS enrichments (
                server_id INTEGER PRIMARY KEY,
                stars INTEGER NOT NULL DEFAULT 0,
                forks INTEGER NOT NULL DEFAULT 0,
                license TEXT NOT NULL DEFAULT '',
                pushed_at TEXT NOT NULL DEFAULT '',
                topics TEXT NOT NULL DEFAULT '',
                enriched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(server_id) REFERENCES servers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS saved_searches (
                name TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_run_at TEXT,
                last_new_ids TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS custom_categories (
                name TEXT PRIMARY KEY,
                terms TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS category_overrides (
                server_id INTEGER PRIMARY KEY,
                categories TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(server_id) REFERENCES servers(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                target TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS scheduler_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                total_searches INTEGER NOT NULL DEFAULT 0,
                total_new INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Column-level migrations first. CREATE TABLE IF NOT EXISTS is a no-op
        # when the table already exists, so a database written by an older
        # release is missing every column added since — and the indexes below
        # would fail on a column that is not there yet.
        self._ensure_columns(
            con,
            "servers",
            {
                "popularity": "INTEGER NOT NULL DEFAULT 0",
                "archived": "INTEGER NOT NULL DEFAULT 0",
                "categories": "TEXT NOT NULL DEFAULT ''",
                "raw_name": "TEXT NOT NULL DEFAULT ''",
                "package_name": "TEXT NOT NULL DEFAULT ''",
                "endpoint_url": "TEXT NOT NULL DEFAULT ''",
                "repository_url": "TEXT NOT NULL DEFAULT ''",
                "registry_id": "TEXT NOT NULL DEFAULT ''",
                "source": "TEXT NOT NULL DEFAULT ''",
                "description": "TEXT NOT NULL DEFAULT ''",
                "publisher": "TEXT NOT NULL DEFAULT ''",
                "name": "TEXT NOT NULL DEFAULT ''",
                # ALTER TABLE cannot use a non-constant default, so these start
                # empty and are stamped below.
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self._ensure_columns(
            con,
            "scheduler_runs",
            {
                "finished_at": "TEXT NOT NULL DEFAULT ''",
                "total_searches": "INTEGER NOT NULL DEFAULT 0",
                "total_new": "INTEGER NOT NULL DEFAULT 0",
                "failed": "INTEGER NOT NULL DEFAULT 0",
                "detail": "TEXT NOT NULL DEFAULT ''",
                "duration_ms": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        con.execute("UPDATE servers SET created_at = CURRENT_TIMESTAMP WHERE created_at = ''")
        con.execute("UPDATE servers SET updated_at = CURRENT_TIMESTAMP WHERE updated_at = ''")
        con.execute(
            "UPDATE scheduler_runs SET finished_at = CURRENT_TIMESTAMP WHERE finished_at = ''"
        )
        # indexes (idempotent)
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_servers_source ON servers(source)",
            "CREATE INDEX IF NOT EXISTS idx_servers_registry ON servers(registry_id)",
            "CREATE INDEX IF NOT EXISTS idx_servers_repo ON servers(repository_url)",
            "CREATE INDEX IF NOT EXISTS idx_servers_endpoint ON servers(endpoint_url)",
            "CREATE INDEX IF NOT EXISTS idx_servers_pub_name ON servers(publisher, name)",
            "CREATE INDEX IF NOT EXISTS idx_servers_active ON servers(archived, popularity DESC)",
            "CREATE INDEX IF NOT EXISTS idx_dupes_a ON duplicate_candidates(server_a)",
            "CREATE INDEX IF NOT EXISTS idx_dupes_b ON duplicate_candidates(server_b)",
            "CREATE INDEX IF NOT EXISTS idx_fp_server ON field_provenance(server_id)",
            "CREATE INDEX IF NOT EXISTS idx_fp_field ON field_provenance(server_id, field_name)",
            "CREATE INDEX IF NOT EXISTS idx_fp_source ON field_provenance(source)",
            "CREATE INDEX IF NOT EXISTS idx_fp_set_at ON field_provenance(set_at)",
        ):
            con.execute(stmt)
        self._backfill_field_provenance(con)

    @staticmethod
    def _ensure_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        """Add any column an older release did not have. Idempotent."""
        existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _backfill_field_provenance(self, con: sqlite3.Connection) -> None:
        """One-time, idempotent migration: provenance JSON -> field_provenance index.

        Guarded by PRAGMA user_version so it runs once per database file.
        Later writes win (rows are replayed chronologically).
        """
        if con.execute("PRAGMA user_version").fetchone()[0] >= _SCHEMA_VERSION:
            return
        cols = {r[1] for r in con.execute("PRAGMA table_info(provenance)")}
        ts_col = "set_at" if "set_at" in cols else "created_at"
        rows = con.execute(
            f"SELECT server_id, source, payload_json, {ts_col} AS set_at "
            "FROM provenance ORDER BY id ASC"
        ).fetchall()
        for r in rows:
            if r["server_id"] is None:
                continue
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            for field, value in payload.items():
                if field not in _PROVENANCE_TRACKED_FIELDS:
                    continue
                con.execute(
                    "INSERT INTO field_provenance "
                    "(server_id, field_name, source, value, set_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(server_id, field_name, source) DO UPDATE SET "
                    "value=excluded.value, set_at=excluded.set_at",
                    (
                        int(r["server_id"]),
                        field,
                        r["source"] or "unknown",
                        "" if value is None else str(value),
                        r["set_at"],
                    ),
                )
        con.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def _ensure_fts(self, con: sqlite3.Connection) -> None:
        try:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS servers_fts USING "
                "fts5(name, publisher, description, categories)"
            )
            cnt = con.execute("SELECT COUNT(*) AS c FROM servers_fts").fetchone()["c"]
            srv = con.execute("SELECT COUNT(*) AS c FROM servers").fetchone()["c"]
            if srv and not cnt:
                con.execute("DELETE FROM servers_fts")
                con.execute(
                    "INSERT INTO servers_fts(rowid,name,publisher,description,categories) "
                    "SELECT id,name,publisher,description,categories FROM servers"
                )
            self._fts_enabled = True
        except sqlite3.OperationalError:
            self._fts_enabled = False

    @property
    def fts_enabled(self) -> bool:
        return self._fts_enabled

    # ---- core CRUD ----
    def _server_exists(self, con: sqlite3.Connection, data: dict[str, Any]):
        if data.get("registry_id"):
            row = con.execute(
                "SELECT id FROM servers WHERE registry_id=? AND source=?",
                (data["registry_id"], data.get("source", "")),
            ).fetchone()
            if row:
                return row["id"]
        row = con.execute(
            "SELECT id FROM servers WHERE name=? AND publisher=? AND description=? AND source=?",
            (
                data.get("name", ""),
                data.get("publisher", ""),
                data.get("description", ""),
                data.get("source", ""),
            ),
        ).fetchone()
        return row["id"] if row else None

    def _candidates_for(self, con: sqlite3.Connection, new_id: int, data: dict[str, Any]) -> list:
        clauses = ["id != ?"]
        params: list[Any] = [new_id]

        reg = (data.get("registry_id") or "").strip()
        if reg:
            clauses.append("registry_id = ?")
            params.append(reg)

        repo = normalize_repo_url(data.get("repository_url"))
        if repo:
            clauses.append("repository_url = ?")
            params.append(repo)

        ep = normalize_url(data.get("endpoint_url"))
        if ep:
            clauses.append("endpoint_url = ?")
            params.append(ep)

        pub = (data.get("publisher") or "").strip()
        if pub:
            norm = normalize_name(data.get("name", ""))
            prefix = norm.split()[0] if norm.split() else ""
            clauses.append("(publisher = ? AND name LIKE ?)")
            params.extend([pub, f"%{prefix}%" if prefix else "%"])

        query = f"SELECT * FROM servers WHERE {' OR '.join(clauses)} LIMIT 200"
        try:
            return con.execute(query, params).fetchall()
        except Exception:
            return []

    def _fts_upsert(
        self,
        con: sqlite3.Connection,
        rowid: int,
        name: str,
        publisher: str,
        description: str,
        categories: str,
    ) -> None:
        if not self._fts_enabled:
            return
        try:
            con.execute("DELETE FROM servers_fts WHERE rowid=?", (int(rowid),))
            con.execute(
                "INSERT INTO servers_fts(rowid,name,publisher,description,categories) "
                "VALUES (?,?,?,?,?)",
                (
                    int(rowid),
                    name or "",
                    publisher or "",
                    description or "",
                    categories or "",
                ),
            )
        except Exception:
            pass

    def _insert_server_row(self, con: sqlite3.Connection, data: dict[str, Any]) -> int:
        cols = (
            "name",
            "publisher",
            "description",
            "source",
            "registry_id",
            "repository_url",
            "endpoint_url",
            "package_name",
            "categories",
            "raw_name",
            "popularity",
        )
        values = []
        for k in cols:
            if k == "popularity":
                values.append(int(data.get(k, 0) or 0))
            elif k == "categories":
                values.append(normalize_categories(data.get(k, "")))
            elif k == "repository_url":
                values.append(normalize_repo_url(data.get(k, "")))
            elif k == "endpoint_url":
                values.append(normalize_url(data.get(k, "")))
            else:
                values.append((data.get(k) or "").strip())
        cur = con.execute(
            f"INSERT INTO servers ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(values),
        )
        new_id = int(cur.lastrowid)
        self._fts_upsert(con, new_id, values[0], values[1], values[2], values[8])
        return new_id

    def _detect_dupes(self, con: sqlite3.Connection, new_id: int, data: dict[str, Any]) -> None:
        for row in self._candidates_for(con, new_id, data):
            cls, reason = compare(dict(row), data)
            if cls == "distinct":
                continue
            a, b = sorted((int(row["id"]), int(new_id)))
            con.execute(
                "INSERT OR IGNORE INTO duplicate_candidates "
                "(server_a,server_b,classification,reason) VALUES (?,?,?,?)",
                (a, b, cls, reason),
            )

    def add_server(self, server: Server | dict[str, Any]) -> int | None:
        data = server.to_dict() if isinstance(server, Server) else dict(server)
        with self.connect() as con:
            existing = self._server_exists(con, data)
            if existing:
                return None
            new_id = self._insert_server_row(con, data)
            _prov.record_row(con, new_id, data.get("source", "") or "unknown", data)
            self._detect_dupes(con, new_id, data)
            return new_id

    def bulk_add(
        self,
        servers: Iterable[Server | dict[str, Any]],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        items = list(servers)
        total = len(items)
        new_count = 0
        skipped = 0
        new_ids: list[int] = []
        with self.connect() as con:
            for idx, srv in enumerate(items):
                data = srv.to_dict() if isinstance(srv, Server) else dict(srv)
                existing = self._server_exists(con, data)
                if existing:
                    skipped += 1
                else:
                    new_id = self._insert_server_row(con, data)
                    _prov.record_row(con, new_id, data.get("source", "") or "unknown", data)
                    self._detect_dupes(con, new_id, data)
                    new_ids.append(new_id)
                    new_count += 1
                if progress_callback and (idx + 1) % 100 == 0:
                    progress_callback(idx + 1, total)
        return {"new": new_count, "skipped": skipped, "ids": new_ids}

    # ---- queries ----
    def get_server(self, server_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM servers WHERE id=?", (int(server_id),)).fetchone()
            return dict(row) if row else None

    def list_servers(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM servers ORDER BY popularity DESC, id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
            return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        q = f"%{(query or '').strip()}%"
        if not query:
            return self.list_servers(limit=limit, offset=offset)
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM servers WHERE archived=0 AND "
                "(name LIKE ? OR publisher LIKE ? OR description LIKE ? OR categories LIKE ?) "
                "ORDER BY popularity DESC, id DESC LIMIT ? OFFSET ?",
                (q, q, q, q, int(limit), int(offset)),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_fts(self, query: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not self._fts_enabled or not (query or "").strip():
            return self.search(query, limit=limit, offset=offset)
        # basic escaping for FTS
        safe = (query or "").replace('"', " ").strip()
        fts_q = " ".join(f'"{w}"' for w in safe.split() if w)
        if not fts_q:
            return []
        with self.connect() as con:
            try:
                rows = con.execute(
                    "SELECT s.* FROM servers_fts f JOIN servers s ON s.id=f.rowid "
                    "WHERE f MATCH ? AND s.archived=0 "
                    "ORDER BY s.popularity DESC, s.id DESC LIMIT ? OFFSET ?",
                    (fts_q, int(limit), int(offset)),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                # fallback: try plain terms without strict quoting issues
                return self.search(query, limit=limit, offset=offset)
            except Exception:
                return self.search(query, limit=limit, offset=offset)

    # ---- enrichments ----
    def servers_needing_enrichment(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT s.* FROM servers s LEFT JOIN enrichments e ON e.server_id=s.id "
                "WHERE s.repository_url != '' AND e.server_id IS NULL "
                "ORDER BY s.popularity DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_enrichment(self, server_id: int, data: dict[str, Any]) -> None:
        stars = int(data.get("stars", 0) or 0)
        forks = int(data.get("forks", 0) or 0)
        license_name = (data.get("license") or "").strip()
        pushed_at = (data.get("pushed_at") or "").strip()
        topics = (data.get("topics") or "").strip()
        with self.connect() as con:
            con.execute(
                "INSERT INTO enrichments "
                "(server_id,stars,forks,license,pushed_at,topics,enriched_at) "
                "VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(server_id) DO UPDATE SET "
                "stars=excluded.stars,forks=excluded.forks,license=excluded.license,"
                "pushed_at=excluded.pushed_at,topics=excluded.topics,"
                "enriched_at=CURRENT_TIMESTAMP",
                (
                    int(server_id),
                    stars,
                    forks,
                    license_name,
                    pushed_at,
                    topics,
                ),
            )
            # dual write: audit event + field index, same transaction
            _prov.record_row(
                con,
                server_id,
                "github",
                {
                    "stars": stars,
                    "forks": forks,
                    "license": license_name,
                    "last_pushed_at": pushed_at,
                    "topics": topics,
                },
            )

    # ---- categories ----
    def custom_categories(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM custom_categories ORDER BY name").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                terms = [t.strip() for t in (d.get("terms") or "").split(",") if t.strip()]
                d["terms"] = terms
                out.append(d)
            return out

    def add_custom_category(self, name: str, terms: str, notes: str = "") -> None:
        nm = (name or "").strip().lower()
        if not nm:
            raise ValueError("category name is required")
        term_list = [t.strip().lower() for t in (terms or "").split(",") if t.strip()]
        with self.connect() as con:
            con.execute(
                "INSERT INTO custom_categories (name,terms,notes) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET terms=excluded.terms, notes=excluded.notes",
                (nm, ",".join(term_list), notes or ""),
            )

    def delete_custom_category(self, name: str) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "DELETE FROM custom_categories WHERE name=?",
                ((name or "").strip().lower(),),
            )
            return cur.rowcount > 0

    def category_overrides(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM category_overrides ORDER BY server_id").fetchall()
            return [dict(r) for r in rows]

    def set_category_override(self, server_id: int, categories: str) -> None:
        cats = normalize_categories(categories)
        with self.connect() as con:
            con.execute(
                "INSERT INTO category_overrides (server_id,categories) VALUES (?,?) "
                "ON CONFLICT(server_id) DO UPDATE SET categories=excluded.categories",
                (int(server_id), cats),
            )
            _prov.record(con, server_id, "categories", "override", cats)

    def clear_category_override(self, server_id: int) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "DELETE FROM category_overrides WHERE server_id=?",
                (int(server_id),),
            )
            return cur.rowcount > 0

    def export_custom_categories_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "exported_at_utc": _now_iso(),
            "categories": self.custom_categories(),
            "overrides": self.category_overrides(),
        }

    def import_custom_categories_json(
        self, data: dict[str, Any], replace: bool = False
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("payload must be a JSON object")
        if replace:
            with self.connect() as con:
                con.execute("DELETE FROM custom_categories")
                con.execute("DELETE FROM category_overrides")
        imported_cats = 0
        imported_ovr = 0
        for c in data.get("categories", []):
            try:
                terms = c.get("terms", [])
                if isinstance(terms, list):
                    terms = ",".join(str(t) for t in terms)
                self.add_custom_category(c.get("name", ""), terms, c.get("notes", ""))
                imported_cats += 1
            except Exception:
                pass
        for o in data.get("overrides", []):
            try:
                self.set_category_override(o.get("server_id"), o.get("categories", ""))
                imported_ovr += 1
            except Exception:
                pass
        return {"categories": imported_cats, "overrides": imported_ovr, "replaced": replace}

    # ---- saved searches ----
    def save_search(self, name: str, query: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO saved_searches (name,query) VALUES (?,?) "
                "ON CONFLICT(name) DO UPDATE SET query=excluded.query",
                (name.strip(), query.strip()),
            )

    def delete_saved_search(self, name: str) -> bool:
        with self.connect() as con:
            cur = con.execute("DELETE FROM saved_searches WHERE name=?", (name.strip(),))
            return cur.rowcount > 0

    def list_saved_searches(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM saved_searches ORDER BY name").fetchall()
            return [dict(r) for r in rows]

    def get_saved_search(self, name: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM saved_searches WHERE name=?",
                (name.strip(),),
            ).fetchone()
            return dict(row) if row else None

    def update_saved_search_run(self, name: str, ids: list[int]) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE saved_searches "
                "SET last_run_at=CURRENT_TIMESTAMP, last_new_ids=? WHERE name=?",
                (json.dumps(ids), name.strip()),
            )

    # ---- alerts / scheduler ----
    def add_alert(self, channel: str, target: str, query: str = "") -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO alerts (channel,target,query) VALUES (?,?,?)",
                (channel.strip(), target.strip(), (query or "").strip()),
            )
            return int(cur.lastrowid)

    def list_alerts(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM alerts ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def log_scheduler_run(
        self,
        total_searches: int,
        total_new: int,
        failed: int,
        detail: str = "",
        duration_ms: int = 0,
    ) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO scheduler_runs "
                "(total_searches,total_new,failed,detail,duration_ms) VALUES (?,?,?,?,?)",
                (int(total_searches), int(total_new), int(failed), detail or "", int(duration_ms)),
            )

    def scheduler_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM scheduler_runs ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---- misc ----
    def stats(self) -> dict[str, Any]:
        with self.connect() as con:
            records = con.execute("SELECT COUNT(*) AS c FROM servers").fetchone()["c"]
            sources = con.execute("SELECT COUNT(DISTINCT source) AS c FROM servers").fetchone()["c"]
            dupes = con.execute("SELECT COUNT(*) AS c FROM duplicate_candidates").fetchone()["c"]
            enriched = con.execute("SELECT COUNT(*) AS c FROM enrichments").fetchone()["c"]
            return {
                "records": records,
                "sources": sources,
                "duplicate_candidates": dupes,
                "enriched": enriched,
                "fts_enabled": self._fts_enabled,
            }

    def all_servers(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM servers ORDER BY id").fetchall()
            return [dict(r) for r in rows]
