# Agent feasibility evaluation

> Historical record. This document describes the local Ollama/qwen model path, which was removed on 2026-09-24 when production switched to OpenRouter (GPT-6 Luna). The measurements below are kept as they were made and are not comparable to the hosted path. See `docs/OPENROUTER.md`.

## Superseded baseline

The earlier **0/5 model-tier routing result is superseded and must not be used as evidence that local models cannot route.** Its root cause was **token budget exhaustion during tool-catalog deliberation, not a model capability limit**. Direct isolated calls proved `qwen3:4b` emits tool calls through both Ollama endpoints.

Controlled integration measurements on the same spatial question:

- Full catalog, 1,800-token cap: 1,470 prompt tokens, 438.2s, `finish_reason=length`, zero calls.
- Trimmed six-tool catalog: 1,125 prompt tokens, 333.8s, `finish_reason=stop`, zero calls.
- Prefiltered two-tool catalog with the mixed routing/synthesis prompt: 641 prompt tokens, 361.3s, `finish_reason=length`, zero calls.
- Routing-only prompt with all six trimmed tools: 901 prompt tokens, 419.8s, `finish_reason=length`, zero calls.
- Routing-only prompt with two candidates: 414 prompt tokens, 281.5s, `finish_reason=tool_calls`, one correct call.

Description/enum trimming reduced prompt size and stopped one runaway completion, but did not produce a call. Candidate prefiltering had the larger practical effect only after routing and synthesis instructions were separated. The corrected harness therefore uses all three.

## Prominent local-provider constraint

**Ollama’s OpenAI-compatible endpoint does not support `tool_choice`.** The harness cannot force Qwen to call a tool. It blocks unsupported direct answers and retries, but the attempts and latency are real local-model costs that a hosted provider with forced tool choice may avoid.

The installed Qwen3 manifest also forced a thinking prefix despite `reasoning_effort=none`; the thinking-off cell used a template-only alias with identical weights and a pre-closed thinking block. The alias was faster in the isolated test, but Qwen can still emit deliberative prose before a tool call or JSON. The harness strips that prose from tool history and recovers a trailing schema-valid JSON object while reporting raw strict schema validity separately.

Stop gate: **50% model-tier routing accuracy** after at least 5 model-tier cases. Stopped early: **False**.

**Outcome:** the corrected staged baseline passed the gate: 100.0% model-tier routing versus 100.0% deterministic-tier routing. The remaining matrix has not been run.

Qwen produced 0 no-tool responses before evidence and 0 valid direct answer attempts. The harness blocked all no-evidence output.

## Headline: synthesis completion split

**Model completed:** 0.0% (0/0). **Harness-rescued (synthesis fallback):** 0.0% (0/0). Model-completed means grounded model prose (`answer_origin=model`); harness-rescued means tools succeeded but synthesis timed out, failed grounding, or otherwise fell back to the deterministic tool summary.

**Synthesis fallback rate: 0.0%** (same denominator; frequent fallback is a regression signal).

## Cell results

| Model | Thinking | Output | Deterministic/model routing | Schema first/eventual | Caveats | Recovery | Model done / rescued | No-tool/direct attempts | Request p50/p95 | Model p50/p95 | Runtime |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen3:4b | off | constrained | 100.0%/100.0% | 100.0%/100.0% | 100.0% | 100.0% | 0.0%/0.0% (0/0 of 0) | 0/0 (0.0%/0.0% per turn) | 178.82/2036.11 ms | None/None ms | 6.5s |

Latency columns report individual model calls. `runtime` is cumulative measured request time plus the final warmup; checkpoint downtime is excluded.

## Harness-guaranteed versus model-dependent

Harness-guaranteed: deterministic routing rules and candidate catalog prefiltering, schema validation and embedded-JSON recovery, duplicate-call suppression, bounded retries, response-contract checks, raw-payload isolation, required caveat injection, no-evidence blocking, and refusal when qualification metadata is unavailable. Required caveats surfaced on 100.0% of deterministic-tier caveat cases.

Model-dependent: tool selection and argument construction on model-tier questions, multi-tool sequencing, recovery after a visible tool error, evidence-faithful synthesis, and choosing clarification/refusal when a deterministic rule does not apply.

## Failed cases

### qwen3:4b / thinking=off / constrained
- None

