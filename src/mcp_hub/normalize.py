from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_WS = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    return _WS.sub(" ", (value or "").strip()).lower()


def normalize_name(value: str | None) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WS.sub(" ", text).strip()


def normalize_url(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return raw.rstrip("/")
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def normalize_repo_url(value: str | None) -> str:
    return normalize_url(value)


def normalize_categories(value: str | list[str] | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        items = [v.strip().lower() for v in value.split(",")]
    else:
        items = [str(v).strip().lower() for v in value]
    seen = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return ",".join(seen)
