"""PEP 503 index rendering — pure, stateless, hash-pinned."""

from __future__ import annotations

from mcp_hub.pep503 import (
    Distribution,
    Release,
    build_index,
    normalize_project,
    render_project,
    render_root,
)


def test_normalize_project_follows_pep503() -> None:
    assert normalize_project("MCP_Hub.X") == "mcp-hub-x"
    assert normalize_project("mcp-hub") == "mcp-hub"


def test_root_page_links_normalized_project() -> None:
    html = render_root(["mcp-hub"])

    assert '<meta name="pypi:repository-version" content="1.0">' in html
    assert '<a href="mcp-hub/">mcp-hub</a>' in html


def test_project_page_hash_pins_artifacts_and_orders_versions() -> None:
    releases = [
        Release(
            version="1.3.1",
            distributions=(
                Distribution(
                    filename="mcp_hub-1.3.1-py3-none-any.whl",
                    url="https://example.test/old.whl",
                    sha256="a" * 64,
                ),
            ),
        ),
        Release(
            version="1.4.0",
            distributions=(
                Distribution(
                    filename="mcp_hub-1.4.0-py3-none-any.whl",
                    url="https://example.test/new.whl",
                    sha256="b" * 64,
                ),
                Distribution(
                    filename="mcp_hub-1.4.0.tar.gz",
                    url="https://example.test/new.tar.gz",
                    sha256="c" * 64,
                    requires_python=">=3.10",
                ),
            ),
        ),
    ]

    html = render_project("mcp-hub", releases)

    assert html.index("new.whl") < html.index("old.whl")
    assert f"https://example.test/new.whl#sha256={'b' * 64}" in html
    assert f"https://example.test/new.tar.gz#sha256={'c' * 64}" in html
    assert 'data-requires-python="&gt;=3.10"' in html
    assert ">mcp_hub-1.4.0.tar.gz</a>" in html


def test_build_index_returns_relative_paths() -> None:
    releases = (
        Release(
            version="1.4.0",
            distributions=(
                Distribution("mcp_hub-1.4.0-py3-none-any.whl", "https://e.test/x", "d" * 64),
            ),
        ),
    )

    files = build_index({"MCP_Hub": releases})

    assert set(files) == {"index.html", "mcp-hub/index.html"}
    assert "mcp-hub" in files["index.html"]
    assert "d" * 64 in files["mcp-hub/index.html"]
