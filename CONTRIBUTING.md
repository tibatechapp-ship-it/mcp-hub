# Contributing

Thanks for taking the time. Small, focused pull requests get merged fastest.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,api,mcp-server]"
```

## Before you push

```bash
ruff check .
ruff format --check .
mypy                                  # strict, security core only
pytest --cov=mcp_hub                  # floor: 88%
```

CI runs the suite on Python 3.10, 3.11, 3.12 and 3.13 (3.14 is advisory) and
fails on any of the above.

`pre-commit` is optional but recommended:

```bash
pip install pre-commit && pre-commit install
```

## Conventions

- **Tests first for behaviour changes.** A bug fix without a failing-then-passing
  test is a bug that comes back.
- **No new runtime dependency** without a discussion — the core is pure Python +
  SQLite, and the API stack is optional. Adding a package to `dependencies`
  affects every install; prefer a new extra.
- **Security code is typed.** `auth.py`, `headers.py`, `ratelimit.py`,
  `payload_limit.py`, `safe_fetch.py`, `redact.py` must pass `mypy --strict`.
- **Never lower a threshold** (coverage floor, rate limit, body cap) to make CI
  green. Fix the code or document the exception.
- **No secrets in fixtures.** Assemble token-shaped strings at runtime
  (`"ghp" + "_" + "x" * 20`), never as literals — GitGuardian scans the whole PR.
- Match the surrounding style; keep diffs small enough to review in one pass.

## Commit messages

`type(scope): summary` — `feat(api): …`, `fix(database): …`, `docs: …`,
`test: …`, `chore: …`. The body explains *why*, not *what*.

## Reporting bugs

Use the issue template. Include `mcp-hub --version`, `mcp-hub doctor`, and the
exact command that failed. For security issues, see
[SECURITY.md](SECURITY.md) — never a public issue.
