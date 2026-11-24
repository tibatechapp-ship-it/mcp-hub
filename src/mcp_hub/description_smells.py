"""Six-code ("hex") checker for catalog description quality.

Every check is deterministic and local; no LLM, network or invented quality
score. The six codes are stable so a report can be diffed across registry
updates: S1 empty, S2 too short, S3 placeholder, S4 secret-shaped, S5 vague,
S6 unmarked language mixing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .catalog_query import iter_entries, load_registry
from .redact import redact

__all__ = ["SMELL_CODES", "check_description", "audit_registry"]

SMELL_CODES: tuple[str, ...] = (
    "S1_EMPTY",
    "S2_TOO_SHORT",
    "S3_PLACEHOLDER",
    "S4_SECRET_SHAPED",
    "S5_VAGUE",
    "S6_MIXED_LANGUAGE",
)

_LABELS: dict[str, tuple[str, str]] = {
    "S1_EMPTY": ("الوصف فارغ", "empty description"),
    "S2_TOO_SHORT": ("الوصف قصير جدا", "description too short"),
    "S3_PLACEHOLDER": ("نص نائب أو غير مكتمل", "placeholder or unfinished text"),
    "S4_SECRET_SHAPED": ("شكل يشبه سرا أو توكن", "secret-shaped text"),
    "S5_VAGUE": ("وصف عام بلا تفاصيل", "vague description"),
    "S6_MIXED_LANGUAGE": ("خلط لغات غير معلن", "unmarked language mixing"),
}

_PLACEHOLDER = re.compile(
    r"(?i)\b(todo|tbd|fixme|lorem ipsum|placeholder|coming soon)\b"
    r"|قيد التطوير|غير مكتمل|نص تجريبي|مثال فقط"
)
_VAGUE = re.compile(
    r"(?i)^(?:a\s+|an\s+)?(?:tool|service|helper|utility|api|general(?:\s+purpose)?|"
    r"useful|اداة عامة|خدمة عامة|افادة)[.! ]*$"
)
_ARABIC = re.compile(r"[\u0600-\u06ff]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
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


def _detail(code: str, evidence: str) -> dict[str, str]:
    label_ar, label_en = _LABELS[code]
    return {"code": code, "label_ar": label_ar, "label_en": label_en, "evidence": evidence[:120]}


def check_description(text: object, *, language: str = "auto") -> dict[str, Any]:
    """Return pass/fail plus the triggered codes for one description."""
    value = "" if text is None else str(text)
    stripped = value.strip()
    smells: list[dict[str, str]] = []

    if not stripped:
        smells.append(_detail("S1_EMPTY", value))
    if 0 < len(stripped) < 20:
        smells.append(_detail("S2_TOO_SHORT", value))
    match = _PLACEHOLDER.search(value)
    if match:
        smells.append(_detail("S3_PLACEHOLDER", match.group(0)))
    if redact(value) != value or re.search(r"\b[A-Za-z0-9_\-]{24,}\b", value):
        smells.append(_detail("S4_SECRET_SHAPED", "redaction rule or long token matched"))
    if _VAGUE.match(stripped):
        smells.append(_detail("S5_VAGUE", value))

    has_arabic = bool(_ARABIC.search(value))
    latin_words = [word.lower() for word in _LATIN_WORD.findall(value)]
    foreign_latin = [word for word in latin_words if word not in _ALLOWED_LATIN]
    if language == "ar" and has_arabic and foreign_latin:
        smells.append(_detail("S6_MIXED_LANGUAGE", ", ".join(foreign_latin[:3])))
    elif language == "en" and has_arabic:
        smells.append(_detail("S6_MIXED_LANGUAGE", "Arabic text in English field"))
    elif language == "auto" and has_arabic and foreign_latin:
        smells.append(_detail("S6_MIXED_LANGUAGE", ", ".join(foreign_latin[:3])))

    codes = [item["code"] for item in smells]
    return {
        "pass": not smells,
        "codes": codes,
        "smells": smells,
        "checked": list(SMELL_CODES),
    }


def audit_registry(*, registry_path: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Audit bundled server/tool descriptions; no arbitrary client-side paths."""
    limit = max(1, min(int(limit), 200))
    items: list[dict[str, Any]] = []
    by_code = {code: 0 for code in SMELL_CODES}
    total = 0
    for row in iter_entries(load_registry(registry_path)):
        field = "description_ar" if row.get("description_ar") else "description"
        report = check_description(row.get(field), language="ar" if field.endswith("_ar") else "en")
        total += 1
        for code in report["codes"]:
            by_code[code] += 1
        if report["codes"]:
            items.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "field": field,
                    "codes": report["codes"],
                }
            )
        if total >= limit:
            break
    return {
        "checked": total,
        "clean": total - len(items),
        "flagged": len(items),
        "by_code": by_code,
        "items": items,
    }


def summarize(items: Mapping[str, Any]) -> str:  # pragma: no cover - small helper for CLI/docs
    """Human-readable one-liner; kept separate from the JSON report."""
    return (
        f"checked={items.get('checked', 0)} "
        f"clean={items.get('clean', 0)} flagged={items.get('flagged', 0)}"
    )
