from unittest.mock import patch

from tracker.discover import Candidate
from tracker.net import FetchError, FetchResult, RobotsCache
from tracker.verify import BLOCKED, REJECTED, VERIFIED, verify


def _candidate(name="AAMAS 2027 Workshop on Agent Economies", url="https://example.org/event") -> Candidate:
    return Candidate(
        query="test query",
        name=name,
        url=url,
        event_type="workshop",
        dates="2027-05-10",
        location="Auckland",
        organizer="IFAAMAS",
        description="A workshop on agent-based economic simulation.",
        relevance_rationale="r",
        reputability_rationale="rep",
    )


def test_verified_when_page_contains_name_tokens():
    candidate = _candidate()
    page = "<html>Welcome to the AAMAS 2027 Workshop on Agent Economies, hosted in Auckland.</html>"
    with patch("tracker.verify.fetch", return_value=FetchResult(200, page)):
        result = verify(candidate, user_agent="test-agent", robots=RobotsCache("test-agent"))

    assert result.status == VERIFIED


def test_rejected_when_page_does_not_mention_event():
    candidate = _candidate()
    page = "<html>This page is about something completely unrelated.</html>"
    with patch("tracker.verify.fetch", return_value=FetchResult(200, page)):
        result = verify(candidate, user_agent="test-agent", robots=RobotsCache("test-agent"))

    assert result.status == REJECTED
    assert "not found" in result.reason


def test_blocked_not_rejected_when_url_unreachable():
    """A fetch failure (robots.txt, 403, timeout) says nothing about whether
    the event is real — it must be kept and flagged, not dropped as if the
    model had hallucinated it."""
    candidate = _candidate()
    with patch("tracker.verify.fetch", side_effect=FetchError("connection refused")):
        result = verify(candidate, user_agent="test-agent", robots=RobotsCache("test-agent"))

    assert result.status == BLOCKED
    assert "could not fetch" in result.reason


def test_blocked_when_name_too_short_to_check():
    candidate = _candidate(name="AI")
    with patch("tracker.verify.fetch", return_value=FetchResult(200, "AI is mentioned here")):
        result = verify(candidate, user_agent="test-agent", robots=RobotsCache("test-agent"))

    assert result.status == BLOCKED
    assert "too short" in result.reason
