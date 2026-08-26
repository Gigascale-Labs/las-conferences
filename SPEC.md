# SPEC — LAS venue & CFP tracker, v2

Supersedes the v1 spec (RSS/Atom + OpenReview API + page-diff, keyword-filtered —
see git history before this file was added). v1's substring-keyword match over
generic academic feeds surfaced too many false positives to be useful. v2
replaces the whole discovery mechanism: an LLM performs the web search and the
relevance/reputability judgment; deterministic code only verifies and dedupes.

## 1. Goal

Once a week, find new, reputable workshops, conferences, and CFPs relevant to
the research scope of <https://www.largeagentsystems.org>, and log/notify on
the ones that pass verification. Runs on GitHub Actions — no server, no
database beyond files committed back to this repo.

## 2. Relevance scope

Directly observed from largeagentsystems.org (fetched 2026-08-26; re-check this
section if the site's stated scope changes): the site is a Gigascale Labs
project studying "a new science of billion-scale systems" — keeping outcomes
pro-human as millions of AI agents enter human economies and societies. Its
four framed research areas are monitoring, steering, simulation, and redesign
of large-scale mixed human-AI systems; named disciplines include network
science, macroprudential regulation, market microstructure, mechanism design,
and behavioral evaluation of agent populations.

This is narrower than v1's net (which took "multi-agent systems" or
"computational social science" as relevant on their own). v2's bar is: an item
must connect to the *systemic/large-scale* framing above, not just multi-agent
systems research in general. The exact wording passed to the model is
`relevance.description` in `config/scope.yaml` — edit it there, not in code.

## 3. Reputability scope

An accepted item must have a real, identifiable organizer (a university, a
named lab, an established academic society, or a recognized conference series)
and independently checkable existence — a live CFP/event page, or a listing on
a known indexer (WikiCFP, DBLP, OpenReview). The model states its reasoning in
a required `reputability_rationale` field on every candidate; there is no
automated reputability gate beyond that and the URL-verification step in
section 5. The exact wording is `reputability.criteria` in
`config/scope.yaml`.

## 4. Discovery mechanism

`src/tracker/discover.py` sends one Chat Completions request per configured
search query (`config/scope.yaml` → `search.queries`) to a model on OpenRouter,
using OpenRouter's model-agnostic `web` plugin (Exa-backed, not a
provider-native tool) so the model and the search mechanism are decoupled —
swapping `model.id` in config never changes how search works. Each request:

- states the query, the relevance scope, and the reputability scope in the
  prompt (see `discover._prompt`)
- constrains the response to a JSON schema (`response_format:
  json_schema`, `strict: true`) listing candidate events: name, url,
  event_type, dates, location, organizer, relevance_rationale,
  reputability_rationale — every field required, no free-form prose reply
- instructs the model to return an empty list rather than invent an item to
  fill it

One query failing (timeout, malformed JSON, HTTP error) is logged and skipped;
it must never fail the whole run (same ground rule as v1). But *every* query
failing in the same run is treated differently: that pattern means something
systemic broke (a bad/missing `OPENROUTER_API_KEY`, OpenRouter itself down),
and left unflagged would look identical to a legitimate quiet week. `main.py`
detects the all-failed case, exits non-zero, and (outside `dry_run`) opens a
`[tracker] all search queries failed` maintenance issue, deduped by title.
Found live during initial testing (2026-08-26): the workflow's secret was
misnamed, every query got an empty bearer token, and the run exited 0 anyway
— exactly the failure mode this check now catches.

## 5. Verification (anti-hallucination)

An LLM web-search call can name a plausible event that does not exist, or get
its URL wrong. `src/tracker/verify.py` treats every candidate as unverified
until checked independently of the model's own claim:

1. Fetch the candidate's URL for real (`tracker.net.fetch` — robots.txt
   checked first, one retry, 20s timeout, identifying UA — same politeness
   ground rules as v1).
2. Confirm the page text actually contains tokens from the claimed event name.
   No match (or an unreachable URL) rejects the candidate — logged in the run
   summary, not silently dropped.

This is a heuristic, not a guarantee: it catches "invented URL" and
"wrong page" but not "real page, wrong claimed date." Known gap — see section
9.

## 6. Dedup

`src/tracker/state.py` keeps a normalized-URL set in `data/seen.json`,
committed back to the repo each run (Actions runners are ephemeral, same as
v1). A candidate whose normalized URL was already accepted in a previous run
is skipped before verification. Within one run, candidates are also deduped by
normalized URL before verification, since more than one search query can
surface the same event.

## 7. Output

Every accepted candidate (verified + not previously seen) is:

1. Appended to `data/discoveries.csv` (the cumulative log — the primary
   artifact, same role as v1's `data/venues.csv`).
2. Included in one weekly digest GitHub issue (`[weekly-scan] N new item(s) —
   <date>`), titled and skipped entirely when `N == 0` — no issue on an empty
   week, matching v1's low-noise intent. One issue, not one per item: expected
   weekly volume is a handful of genuinely new items, not a feed's worth.

A run summary (query results, rejections with reasons, skip counts) is
written to `$GITHUB_STEP_SUMMARY`.

## 8. Cost and secrets

Needs one repo secret: `OPENROUTER_API_KEY`. No other paid service. `model.id`
and `search.queries` are both in `config/scope.yaml`.

The original pricing estimate (2026-08-26, before this pipeline had ever run
for real) assumed OpenRouter's `web` plugin behaves as documented: one flat,
non-agentic Exa search per call, ~$0.007-0.02 regardless of model. That
assumption was wrong in a way that mattered: left with `engine` unset, the
plugin silently substitutes each provider's *native* search tool when the
model has one (true for Anthropic, OpenAI, Google, Perplexity, xAI) — and
Anthropic's native tool is agentic, letting the model invoke it repeatedly
within one turn. Measured: 2 real queries against `anthropic/claude-sonnet-5`
with `engine` unset cost a combined ~$2 (2026-08-26) — no per-call cost
breakdown was captured at the time, so the exact split isn't known, but it is
10-100x the ~$0.02-0.20 the original estimate assumed for that many queries.
Fix: `discover.py` now pins `plugins: [{"id": "web", "engine": "exa", ...}]`
explicitly (`WEB_PLUGIN_ENGINE`), restoring one bounded, non-agentic search
per query at Exa's flat ~$0.007 (auto mode, up to 10 results) regardless of
which model is configured — this is what makes the model choice barely
matter, not the unpinned default. `discover.run_query` also now returns
`usage.cost`/`prompt_tokens`/`completion_tokens` from OpenRouter's response,
logged per-query and summed per-run in the run summary, so actual spend is
observed on every run going forward rather than assumed.

## 9. Non-goals / known gaps

- No headless-browser rendering. If a candidate's page is JS-rendered and
  `requests.get` sees an empty shell, verification will reject it as a false
  negative. Not solved in v2 — same non-goal as v1 had for Tier C sources.
- Verification checks *existence*, not *date accuracy*. A candidate could be a
  real, reputable, on-topic event with a stale or wrong date and still pass.
  Not checked in v2.
- No cap yet on total OpenRouter spend per run beyond `search.queries` length ×
  `search.max_results_per_query`. If this becomes a concern, add a hard token/
  request budget in `main.py` — not needed at the current ~10-query scale.
- Duplicate-but-differently-worded candidates from two different search
  queries (e.g. the model paraphrases the same event's name two different
  ways with two different URL casings/query-strings that `normalize_url`
  doesn't collapse) can both pass dedup. Not observed yet since v2 hasn't run;
  watch `data/discoveries.csv` for this and tighten `normalize_url` if it
  happens.
