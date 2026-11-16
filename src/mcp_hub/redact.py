"""Secret redaction for error strings and log lines (v1.3.0).

Any exception message, URL or clipboard-style detail that reaches a user, a log
file or an alert payload goes through :func:`redact` first. The goal is narrow
and testable: a stack trace must never print an API key, bearer token, password
or PEM block.

Rules run most-specific first so a provider token is replaced as a whole rather
than partially mangled by the generic ``key=value`` rule.
"""

from __future__ import annotations

import re

__all__ = ["REDACTED", "redact"]

REDACTED = "<REDACTED>"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "-----BEGIN <REDACTED> PRIVATE KEY-----",
    ),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "gh<REDACTED>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "github_pat_<REDACTED>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA<REDACTED>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "xox-<REDACTED>"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "AIza<REDACTED>"),
    (re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)\S+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9_\-./+]{8,}"), r"\1" + REDACTED),
    (re.compile(r"(?i)(x-api-key\s*:\s*)\S+"), r"\1" + REDACTED),
    (
        re.compile(
            r"(?i)([?&](?:api[_-]?key|key|token|secret|password|passwd|"
            r"access[_-]?token|refresh[_-]?token|client[_-]?secret|signature|sig)=)"
            r"[^&\s#]+"
        ),
        r"\1" + REDACTED,
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|"
            r"access[_-]?token|refresh[_-]?token|client[_-]?secret)"
            r"\s*[=:]\s*['\"]?([^\s'\",&]{6,})"
        ),
        r"\1=" + REDACTED,
    ),
    (re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^/\s:]+:)[^@\s]+(@)"), r"\1" + REDACTED + r"\2"),
]


def redact(text: object) -> str:
    """Return ``text`` as a string with every recognised secret replaced."""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:  # noqa: BLE001 - a broken __str__ must not mask the error
            return "<unprintable>"
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
