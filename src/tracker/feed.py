"""Public feeds (SPEC.md section 7a): the full current contents of the events
DB, republished as static files every run for largeagentsystems.org (or
anything else) to fetch. Regenerated from the whole table each time, not just
this run's new rows, so they always reflect the cumulative known list.

Two formats, same rows:

- `docs/events.json` — the original feed, already consumed by the site. Its
  bytes and behaviour are unchanged by the Atom feed below.
- `docs/events.xml` — Atom 1.0, so largeagentsystems.org/events/feed.xml can
  serve upstream bytes rather than rebuild the document itself. This project
  group's rule: a feed is generated once, upstream, and every consumer serves
  it verbatim.

Nothing in the Atom path reads a clock. Every timestamp in it comes from a
row's `date_scraped`, so rebuilding an unchanged table produces a
byte-identical document — a rebuild must not re-announce every event to every
subscriber.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax import saxutils

from tracker import db
from tracker.verify import VERIFIED

SITE_URL = "https://largeagentsystems.org"
EVENTS_PAGE_URL = f"{SITE_URL}/events"
FEED_SELF_URL = f"{SITE_URL}/events/feed.xml"
FEED_TITLE = "Events and CFPs — largeagentsystems.org"
# Atom requires an author on the feed or on every entry; one feed-level author
# is correct here, since the tracker (not each event's organizer) is what
# produces these entries.
FEED_AUTHOR_NAME = "largeagentsystems.org"

# Entry <id> is a tag: URI (RFC 4151) built from the row's uuid, NOT from the
# event's own url. Reasoning: an entry id must be unique and must never change
# for the same item, because a reader uses it to decide what it has already
# shown. The row id is a uuid4 assigned at insert and never updated (db.py is
# insert-only), so it satisfies both. The event url does not: it is a third
# party's URL that can move or be re-published under a different address, it
# can be empty (see _entry_xml), and it is not guaranteed unique across rows.
# The date part is a fixed literal, never today's date, for the same
# no-clock reason as _updated.
ENTRY_ID_PREFIX = "tag:largeagentsystems.org,2026:event/"

# Feed-level <updated> when the table is empty. An empty feed has no row to
# take a date from, and reading a clock here would make every rebuild of an
# empty feed differ from the last one.
EMPTY_FEED_UPDATED = "1970-01-01T00:00:00Z"

HTTP_SCHEMES = ("http://", "https://")


def write_json_feed(conn: sqlite3.Connection, path: Path) -> None:
    events = db.fetch_all_events(conn)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(events),
        "events": events,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def write_atom_feed(conn: sqlite3.Connection, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_atom_feed(db.fetch_all_events(conn)), encoding="utf-8")


def build_atom_feed(events: list[dict]) -> str:
    """An Atom 1.0 document for `events`, newest `date_scraped` first.

    Sorted here rather than trusted from the caller, so the document's order
    is a property of this function and not of the query that fed it. The sort
    is stable, so rows sharing a date keep the order they arrived in
    (db.fetch_all_events gives them name-ascending).
    """
    events = sorted(events, key=lambda event: event["date_scraped"], reverse=True)
    updated = max((_updated(event["date_scraped"]) for event in events), default=EMPTY_FEED_UPDATED)
    entries = "".join("\n" + _entry_xml(event) for event in events)

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"<title>{_esc(FEED_TITLE)}</title>\n"
        f"<id>{_esc(EVENTS_PAGE_URL)}</id>\n"
        f'<link rel="self" href={saxutils.quoteattr(FEED_SELF_URL)}/>\n'
        f'<link rel="alternate" href={saxutils.quoteattr(EVENTS_PAGE_URL)}/>\n'
        f"<updated>{updated}</updated>\n"
        f"<author><name>{_esc(FEED_AUTHOR_NAME)}</name></author>"
        f"{entries}\n"
        "</feed>\n"
    )


def _entry_xml(event: dict) -> str:
    """One <entry>. Every value that reaches XML here is event text, i.e.
    text a third-party page supplied and a model copied, so all of it is
    escaped — `&` and `<` in a name or description must not be able to close
    an element early or forge an entry."""
    url = (event["url"] or "").strip()
    # A row with an empty or non-http(s) url still gets a full entry, minus
    # the <link rel="alternate">: the row is a known event and dropping it
    # would hide it from the feed while the JSON feed still lists it (the same
    # "don't silently drop it" rule SPEC.md section 5 applies to BLOCKED
    # rows). An href of "" or of a non-http scheme is what cannot be emitted —
    # it is either an invalid IRI or something a reader should not be handed
    # as a link. The entry stays addressable through its <id>, and
    # _content_html states the unusable url as plain text so the fact is not
    # lost from the document.
    link = f'<link rel="alternate" href={saxutils.quoteattr(url)}/>' if _is_http_url(url) else ""
    return (
        "<entry>"
        f"<title>{_esc(event['name'])}</title>"
        f"<id>{_esc(ENTRY_ID_PREFIX + event['id'])}</id>"
        f"{link}"
        f"<updated>{_updated(event['date_scraped'])}</updated>"
        # The double layer: _content_html already escaped each value for HTML,
        # and the finished fragment is escaped again here for XML, because
        # Atom carries HTML content as an ordinary XML text node. One layer
        # alone would let a "</content>" in a description close the element.
        f'<content type="html">{_esc(_content_html(event))}</content>'
        "</entry>"
    )


def _content_html(event: dict) -> str:
    """The HTML fragment for one entry, with every value escaped for HTML.
    The caller escapes the whole fragment again for XML."""
    parts = []
    if event["description"].strip():
        parts.append(f"<p>{_esc(event['description'])}</p>")

    fields = [
        ("Type", event["event_type"]),
        ("Dates", event["dates"]),
        ("Location", event["location"]),
        ("Organizer", event["organizer"]),
    ]
    url = (event["url"] or "").strip()
    if url and not _is_http_url(url):
        # Stated, not linked — see _entry_xml.
        fields.append(("Source URL (not http(s), not linked)", url))
    if event["verification_status"] != VERIFIED:
        # SPEC.md section 5: a row the tracker could not verify must never be
        # presented as confirmed in reader-facing output. The digest issue
        # separates the two into different tables; a feed entry has no table
        # to sit in, so it says so on its own face.
        status = f"{event['verification_status']} — {event['verification_note']}"
        fields.append(("Verification", status))

    items = "".join(
        f"<li><strong>{_esc(label)}:</strong> {_esc(value)}</li>"
        for label, value in fields
        if (value or "").strip()
    )
    if items:
        parts.append(f"<ul>{items}</ul>")
    return "".join(parts)


def _updated(date_scraped: str) -> str:
    """RFC 3339 timestamp for one entry, derived from `date_scraped` alone.

    db.py stores a UTC date ("2026-08-26"), so midnight UTC on that date is
    the most precise honest value available, and it is fixed for the life of
    the row (`date_scraped` is never updated). No clock is read.
    """
    try:
        return f"{date.fromisoformat(date_scraped.strip()).isoformat()}T00:00:00Z"
    except (ValueError, AttributeError):
        # Unreachable through db.insert_events, which writes an ISO date. A
        # hand-edited row must still not put an invalid timestamp into the
        # document, so it falls back to a fixed value rather than a clock.
        return EMPTY_FEED_UPDATED


def _is_http_url(url: str) -> bool:
    return url.lower().startswith(HTTP_SCHEMES)


def _esc(text: str) -> str:
    return saxutils.escape(text or "")
