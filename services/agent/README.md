# Wildfire policy agent prototype

Feasibility harness for routing one trusted user's natural-language question to
the four read-only backend services. It is an API experiment, not a production
chat product.

## Architecture boundary

The deterministic tier handles only high-confidence requests whose operation,
dataset/metric, scope, and required time/location slots are explicit:

- filtered count/list → `data_query_records`
- explicit single-dataset ranking → `data_query_rank`
- coordinate context → `data_query_spatial`
- map/time series/detail → a visualization tool
- fully specified utility/region/period comparison → `comparison_run`
- explicit cell/date, coordinate/date, county/date, or utility/date risk → `risk_forecast` chain
- a risk map or surface question with a date and no place → `risk_surface`
  (statewide hindcast; a router-only tool, not in the model's tool list)
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
logged.

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
  utility count gets the attribute count in the same way.
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

## Model provider and Jev

- Default model tier: Ollama with `qwen2.5:7b` (`AGENT_MODEL`).
- `AGENT_LLM_PROVIDER=openrouter` sends routing and synthesis to OpenRouter
  (GPT-6 Luna, with Sol for retry turns) with native `tool_choice` and strict
  structured outputs. It is off by default, also needs
  `AGENT_ALLOW_REMOTE_PROVIDER=true` and `OPENROUTER_API_KEY`, and
  [`docs/OPENROUTER.md`](../../docs/OPENROUTER.md) records why production has not
  switched yet.
- `AGENT_JEV_MODE` is `off` by default; `shadow`, `tool_pick`, and
  `tool_pick_template` are described in [`docs/JEV_SHADOW.md`](../../docs/JEV_SHADOW.md).
  `AGENT_JEV_BACKEND` is `typesafe` or `openrouter`. `decide` (off by default)
  runs the Jev-first decider right after `route_question`: backstops first, then
  Jev's disposition behind a 0.8 decline gate and a 0.9 answer gate
  ([`docs/JEV_DECIDE.md`](../../docs/JEV_DECIDE.md), `decisions/decide_mode.py`).
- `AGENT_SLOT_PLAN` (off by default) plans a deferred multi-entity question as
  several deterministic calls from router slots
  ([`docs/JEV_MULTI_TOOL.md`](../../docs/JEV_MULTI_TOOL.md)). With decide on,
  decide runs first and the planner acts only on questions decide leaves as answer.
  Jev's plan mode was archived and is not accepted.

## Run

The process binds `:8004` even when Ollama is unreachable. Deterministic
routes still answer; model-tier questions return an offline error payload
instead of failing startup or returning HTTP 500. Context is warmed lazily
on the first `complete()` after the GPU comes back. `/health` checks the
model with `GET /v1/models` (it does not load the model) and calls `/health` on
each of the four backend services.

Start the four backend services on ports 8000–8003, then:

```powershell
$env:PYTHONPATH = "."
$env:PYTHONIOENCODING = "utf-8"
uvicorn services.agent.app:app --port 8004 --app-dir .
```

- `GET /health`
- `POST /ask` with `{"question":"How many PG&E ignitions were there in 2024?"}`: leave this path unchanged for eval
- `POST /ask/stream`: SSE harness progress for the website Ask panel (not a second answer path)
- `GET /artifacts/{ref}` for a non-expired full backend payload

The service is single-exchange: it stores no conversation history.

## Evaluation

The 107 cases in `eval/cases.json` (14 of them `force_model`) cover
single-service, multi-service, ranking, required caveats,
clarifications/refusals, recovery, and partial-HTTP-200 detection. Any subset
of the eight matrix cells can be selected:

```powershell
python -m services.agent.eval.runner `
  --models qwen3:4b,qwen3:8b `
  --thinking off,on `
  --modes prompt,constrained
```

To match production (the runner itself defaults to `qwen3:4b`):

```powershell
python -m services.agent.eval.runner `
  --models qwen2.5:7b --thinking off --modes constrained
```

Initial staged baseline only:

```powershell
python -m services.agent.eval.runner `
  --models qwen3:4b --thinking off --modes prompt
```

Use `--case-ids id1,id2` for focused development. Results are written to
`eval/REPORT.md`, `summary.json`, `summary.csv`, and per-case gzip raw logs.

The stop gate is **50% model-tier routing accuracy**, evaluated separately from
deterministic bypasses after at least five model-tier cases. Below the gate, the
runner stops before subsequent selected cells.

## Ollama limitations

Ollama's OpenAI-compatible endpoint does not support `tool_choice`. On the
Ollama path, routing instead sends a JSON call envelope (`{"calls": [...]}`,
drawn from the candidate tools) as the native `/api/chat` `format`; the array
may be empty, so the harness still cannot force a tool call (the OpenRouter
path sends `tool_choice: "required"`). Evaluation therefore records no-tool
responses and valid direct-answer attempts before evidence. The harness blocks
either from becoming a factual answer.

The installed Qwen3 model template forces a thinking prefix even when
`reasoning_effort=none`. For the thinking-off cell, startup creates an
idempotent template-only `*-agent-nothink` alias that uses the exact base
weights and closes the thinking block before generation. Routing and
constrained synthesis go through native `/api/chat`; prompt-mode synthesis uses
`/v1/chat/completions`. Reports retain the base model name.
