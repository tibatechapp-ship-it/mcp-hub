"""Read-only skill packages for the 2026 MCP revision.

A skill package is a directory with a ``SKILL.md`` front matter and supporting
text files. Loading a skill never executes the tools it mentions: the registry
publishes metadata/content only, while tool execution stays behind Phase 2's
policy gateway. Approvals are bound to a canonical SHA-256 digest, so any file
or metadata change revokes the approval automatically.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .database import Catalog
from .redact import redact

__all__ = [
    "SkillError",
    "SkillFile",
    "SkillPackage",
    "SkillRegistry",
    "SKILL_RESOURCE_PREFIX",
    "build_skill_approval_handlers",
    "load_skill",
]

MAX_PACKAGE_BYTES = 262_144
MAX_FILE_BYTES = 65_536
MAX_FILES = 32

SKILL_RESOURCE_PREFIX = "mcp-hub://skills/"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9.-]+)?$")
_METADATA_KEYS = frozenset({"name", "version", "description", "mentioned_tools"})


class SkillError(ValueError):
    """A skill package failed strict validation."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _no_secret(label: str, value: str) -> str:
    if redact(value) != value:
        raise SkillError(f"{label} must not contain secret-shaped values")
    return value


@dataclass(frozen=True, slots=True)
class SkillFile:
    """One validated UTF-8 text file inside a skill package."""

    path: str
    text: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class SkillPackage:
    """A validated skill package with its content-bound digest."""

    name: str
    version: str
    description: str
    mentioned_tools: tuple[str, ...]
    files: tuple[SkillFile, ...]
    digest: str
    size_bytes: int
    approved: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "sha256": self.digest,
            "size_bytes": self.size_bytes,
            "file_count": len(self.files),
            "approved": self.approved,
            "grants_execute": False,
        }

    def to_dict(self, *, include_files: bool = False) -> dict[str, Any]:
        payload = self.summary()
        payload["mentioned_tools"] = list(self.mentioned_tools)
        payload["grants_execute"] = False
        if include_files:
            payload["files"] = [{**item.to_dict(), "text": item.text} for item in self.files]
        return payload


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with --- front matter")
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        raise SkillError("SKILL.md front matter is not closed")
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise SkillError("front matter lines must be key: value")
        key, value = (part.strip() for part in stripped.split(":", 1))
        if key not in _METADATA_KEYS:
            raise SkillError(f"unsupported skill metadata key: {key}")
        if key in metadata:
            raise SkillError(f"duplicate skill metadata key: {key}")
        metadata[key] = _no_secret(key, value)
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillError("SKILL.md must contain instructions after front matter")
    return metadata, body


