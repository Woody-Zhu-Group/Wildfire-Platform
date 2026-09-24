# Wildfire policy agent prototype

Feasibility harness for routing one trusted user's natural-language question to
the four read-only backend services. It is an API experiment, not a production
chat product.

## Architecture boundary

The deterministic tier handles only high-confidence requests whose operation,
dataset/metric, scope, and required time/location slots are explicit:

- filtered count/list → `data_query_records`
- explicit single-dataset ranking → `data_query_rank`. A ranking restricted to
  an HFTD tier clarifies (rank statewide or map the tier) once its dataset
  resolves; bare "ignitions" resolves to CPUC ignitions and bare "outages" to
  EPSS, as without the tier, and a tier ranking with no dataset asks for one
- a count and a time series asked together, including a chart plus its total,
  with one dataset, at most one utility and county, and one window →
  `data_query_records` then `visualization_create` (`multi_intent_count_and_trend`).
  A breakdown (by county, by utility, each year, annual) or a second utility or
  county defers to the model instead of one collapsed pair
- a map plus a count ("and how many there were") with one dataset and window →
  `data_query_records` then the map (`multi_intent_count_and_map`), never the
  map alone
- an EPSS read for a utility other than PG&E (count, series, map, or pair) →
  clarification `epss_non_pge_utility`: EPSS rows exist only for PG&E, so the
  result would be absent, not zero. The same rule covers every comparison on an
  EPSS metric (`epss_outage_count`, `epss_to_ignition_ratio`: period, utility,
  HFTD, or an open comparison) where no named utility is PG&E; that
  clarification offers the utility's PSPS events or CPUC ignitions. A
  comparison that names PG&E still runs, and the non-PG&E side is null with
  its reason. "Outage" in a comparison means EPSS unless the question names PSPS
- a comparison that would drop a named utility, county, HFTD tier, or month
  (one `comparison_run` compares utilities, tiers, or two whole years of one
  scope) → clarification `unexpressed_filter_constraints`, for every metric.
  "Compare SCE ignitions tier 2 vs tier 3 in 2023" used to return statewide
  tier counts. A utility counts as carried when the metric's dataset covers
  only that utility (a PG&E EPSS tier comparison runs). The Jev comparison
  template applies the same check (`comparison_uncarried_constraints`), and on
  the model path any run that includes a comparison must cover every named
  utility, county, year, and tier or it stops with a clarification
- a US-sample question restricted to a utility (map, count, series, rank, or a
  comparison with CPUC) → clarification `us_sample_utility_filter` (label rule
  J): the sample has no utility column, so the router offers the national
  sample or that utility's CPUC ignitions instead of passing or dropping it
