from __future__ import annotations

import json

from mcp_hub import doctor, exporters, saved_searches, taxonomy
from mcp_hub.database import Catalog
from mcp_hub.models import Server


def test_add_search_and_stats(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    sid = db.add_server(
        Server(
            name="pg-tool",
            publisher="acme",
            description="PostgreSQL query runner",
            repository_url="https://github.com/acme/pg-tool",
            source="test",
        )
    )
    assert sid is not None
    # duplicate skip
    assert (
        db.add_server(
            Server(
                name="pg-tool",
                publisher="acme",
                description="PostgreSQL query runner",
                source="test",
            )
        )
        is None
    )
    stats = db.stats()
    assert stats["records"] == 1
    assert db.search("postgres")
    assert db.search_fts("postgres") or db.search("postgres")


def test_bulk_add(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    servers = [
        Server(name=f"S{i}", publisher="p", description=f"d{i}", source="t") for i in range(50)
    ]
    res = db.bulk_add(servers)
    assert res["new"] == 50
    res2 = db.bulk_add(servers)
    assert res2["skipped"] == 50


def test_classify_and_export(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="db", publisher="x", description="PostgreSQL tool", source="t"))
    out = taxonomy.classify_catalog(db)
    assert out["total"] == 1
    row = db.search("postgres")[0]
    assert "database" in row["categories"]

    j = tmp_path / "out.json"
    c = tmp_path / "out.csv"
    m = tmp_path / "out.md"
    exporters.export_json(db, j)
    exporters.export_csv(db, c)
    exporters.export_markdown(db, m)
    assert json.loads(j.read_text())
    assert c.exists() and m.exists()
    assert "PostgreSQL" in m.read_text()


def test_saved_search(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="pg", publisher="a", description="PostgreSQL", source="t"))
    saved_searches.save(db, "pg", "postgres")
    res = saved_searches.run(db, "pg")
    assert res["match_count"] == 1
    res2 = saved_searches.run(db, "pg")
    assert res2["new_count"] == 0


def test_categories_export_import(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    db.add_custom_category("customdb", "foobar,baz", "note")
    db.add_server(Server(name="x", publisher="p", description="y", source="t"))
    sid = db.search("x")[0]["id"]
    db.set_category_override(sid, "customdb")
    payload = db.export_custom_categories_json()
    db2 = Catalog(tmp_path / "db2.sqlite3")
    res = db2.import_custom_categories_json(payload)
    assert res["categories"] == 1


def test_doctor(tmp_path):
    db = Catalog(tmp_path / "db.sqlite3")
    res = doctor.run(db)
    assert res["db_ok"] is True
    assert "version" in res


def test_fts_is_not_silently_the_like_search(tmp_path):
    """`fts=True` must run FTS5, not fall back to LIKE on a swallowed error."""
    from mcp_hub.database import Catalog
    from mcp_hub.models import Server

    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="alpha", publisher="acme", description="alpha tool", source="t"))
    db.add_server(
        Server(name="alphabet", publisher="acme", description="alphabet soup", source="t")
    )

    assert [r["name"] for r in db.search_fts("alpha")] == ["alpha"]
    assert {r["name"] for r in db.search("alpha")} == {"alpha", "alphabet"}


def test_fts_prefix_query_matches_word_starts(tmp_path):
    from mcp_hub.database import Catalog
    from mcp_hub.models import Server

    db = Catalog(tmp_path / "db.sqlite3")
    db.add_server(Server(name="postgres tool", publisher="p", description="sql", source="t"))
    db.add_server(Server(name="redis tool", publisher="p", description="cache", source="t"))

    assert [r["name"] for r in db.search_fts("postgres")] == ["postgres tool"]
    assert db.search_fts("nosuchtoken") == []
