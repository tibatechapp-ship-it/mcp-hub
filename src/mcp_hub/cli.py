from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    __version__,
    alerts,
    doctor,
    exporters,
    github_enrich,
    registry_api,
    saved_searches,
    scheduler,
    taxonomy,
)
from .database import Catalog
from .models import Server


def _db(args) -> Catalog:
    return Catalog(getattr(args, "db", None) or "mcp_hub.sqlite3")


def cmd_stats(args):
    print(json.dumps(_db(args).stats(), ensure_ascii=False, indent=2))


def cmd_doctor(args):
    res = doctor.run(_db(args))
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_sync_registry(args):
    db = _db(args)
    servers = registry_api.fetch_servers(limit=int(args.limit), cursor=args.cursor)

    def prog(i, total):
        print(f"progress: {i}/{total}", file=sys.stderr)

    res = db.bulk_add(servers, progress_callback=prog)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_import_text(args):
    db = _db(args)
    path = Path(args.file)
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 2
    servers = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # name|publisher|description|repo_url
        parts = [p.strip() for p in line.split("|")]
        name = parts[0] if parts else ""
        publisher = parts[1] if len(parts) > 1 else ""
        desc = parts[2] if len(parts) > 2 else ""
        repo = parts[3] if len(parts) > 3 else ""
        servers.append(
            Server(
                name=name,
                publisher=publisher,
                description=desc,
                repository_url=repo,
                source=args.source or "text",
            )
        )
    res = db.bulk_add(servers)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args):
    db = _db(args)
    rows = (
        db.search_fts(args.query, limit=int(args.limit))
        if args.fts
        else db.search(args.query, limit=int(args.limit))
    )
    print(json.dumps({"count": len(rows), "results": rows}, ensure_ascii=False, indent=2))


def cmd_enrich_github(args):
    db = _db(args)
    res = github_enrich.enrich_catalog(db, limit=int(args.limit), token=args.token)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_classify(args):
    db = _db(args)
    res = taxonomy.classify_catalog(db, overwrite=bool(args.overwrite))
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_export(args):
    db = _db(args)
    fmt = args.format
    out = Path(args.output)
    if fmt == "json":
        exporters.export_json(db, out)
    elif fmt == "csv":
        exporters.export_csv(db, out)
    elif fmt == "markdown" or fmt == "md":
        exporters.export_markdown(db, out)
    else:
        print(f"unknown format: {fmt}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "format": fmt, "output": str(out)}, ensure_ascii=False))
    return 0


def cmd_save_search(args):
    saved_searches.save(_db(args), args.name, args.query)
    print(json.dumps({"ok": True, "name": args.name}, ensure_ascii=False))


def cmd_list_searches(args):
    print(json.dumps(saved_searches.list_all(_db(args)), ensure_ascii=False, indent=2))


def cmd_run_search(args):
    try:
        res = saved_searches.run(_db(args), args.name)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_run_all_searches(args):
    res = scheduler.run_all(_db(args))
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_export_categories(args):
    data = _db(args).export_custom_categories_json()
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": args.output}, ensure_ascii=False))


def cmd_import_categories(args):
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    res = _db(args).import_custom_categories_json(payload, replace=bool(args.replace))
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_add_alert(args):
    alert_id = alerts.add_alert(_db(args), args.channel, args.target, args.query or "")
    print(json.dumps({"id": alert_id}, ensure_ascii=False))


def cmd_list_alerts(args):
    print(json.dumps(alerts.list_alerts(_db(args)), ensure_ascii=False, indent=2))


def cmd_serve(args):
    try:
        import uvicorn
    except Exception as e:  # noqa: BLE001
        print(
            "uvicorn/fastapi not installed. Install with: pip install mcp-hub[api]", file=sys.stderr
        )
        print(str(e), file=sys.stderr)
        return 2
    from .api import create_app

    app = create_app(db_path=args.db, api_key=args.api_key)
    uvicorn.run(app, host=args.host, port=int(args.port))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcp-hub", description="Local-first MCP server catalog")
    p.add_argument("--db", default="mcp_hub.sqlite3", help="Path to SQLite database")
    p.add_argument("--version", action="version", version=f"mcp-hub {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats", help="Show catalog stats")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("doctor", help="Run diagnostics")
    s.add_argument("--json", action="store_true", help="Machine-readable output")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sync-registry", help="Sync from MCP registry")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--cursor", default=None)
    s.set_defaults(func=cmd_sync_registry)

    s = sub.add_parser("import-text", help="Import from pipe-delimited text file")
    s.add_argument("file")
    s.add_argument("--source", default="text")
    s.set_defaults(func=cmd_import_text)

    s = sub.add_parser("search", help="Search servers")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--fts", action="store_true")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("enrich-github", help="Enrich servers from GitHub")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--token", default=None)
    s.set_defaults(func=cmd_enrich_github)

    s = sub.add_parser("classify", help="Classify servers into categories")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(func=cmd_classify)

    s = sub.add_parser("export", help="Export catalog")
    s.add_argument("format", choices=["json", "csv", "markdown", "md"])
    s.add_argument("output")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("save-search", help="Save a named search")
    s.add_argument("name")
    s.add_argument("query")
    s.set_defaults(func=cmd_save_search)

    s = sub.add_parser("list-searches", help="List saved searches")
    s.set_defaults(func=cmd_list_searches)

    s = sub.add_parser("run-search", help="Run a saved search")
    s.add_argument("name")
    s.set_defaults(func=cmd_run_search)

    s = sub.add_parser("run-all-searches", help="Run all saved searches")
    s.set_defaults(func=cmd_run_all_searches)

    s = sub.add_parser("export-categories", help="Export custom categories and overrides")
    s.add_argument("output")
    s.set_defaults(func=cmd_export_categories)

    s = sub.add_parser("import-categories", help="Import custom categories and overrides")
    s.add_argument("file")
    s.add_argument("--replace", action="store_true")
    s.set_defaults(func=cmd_import_categories)

    s = sub.add_parser("add-alert", help="Add alert channel")
    s.add_argument("channel", choices=["webhook"])
    s.add_argument("target")
    s.add_argument("--query", default="")
    s.set_defaults(func=cmd_add_alert)

    s = sub.add_parser("list-alerts", help="List alerts")
    s.set_defaults(func=cmd_list_alerts)

    s = sub.add_parser("serve", help="Run REST API + dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--api-key", default=None)
    s.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
        return int(rc or 0)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
