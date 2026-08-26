"""One-off restyle pass over `description` in data/discoveries.db.

`discover.STYLE_RULES` was added after 32 rows had already been written, so
the stored descriptions predate the writing rules the discovery prompt now
applies to new rows. This module rewrites the existing rows to match, then
regenerates docs/events.json from the rewritten table via the normal
`feed.write_json_feed` path — the JSON feed is never hand-edited.

**Restyle only, no new facts.** The model is given the event name and the
existing description and nothing else. It is deliberately NOT given the
scraped page: handing it the source invites re-deriving a description rather
than rewriting the one in front of it, and a re-derived description is a new
extraction with new failure modes (and no verification step behind it). The
prompt states the no-new-facts constraint explicitly, and deletion is allowed
where addition is not.

Failure handling matches the rest of the pipeline (SPEC.md section 4): one bad
batch is collected and reported, never raised, and never blanks a row — a row
whose rewrite failed keeps its original description. Only when *every* batch
fails does the process exit non-zero.

Run: python -m tracker.restyle [--dry-run] [--limit N] [--batch-size N]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from tracker import db, emit, feed
from tracker.discover import DESCRIPTION_RULES, OPENROUTER_URL
# Same config file and same data/docs directories as the weekly run — imported
# rather than redeclared so the two entry points cannot drift apart.
from tracker.main import DATA_DIR, DOCS_DIR, _env_flag, load_config

REQUEST_TIMEOUT_SECONDS = 120
API_KEY_ENV = "OPENROUTER_API_KEY"

# Several events per call, not one: the writing rules are the bulk of the
# prompt and resending them once per event is the dominant token cost. Kept
# small enough that one failed call loses a few rewrites, not all of them.
DEFAULT_BATCH_SIZE = 5

# Enough for BATCH_SIZE one-sentence descriptions plus JSON overhead, with the
# same truncation caveat as discover.py's max_output_tokens: this is a margin,
# not a measured sufficient value. A truncated response fails its batch and is
# reported; the rows keep their originals.
MAX_OUTPUT_TOKENS = 2000

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "rewrites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Copy an id from the input exactly. Never invent one.",
                    },
                    "description": {"type": "string"},
                },
                "required": ["id", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rewrites"],
    "additionalProperties": False,
}

# A model client: takes the rows of one batch, returns {event id: new
# description}. Raises RestyleError on any failure. Real implementation is
# openrouter_client; tests pass a stub.
Client = Callable[[list[dict]], dict]


class RestyleError(Exception):
    pass


@dataclass
class Change:
    id: str
    name: str
    old: str
    new: str


@dataclass
class RestyleReport:
    """Everything that happened, including everything that did not happen.
    No count here is inferred from another — an id appears in exactly one of
    changed/unchanged/omitted/failures."""

    total_rows: int = 0
    considered: int = 0
    backup_path: Path | None = None
    changed: list[Change] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    batches_attempted: int = 0
    batches_failed: int = 0
    wrote: bool = False


def _prompt(rows: list[dict]) -> str:
    items = json.dumps(
        [{"id": row["id"], "name": row["name"], "description": row["description"]} for row in rows],
        indent=2,
        ensure_ascii=False,
    )
    return (
        "Rewrite the description of each event below so it follows the "
        "writing rules at the end of this message.\n\n"
        "HARD CONSTRAINT — NO NEW FACTS. The only information you may use is "
        "the `description` text and the `name` given to you here. Do not add "
        "any claim that is not already in that text: no date, no deadline, no "
        "location, no venue, no organizer, no affiliation, no number, no "
        "topic, no acronym expansion that is not already there. Do not use "
        "anything you know about this event from anywhere else, and do not "
        "look anything up. If applying a rule would mean removing a claim "
        "(it is praise, filler, or a hedge), remove it — deleting text is "
        "allowed, adding text is not. If a description already follows the "
        "rules, return it unchanged rather than paraphrasing it.\n\n"
        "Return exactly one entry per input id. Copy each id "
        "character-for-character from the input. Do not invent an id, do not "
        "merge two events into one entry, and do not drop an entry — if you "
        "cannot improve one, return its original text under its own id.\n\n"
        f"Events to restyle:\n{items}\n\n"
        f"Writing rules for `description`:\n{DESCRIPTION_RULES}"
    )


def openrouter_client(*, api_key: str, model: str) -> Client:
    """The real client. No `web` plugin here, unlike discover.run_query — this
    call must not search, only rewrite the text it is handed."""

    def call(rows: list[dict]) -> dict:
        body = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rewrites", "strict": True, "schema": REWRITE_SCHEMA},
            },
            "messages": [{"role": "user", "content": _prompt(rows)}],
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
            parsed = json.loads(content)
            return {item["id"]: item["description"] for item in parsed["rewrites"]}
        except Exception as exc:  # noqa: BLE001 - one bad batch must never fail the rest
            raise RestyleError(str(exc)) from exc

    return call


def _backup(db_path: Path) -> Path:
    """Timestamped copy taken before the first write, never overwritten.
    Lives in data/backups/, which is gitignored — the workflow's own
    upload-artifact step is what makes a CI run recoverable."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, target)
    return target


