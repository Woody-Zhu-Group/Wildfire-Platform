# Deterministic vs model-only routing experiment

> Historical record. This document describes the local Ollama/qwen model path, which was removed on 2026-09-24 when production switched to OpenRouter (GPT-6 Luna). The measurements below are kept as they were made and are not comparable to the hosted path. See `docs/OPENROUTER.md`.

Default remains **deterministic-first**. Flag: `AGENT_DISABLE_DETERMINISTIC_ROUTING` / eval `--disable-deterministic`.

Harness guarantees stayed active on both paths: caveat injection, grounding, argument validation, year guards, retry bounding, relative-date resolution, and unsupported refusals.

**Latency note:** CPU/local Ollama numbers. They will not reflect GPU EC2 deployment latency.

## Runs

| Mode | Artifact |
|---|---|
| Deterministic-first (single-shot) | `runs/.../harness-guards-full` → **49/50**; flake `collision_wrong_kind_model_repair` (no tool calls) |
| Deterministic-first (merged after one-case retry + JSONL repair) | `runs/.../harness-guards-merged` → **50/50** |
| Model-only | `runs/.../model-only-routing-exp` → **38/50 (76%)** |

## Summary metrics (merged det vs model-only)

| Metric | Deterministic-first | Model-only |
|---|---:|---:|
| End-to-end pass | 100.0% (50/50 merged; 49/50 single-shot) | 76.0% (38/50) |
| Routing / tool pass (scored) | 100.0% | 100.0%* |
| Status pass | 100.0% | 80.0% |
| Caveat pass | 100.0% | 84.0% |
| Recovery pass | 100.0% | 100.0% |
| Model-path cases | 10 | 35 |
| Request p50/p95 (ms) | ~0.3s / ~10m | ~43s / ~5m |
| Model-call p50/p95 (ms) | ~80s / ~208s | ~19s / ~88s |

\*Model-only scoring treats former deterministic cases as path=`model` and allows alternate correct tool sequences when status/caveats hold. Failures below are mostly status/caveat errors after bad args or exhausted loops, not “wrong path” labels.

## Cases only deterministic-first passed (12)

These are the load-bearing failures for dropping the router:

- `spatial_point_context` — model thrashed spatial point calls; loop exhausted
- `list_psps_2021` — schema-retry bound exhausted after repeated invalid args
- `compare_pge_periods` — answered with incomplete comparison; missing period caveats
- `ratio_per_circuit` — schema-retry exhausted; missing EPSS caveat
- `collision_compare_territories_last_year` — loop exhausted despite harness-resolved year
- `collision_boundary_only_sce` / `collision_map_sce_territory_boundary` — model invented `year=2023` on a no-time question; year guard correctly blocked; loop exhausted
- `collision_period_not_utilities` — status/caveat miss under model arg construction
- `collision_relative_past_two_years` / `collision_this_year_count` / `relative_last_year_count` / `relative_two_years_ago_compare` — relative-date questions that the harness resolves cleanly on the det path; model-only still struggles to finish grounded answers even with filled year slots

## Cases only model-only passed

- None (after det JSONL repair)

## Alternate but also-correct tool sequences (both passed)

- `coordinate_to_risk`: det=`[data_query_spatial, risk_forecast]` · model-only=`[risk_forecast]`  
  (model skipped the spatial lookup; still produced a risk answer — counted as alternate-correct, not a failure)

## Interpretation for PI (data, not a decision)

1. On this CPU cell, deterministic-first is clearly more reliable (49–50/50 vs 38/50) even with all harness guards present on both paths.
2. Several model-only misses are exactly the class of bugs this work closed on the det path: invented years, thrashing invalid schemas, incomplete comparisons.
3. Request p50 jumps from sub-second (det answers) to ~43s when every former det case pays a model turn — GPU will shrink that gap, but will not by itself restore the 12 missed cases.
4. Keeping the router is still a maintenance cost; this experiment quantifies the reliability it currently buys on Qwen3:4b.
