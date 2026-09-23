# OpenRouter backends

Both switches default off. With no new env vars set, the agent runs exactly as before:
Qwen on the local Ollama host and Jev on api.typesafe.ai.

## Production switch

Do not switch yet. The invented placeholder filters are fixed (37 values to 0 on the
force_model cases), but the holdout run below still has wrong answers that are not explained
away: Luna answers multi-part questions with one call, and 5 filter values in one holdout
question are still flagged. Keep this note until invented filters are zero and every wrong
answer is explained.

Add these lines to the backend host's `.env` (or the systemd EnvironmentFile for
`wildfire-agent`), then restart the service:

    AGENT_LLM_PROVIDER=openrouter
    AGENT_ALLOW_REMOTE_PROVIDER=true
    OPENROUTER_API_KEY=<key from the OpenRouter dashboard>

Luna and Sol are the defaults, so `AGENT_LLM_MODEL` and `AGENT_LLM_FALLBACK_MODEL` can stay
unset. Jev stays on TypeSafe unless `AGENT_JEV_BACKEND=openrouter` is also set. The key goes
only in that file on the host, never in git, logs, or chat.

Check the switch took: `GET /health` reports model `openai/gpt-6-luna`, and every model call
prints an `llm_usage` line with that model.

Once the agent runs on OpenRouter it makes no calls to the Ollama model host (172.31.6.133),
so that host can be stopped. Keep it only if something else still needs local Qwen, such as
a qwen eval run.

## LLM: `AGENT_LLM_PROVIDER`

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_LLM_PROVIDER` | `ollama` | `ollama` or `openrouter` |
| `AGENT_LLM_MODEL` | `openai/gpt-6-luna` | Primary model when the provider is `openrouter` |
| `AGENT_LLM_FALLBACK_MODEL` | `openai/gpt-6-sol` | Used for retries after a failed turn, and once when a primary request errors |
| `AGENT_ALLOW_REMOTE_PROVIDER` | `false` | Must be `true` for `openrouter`. The existing security gate is kept |
| `OPENROUTER_API_KEY` | unset | Required for `openrouter`. Startup fails with a clear error if missing. Never commit it |

With `openrouter`, requests go to `https://openrouter.ai/api/v1/chat/completions`. Two Ollama
workarounds are replaced:

- Routing: native `tools` with `tool_choice: "required"` instead of the JSON call envelope
  sent through `/api/chat` `format`. The tool list is the same `lean_enums` catalog, filtered
  to the candidate tools the envelope allowed.
- Synthesis: `response_format` `json_schema` with `strict: true` instead of native `format`.
  It is the same answer schema (`status`, `answer`, `claims[].text`, `claims[].evidence_ids`,
  all required), with `additionalProperties: false` added because strict mode requires it.

Everything after the model call is unchanged: deterministic routing first, the same tools,
harness rules, evidence_ids grounding, and caveats. Ollama-only fields (`options.num_ctx`,
`keep_alive`, `think`) and the context warmup are skipped. `temperature` is not sent because
OpenRouter lists no temperature support for the GPT-6 models; `seed` is still sent.
`provider.require_parameters` keeps requests on hosts that honor `tool_choice` and
`response_format`.

Tool schemas: hosted routing sends each tool as a strict function where every optional
field is nullable (`type: [T, "null"]`, `null` added to enums, every field listed in
`required`). Strict mode requires every field, so without null the model has to invent a
value for each optional filter. The provider removes null fields before the harness sees the
call, so null means "not set" (`strict_nullable_tool` and `drop_null_arguments` in
`provider.py`).

Filter grounding (every provider, model path only): `services/agent/grounding.py` drops any
model-proposed `circuit_id`, `tier`, `hftd_tier`, `county`, `lat`, or `lon` that does not come
from the question text or router slots, and any sentinel value (all-zero ids, 0,0
coordinates, empty strings). Each drop is logged as a `filter_dropped` trajectory event and
stdout line. Utilities keep the existing executor rule and its `utility_filter_stripped`
caveat; dates keep `apply_harness_years`, which already overrides wrong years and rejects
invented ones. Deterministic router calls are not touched. Replaying the rule over the 97
distinct stored qwen3:4b model-path calls in `services/agent/eval/runs/` drops 18 values, all
invented (`county: ""` 9, `circuit_id: ""` 4, `Tier 2` with no tier in the question 5);
grounded values pass unchanged.

Eval scoring: `score_case` in `runner.py` now fails `routing_pass` when an executed filter is
not grounded in the question or slots (`invented_filters`) or when a utility or county the
question names never ran (`missing_filters`).

Escalation to Sol: routing step 2 and later and synthesis attempts 2 and later use the
fallback model. Those turns only happen after the first turn emitted no usable call or failed
validation. A primary request that raises an HTTP error is retried once on the fallback.

Every hosted request prints an `llm_usage` JSON line: provider, phase, model, input and
output tokens, computed cost, OpenRouter's reported cost, and latency. Prices live in
`services/agent/pricing.py` with their source URLs.

