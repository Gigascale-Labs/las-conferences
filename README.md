# LAS Venue & CFP Tracker

Weekly, LLM-driven web search for new, reputable workshops, conferences, and
CFPs relevant to <https://www.largeagentsystems.org>. Runs entirely on GitHub
Actions. See [SPEC.md](SPEC.md) for the full design and reasoning; this file
is the short operational version.

## How it works

Every Monday 20:00 UTC (and on manual `workflow_dispatch`):

1. For each search query in [`config/scope.yaml`](config/scope.yaml), call an
   LLM on [OpenRouter](https://openrouter.ai) with web search enabled, asking
   it to extract candidate events (name, dates, location, a one-line
   description, organizer) that meet the relevance and reputability bars
   defined in that same file.
2. Dedupe candidates against each other and against `data/seen.json` (events
   already reported in a previous run).
3. Fetch each remaining candidate's URL for real and check the page actually
   mentions the claimed event name — an LLM can name a plausible event that
   doesn't exist, so nothing is trusted on the model's word alone. A page that
   loads but doesn't mention the event is dropped (likely hallucinated); a
   page that can't be fetched at all (robots.txt, 403, timeout) is kept and
   flagged as unverified rather than dropped, since that says nothing about
   whether the event is real.
4. Insert everything that survives into [`data/discoveries.db`](data/discoveries.db)
   (SQLite — the cumulative log; each row gets a `uuid4` id and the UTC date
   it was scraped) and open one digest GitHub issue for the run — confirmed
   and unverified-but-possibly-relevant items in separate tables — skipped
   entirely if nothing new was found.
5. Regenerate [`docs/events.json`](docs/events.json) and
   [`docs/events.xml`](docs/events.xml) from the *entire* database (not just
   this run) — a public JSON feed and an Atom 1.0 feed for
   largeagentsystems.org or anything else to fetch. The site serves the Atom
   file verbatim at `/events/feed.xml` rather than rebuilding it. Every Atom
   timestamp comes from a row's `date_scraped`, never from a clock, so a
   rebuild of unchanged rows is byte-identical and does not re-announce every
   event to subscribers. See SPEC.md section 7a for both shapes, the entry-id
   and escaping decisions, and what wiring the actual site up still needs.

State (`data/seen.json`, `data/discoveries.db`, `docs/events.json`,
`docs/events.xml`) is committed back to the repo at the end of each run, since
GitHub Actions runners are ephemeral. This repo is public (since 2026-08-26)
so the feeds have a stable public URL once GitHub Pages is enabled on it.

## Setup

Needs one repo secret: `OPENROUTER_API_KEY`. Nothing else beyond the default
`GITHUB_TOKEN`.

## Tuning what it searches for

Edit [`config/scope.yaml`](config/scope.yaml):

- `search.queries` — the list of web searches run each week.
- `relevance.description` / `reputability.criteria` — the bars the model
  applies to every candidate. Change these, not the Python source, to change
  what counts as in-scope or reputable.
- `model.id` — any OpenRouter model slug. See SPEC.md section 8 for measured
  per-query cost and why model choice moves it more than originally assumed.

The writing rules for the three fields a human reads (`description`,
`relevance_rationale`, `reputability_rationale`) are `STYLE_RULES` in
[`src/tracker/discover.py`](src/tracker/discover.py), not config — the
comment above them records which clauses of the project ruleset were carried,
adapted or dropped, and why. See SPEC.md section 4a.

## Restyling descriptions already in the database

Rows written before the writing rules existed can be rewritten in place:

```bash
OPENROUTER_API_KEY=... python -m tracker.restyle --dry-run --limit 2   # prints old -> new, writes nothing
OPENROUTER_API_KEY=... python -m tracker.restyle                       # rewrites, then regenerates both feeds
```

A real run copies `data/discoveries.db` to `data/backups/` (gitignored) before
its first write and prints where. This is a **restyle, not a re-extraction**:
the model sees the stored description and the event name only, and may not add
a claim that is not already in that text. A row whose rewrite fails keeps its
original description — nothing is ever blanked.

In CI, run the manual-only `restyle-descriptions.yaml` workflow (`dry_run`
defaults to true, and every run uploads a pre-run copy of the database and
both feeds as an artifact):

```bash
gh workflow run restyle-descriptions.yaml                     # dry run
gh workflow run restyle-descriptions.yaml -f dry_run=false    # writes and commits
```

## Testing without touching main's data or issues

`workflow_dispatch` defaults `dry_run` to true: the run still calls
OpenRouter, verifies candidates, and writes the job summary, but skips the
`data/` commit and the GitHub issue. Scheduled runs always run for real
(`dry_run` has no effect on `schedule`). To test the pipeline itself without
any of this reaching `main`, push to a branch (e.g. `dev`) and dispatch the
workflow against that ref:

```bash
gh workflow run track.yaml --ref dev              # dry_run=true by default
gh workflow run track.yaml --ref dev -f dry_run=false   # writes for real, but to dev, not main
```

## Local development

```bash
pip install -e ".[dev]"
pytest
OPENROUTER_API_KEY=... python -m tracker.main --repo yourname/las-venue-tracker
```

Tests run fully offline (network calls mocked) — no OpenRouter or live-site
calls are made in CI.

## Re-detecting an item you've already seen

To make the tracker re-report an item already in `data/seen.json` (e.g. to
test the pipeline), remove its normalized URL from the JSON list in that file.
