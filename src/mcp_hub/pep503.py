"""Stateless PEP 503 simple-repository rendering.

No database, no network, no cache: a pure function turns release metadata into
the HTML files pip expects at ``<index-url>/<project>/``. Hash fragments in the
links make the index anti-rug-pull by construction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import escape

_NORMALIZE_RE = re.compile(r"[-_.]+")
_VERSION_SPLIT_RE = re.compile(r"[.\-+]")


@dataclass(frozen=True, slots=True)
class Distribution:
    """One downloadable artifact with its immutable SHA-256 digest."""

    filename: str
    url: str
    sha256: str
    requires_python: str = ">=3.10"


@dataclass(frozen=True, slots=True)
class Release:
    """One version of a project."""

    version: str
    distributions: tuple[Distribution, ...]


def normalize_project(name: str) -> str:
    """PEP 503 normalization: lowercase and collapse ``-``/``_``/``.`` runs."""

    return _NORMALIZE_RE.sub("-", name).lower()


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in _VERSION_SPLIT_RE.split(version))


def render_root(projects: Iterable[str]) -> str:
    """Render the top-level ``simple/index.html``."""

    links = "\n".join(
        f'    <a href="{escape(normalize_project(name))}/">{escape(name)}</a>'
        for name in sorted(projects, key=normalize_project)
    )
    return f"""<!DOCTYPE html>
<html>
  <head>
    <meta name="pypi:repository-version" content="1.0">
    <title>Simple index</title>
  </head>
  <body>
{links}
  </body>
</html>
"""


def render_project(project: str, releases: Iterable[Release]) -> str:
    """Render the per-project page with hash-pinned artifact links."""

    rows: list[str] = []
    for release in sorted(releases, key=lambda item: _version_key(item.version), reverse=True):
        for dist in release.distributions:
            href = escape(f"{dist.url}#sha256={dist.sha256}", quote=True)
            requires = escape(dist.requires_python, quote=True)
            rows.append(
                f'    <a href="{href}" data-requires-python="{requires}">'
                f"{escape(dist.filename)}</a>"
            )
    links = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html>
  <head>
    <meta name="pypi:repository-version" content="1.0">
    <title>Links for {escape(project)}</title>
  </head>
  <body>
    <h1>Links for {escape(project)}</h1>
{links}
  </body>
</html>
"""


def build_index(releases_by_project: Mapping[str, Sequence[Release]]) -> dict[str, str]:
    """Build every file of a PEP 503 repository tree.

    Keys are relative paths; values are UTF-8 HTML. Callers write them wherever
    their static host expects them.
    """

    files = {"index.html": render_root(releases_by_project)}
    for project, releases in releases_by_project.items():
        files[f"{normalize_project(project)}/index.html"] = render_project(project, releases)
    return files
