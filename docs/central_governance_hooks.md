# Global governance hooks

Phase 2 of central governance brings the same policy-gated `.arflow` path to
local Git hooks. A repository is first **bound** to a tenant, a playbook and a
SQLite audit DB; then global `pre-commit` / `pre-push` hooks are installed once
and reuse the Phase 9 incremental engine plus the Phase 10 linked-file
fingerprints. The pinned tool manifest, tool/resource specs, handler names,
default policies and read-only Apps surface do not change.

## Bind a repository

```sh
mcp-hub governance init \
  --tenant tayf-main \
  --playbook .arflow/security-check.arflow \
  --audit-db .mcp-hub-governance.sqlite3 \
  --session git --actor git
mcp-hub governance show --json --lang ar
```

This writes `.mcp-hub-governance.json` at the worktree root:

```json
{
  "version": 1,
  "tenant": "tayf-main",
  "playbook": ".arflow/security-check.arflow",
  "audit_db": ".mcp-hub-governance.sqlite3",
  "session": "git",
  "actor": "git"
}
```

Both paths must stay relative to the repository and cannot contain `..`,
absolute prefixes, backslashes or NUL bytes. Tenant, session and actor must be
`[A-Za-z0-9._:@-]{1,128}`; Arabic-Indic digits (`٠-٩` / `۰-۹`) are rejected while
the original text is otherwise preserved verbatim. Use `--force` to replace an
existing binding.

## Install, check and uninstall global hooks

```sh
mcp-hub governance hooks install --json --lang ar
mcp-hub governance hooks check --json --lang ar
mcp-hub governance hooks uninstall --json --lang ar
```

Install writes two managed scripts under
`~/.config/mcp-hub/hooks/` (`pre-commit` and `pre-push`), stores the previous
global `core.hooksPath` in `~/.config/mcp-hub/governance-global.json`, and points
Git's **global** `core.hooksPath` at that directory. It refuses to clobber a
foreign `core.hooksPath`; resolve or uninstall that setup first.

Each script contains a stable managed marker. Uninstall removes only scripts
with that marker, restores the previous `core.hooksPath` (or unsets it when
there was none), and leaves any unrecognized file in place. That makes
uninstall safe even if you added your own hook after installation.

For isolated tests or multiple toolchains, override the config root with
`MCP_HUB_GOVERNANCE_HOME=/path/to/config`. To test against a temporary Git
config instead of your real one, set `GIT_CONFIG_GLOBAL=/path/to/gitconfig`
before running the commands.

## What a hook does

For `pre-commit`, changed paths come from `git diff --cached --name-only -z`.
For `pre-push`, the upstream range is used, falling back to the last commit and
then staged/tracked files when no upstream exists.

The hook skips cleanly with exit `0` when:

- the current directory is not a Git repository (`not_a_git_repo`),
- the repository has no binding (`unbound_repository`),
- the bound playbook file is missing (`no_playbook`), or
- neither the playbook nor any linked file changed (`no_relevant_changes`).

Otherwise it compiles the playbook with the bound tenant/session/actor,
overlays linked-file SHA-256 digests onto step fingerprints, and calls
`incremental.run_incremental()`. Unchanged successful steps are adopted; a
linked-file change reruns only the steps that mention that file. Results are
recorded in the existing `arflow_incremental_audit` table with verbatim Arabic
text and identifiers. No new audit table is introduced.

Useful environment switches inside hooks:

| Variable | Meaning |
| --- | --- |
| `MCP_HUB_GOVERNANCE_JSON=1` | Pass `--json` to the hook runner |
| `MCP_HUB_GOVERNANCE_LANG=ar` | Pass `--lang ar` (or use `MCP_HUB_LANG`) |
| `MCP_HUB_BIN=/path/to/mcp-hub` | Executable used when installing scripts |
| `MCP_HUB_GOVERNANCE_HOME` | Override the global config/hook directory |

## Exit codes

`0` means success or a clean skip, `1` means a runtime/gate failure (including
a broken `hooks check` or failed playbook), and `2` means bad input (invalid ID,
bad path, unsupported event, or a refused install). Under `--json`, structured
errors go to stderr only with the same keys as the rest of the CLI.

## Termux notes

Termux has no `/tmp` write guarantee; keep test config roots under `$TMPDIR`,
for example:

```sh
export MCP_HUB_GOVERNANCE_HOME="$TMPDIR/mcp-hub-hooks"
export GIT_CONFIG_GLOBAL="$TMPDIR/gitconfig-global"
mcp-hub governance init --tenant termux-local --playbook .arflow/security-check.arflow
mcp-hub governance hooks install --json
```

Git must be installed (`pkg install git`) and the playbook must be committed or
staged before `pre-commit` can see it. If a hook blocks a commit because a
linked file changed, fix the playbook or run
`mcp-hub arflow test --file <playbook> --json --lang ar` to inspect the failure,
then commit again.

## Relationship to CI

Global hooks are a local convenience, not a replacement for
`governance-gate.yml`. Keep the reusable workflow and GitHub Ruleset gate as the
authoritative check; local hooks give the same feedback before the push.
