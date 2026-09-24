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
  result would be absent, not zero
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
  When a question is missing more than one item (year, place, dataset), one
  clarification asks for all of them and ends with an example rephrasing built
  from what the question already named (`clarify_missing.py`). The rule id does
  not change.

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

## Derived arithmetic

Synthesis may state only numbers found in evidence or caveats, and the model
never does arithmetic. When a question asks for a change, difference,
increase, decrease, percent change, or ratio, `derived.py` computes those
values from the successful primary counts before synthesis and adds them as
one `harness_arithmetic` evidence item (`evidence_derived_...`). Each
derivation carries its `source_evidence_ids`. Pairs are the same entity across
periods (earliest first, with `direction`) and two entities in one period
(with `larger`). A zero base gives a null percent or ratio with a reason, not a
number. Companion reads are never used. The trajectory records a
`derived_evidence` event, and the deterministic fallback renders the same
values.

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
  runs the Jev-first decider right after `route_question`: backstops first, then
  Jev's disposition behind a 0.8 decline gate and a 0.9 answer gate
  ([`docs/JEV_DECIDE.md`](../../docs/JEV_DECIDE.md), `decisions/decide_mode.py`).
  Jev owns the disposition and the router owns the clarification or refusal wording.
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
| `backstop` | `rule` | A router hard backstop fired (`decide_mode.BACKSTOP_RULES`), in any mode |
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
