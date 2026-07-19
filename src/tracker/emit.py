"""Outputs (spec section 6): CSV append, GitHub issue creation, run summary."""
from __future__ import annotations

import csv
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CSV_COLUMNS = [
    "date_seen",
    "source_id",
    "tier",
    "title",
    "url",
    "matched_topics",
    "priority",
    "published_date",
    "notes",
]

ISSUE_LABELS = {
    "cfp": "d73a4a",
    "tracker-maintenance": "ededed",
    "multi_agent": "0e8a16",
    "digital_twin": "1d76db",
    "comp_econ": "fbca04",
    "css": "5319e7",
    "llm_evals": "b60205",
}


@dataclass
class Finding:
    source_id: str
    tier: str
    title: str
    url: str
    matched_topics: list[str] = field(default_factory=list)
    priority: str = "normal"
    published_date: str = ""
    notes: str = ""


def append_csv(path: Path, findings: list[Finding]) -> None:
    if not findings:
        return
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        today = datetime.now(timezone.utc).date().isoformat()
        for finding in findings:
            writer.writerow(
                {
                    "date_seen": today,
                    "source_id": finding.source_id,
                    "tier": finding.tier,
                    "title": finding.title,
                    "url": finding.url,
                    "matched_topics": ";".join(finding.matched_topics),
                    "priority": finding.priority,
                    "published_date": finding.published_date,
                    "notes": finding.notes,
                }
            )


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout


def ensure_labels(repo: str) -> None:
    try:
        existing = {item["name"] for item in json.loads(_gh("label", "list", "--repo", repo, "--json", "name"))}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return
    for name, color in ISSUE_LABELS.items():
        if name in existing:
            continue
        try:
            _gh("label", "create", name, "--repo", repo, "--color", color)
        except subprocess.CalledProcessError:
            pass  # non-fatal: issue creation still works without a custom color


def _open_issue_bodies_matching(search: str, repo: str) -> list[dict]:
    out = _gh(
        "issue", "list", "--repo", repo, "--state", "open",
        "--search", search, "--json", "title,body",
    )
    return json.loads(out)


def create_cfp_issue(finding: Finding, repo: str) -> bool:
    """Create a [CFP] issue for a high-priority finding, deduped by URL. Returns
    True if an issue was created, False if one already existed for this URL."""
    try:
        if any(finding.url in (item.get("body") or "") for item in _open_issue_bodies_matching(finding.url, repo)):
            return False
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pass  # search failed open: proceed to create rather than silently drop the finding

    body_lines = [
        f"**Link:** {finding.url}",
        f"**Source:** {finding.source_id} (tier {finding.tier})",
        f"**Matched topics:** {', '.join(finding.matched_topics)}",
    ]
    if finding.published_date:
        body_lines.append(f"**Published:** {finding.published_date}")
    if finding.notes:
        body_lines.append(f"**Notes:** {finding.notes}")

    labels = ["cfp"] + [t for t in finding.matched_topics if t in ISSUE_LABELS]
    _gh(
        "issue", "create", "--repo", repo,
        "--title", f"[CFP] {finding.title}",
        "--body", "\n".join(body_lines),
        "--label", ",".join(labels),
    )
    return True


def create_maintenance_issue(title: str, body: str, repo: str) -> bool:
    """Create a tracker-maintenance issue, deduped by exact title match."""
    try:
        if any(item.get("title") == title for item in _open_issue_bodies_matching(title, repo)):
            return False
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        pass

    _gh(
        "issue", "create", "--repo", repo,
        "--title", title, "--body", body, "--label", "tracker-maintenance",
    )
    return True


class RunSummary:
    def __init__(self):
        self.lines: list[str] = ["# LAS Venue Tracker — run summary", ""]

    def source_ok(self, source_id: str, new_items: int, relevant_items: int) -> None:
        self.lines.append(f"- `{source_id}`: {new_items} new item(s), {relevant_items} relevant")

    def source_not_modified(self, source_id: str) -> None:
        self.lines.append(f"- `{source_id}`: not modified (304)")

    def source_no_change(self, source_id: str) -> None:
        self.lines.append(f"- `{source_id}`: no change")

    def source_failed(self, source_id: str, error: str) -> None:
        self.lines.append(f"- `{source_id}`: **FAILED** — {error}")

    def source_disabled(self, source_id: str) -> None:
        self.lines.append(f"- `{source_id}`: disabled, skipped")

    def write(self, path: str | None = None) -> None:
        path = path or os.environ.get("GITHUB_STEP_SUMMARY")
        text = "\n".join(self.lines) + "\n"
        if not path:
            print(text)
            return
        with open(path, "a") as f:
            f.write(text)
