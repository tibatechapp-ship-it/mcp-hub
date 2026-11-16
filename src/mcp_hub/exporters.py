from __future__ import annotations

import csv
import json
from pathlib import Path


COLUMNS = [
    "id",
    "name",
    "publisher",
    "description",
    "source",
    "registry_id",
    "repository_url",
    "endpoint_url",
    "package_name",
    "categories",
    "popularity",
]


def export_json(db, path: str | Path) -> Path:
    path = Path(path)
    rows = db.all_servers()
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_csv(db, path: str | Path) -> Path:
    path = Path(path)
    rows = db.all_servers()
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in COLUMNS})
    return path


def export_markdown(db, path: str | Path) -> Path:
    path = Path(path)
    rows = db.all_servers()
    lines = [
        "# MCP Hub Catalog",
        "",
        f"Total servers: **{len(rows)}**",
        "",
        "| id | name | publisher | categories | description |",
        "|---:|---|---|---|---|",
    ]
    for r in rows:
        desc = (r.get("description") or "").replace("\n", " ").replace("|", "\\|")
        if len(desc) > 120:
            desc = desc[:117] + "..."
        lines.append(
            f"| {r.get('id','')} | {r.get('name','')} | {r.get('publisher','')} | "
            f"{r.get('categories','')} | {desc} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
