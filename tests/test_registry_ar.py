"""Tests for the unified Arabic MCP registry and its generated catalog."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry" / "mcp_servers.json"
CATALOG = ROOT / "docs" / "catalog_ar.md"
_VALIDATOR = ROOT / "scripts" / "validate_registry.py"

spec = importlib.util.spec_from_file_location("validate_registry", _VALIDATOR)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def _data() -> dict:
    return validator.load(REGISTRY)


def test_registry_is_valid() -> None:
    assert validator.validate(_data()) == []


def test_registry_is_strict_utf8() -> None:
    text = REGISTRY.read_text(encoding="utf-8")
    assert "\ufffd" not in text
    assert "فهرس" in text


def test_core_servers_are_present() -> None:
    servers = _data()["mcpServers"]
    assert validator.CORE_IDS <= set(servers)


def test_no_secret_values_in_env() -> None:
    servers = _data()["mcpServers"]
    for entry in servers.values():
        assert all(value == "" for value in entry["env"].values())


def test_native_fields_are_unchanged() -> None:
    servers = _data()["mcpServers"]
    assert servers["mcp-hub"]["command"] == "python"
    assert servers["mcp-hub"]["args"] == ["-m", "mcp_hub.mcp_server"]
    assert servers["fast-anything"]["command"] == "fast-anything"
    assert servers["fast-anything"]["args"] == ["mcp"]
    assert {t["name"] for t in servers["arabic-tools"]["tools"]} == {
        "greet",
        "prayer_times",
        "quran_search",
        "translate_to_arabic",
    }


def test_every_tool_is_localized() -> None:
    for entry in _data()["mcpServers"].values():
        for tool in entry["tools"]:
            assert validator.ARABIC.search(tool["name_ar"])
            assert validator.ARABIC.search(tool["description_ar"])


def test_arabic_catalog_covers_every_server_and_tool() -> None:
    catalog = CATALOG.read_text(encoding="utf-8")
    for sid, entry in _data()["mcpServers"].items():
        assert f"`{sid}`" in catalog, sid
        assert entry["name_ar"] in catalog, sid
        for tool in entry["tools"]:
            assert f"`{tool['name']}`" in catalog, (sid, tool["name"])
            assert tool["name_ar"] in catalog, (sid, tool["name_ar"])


def test_json_serialises_arabic_without_escaping() -> None:
    raw = REGISTRY.read_text(encoding="utf-8")
    assert "إدارة مستودعات GitHub" in raw
    assert json.loads(raw)["mcpServers"]["github"]["name_ar"] == "إدارة مستودعات GitHub"


def test_capability_fields_are_complete() -> None:
    for sid, entry in _data()["mcpServers"].items():
        assert entry["access_mode"] in validator.ACCESS_MODES, sid
        assert entry["tags"] and set(entry["tags"]) <= validator.KNOWN_TAGS, sid
        assert set(entry["task_profiles"]) <= validator.PROFILE_IDS, sid
        for tool in entry["tools"]:
            where = f"{sid}.{tool['name']}"
            assert tool["access_mode"] in validator.ACCESS_MODES, where
            assert tool["tags"] and set(tool["tags"]) <= validator.KNOWN_TAGS, where
            assert set(tool["task_profiles"]) <= validator.PROFILE_IDS, where
            assert set(tool["requires_env"]) <= set(entry["env"]), where


def test_query_catalog_tool_is_registered() -> None:
    tools = _data()["mcpServers"]["mcp-hub"]["tools"]
    query = next(t for t in tools if t["name"] == "query_catalog")
    assert "الاستعلام" in query["name_ar"]
    assert query["access_mode"] == "read-only"
    assert "rag-web-search" in validator.KNOWN_TAGS


def test_profiles_are_valid() -> None:
    assert validator.validate_profiles() == []
    files = sorted(validator.DEFAULT_PROFILES_DIR.glob("*.json"))
    ids = {json.loads(file.read_text(encoding="utf-8"))["profile_id"] for file in files}
    assert ids == validator.PROFILE_IDS


def test_profile_access_defaults() -> None:
    profiles = {
        json.loads(file.read_text(encoding="utf-8"))["profile_id"]: json.loads(
            file.read_text(encoding="utf-8")
        )
        for file in validator.DEFAULT_PROFILES_DIR.glob("*.json")
    }
    for pid in ("code-review", "ai-agent-builder", "quality-security-audit"):
        assert profiles[pid]["default_access_mode"] == "read-only"
    assert profiles["docs-localization"]["default_access_mode"] == "read-write"
    ops = profiles["automation-ops"]
    assert ops["default_access_mode"] == "read-write"
    assert ops["requires_explicit_approval"] is True


def test_validator_rejects_bad_capability_values() -> None:
    data = _data()
    broken = copy.deepcopy(data)
    broken["mcpServers"]["mcp-hub"]["tools"][0]["access_mode"] = "admin"
    errors = validator.validate(broken, check_profiles=False)
    assert any("access_mode" in error for error in errors)

    broken = copy.deepcopy(data)
    broken["mcpServers"]["mcp-hub"]["tools"][0]["task_profiles"] = ["unknown-profile"]
    errors = validator.validate(broken, check_profiles=False)
    assert any("task_profiles" in error for error in errors)

    broken = copy.deepcopy(data)
    broken["mcpServers"]["mcp-hub"]["tools"][0]["requires_env"] = ["NOT_DECLARED"]
    errors = validator.validate(broken, check_profiles=False)
    assert any("requires_env" in error for error in errors)
