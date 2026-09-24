# OpenRouter backends

OpenRouter is the agent's only LLM provider. Jev stays on api.typesafe.ai unless
`AGENT_JEV_BACKEND=openrouter` is set.

## Production switch

Production switched to OpenRouter on 2026-09-24, and the local Ollama/qwen path and its
workarounds were removed from the code the same day. The provider-level fixes recorded here
were in before the switch: invented placeholder filters went from 37 to 0 on the force_model
cases, all 14 of those cases pass, and the 8 multi-part holdout failures cover every named
entity with SQL-correct numbers. The remaining holdout failures below are not provider
problems; they need the Jev-first decider (`AGENT_JEV_MODE=decide`, `docs/JEV_DECIDE.md`)
and the slot planner (`AGENT_SLOT_PLAN`, `docs/JEV_MULTI_TOOL.md`), both still off by default.

### What still needs the decider and the planner

Failure categories from the 105-question holdout run, and what clears each:

| Category | Count | Cleared by |
|---|---|---|
| Answered a question labeled clarify or refuse | 29 | Jev-first decider (`AGENT_JEV_MODE=decide`, off by default). With decide off, nothing on the model path can clarify or refuse once the router sends a question there, because routing forces a tool call. Jev decides answer, clarify, or refuse before any tool runs. |
| Multi-part question answered with one call | 8 | Fixed here for hosted models by the coverage continuation (all 8 now cover every entity). The slot planner (`AGENT_SLOT_PLAN`, on main, off by default) makes it deterministic: one planned call per named entity, no dependence on the model choosing to call again. |
| Ranking or breakdown answered with one statewide total | 3 | Jev-first decider tool pick (intent rank, tool `data_query_rank`), so the model is not left to pick `data_query_records`. |
| Tool cannot answer the question (overlay, PSPS by tier, share by tier, California share of the US sample) | 4 | Jev-first decider: refuse or clarify when no tool can express the operation, instead of answering a neighbouring question. |
| Named entity the router does not extract (Bear Valley in `ho_022`) | 1 | Slot planner with the router's utility aliases extended to Bear Valley (BVES), so the entity is planned rather than left to the model. |
| City names geocoded by the model (Auburn, Chico, Sacramento, Stockton, Fresno) | 12 drops | Jev-first decider clarify (city needs a place), backed by the router's city_needs_place backstop. The grounding check already stops these invented coordinates from running. |
| Filter values in `ho_088` (every IOU listed for "utility service territories") | 5 | Slot planner: expand "utility service territories" into the IOU list as planned calls, so the values come from the plan and not the model. |
| Complete answer, different tool than the label | 3, plus 7 of the 8 multi-part reruns | Label and scorer review, not a code fix: the holdout scorer requires the labeled tool, and one `data_query_records` call per entity is a valid alternative to `comparison_run`. |

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

The agent makes no calls to the former Ollama model host (172.31.6.133); that instance and
the old GPU instance are retired.

## LLM: `AGENT_LLM_PROVIDER`

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_LLM_PROVIDER` | `openrouter` | The only accepted value; any other value fails at startup |
| `AGENT_LLM_MODEL` | `openai/gpt-6-luna` | Primary model |
| `AGENT_LLM_FALLBACK_MODEL` | `openai/gpt-6-sol` | Used for retries after a failed turn, and once when a primary request errors |
| `AGENT_ALLOW_REMOTE_PROVIDER` | `false` | Must be `true` or the agent refuses to start with a clear message. The gate is kept so sending questions off the host is a deliberate choice |
| `OPENROUTER_API_KEY` | unset | Required. Startup fails with a clear error if missing. Never commit it |

Requests go to `https://openrouter.ai/api/v1/chat/completions`. Two workarounds from the
removed Ollama path are gone:

- Routing: native `tools` with `tool_choice: "required"` instead of the JSON call envelope
  sent through `/api/chat` `format`. The tool list is the same `lean_enums` catalog, filtered
  to the candidate tools the envelope allowed.
