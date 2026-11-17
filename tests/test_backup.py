"""Backup / restore and migration safety (v1.3.1 P2.1, P2.2)."""

from __future__ import annotations

import sqlite3

import pytest

from mcp_hub.backup import BackupError, backup, restore, verify_backup
from mcp_hub.database import Catalog
from mcp_hub.models import Server


def _seed(db_path) -> Catalog:
    catalog = Catalog(db_path)
    catalog.bulk_add(
        [
            Server(name="alpha", publisher="acme", description="first", source="t"),
            Server(name="beta", publisher="acme", description="second", source="t"),
        ]
    )
    return catalog


def test_backup_produces_a_readable_copy(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "backup.sqlite3"
    result = backup(db, dest)
    assert dest.exists()
    assert result["bytes"] == dest.stat().st_size
    assert result["sha256"] == verify_backup(dest)
    with sqlite3.connect(dest) as con:
        assert con.execute("SELECT COUNT(*) FROM servers").fetchone()[0] == 2


def test_backup_writes_a_sidecar(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    sidecar = tmp_path / "b.sqlite3.sha256"
    assert sidecar.exists()
    assert dest.name in sidecar.read_text()


def test_backup_refuses_to_overwrite(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    with pytest.raises(BackupError, match="refusing to overwrite"):
        backup(db, dest)


def test_backup_of_a_missing_database_fails(tmp_path):
    with pytest.raises(BackupError, match="database not found"):
        backup(tmp_path / "nope.sqlite3", tmp_path / "b.sqlite3")


def test_backup_works_while_the_database_is_open(tmp_path):
    """The online backup API must not need the writer to stop."""
    db = tmp_path / "cat.sqlite3"
    catalog = _seed(db)
    with catalog.connect() as con:
        con.execute(
            "INSERT INTO servers (name, publisher, description, source) VALUES ('x','y','z','t')"
        )
        result = backup(db, tmp_path / "live.sqlite3")
    assert result["bytes"] > 0
    with sqlite3.connect(tmp_path / "live.sqlite3") as con:
        assert con.execute("SELECT COUNT(*) FROM servers").fetchone()[0] >= 2


def test_verify_detects_tampering(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    with dest.open("ab") as handle:
        handle.write(b"\x00tampered")
    with pytest.raises(BackupError, match="checksum mismatch"):
        verify_backup(dest)


def test_verify_requires_a_sidecar(tmp_path):
    stray = tmp_path / "stray.sqlite3"
    stray.write_bytes(b"not-a-db")
    with pytest.raises(BackupError, match="sidecar missing"):
        verify_backup(stray)


def test_restore_round_trip(tmp_path):
    db = tmp_path / "cat.sqlite3"
    catalog = _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    catalog.bulk_add([Server(name="gamma", publisher="acme", description="third", source="t")])

    result = restore(dest, db, force=True)
    assert result["previous"]
    restored = Catalog(db)
    names = {row["name"] for row in restored.search("", limit=50)}
    assert names == {"alpha", "beta"}


def test_restore_keeps_the_previous_file(tmp_path):
    db = tmp_path / "cat.sqlite3"
    catalog = _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    catalog.bulk_add([Server(name="gamma", publisher="acme", description="third", source="t")])
    restore(dest, db, force=True)
    previous = tmp_path / "cat.sqlite3.pre-restore"
    assert previous.exists()
    with sqlite3.connect(previous) as con:
        assert con.execute("SELECT COUNT(*) FROM servers").fetchone()[0] == 3


def test_restore_requires_force_when_a_database_exists(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    with pytest.raises(BackupError, match="already exists"):
        restore(dest, db)


def test_restore_refuses_a_corrupt_backup(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    with dest.open("ab") as handle:
        handle.write(b"garbage")
    with pytest.raises(BackupError, match="checksum mismatch"):
        restore(dest, db, force=True)


def test_restore_leaves_the_target_untouched_on_a_bad_checksum(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    dest = tmp_path / "b.sqlite3"
    backup(db, dest)
    before = db.read_bytes()
    with dest.open("ab") as handle:
        handle.write(b"garbage")
    with pytest.raises(BackupError):
        restore(dest, db, force=True)
    assert db.read_bytes() == before


# --- migrations ------------------------------------------------------------


def test_migration_from_a_pre_provenance_database(tmp_path):
    """
    A database created by the 1.0 schema (no provenance table, no
    scheduler_runs.duration_ms, user_version=0) must migrate without data loss.
    """
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        """
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
            raw_name TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO servers (name, publisher, description, source)
        VALUES ('legacy-one', 'old-co', 'from 1.0', 'import');
        """
    )
    con.commit()
    con.close()

    catalog = Catalog(db)
    with catalog.connect() as c:
        names = [r["name"] for r in c.execute("SELECT name FROM servers").fetchall()]
        version = c.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "legacy-one" in names
    assert version >= 1
    assert "provenance" in tables


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "cat.sqlite3"
    _seed(db)
    Catalog(db)  # second open re-runs _init against an already-migrated file
    Catalog(db)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM servers").fetchone()[0] == 2


def test_doctor_reports_journal_and_pages(tmp_path):
    from mcp_hub import doctor

    db = tmp_path / "cat.sqlite3"
    catalog = _seed(db)
    result = doctor.run(catalog)
    assert result["status"] == "ok"
    assert result["journal_mode"] in {"delete", "wal", "truncate", "persist", "memory"}
    assert int(result["pages"]) > 0
    assert "backup" in str(result["backup_hint"])
