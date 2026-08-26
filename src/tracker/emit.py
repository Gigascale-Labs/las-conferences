"""Outputs (SPEC.md section 7): CSV append, one weekly digest GitHub issue, run
summary.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tracker.verify import VerificationResult

CSV_COLUMNS = [
    "date_found",
    "name",
    "event_type",
    "dates",
    "location",
    "organizer",
    "url",
    "query",
    "relevance_rationale",
    "reputability_rationale",
    "verification_note",
]


def append_csv(path: Path, accepted: list[VerificationResult]) -> None:
    if not accepted:
        return
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if is_new:
            writer.writeheader()
        today = datetime.now(timezone.utc).date().isoformat()
        for result in accepted:
            c = result.candidate
            writer.writerow(
                {
                    "date_found": today,
                    "name": c.name,
                    "event_type": c.event_type,
                    "dates": c.dates,
                    "location": c.location,
                    "organizer": c.organizer,
                    "url": c.url,
                    "query": c.query,
                    "relevance_rationale": c.relevance_rationale,
                    "reputability_rationale": c.reputability_rationale,
                    "verification_note": result.reason,
                }
            )


def _gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout


def create_maintenance_issue(title: str, body: str, repo: str) -> bool:
    """A tracker-maintenance issue, deduped by exact open-title match so a
    still-broken run doesn't open a new issue every week."""
    try:
        existing = json.loads(
            _gh("issue", "list", "--repo", repo, "--state", "open", "--search", title, "--json", "title")
        )
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        existing = []
    if any(item.get("title") == title for item in existing):
        return False

    _gh(
        "issue", "create", "--repo", repo,
        "--title", title, "--body", body, "--label", "tracker-maintenance",
    )
    return True


def create_digest_issue(accepted: list[VerificationResult], repo: str) -> None:
    """One issue per run listing every accepted item (SPEC.md section 7). Only
    called by main.py when accepted is non-empty — no issue on an empty week.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"{len(accepted)} new item(s) passed relevance, reputability, and URL "
        "verification this run.",
        "",
        "| Name | Type | Dates | Organizer | Link |",
        "|---|---|---|---|---|",
    ]
    for result in accepted:
        c = result.candidate
        lines.append(f"| {c.name} | {c.event_type} | {c.dates} | {c.organizer} | {c.url} |")

    lines.append("")
    lines.append("<details><summary>Rationale per item</summary>")
    lines.append("")
    for result in accepted:
        c = result.candidate
        lines.append(f"**{c.name}**")
        lines.append(f"- Relevance: {c.relevance_rationale}")
        lines.append(f"- Reputability: {c.reputability_rationale}")
        lines.append(f"- Verification: {result.reason}")
        lines.append("")
    lines.append("</details>")

    _gh(
        "issue", "create", "--repo", repo,
        "--title", f"[weekly-scan] {len(accepted)} new item(s) — {today}",
        "--body", "\n".join(lines),
        "--label", "cfp",
    )


class RunSummary:
    def __init__(self):
        self.lines: list[str] = ["# LAS venue tracker — run summary", ""]

    def query_ok(self, query: str, candidate_count: int, cost_usd: float | None = None) -> None:
        cost_note = f", ${cost_usd:.4f}" if cost_usd is not None else ", cost unknown"
        self.lines.append(f"- query `{query}`: {candidate_count} candidate(s){cost_note}")

    def query_failed(self, query: str, error: str) -> None:
        self.lines.append(f"- query `{query}`: **FAILED** — {error}")

    def candidate_skipped(self, candidate, reason: str) -> None:
        self.lines.append(f"  - skipped `{candidate.name}` ({candidate.url}): {reason}")

    def candidate_rejected(self, candidate, reason: str) -> None:
        self.lines.append(f"  - rejected `{candidate.name}` ({candidate.url}): {reason}")

    def candidate_accepted(self, candidate) -> None:
        self.lines.append(f"  - accepted `{candidate.name}` ({candidate.url})")

    def write(self, path: str | None = None) -> None:
        text = "\n".join(self.lines) + "\n"
        print(text)  # always visible in the plain step log, not just the job summary panel
        path = path or os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a") as f:
                f.write(text)
