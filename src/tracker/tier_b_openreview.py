"""Tier B: OpenReview venue-group polling (spec section 3).

Uses the officially supported v2 client per docs.openreview.net (API v1 is
deprecated — do not use `openreview.Client`/api1 patterns from older snippets).

Venue discovery uses the `active_venues` special group, which lists every
currently active OpenReview venue group ID in one call — confirmed live and
unauthenticated via `GET https://api2.openreview.net/groups?id=active_venues`.
We filter that flat list client-side by the configured parent prefixes (e.g.
"NeurIPS.cc") rather than querying each parent/year combination separately; this
is the venue-discovery pattern OpenReview's own docs describe, and it costs a
single request per run regardless of how many parents/years are configured.
"""
from __future__ import annotations

from dataclasses import dataclass

import openreview

from tracker.state import State

API_V2_BASEURL = "https://api2.openreview.net"


@dataclass
class Item:
    source_id: str
    tier: str
    title: str
    url: str
    published_date: str
    summary: str


def _guest_client() -> "openreview.api.OpenReviewClient":
    # Unauthenticated guest client: public group/venue listing needs no credentials.
    return openreview.api.OpenReviewClient(baseurl=API_V2_BASEURL)


def poll_venues(
    source_id: str,
    parents: list[str],
    years: list[str],
    *,
    state: State,
    client: "openreview.api.OpenReviewClient | None" = None,
) -> list[Item]:
    """Return newly-observed venue group IDs under any of `parents`.

    A venue ID not previously seen in data/seen.json is a new venue event — the
    mechanism that catches a freshly-announced workshop before any CFP circulates.
    `years` narrows further (e.g. ["2026", "2027"]); leave empty to match all years.
    """
    client = client or _guest_client()
    all_venues: list[str] = client.get_group(id="active_venues").members

    def matches(venue_id: str) -> bool:
        if not any(venue_id.startswith(f"{parent}/") for parent in parents):
            return False
        if years and not any(f"/{year}/" in venue_id for year in years):
            return False
        return True

    items: list[Item] = []
    for venue_id in all_venues:
        if not matches(venue_id) or state.is_seen(source_id, venue_id):
            continue
        state.mark_seen(source_id, venue_id)
        items.append(
            Item(
                source_id=source_id,
                tier="B",
                title=venue_id,
                url=f"https://openreview.net/group?id={venue_id}",
                published_date="",
                summary=venue_id.replace("/", " ").replace(".", " ").replace("_", " "),
            )
        )
    return items
