"""Orchestrator (SPEC.md section 1): run every configured search query through
OpenRouter, dedupe, verify each candidate for real, then emit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from tracker import discover, emit
from tracker.discover import DiscoveryError
from tracker.net import RobotsCache, build_user_agent
from tracker.state import SeenStore, normalize_url
from tracker.verify import verify

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "scope.yaml"
DATA_DIR = ROOT / "data"


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
            "**DRY RUN** — data/discoveries.csv, data/seen.json, and the GitHub "
            "issue below are NOT written. This is what would have happened."
        )

    all_candidates: list[discover.Candidate] = []
    for query in search_cfg["queries"]:
        try:
            candidates = discover.run_query(
                query,
                api_key=api_key,
                model=model_cfg["id"],
                relevance=relevance,
                reputability=reputability,
                max_results=search_cfg["max_results_per_query"],
                max_output_tokens=model_cfg["max_output_tokens"],
            )
        except DiscoveryError as exc:
            summary.query_failed(query, str(exc))
            continue
        summary.query_ok(query, len(candidates))
        all_candidates.extend(candidates)

    accepted = []
    for candidate in _dedupe_within_run(all_candidates):
        if seen.is_seen(candidate.url):
            summary.candidate_skipped(candidate, "already reported in a previous run")
            continue

        result = verify(candidate, user_agent=user_agent, robots=robots)
        if not result.verified:
            summary.candidate_rejected(candidate, result.reason)
            continue

        summary.candidate_accepted(candidate)
        accepted.append(result)
        seen.mark_seen(candidate.url)

    if dry_run:
        if accepted:
            summary.lines.append(f"- would append {len(accepted)} row(s) to data/discoveries.csv")
            summary.lines.append(f"- would open digest issue: [weekly-scan] {len(accepted)} new item(s)")
        summary.write()
        return

    emit.append_csv(DATA_DIR / "discoveries.csv", accepted)

    if repo and accepted:
        try:
            emit.create_digest_issue(accepted, repo)
        except Exception as exc:  # noqa: BLE001 - a failed issue post must not lose the CSV write above
            summary.lines.append(f"- digest issue creation failed: {exc}")

    seen.save()
    summary.write()


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


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
