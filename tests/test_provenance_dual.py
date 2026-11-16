"""v1.1.1 — dual-layer provenance (audit trail + indexed field index).

Verifies:
  1. Both tables stay in sync on every write (same transaction).
  2. field_conflicts is fast and correct.
  3. event_history preserves the audit trail.
  4. Backfill migrates legacy provenance rows.
  5. Legacy history()/conflicts() aliases keep working.
"""

from __future__ import annotations

import sqlite3
import time

from mcp_hub import provenance
from mcp_hub.database import Catalog
from mcp_hub.models import Server
from mcp_hub.provenance import _timestamp_column

LEGACY_SERVERS_DDL = """
    CREATE TABLE servers (
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
"""


# -- 1. dual-write consistency ----------------------------------------------
def test_record_writes_to_both_tables(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="t"))

    provenance.record(db, sid, "description", "src-a", "alpha")

    with db.connect() as con:
        audit = con.execute("SELECT * FROM provenance WHERE server_id=?", (sid,)).fetchall()
        fields = con.execute("SELECT * FROM field_provenance WHERE server_id=?", (sid,)).fetchall()
    assert len(audit) >= 2  # insert event + field event
    assert any(r["field_name"] == "description" for r in fields)
    assert any(r["source"] == "src-a" for r in fields)


