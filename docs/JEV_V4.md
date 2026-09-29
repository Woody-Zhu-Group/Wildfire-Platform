# Jev-first decide mode (v4)

**Historical experiment, not the current `decide` runtime.** PR #113's review
rejected replacing production v3. `decide` retains v3; the new experiment is
`router_gate`, documented in [JEV_ROUTER_GATE.md](JEV_ROUTER_GATE.md).
The v4 policy and runner below are retained only as a comparison baseline.
The following sections describe the previous prototype, not deployed behavior.
An offline [argmax ablation](JEV_V4_ARGMAX_RESULTS_20260928.md) disables confidence
gates while keeping the same captured responses and structural checks.
The subsequent [live missing-scope experiment](JEV_V4_SCOPE_RESULTS_20260929.md)
replaces one geographic fact without changing production v3 or the old payloads.

With `AGENT_JEV_MODE=decide`, Jev owns semantic intent and disposition. The
runtime does not call `route_question`, its keyword candidate selector, or the
semantic slot planner. Other modes and explicit `force_model` evaluation are
unchanged. The feature remains opt-in; this document does not claim deployment.

`question_context` parses explicit dates and named entities without choosing a
task. `decisions/jev_first.py` gates Jev's v4 facts, checks date and dataset
capabilities, and either asks for clarification, refuses, selects a supported
specialized call, or gives the argument-building model tools selected by Jev's
intent. Tool validation, measured coverage, filter grounding, caveats and
evidence checks still run. A period comparison cannot complete with just one
total over its whole range.

- The 0.8 gate applies to intent and refusals. The 0.9 answer gate applies to
  the on-topic and negative refusal facts. Below the applicable threshold,
  clarify without querying data. A timeout, missing backend, or exhausted call
  budget returns an error, with no semantic-router fallback.
- V4 adds `model_metrics`, `risk_surface`, `risk_map_kind`, `model_performance`,
  `risk_grid`, and `advice_or_judgment`. Model metrics and statewide risk or
  residual surfaces are compiled to existing harness tools, not new tools.
- V3 payloads and `decide_mode.py`'s combined policy remain for reproducing the
  existing captured v3 evaluations and for the unchanged shadow/tool-pick
  experiments. Their reported accuracy is not evidence for v4.

## Prepared evaluation

[`jev_v4_cases.json`](../services/agent/eval/jev_v4_cases.json) contains 36
development cases: year and month comparisons, count controls, maps and charts,
lookups, model metrics and surfaces, advice, live questions, and missing or
unsupported constraints. It was written during implementation and is **not a
clean holdout**. Existing frozen sets and gold labels are unchanged. Review this
new set's labels before treating it as an agreed acceptance set.

Preview, with no credentials or API calls:

```powershell
python -m services.agent.eval.jev_v4
```

The default is five repeats, or at most 540 Jev requests for all 36 cases. It
calls Jev only, not the agent's generative model or data services. Low-confidence,
timeout and error behavior is tested separately with scripted responses.

When ready, put `TYPESAFE_API_KEY` in the existing repo `.env` or process
environment. For the OpenRouter backend, use `OPENROUTER_API_KEY` and pass
`--backend openrouter`. Do not commit keys. Example of an explicitly budgeted
run, into a new directory of your choice:

Run these commands in the repository's Python environment, with
`python -m pip install -r requirements.txt` completed first.

```powershell
python -m services.agent.eval.jev_v4 run --repeats 5 --max-calls 540 --cap-usd 1 --output "$env:TEMP\wildfire-jev-v4-results"
```

The dollar cap uses an **estimate** from the configured input-token rate. Its
default is the existing eval code's historical rate, not a current billing
guarantee. Verify the provider rate and set `--input-usd-per-million` before
running. `--max-calls` is a hard request-group limit; provider retries are
disabled by the existing backend. The runner stops on backend errors and saves
completed and partial records. It refuses to overwrite an output directory.

