from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import (
    __version__,
    alerts,
    arabic_dsl,
    arflow,
    doctor,
    exporters,
    github_enrich,
    governance,
    incremental,
    provenance,
    registry_api,
    saved_searches,
    scheduler,
    taxonomy,
    watch,
)
from . import (
    apps as mcp_apps,
)
from . import (
    backup as backup_mod,
)
from . import (
    telemetry as telemetry_mod,
)
from .arabic_runtime import arabic_error_fields, normalize_query_text, resolve_lang
from .database import Catalog
from .models import Server


def _db(args) -> Catalog:
    return Catalog(getattr(args, "db", None) or "mcp_hub.sqlite3")


EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_BAD_INPUT = 2


def _lang(args) -> str:
    return resolve_lang(getattr(args, "lang", None))


def _query(value):
    """Normalize a free-text query only; never pass identifiers through this."""
    return normalize_query_text(value) if isinstance(value, str) else value


def _add_lang_option(parser) -> None:
    parser.add_argument(
        "--lang",
        choices=("en", "ar"),
        default=None,
        help="Diagnostic language (also settable with MCP_HUB_LANG)",
    )


def _emit_json(args, payload) -> int:
    """Print pretty JSON by default and compact JSON with --json."""
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK


def _fail_json(args, command: str, exc: BaseException, code: int) -> int:
    """Report a command failure on stderr, with a JSON object under --json."""
    payload = arabic_error_fields(command, exc, code, _lang(args))
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
    else:
        print(f"{command} failed: {exc}", file=sys.stderr)
        if payload.get("message_ar"):
            print(payload["message_ar"], file=sys.stderr)
    return code


def cmd_stats(args):
    print(json.dumps(_db(args).stats(), ensure_ascii=False, indent=2))