def test_record_row_writes_event_and_fields(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", description="v1", source="src-a"))
    db.save_enrichment(
        sid,
        {
            "stars": 10,
            "forks": 2,
            "license": "MIT",
            "pushed_at": "2026-01-01T00:00:00Z",
            "topics": "ai",
        },
    )

    events = provenance.event_history(db, sid)
    github_event = next(e for e in events if e["source"] == "github")
    # one audit event carrying all enrichment fields
    assert set(github_event["fields"].keys()) == {
        "stars",
        "forks",
        "license",
        "last_pushed_at",
        "topics",
    }

    fields = provenance.field_history(db, sid)
    names = {f["field_name"] for f in fields}
    assert {"stars", "forks", "license", "last_pushed_at", "topics"} <= names


def test_add_server_dual_write_is_atomic(tmp_path):
    """add_server writes index rows in the same transaction (was silently lost)."""
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(
        Server(
            name="X",
            publisher="p",
            description="d",
            source="t",
            registry_id="reg-1",
        )
    )

    fields = {f["field_name"]: f for f in provenance.field_history(db, sid)}
    assert fields["name"]["value"] == "X"
    assert fields["description"]["value"] == "d"
    assert fields["name"]["source"] == "t"

    # audit trail keeps the FULL payload, not just tracked fields
    event = next(e for e in provenance.event_history(db, sid) if e["source"] == "t")
    assert "registry_id" in event["fields"]


def test_upsert_keeps_latest_per_source(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    provenance.record(db, sid, "stars", "github", 10)
    provenance.record(db, sid, "stars", "github", 42)

    rows = provenance.field_history(db, sid, "stars")
    github_rows = [r for r in rows if r["source"] == "github"]
    assert len(github_rows) == 1
    assert github_rows[0]["value"] == "42"


def test_record_ignores_untracked_field(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    provenance.record(db, sid, "not_a_real_field", "src-b", "x")
    assert provenance.field_history(db, sid, "not_a_real_field") == []


# -- 2. conflict detection ---------------------------------------------------
def test_no_conflict_when_sources_agree(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", description="same", source="src-a"))
    provenance.record(db, sid, "description", "src-b", "same")
    assert "description" not in provenance.field_conflicts(db, sid)


def test_conflict_when_sources_disagree(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", description="alpha", source="src-a"))
    provenance.record(db, sid, "description", "src-b", "beta")
    conflicts = provenance.field_conflicts(db, sid)
    assert "description" in conflicts
    assert {e["value"] for e in conflicts["description"]} == {"alpha", "beta"}


def test_conflict_performance_on_10k(tmp_path):
    """Conflict detection must stay sub-second on a typical server size."""
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    for i in range(10):
        for f in ["name", "description", "stars", "forks", "license"]:
            provenance.record(db, sid, f, f"src-{i}", f"v{i}")

    start = time.monotonic()
    for _ in range(100):
        found = provenance.field_conflicts(db, sid)
    elapsed = time.monotonic() - start

    assert "description" in found  # correctness, not just speed
    assert elapsed < 1.0, f"100 conflict checks took {elapsed:.2f}s"


# -- 3. event history --------------------------------------------------------
def test_event_history_preserves_events(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", description="v1", source="src-a"))
    db.save_enrichment(
        sid,
        {
            "stars": 10,
            "forks": 2,
            "license": "MIT",
            "pushed_at": "2026-01-01T00:00:00Z",
            "topics": "ai",
        },
    )

    events = provenance.event_history(db, sid)
    assert len(events) >= 2  # initial insert + github enrich
    sources = {e["source"] for e in events}
    assert {"src-a", "github"} <= sources


def test_event_history_limit(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    for i in range(5):
        provenance.record(db, sid, "stars", f"src-{i}", i)
    events = provenance.event_history(db, sid, limit=2)
    assert len(events) == 2
    assert all(set(e) == {"source", "set_at", "fields"} for e in events)


# -- 4. backfill migration ---------------------------------------------------
def test_backfill_from_legacy_provenance(tmp_path):
    """A v1.1.0 DB (provenance only, no field_provenance) must backfill on open."""
    db_path = tmp_path / "legacy.sqlite3"
    con = sqlite3.connect(db_path)
    con.executescript(
        LEGACY_SERVERS_DDL
        + """
        CREATE TABLE provenance (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          server_id INTEGER,
          source TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO servers (name, publisher) VALUES ('Legacy', 'p');
        INSERT INTO provenance (server_id, source, payload_json)
          VALUES (1, 'src-a', '{"description":"old-desc","stars":"5"}');
    """
    )
    con.commit()
    con.close()

    db = Catalog(db_path)
    fields = {(f["field_name"], f["source"]): f["value"] for f in provenance.field_history(db, 1)}
    assert fields[("description", "src-a")] == "old-desc"
    assert fields[("stars", "src-a")] == "5"

    # audit trail kept intact
    events = provenance.event_history(db, 1)
    assert events and events[0]["source"] == "src-a"


def test_backfill_is_idempotent(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    provenance.record(db, sid, "description", "src-b", "beta")

    db2 = Catalog(tmp_path / "db.sqlite3")  # reopen -> must not duplicate
    fields = provenance.field_history(db2, sid, "description")
    assert len(fields) == 2  # src-a + src-b


def test_timestamp_column_detection(tmp_path):
    con = sqlite3.connect(tmp_path / "empty.sqlite3")
    con.row_factory = sqlite3.Row
    try:
        assert _timestamp_column(con) == "rowid"
        con.execute("CREATE TABLE provenance (id INTEGER PRIMARY KEY, set_at TEXT)")
        assert _timestamp_column(con) == "set_at"
        con.execute("CREATE TABLE provenance_v1 (id INTEGER PRIMARY KEY, created_at TEXT)")
        con.execute("DROP TABLE provenance")
        assert _timestamp_column(con) == "rowid"
    finally:
        con.close()


# -- 5. backward compatibility ----------------------------------------------
def test_history_alias_still_works(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    assert provenance.history(db, sid) == provenance.field_history(db, sid)


def test_conflicts_alias_still_works(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", description="a", source="src-a"))
    provenance.record(db, sid, "description", "src-b", "b")
    assert "description" in provenance.conflicts(db, sid)
    assert provenance.conflicts(db, sid) == provenance.field_conflicts(db, sid)


# -- 6. failure tolerance ----------------------------------------------------
class _BrokenCatalog:
    def connect(self):
        raise RuntimeError("boom")


def test_record_swallows_write_errors():
    provenance.record(_BrokenCatalog(), 1, "stars", "src", 1)  # must not raise


def test_record_row_swallows_write_errors():
    provenance.record_row(_BrokenCatalog(), 1, "src", {"stars": 1})  # must not raise


def test_record_row_empty_payload_is_noop(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    before = len(provenance.event_history(db, sid))
    provenance.record_row(db, sid, "src-b", {})
    assert len(provenance.event_history(db, sid)) == before


def test_event_history_tolerates_bad_payloads(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(Server(name="X", publisher="p", source="src-a"))
    with db.connect() as con:
        for source, payload in (("bad", "not-json"), ("list", "[1, 2]"), ("null", "")):
            con.execute(
                "INSERT INTO provenance (server_id, source, payload_json) VALUES (?,?,?)",
                (sid, source, payload),
            )
    events = {e["source"]: e for e in provenance.event_history(db, sid, limit=10)}
    assert events["bad"]["fields"] == {}
    assert events["list"]["fields"] == {}
    assert events["null"]["fields"] == {}
