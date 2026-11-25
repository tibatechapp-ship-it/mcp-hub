from __future__ import annotations

from pathlib import Path

import yaml

from mcp_hub import arflow

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "governance-gate.yml"
TEMPLATES = ROOT / "templates" / "arflow"


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_action_contract() -> None:
    data = _load(ACTION)
    assert data["name"]
    required_inputs = {
        "playbook-path",
        "mode",
        "incremental",
        "changed-only",
        "lang",
        "tenant",
        "audit-db",
    }
    assert required_inputs <= set(data["inputs"])
    assert {"exit-code", "summary"} <= set(data["outputs"])
    runs = data["runs"]
    assert runs["using"] == "composite"
    steps = runs["steps"]
    assert steps and all("shell" in step for step in steps)
    assert not any("uses" in step for step in steps)
    script = "\n".join(str(step.get("run", "")) for step in steps)
    assert "--index-url" in script
    assert "mcp-hub==$VERSION" in script
    assert "arflow" in script
    assert "--json" in script
    assert "--no-incremental" in script
    assert "$GITHUB_STEP_SUMMARY" in script


def test_reusable_workflow_contract() -> None:
    data = _load(WORKFLOW)
    trigger = data.get("on") or data.get(True)
    assert isinstance(trigger, dict)
    assert "workflow_call" in trigger
    call = trigger["workflow_call"]
    assert {
        "playbook-path",
        "mode",
        "incremental",
        "changed-only",
        "lang",
        "tenant",
        "audit-db",
        "action-ref",
    } <= set(call["inputs"])
    assert {"exit-code", "summary"} <= set(call["outputs"])
    job = data["jobs"]["gate"]
    steps = job["steps"]
    assert any(str(step.get("uses", "")).startswith("actions/cache@") for step in steps)
    assert any(str(step.get("uses", "")).startswith("actions/checkout@") for step in steps)
    assert any(step.get("uses") == "./mcp-hub-action" for step in steps)
    assert any("GITHUB_STEP_SUMMARY" in str(step.get("run", "")) for step in steps)
    assert any(step.get("if") == "always()" for step in steps)
    assert job["outputs"]["exit-code"] == "${{ steps.governance.outputs.exit-code }}"


def test_reference_templates_compile() -> None:
    names = ("policy-check.arflow", "security-check.arflow", "release-readiness.arflow")
    for name in names:
        path = TEMPLATES / name
        assert path.is_file()
        compiled = arflow.build(path.read_text(encoding="utf-8"), name=path.stem)
        assert compiled.ok, [issue.message for issue in compiled.issues]


def test_docs_and_packaging_references() -> None:
    doc = (ROOT / "docs" / "central_governance.md").read_text(encoding="utf-8")
    for needle in ("governance-gate.yml", "tayf", "novamind-core", "Ruleset", "PEP 503"):
        assert needle in doc
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "central_governance.md" in nav
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "templates/arflow/" in readme
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Central governance Phase 1" in changelog
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"/action.yml"' in pyproject
    assert '"/templates"' in pyproject
    assert "PyYAML>=6,<7" in pyproject
