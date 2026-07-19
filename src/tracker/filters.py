"""Keyword matching / relevance scoring (see config/keywords.yaml, spec section 5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

META_GROUP = "meta"


def load_keywords(path: Path) -> dict[str, list[str]]:
    return yaml.safe_load(path.read_text()) or {}


@dataclass
class Relevance:
    matched_topics: list[str] = field(default_factory=list)
    priority: str | None = None  # "high", "normal", or None (not relevant)

    @property
    def relevant(self) -> bool:
        return bool(self.matched_topics)


def classify(title: str, summary: str, keywords: dict[str, list[str]]) -> Relevance:
    """Case-insensitive substring match on title + summary against each topic group.

    A match on any non-meta group makes an item relevant. A relevant item that also
    matches a `meta` term (e.g. "workshop", "call for papers") is high priority.
    """
    text = f"{title} {summary}".lower()

    matched = [
        group
        for group, terms in keywords.items()
        if group != META_GROUP and any(term.lower() in text for term in terms)
    ]
    if not matched:
        return Relevance()

    meta_terms = keywords.get(META_GROUP, [])
    is_high_priority = any(term.lower() in text for term in meta_terms)
    return Relevance(matched_topics=matched, priority="high" if is_high_priority else "normal")
