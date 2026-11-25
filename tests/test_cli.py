from __future__ import annotations

import argparse
import json
import sys

import pytest

from mcp_hub import cli, github_enrich, provenance, registry_api
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


# --- command surface (P4.1) -------------------------------------------------
# Every command is exercised end-to-end through main() with a temp database, so
# the CLI's real contract (exit codes, JSON on stdout, errors on stderr) is what
# gets asserted — not the internals of each module.


def _seed_text(tmp_path):
    src = tmp_path / "servers.txt"
    src.write_text(
        "# comment line\n"
        "\n"
        "alpha|acme|first server|https://example.com/a\n"
        "beta|acme|second server\n"
        "gamma\n",
        encoding="utf-8",
    )
    return src


def _imported(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    assert main(["--db", str(db_path), "import-text", str(_seed_text(tmp_path))]) == 0
    return db_path


def test_import_text_parses_pipes_and_skips_noise(tmp_path, capsys):
    db_path = _imported(tmp_path)
    out = json.loads(capsys.readouterr().out)
    assert out["new"] == 3

    assert main(["--db", str(db_path), "search", "alpha"]) == 0
    rows = json.loads(capsys.readouterr().out)["results"]
    assert rows[0]["publisher"] == "acme"
    assert rows[0]["repository_url"] == "https://example.com/a"

    # a bare name has no publisher/description — must not crash
    assert main(["--db", str(db_path), "search", "gamma"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_import_text_missing_file(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "db.sqlite3"), "import-text", "nope.txt"]) == 2
    assert "file not found" in capsys.readouterr().err


def test_stats_and_doctor(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()

    assert main(["--db", str(db_path), "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 3

    assert main(["--db", str(db_path), "doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["journal_mode"]

    assert main(["--db", str(db_path), "doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["journal_mode"]


def test_search_fts_flag(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()
    assert main(["--db", str(db_path), "search", "second", "--fts"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
    assert main(["--db", str(db_path), "search", "second", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1


def test_backup_verify_restore_roundtrip(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()
    dest = tmp_path / "snap.sqlite3"

    assert main(["--db", str(db_path), "backup", str(dest)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sha256"]

    # a second backup to the same path is refused, not silently overwritten
    assert main(["--db", str(db_path), "backup", str(dest)]) == 2
    assert "backup failed" in capsys.readouterr().err

    assert main(["--db", str(db_path), "verify-backup", str(dest)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"

    # restore refuses to clobber a live database without --force
    assert main(["--db", str(db_path), "restore", str(dest)]) == 2
    assert "restore failed" in capsys.readouterr().err

    assert main(["--db", str(db_path), "restore", str(dest), "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"]

    assert main(["--db", str(db_path), "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 3


def test_verify_backup_detects_tampering(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()
    dest = tmp_path / "snap.sqlite3"
    assert main(["--db", str(db_path), "backup", str(dest)]) == 0
    capsys.readouterr()
    dest.write_bytes(dest.read_bytes() + b"\x00")

    assert main(["--db", str(db_path), "verify-backup", str(dest)]) == 2
    assert "verify failed" in capsys.readouterr().err


def test_export_all_formats(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()
    for fmt in ("json", "csv", "md", "markdown"):
        out = tmp_path / f"catalog.{fmt}"
        assert main(["--db", str(db_path), "export", fmt, str(out)]) == 0
        assert out.exists() and out.stat().st_size > 0
        assert json.loads(capsys.readouterr().out)["ok"] is True

    # argparse blocks unknown formats, so hit the defensive branch directly
    args = argparse.Namespace(db=str(db_path), format="yaml", output=str(tmp_path / "x"))
    assert cli.cmd_export(args) == 2
    assert "unknown format" in capsys.readouterr().err


def test_saved_searches_roundtrip(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()

    assert main(["--db", str(db_path), "save-search", "alphas", "alpha"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "alphas"

    assert main(["--db", str(db_path), "list-searches"]) == 0
    assert [s["name"] for s in json.loads(capsys.readouterr().out)] == ["alphas"]

    assert main(["--db", str(db_path), "run-search", "alphas"]) == 0
    assert json.loads(capsys.readouterr().out)["new_count"] >= 0

    assert main(["--db", str(db_path), "run-search", "missing"]) == 2
    assert "missing" in capsys.readouterr().err

    assert main(["--db", str(db_path), "run-all-searches"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_custom_categories_roundtrip(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()
    dump = tmp_path / "cats.json"

    assert main(["--db", str(db_path), "export-categories", str(dump)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)

    assert main(["--db", str(db_path), "import-categories", str(dump), "--replace"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_alerts_roundtrip(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()

    assert main(["--db", str(db_path), "add-alert", "webhook", "https://example.com/hook"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] > 0

    assert main(["--db", str(db_path), "list-alerts"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["target"] == "https://example.com/hook"


def test_sync_registry_prints_progress(tmp_path, capsys, monkeypatch):
    def fake_fetch(limit, cursor=None):
        return [Server(name="delta", publisher="p", description="d", source="registry")]

    monkeypatch.setattr(registry_api, "fetch_servers", fake_fetch)
    db_path = tmp_path / "db.sqlite3"

    assert main(["--db", str(db_path), "sync-registry", "--limit", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1


def test_progress_callback_writes_to_stderr(capsys):
    cli._progress(2, 5)
    assert capsys.readouterr().err.strip() == "progress: 2/5"


def test_enrich_github_uses_environment_token_and_rejects_cli_token(tmp_path, capsys, monkeypatch):
    seen = {}

    def fake_enrich(db, limit=100, token=None):
        seen["limit"] = limit
        seen["token"] = token
        return {"enriched": 0}

    monkeypatch.setattr(github_enrich, "enrich_catalog", fake_enrich)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    db_path = tmp_path / "db.sqlite3"

    assert main(["--db", str(db_path), "enrich-github", "--limit", "3"]) == 0
    assert seen == {"limit": 3, "token": None}
    assert capsys.readouterr().err == ""

    with pytest.raises(SystemExit):
        main(["--db", str(db_path), "enrich-github", "--token", "cli-token"])


def test_classify_offline(tmp_path, capsys):
    db_path = _imported(tmp_path)
    capsys.readouterr()
    assert main(["--db", str(db_path), "classify"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)
    assert main(["--db", str(db_path), "classify", "--overwrite"]) == 0


def test_serve_without_uvicorn(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert main(["--db", str(tmp_path / "db.sqlite3"), "serve"]) == 2
    assert "uvicorn/fastapi not installed" in capsys.readouterr().err


def test_serve_starts_uvicorn(tmp_path, capsys, monkeypatch):
    calls = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, host, port):
            calls.update(host=host, port=port, has_app=app is not None)

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)
    assert main(["--db", str(tmp_path / "db.sqlite3"), "serve", "--port", "8899"]) == 0
    assert calls == {"host": "127.0.0.1", "port": 8899, "has_app": True}


def test_broken_pipe_is_not_an_error(tmp_path, monkeypatch):
    def boom(args):
        raise BrokenPipeError

    monkeypatch.setattr(cli, "cmd_stats", boom)
    assert main(["--db", str(tmp_path / "db.sqlite3"), "stats"]) == 0


def test_default_database_path():
    assert cli._db(argparse.Namespace()).path.name == "mcp_hub.sqlite3"


def test_version_flag_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "mcp-hub" in output
    assert "1.8.0" in output
