"""Dedup state (SPEC.md section 6): remembers which candidate URLs have already
been reported, so rediscovering the same event next week doesn't re-report it.
Lives in data/seen.json, committed by the workflow each run (Actions runners
are ephemeral).
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip().lower())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class SeenStore:
    def __init__(self, path: Path):
        self.path = path
        self._seen: set[str] = set(json.loads(path.read_text())) if path.exists() else set()

    def is_seen(self, url: str) -> bool:
        return normalize_url(url) in self._seen

    def mark_seen(self, url: str) -> None:
        self._seen.add(normalize_url(url))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._seen), indent=2) + "\n")