- Synthesis: `response_format` `json_schema` with `strict: true` instead of native `format`.
  It is the same answer schema (`status`, `answer`, `claims[].text`, `claims[].evidence_ids`,
  all required), with `additionalProperties: false` added because strict mode requires it.

Everything after the model call is unchanged: deterministic routing first, the same tools,
harness rules, evidence_ids grounding, and caveats. `temperature` is not sent because
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

Enum rule (2026-09-24): a schema-valid enum value is still an invented filter when the question
never asked for it. `utility=untagged` is dropped unless the question mentions untagged,
unattributed, or non-utility records, and `incident_type_mode` `all` or `untyped` is dropped unless
the question asks for every incident type or for records with no type. Luna sent
`utility=untagged` on its own for `recover_503` ("Show a weekly CPUC ignition time series for
2024.") in one rerun; the executor already stripped it, but an injected fault recorded the raw
payload, so the audit flagged it. Faults now record the arguments that would have run.

Tier rule: a question that names tiers by number keeps only those ("tier 2 or 3" keeps both).
A question that asks across tiers without a number ("which hftd tier covered the most
circuits") keeps Tier 2 and Tier 3. HFTD alone with no tier word keeps neither.

Multi-part coverage (hosted only): after a successful routing turn, the loop checks the
named entities (router utility and year slots, every county the question names when it says
county or counties, and both tiers when both are named) against the successful calls. If
any are uncovered it asks the model for the missing ones and continues, within the existing
`AGENT_MAX_TOOL_STEPS` limit. A continuation after a success stays on Luna; only failed or
empty turns escalate to Sol. If entities are still uncovered at the limit, the answer is an
error naming them, never a partial answer. `parallel_tool_calls` is not sent: OpenRouter does
not list it for GPT-6 Luna, and with `provider.require_parameters` the request then 404s.
Several calls per turn are allowed by default.

Synthesis sample sizes: the quantity check no longer rejects a record-list sample size
("returned 10 records") when the number equals a records call's `returned` value and the
nearby text says returned, shown, listed, or sample. Other uncited counts are still rejected.

Eval scoring: `score_case` in `runner.py` now fails `routing_pass` when an executed filter is
not grounded in the question or slots (`invented_filters`) or when a utility or county the
question names never ran (`missing_filters`).

Escalation to Sol: routing turns after a failed or empty turn, and synthesis attempts 2 and
later, use the fallback model. A primary request that raises an HTTP error is retried once on the fallback.

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
p50 3.9 s, p95 12.8 s per case; $0.083 total. No request reached the then-current Ollama host.
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
`jev_holdout_v3.json` (v2 and v3 are not on main; they were read from `platform/jev-multi-tool`), 105 questions, one pass,
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

- 29 answered a question labeled clarify (11) or refuse (18). On main nothing on the
  model path can clarify or refuse once the router sends a question there: routing forces a
  tool call (the removed Ollama envelope did the same) and Jev disposition gating is not enabled.
  This is a pipeline gap, not specific to Luna.
- 18 answered a question labeled answer, but partially or with the wrong tool:
  - 8 multi-part questions answered with one call (`ho_006`, `ho_018`, `ho_022`, `ho_047`,
    `hv2_020`, `hv2_025`, `hv2_028`, `hv3_006`): "2021 or 2022" answered for 2021 only, three
    counties answered for one, "PG&E, SCE, and SDGE" answered for PG&E only, Tier 2 and Tier 3
    answered for Tier 2 only. The removed Ollama envelope asked for a `calls` array; the
    native tool path returns one call and the routing loop stops after a successful turn.
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

### After multi-call coverage, the tier rule, and the sample-size fix (2026-09-23)

Force_model cases, run tag `openrouter-luna-multicall`: 14 of 14 pass (status, tools,
caveats, evidence, and the filter check), 0 invented values, 0 wrong answers (every number
matches SQL), p50 4.5 s, p95 7.5 s, $0.022. `cpuc_vs_us` now makes both primary calls, and the
two "tell me about" cases keep their model-written prose.

The 8 multi-part holdout failures (`ho_006`, `ho_018`, `ho_022`, `ho_047`, `hv2_020`,
`hv2_025`, `hv2_028`, `hv3_006`), run file `services/agent/eval/runs/hosted_holdout_20260923T224001Z.jsonl`:
every named entity is now covered and every number matches SQL (Butte 9 and Shasta 4 CAL FIRE
2020; PSPS 2021 PG&E 2, SCE 5, SDGE 1; CPUC 2023 PG&E 374, SCE 90, SDGE 16; CAL FIRE 2021 172
and 2022 150; Fresno 0 and 4; Riverside 8, San Bernardino 6, Los Angeles 5; Tier 2 113 and
Tier 3 45 in 2020; Riverside CPUC 0, 15, 18, 13), 0 invented values, median 8.2 s, max 14.6 s,
$0.014. The strict holdout scorer still passes only 1 of 8, because 7 used one call per entity
where the label names `comparison_run` or `data_query_rank`. One answer is incomplete:
`ho_022` leaves out Bear Valley, which the router does not extract as a utility; the answer says
it cannot place Bear Valley rather than inventing a number. Shasta's 4 is exact-county: 2 more
2020 incidents are tagged "Shasta, Tehama" and are excluded by the data service's county filter.
Since the `filter-followups` change (issue #78) a county filter includes them, so Shasta 2020 is
6 and the answer carries the multi-county caveat; this run predates that change.

### Derived arithmetic, every count card, every period in the caveat (2026-09-24)

Branch `answer-arithmetic`. The production question "Were there more CPUC ignitions in PG&E
or SCE territory in 2020 compared to 2023, and by how much did each change?" plus the same 8
multi-part holdouts, one pass, `AGENT_JEV_MODE=off`, Luna, run file
`services/agent/eval/runs/hosted_holdout_20260924T041008Z.jsonl`, $0.0087 (one more live
ask of the production question to read its views: $0.0007). v1 and v2 are development data;
v3 is partly tuned; the production question is not in any eval set.

- Production question: the answer now states both changes (PG&E 510 to 374, a decrease of
  136; SCE 145 to 90, a decrease of 55), cited to the `harness_arithmetic` evidence, with no
  grounding error. SQL on the local warehouse gives the same four counts. Four stat cards (was
  three), and both `ignition_definition` caveats list 2020 and 2023 (PG&E spatial 509 and 377,
  SCE 145 and 87).
- The 8 holdouts: the same numbers as the 2026-09-23 run, except `hv2_028` Tier 2 is 105, not
  113, which is the HFTD polygon reload in `docs/DATA_CHANGE_HFTD_IOU.md`, not this branch.
  `ho_022` now lists Bear Valley at 0 (Luna called `utility=BVES`, which the filter check
  accepts; the warehouse has no BVES or Liberty rows, so 0 matches SQL). The strict scorer passes 0 of 8 (1 of 8 before): `hv2_020` used two
  `data_query_records` calls this time where the label names `comparison_run`, with the same
  numbers (Fresno 0 and 4). That is tool-choice variance on a routing prompt this branch does
  not change, not a wrong answer. No invented or missing filters.
- Routes: `routing.py` is unchanged, and path, rule, tool calls, and slots are identical to
  main for all 398 questions in cases.json, jev_paraphrases.json, and holdouts v1, v2, and v3.

LLM, the 14 force_model cases in cases.json, with the data services and PostGIS running (the
`--thinking` and `--modes` flags from the original command were removed with the Ollama path):

    AGENT_ALLOW_REMOTE_PROVIDER=true \
      python -m services.agent.eval.runner --models openai/gpt-6-luna --case-ids \
      spatial_pge_ignitions_2024,cpuc_vs_us,count_plus_trend,recover_validation,recover_503,detect_partial_200,holdout_spatial_sce_2023,holdout_count_trend_sce_2023,collision_wrong_kind_model_repair,schema_retry_bound_persistent,model_explicit_year_filled,model_synthesis_bounded,model_cpuc_tell_me_about_2023,model_sacramento_tell_me_about_2024 --run-tag openrouter-luna
