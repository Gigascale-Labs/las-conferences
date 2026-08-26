"""Verification (SPEC.md section 5): an LLM web-search call can name a
plausible event that does not exist, or attach the wrong URL. Every candidate
is treated as unverified until its URL is fetched for real and checked to
actually mention the claimed event — independent of the model's own claim.

Three outcomes, not two: a candidate whose page loads but doesn't mention the
claimed name is REJECTED (a real hallucination signal). A candidate whose page
can't even be fetched — robots.txt disallow, 403, timeout — is BLOCKED: that
says nothing about whether the event is real, only that this checker can't
see the page, so it's kept and flagged rather than dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

from tracker.discover import Candidate
from tracker.net import FetchError, RobotsCache, fetch

_MIN_TOKEN_LENGTH = 4

VERIFIED = "verified"
REJECTED = "rejected"
BLOCKED = "blocked"


@dataclass
class VerificationResult:
    candidate: Candidate
    status: str  # one of VERIFIED, REJECTED, BLOCKED
    reason: str


def _name_tokens(name: str) -> list[str]:
    return [t.lower() for t in name.split() if len(t) >= _MIN_TOKEN_LENGTH]


def verify(candidate: Candidate, *, user_agent: str, robots: RobotsCache) -> VerificationResult:
    tokens = _name_tokens(candidate.name)
    if not tokens:
        return VerificationResult(candidate, BLOCKED, "candidate name too short to check")

    try:
        result = fetch(candidate.url, user_agent=user_agent, robots=robots)
    except FetchError as exc:
        return VerificationResult(candidate, BLOCKED, f"could not fetch page: {exc}")

    page_text = (result.text or "").lower()
    matches = sum(1 for t in tokens if t in page_text)
    if matches == 0:
        return VerificationResult(candidate, REJECTED, "event name not found on its own URL")
    return VerificationResult(candidate, VERIFIED, f"{matches}/{len(tokens)} name tokens found on page")
