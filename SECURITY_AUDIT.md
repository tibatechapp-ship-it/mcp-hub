# Security audit — git history and supply chain

**Repository:** `tibatechapp-ship-it/mcp-hub`
**Audit date:** 2026-11-17
**Scope:** every commit reachable from `main` (33 commits, `599f04db` → `8510ba15`), 55 distinct files
**Method:** GitHub REST API (`/repos/{owner}/{repo}/commits`, per-commit file patches), regex sweep over every added/removed line plus a path check
**Performed by:** maintainers, as part of the 1.3.1 hardening work

## Why this document exists

A credential that was committed once and deleted later is still readable in the
git history and in every clone, fork and cached mirror. Rotating the secret is
the only real fix; deleting the file is not. This is a committed, reproducible
record of that check so the answer to "did anything leak?" does not depend on
someone's memory.

## What was checked

| Class | Pattern |
|---|---|
| GitHub tokens | `ghp_…`, `gho_…`, `ghu_…`, `ghs_…`, `ghr_…`, `github_pat_…` |
| AWS access keys | `AKIA…`, `ASIA…` + 16 chars |
| Private keys | `-----BEGIN … PRIVATE KEY` |
| Slack tokens | `xoxb-…`, `xoxp-…`, `xoxa-…`, `xoxr-…`, `xoxs-…` |
| Google API keys | `AIza…` (35 chars) |
| Credential assignments | `password` / `passwd` / `secret` / `api_key` / `access_token` / `auth_token` followed by an 8+ character literal |
| Sensitive paths | `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa`, `id_ed25519`, `*.sqlite3`, `*.db` |

## Result

**One file matched the sensitive-path check: `.env.example`. No secrets found in
any commit.**

`.env.example` is a template that ships with the repository by design. Every
assignment in it is empty — the file documents which variables exist, not their
values:

```
MCP_HUB_API_KEY=
GITHUB_TOKEN=
```

The credential-assignment pattern produced no hits, which confirms no real value
was ever committed under those names. Nothing needs rotation, and history does
not need rewriting.

## Current state of the surface

| Control | State |
|---|---|
| Secrets in git history | none |
| Secrets in CI | only `secrets.GITHUB_TOKEN` (ephemeral, workflow-scoped) and `secrets.PYPI_TOKEN`-free trusted publishing |
| Workflow permissions | scoped per job (`contents: read` by default) |
| Action pinning | every action pinned to a full commit SHA |
| Dependency bounds | explicit upper bounds in `pyproject.toml` |
| Image scan | Trivy, CRITICAL + HIGH with a fix, blocking |
| SAST | bandit (blocks HIGH) + semgrep (`p/python`, `p/security-audit`) |
| Dependency CVEs | pip-audit, blocking |
| Licence policy | pip-licenses, GPL/AGPL blocked |
| SBOM | SPDX JSON published with each release |
| Push protection | GitHub Secret Scanning — see the manual step below |

## Manual steps only the repository owner can complete

These require account-level access and are not reproducible from CI:

1. **Settings → Code security → Secret Scanning → Push protection: enable.**
   Blocks a commit containing a recognisable secret before it reaches the remote.
2. **Settings → Code security → Dependabot alerts + Dependabot security updates: enable.**
   `dependabot.yml` handles version updates; alerts cover advisories that land
   between runs.
3. **Settings → Code security → CodeQL: enable "Default" setup for Python.**
   Complements bandit/semgrep with dataflow analysis.
4. **Settings → Branches → protect `main`:** require the `test` and
   `security-scan` checks, require a pull request, disallow force pushes.
5. **Rotate `MCP_HUB_API_KEY` on a schedule** if the service is exposed beyond
   loopback — the key is a bearer token with no expiry.

## Re-running this audit

The sweep is a plain REST walk — no clone needed:

```bash
for sha in $(gh api "repos/OWNER/REPO/commits?per_page=100" --jq '.[].sha'); do
  gh api "repos/OWNER/REPO/commits/$sha" \
    --jq '.files[] | .filename, (.patch // "")'
done | grep -nEi 'gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|xox[bpras]-'
```

An empty result is a pass. Any hit: treat the value as compromised, rotate it
first, then decide whether history rewriting is worth the disruption.
