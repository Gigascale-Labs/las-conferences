"""Tier C: page snapshot + diff for feedless sources (spec section 4).

Last resort only — never used for a source that has a feed or API. Replaces
changedetection.io with a from-scratch snapshot/diff since that tool is a server
app and the wrong shape for a stateless GitHub Actions run.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from tracker.net import RobotsCache, fetch
from tracker.state import SnapshotStore

_STRIP_TAGS = ("script", "style", "nav", "footer")
_MIN_LINE_LENGTH = 4


def normalise(html: str, selector: str | None = None) -> str:
    """Visible text only, whitespace-collapsed, short lines dropped, sorted.

    Sorting makes the diff insensitive to reordering of unrelated page content
    (nav menus, unrelated list reshuffles) so a unified diff's added lines are
    reliably the new CFP/date text, not noise from moved-but-unchanged lines.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    root = (soup.select_one(selector) if selector else None) or soup
    text = root.get_text(separator="\n")

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) >= _MIN_LINE_LENGTH]
    lines.sort()
    return "\n".join(lines)


@dataclass
class DiffEvent:
    source_id: str
    added_lines: list[str]


def check_page(
    source_id: str,
    url: str,
    *,
    user_agent: str,
    robots: RobotsCache,
    snapshots: SnapshotStore,
    selector: str | None = None,
) -> DiffEvent | None:
    """Fetch, normalise, diff against the stored snapshot, overwrite the snapshot.

    Returns None on the first run for a source (seeded silently, per spec) or when
    there is no change; returns a DiffEvent with only the added lines otherwise.
    """
    result = fetch(url, user_agent=user_agent, robots=robots)
    new_text = normalise(result.text or "", selector)

    previous = snapshots.load(source_id)
    snapshots.save(source_id, new_text)

    if previous is None:
        return None

    diff = difflib.unified_diff(
        previous.splitlines(), new_text.splitlines(), lineterm=""
    )
    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]

    if not added:
        return None
    return DiffEvent(source_id=source_id, added_lines=added)
