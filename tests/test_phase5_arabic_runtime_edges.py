"""Edge tests for the Arabic localization runtime.

These cover fail-open and malformed-input branches without network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_hub import arabic_runtime as ar
from mcp_hub.live_sync import SyncResult


def _registry() -> dict[str, Any]:
    return {
        "mcpServers": {
            "demo": {
                "tools": [
                    {
                        "name": "demo_tool",
                        "description_ar": "وصف عربي",
                        "params_ar": {"q": {"description_ar": "الاستعلام"}},
                    }
                ]
            }
        }
    }


def test_registry_shape_and_translation_map_edges(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "registry").mkdir(parents=True)
    bundled = package / "registry" / "mcp_servers.json"
    bundled.write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ar, "__file__", str(package / "__init__.py"))
    assert ar._bundled_registry_path() == bundled
    monkeypatch.undo()

    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"mcpServers": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="mcpServers"):
        ar.load_registry(invalid)

    assert ar.build_translation_map({"mcpServers": []}) == {}
    noisy = {
        "mcpServers": {
            "bad_entry": "not-a-dict",
            "no_tools": {"tools": "not-a-list"},
            "bad_tool": {"tools": ["not-a-dict"]},
            "bad_name": {"tools": [{"name": 42}]},
        }
    }
    assert ar.build_translation_map(noisy) == {}
    assert ar._format_params({}) == ""


def test_description_copy_fallbacks() -> None:
    class Model:
        def __init__(self, description: str) -> None:
            self.description = description

        def model_copy(self, *, update: dict[str, Any]) -> Model:
            clone = Model(self.description)
            for key, value in update.items():
                setattr(clone, key, value)
            return clone

    model = Model("original")
    copied = ar._with_description(model, "arabic")
    assert copied is not model
    assert copied.description == "arabic"

    immutable = (1, 2)
    assert ar._with_description(immutable, "arabic") is immutable

    assert ar.localize_tool(object(), {}) is not None
    assert ar.localize_result(object(), {}) is not None

    dict_result = ar.localize_result({"tools": []}, {})
    assert dict_result == {"tools": []}

    class ResultModel:
        def __init__(self) -> None:
            self.tools: list[Any] = []

        def model_copy(self, *, update: dict[str, Any]) -> ResultModel:
            clone = ResultModel()
            for key, value in update.items():
                setattr(clone, key, value)
            return clone

    assert isinstance(ar.localize_result(ResultModel(), {}), ResultModel)

    class ReadOnlyResult:
        @property
        def tools(self) -> list[Any]:
            return []

    read_only = ReadOnlyResult()
    assert ar.localize_result(read_only, {}) is read_only


def test_initialize_fail_open_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ar, "_warned", set())
    cache = tmp_path / "cache.json"
    monkeypatch.setenv(ar.ENV_CACHE, str(cache))

    monkeypatch.setenv(ar.ENV_UPSTREAM, "https://evil.example/registry/mcp_servers.json")
    monkeypatch.setattr(ar, "load_cache", lambda _path: {"registry": _registry()})
    monkeypatch.setattr(ar, "save_cache", lambda *_args, **_kwargs: None)
    assert ar._initialize() is True
    assert ar._STATE["upstream"] == ""

    monkeypatch.setenv(ar.ENV_UPSTREAM, ar.DEFAULT_UPSTREAM_URL)

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("network down")

    monkeypatch.setattr(ar, "refresh_from_upstream", _boom)
    monkeypatch.setattr(ar, "load_cache", lambda _path: {"registry": _registry()})
    assert ar._initialize() is True
    assert "default_tool" not in ar._STATE["translations"]
    assert "demo_tool" in ar._STATE["translations"]

    monkeypatch.delenv(ar.ENV_UPSTREAM)
    monkeypatch.setattr(ar, "load_cache", _boom)
    monkeypatch.setattr(ar, "load_registry", _boom)
    assert ar._initialize() is False
    assert ar._STATE["translations"] == {}


def test_initialize_uses_upstream_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ar.ENV_CACHE, str(tmp_path / "cache.json"))
    monkeypatch.setenv(ar.ENV_UPSTREAM, ar.DEFAULT_UPSTREAM_URL)
    monkeypatch.setattr(
        ar,
        "refresh_from_upstream",
        lambda *_args, **_kwargs: SyncResult(
            status="fresh", registry=_registry(), source="upstream", digest="d"
        ),
    )
    assert ar._initialize() is True
    assert "demo_tool" in ar._STATE["translations"]


def test_maybe_refresh_success_and_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ar, "_warned", set())
    monkeypatch.setenv(ar.ENV_LIVE, "1")
    monkeypatch.setenv(ar.ENV_TTL, "not-a-number")
    monkeypatch.setattr(
        ar,
        "_STATE",
        {
            "translations": {},
            "cache_path": tmp_path / "cache.json",
            "upstream": ar.DEFAULT_UPSTREAM_URL,
        },
    )

    monkeypatch.setattr(
        ar,
        "refresh_from_upstream",
        lambda *_args, **_kwargs: SyncResult(
            status="fresh", registry=_registry(), source="upstream", digest="d"
        ),
    )
    ar._maybe_refresh()
    assert "demo_tool" in ar._STATE["translations"]

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("refresh down")

    monkeypatch.setattr(ar, "refresh_from_upstream", _boom)
    ar._maybe_refresh()

    monkeypatch.setitem(ar._STATE, "upstream", "")
    ar._maybe_refresh()

    monkeypatch.setitem(ar._STATE, "upstream", ar.DEFAULT_UPSTREAM_URL)
    monkeypatch.setitem(ar._STATE, "cache_path", "not-a-path")
    ar._maybe_refresh()

    monkeypatch.delenv(ar.ENV_LIVE)
    ar._maybe_refresh()


def test_install_no_translations_and_no_list_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ar, "_warned", set())
    monkeypatch.setenv(ar.ENV_RUNTIME, "1")
    monkeypatch.setattr(ar, "_initialize", lambda: False)

    class App:
        request_handlers: dict[str, Any] = {}

    assert ar.install(App()) is False

    monkeypatch.setattr(ar, "_initialize", lambda: True)

    class AppWithHandler:
        def __init__(self) -> None:
            self.request_handlers = {"Other": lambda _message: None}

    assert ar.install(AppWithHandler()) is False