Outputs:

- `responses.jsonl`: user query, exact canonical request bodies, payload hash,
  parsed answers and confidence/probability fields, raw provider responses,
  model version, repeat number, input tokens and errors.
- `report.json`: label accuracy and mean confidence per scored field, policy
  decisions, differing outcomes across repeats, every failing case, candidate
  tools and specialized calls. General model-path arguments are not generated
  by this Jev-only evaluation.

Replay without network:

```powershell
python -m services.agent.eval.jev_v4 replay --responses "$env:TEMP\wildfire-jev-v4-results\responses.jsonl" --output "$env:TEMP\wildfire-jev-v4-replay"
```

Replay rejects v3 records and changed v4 request payloads. No new live v4
accuracy, confidence or five-repeat consistency result has been measured as
part of this implementation. Fresh capture is required before deployment.

## Queries, judgments and output

The [complete request example](../services/agent/eval/jev_v4_request_example.json)
contains all three canonical payloads, including the full county and utility
options. It is generated from the current code and has **not been sent**.

| Call | What Jev judges |
|---|---|
| `facts` | Time scope, vague/future time, specific place, proximity, broad region, risk request, named risk measure, prompt injection |
| `topic` | Topic, intent, dataset, multiple intents/datasets, ranking dimension, measure, risk versus residual grid |
| `places` | Named utilities and California county |

For `How did the number of PG&E ignitions change from 2020 to 2023?`, this is a
**scripted test response**, not an API prediction:

```json
{
  "intent": {"kind": "choice", "value": "compare", "confidence": 0.95},
  "off_topic": {"kind": "choice", "value": "on_topic", "confidence": 0.95},
  "dataset": {"kind": "choice", "value": "cpuc_ignitions", "confidence": 0.95},
  "measure": {"kind": "choice", "value": "event_count", "confidence": 0.95}
}
```

With the other required facts also clear, code returns `model / jev_intent`,
offers `comparison_run`, `data_query_records` and `data_query_spatial`, and
passes the resolved intent to the argument-building model. One valid scripted
execution is:

```json
[
  {"tool": "data_query_records", "arguments": {"dataset": "cpuc_ignitions", "utility": "PGE", "result_mode": "count", "year": 2020}},
  {"tool": "data_query_records", "arguments": {"dataset": "cpuc_ignitions", "utility": "PGE", "result_mode": "count", "year": 2023}}
]
```

The harness requires the endpoint results and derives the change from their
evidence. Jev supplies no wildfire count, SQL, or numeric change itself.

Other scripted branches:

| Query | Clear Jev reading | Code outcome |
|---|---|---|
| How well does the fitted ignition model perform? | `model_metrics` | Existing `risk_metrics({})` call |
| Show the statewide risk grid on 2024-08-15. | `risk_surface`, kind `risk` | Existing `risk_surface({date: 2024-08-15})` call |
| Should PG&E be held responsible for its 2023 ignitions? | `advice_or_judgment` | Refusal; no data query |
| Any otherwise supported count question | Intent confidence 0.6 | Clarification; no router fallback |
| Any question | Backend timeout | Error; no model or data query |

For contrast, an **existing real v3 capture** in
`services/agent/eval/runs/jev_decide_store.json`, row
`dev|cases:dq_pge_ignitions_2024`, records the query
`How many PG&E utility-attributed ignitions were there in 2024?`. On the stored
2026-09-23 TypeSafe run, `intent=count`, `off_topic=on_topic`,
`dataset=cpuc_ignitions` and `measure=event_count` each had confidence 1.0.
That observation is from the old payload and development data; it is not a v4
test or a claim of general accuracy.
# Current implementation

The router-first V4 is now documented in [JEV_V4_ROUTER.md](JEV_V4_ROUTER.md)
and selected with `AGENT_JEV_MODE=v4`. The material below describes the earlier
Jev-first experiment and its historical payloads, not the new runtime mode.
