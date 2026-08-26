"""Outputs (SPEC.md section 7): one weekly digest GitHub issue, run summary.
The persistent event log itself lives in tracker.db / tracker.feed.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from tracker.verify import BLOCKED, VerificationResult


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


def _item_table(results: list[VerificationResult]) -> list[str]:
    lines = [
        "| Name | Type | Dates | Location | Description | Organizer | Link |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        c = result.candidate
        lines.append(
            f"| {c.name} | {c.event_type} | {c.dates} | {c.location} | {c.description} "
            f"| {c.organizer} | {c.url} |"
        )
    return lines


def create_digest_issue(kept: list[VerificationResult], repo: str) -> None:
    """One issue per run listing every kept item (SPEC.md section 7). Only
    called by main.py when kept is non-empty — no issue on an empty week.
    VERIFIED and BLOCKED results (SPEC.md section 5) get separate tables:
    a BLOCKED item's page couldn't be fetched to confirm it, so it's flagged
    as possibly relevant rather than presented as confirmed.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    verified = [r for r in kept if r.status != BLOCKED]
    blocked = [r for r in kept if r.status == BLOCKED]

    lines = [
        f"{len(verified)} new item(s) passed relevance, reputability, and URL "
        f"verification this run; {len(blocked)} more matched relevance and "
        "reputability but could not be independently verified (see below).",
        "",
    ]
    if verified:
        lines.append("### Verified")
        lines.append("")
        lines.extend(_item_table(verified))
        lines.append("")
    if blocked:
        lines.append("### Possibly relevant — not verified (page fetch was blocked)")
        lines.append("")
        lines.append(
            "These matched the relevance/reputability bars, but their URL "
            "could not be fetched to confirm the event actually exists as "
            "described (robots.txt disallow, 403, timeout, or similar) — "
            "check manually before treating them as confirmed."
        )
        lines.append("")
        lines.extend(_item_table(blocked))
        lines.append("")

    lines.append("<details><summary>Rationale per item</summary>")
    lines.append("")
    for result in kept:
        c = result.candidate
        lines.append(f"**{c.name}**" + (" _(unverified — blocked)_" if result.status == BLOCKED else ""))
        lines.append(f"- Relevance: {c.relevance_rationale}")
        lines.append(f"- Reputability: {c.reputability_rationale}")
        lines.append(f"- Verification: {result.reason}")
        lines.append("")
    lines.append("</details>")

    _gh(
        "issue", "create", "--repo", repo,
        "--title", f"[weekly-scan] {len(verified)} new item(s), {len(blocked)} unverified — {today}",
        "--body", "\n".join(lines),
        "--label", "cfp",
    )


class RunSummary:
    def __init__(self, title: str = "LAS venue tracker — run summary"):
        self.lines: list[str] = [f"# {title}", ""]

    def query_ok(self, query: str, candidate_count: int, cost_usd: float | None = None) -> None:
        cost_note = f", ${cost_usd:.4f}" if cost_usd is not None else ", cost unknown"
        self.lines.append(f"- query `{query}`: {candidate_count} candidate(s){cost_note}")

    def query_failed(self, query: str, error: str) -> None:
        self.lines.append(f"- query `{query}`: **FAILED** — {error}")

    def candidate_skipped(self, candidate, reason: str) -> None:
        self.lines.append(f"  - skipped `{candidate.name}` ({candidate.url}): {reason}")

    def candidate_rejected(self, candidate, reason: str) -> None:
        self.lines.append(f"  - rejected `{candidate.name}` ({candidate.url}): {reason}")

    def candidate_blocked(self, candidate, reason: str) -> None:
        self.lines.append(f"  - kept but UNVERIFIED `{candidate.name}` ({candidate.url}): {reason}")

    def candidate_accepted(self, candidate) -> None:
        self.lines.append(f"  - accepted `{candidate.name}` ({candidate.url})")

    def write(self, path: str | None = None) -> None:
        text = "\n".join(self.lines) + "\n"
        print(text)  # always visible in the plain step log, not just the job summary panel
        path = path or os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a") as f:
                f.write(text)
