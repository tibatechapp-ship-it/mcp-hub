# Contributing to mcp-hub

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,api]"
pre-commit install
```

## Tests and style

```bash
ruff check src tests
ruff format src tests
pytest -q
pytest --cov=mcp_hub --cov-report=term-missing
```

## Pull request checklist

- [ ] New/changed behavior has tests
- [ ] `ruff check` and `ruff format --check` pass
- [ ] `pytest` passes locally
- [ ] Docs updated (README/CHANGELOG/SECURITY as needed)
- [ ] No secrets or credentials committed

## Notes

- Keep the project local-first and dependency-light.
- Prefer additive, backward-compatible DB migrations.
- Do not remove v1.x CLI commands or data compatibility without a migration path.
