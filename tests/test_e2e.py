"""End-to-end: sync -> enrich -> classify -> search -> export."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from mcp_hub import github_enrich, registry_api, saved_searches, taxonomy
from mcp_hub.database import Catalog
from mcp_hub.exporters import export_csv, export_markdown


def _resp(payload, status=200):
    m = MagicMock()
    m.read.return_value = json.dumps(payload).encode()
    m.status = status
    m.__enter__ = lambda s: s
    m.__exit__ = lambda s, *a: None
    return m


def test_full_lifecycle(tmp_path):
    db = Catalog(tmp_path / "e2e.sqlite3")

    # 1. Sync from registry
    page = {
        "servers": [
            {
                "server": {
                    "name": "io.github.acme/tool1",
                    "description": "PostgreSQL query runner",
                    "repository": {"url": "https://github.com/acme/tool1"},
                    "remotes": [{"url": "https://mcp.acme.com/mcp"}],
                },
                "_meta": {},
            },
            {
                "server": {
                    "name": "io.github.acme/tool2",
                    "description": "Web search via API",
                    "repository": {"url": "https://github.com/acme/tool2"},
                },
                "_meta": {},
            },
        ],
        "metadata": {},
    }
    with patch("mcp_hub.registry_api.urllib.request.urlopen", return_value=_resp(page)):
        rows = registry_api.fetch_servers(limit=10)
    res = db.bulk_add(rows)
    assert res["new"] == 2
    assert db.stats()["records"] == 2

    # 2. Enrich from GitHub
    gh = {
        "stargazers_count": 42,
        "forks_count": 5,
        "license": {"spdx_id": "MIT"},
        "pushed_at": "2026-01-01T00:00:00Z",
        "topics": ["mcp"],
    }
    with patch("mcp_hub.github_enrich.urllib.request.urlopen", return_value=_resp(gh)):
        summary = github_enrich.enrich_catalog(db)
    assert summary["succeeded"] == 2

    # 3. Classify
    taxonomy.classify_catalog(db)
    pg = db.search("tool1")[0]
    assert "database" in pg["categories"]

    # 4. Search (both modes)
    assert len(db.search("postgres")) >= 1
    assert len(db.search_fts("postgres")) >= 1

    # 5. Saved search + run
    saved_searches.save(db, "pg", "postgres")
    result = saved_searches.run(db, "pg")
    assert result["match_count"] >= 1

    # 6. Export (all formats)
    csv_path = tmp_path / "out.csv"
    md_path = tmp_path / "out.md"
    export_csv(db, csv_path)
    export_markdown(db, md_path)
    assert csv_path.exists() and md_path.exists()
    assert "PostgreSQL" in md_path.read_text()