- coordinate context → `data_query_spatial`
- map/time series/detail → a visualization tool
- fully specified utility/region/period comparison → `comparison_run`
- explicit cell/date, coordinate/date, county/date, or utility/date risk → `risk_forecast` chain
- a risk map or surface question with a date and no place → `risk_surface`
  (statewide hindcast; a router-only tool, not in the model's tool list)
- a question about the risk model's accuracy or performance with no place,
  cell, tier, or time → `risk_metrics` (router-only, `GET /metrics`; rule
  `risk_model_metrics`), which opens the Risk model performance card and
  carries the cNHPP caveats; a 503 is reported with the service's reason
  ("How well does the model predict fires?" counts: a predict word alone is
  not a future request, but a forward phrase, will, or a later year still
  refuses or clarifies as before)
- known unavailable domains (CPZ, cost, air quality, evacuation, translation,
  personnel, satellite imagery, leadership, optimization, damage, live web,
  future predictions) → refusal
- missing risk metric, location, region definition, or time → clarification.
  When a question is missing more than one item (year, place, dataset, ranking
  grouping), one clarification asks for all of them and ends with an example
  rephrasing built from what the question already named (`clarify_missing.py`).
  The missing items come from what the question reads and the router's slots,
  not the rule (a place is computed only for risk questions; for counts, maps,
  and charts only the router's place rules ask for one), so any rule's text (the router's or, in decide mode, Jev's) asks for
  all of them, with options from the registry (`RANK_MEASURES`,
  `COMPARE_MEASURES`, `SERIES_DATASETS`). The rule id does not change.

Compositions, cross-dataset questions, and requests not matching those strict
rules go to the model. Every response logs `path`, `rule`, and tool trajectory.

On the model path the harness holds tool calls to the resolved years and date
range (`time_resolve.apply_harness_years`), strips invented utilities
(`tools._strip_ungrounded_utilities`), and drops model-proposed filters (circuit
id, HFTD tier, county, coordinates) and sentinel values that the question and
router slots do not support (`grounding.ground_model_filters`); each drop is
logged. Enum values the tool schema accepts are filters too: `utility=untagged`
runs only when the question asks about untagged, unattributed, or non-utility
records (`grounding.question_allows_untagged`), and a CAL FIRE
`incident_type_mode` of `all` or `untyped` runs only when the question asks for
every incident type or for records with no type
(`grounding.question_incident_type_modes`). Otherwise the value is dropped and
the tool runs at its default, never at a narrower or wider scope the user did
not ask for.

## Grouped tools

| Tool | Responsibility |
|---|---|
| `data_query_records` | Filtered counts and small record samples |
| `data_query_rank` | Top-N inside one dataset (county / utility / circuit) |
| `data_query_spatial` | Point context or one polygon-contained summary |
| `visualization_create` | Map layer or time series |
| `visualization_inspect` | Utility territory or one event/circuit detail |
| `risk_forecast` | Historical fitted place/date risk (cell, point, county, or PGE/SCE/SDGE) |
| `comparison_run` | Utility, HFTD/county, or period comparison |

Pydantic validates all arguments before HTTP execution. Tool errors contain a
stable code, `recoverable`, suggested action, and field errors. Backend HTTP 200
responses are contract-checked so partial data cannot silently degrade into an
answer. Full payloads are stored in a bounded 15-minute artifact store; only
summaries enter model context.

## Deterministic qualifications

The caveat engine reads response metadata and may issue qualification-only
companion calls:

- Every successful CPUC ignition read carries the utility-caused definition
  (`cpuc_utility_caused`).
- Every utility-scoped CPUC ignition count is paired with the same-period
  spatial containment count, for any utility (not only PG&E); a spatial
  utility count gets the attribute count in the same way. One caveat per
  utility lists the pair for every period checked ("510 in 2020, 374 in
  2023"); a single period keeps the one-pair wording.
- CAL FIRE answers report missing incident-type and utility-tag counts. The
  incident-map-feed caveat (`calfire_map_feed_counts`; listed 133→611 is
  posting, not occurrence) attaches when a CAL FIRE answer spans 2023 and 2024
  or compares CAL FIRE **counts** across two or more years.
- US ignition answers state that the CA-heavy FireCastRL data is a sample, not
  a census, and is not comparable to CPUC.
- EPSS answers state that warehouse coverage is PG&E-only.
- Fitted risk answers state that cNHPP is fitted on a 0.24° grid (cell
  aggregates, not circuit-level risk) and that cNHPP versus NHPP is a
  statistical tie; answers that score cell 461 also note its mean-filled
  vegetation covariates.

If a required companion call or metadata field fails, the primary result is
suppressed rather than returned without its qualification.

### Time windows the model chose

Widening a model call to the resolved range applies only when a single call
narrows the range with no other call covering the rest. The harness collects
the window of every model call for one question across all model turns
(`CallWindows`), because hosted models often send one call per turn. When
those calls name distinct periods (a 2019 count and a 2022 count for "from
2019 to 2022", or a July count and an August count for "July 2024 and August
2024"), in one turn or across turns, the model is splitting the question into
periods on purpose and every call is kept as written (`_hold_resolved_window`).
When Jev reads the question as a comparison or trend (below), a lone call on
one endpoint year is kept as written too, even in the first turn: coverage
asks for the other endpoint in a later turn, or declines. Nothing here reads
the question's wording: there is no list of change words. A written range ("between 2020 and 2023") is always one span
in the time resolution; years or months listed separately ("2020 vs 2023",
"July 2024 and August 2024", `named_months`) are separate periods, and a count
over listed months defers to the model rather than counting one month. Years
the question never named are still rejected.

Whether two endpoint reads cover a written range takes meaning: "how did X
change from 2020 to 2023" wants the two endpoints, "how many from 2020 to
2023" wants every year. That is Jev's existing intent fact, with no new
wording and no payload change. In decide mode, when Jev's facts are present
and Jev reads the intent as compare or trend at or above the decline gate
(`AGENT_JEV_DECIDE_MIN_CONFIDENCE`), the range names its endpoints for the
uncovered-entities check. Otherwise (count or records intent, below the gate,
a Jev error, or Jev off) every year in the range must be covered, as on main,
so a total answered from two endpoint reads still declines, and a lone
endpoint call is widened to the span, as on main. The reading is recorded on
the `jev_decide` slot as `jev_intent` and `jev_intent_confidence`.

Calendar months named as separate periods ("July 2023 and August 2023",
"July and August 2023", "October 2023 than October 2022";
`named_month_periods`, which needs a year after the month, so "may" the verb
never counts) are coverage entities like years (`month:YYYY-MM`). A call
covers a named month when its window overlaps that month and reads no month
the question did not name: a July call covers July only, so a July and August
question fetched one month per turn continues until August is read, or
declines; one call over July through August covers both; a call over all of
2023 covers neither. A written month range ("from March to June 2023") is one
span, not separate months.

## Derived arithmetic

Synthesis may state only numbers found in evidence or caveats, and the model
never does arithmetic. `derived.py` computes the difference, percent change,
and ratio
values from the successful primary counts before synthesis (and on the
deterministic path before the answer is rendered) and adds them as
one `harness_arithmetic` evidence item (`evidence_derived_...`). Each
derivation carries its `source_evidence_ids`. Which pairs to form comes from
the structure of the calls, not from the question's words: an entity read in
two or more periods gets its change over time (earliest first, with
`direction`); two entities get their difference (with `larger`) only when every
call of that measure shares one period and there are exactly two entities, so
four calls over two periods never produce cross-entity rows the question did
not ask for. A zero base gives a null percent or ratio with a reason, not a
number. Companion reads are never used. The trajectory records a
`derived_evidence` event, and the deterministic fallback renders the same
values.

Whether the question wants those figures is meaning, not call structure:
"List PG&E ignitions in 2019 and in 2023" reads two periods and asks for no
change. So the figures are attached only when decide mode has Jev's facts and
Jev's intent fact reads compare or trend at or above the decline gate
(`AgentOrchestrator._jev_reads_change`, the same reading the coverage rule
uses). Otherwise (count or records intent, below the gate, a Jev error, or
Jev off) they stay out of the evidence, so neither synthesis nor the fallback
text can show them, and the trajectory records `derived_evidence_withheld`.
With Jev off, a change question over two model reads therefore gets both
counts and no computed change.

## Dataset coverage in the executor

`ToolExecutor.execute` is the one guarantee that a read never reports a zero
for a utility or period its dataset does not cover, on every path (router,
model, Jev templates, slot planner). Coverage is measured, never declared: the
loaders write `shared/dataset_coverage.json` (which utilities each dataset has
rows for, and each one's first and last date; see `db/README.md`), and the
registry reads it (`dataset_coverage_gap`). A utility is covered from its first
row to the dataset's last row; a read with no utility is checked against the
dataset's own dates. For example, CPUC has rows for PacifiCorp (from
2025-04-24), PG&E, SCE, and SDG&E only; PSPS for PG&E, SCE, SDG&E, and Liberty
from 2021-10-11; EPSS for PG&E from 2021-11-01.

`services/agent/coverage.py` applies that to one call (`call_coverage_gap`:
the call's dataset, named utilities, and periods, a year becoming that
calendar year). A `data_query_records`, `data_query_rank`,
`data_query_spatial`, `visualization_create`, or `comparison_run` call with
nothing covered returns `ok: false` with code `not_covered` (not recoverable),
the reason, and `not_covered` details (dataset, utilities, periods, covered
utilities, alternatives), and no service is called. A comparison with one
covered side (a utility, or one of two periods) runs, and the uncovered side
comes back null with its reason. The orchestrator turns a `not_covered` result
into a clarification on the deterministic path, the Jev template path, and the
model loop (which stops at the first one). The router checks the same call
before it answers (`dataset_not_covered`, or `epss_non_pge_utility` and
`us_sample_utility_filter` when a named utility has no rows in EPSS or the US
sample at all: label rules I and J), and a `*_missing_year` clarification
becomes the not-covered one when the dataset has no rows for the named utility
at any date, since a year cannot help. The Jev templates and the slot planner
read the same check (`not_covered_rule` gives the rule id); none of them names
a utility.

A clarification offers only data measured coverage has: an alternative
dataset (the spec's `not_covered_alternatives`, in order) only where it covers
every named utility in every asked period; another utility's rows only when
the dataset covers exactly one in that period (EPSS: PG&E); and a named
utility's own later dates ("PG&E's PSPS events from 2021-10-11 on"). So
"Liberty CPUC ignitions in 2023" offers Liberty's CAL FIRE incidents but not
PSPS (Liberty's PSPS rows start 2024-11-11), and "PacifiCorp vs PG&E PSPS in
2019" offers CAL FIRE only.

Coverage also applies per number. One result can hold counts for several
datasets (a spatial summary for SCE territory counts CPUC ignitions, EPSS
outages, and CAL FIRE incidents inside it, and EPSS comes back 0 because it
holds PG&E circuits only). After any tool returns, `mark_uncovered_counts`
resolves each key of the result's `counts` to its dataset through the registry;
a count whose dataset does not cover the named utility in the call's period
becomes `None`, with the reason under `summary.not_covered`. An unknown count
key raises. A comparison's values are checked side by side the same way (a
service value outside coverage is nulled even if a service returns a number),
and a covered count whose period runs past measured coverage gets a note
saying which part it counts (`summary.coverage_notes`: "the count in 2021
covers only 2021-11-01 to 2021-12-31"). Then:

- the answer text says the count is not covered, once, on every path
  (`_with_not_covered_notes`, the last step of `_ensure_readable_answer`); the
  deterministic line reads `epss_outages=not covered`;
- the stat card for that count has `value: null` and `unavailable_reason`
  (`StatCardViewParams` allows no value only with a reason, and grounding
  accepts it only when the cited evidence has no value for that count either),
  and the website shows "Not available" with the reason instead of a number;
- a synthesized answer that states a number beside that dataset's registry
  names ("0 EPSS outages") fails grounding (`_uncovered_count_claims`), even
  when the same number appears elsewhere in the evidence, and falls back to
  the evidence.

A missing count is never a zero either. Where a count used to default to 0
(`or 0` in the count, spatial, and rank renders), a missing total or count now
renders as not available with its reason: the stat card has no value and an
`unavailable_reason` (the service's `empty_reason`, or that the service returned
no total), the fallback text reads `count: not available`, and a rank headline
without a group total says the number of groups is not available. On the
website, a medical-exposure total whose every outage lacks the value, and the
cumulative-acres count of incidents without acreage before the records load,
also read "not available" instead of 0.

A comparison answer (`_render_comparison_answer` in `orchestrator.py`) is
plain sentences on every comparison route. A null value is never shown as
`None`: the sentence names the service's reason, says no change can be
computed when either period is null, and names data that does exist (for
EPSS, the utility's PSPS events and CPUC ignitions; for a county PSPS
comparison, the datasets that carry a county).

## Views

Every count gets its own stat card; only non-count stat cards (risk and
spatial metrics) are capped at three. The website renders ranking comparisons
only, so a multi-entity count answer (two utilities in two years) shows one
card per count rather than a utility-by-year comparison.

## Model provider and Jev

- Model tier: OpenRouter (GPT-6 Luna, with GPT-6 Sol for retry turns) with
  native `tool_choice: "required"` for routing and strict structured outputs
  for synthesis. `OPENROUTER_API_KEY` is required, and startup fails with a
  clear message until `AGENT_ALLOW_REMOTE_PROVIDER=true` confirms that
  questions may leave the host. `AGENT_LLM_MODEL` and
  `AGENT_LLM_FALLBACK_MODEL` override the models. The local Ollama/qwen path
  was removed on 2026-09-24 ([`docs/OPENROUTER.md`](../../docs/OPENROUTER.md)).
- `AGENT_JEV_MODE` is `off` by default; `shadow`, `tool_pick`, and
  `tool_pick_template` are described in [`docs/JEV_SHADOW.md`](../../docs/JEV_SHADOW.md).
  `AGENT_JEV_BACKEND` is `typesafe` or `openrouter`. `decide` (off by default)
  runs the Jev-first decider right after `route_question`: backstops (live, future,
  city, HFTD constraint) first, topic keyword refusals decided by Jev's `off_topic` (refused at
  the decline gate, lifted only at the answer gate) with the keyword rule as fallback
  (issue #97), then
  Jev's disposition behind a 0.8 decline gate and a 0.9 answer gate
  ([`docs/JEV_DECIDE.md`](../../docs/JEV_DECIDE.md), `decisions/decide_mode.py`).
  Jev owns the disposition and the router owns the clarification or refusal wording,
  except that the generic `ranking_missing_slots` question yields to Jev's more specific one.
- `AGENT_SLOT_PLAN` (off by default) plans a deferred multi-entity question as
  several deterministic calls from router slots
  ([`docs/JEV_MULTI_TOOL.md`](../../docs/JEV_MULTI_TOOL.md)). With decide on,
  decide runs first and the planner acts only on questions decide leaves as answer.
  Jev's plan mode was archived and is not accepted.

## Run

The process binds `:8004` even when OpenRouter is unreachable. Deterministic
routes still answer; model-tier questions return an offline error payload
instead of failing startup or returning HTTP 500. `/health` checks the model
with `GET /models` on the provider and calls `/health` on each of the four
backend services.

Start the four backend services on ports 8000–8003, then:

```powershell
$env:PYTHONPATH = "."
$env:PYTHONIOENCODING = "utf-8"
uvicorn services.agent.app:app --port 8004 --app-dir .
```

- `GET /health`
- `POST /ask` with `{"question":"How many PG&E ignitions were there in 2024?"}`: leave this path unchanged for eval
- `POST /ask/stream`: SSE harness progress for the website Ask panel (not a second answer path)

Both carry `decision_source`, who made the answer, clarify, or refuse decision: in the `/ask` response and in the stream's `routing` event (`services/agent/decisions/provenance.py`). It never includes Jev's raw payload.

| `source` | Fields | When |
|---|---|---|
| `backstop` | `rule` | A router hard backstop fired (`decide_mode.BACKSTOP_RULES`), in any mode; outside decide mode also a topic keyword refusal (`routing.TOPIC_JUDGMENT_RULES`) |
| `jev` | `disposition`, `confidence` | `AGENT_JEV_MODE=decide` applied Jev's answer, clarify, or refuse |
| `router` | `why` | The router's decision stands |

`why` for the router: `jev_below_gate` and `jev_agreed` (both with `jev_disposition` and `jev_confidence`), `jev_error`, `jev_timeout`, `jev_daily_cap` (the per-process `AGENT_JEV_DAILY_CALL_CAP` on API calls was reached, so Jev was not asked), `verified_fact` (the resolver or the chosen tool call proved the time or place, decide's `code_verified`, `contradicts_slot`, or `slot_unused`), `router_only_route` (decide's `regex_only` or `router_only_tool`), `jev_off`, `jev_shadow` and `jev_tool_pick` (modes where Jev does not make this decision), and `jev_skipped` (decide mode skipped a forced-model eval request). Every value also carries `mode`, the `AGENT_JEV_MODE` in force.
- `GET /artifacts/{ref}` for a non-expired full backend payload

The service is single-exchange: it stores no conversation history.

## Evaluation

The 107 cases in `eval/cases.json` (14 of them `force_model`) cover
single-service, multi-service, ranking, required caveats,
clarifications/refusals, recovery, and partial-HTTP-200 detection. Each
`--models` entry is one cell. The default is the production model, and every
model-path case spends OpenRouter credits, so run it only with an explicit
budget and prefer `--case-ids` for focused work:

```powershell
$env:AGENT_ALLOW_REMOTE_PROVIDER = "true"
python -m services.agent.eval.runner --models openai/gpt-6-luna
```

Use `--case-ids id1,id2` for focused development. Results are written to
`eval/REPORT.md`, `summary.json`, `summary.csv`, and per-case gzip raw logs.

The stop gate is **50% model-tier routing accuracy**, evaluated separately from
deterministic bypasses after at least five model-tier cases. Below the gate, the
runner stops before subsequent selected cells.

## Provider notes

Routing sends the candidate tools with `tool_choice: "required"`, so the
model cannot answer without a tool call. Evaluation still records no-tool
responses and direct-answer attempts before evidence, and the harness blocks
either from becoming a factual answer. Earlier local-model runs and their
Ollama workarounds are historical records under `eval/` and are not comparable
to hosted runs.
