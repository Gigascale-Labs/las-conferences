# SPEC — LAS venue & CFP tracker, v2

Supersedes the v1 spec (RSS/Atom + OpenReview API + page-diff, keyword-filtered —
see git history before this file was added). v1's substring-keyword match over
generic academic feeds surfaced too many false positives to be useful. v2
replaces the whole discovery mechanism: an LLM performs the web search and the
relevance/reputability judgment; deterministic code only verifies and dedupes.

## 1. Goal

Once a week, find new, reputable workshops, conferences, and CFPs relevant to
the research scope of <https://www.largeagentsystems.org>, and log/notify on
the ones that pass verification. Runs on GitHub Actions — no server; the only
"database" is a SQLite file committed back to this repo (section 7). Findings
are also published as a public JSON feed for largeagentsystems.org (or
anything else) to consume (section 7a).

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
  event_type, dates, location, organizer, description,
  relevance_rationale, reputability_rationale — every field required, no
  free-form prose reply. `dates`/`location` are the model's transcription of
  what the search results state, not independently checked — empty string if
  not stated, and the model is told not to guess them. `description` is a
  neutral one-sentence summary of what the event covers, distinct from
  `relevance_rationale` (why it's in scope) and `reputability_rationale` (why
  its organizer is credible) — kept as three separate fields so a reader can
  tell "what is this" from "why is it here" from "why should I believe it."
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

## 4a. Writing rules for the reader-facing fields

Three of the model's output fields are read by humans as prose:
`description`, `relevance_rationale`, `reputability_rationale`. All three are
rendered in the weekly digest issue and published verbatim in
`docs/events.json`, which largeagentsystems.org/events reads. All three
therefore carry this project's writing ruleset, stated in
`discover.STYLE_RULES` and appended to the discovery prompt. The other fields
(name, url, dates, location, organizer) are transcription and carry no style
rule.

The ruleset was written for prose, code review and charts, so it is adapted,
not pasted. `discover.py` carries a per-clause record of what was kept as-is,
what was adapted and how, and what was dropped and why — the three
visualisation clauses (this pipeline draws nothing) and the "use a table"
clause (these fields land in a markdown table cell and a JSON string, where a
nested table renders in neither). Nothing was dropped silently; edit that
comment if the ruleset changes.

`src/tracker/restyle.py` (`python -m tracker.restyle`, and the manual-only
`.github/workflows/restyle-descriptions.yaml`) applies the same rules to
`description` on rows already in `data/discoveries.db`, which predate the
rules, then regenerates `docs/events.json` through `feed.write_json_feed`.
It is a **restyle, not a re-extraction**: the model gets the event name and
the stored description and nothing else — not the scraped page, which would
invite re-deriving a description from source, producing new claims with no
verification step (section 5) behind them. The prompt forbids adding any
claim not already in the text it is given, and permits deletion only. A model
call that fails, returns an unknown id, omits an id, or returns an empty
string leaves that row's original description in place and is reported;
descriptions are never blanked. Every batch failing exits non-zero, the same
systemic-failure rule as section 4.

## 5. Verification (anti-hallucination)

An LLM web-search call can name a plausible event that does not exist, or get
its URL wrong. `src/tracker/verify.py` treats every candidate as unverified
until checked independently of the model's own claim, fetching the
candidate's URL for real (`tracker.net.fetch` — robots.txt checked first, one
retry, 20s timeout, identifying UA — same politeness ground rules as v1) and
comparing its text against tokens from the claimed event name. This produces
one of three outcomes, not a pass/fail:

- **VERIFIED** — the page loaded and mentions the claimed name. Kept, shown as
  confirmed.
- **REJECTED** — the page loaded but does *not* mention the claimed name. This
  is the actual hallucination signal (wrong URL, or an invented event) and is
  dropped — logged in the run summary for debugging, never written to
  `data/discoveries.db` or the digest issue.
