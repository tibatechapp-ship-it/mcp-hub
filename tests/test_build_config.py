"""Build-config consistency checks (v1.4.1 hardening, v1.8.0 release gate)."""

from __future__ import annotations

import re
from pathlib import Path

from mcp_hub import __version__

_ROOT = Path(__file__).resolve().parents[1]


def test_docker_base_matches_ci_and_dependabot_policy() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dependabot = (_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim AS base" in dockerfile
    assert '"3.15-slim"' in dependabot  # still ignored as unreleased
    assert '"3.13"' in ci  # the claimed stable line is actually tested


def test_release_attaches_checksums_and_docs_builds_simple_index() -> None:
    release = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docs = (_ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")

    assert "sha256sum *.whl *.tar.gz > SHA256SUMS" in release
    assert "dist/SHA256SUMS" in release
    assert "python scripts/generate_simple_index.py" in docs
    assert "workflow_run:" in docs  # regenerate after GITHUB_TOKEN release creation


def test_release_version_is_consistent() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "1\.8\.0"$', pyproject, re.MULTILINE)
    assert __version__ == "1.8.0"
