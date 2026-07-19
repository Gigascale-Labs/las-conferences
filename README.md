# LAS Venue & CFP Tracker

Automated weekly tracker for workshops, conferences, and CFPs relevant to a research
lab working on large-scale multi-agent systems (LAS), digital twins, computational
economics, computational social science, and LLM multi-agent evals.

Runs entirely on GitHub Actions — no servers, no paid services, no secrets beyond
the default `GITHUB_TOKEN`.

## How it works

Every Monday 20:00 UTC (and on manual `workflow_dispatch`), the tracker polls every
enabled source in [`config/sources.yaml`](config/sources.yaml) across three tiers:

- **Tier A** — native RSS/Atom feeds (WikiCFP, arXiv, GitHub commit feeds, and one
  auto-published CSV index), parsed with `feedparser`.
- **Tier B** — the [OpenReview](https://openreview.net) v2 API, polling the
  `active_venues` group for newly-created workshop/conference venues under
  NeurIPS/ICML/ICLR/AAAI.
- **Tier C** — snapshot-and-diff for the handful of sources with neither a feed nor
  an API (society conference-announcement pages). Only used as a last resort.

New items are matched against [`config/keywords.yaml`](config/keywords.yaml). A
match on any topic group makes an item relevant; a relevant item that *also*
matches the `meta` group (workshop, CFP, deadline, ...) is high priority.

Every run produces:

1. New relevant items appended to [`data/venues.csv`](data/venues.csv) (the
   cumulative log — this is the primary artifact).
2. A GitHub issue (`[CFP] <title>`, labelled `cfp` + one label per matched topic)
   for each **high-priority** item, deduped by URL against existing open issues.
3. A run summary in the Actions job output: items found per source, sources that
   failed, sources returning 304/not-modified.

State (`data/seen.json`, `data/snapshots/`) is committed back to the repo at the
end of each run, since GitHub Actions runners are ephemeral.

## Politeness

Sources here are mostly volunteer-run academic sites, so the tracker is
deliberately conservative:

- At most one request per URL per run, and the workflow itself runs at most weekly.
- `robots.txt` is checked (via stdlib `urllib.robotparser`) before every fetch.
- Requests identify themselves with `las-venue-tracker/1.0 (+<repo-url>;
  mailto:<maintainer-email>)` — see `meta:` in `config/sources.yaml`.
- Conditional GETs (`If-None-Match` / `If-Modified-Since`) are used wherever the
  server returns an `ETag` or `Last-Modified` header, cached per source in
  `data/seen.json`.
- Every network call has a 20s timeout and one retry with backoff before that
  source is marked failed for the run — one broken source never fails the whole
  workflow (see `src/tracker/main.py`, `_run_source`).

## Adding a source

1. Prefer a feed. If the source publishes RSS/Atom, add a Tier A entry to
   `config/sources.yaml`:

   ```yaml
   - id: my-source
     tier: A
     name: "Human-readable name"
     url: "https://example.org/feed.xml"
     enabled: true
   ```

   If the source instead publishes a machine-readable CSV/index (rare — see
   `gh-eval-tools` for the pattern), add `format: csv` and the CSV parses via
   `tracker.tier_a_feeds.parse_csv_index` instead of `feedparser`.

2. If no feed exists but there's a structured API, add support similarly to
   `src/tracker/tier_b_openreview.py`.

3. Only if neither exists, add a Tier C entry pointing at the *specific*
   events/CFP page (not the homepage — homepages churn with unrelated noise):

   ```yaml
   - id: my-society
     tier: C
     name: "My Society conference page"
     url: "https://example.org/events/"
     enabled: true
     # optional: scope extraction to one element if the page has a lot of
     # surrounding chrome that survives normalisation (nav/footer are already
     # stripped automatically)
     selector: "main"
   ```

   The first run for a new Tier C source seeds its snapshot silently — no items
   are emitted until the *second* run sees a change.

Every source is wrapped in its own try/except in `main.py`; a source that fails 4
runs in a row gets a one-time `[tracker] source <id> failing` maintenance issue
rather than failing silently forever.

## Tuning keywords

Edit `config/keywords.yaml` — nothing is hardcoded in Python. Matching is a
case-insensitive substring match on title + summary, which is deliberate: short
stems like `agent societ` or `behavio` are chosen to catch plural/spelling variants
(`societies`, `behaviour`/`behavior`) without maintaining exhaustive lists. Note
that a couple of the acronym entries (`MAS`, `ACE`) are short enough to produce
occasional false positives on unrelated text containing those letter sequences —
if that becomes noisy in practice, tighten them (e.g. to `" MAS "` with padding)
directly in this file.

Add a new topic group by adding a new top-level key with a list of terms; it's
picked up automatically by `src/tracker/filters.py`. The special `meta` group
controls high-priority escalation, not relevance on its own — an item matching
only `meta` terms (e.g. just "workshop") is not considered relevant.

## Re-seeding a Tier C snapshot

If a Tier C source's snapshot has drifted (e.g. after tightening a `selector:`, or
after the page structure changed enough that diffs are noisy), delete its snapshot
file and let the next run reseed it silently:

```bash
rm data/snapshots/<source-id>.txt
```

The next run will save a fresh snapshot for that source without emitting any
items (same as a brand-new source's first run).

## Forcing redetection of a Tier A / Tier B item

To make the tracker re-report an item you've already seen (e.g. to test the
pipeline, or after fixing a bug that mis-classified something), delete its ID from
`seen_ids` for that source in `data/seen.json`. **Also clear that source's `etag`
and `last_modified` fields in the same edit.** Conditional GET (spec ground rule 3)
means an unchanged upstream feed can short-circuit to a 304 before any parsing or
dedupe check runs — if the cache validators are still set, the removed ID won't be
redetected until the feed's content actually changes upstream. Clearing all three
fields together guarantees a full re-fetch and re-evaluation on the next run.

## Local development

```bash
pip install -e ".[dev]"
pytest
python -m tracker.main --repo yourname/las-venue-tracker   # requires gh CLI auth for issue creation
```

Tests run fully offline against fixtures in `tests/fixtures/` — no network calls
are made in CI.

## Known gaps / follow-ups

- `essa`'s events page includes an unrelated "recent posts"-style widget alongside
  the real events content (observed: an unrelated Scout Laws article), with no
  distinguishing class/id found to scope a `selector:` around just the events
  list. Left unscoped deliberately — this is exactly what the diff-noise
  maintenance-issue mechanism (spec section 8) exists to catch; add a `selector:`
  if/when `[tracker] source essa diff noise` fires.
- `dtc` (Digital Twin Consortium) is disabled: its events page returns HTTP 403 to
  every UA tried, consistent with bot/WAF protection rather than a robots.txt
  disallow. No JS-rendering or bot-bypass workarounds were added, per the v1
  non-goals — re-enable if this changes.
- `wikicfp-dt` covers the "digital twin" category only; WikiCFP also separately
  tags "digital twins" (plural) as its own category. Not added as a second source
  to avoid near-duplicate items, since `wikicfp-all` plus keyword filtering already
  catches anything it would add.
