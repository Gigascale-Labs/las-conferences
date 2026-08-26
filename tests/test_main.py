import json
from unittest.mock import patch

import pytest
import yaml

from tracker import db, main
from tracker.discover import Candidate, DiscoveryError, QueryResult
from tracker.verify import BLOCKED, REJECTED, VERIFIED, VerificationResult

SCOPE = {
    "meta": {"repo_url": "https://example.org/repo", "maintainer_email": "test@example.org"},
    "model": {"id": "test/model", "max_output_tokens": 1000},
    "search": {
        "max_results_per_query": 5,
        "max_candidates_per_query": 8,
        "queries": ["query one", "query two"],
    },
    "relevance": {"description": "test relevance"},
    "reputability": {"criteria": "test reputability"},
}


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "scope.yaml"
    config_path.write_text(yaml.safe_dump(SCOPE))
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(main, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def _candidate(name="Test Workshop", url="https://example.org/event") -> Candidate:
    return Candidate(
        query="q", name=name, url=url, event_type="workshop", dates="", location="",
        organizer="Test Org", description="d", relevance_rationale="r", reputability_rationale="rep",
    )


def _query_result(candidates: list[Candidate]) -> QueryResult:
    return QueryResult(candidates=candidates, prompt_tokens=100, completion_tokens=20, cost_usd=0.007)


def test_all_queries_failing_exits_nonzero_without_creating_issue_in_dry_run():
    with patch("tracker.main.discover.run_query", side_effect=DiscoveryError("401 unauthorized")):
        with pytest.raises(SystemExit) as exc_info:
            main.run(repo="owner/repo", dry_run=True)

    assert exc_info.value.code == 1


def test_all_queries_failing_opens_maintenance_issue_when_not_dry_run():
    with patch("tracker.main.discover.run_query", side_effect=DiscoveryError("401 unauthorized")):
        with patch("tracker.main.emit.create_maintenance_issue") as mock_issue:
            with pytest.raises(SystemExit):
                main.run(repo="owner/repo", dry_run=False)

    mock_issue.assert_called_once()
    assert "all search queries failed" in mock_issue.call_args.args[0]


def test_partial_query_failure_does_not_exit():
    def fake_run_query(query, **kwargs):
        if query == "query one":
            raise DiscoveryError("timeout")
        return _query_result([_candidate()])

    with patch("tracker.main.discover.run_query", side_effect=fake_run_query):
        with patch("tracker.main.verify", return_value=VerificationResult(_candidate(), VERIFIED, "ok")):
            main.run(repo=None, dry_run=True)  # must not raise


def test_dry_run_does_not_write_db_feed_or_seen_state():
    with patch("tracker.main.discover.run_query", return_value=_query_result([_candidate()])):
        with patch("tracker.main.verify", return_value=VerificationResult(_candidate(), VERIFIED, "ok")):
            main.run(repo=None, dry_run=True)

    assert not (main.DATA_DIR / "discoveries.db").exists()
    assert not (main.DOCS_DIR / "events.json").exists()
    assert not (main.DATA_DIR / "seen.json").exists()


def test_real_run_writes_db_feed_and_seen_state():
    with patch("tracker.main.discover.run_query", return_value=_query_result([_candidate()])):
        with patch("tracker.main.verify", return_value=VerificationResult(_candidate(), VERIFIED, "ok")):
            main.run(repo=None, dry_run=False)

    assert (main.DATA_DIR / "discoveries.db").exists()
    assert (main.DOCS_DIR / "events.json").exists()
    assert (main.DATA_DIR / "seen.json").exists()

    conn = db.connect(main.DATA_DIR / "discoveries.db")
    events = db.fetch_all_events(conn)
    conn.close()
    assert len(events) == 1
    assert events[0]["name"] == "Test Workshop"
    assert events[0]["id"]  # a uuid was assigned
    assert events[0]["date_scraped"]


def test_real_run_creates_digest_issue_only_when_repo_and_accepted_given():
    with patch("tracker.main.discover.run_query", return_value=_query_result([_candidate()])):
        with patch("tracker.main.verify", return_value=VerificationResult(_candidate(), VERIFIED, "ok")):
            with patch("tracker.main.emit.create_digest_issue") as mock_digest:
                main.run(repo="owner/repo", dry_run=False)

    mock_digest.assert_called_once()


def test_blocked_candidate_is_kept_not_dropped():
    """A robots.txt/403/timeout block says nothing about whether the event is
    real, so it must still reach the DB and feed — just marked as
    unverified."""
    with patch("tracker.main.discover.run_query", return_value=_query_result([_candidate()])):
        with patch(
            "tracker.main.verify",
            return_value=VerificationResult(_candidate(), BLOCKED, "could not fetch page: 403"),
        ):
            main.run(repo=None, dry_run=False)

    conn = db.connect(main.DATA_DIR / "discoveries.db")
    events = db.fetch_all_events(conn)
    conn.close()
    assert len(events) == 1
    assert events[0]["verification_status"] == BLOCKED
    assert events[0]["name"] == "Test Workshop"

    feed_events = json.loads((main.DOCS_DIR / "events.json").read_text())["events"]
    assert feed_events[0]["verification_status"] == BLOCKED


def test_rejected_candidate_is_dropped_entirely():
    """A page that loads but doesn't mention the claimed event is the actual
    hallucination signal — it must not reach the DB or the digest issue."""
    with patch("tracker.main.discover.run_query", return_value=_query_result([_candidate()])):
        with patch(
            "tracker.main.verify",
            return_value=VerificationResult(_candidate(), REJECTED, "event name not found on its own URL"),
        ):
            with patch("tracker.main.emit.create_digest_issue") as mock_digest:
                main.run(repo="owner/repo", dry_run=False)

    conn = db.connect(main.DATA_DIR / "discoveries.db")
    events = db.fetch_all_events(conn)
    conn.close()
    assert events == []
    mock_digest.assert_not_called()
