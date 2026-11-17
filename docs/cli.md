# CLI reference

Every command takes `--db PATH` (default `mcp_hub.sqlite3`) before the
subcommand. Commands print JSON on stdout; diagnostics go to stderr.

| Command | Purpose |
| --- | --- |
| `stats` | Record counts, sources, duplicate candidates, FTS state |
| `doctor` | Diagnostics: journal mode, page counts, vacuum advice, backup hint |
| `sync-registry [--limit N] [--cursor C]` | Pull from the MCP registry |
| `import-text FILE [--source S]` | Import `name\|publisher\|description\|repo_url` lines |
| `search QUERY [--limit N] [--fts]` | Search (LIKE by default, FTS5 with `--fts`) |
| `enrich-github [--limit N] [--token T]` | Add stars/forks/license/topics |
| `classify [--overwrite]` | Assign categories |
| `provenance ID [--field F] [--conflicts-only] [--events] [--limit N]` | Field history or audit trail |
| `export {json,csv,markdown,md} OUTPUT` | Export the catalog |
| `save-search NAME QUERY` / `list-searches` | Manage saved searches |
| `run-search NAME` / `run-all-searches` | Re-run and diff against last results |
| `export-categories OUT` / `import-categories FILE [--replace]` | Custom category rules |
| `add-alert webhook URL [--query Q]` / `list-alerts` | Alert channels |
| `backup DEST` / `verify-backup FILE` / `restore SRC [--force]` | Data safety |
| `serve [--host H] [--port P] [--api-key K]` | REST API + dashboard |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Requested record not found (e.g. `provenance 999`) |
| `2` | Bad input: missing file, refused overwrite, failed checksum, unknown format, missing web dependencies |

`main()` returns `0` on `BrokenPipeError` so `mcp-hub export json - | head` is safe.

## Examples

```bash
# import and search
mcp-hub --db cat.sqlite3 import-text servers.txt
mcp-hub --db cat.sqlite3 search "vector database" --fts --limit 5

# snapshot before an upgrade, verify it, restore it
mcp-hub --db cat.sqlite3 backup /backups/cat-$(date +%F).sqlite3
mcp-hub --db cat.sqlite3 verify-backup /backups/cat-2026-01-01.sqlite3
mcp-hub --db cat.sqlite3 restore /backups/cat-2026-01-01.sqlite3 --force
```