- **BLOCKED** — the page could not be fetched at all: robots.txt disallow,
  403, timeout, or a candidate name too short to check meaningfully. This says
  nothing about whether the event is real, only that this checker can't see
  the page — a bot-blocking WAF or a disallowed crawler path is exactly as
  likely on a genuine, reputable event's site as on a fake one. **Kept, not
  dropped**: written to `data/discoveries.db` with
  `verification_status=blocked` and shown in the digest issue under "Possibly
  relevant — not verified," distinct from the confirmed table, so a reader
  can manually check the ones automated verification couldn't reach instead
  of never seeing them. (Earlier version of this pipeline dropped these
  silently; changed 2026-08-26 after a real run produced 3 blocked candidates
  — 2 robots.txt-disallowed, 1 an OpenReview PDF returning 403 — that were
  plausible, on-topic, real-looking events with no way to tell from the
  reject reason alone that they weren't hallucinations.)

This is still a heuristic, not a guarantee: it catches "invented URL" and
"wrong page" but not "real page, wrong claimed date," and a BLOCKED item is
explicitly *not* confirmed — it is flagged as worth a human look, not
presented as verified. Known gap — see section 9.

## 6. Dedup

`src/tracker/state.py` keeps a normalized-URL set in `data/seen.json`,
committed back to the repo each run (Actions runners are ephemeral, same as
v1). A candidate whose normalized URL was already kept (VERIFIED or BLOCKED —
see section 5) in a previous run is skipped before verification runs again.
Within one run, candidates are also deduped by normalized URL before
verification, since more than one search query can surface the same event. A
BLOCKED item is marked seen the same as a VERIFIED one — reported once, not
re-flagged indefinitely; if a block turns out to be transient, re-checking it
means removing its URL from `data/seen.json` by hand (README: "Re-detecting an
item you've already seen").

## 7. Output

Every kept candidate — VERIFIED or BLOCKED, not previously seen (section 5) —
is:

1. Inserted into `data/discoveries.db` (`src/tracker/db.py`), a SQLite file
   committed back to the repo each run — the cumulative primary artifact,
   same role as v1's `data/venues.csv`. Insert-only, one row per event, never
   updated: `id` (a `uuid4`, assigned at insert — this is what "each item
   scraped gets a uuid" means concretely), `date_scraped` (UTC date the row
   was inserted, immutable after that), plus every `Candidate` field
   (name/dates/location/description/organizer/url/query/rationales) and
   `verification_status`/`verification_note` from section 5.
2. Included in one weekly digest GitHub issue (`[weekly-scan] N new item(s), M
   unverified — <date>`), skipped entirely when nothing was kept — no issue on
   an empty week, matching v1's low-noise intent. VERIFIED and BLOCKED items
   get separate tables in the issue body, so a BLOCKED item is never presented
   as confirmed. One issue, not one per item: expected weekly volume is a
   handful of genuinely new items, not a feed's worth.

A run summary (query results, rejections with reasons, skip counts) is
written to `$GITHUB_STEP_SUMMARY`.

**SQLite-in-git tradeoff, accepted deliberately (2026-08-26)**: a SQLite file
doesn't diff or delta-compress the way an append-only CSV does — every commit
stores something closer to a full rewrite of the changed pages, so repo size
grows faster per row than the CSV did. Chosen anyway over a hosted DB because
it needs no new infra/secret/cost and stays inside the existing "commit state
back to the repo" pattern; revisit if `data/discoveries.db` size becomes
noticeable (`du -h data/discoveries.db` — no threshold picked yet for
"noticeable").

## 7a. Public feed and website

`src/tracker/feed.py` regenerates `docs/events.json` from the *entire*
`data/discoveries.db` table (not just this run's new rows) every real run, so
it always reflects the cumulative current state, and is committed alongside
`data/` in the same workflow step. Shape:

```json
{
  "generated_at": "<ISO 8601 UTC timestamp of this run>",
  "count": <int>,
  "events": [ {"id": ..., "date_scraped": ..., "name": ..., ...all db.py columns... } ]
}
```

This repo was made **public** on 2026-08-26 specifically so this feed has a
stable public URL — GitHub Pages cannot serve a private repo's contents
without GitHub Enterprise, which this org does not have confirmed. Checked
before flipping visibility: full working tree and `git log --all -p` (every
branch) for secret-shaped strings — none found beyond a test fixture's literal
`"test-key"`. `config/scope.yaml`'s `meta.maintainer_email` is a real personal
address and is now public as a side effect; flagged to the user, not yet
changed.

The feed includes *all* kept events regardless of `verification_status` —
deciding whether to display a `blocked` item is left to whatever consumes the
feed, not filtered out here (section 5's "don't silently drop a BLOCKED item"
reasoning applies to the public feed too). A consumer wanting only confirmed
events should filter on `verification_status == "verified"` itself.

**Integration with the actual largeagentsystems.org site is not done here.**
This repo publishes the feed; wiring the live site to fetch and render it is
a change in that site's own codebase, which this repo has no access to.
GitHub Pages itself also isn't enabled yet as of this writing — needs `docs/`
to exist on a real branch first (chicken-and-egg with the first real run),
and a one-time `gh api repos/{owner}/{repo}/pages` call pointing at the branch
that has it.

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
which model is configured. `discover.run_query` also now returns
`usage.cost`/`prompt_tokens`/`completion_tokens` from OpenRouter's response,
logged per-query and summed per-run in the run summary, so actual spend is
observed on every run going forward rather than assumed.

Measured after the fix (first full real run, 2026-08-26, 10 queries against
`anthropic/claude-sonnet-5`, n=10 per-query costs): $0.0431–$0.0710, mean
$0.0571, total $0.5713 for the run. ~17x cheaper than the ~$1/query estimated
from the pre-fix $2/2-query spend, but still ~8x the $0.007 flat search fee —
the rest is Sonnet's own token cost (candidate lists with two rationale
sentences each, at Sonnet's $10/M output price). At 52 runs/year: ~$29.70/year
for search this size. **Correction to the original claim in this section**:
model choice does NOT barely move cost once the search fee is pinned and
small relative to token cost, as it appeared to when the flat fee was assumed
to dominate — Sonnet's per-token price is the majority of the per-query cost
here. A cheaper model (e.g. `openai/gpt-oss-120b`, ~55-270x cheaper per
token — see the conversation that produced this spec for the OpenRouter
pricing pull) would very likely cut this further; not yet tried, and quality
on this specific task (structured-output reliability, reputability judgment)
is unverified for any model, Sonnet included.

## 9. Non-goals / known gaps

- No headless-browser rendering. If a candidate's page is JS-rendered and
  `requests.get` sees an empty shell, the page still returns 200 with no
  matching text, so verification marks it REJECTED (not BLOCKED) — a false
  negative distinct from the robots.txt/403/timeout cases the BLOCKED status
  now catches (section 5). Not solved in v2 — same non-goal as v1 had for
  Tier C sources.
- Verification checks *existence*, not *date accuracy*. A candidate could be a
  real, reputable, on-topic event with a stale or wrong date and still pass.
  Not checked in v2.
- No cap yet on total OpenRouter spend per run beyond `search.queries` length ×
  `search.max_results_per_query`. If this becomes a concern, add a hard token/
  request budget in `main.py` — not needed at the current ~10-query scale.
- Duplicate-but-differently-worded candidates from two different search
  queries (e.g. the model paraphrases the same event's name two different
  ways with two different URL casings/query-strings that `normalize_url`
  doesn't collapse) can both pass dedup. Not observed in the first full run
  (2026-08-26, 32 verified + 3 blocked, no duplicates seen) but that's one
  small sample, not a guarantee — watch `data/discoveries.db` over time and
  tighten `normalize_url` if it happens.
- A BLOCKED item is marked seen after being flagged once (section 6) — if its
  block was transient (a temporary WAF rate-limit, a flaky timeout) rather
  than permanent (a standing robots.txt disallow), it will not be
  automatically re-attempted on a later run. No automatic retry-after-N-runs
  exists in v2; manual removal from `data/seen.json` is the only way to
  re-check one.
