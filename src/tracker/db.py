"""Persistent event store (SPEC.md section 7): a SQLite file committed back to
the repo each run, same as v1's CSV. Every row gets a UUID primary key and the
UTC date it was scraped — insert-only, never updated, so this is a append-only
log with real types/columns rather than a mutable table.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tracker.verify import VerificationResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    date_scraped TEXT NOT NULL,
    name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    dates TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL,
    organizer TEXT NOT NULL,
    url TEXT NOT NULL,
    query TEXT NOT NULL,
    relevance_rationale TEXT NOT NULL,
    reputability_rationale TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    verification_note TEXT NOT NULL
);
"""

COLUMNS = [
    "id",
    "date_scraped",
    "name",
    "event_type",
    "dates",
    "location",
    "description",
    "organizer",
    "url",
    "query",
    "relevance_rationale",
    "reputability_rationale",
    "verification_status",
    "verification_note",
]


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def insert_events(conn: sqlite3.Connection, kept: list[VerificationResult]) -> None:
    if not kept:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    rows = []
    for result in kept:
        c = result.candidate
        rows.append(
            (
                str(uuid.uuid4()),
                today,
                c.name,
                c.event_type,
                c.dates,
                c.location,
                c.description,
                c.organizer,
                c.url,
                c.query,
                c.relevance_rationale,
                c.reputability_rationale,
                result.status,
                result.reason,
            )
        )
    placeholders = ", ".join("?" for _ in COLUMNS)
    conn.executemany(f"INSERT INTO events ({', '.join(COLUMNS)}) VALUES ({placeholders})", rows)
    conn.commit()


def fetch_all_events(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM events ORDER BY date_scraped DESC, name ASC")
    return [dict(row) for row in cursor.fetchall()]
