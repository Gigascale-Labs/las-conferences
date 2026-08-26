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


def _openrouter_response(candidates: list[dict], usage: dict | None = None) -> Mock:
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"candidates": candidates})}}],
        "usage": usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.007},
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
        "description": "A workshop on mechanism design for economies of AI agents.",
        "relevance_rationale": "Studies mechanism design for large agent populations.",
        "reputability_rationale": "Affiliated with the AAMAS conference series.",
    }
    with patch("tracker.discover.requests.post", return_value=_openrouter_response([candidate_payload])):
        result = run_query("agent economies workshop", **CANDIDATE_KWARGS)

    assert len(result.candidates) == 1
    assert isinstance(result.candidates[0], Candidate)
    assert result.candidates[0].name == "AAMAS 2027 Workshop on Agent Economies"
    assert result.candidates[0].query == "agent economies workshop"


def test_usage_and_cost_are_surfaced_from_response():
    usage = {"prompt_tokens": 4321, "completion_tokens": 654, "cost": 0.0123}
    with patch("tracker.discover.requests.post", return_value=_openrouter_response([], usage=usage)):
        result = run_query("cost query", **CANDIDATE_KWARGS)

    assert result.prompt_tokens == 4321
    assert result.completion_tokens == 654
    assert result.cost_usd == 0.0123


def test_missing_usage_surfaces_none_cost_rather_than_erroring():
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"choices": [{"message": {"content": json.dumps({"candidates": []})}}]}
    with patch("tracker.discover.requests.post", return_value=resp):
        result = run_query("no usage field query", **CANDIDATE_KWARGS)

    assert result.cost_usd is None


def test_web_plugin_is_pinned_to_exa_engine():
    """Regression guard: leaving `engine` unset makes OpenRouter silently use
    each provider's native (often agentic, much costlier) search tool instead
    — this is what drove a $2 spend across 2 real queries on 2026-08-26."""
    with patch("tracker.discover.requests.post", return_value=_openrouter_response([])) as mock_post:
        run_query("engine check query", **CANDIDATE_KWARGS)

    sent_body = mock_post.call_args.kwargs["json"]
    assert sent_body["plugins"] == [{"id": "web", "engine": "exa", "max_results": CANDIDATE_KWARGS["max_results"]}]


def test_empty_candidates_list_returns_empty():
    with patch("tracker.discover.requests.post", return_value=_openrouter_response([])):
        result = run_query("no results query", **CANDIDATE_KWARGS)

    assert result.candidates == []


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
