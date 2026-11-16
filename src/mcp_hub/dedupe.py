from __future__ import annotations

from difflib import SequenceMatcher

from .normalize import normalize_name, normalize_repo_url, normalize_url


def compare(a: dict, b: dict) -> tuple[str, str]:
    """Return (classification, reason): duplicate|related|distinct."""
    # Strong identity matches
    if a.get("registry_id") and a.get("registry_id") == b.get("registry_id"):
        return "duplicate", "same registry_id"
    ra = normalize_repo_url(a.get("repository_url"))
    rb = normalize_repo_url(b.get("repository_url"))
    if ra and rb and ra == rb:
        return "duplicate", "same repository_url"
    ea = normalize_url(a.get("endpoint_url"))
    eb = normalize_url(b.get("endpoint_url"))
    if ea and eb and ea == eb:
        return "duplicate", "same endpoint_url"

    na = normalize_name(a.get("name"))
    nb = normalize_name(b.get("name"))
    pa = (a.get("publisher") or "").strip().lower()
    pb = (b.get("publisher") or "").strip().lower()

    if na and nb and na == nb and pa and pb and pa == pb:
        return "duplicate", "same publisher+name"

    ratio = SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0
    if ratio >= 0.92 and pa and pb and pa == pb:
        return "related", f"similar name ({ratio:.2f}) same publisher"
    if ratio >= 0.97:
        return "related", f"very similar name ({ratio:.2f})"
    if ra and rb and ra.split("/")[-1] == rb.split("/")[-1]:
        return "related", "same repo slug"

    return "distinct", "no strong match"
