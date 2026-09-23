# OpenRouter backends

Both switches default off. With no new env vars set, the agent runs exactly as before:
Qwen on the local Ollama host and Jev on api.typesafe.ai.

## Production switch

Do not switch yet: the force_model run below found Luna inventing tool filters. Fix that
and rerun those cases first.

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

### LLM result on the force_model cases (2026-09-23)

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

The fix has to land before the production switch, for example strict tool schemas where
optional fields are nullable, or a harness check that drops placeholder values before a call.

LLM, the 14 force_model cases in cases.json, with the data services and PostGIS running. This does not
contact the Ollama host:

    AGENT_LLM_PROVIDER=openrouter AGENT_ALLOW_REMOTE_PROVIDER=true \
      python -m services.agent.eval.runner --models openai/gpt-6-luna --thinking off \
      --modes constrained --case-ids \n      spatial_pge_ignitions_2024,cpuc_vs_us,count_plus_trend,recover_validation,recover_503,detect_partial_200,holdout_spatial_sce_2023,holdout_count_trend_sce_2023,collision_wrong_kind_model_repair,schema_retry_bound_persistent,model_explicit_year_filled,model_synthesis_bounded,model_cpuc_tell_me_about_2023,model_sacramento_tell_me_about_2024 --run-tag openrouter-luna
