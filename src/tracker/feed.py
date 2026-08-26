"""Public JSON feed (SPEC.md section 7): the full current contents of the
events DB, republished as a static file every run for largeagentsystems.org
(or anything else) to fetch. Regenerated from the whole table each time, not
just this run's new rows, so it always reflects the cumulative known list.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tracker import db


def write_json_feed(conn: sqlite3.Connection, path: Path) -> None:
    events = db.fetch_all_events(conn)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(events),
        "events": events,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
