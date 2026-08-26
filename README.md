# LAS Venue & CFP Tracker

Weekly, LLM-driven web search for new, reputable workshops, conferences, and
CFPs relevant to <https://www.largeagentsystems.org>. Runs entirely on GitHub
Actions. See [SPEC.md](SPEC.md) for the full design and reasoning; this file
is the short operational version.

## How it works

Every Monday 20:00 UTC (and on manual `workflow_dispatch`):

1. For each search query in [`config/scope.yaml`](config/scope.yaml), call an
   LLM on [OpenRouter](https://openrouter.ai) with web search enabled, asking
   it to extract candidate events that meet the relevance and reputability
   bars defined in that same file.
2. Dedupe candidates against each other and against `data/seen.json` (events
   already reported in a previous run).
3. Fetch each remaining candidate's URL for real and check the page actually
   mentions the claimed event name — an LLM can name a plausible event that
   doesn't exist, so nothing is trusted on the model's word alone.
4. Append everything that survives to [`data/discoveries.csv`](data/discoveries.csv)
   (the cumulative log) and open one digest GitHub issue for the run (skipped
   entirely if nothing new was found).

State (`data/seen.json`) is committed back to the repo at the end of each run,
since GitHub Actions runners are ephemeral.

## Setup

Needs one repo secret: `OPENROUTER_API_KEY`. Nothing else beyond the default
`GITHUB_TOKEN`.

## Tuning what it searches for

Edit [`config/scope.yaml`](config/scope.yaml):

- `search.queries` — the list of web searches run each week.
- `relevance.description` / `reputability.criteria` — the bars the model
  applies to every candidate. Change these, not the Python source, to change
  what counts as in-scope or reputable.
- `model.id` — any OpenRouter model slug. See SPEC.md section 8 for why cost
  barely depends on this choice at weekly cadence.

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
