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

# --------------------------------------------------------------------------
# Writing rules for the reader-facing text fields
# --------------------------------------------------------------------------
# `description`, `relevance_rationale` and `reputability_rationale` are the
# only three fields a human reads as prose. All three are rendered in the
# weekly digest issue (emit.create_digest_issue) and published verbatim in
# docs/events.json, which largeagentsystems.org/events reads — so the rules
# below are applied to all three, not to `description` alone. The remaining
# schema fields (name, url, dates, location, organizer) are transcription and
# take no style rule.
#
# These are this project's general writing ruleset, adapted. That ruleset was
# written for prose, code review and charts; an event description is one
# sentence with nothing measured, no table and no chart in it, so it is
# carried across rather than pasted. What was carried, and what was NOT and
# why — nothing here was dropped silently:
#
#   as-is     "Answer in the first sentence."
#   as-is     "Give facts and numbers, not justifications."
#   as-is     "No metaphor, no praise, no filler, no stacked hedges."
#   as-is     "Do not use passive voice." Added to the ruleset on 2026-08-26,
#             the same day the restyle run showed why it is needed: the
#             no-throat-clearing rule below rewrote "This workshop addresses
#             the systems foundations—sandboxing, privacy, trust establishment
#             without central control, and rigorous evaluation—needed to make
#             internet-scale networks of autonomous, delegating AI agents
#             trustworthy in practice." as "Systems foundations for
#             internet-scale networks of autonomous, delegating AI agents are
#             addressed, including ...". Dropping the weak opener cost the
#             active voice, because no rule asked for it. Both rule sets below
#             now state the clause and carry that before/after, so the trade is
#             not made again.
#   adapted   "State every number with its n and its spread." Nothing in this
#             pipeline is measured; the only numbers available are dates,
#             deadlines and edition numbers transcribed off a page. Demanding
#             an n and a spread would make the model invent statistics.
#             Carried as: use the numbers the sources state and no others —
#             stated in full for `description`, referred back to for the two
#             rationales, which can quote an edition number or a deadline too.
#   adapted   "Label what you measured, observed, inferred, assumed." One
#             sentence has no room for four labels, and the model measures
#             nothing. Carried as the stronger rule for this case: state only
#             what the sources state, and omit anything you would infer.
#   adapted   "Name what you did not check." Nothing here is checked by the
#             model — verify.py performs the only real check, after the fact.
#             Carried into reputability_rationale as: name where the claim
#             came from, and say when only the event's own page makes it.
#   adapted   "Say 'I do not know' when you do not know, and name the test
#             that would settle it." The schema requires a non-empty string
#             for every field, so "I do not know" is not an available answer,
#             and the test that settles existence (fetch the URL) is chosen by
#             verify.py, not by the model. Carried as: say what the sources do
#             not state instead of guessing at it.
#   adapted   "Then give the detail." A description is one sentence — there is
#             no second part to hold detail. Kept for the two rationales only.
#   dropped   "Use a table for three or more parallel items." These fields
#             land inside a markdown table cell in the digest issue and inside
#             a JSON string in the feed. A nested table renders in neither.
#   dropped   "Avoid making claims in visualisation titles." / "Label
#             visualisation axes and provide keys." / "Do not overlay two
#             visualisation elements using the same colour." This pipeline
#             draws nothing — no chart, no image, no diagram anywhere in it.

