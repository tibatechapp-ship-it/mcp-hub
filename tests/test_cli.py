from __future__ import annotations

import json

from mcp_hub import provenance
from mcp_hub.cli import build_parser, main
from mcp_hub.database import Catalog
from mcp_hub.models import Server


def test_parser_provenance_defaults():
    args = build_parser().parse_args(["provenance", "7"])
    assert args.server_id == 7
    assert args.field is None
    assert args.events is False
    assert args.conflicts_only is False
    assert args.limit == 50


def test_cli_provenance_modes(tmp_path, capsys):
    db_path = tmp_path / "db.sqlite3"
    db = Catalog(db_path)
    sid = db.add_server(Server(name="X", publisher="p", description="a", source="s"))
    provenance.record(db, sid, "description", "s2", "b")

    assert main(["--db", str(db_path), "provenance", str(sid)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {r["field_name"] for r in out} >= {"description", "name"}

    assert main(["--db", str(db_path), "provenance", str(sid), "--field", "description"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out and all(r["field_name"] == "description" for r in out)

    assert main(["--db", str(db_path), "provenance", str(sid), "--conflicts-only"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "description" in out

    assert main(["--db", str(db_path), "provenance", str(sid), "--events", "--limit", "1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 1


def test_cli_provenance_missing_server(tmp_path, capsys):
    db_path = tmp_path / "db.sqlite3"
    Catalog(db_path)
    assert main(["--db", str(db_path), "provenance", "999"]) == 1
    assert "not found" in capsys.readouterr().err
