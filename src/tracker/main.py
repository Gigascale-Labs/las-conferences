"""Orchestrator: run all enabled sources across all tiers, merge, emit (spec section 1)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from tracker import emit, tier_a_feeds, tier_b_openreview, tier_c_pagediff
from tracker.filters import classify, load_keywords
from tracker.net import RobotsCache, build_user_agent
from tracker.state import SnapshotStore, State

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

MAX_CONSECUTIVE_FAILURES = 4
MAX_CONSECUTIVE_DIFF_EVENTS = 3


def load_config() -> tuple[dict, dict]:
    sources_cfg = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text())
    keywords = load_keywords(CONFIG_DIR / "keywords.yaml")
    return sources_cfg, keywords


def run(repo: str | None = None) -> None:
    sources_cfg, keywords = load_config()
    meta = sources_cfg.get("meta", {})
    user_agent = build_user_agent(meta["repo_url"], meta["maintainer_email"])
    robots = RobotsCache(user_agent)

    state = State(DATA_DIR / "seen.json")
    snapshots = SnapshotStore(DATA_DIR / "snapshots")
    summary = emit.RunSummary()
    repo = repo or os.environ.get("GITHUB_REPOSITORY")

    if repo:
        try:
            emit.ensure_labels(repo)
        except Exception:  # noqa: BLE001 - label setup is best-effort, never fatal
            pass

    all_findings: list[emit.Finding] = []

    for source in sources_cfg.get("sources", []):
        source_id = source["id"]
        if not source.get("enabled", True):
            summary.source_disabled(source_id)
            continue

        try:
            findings = _run_source(source, state, snapshots, keywords, user_agent, robots, summary, repo)
            state.record_success(source_id)
            state.set_failure_issue_open(source_id, False)
            all_findings.extend(findings)
        except Exception as exc:  # noqa: BLE001 - one broken source must never fail the run (ground rule 5)
            summary.source_failed(source_id, str(exc))
            failures = state.record_failure(source_id)
            if failures >= MAX_CONSECUTIVE_FAILURES and not state.failure_issue_open(source_id) and repo:
                created = emit.create_maintenance_issue(
                    f"[tracker] source {source_id} failing",
                    f"`{source_id}` has failed {failures} consecutive runs.\n\nLatest error:\n```\n{exc}\n```",
                    repo,
                )
                if created:
                    state.set_failure_issue_open(source_id, True)

    emit.append_csv(DATA_DIR / "venues.csv", all_findings)

    if repo:
        for finding in (f for f in all_findings if f.priority == "high"):
            try:
                emit.create_cfp_issue(finding, repo)
            except Exception as exc:  # noqa: BLE001 - keep going even if one issue creation fails
                summary.lines.append(f"- issue creation failed for {finding.url}: {exc}")

    state.save()
    summary.write()


def _run_source(source, state, snapshots, keywords, user_agent, robots, summary, repo) -> list[emit.Finding]:
    source_id = source["id"]
    tier = source["tier"]

    if tier == "A":
        if source.get("format") == "csv":
            items = tier_a_feeds.poll_csv_index(
                source_id, source["url"], user_agent=user_agent, robots=robots, state=state,
                id_field=source.get("csv_id_field", "full_name"),
            )
        else:
            items = tier_a_feeds.poll_feed(
                source_id, source["url"], user_agent=user_agent, robots=robots, state=state
            )
        findings = _classify_items(items, keywords)
        summary.source_ok(source_id, len(items), len(findings))
        return findings

    if tier == "B":
        items = tier_b_openreview.poll_venues(
            source_id, source.get("parents", []), source.get("years", []), state=state
        )
        findings = _classify_items(items, keywords)
        summary.source_ok(source_id, len(items), len(findings))
        return findings

    if tier == "C":
        first_run = snapshots.load(source_id) is None
        event = tier_c_pagediff.check_page(
            source_id, source["url"],
            user_agent=user_agent, robots=robots, snapshots=snapshots,
            selector=source.get("selector"),
        )
        noise_count = state.record_diff_event(source_id, event is not None)

        if first_run:
            summary.lines.append(f"- `{source_id}`: seeded snapshot (first run)")
            return []
        if event is None:
            summary.source_no_change(source_id)
            return []

        text_blob = "\n".join(event.added_lines)
        relevance = classify(source.get("name", source_id), text_blob, keywords)
        findings = []
        if relevance.relevant:
            findings.append(
                emit.Finding(
                    source_id=source_id,
                    tier="C",
                    title=f"Change detected: {source.get('name', source_id)}",
                    url=source["url"],
                    matched_topics=relevance.matched_topics,
                    priority=relevance.priority,
                    notes=text_blob[:500],
                )
            )
        summary.source_ok(source_id, 1, len(findings))

        if noise_count > MAX_CONSECUTIVE_DIFF_EVENTS and not state.diff_noise_issue_open(source_id) and repo:
            created = emit.create_maintenance_issue(
                f"[tracker] source {source_id} diff noise",
                f"`{source_id}` has produced a non-empty diff on {noise_count} consecutive runs — "
                "likely a dynamic page element surviving normalisation. Consider adding a tighter "
                "`selector:` for this source in config/sources.yaml.",
                repo,
            )
            if created:
                state.set_diff_noise_issue_open(source_id, True)
        return findings

    raise ValueError(f"unknown tier {tier!r} for source {source_id}")


def _classify_items(items, keywords) -> list[emit.Finding]:
    findings = []
    for item in items:
        relevance = classify(item.title, item.summary, keywords)
        if not relevance.relevant:
            continue
        findings.append(
            emit.Finding(
                source_id=item.source_id,
                tier=item.tier,
                title=item.title,
                url=item.url,
                matched_topics=relevance.matched_topics,
                priority=relevance.priority,
                published_date=item.published_date,
            )
        )
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None, help="owner/repo, defaults to $GITHUB_REPOSITORY")
    args = parser.parse_args()
    run(repo=args.repo)


if __name__ == "__main__":
    main()