DESCRIPTION_RULES = (
    "- One sentence, and it answers 'what does this event cover?' in that "
    "sentence. Not why it matters, not who should attend, not how good it is.\n"
    "- Facts, not justification: name the subject matter, methods and "
    "disciplines the sources state.\n"
    "- Use the numbers the sources state (dates, deadlines, edition number) "
    "and no others. Never estimate, round, or invent a number.\n"
    "- State only what the sources state. Leave out anything you would have "
    "to infer or guess at. A shorter sentence is correct; a padded one is not.\n"
    "- No metaphor, no praise, no filler, no stacked hedges. Cut words like "
    "'leading', 'premier', 'cutting-edge', 'exciting', 'may potentially'.\n"
    "- No throat-clearing opener ('This workshop aims to bring together "
    "researchers in order to explore...'). Open on the subject matter.\n"
    "- Do not use passive voice. Every verb keeps its actor: 'The workshop "
    "covers X', 'Papers examine X', 'Covers X'. Never 'X is addressed', 'X "
    "will be discussed', 'X are covered'.\n"
    "- Dropping the opener must not cost the active voice. This exact trade "
    "was made once: 'This workshop addresses the systems foundations - "
    "sandboxing, privacy, trust establishment without central control - "
    "needed to make internet-scale networks of autonomous, delegating AI "
    "agents trustworthy in practice.' became 'Systems foundations for "
    "internet-scale networks of autonomous, delegating AI agents are "
    "addressed, including sandboxing, privacy, ...', which fixed the opener "
    "and broke the rule above. Write both rules at once: 'Covers sandboxing, "
    "privacy, trust establishment without central control, and evaluation of "
    "internet-scale networks of autonomous, delegating AI agents.'"
)

RATIONALE_RULES = (
    "- Answer in the first sentence, then give the detail.\n"
    "- relevance_rationale: state the in-scope subject matter the event's own "
    "page states, and which part of the relevance bar above it matches. Not "
    "how strong the match feels.\n"
    "- reputability_rationale: name the organizing body, the conference "
    "series, or the indexer that lists the event, and say where that came "
    "from — the event's own page, or an independent listing. Say so plainly "
    "when only the event's own page claims it; a self-description is not "
    "independent evidence.\n"
    "- Facts, not praise. 'Listed on WikiCFP, organizer given as the AAMAS "
    "2027 programme committee' is a fact. 'A highly respected venue' is not.\n"
    "- Do not use passive voice. Name the actor, because here the actor is "
    "the evidence: 'WikiCFP lists the workshop' and 'IFAAMAS runs the series' "
    "are checkable. 'The workshop is listed' hides who lists it. The same "
    "trade as in the description rules applies — cutting a weak opener must "
    "not turn the sentence passive.\n"
    "- Same rule as above on metaphor, praise, filler and stacked hedges.\n"
    "- Same rule as above on numbers: use the numbers the sources state "
    "(edition number, dates, deadlines) and no others. Never estimate, round, "
    "or invent one.\n"
    "- Where the sources do not state something, say it is not stated rather "
    "than guessing at it."
)

STYLE_RULES = (
    "Writing rules for the three fields a human reads — `description`, "
    "`relevance_rationale`, `reputability_rationale`:\n\n"
    f"description:\n{DESCRIPTION_RULES}\n\n"
    f"relevance_rationale and reputability_rationale:\n{RATIONALE_RULES}"
)

CANDIDATE_PROPERTIES = {
    "name": {"type": "string"},
    "url": {"type": "string"},
    "event_type": {"type": "string", "enum": ["workshop", "conference", "cfp"]},
    "dates": {"type": "string", "description": "Empty string if not stated in the results."},
    "location": {"type": "string", "description": "Empty string if not stated in the results."},
    "organizer": {"type": "string"},
    "description": {
        "type": "string",
        "description": (
            "One sentence on what the event covers — topic/scope, not why it's "
            "relevant or reputable. Follow the writing rules in the prompt."
        ),
    },
    "relevance_rationale": {
        "type": "string",
        "description": "Why this is in scope. Follow the writing rules in the prompt.",
    },
    "reputability_rationale": {
        "type": "string",
        "description": "Who runs it and how that is checkable. Follow the writing rules in the prompt.",
    },
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
    description: str
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
        f"{max_candidates} qualify. For each, give its dates and location "
        "exactly as stated in the results (empty string if not stated — do "
        "not guess), and a one-sentence description of what the event covers, "
        "separate from why it's relevant or reputable. Skip "
        "anything whose CFP/registration has already closed with no future "
        "edition mentioned in the results. If nothing in the results "
        "qualifies, return an empty candidates list rather than including a "
        "borderline or invented item.\n\n"
        f"{STYLE_RULES}"
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
