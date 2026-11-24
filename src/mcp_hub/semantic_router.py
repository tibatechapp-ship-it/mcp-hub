"""Deterministic bilingual semantic routing over mcp-hub's tool contracts.

This is a transparent heuristic, not an LLM: Arabic and English task text is
normalized, tokenized and scored against tool names, descriptions, tags and
task profiles from the bundled registry. ``compact=True`` shortens descriptions
and omits schemas so a client can fit more candidates into a small context.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .catalog_query import iter_entries, load_registry
from .mcp_stateless import ToolSpec

__all__ = [
    "detect_language",
    "tokenize",
    "build_index",
    "route_task",
]

_ARABIC = re.compile(r"[\u0600-\u06ff]")
_LATIN = re.compile(r"[A-Za-z]")
_ARABIC_TOKENS = re.compile(r"[\u0600-\u06ff]+")
_LATIN_TOKENS = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*")
_TATWEEL = "\u0640"
_DIACRITICS = re.compile(r"[\u064b-\u0652]")

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "من",
        "في",
        "على",
        "عن",
        "او",
        "و",
        "هذا",
        "هذه",
        "التي",
        "الذي",
        "ان",
        "كن",
    }
)

# Tiny, explicit bridge for common Arabic task verbs. It is a dictionary, not
# a translation model: every mapping is reviewable in tests and docs.
_SYNONYMS: dict[str, frozenset[str]] = {
    "ابحث": frozenset({"search", "query", "فهرس", "كاتالوج"}),
    "بحث": frozenset({"search", "query"}),
    "فهرس": frozenset({"catalog", "index", "كاتالوج"}),
    "كاتالوج": frozenset({"catalog", "index"}),
    "خادم": frozenset({"server"}),
    "خوادم": frozenset({"servers"}),
    "فئه": frozenset({"category"}),
    "فئات": frozenset({"categories"}),
    "تدقيق": frozenset({"audit", "provenance"}),
    "سجل": frozenset({"history", "events"}),
    "جوده": frozenset({"quality", "doctor"}),
    "صحه": frozenset({"health", "doctor"}),
    "سياسه": frozenset({"policy"}),
    "روايح": frozenset({"smell", "smells"}),
    "اوصاف": frozenset({"description", "descriptions"}),
}

_ALLOWED_LATIN = frozenset(
    {
        "api",
        "apis",
        "cli",
        "github",
        "http",
        "https",
        "iban",
        "json",
        "llm",
        "mcp",
        "rag",
        "rest",
        "sdk",
        "server",
        "servers",
        "sql",
        "ssn",
        "tool",
        "tools",
        "url",
    }
)


@dataclass(frozen=True, slots=True)
class _FieldTokens:
    name: frozenset[str]
    description: frozenset[str]
    tags: frozenset[str]
    profiles: frozenset[str]
    description_ar: str = ""
    description_en: str = ""


def detect_language(text: str) -> str:
    """Return ``ar``, ``en``, ``mixed`` or ``none`` from Unicode ranges."""
    if _ARABIC.search(text) and _LATIN.search(text):
        return "mixed"
    if _ARABIC.search(text):
        return "ar"
    if _LATIN.search(text):
        return "en"
    return "none"


def _normalize(token: str) -> str:
    value = _DIACRITICS.sub("", token.replace(_TATWEEL, "")).lower()
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        value = value.replace(source, target)
    if len(value) > 4 and value.startswith("ال"):
        value = value[2:]
    if value in _STOPWORDS:
        return ""
    return value


def tokenize(text: str) -> tuple[str, ...]:
    """Stable, normalized tokens from Arabic and/or Latin text."""
    raw = [*_ARABIC_TOKENS.findall(text), *_LATIN_TOKENS.findall(text)]
    tokens: list[str] = []
    for token in raw:
        normalized = _normalize(token)
        if normalized and normalized not in tokens:
            tokens.append(normalized)
    return tuple(tokens)


def _expand(tokens: Iterable[str]) -> frozenset[str]:
    expanded: set[str] = set()
    for token in tokens:
        expanded.add(token)
        expanded.update(_SYNONYMS.get(token, ()))
    return frozenset(expanded)


def build_index(
    specs: Sequence[ToolSpec], rows: Iterable[Mapping[str, Any]]
) -> dict[str, _FieldTokens]:
    """Index tool contracts plus registry Arabic metadata by tool name."""
    arabic: dict[str, str] = {}
    tags: dict[str, tuple[str, ...]] = {}
    profiles: dict[str, tuple[str, ...]] = {}
    for row in rows:
        name = row.get("tool_name")
        if not isinstance(name, str) or not name:
            continue
        arabic.setdefault(name, str(row.get("description_ar") or ""))
        tags.setdefault(name, tuple(str(tag) for tag in row.get("tags") or []))
        profiles.setdefault(name, tuple(str(tag) for tag in row.get("task_profiles") or []))

    index: dict[str, _FieldTokens] = {}
    for spec in specs:
        name_tokens = _expand(tokenize(spec.name.replace("_", " ")))
        description_tokens = _expand(tokenize(spec.description))
        tag_tokens = _expand(tokenize(" ".join(tags.get(spec.name, ()))))
        profile_tokens = _expand(tokenize(" ".join(profiles.get(spec.name, ()))))
        description_ar = arabic.get(spec.name, "")
        if description_ar:
            description_tokens |= _expand(tokenize(description_ar))
        index[spec.name] = _FieldTokens(
            name=name_tokens,
            description=description_tokens,
            tags=tag_tokens,
            profiles=profile_tokens,
            description_ar=description_ar,
            description_en=spec.description,
        )
    return index


def route_task(
    task: str,
    *,
    specs: Sequence[ToolSpec],
    registry_path: str | None = None,
    limit: int = 5,
    compact: bool = False,
) -> dict[str, Any]:
    """Rank tools for ``task``; compact mode returns short, schema-free hits."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    limit = max(1, min(int(limit), 10))
    rows = iter_entries(load_registry(registry_path))
    index = build_index(specs, rows)
    tokens = tuple(sorted(_expand(tokenize(task))))
    hits: list[dict[str, Any]] = []
    for name, fields in index.items():
        matched: list[str] = []
        score = 0
        for token in tokens:
            weight = 0
            if token in fields.name:
                weight += 3
            if token in fields.description:
                weight += 2
            if token in fields.tags:
                weight += 2
            if token in fields.profiles:
                weight += 1
            if weight:
                score += weight
                matched.append(token)
        if score <= 0:
            continue
        hit: dict[str, Any] = {"name": name, "score": score}
        description = fields.description_ar or fields.description_en
        if compact:
            hit["description"] = description[:80]
        else:
            hit["description"] = description
            hit["description_ar"] = fields.description_ar
            hit["matched"] = matched
            hit["input_schema"] = next(
                (spec.input_schema for spec in specs if spec.name == name), {}
            )
        hits.append(hit)
    hits.sort(key=lambda item: (-int(item["score"]), str(item["name"])))
    return {
        "language": detect_language(task),
        "tokens": list(tokens),
        "compact": compact,
        "count": len(hits),
        "results": hits[:limit],
    }
