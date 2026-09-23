from __future__ import annotations

import re


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", text) is not None


def skip_reason(p: dict, exclude: dict) -> str | None:
    """Why a posting should not be scored, or None if it should."""
    title = p["title"].lower()
    for kw in exclude.get("title_keywords") or []:
        if _has_word(title, kw):
            return f"title: {kw}"
    etype = p["employment_type"].lower()
    for t in exclude.get("employment_types") or []:
        if t.lower() in etype:
            return f"type: {t}"
    company = p["company"].lower()
    for c in exclude.get("companies") or []:
        if c.lower() == company:
            return f"company: {c}"
    desc = p["description"].lower()
    for kw in exclude.get("description_keywords") or []:
        if _has_word(desc, kw):
            return f"description: {kw}"
    return None
