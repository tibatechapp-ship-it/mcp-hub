"""Build-config consistency checks (v1.4.1 hardening)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_docker_base_matches_ci_and_dependabot_policy() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dependabot = (_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim AS base" in dockerfile
    assert '"3.15-slim"' in dependabot  # still ignored as unreleased
    assert '"3.13"' in ci  # the claimed stable line is actually tested