def _digest_for(
    name: str,
    version: str,
    description: str,
    mentioned_tools: tuple[str, ...],
    files: tuple[SkillFile, ...],
) -> str:
    payload = {
        "name": name,
        "version": version,
        "description": description,
        "mentioned_tools": list(mentioned_tools),
        "files": [item.to_dict() for item in files],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_skill(path: str | Path) -> SkillPackage:
    """Validate one skill directory and return a content-bound package."""

    root = Path(path).expanduser()
    if not root.is_dir() or root.is_symlink():
        raise SkillError("skill path must be a directory")
    files: list[SkillFile] = []
    total = 0
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise SkillError("skill packages must not contain symlinks")
        if not item.is_file():
            continue
        relative = item.relative_to(root).as_posix()
        if relative.startswith("/") or ".." in relative.split("/"):
            raise SkillError("skill file path escapes the package")
        size = item.stat().st_size
        if size > MAX_FILE_BYTES:
            raise SkillError(f"skill file too large: {relative}")
        total += size
        if total > MAX_PACKAGE_BYTES:
            raise SkillError("skill package exceeds the size limit")
        try:
            text = item.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillError(f"skill file is not UTF-8: {relative}") from exc
        files.append(
            SkillFile(
                path=relative,
                text=text,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                size_bytes=size,
            )
        )
    if not files or len(files) > MAX_FILES:
        raise SkillError("skill package must contain 1 to 32 files")
    skill_md = next((item for item in files if item.path == "SKILL.md"), None)
    if skill_md is None:
        raise SkillError("skill package requires SKILL.md")
    metadata, _ = _parse_frontmatter(skill_md.text)
    missing = sorted({"name", "version", "description"} - set(metadata))
    if missing:
        raise SkillError("missing skill metadata: " + ", ".join(missing))
    name = metadata["name"]
    version = metadata["version"]
    description = metadata["description"]
    if not _NAME_RE.fullmatch(name):
        raise SkillError("skill name must be lowercase and 1-64 characters")
    if not _VERSION_RE.fullmatch(version):
        raise SkillError("skill version must look like X.Y.Z")
    if len(description) > 500:
        raise SkillError("skill description is too long")
    mentioned = tuple(
        part.strip() for part in metadata.get("mentioned_tools", "").split(",") if part.strip()
    )
    if any(not _NAME_RE.fullmatch(tool) for tool in mentioned):
        raise SkillError("mentioned_tools entries must be lowercase tool names")
    ordered = tuple(sorted(files, key=lambda item: item.path))
    return SkillPackage(
        name=name,
        version=version,
        description=description,
        mentioned_tools=mentioned,
        files=ordered,
        digest=_digest_for(name, version, description, mentioned, ordered),
        size_bytes=total,
        approved=False,
    )


class SkillRegistry:
    """Load skills from a directory and track approvals in SQLite."""

    def __init__(
        self,
        catalog: Catalog,
        root: str | Path | None = None,
        *,
        initialize: bool = True,
    ) -> None:
        self.catalog = catalog
        self.root = (
            Path(root).expanduser()
            if root is not None
            else Path(__file__).resolve().parent / "skills"
        )
        if initialize:
            self._init_table()
        self._packages = self._load_packages()

    def _init_table(self) -> None:
        with self.catalog.connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS skill_approvals ("
                "name TEXT PRIMARY KEY, "
                "sha256 TEXT NOT NULL, "
                "approved_at TEXT NOT NULL, "
                "actor TEXT NOT NULL DEFAULT 'local')"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS skill_approval_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, "
                "sha256 TEXT NOT NULL, "
                "action TEXT NOT NULL, "
                "actor TEXT NOT NULL DEFAULT 'local', "
                "detail TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL)"
            )

    def _load_packages(self) -> dict[str, SkillPackage]:
        packages: dict[str, SkillPackage] = {}
        if not self.root.exists():
            return packages
        for child in sorted(self.root.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            if not (child / "SKILL.md").is_file():
                continue
            package = load_skill(child)
            if package.name in packages:
                raise SkillError(f"duplicate skill name: {package.name}")
            packages[package.name] = package
        return packages

    def _approved_digest(self, name: str) -> str | None:
        with self.catalog.connect() as con:
            row = con.execute("SELECT sha256 FROM skill_approvals WHERE name=?", (name,)).fetchone()
        return str(row["sha256"]) if row else None

    def _with_approval(self, package: SkillPackage) -> SkillPackage:
        approved = self._approved_digest(package.name) == package.digest
        return replace(package, approved=approved)

    @staticmethod
    def _record_approval_event(
        con: Any,
        name: str,
        digest: str,
        action: str,
        actor: str,
        detail: str,
    ) -> None:
        con.execute(
            "INSERT INTO skill_approval_events "
            "(name,sha256,action,actor,detail,created_at) VALUES (?,?,?,?,?,?)",
            (
                name,
                digest,
                action,
                redact(actor)[:64] or "local",
                redact(detail)[:500],
                _utc_now(),
            ),
        )

    def approval_events(self, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return redacted approval events, newest first."""
        if not 1 <= limit <= 200:
            raise SkillError("limit must be between 1 and 200")
        sql = "SELECT name,sha256,action,actor,detail,created_at FROM skill_approval_events"
        params: list[Any] = []
        if name is not None:
            sql += " WHERE name=?"
            params.append(name)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.catalog.connect() as con:
            return [dict(row) for row in con.execute(sql, params).fetchall()]

    def reload(self) -> None:
        self._packages = self._load_packages()

    def list(self) -> list[SkillPackage]:
        return [self._with_approval(package) for package in self._packages.values()]

    def get(self, name: str) -> SkillPackage:
        package = self._packages.get(name)
        if package is None:
            raise KeyError(f"unknown skill: {name}")
        return self._with_approval(package)

    def approve(self, name: str, actor: str = "local") -> str:
        package = self._packages.get(name)
        if package is None:
            raise KeyError(f"unknown skill: {name}")
        safe_actor = _no_secret("actor", actor)[:64] or "local"
        with self.catalog.connect() as con:
            con.execute(
                "INSERT INTO skill_approvals (name,sha256,approved_at,actor) "
                "VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "sha256=excluded.sha256, approved_at=excluded.approved_at, "
                "actor=excluded.actor",
                (name, package.digest, _utc_now(), safe_actor),
            )
            self._record_approval_event(
                con,
                name,
                package.digest,
                "approved",
                safe_actor,
                f"approved digest {package.digest}",
            )
        return package.digest

    def revoke(self, name: str, actor: str = "local") -> bool:
        safe_actor = _no_secret("actor", actor)[:64] or "local"
        with self.catalog.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT sha256 FROM skill_approvals WHERE name=?", (name,)).fetchone()
            if row is None:
                return False
            digest = str(row["sha256"])
            cursor = con.execute("DELETE FROM skill_approvals WHERE name=?", (name,))
            self._record_approval_event(
                con, name, digest, "revoked", safe_actor, f"revoked digest {digest}"
            )
        return int(cursor.rowcount) > 0

    def read_resource(self, uri: str) -> dict[str, Any]:
        if not isinstance(uri, str) or not uri.startswith(SKILL_RESOURCE_PREFIX):
            raise SkillError("unsupported skill resource URI")
        parts = [unquote(part) for part in uri[len(SKILL_RESOURCE_PREFIX) :].split("/")]
        name, requested = parts[0], "/".join(parts[1:]) or "SKILL.md"
        if not name or ".." in requested.split("/") or requested.startswith("/"):
            raise SkillError("invalid skill resource URI")
        package = self.get(name)
        skill_file = next((item for item in package.files if item.path == requested), None)
        if skill_file is None:
            raise SkillError(f"unknown skill resource: {uri}")
        mime = "text/markdown" if skill_file.path.endswith(".md") else "text/plain"
        return {
            "uri": uri,
            "name": name,
            "path": skill_file.path,
            "text": skill_file.text,
            "sha256": skill_file.sha256,
            "mimeType": mime,
            "approved": package.approved,
        }

    def resource_specs(self) -> tuple[tuple[str, str, str], ...]:
        """Return ``(uri, name, mime_type)`` tuples for every skill file."""

        specs: list[tuple[str, str, str]] = []
        for package in self._packages.values():
            for skill_file in package.files:
                uri = f"{SKILL_RESOURCE_PREFIX}{package.name}/{skill_file.path}"
                mime = "text/markdown" if skill_file.path.endswith(".md") else "text/plain"
                specs.append((uri, f"{package.name}: {skill_file.path}", mime))
        return tuple(specs)


def build_skill_handlers(registry: SkillRegistry) -> dict[str, Any]:
    """Return handlers for ``skills/list`` and ``skills/get``."""

    def list_skills(_: Mapping[str, Any]) -> dict[str, Any]:
        return {"skills": [package.summary() for package in registry.list()]}

    def get_skill(args: Mapping[str, Any]) -> dict[str, Any]:
        name = args.get("name")
        if not isinstance(name, str) or not name:
            raise SkillError("name is required for skills/get")
        return {"skill": registry.get(name).to_dict(include_files=True)}

    return {"skills/list": list_skills, "skills/get": get_skill}


def build_skill_approval_handlers(registry: SkillRegistry) -> dict[str, Any]:
    """Return write handlers that pin or revoke a skill digest."""

    def _name(args: Mapping[str, Any]) -> str:
        name = args.get("name")
        if not isinstance(name, str) or not name:
            raise SkillError("name is required for skill approval")
        return name

    def _actor(args: Mapping[str, Any]) -> str:
        actor = args.get("actor", "local")
        if not isinstance(actor, str) or not actor:
            return "local"
        return actor

    def approve(args: Mapping[str, Any]) -> dict[str, Any]:
        name = _name(args)
        digest = registry.approve(name, _actor(args))
        return {"name": name, "sha256": digest, "approved": True}

    def revoke(args: Mapping[str, Any]) -> dict[str, Any]:
        name = _name(args)
        removed = registry.revoke(name, _actor(args))
        return {"name": name, "approved": False, "removed": removed}

    return {"skills/approve": approve, "skills/revoke": revoke}


def build_skill_resources(registry: SkillRegistry) -> dict[str, Any]:
    """Return URI handlers for skill-backed ``resources/read`` calls."""

    handlers: dict[str, Any] = {}
    for uri, _, _ in registry.resource_specs():

        def resource_handler(_: Mapping[str, Any], resource_uri: str = uri) -> str:
            return str(registry.read_resource(resource_uri)["text"])

        handlers[uri] = resource_handler
    return handlers
