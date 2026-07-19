"""Tier A: RSS/Atom feed polling via feedparser (spec section 2).

feedparser handles RSS 1.0/2.0/Atom uniformly, so there is no format-specific code
here. We fetch the raw bytes ourselves (via tracker.net) so the shared politeness
rules — robots.txt, UA string, conditional GET, timeout/retry — apply uniformly to
every source, then hand the body to feedparser for parsing.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import feedparser

from tracker.net import RobotsCache, fetch
from tracker.state import State


@dataclass
class Item:
    source_id: str
    tier: str
    title: str
    url: str
    published_date: str
    summary: str


def parse_feed_text(source_id: str, feed_text: str, state: State) -> list[Item]:
    """Parse already-fetched feed text and return new (not-previously-seen) entries.

    Every entry encountered is marked seen regardless of keyword relevance —
    high-volume feeds like wikicfp-all would otherwise be re-parsed for the same
    irrelevant entries on every run. Keyword filtering happens downstream in
    main.py so this module stays format-agnostic. Split out from poll_feed so it
    can be unit-tested against fixture files with no network involved.
    """
    parsed = feedparser.parse(feed_text)
    items: list[Item] = []
    for entry in parsed.entries:
        item_id = entry.get("id") or entry.get("link")
        if not item_id:
            continue
        if state.is_seen(source_id, item_id):
            continue
        state.mark_seen(source_id, item_id)
        items.append(
            Item(
                source_id=source_id,
                tier="A",
                title=(entry.get("title") or "").strip(),
                url=entry.get("link", ""),
                published_date=entry.get("published", entry.get("updated", "")),
                summary=entry.get("summary", ""),
            )
        )
    return items


def poll_feed(
    source_id: str,
    feed_url: str,
    *,
    user_agent: str,
    robots: RobotsCache,
    state: State,
) -> list[Item]:
    """Fetch a feed over HTTP (politeness rules applied) and return new entries."""
    etag = state.get_etag(source_id)
    last_modified = state.get_last_modified(source_id)

    result = fetch(
        feed_url,
        user_agent=user_agent,
        robots=robots,
        etag=etag,
        last_modified=last_modified,
    )
    if result.not_modified:
        return []

    state.set_etag(source_id, result.etag)
    state.set_last_modified(source_id, result.last_modified)

    return parse_feed_text(source_id, result.text, state)


def parse_csv_index(source_id: str, csv_text: str, state: State, id_field: str = "full_name") -> list[Item]:
    """Parse a machine-readable CSV repo index (e.g. an auto-published awesome-list
    export) into new items. Used instead of a commits-Atom-feed when a source
    publishes one, per spec section 2 (gh-eval-tools): a weekly-regenerated CSV is
    a stronger signal than "something changed" commit noise.
    """
    items: list[Item] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        item_id = row.get(id_field) or row.get("url")
        if not item_id or state.is_seen(source_id, item_id):
            continue
        state.mark_seen(source_id, item_id)
        items.append(
            Item(
                source_id=source_id,
                tier="A",
                title=row.get("name", item_id),
                url=row.get("url", ""),
                published_date=row.get("last_commit", ""),
                summary=f"{row.get('category', '')} — {row.get('description', '')}",
            )
        )
    return items


def poll_csv_index(
    source_id: str,
    csv_url: str,
    *,
    user_agent: str,
    robots: RobotsCache,
    state: State,
    id_field: str = "full_name",
) -> list[Item]:
    etag = state.get_etag(source_id)
    last_modified = state.get_last_modified(source_id)

    result = fetch(
        csv_url, user_agent=user_agent, robots=robots, etag=etag, last_modified=last_modified
    )
    if result.not_modified:
        return []

    state.set_etag(source_id, result.etag)
    state.set_last_modified(source_id, result.last_modified)

    return parse_csv_index(source_id, result.text, state, id_field=id_field)