def cmd_doctor(args):
    res = doctor.run(_db(args))
    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_telemetry_status(args):
    _ = args
    mode = os.environ.get(telemetry_mod.ENV_TELEMETRY, "sqlite").strip().lower() or "sqlite"
    print(
        json.dumps(
            {
                "configured": mode,
                "log_tool_io": telemetry_mod.log_tool_io_enabled(),
                "env": telemetry_mod.ENV_TELEMETRY,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_app_resources(args):
    enabled = mcp_apps.enabled()
    runtime = mcp_apps.build_runtime(_db(args)) if enabled else mcp_apps.empty_runtime()
    print(
        json.dumps(
            {
                "enabled": enabled,
                "resources": [spec.to_dict() for spec in runtime.resource_specs],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_backup(args):
    try:
        res = backup_mod.backup(args.db, args.destination)
    except backup_mod.BackupError as e:
        print(f"backup failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_restore(args):
    try:
        res = backup_mod.restore(args.source, args.db, force=args.force)
    except backup_mod.BackupError as e:
        print(f"restore failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_verify_backup(args):
    try:
        checksum = backup_mod.verify_backup(args.file)
    except backup_mod.BackupError as e:
        print(f"verify failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"file": args.file, "sha256": checksum, "status": "ok"}))
    return 0


def _progress(done: int, total: int) -> None:
    print(f"progress: {done}/{total}", file=sys.stderr)


def cmd_sync_registry(args):
    db = _db(args)
    servers = registry_api.fetch_servers(limit=int(args.limit), cursor=args.cursor)
    res = db.bulk_add(servers, progress_callback=_progress)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_sync_arabic(args):
    from .live_sync import DEFAULT_CACHE, DEFAULT_UPSTREAM_URL, refresh_from_upstream

    cache = Path(args.cache).expanduser() if args.cache else DEFAULT_CACHE
    upstream = args.upstream or os.environ.get("MCP_HUB_AR_UPSTREAM_URL", DEFAULT_UPSTREAM_URL)
    try:
        result = refresh_from_upstream(cache, upstream, force=True)
    except Exception as e:  # noqa: BLE001 - CLI reports the policy/network error
        print(f"arabic sync failed: {e}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "status": result.status,
                "source": result.source,
                "digest": result.digest,
                "cache": str(cache),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_route_task(args):
    from .catalog_tools import TOOL_SPECS
    from .semantic_router import route_task

    try:
        result = route_task(
            _query(args.task),
            specs=TOOL_SPECS,
            registry_path=args.registry,
            limit=args.limit,
            compact=args.compact,
        )
    except (OSError, ValueError) as e:
        print(f"route-task failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_description_smells(args):
    from .description_smells import audit_registry

    try:
        result = audit_registry(registry_path=args.registry, limit=args.limit)
    except (OSError, ValueError) as e:
        print(f"description-smells failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_policy_snapshot(args):
    from .policy_gateway import ConfigError, PolicyGateway

    _ = args
    try:
        result = PolicyGateway.from_env().snapshot()
    except (ConfigError, OSError, ValueError) as e:
        print(f"policy-snapshot failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_skill_list(args):
    from .skills import SkillRegistry

    registry = SkillRegistry(_db(args))
    return _emit_json(args, [item.summary() for item in registry.list()])


def cmd_skill_get(args):
    from .skills import SkillError, SkillRegistry

    try:
        package = SkillRegistry(_db(args)).get(args.name)
    except (KeyError, SkillError) as exc:
        print(f"skill-get failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            package.to_dict(include_files=not args.metadata_only), ensure_ascii=False, indent=2
        )
    )
    return 0


def cmd_skill_approve(args):
    from .skills import SkillRegistry

    name = args.name
    try:
        digest = SkillRegistry(_db(args)).approve(name, args.actor)
    except KeyError as exc:
        return _fail_json(args, "skill-approve", exc, EXIT_NOT_FOUND)
    except (ValueError, OSError) as exc:
        return _fail_json(args, "skill-approve", exc, EXIT_BAD_INPUT)
    return _emit_json(args, {"name": name, "sha256": digest, "approved": True})


def cmd_skill_revoke(args):
    from .skills import SkillRegistry

    name = args.name
    try:
        removed = SkillRegistry(_db(args)).revoke(name, args.actor)
    except (ValueError, OSError) as exc:
        return _fail_json(args, "skill-revoke", exc, EXIT_BAD_INPUT)
    return _emit_json(args, {"name": name, "approved": False, "removed": removed})


def cmd_task_get(args):
    from .tasks import TaskError, TaskStore

    try:
        result = TaskStore(_db(args)).get(args.task_id, args.tenant, args.session)
    except TaskError as exc:
        print(f"task-get failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_task_cancel(args):
    from .tasks import TaskError, TaskStore

    try:
        result = TaskStore(_db(args)).cancel(args.task_id, args.tenant, args.session, args.reason)
    except TaskError as exc:
        print(f"task-cancel failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_task_list(args):
    from .tasks import TaskError, TaskStore

    try:
        result = TaskStore(_db(args)).list(args.tenant, args.session, limit=args.limit)
    except TaskError as exc:
        return _fail_json(args, "task-list", exc, EXIT_BAD_INPUT)
    return _emit_json(args, result)


def cmd_task_approve(args):
    from .tasks import TaskError, TaskStore

    try:
        result = TaskStore(_db(args)).approve(
            args.task_id,
            args.tenant,
            args.session,
            actor=args.actor,
            reason=args.reason,
        )
    except TaskError as exc:
        code = EXIT_NOT_FOUND if "not found" in str(exc).lower() else EXIT_BAD_INPUT
        return _fail_json(args, "task-approve", exc, code)
    return _emit_json(args, result)


def cmd_persona_simulate(args):
    from .catalog_tools import build_tool_policy
    from .persona import PersonaSimulator
    from .tasks import TaskError, TaskStore

    try:
        result = PersonaSimulator(TaskStore(_db(args)), build_tool_policy()).run(
            args.scenario,
            args.tenant,
            args.session,
            task_id=args.task_id,
        )
    except (TaskError, ValueError) as exc:
        print(f"persona-simulate failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_query_catalog(args):
    from .catalog_query import query_catalog

    try:
        result = query_catalog(
            kind=args.kind,
            category=args.category,
            tag=args.tag,
            profile=args.profile,
            access_mode=args.access_mode,
            ready_only=args.ready_only,
            limit=args.limit,
            registry_path=args.registry,
            profiles_dir=args.profiles_dir,
        )
    except (OSError, ValueError) as e:
        print(f"query-catalog failed: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


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
    # No CLI token: passing secrets on the command line leaks them to `ps` and
    # shell history. github_enrich.enrich_catalog reads GITHUB_TOKEN itself.
    res = github_enrich.enrich_catalog(db, limit=int(args.limit))
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_classify(args):
    db = _db(args)
    res = taxonomy.classify_catalog(db, overwrite=bool(args.overwrite))
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_provenance(args):
    db = _db(args)
    if not db.get_server(args.server_id):
        print(
            json.dumps({"error": f"server {args.server_id} not found"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    if args.events:
        data = provenance.event_history(db, args.server_id, limit=int(args.limit))
    elif args.conflicts_only:
        data = provenance.field_conflicts(db, args.server_id)
    else:
        data = provenance.field_history(db, args.server_id, args.field)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


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


def cmd_arabic_dsl(args):
    """Run Arabic commands/pipelines through the unified MCP gateway."""
    try:
        context = arabic_dsl.DslContext(
            tenant_id=args.tenant,
            session_id=args.session,
            actor=args.actor,
            lang=_lang(args),
        )
        text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
        commands = arabic_dsl.parse(text, context)
        payload = arabic_dsl.execute(commands, _db(args), dry_run=args.dry_run)
    except arabic_dsl.DslError as exc:
        return _fail_json(args, "arabic-dsl", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "arabic-dsl", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def _arflow_overrides(args) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pair in getattr(args, "var", None) or []:
        name, separator, value = pair.partition("=")
        if not separator or not name.strip():
            raise arflow.ArflowError("invalid --var, expected name=value")
        overrides[name.strip()] = value
    return overrides


def _arflow_context(args) -> arabic_dsl.DslContext:
    return arabic_dsl.DslContext(
        tenant_id=args.tenant,
        session_id=args.session,
        actor=args.actor,
        lang=_lang(args),
    )


def cmd_arflow_watch(args):
    """Watch a playbook and rerun only steps affected by linked files."""
    try:
        if getattr(args, "once", False) and int(args.max_cycles) != 1:
            raise watch.WatchError("--once cannot be combined with --max-cycles > 1")
        payload = watch.run_watch(
            _db(args),
            playbook=args.file,
            watch_files=tuple(getattr(args, "watch_file", None) or []),
            context=_arflow_context(args),
            overrides=_arflow_overrides(args),
            max_cycles=int(args.max_cycles),
            interval=float(args.interval),
            settle=float(args.settle),
            reason=args.reason,
        )
    except watch.WatchError as exc:
        return _fail_json(args, "arflow-watch", exc, exc.code)
    except arabic_dsl.DslError as exc:
        return _fail_json(args, "arflow-watch", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "arflow-watch", exc, EXIT_NOT_FOUND)

    if _lang(args) == "ar" and payload.get("status") != "ok":
        if payload.get("toctou_mismatch"):
            prefix = "عدم تطابق البصمة أثناء المراقبة"
        else:
            prefix = "فشلت مراقبة الخطة"
        payload["message_ar"] = f"{prefix}: {payload.get('stop_reason', 'failed')}"
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_arflow(args):
    """Compile, check and run ``.arflow`` playbooks over the Arabic DSL."""
    try:
        text = Path(args.file).read_text(encoding="utf-8")
        overrides = _arflow_overrides(args)
        context = _arflow_context(args)
        if args.arflow_cmd == "check":
            payload = arflow.check(
                text, name=Path(args.file).stem, context=context, overrides=overrides
            )
            if payload["exit_code"] != EXIT_OK:
                first = payload["issues"][0]["message"] if payload["issues"] else "check failed"
                exc = arflow.ArflowError(first)
                fields = arabic_error_fields("arflow-check", exc, EXIT_BAD_INPUT, _lang(args))
                fields["issues"] = payload["issues"]
                if getattr(args, "json", False):
                    print(
                        json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                        file=sys.stderr,
                    )
                else:
                    print(f"arflow-check failed: {first}", file=sys.stderr)
                    if fields.get("message_ar"):
                        print(fields["message_ar"], file=sys.stderr)
                return EXIT_BAD_INPUT
            _emit_json(args, payload)
            return EXIT_OK

        compiled = arflow.build(
            text, name=Path(args.file).stem, context=context, overrides=overrides
        )
        if not compiled.ok:
            first = compiled.issues[0].message if compiled.issues else "playbook invalid"
            raise arflow.ArflowError(first)
        use_incremental = (
            getattr(args, "arflow_cmd", None) in {"run", "test"}
            and not getattr(args, "dry_run", False)
            and (
                getattr(args, "incremental", False)
                or getattr(args, "changed_only", False)
                or getattr(args, "force_full", False)
            )
        )
        if use_incremental:
            payload = incremental.run_incremental(
                _db(args),
                compiled,
                incremental=True,
                force_full=bool(getattr(args, "force_full", False)),
            )
        else:
            payload = arflow.run(_db(args), compiled, dry_run=args.dry_run)
    except arflow.ArflowError as exc:
        return _fail_json(args, "arflow", exc, exc.code)
    except arabic_dsl.DslError as exc:
        return _fail_json(args, "arflow", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "arflow", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_governance_init(args):
    """Bind a repository to a tenant, playbook and audit DB."""
    try:
        payload = governance.init_project(
            governance.find_repo_root(),
            tenant=args.tenant,
            playbook=args.playbook,
            audit_db=args.audit_db,
            session=args.session,
            actor=args.actor,
            force=args.force,
        )
    except governance.GovernanceError as exc:
        return _fail_json(args, "governance-init", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "governance-init", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_governance_show(args):
    """Show the local governance binding."""
    try:
        payload = governance.show_project(governance.find_repo_root())
    except governance.GovernanceError as exc:
        return _fail_json(args, "governance-show", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "governance-show", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_governance_hooks_install(args):
    """Install managed global governance hooks."""
    try:
        payload = governance.install_hooks(governance.find_repo_root())
    except governance.GovernanceError as exc:
        return _fail_json(args, "governance-hooks-install", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "governance-hooks-install", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_governance_hooks_check(args):
    """Check the managed global governance hooks."""
    try:
        payload = governance.check_hooks(governance.find_repo_root())
    except governance.GovernanceError as exc:
        return _fail_json(args, "governance-hooks-check", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "governance-hooks-check", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_governance_hooks_uninstall(args):
    """Remove managed global governance hooks safely."""
    try:
        payload = governance.uninstall_hooks(governance.find_repo_root())
    except governance.GovernanceError as exc:
        return _fail_json(args, "governance-hooks-uninstall", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "governance-hooks-uninstall", exc, EXIT_NOT_FOUND)
    _emit_json(args, payload)
    return int(payload["exit_code"])


def cmd_governance_hook(args):
    """Run the bounded incremental gate for one Git hook event."""
    try:
        payload = governance.run_hook(args.event, lang=_lang(args))
    except governance.GovernanceError as exc:
        return _fail_json(args, "governance-hook", exc, exc.code)
    except Exception as exc:  # noqa: BLE001 - CLI failures stay structured
        return _fail_json(args, "governance-hook", exc, EXIT_NOT_FOUND)
    if _lang(args) == "ar" and payload.get("status") == "failed":
        payload["message_ar"] = f"فشل فحص الحوكمة: {payload.get('reason', 'runtime')}"
    _emit_json(args, payload)
    return int(payload["exit_code"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcp-hub", description="Local-first MCP server catalog")
    p.add_argument("--db", default="mcp_hub.sqlite3", help="Path to SQLite database")
    p.add_argument("--version", action="version", version=f"mcp-hub {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats", help="Show catalog stats")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("backup", help="Consistent snapshot of the database")
    s.add_argument("destination", help="Path for the backup file")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore", help="Replace the database from a verified backup")
    s.add_argument("source", help="Backup file created by `mcp-hub backup`")
    s.add_argument("--force", action="store_true", help="Replace an existing database")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("verify-backup", help="Check a backup against its checksum")
    s.add_argument("file")
    s.set_defaults(func=cmd_verify_backup)

    s = sub.add_parser("doctor", help="Run diagnostics")
    s.add_argument("--json", action="store_true", help="Machine-readable output")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sync-registry", help="Sync from MCP registry")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--cursor", default=None)
    s.set_defaults(func=cmd_sync_registry)

    s = sub.add_parser("sync-arabic", help="Refresh Arabic registry from upstream only")
    s.add_argument("--upstream", default=None, help="Exact upstream default-branch registry URL")
    s.add_argument("--cache", default=None, help="Arabic runtime cache file")
    s.set_defaults(func=cmd_sync_arabic)

    s = sub.add_parser("query-catalog", help="Query the bundled capability catalog")
    s.add_argument("--kind", choices=["all", "server", "tool"], default="all")
    s.add_argument("--category", default=None, help="Filter by server category")
    s.add_argument("--tag", default=None, help="Filter by capability tag")
    s.add_argument("--profile", default=None, help="Filter by task profile id")
    s.add_argument(
        "--access-mode",
        choices=["read-only", "read-write"],
        default=None,
        help="Filter by access mode",
    )
    s.add_argument("--ready-only", action="store_true", help="Only show runnable entries")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--registry", default=None, help="Override registry JSON path")
    s.add_argument("--profiles-dir", default=None, help="Override task profiles directory")
    s.set_defaults(func=cmd_query_catalog)

    s = sub.add_parser("route-task", help="Rank tools for an Arabic or English task")
    s.add_argument("task")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--compact", action="store_true", help="Omit schemas and shorten descriptions")
    s.add_argument("--registry", default=None, help="Override registry JSON path")
    s.set_defaults(func=cmd_route_task)

    s = sub.add_parser("description-smells", help="Audit descriptions with six stable codes")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--registry", default=None, help="Override registry JSON path")
    s.set_defaults(func=cmd_description_smells)

    s = sub.add_parser("policy-snapshot", help="Show the active tool policy safely")
    s.set_defaults(func=cmd_policy_snapshot)

    s = sub.add_parser("skill-list", help="List validated read-only skill packages")
    s.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(s)
    s.set_defaults(func=cmd_skill_list)

    s = sub.add_parser("skill-get", help="Show a validated skill package")
    s.add_argument("name")
    s.add_argument("--metadata-only", action="store_true", help="Omit file bodies")
    s.set_defaults(func=cmd_skill_get)

    s = sub.add_parser("skill-approve", help="Pin the current skill digest as approved")
    s.add_argument("name")
    s.add_argument("--actor", default="local")
    s.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(s)
    s.set_defaults(func=cmd_skill_approve)

    s = sub.add_parser("skill-revoke", help="Remove a stored skill approval")
    s.add_argument("name")
    s.add_argument("--actor", default="local")
    s.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(s)
    s.set_defaults(func=cmd_skill_revoke)

    s = sub.add_parser("task-get", help="Read a scoped durable task")
    s.add_argument("task_id")
    s.add_argument("--tenant", required=True)
    s.add_argument("--session", required=True)
    s.set_defaults(func=cmd_task_get)

    s = sub.add_parser("task-cancel", help="Cancel a scoped durable task")
    s.add_argument("task_id")
    s.add_argument("--tenant", required=True)
    s.add_argument("--session", required=True)
    s.add_argument("--reason", default="cancelled by caller")
    s.set_defaults(func=cmd_task_cancel)

    s = sub.add_parser("task-list", help="List scoped durable tasks")
    s.add_argument("--tenant", required=True)
    s.add_argument("--session", default=None)
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(s)
    s.set_defaults(func=cmd_task_list)

    s = sub.add_parser("task-approve", help="Approve a task awaiting approval")
    s.add_argument("task_id")
    s.add_argument("--tenant", required=True)
    s.add_argument("--session", required=True)
    s.add_argument("--actor", default="local")
    s.add_argument("--reason", default="approved by caller")
    s.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(s)
    s.set_defaults(func=cmd_task_approve)

    s = sub.add_parser("persona-simulate", help="Replay a local deterministic tool chain")
    s.add_argument(
        "scenario", choices=("safe_chain", "retry_then_success", "secret_leak", "permission_bypass")
    )
    s.add_argument("--tenant", required=True)
    s.add_argument("--session", required=True)
    s.add_argument("--task-id", default=None)
    s.set_defaults(func=cmd_persona_simulate)

    s = sub.add_parser("telemetry-status", help="Show telemetry configuration safely")
    s.set_defaults(func=cmd_telemetry_status)

    s = sub.add_parser("app-resources", help="List available MCP App UI resources")
    s.set_defaults(func=cmd_app_resources)

    s = sub.add_parser("import-text", help="Import from pipe-delimited text file")
    s.add_argument("file")
    s.add_argument("--source", default="text")
    s.set_defaults(func=cmd_import_text)

    s = sub.add_parser("search", help="Search servers")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--fts", action="store_true")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser(
        "enrich-github", help="Enrich servers from GitHub (uses GITHUB_TOKEN from the environment)"
    )
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_enrich_github)

    s = sub.add_parser("classify", help="Classify servers into categories")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(func=cmd_classify)

    s = sub.add_parser("provenance", help="Show provenance (by field or by event)")
    s.add_argument("server_id", type=int)
    s.add_argument("--field", default=None, help="Filter to a single field (e.g. 'description')")
    s.add_argument(
        "--conflicts-only",
        action="store_true",
        help="Only show fields where sources disagree",
    )
    s.add_argument(
        "--events",
        action="store_true",
        help="Show the audit trail (event history) instead of fields",
    )
    s.add_argument("--limit", type=int, default=50, help="Event history limit (only with --events)")
    s.set_defaults(func=cmd_provenance)

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

    s = sub.add_parser(
        "arabic-dsl", help="Run Arabic commands/pipelines over the unified MCP gateway"
    )
    source = s.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--text", help="One Arabic command or a pipeline separated by ; or newlines"
    )
    source.add_argument("--file", help="UTF-8 file containing one Arabic command per line")
    s.add_argument("--dry-run", action="store_true", help="Show the translated plan only")
    s.add_argument("--tenant", default="local", help="Tenant id passed verbatim to tasks")
    s.add_argument("--session", default="cli", help="Session id passed verbatim to tasks")
    s.add_argument("--actor", default="local", help="Actor passed verbatim to approvals")
    s.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(s)
    s.set_defaults(func=cmd_arabic_dsl)

    arflow_parser = sub.add_parser(
        "arflow", help="Arabic .arflow playbooks: check, run, incremental test and watch"
    )
    arflow_sub = arflow_parser.add_subparsers(dest="arflow_cmd", required=True)

    def add_arflow_options(parser: argparse.ArgumentParser, *, testing: bool = False) -> None:
        parser.add_argument("--file", required=True, help="UTF-8 .arflow playbook")
        parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE")
        parser.add_argument("--dry-run", action="store_true", help="Show the plan only")
        parser.add_argument("--tenant", default="local", help="Tenant id passed verbatim")
        parser.add_argument("--session", default="cli", help="Session id passed verbatim")
        parser.add_argument("--actor", default="local", help="Actor passed verbatim to approvals")
        parser.add_argument("--json", action="store_true", help="Emit compact JSON")
        if testing:
            parser.add_argument(
                "--incremental",
                action=argparse.BooleanOptionalAction,
                default=True,
                help="Reuse unchanged prior steps (default: true)",
            )
        else:
            parser.add_argument(
                "--incremental", action="store_true", help="Reuse unchanged prior steps"
            )
        parser.add_argument(
            "--changed-only",
            action="store_true",
            help="Alias for incremental adoption with fail-fast re-run after changes",
        )
        parser.add_argument(
            "--force-full", action="store_true", help="Ignore stored results and re-run all steps"
        )
        _add_lang_option(parser)
        parser.set_defaults(func=cmd_arflow)

    check_parser = arflow_sub.add_parser("check", help="Static check of a playbook (no handlers)")
    add_arflow_options(check_parser)

    run_parser = arflow_sub.add_parser("run", help="Run a playbook over the unified MCP gateway")
    add_arflow_options(run_parser)

    test_parser = arflow_sub.add_parser(
        "test", help="Incremental Arabic test run with expectations and audit"
    )
    add_arflow_options(test_parser, testing=True)

    watch_parser = arflow_sub.add_parser(
        "watch", help="Bounded watch over a playbook and its linked files"
    )
    watch_parser.add_argument("--file", required=True, help="UTF-8 .arflow playbook")
    watch_parser.add_argument(
        "--watch-file",
        action="append",
        default=[],
        dest="watch_file",
        metavar="PATH",
        help="Extra linked file relative to the playbook directory (repeatable)",
    )
    watch_parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE")
    watch_parser.add_argument("--tenant", default="local", help="Tenant id passed verbatim")
    watch_parser.add_argument("--session", default="cli", help="Session id passed verbatim")
    watch_parser.add_argument("--actor", default="local", help="Actor passed verbatim")
    watch_parser.add_argument("--reason", default="arflow watch", help="Audit reason verbatim")
    watch_parser.add_argument("--once", action="store_true", help="Run exactly one cycle")
    watch_parser.add_argument("--max-cycles", type=int, default=1, help="Upper bound (1-1000)")
    watch_parser.add_argument(
        "--interval", type=float, default=1.0, help="Seconds between cycles (0-60)"
    )
    watch_parser.add_argument(
        "--settle", type=float, default=0.1, help="Seconds to settle after a change (0-10)"
    )
    watch_parser.add_argument("--json", action="store_true", help="Emit compact JSON")
    _add_lang_option(watch_parser)
    watch_parser.set_defaults(func=cmd_arflow_watch)

    governance_parser = sub.add_parser(
        "governance", help="Bind repositories and manage global governance hooks"
    )
    governance_sub = governance_parser.add_subparsers(dest="governance_cmd", required=True)

    def add_governance_options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--json", action="store_true", help="Emit compact JSON")
        _add_lang_option(parser)

    init_parser = governance_sub.add_parser("init", help="Bind the current repository")
    init_parser.add_argument("--tenant", required=True, help="Tenant id passed verbatim")
    init_parser.add_argument("--playbook", default=".arflow/security.arflow")
    init_parser.add_argument("--audit-db", default=".mcp-hub-audit.sqlite3", dest="audit_db")
    init_parser.add_argument("--session", default="git")
    init_parser.add_argument("--actor", default="git")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing binding")
    add_governance_options(init_parser)
    init_parser.set_defaults(func=cmd_governance_init)

    show_parser = governance_sub.add_parser("show", help="Show the current binding")
    add_governance_options(show_parser)
    show_parser.set_defaults(func=cmd_governance_show)

    hooks_parser = governance_sub.add_parser("hooks", help="Manage global Git hooks")
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_cmd", required=True)

    install_parser = hooks_sub.add_parser("install", help="Install managed global hooks")
    add_governance_options(install_parser)
    install_parser.set_defaults(func=cmd_governance_hooks_install)

    check_parser = hooks_sub.add_parser("check", help="Check managed global hooks")
    add_governance_options(check_parser)
    check_parser.set_defaults(func=cmd_governance_hooks_check)

    uninstall_parser = hooks_sub.add_parser("uninstall", help="Safely remove managed hooks")
    add_governance_options(uninstall_parser)
    uninstall_parser.set_defaults(func=cmd_governance_hooks_uninstall)

    hook_parser = governance_sub.add_parser("hook", help="Run one bounded Git hook gate")
    hook_parser.add_argument("--event", choices=("pre-commit", "pre-push"), required=True)
    add_governance_options(hook_parser)
    hook_parser.set_defaults(func=cmd_governance_hook)

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