## Jev: `AGENT_JEV_BACKEND`

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_JEV_BACKEND` | `typesafe` | `typesafe` or `openrouter` |
| `AGENT_JEV_MODEL` | `jev-latest`, or `typesafe/jev-1.13-20260917` for `openrouter` | An explicit value wins. The local `.env` and `.env.example` set `jev-latest`; remove that line or set the pinned id when switching the Jev backend |

OpenRouter serves Jev through the TypeSafe request format at
`POST https://openrouter.ai/api/v1/systemone`
([docs](https://openrouter.ai/docs/guides/community/typesafe-sdk)). The OpenRouter backend
uses the same `typesafe_sdk` client with `base_url=https://openrouter.ai/api` and
`OPENROUTER_API_KEY`, so the body (`model`, `state`, `questions`) is built by the same code.
`test_openrouter_jev_request_carries_the_same_state_questions_and_options` captures both wire
bodies on a mock transport and fails if state, questions, or criteria differ.

The SDK drops OpenRouter's `usage.cost`, so Jev cost is computed from input tokens at
$0.042 per million (output is free) and stored in `DecisionResult.extra`.

### Pinned Jev id and measured difference

The OpenRouter backend pins `typesafe/jev-1.13-20260917`, the dated build OpenRouter reports
in responses; a probe on 2026-09-23 confirmed it is accepted as the request model and served
unchanged. TypeSafe direct serves `jev-1.13.0` for `jev-latest`.

Dev set, one pass per backend, `v3_hybrid`, 105 questions, 2026-09-23. Dev is used for
tuning, so these compare backends; they are not a clean accuracy estimate.
Run file: `services/agent/eval/runs/jev_backend_compare_20260923T214759Z.json`.

| | TypeSafe | OpenRouter |
|---|---|---|
| Label accuracy | 96.3% (211/219) | 96.8% (212/219) |
| Mean confidence (Noul as max(p, 1 - p)) | 0.906 | 0.907 |
| p50 latency per question | 469 ms | 437 ms |
| p95 latency per question | 851 ms | 1294 ms |
| Cost | $0.030 | $0.030 |

Agreement between backends: 98.9% of labels, every disposition, dataset, and tool_pick.
p95 is about 440 ms (roughly 50%) slower on OpenRouter while p50 is slightly faster. With the
default `AGENT_JEV_TIMEOUT_SECONDS=3` both fit; tail calls on OpenRouter have less headroom.

## Prices (checked 2026-09-23)

| Model | Input $/M | Output $/M | Source |
|---|---|---|---|
| `openai/gpt-6-luna` | 0.10 | 0.50 | https://openrouter.ai/openai/gpt-6-luna |
| `openai/gpt-6-sol` | 2.00 | 10.00 | https://openrouter.ai/openai/gpt-6-sol |
| `typesafe/jev-1.13` | 0.042 | 0.00 | https://openrouter.ai/typesafe/jev-1.13 |

## Measuring

Jev, dev set (cases.json plus paraphrases, used for tuning), one pass through each backend:

    python -m services.agent.eval.jev_backend_compare --backends typesafe,openrouter --typesafe-model jev-latest --openrouter-model typesafe/jev-1.13-20260917

### LLM result on the force_model cases, first run (2026-09-23)

Luna, one pass, run tag `openrouter-luna`, 14 force_model cases from cases.json (dev, used
for tuning). 5 of 14 pass the runner's status, tools, and caveat checks plus evidence present;
p50 3.9 s, p95 12.8 s per case; $0.083 total. No request reached the Ollama host.
Run files: `services/agent/eval/runs/openai-gpt-6-luna__thinking-off__constrained__openrouter-luna/`.

Not ready for production. Luna fills optional tool fields with placeholder values instead of
omitting them: `circuit_id: "000000000"` on ignition and CAL FIRE queries, `lat: 0, lon: 0,
hftd_tier: "Tier 2"` on spatial summaries, `tier: "Tier 2"` and `county: ""` on views. The
validators reject most of these, the retry repeats the same arguments, and the case ends in
an error. Two passing cases are also wrong:

- `model_explicit_year_filled` answered "0 EPSS outages for circuit 000000000 in 2024": the
  placeholder was valid for EPSS, so the query ran with an invented filter.
- `detect_partial_200` says a Tier 2 filter was applied. The count (741) is the unfiltered
  2024 total, so the statement is false.

Rescored with the invented-filter check, this run passes 1 of 14: 37 invented filter values
across 13 cases.

### After nullable strict schemas and filter grounding (2026-09-23)

Same 14 cases, run tag `openrouter-luna-grounded`: 11 of 14 pass with the filter check,
0 invented filters, 0 drops needed (the nullable schema alone stopped the placeholders),
p50 5.6 s, p95 12.0 s per case, $0.052. Every number in the 14 answers matches SQL (US 2024
3,789; CPUC 2023 480; EPSS 2024 2,787; PGE 2024 532 attribute and 536 spatial; SCE 2024 178;
SCE 2023 90 attribute and 86 spatial; Sacramento CAL FIRE 2024 11). The 3 failures:

- `cpuc_vs_us`: one primary call where the case expects two. The harness companion fetched the
  US count and the answer is correct.
- `model_cpuc_tell_me_about_2023`, `model_sacramento_tell_me_about_2024`: Luna and Sol wrote
  correct prose, but the synthesis quantity check rejected "returned 10 records" as a
  mismatch with the total, so the answer fell back to the tool summary. This is a false
  positive in the existing harness check, not a model error.

### Holdouts v1, v2, v3 (2026-09-23)

Every model-path question in `jev_holdout.json`, `jev_holdout_v2.json`, and
`jev_holdout_v3.json` (v2 and v3 read from `platform/jev-multi-tool`), 105 questions, one pass,
`AGENT_JEV_MODE=off`. These were never used to tune Luna's tool arguments. Scored against the
holdout labels by `services/agent/eval/hosted_holdout_run.py`: status matches the label, the
labeled tool and dataset ran, evidence present, and no invented or missing filters.
Run file: `services/agent/eval/runs/hosted_holdout_20260923T221428Z_rescored.jsonl`.

| Set | Questions | Pass | Wrong answers | Invented | Dropped by harness | p50 | p95 | Cost |
|---|---|---|---|---|---|---|---|---|
| v1 | 42 | 12 | 26 | 5 | 4 | 6.1 s | 15.2 s | $0.085 |
| v2 | 27 | 9 | 16 | 0 | 0 | 5.0 s | 8.2 s | $0.038 |
| v3 | 36 | 27 | 5 | 0 | 10 | 6.2 s | 16.5 s | $0.092 |
| All | 105 | 48 | 47 | 5 | 14 | 5.8 s | 15.2 s | $0.216 |

Where the 47 wrong answers come from:

- 29 answered a question labeled clarify (11) or refuse (18). On this branch nothing on the
  model path can clarify or refuse once the router sends a question there: routing forces a
  tool call (the Ollama envelope does the same) and Jev disposition gating is not enabled.
  This is a pipeline gap, not specific to Luna.
- 18 answered a question labeled answer, but partially or with the wrong tool:
  - 8 multi-part questions answered with one call (`ho_006`, `ho_018`, `ho_022`, `ho_047`,
    `hv2_020`, `hv2_025`, `hv2_028`, `hv3_006`): "2021 or 2022" answered for 2021 only, three
    counties answered for one, "PG&E, SCE, and SDGE" answered for PG&E only, Tier 2 and Tier 3
    answered for Tier 2 only. The Ollama envelope asks for a `calls` array; the native tool
    path returns one call and the routing loop stops after a successful turn.
  - 3 rankings or breakdowns answered with one statewide total (`ho_069`, `hv2_001`, `hv3_037`).
  - 4 used a tool that cannot answer the question (`ho_026` overlay, `ho_029` PSPS by tier,
    `hv2_014` share by tier, `hv3_005` California share of the US sample).
  - 3 look complete but used a different tool than the label (`ho_081`, `hv2_024`, `hv3_007`);
    these may be label strictness rather than wrong answers.

Invented filters: the 5 remaining values are all in `ho_088` ("what proportion of 2020
sampled wildfire ignitions were inside utility service territories"), where Luna listed every
IOU. The question arguably asks for all of them, but the strict check counts them, and the
calls failed validation anyway. Of the 14 harness drops, 12 are coordinates Luna geocoded
for city names (Auburn, Chico, Sacramento, Stockton, Fresno); those questions ended in errors
rather than answers built on an invented place. The other 2 are a false positive of the
grounding rule: `ho_083` ("which hftd tier covered the most utility distribution circuits")
implies both tiers but names no tier number, so Tier 2 and Tier 3 were dropped. The tier rule
needs to accept both tiers when the question asks across HFTD tiers.

Before the fixes there was no holdout run, so the before and after comparison for invented
filters is the force_model set: 37 values before, 0 after.

LLM, the 14 force_model cases in cases.json, with the data services and PostGIS running. This does not
contact the Ollama host:

    AGENT_LLM_PROVIDER=openrouter AGENT_ALLOW_REMOTE_PROVIDER=true \
      python -m services.agent.eval.runner --models openai/gpt-6-luna --thinking off \
      --modes constrained --case-ids \
      spatial_pge_ignitions_2024,cpuc_vs_us,count_plus_trend,recover_validation,recover_503,detect_partial_200,holdout_spatial_sce_2023,holdout_count_trend_sce_2023,collision_wrong_kind_model_repair,schema_retry_bound_persistent,model_explicit_year_filled,model_synthesis_bounded,model_cpuc_tell_me_about_2023,model_sacramento_tell_me_about_2024 --run-tag openrouter-luna
