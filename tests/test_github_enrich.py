"""GitHub enrichment: URL parsing, success path, and redacted failures.

No network: ``_fetch_repo`` is the single HTTP seam and is monkeypatched.
"""

from __future__ import annotations

import urllib.error

import pytest

from mcp_hub import github_enrich
from mcp_hub.database import Catalog
from mcp_hub.models import Server


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/modelcontextprotocol/servers", "modelcontextprotocol/servers"),
        ("https://github.com/modelcontextprotocol/servers.git", "modelcontextprotocol/servers"),
        ("http://github.com/a/b/tree/main", "a/b"),
        ("https://gitlab.com/a/b", None),
        ("", None),
    ],
)
def test_repo_from_url(url, expected):
    assert github_enrich._repo_from_url(url) == expected


def _seed(catalog: Catalog, name: str, repo_url: str) -> None:
    catalog.add_server(
        Server(name=name, publisher="acme", description="d", source="t", repository_url=repo_url)
    )


def test_enrich_saves_fields_from_the_api(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "db.sqlite3")
    _seed(catalog, "alpha", "https://github.com/acme/alpha")

    monkeypatch.setattr(
        github_enrich,
        "_fetch_repo",
        lambda repo, token=None: {
            "stargazers_count": 42,
            "forks_count": 7,
            "license": {"spdx_id": "MIT"},
            "pushed_at": "2026-11-01T00:00:00Z",
            "topics": ["mcp", "sqlite"],
        },
    )

    result = github_enrich.enrich_catalog(catalog, limit=10, token="t")

    assert result == {"attempted": 1, "succeeded": 1, "failed": 0, "skipped": 0}
    server_id = catalog.search("alpha")[0]["id"]
    with catalog.connect() as con:
        row = con.execute(
            "SELECT stars, forks, license, pushed_at, topics FROM enrichments WHERE server_id=?",
            (server_id,),
        ).fetchone()
    assert dict(row) == {
        "stars": 42,
        "forks": 7,
        "license": "MIT",
        "pushed_at": "2026-11-01T00:00:00Z",
        "topics": "mcp,sqlite",
    }


def test_enrich_skips_servers_without_a_github_url(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "db.sqlite3")
    _seed(catalog, "beta", "https://example.com/beta")
    monkeypatch.setattr(github_enrich, "_fetch_repo", pytest.fail)

    result = github_enrich.enrich_catalog(catalog, limit=10)

    assert result["skipped"] == 1
    assert result["attempted"] == 1
    assert result["succeeded"] == 0


def test_enrich_reports_an_http_error_without_the_url(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "db.sqlite3")
    _seed(catalog, "gamma", "https://github.com/acme/gamma")

    def boom(repo, token=None):
        raise urllib.error.HTTPError("https://api.github.com", 403, "rate limited", {}, None)

    monkeypatch.setattr(github_enrich, "_fetch_repo", boom)

    result = github_enrich.enrich_catalog(catalog, limit=10)

    assert result["failed"] == 1
    assert result["last_error"] == "HTTP 403 for acme/gamma"


def test_enrich_redacts_unexpected_errors(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "db.sqlite3")
    _seed(catalog, "delta", "https://github.com/acme/delta")

    def boom(repo, token=None):
        raise RuntimeError("token=ghp_secretvalue leaked")

    monkeypatch.setattr(github_enrich, "_fetch_repo", boom)

    result = github_enrich.enrich_catalog(catalog, limit=10)

    assert result["failed"] == 1
    assert "ghp_secretvalue" not in result["last_error"]


def test_enrich_uses_the_env_token(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "db.sqlite3")
    _seed(catalog, "epsilon", "https://github.com/acme/epsilon")
    seen: list[str | None] = []

    def fake(repo, token=None):
        seen.append(token)
        return {}

    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr(github_enrich, "_fetch_repo", fake)

    github_enrich.enrich_catalog(catalog, limit=10)

    assert seen == ["env-token"]
