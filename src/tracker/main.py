"""Orchestrator (SPEC.md section 1): run every configured search query through
OpenRouter, dedupe, verify each candidate for real, then emit.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from tracker import db, discover, emit, feed
from tracker.discover import DiscoveryError
from tracker.net import RobotsCache, build_user_agent
from tracker.state import SeenStore, normalize_url
from tracker.verify import BLOCKED, REJECTED, verify

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "scope.yaml"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _dedupe_within_run(candidates: list[discover.Candidate]) -> list[discover.Candidate]:
    by_url: dict[str, discover.Candidate] = {}
    for candidate in candidates:
        by_url.setdefault(normalize_url(candidate.url), candidate)
    return list(by_url.values())


def run(repo: str | None = None, dry_run: bool = False) -> None:
    config = load_config()
    meta = config["meta"]
    model_cfg = config["model"]
    search_cfg = config["search"]
    relevance = config["relevance"]["description"]
    reputability = config["reputability"]["criteria"]

    api_key = os.environ["OPENROUTER_API_KEY"]
    user_agent = build_user_agent(meta["repo_url"], meta["maintainer_email"])
    robots = RobotsCache(user_agent)
    seen = SeenStore(DATA_DIR / "seen.json")
    summary = emit.RunSummary()
    repo = repo or os.environ.get("GITHUB_REPOSITORY")

    if dry_run:
        summary.lines.append(
            "**DRY RUN** — data/discoveries.db, docs/events.json, docs/events.xml, "
            "data/seen.json, and the GitHub issue below are NOT written. This is "
            "what would have happened."
        )

    queries = search_cfg["queries"]
    all_candidates: list[discover.Candidate] = []
    failures: list[str] = []
    total_cost_usd = 0.0
    cost_known_for_all_queries = True
    for query in queries:
        try:
            result = discover.run_query(
                query,
                api_key=api_key,
                model=model_cfg["id"],
                relevance=relevance,
                reputability=reputability,
                max_results=search_cfg["max_results_per_query"],
                max_output_tokens=model_cfg["max_output_tokens"],
                max_candidates=search_cfg["max_candidates_per_query"],
            )
        except DiscoveryError as exc:
            summary.query_failed(query, str(exc))
            failures.append(str(exc))
            continue
        summary.query_ok(query, len(result.candidates), result.cost_usd)
        all_candidates.extend(result.candidates)
        if result.cost_usd is None:
            cost_known_for_all_queries = False
        else:
            total_cost_usd += result.cost_usd

    if total_cost_usd or not cost_known_for_all_queries:
        suffix = "" if cost_known_for_all_queries else " (incomplete — some queries didn't report cost)"
        summary.lines.append(f"- total reported cost this run: ${total_cost_usd:.4f}{suffix}")

    # One bad query is tolerated (ground rule: a single flaky query must never
    # fail the whole run) but every query failing is a systemic problem (e.g.
    # a bad/missing OPENROUTER_API_KEY) that looks identical to "quiet week,
    # nothing new" unless flagged loudly here.
    if queries and len(failures) == len(queries):
        message = f"all {len(queries)} search queries failed this run — first error: {failures[0]}"
        summary.lines.append(f"**SYSTEMIC FAILURE**: {message}")
        if not dry_run and repo:
            try:
                emit.create_maintenance_issue(
                    "[tracker] all search queries failed",
                    f"{message}\n\nCheck OPENROUTER_API_KEY is set and valid.",
                    repo,
                )
            except Exception as exc:  # noqa: BLE001 - don't lose the summary write below over this
                summary.lines.append(f"- maintenance issue creation failed: {exc}")
        summary.write()
        sys.exit(1)

    # A candidate that fails verification splits two ways (SPEC.md section
    # 5): REJECTED (page loads, doesn't mention the claimed event — a real
    # hallucination signal) is dropped. BLOCKED (page couldn't be fetched at
    # all — robots.txt, 403, timeout) says nothing about whether the event is
    # real, so it's kept and flagged rather than dropped.
    kept = []
    for candidate in _dedupe_within_run(all_candidates):
        if seen.is_seen(candidate.url):
            summary.candidate_skipped(candidate, "already reported in a previous run")
            continue

        result = verify(candidate, user_agent=user_agent, robots=robots)
        if result.status == REJECTED:
            summary.candidate_rejected(candidate, result.reason)
            continue

        if result.status == BLOCKED:
            summary.candidate_blocked(candidate, result.reason)
        else:
            summary.candidate_accepted(candidate)
        kept.append(result)
        seen.mark_seen(candidate.url)

    _write_github_output("new_items", str(len(kept)))

    if dry_run:
        if kept:
            verified_count = sum(1 for r in kept if r.status != BLOCKED)
            summary.lines.append(f"- would insert {len(kept)} row(s) into data/discoveries.db")
            summary.lines.append(
                f"- would open digest issue: [weekly-scan] {verified_count} new item(s), "
                f"{len(kept) - verified_count} unverified"
            )
        summary.write()
        return

    conn = db.connect(DATA_DIR / "discoveries.db")
    try:
        db.insert_events(conn, kept)
        # Both public feeds, same rows, every run (SPEC.md section 7a).
        feed.write_json_feed(conn, DOCS_DIR / "events.json")
        feed.write_atom_feed(conn, DOCS_DIR / "events.xml")
    finally:
        conn.close()

    if repo and kept:
        try:
            emit.create_digest_issue(kept, repo)
        except Exception as exc:  # noqa: BLE001 - a failed issue post must not lose the DB write above
            summary.lines.append(f"- digest issue creation failed: {exc}")

    seen.save()
    summary.write()


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


def _write_github_output(key: str, value: str) -> None:
    """SQLite is binary, so the workflow's commit step can't recover an item
    count from `git diff --numstat` the way it could with the old CSV. Write
    it here instead, where we already have the real count."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        f.write(f"{key}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None, help="owner/repo, defaults to $GITHUB_REPOSITORY")
    parser.add_argument(
        "--dry-run", action="store_true", default=_env_flag("DRY_RUN"),
        help="skip writing data/ and creating an issue; defaults to $DRY_RUN",
    )
    args = parser.parse_args()
    run(repo=args.repo, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
