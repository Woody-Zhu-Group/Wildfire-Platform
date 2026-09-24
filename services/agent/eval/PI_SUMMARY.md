# Local AI agent for wildfire data questions: summary for PI

> Historical record, 2026-09-24: dated August 2026 summary of the local `qwen3:4b` path; the results below are unchanged. Since then: `eval/cases.json` has grown to 107 cases; the local Ollama path was removed and the agent runs on OpenRouter (GPT-6 Luna, `tool_choice: "required"`; see [`docs/OPENROUTER.md`](../../../docs/OPENROUTER.md), which records `cpuc_vs_us` passing on that path on 2026-09-23); and CPUC versus US compare questions now companion-fetch the missing dataset in the harness (`services/agent/caveats.py`).

**Date:** August 2026 (updated after synthesis-quality eval)  
**Question we set out to answer:** Can a small language model running on a laptop reliably route policymaker questions to our existing wildfire data services, and return answers with the right scientific caveats?

**Short answer:** Yes for this prototype scope, with most reliability coming from harness engineering around the model. On the current **57-case** suite (`qwen3:4b`, thinking off, constrained synthesis), **55/57** cases passed the measured checks. Synthesis now produces usable policymaker prose on CPU; interactive latency and multi-tool sequencing remain the main limits, and those argue for GPU or hosted inference, not a larger rewrite of the harness.

---

## Current evaluation (2026-08-11)

Run tag: `synthesis-quality-20260811` · full report: `services/agent/eval/REPORT.md`

| Check | Result |
|---|---|
| Cases | **57** (13 model-tier) |
| End-to-end pass (routing + status + caveats) | **55/57 (96.5%)** |
| Deterministic-tier routing | **100%** |
| Model-tier routing | **84.6%** (11/13) |
| Status accuracy | **98.2%** |
| Required caveats | **93.1%** overall (100% on deterministic-tier caveat cases) |
| Injected-fault recovery | **100%** (4/4) |
| Refusal / clarification | **100%** |
| **Model completed synthesis** (grounded prose) | **72.7% (8/11)** |
| **Harness-rescued** (tools OK → tool-summary fallback) | **27.3% (3/11)** |
| Synthesis fallback rate | **27.3%** (same denominator; regression signal) |
| Cumulative runtime (CPU laptop) | **~28 minutes** |

Failed cases:

- `cpuc_vs_us`: systematic multi-tool miss (see below); synthesis prose is honest but incomplete.
- `collision_wrong_kind_model_repair`: forced model repair of a wrong comparison kind; no successful tool call.

Harness-rescued (still scored OK when tools/caveats succeeded): `recover_validation`, `recover_503`, `holdout_count_trend_sce_2023`.

---

## What works

- **Straightforward questions** (counts, maps, trends, clear comparisons) are reliable. Many never need the model: a deterministic router sends them to the right service.
- **Synthesis prose** is no longer a bare count restatement. After fixing a grounding bug (the harness put metadata numbers like `1234` in evidence, then rejected answers that cited `1,234`), the model writes short briefs that name dataset, scope, period, and what the number does *not* mean.
- **Out-of-scope / ambiguous asks** refuse or clarify rather than invent.
- **Required caveats** (US sample geography, EPSS PG&E-only, CPUC utility-caused definition, attribute vs spatial ignition counts) are injected by the harness from service metadata.
- **Transient tool faults** are auto-retried when the request was already well-formed.

### Example answer (Sacramento County, 2024)

> CAL FIRE recorded 11 wildfire incidents in Sacramento County during 2024 under the default incident-type filter. This count excludes untyped events (1,234 statewide records without incident type) and utility-tagged incidents (282 records without utility data), so it does not represent all fire events. One example is the Marsh Fire.

That answer took **~96 seconds** end-to-end on this CPU laptop (~60 seconds in synthesis). Qualities like this, not pass rates alone, are what make the prototype useful for staff.

---

## What does not work (or needs hardware)

- **CPU latency.** Model-heavy questions commonly take **60–96 seconds** (sometimes longer). Correctness is achievable; interactive feel is not.
- **Synthesis thinking on CPU.** Enabling thinking for synthesis (while keeping routing thinking-off) **timed out past 180 seconds** on every probe. Thinking needs a GPU or hosted endpoint; local default keeps it off.
- **Multi-tool sequencing on 4B + Ollama.** `cpuc_vs_us` (“Compare CPUC and US ignition counts in 2024…”) fails **systematically** (10/10 in a flake probe; still failed in the full suite). Diagnosis:
  1. **Baseline:** the model emits **one** successful `data_query_records` call (CPUC only). The harness treats a clean single-tool turn as done and synthesizes. It does **not** attempt a second call and fail; it never plans both.
  2. **Explicit compare prompt:** telling the model to emit both `cpuc_ignitions` and `us_ignitions` in one turn gets **two calls every time (5/5)**, so intent is promptable, but both calls then fail schema validation (`county="null"`, invented `tier`). Dual *success* still fails.
  3. **PI takeaway:** this is the clearest remaining **model/provider capability limit**. Ollama cannot force multi-tool calls (`tool_choice` unsupported). Hosted inference with required parallel tools (or a stronger model) is the concrete fix; more harness prompts alone do not deliver reliable dual-dataset answers.
- **Not a product chatbot.** Single exchange, loopback, read-only; no multi-user session memory.

---

## What it would take to run in production

Treat the system as a **read-only, single-trusted-user assistant** over warehouse APIs.

1. Keep harness guarantees: deterministic routing for unambiguous asks, caveat injection, no answer from model memory when tools fail, structured refusals.
2. **GPU or hosted inference** for latency (and for synthesis thinking / stronger multi-tool control).
3. Operational packaging: supervised processes, health checks, route/tool audit logs, `SECURITY.md` boundary.
4. Human review for numbers that enter regulatory products, especially attribute vs spatial ignition definitions.
5. Expand eval with real staff questions before calling it production-ready.

---

## Hardware

| Setup | Role |
|---|---|
| **Current: CPU-only Windows laptop** | Correctness for most of the suite. Interactive Q&A is painful (tens of seconds to ~2 minutes on model path). Synthesis thinking not usable. |
| **AWS GPU (`g5.xlarge` / cheaper `g4dn.xlarge`)** | Same software, near-interactive latency; enables synthesis thinking experiments. |
| **Hosted API with `tool_choice`** | Addresses the multi-tool sequencing failure class (`cpuc_vs_us`) that local Ollama cannot force. |

A GPU makes answers feel instant. It does not replace the harness for caveats, refusals, or grounding. Hosted tool-forcing addresses a different gap than GPU speed alone.

---

## Bottom line for the PI

We can run a small local model as a front door to wildfire services we already trust, provided the engineering around it owns easy routing, caveat attachment, recovery, and grounded synthesis. **Synthesis now writes useful briefs** (see Sacramento example). **Fallback is still ~27% of synthesis attempts**: better than constant rescue, not yet “rare.” The remaining hard limit for the PI narrative is precise: **4B + Ollama will not reliably execute two-dataset comparisons**; prompting can elicit the second call but not clean arguments, and the API cannot require tools. Next deployment step: GPU/hosted inference for latency and multi-tool control, plus a short pilot with real analyst questions under the same safety constraints.
