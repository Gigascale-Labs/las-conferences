"""Discovery (SPEC.md section 4): one OpenRouter chat-completions call per
configured search query, using OpenRouter's model-agnostic `web` plugin so the
search mechanism stays the same no matter which model is configured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT_SECONDS = 60

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


def _prompt(query: str, relevance: str, reputability: str) -> str:
    return (
        f"Search query: {query}\n\n"
        f"Relevance bar (an event must connect to this):\n{relevance}\n\n"
        f"Reputability bar:\n{reputability}\n\n"
        "From the web search results for this query, list every distinct "
        "workshop, conference, or open CFP that meets BOTH bars above. Skip "
        "anything whose CFP/registration has already closed with no future "
        "edition mentioned in the results. If nothing in the results "
        "qualifies, return an empty candidates list rather than including a "
        "borderline or invented item."
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
) -> list[Candidate]:
    body = {
        "model": model,
        "max_tokens": max_output_tokens,
        "plugins": [{"id": "web", "max_results": max_results}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "candidates", "strict": True, "schema": CANDIDATE_SCHEMA},
        },
        "messages": [{"role": "user", "content": _prompt(query, relevance, reputability)}],
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
        content = payload["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001 - one bad query must never fail the run, see main.py
        raise DiscoveryError(f"query {query!r}: {exc}") from exc

    return [Candidate(query=query, **item) for item in parsed.get("candidates", [])]
