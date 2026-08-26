"""Discovery (SPEC.md section 4): one OpenRouter chat-completions call per
configured search query, using OpenRouter's `web` plugin pinned to the Exa
engine (see WEB_PLUGIN_ENGINE below for why pinning matters) so the search
mechanism and its cost stay the same no matter which model is configured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 60

# Left unset, OpenRouter's `web` plugin silently uses each provider's *native*
# search tool when the model has one (Anthropic, OpenAI, Google, Perplexity,
# xAI) instead of the flat-cost Exa path. Anthropic's native tool is agentic —
# the model can invoke it many times in one turn — which is almost certainly
# why two real queries cost ~$2 total on 2026-08-26 (not yet reproduced with
# this fix; watch usage.cost in the run summary on the next real run to
# confirm). Pinning to "exa" restores one bounded, non-agentic search per
# query, at a flat ~$0.007 (Exa's "auto" mode, up to 10 results) regardless of
# which model is configured.
WEB_PLUGIN_ENGINE = "exa"

CANDIDATE_PROPERTIES = {
    "name": {"type": "string"},
    "url": {"type": "string"},
    "event_type": {"type": "string", "enum": ["workshop", "conference", "cfp"]},
    "dates": {"type": "string", "description": "Empty string if not stated in the results."},
    "location": {"type": "string", "description": "Empty string if not stated in the results."},
    "organizer": {"type": "string"},
    "relevance_rationale": {"type": "string"},
    "reputability_rationale": {"type": "string"},
}

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": CANDIDATE_PROPERTIES,
                "required": list(CANDIDATE_PROPERTIES),
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


class DiscoveryError(Exception):
    pass


@dataclass
class Candidate:
    query: str
    name: str
    url: str
    event_type: str
    dates: str
    location: str
    organizer: str
    relevance_rationale: str
    reputability_rationale: str


@dataclass
class QueryResult:
    candidates: list[Candidate]
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None


def _prompt(query: str, relevance: str, reputability: str, max_candidates: int) -> str:
    return (
        f"Search query: {query}\n\n"
        f"Relevance bar (an event must connect to this):\n{relevance}\n\n"
        f"Reputability bar:\n{reputability}\n\n"
        f"From the web search results for this query, list up to {max_candidates} "
        "distinct workshops, conferences, or open CFPs that meet BOTH bars above "
        "— the most relevant and reputable ones first if more than "
        f"{max_candidates} qualify. Skip anything whose CFP/registration has "
        "already closed with no future edition mentioned in the results. If "
        "nothing in the results qualifies, return an empty candidates list "
        "rather than including a borderline or invented item."
    )


def run_query(
    query: str,
    *,
    api_key: str,
    model: str,
    relevance: str,
    reputability: str,
    max_results: int,
    max_output_tokens: int,
    max_candidates: int,
) -> QueryResult:
    body = {
        "model": model,
        "max_tokens": max_output_tokens,
        "plugins": [{"id": "web", "engine": WEB_PLUGIN_ENGINE, "max_results": max_results}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "candidates", "strict": True, "schema": CANDIDATE_SCHEMA},
        },
        "messages": [{"role": "user", "content": _prompt(query, relevance, reputability, max_candidates)}],
    }
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        if content is None:
            raise ValueError(f"empty message content, finish_reason={choice.get('finish_reason')!r}")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{exc} (finish_reason={choice.get('finish_reason')!r}, "
                f"content_length={len(content)}, max_output_tokens={max_output_tokens} — "
                "likely truncated by the output token cap if finish_reason is 'length'"
            ) from exc
    except Exception as exc:  # noqa: BLE001 - one bad query must never fail the run, see main.py
        raise DiscoveryError(f"query {query!r}: {exc}") from exc

    usage = payload.get("usage") or {}
    candidates = [Candidate(query=query, **item) for item in parsed.get("candidates", [])]
    return QueryResult(
        candidates=candidates,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        cost_usd=usage.get("cost"),
    )
