"""Persistent state for the tracker: seen item IDs and per-source health counters.

State lives in data/seen.json (committed by the workflow at the end of each run,
since GitHub Actions runners are ephemeral). Snapshot text for Tier C sources lives
alongside as separate files under data/snapshots/<id>.txt.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _empty_source_state() -> dict[str, Any]:
    return {
        "seen_ids": [],
        "consecutive_failures": 0,
        "consecutive_diff_events": 0,
        "failure_issue_open": False,
        "diff_noise_issue_open": False,
        "etag": None,
        "last_modified": None,
    }


class State:
    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self._data: dict[str, Any] = json.loads(path.read_text())
        else:
            self._data = {"sources": {}}
        self._data.setdefault("sources", {})

    def _source(self, source_id: str) -> dict[str, Any]:
        return self._data["sources"].setdefault(source_id, _empty_source_state())

    # -- seen items (Tier A / Tier B) ----------------------------------------

    def is_seen(self, source_id: str, item_id: str) -> bool:
        return item_id in self._source(source_id)["seen_ids"]

    def mark_seen(self, source_id: str, item_id: str) -> None:
        seen = self._source(source_id)["seen_ids"]
        if item_id not in seen:
            seen.append(item_id)

    # -- failure tracking -----------------------------------------------------

    def record_success(self, source_id: str) -> None:
        self._source(source_id)["consecutive_failures"] = 0

    def record_failure(self, source_id: str) -> int:
        src = self._source(source_id)
        src["consecutive_failures"] += 1
        return src["consecutive_failures"]

    def failure_issue_open(self, source_id: str) -> bool:
        return self._source(source_id)["failure_issue_open"]

    def set_failure_issue_open(self, source_id: str, value: bool) -> None:
        self._source(source_id)["failure_issue_open"] = value

    # -- Tier C diff-noise tracking --------------------------------------------

    def record_diff_event(self, source_id: str, had_diff: bool) -> int:
        src = self._source(source_id)
        if had_diff:
            src["consecutive_diff_events"] += 1
        else:
            src["consecutive_diff_events"] = 0
        return src["consecutive_diff_events"]

    def diff_noise_issue_open(self, source_id: str) -> bool:
        return self._source(source_id)["diff_noise_issue_open"]

    def set_diff_noise_issue_open(self, source_id: str, value: bool) -> None:
        self._source(source_id)["diff_noise_issue_open"] = value

    # -- conditional GET caching ----------------------------------------------

    def get_etag(self, source_id: str) -> str | None:
        return self._source(source_id)["etag"]

    def set_etag(self, source_id: str, value: str | None) -> None:
        self._source(source_id)["etag"] = value

    def get_last_modified(self, source_id: str) -> str | None:
        return self._source(source_id)["last_modified"]

    def set_last_modified(self, source_id: str, value: str | None) -> None:
        self._source(source_id)["last_modified"] = value

    # -- persistence ------------------------------------------------------------

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")


class SnapshotStore:
    """Tier C normalised-page-text snapshots, one file per source."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, source_id: str) -> Path:
        return self.directory / f"{source_id}.txt"

    def load(self, source_id: str) -> str | None:
        path = self._path(source_id)
        return path.read_text() if path.exists() else None

    def save(self, source_id: str, text: str) -> None:
        self._path(source_id).write_text(text)