def restyle(
    db_path: Path,
    feed_path: Path,
    *,
    client: Client,
    dry_run: bool = False,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> RestyleReport:
    report = RestyleReport()
    conn = db.connect(db_path)
    try:
        rows = db.fetch_all_events(conn)
        report.total_rows = len(rows)
        rows = rows[:limit] if limit is not None else rows
        report.considered = len(rows)

        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            batch_ids = {row["id"] for row in batch}
            report.batches_attempted += 1
            try:
                returned = client(batch)
            except Exception as exc:  # noqa: BLE001 - collect, never raise (module docstring)
                report.batches_failed += 1
                report.failures.append(
                    f"batch of {len(batch)} starting {batch[0]['id']}: {exc} — originals kept"
                )
                continue

            if not isinstance(returned, dict):
                report.batches_failed += 1
                report.failures.append(
                    f"batch of {len(batch)} starting {batch[0]['id']}: client returned "
                    f"{type(returned).__name__}, expected a dict — originals kept"
                )
                continue

            # An id the model made up refers to no row and is dropped, not
            # written anywhere. Reported so an invented-id habit is visible.
            report.invented.extend(sorted(set(returned) - batch_ids))

            for row in batch:
                new = returned.get(row["id"])
                if new is None:
                    # Asked for, not returned. Row keeps its original text;
                    # reported so it is neither silently kept nor blanked.
                    report.omitted.append(row["id"])
                    continue
                if not isinstance(new, str) or not new.strip():
                    report.failures.append(
                        f"{row['id']}: model returned an empty description — original kept"
                    )
                    continue
                new = new.strip()
                if new == row["description"]:
                    report.unchanged.append(row["id"])
                    continue
                report.changed.append(Change(row["id"], row["name"], row["description"], new))

        if dry_run or not report.changed:
            return report

        report.backup_path = _backup(db_path)
        conn.executemany(
            "UPDATE events SET description = ? WHERE id = ?",
            [(change.new, change.id) for change in report.changed],
        )
        conn.commit()
        feed.write_json_feed(conn, feed_path)
        report.wrote = True
        return report
    finally:
        conn.close()


def _summary_lines(report: RestyleReport, *, dry_run: bool) -> list[str]:
    lines = []
    if dry_run:
        lines.append(
            "**DRY RUN** — data/discoveries.db and docs/events.json are NOT "
            "written. This is what would have happened."
        )
        lines.append("")
    lines.append(
        f"- {report.considered} of {report.total_rows} row(s) considered, in "
        f"{report.batches_attempted} batch(es); {report.batches_failed} batch(es) failed"
    )
    lines.append(
        f"- {len(report.changed)} rewritten, {len(report.unchanged)} returned unchanged, "
        f"{len(report.omitted)} omitted by the model, {len(report.invented)} invented id(s) dropped"
    )
    if report.backup_path:
        lines.append(f"- backed up data/discoveries.db to `{report.backup_path}` before writing")
    if report.wrote:
        lines.append("- wrote data/discoveries.db and regenerated docs/events.json")
    elif not dry_run:
        lines.append("- nothing changed, so nothing was written")

    for change in report.changed:
        lines.append("")
        lines.append(f"**{change.name}** (`{change.id}`)")
        lines.append(f"- old: {change.old}")
        lines.append(f"- new: {change.new}")

    if report.omitted:
        lines.append("")
        lines.append("Omitted by the model — original description kept, not blanked:")
        lines.extend(f"- `{event_id}`" for event_id in report.omitted)
    if report.invented:
        lines.append("")
        lines.append("Ids the model invented — dropped:")
        lines.extend(f"- `{event_id}`" for event_id in report.invented)
    if report.failures:
        lines.append("")
        lines.append("Failures — originals kept in every case:")
        lines.extend(f"- {failure}" for failure in report.failures)
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rewrite stored event descriptions to the writing rules in discover.STYLE_RULES."
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=_env_flag("DRY_RUN"),
        help="print each old -> new description and write nothing; defaults to $DRY_RUN",
    )
    parser.add_argument("--limit", type=int, default=None, help="restyle at most N rows")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"events per model call (default {DEFAULT_BATCH_SIZE})",
    )
    args = parser.parse_args()

    # Checked before anything else, dry run included: a dry run still calls
    # the model (that is the point — it prints what the model would write), so
    # a missing key must fail here with a sentence naming the variable rather
    # than as an HTTP 401 traceback several minutes in.
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(
            f"error: ${API_KEY_ENV} is not set, so no model call can be made. "
            "Set it in the environment (in CI it is mapped from the "
            "LAS_CONFERENCES_26AUG repo secret — see "
            ".github/workflows/restyle-descriptions.yaml). Nothing was written.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    db_path = DATA_DIR / "discoveries.db"
    if not db_path.exists():
        print(f"error: {db_path} does not exist — nothing to restyle.", file=sys.stderr)
        raise SystemExit(2)

    if args.limit is not None and args.limit < 1:
        print("error: --limit must be 1 or more.", file=sys.stderr)
        raise SystemExit(2)
    if args.batch_size < 1:
        print("error: --batch-size must be 1 or more.", file=sys.stderr)
        raise SystemExit(2)

    config = load_config()
    report = restyle(
        db_path,
        DOCS_DIR / "events.json",
        client=openrouter_client(api_key=api_key, model=config["model"]["id"]),
        dry_run=args.dry_run,
        limit=args.limit,
        batch_size=args.batch_size,
    )

    summary = emit.RunSummary("LAS venue tracker — description restyle")
    summary.lines.extend(_summary_lines(report, dry_run=args.dry_run))
    summary.write()

    # Same rule as main.py: some batches failing is tolerated, every batch
    # failing is systemic (bad key, model down) and must not exit 0.
    if report.batches_attempted and report.batches_failed == report.batches_attempted:
        print(
            f"error: all {report.batches_attempted} batch(es) failed — first error: "
            f"{report.failures[0]}",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
