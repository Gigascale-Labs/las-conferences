import json
from unittest.mock import Mock, patch

import pytest

from tracker.discover import Candidate, DiscoveryError, run_query

CANDIDATE_KWARGS = dict(
    api_key="test-key",
    model="test/model",
    relevance="large-scale multi-agent systems",
    reputability="named organizer, live CFP",
    max_results=5,
    max_output_tokens=1000,
    max_candidates=8,
)


def _openrouter_response(candidates: list[dict]) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"candidates": candidates})}}]
    }
    return resp


def test_parses_candidates_from_response():
    candidate_payload = {
        "name": "AAMAS 2027 Workshop on Agent Economies",
        "url": "https://example.org/aamas2027-workshop",
        "event_type": "workshop",
        "dates": "2027-05-10",
        "location": "Auckland",
        "organizer": "IFAAMAS",
        "relevance_rationale": "Studies mechanism design for large agent populations.",
        "reputability_rationale": "Affiliated with the AAMAS conference series.",
    }
    with patch("tracker.discover.requests.post", return_value=_openrouter_response([candidate_payload])):
        candidates = run_query("agent economies workshop", **CANDIDATE_KWARGS)

    assert len(candidates) == 1
    assert isinstance(candidates[0], Candidate)
    assert candidates[0].name == "AAMAS 2027 Workshop on Agent Economies"
    assert candidates[0].query == "agent economies workshop"


def test_empty_candidates_list_returns_empty():
    with patch("tracker.discover.requests.post", return_value=_openrouter_response([])):
        candidates = run_query("no results query", **CANDIDATE_KWARGS)

    assert candidates == []


def test_http_error_raises_discovery_error():
    with patch("tracker.discover.requests.post", side_effect=ConnectionError("boom")):
        with pytest.raises(DiscoveryError):
            run_query("failing query", **CANDIDATE_KWARGS)


def test_malformed_json_content_raises_discovery_error():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
    with patch("tracker.discover.requests.post", return_value=resp):
        with pytest.raises(DiscoveryError):
            run_query("malformed query", **CANDIDATE_KWARGS)


def test_truncated_json_error_names_finish_reason():
    resp = Mock()
    resp.raise_for_status = Mock()
    truncated = '{"candidates": [{"name": "Some Workshop", "url": "https://ex'
    resp.json.return_value = {
        "choices": [{"message": {"content": truncated}, "finish_reason": "length"}]
    }
    with patch("tracker.discover.requests.post", return_value=resp):
        with pytest.raises(DiscoveryError, match="length"):
            run_query("truncated query", **CANDIDATE_KWARGS)


def test_none_content_raises_discovery_error_naming_finish_reason():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "choices": [{"message": {"content": None}, "finish_reason": "content_filter"}]
    }
    with patch("tracker.discover.requests.post", return_value=resp):
        with pytest.raises(DiscoveryError, match="content_filter"):
            run_query("refused query", **CANDIDATE_KWARGS)
