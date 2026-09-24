# Agent model upgrade notes: qwen3:4b → qwen3:32b (remote Ollama, L4 24GB)

> Status (2026-09-23): historical notes from the qwen3:4b to qwen3:32b investigation. The runtime default is now `qwen2.5:7b` (`services/agent/config.py`), the eval runner still defaults to `qwen3:4b`, and a hosted OpenRouter option exists behind `AGENT_LLM_PROVIDER` (see [`docs/OPENROUTER.md`](docs/OPENROUTER.md)). Statements below about the `qwen3:4b` runtime default describe the code at the time they were written.
>
> Other statements below that no longer match the code: `.env.example` now sets `AGENT_MODEL=qwen2.5:7b` (section 1 table). A failed `ensure_runtime_model()` or `ensure_context_loaded()` no longer stops the process from becoming healthy; lifespan catches both and still binds the port (`services/agent/app.py` 33-45; section 4). systemd units now exist in `deploy/systemd/` (`wildfire-agent.service` is `Type=simple` with the default `TimeoutStartSec`; section 4). `eval/cases.json` has 107 cases, not 62 (section 6). `validate()` is now at `config.py` 174-232 (section 5), and the `num_ctx` comment quoted in section 2 now reads "Ollama defaults to 4096 when unset" (`config.py` 56).

Read-only audit. No code, `.env`, or config was changed. Findings are grounded in `services/agent/` as of this write-up.

The service was designed and scored against local `qwen3:4b` (thinking off, constrained synthesis, often CPU). Pointing it at remote `qwen3:32b` on an NVIDIA L4 (24GB) does **not** require a rewrite, but three things will misbehave if left at 4B defaults: **32k context vs VRAM**, **the thinking-off alias never being created on a remote URL**, and **eval expectations that assume 4B tool-sequence / synthesis-origin behavior**.

---

## 1. Hardcoded model-size assumptions

### Literal `qwen3:4b` outside `config.py`'s default

Runtime config defaults to `qwen3:4b` in two places only (`AgentSettings.model` and `from_env()`). That is the intended default; it is overridden by `AGENT_MODEL`.

Other `qwen3:4b` strings in `services/agent/` are documentation, eval history, or CLI defaults: they do **not** pin the live service:

| Location | Role |
|---|---|
| `eval/runner.py` `--models` default and one REPORT paragraph | Eval CLI / historical write-up |
| `eval/diagnostic_matrix.py` comment | Diagnostic baseline label |
| `eval/REPORT.md`, `PI_SUMMARY.md`, `summary.json`, `runs/qwen3-4b__*/` | Past 4B results |
| `README.md` (service + repo) | Example commands |
| `.env.example` | Local-dev template (`AGENT_MODEL=qwen3:4b`) |

Nothing in the live request path branches on `:4b` vs `:32b`.

### Token limits, timeouts, buffers tuned for a small/fast model

These are **fixed integers**, not functions of model size:

| Setting | Default | Used for |
|---|---|---|
| `AGENT_MAX_COMPLETION_TOKENS` | 1800 | OpenAI-compat completions |
| `AGENT_MAX_ROUTING_TOKENS` | 900 | Constrained tool-envelope routing |
| `AGENT_MAX_SYNTHESIS_TOKENS` | 1200 | Constrained JSON synthesis |
| `AGENT_MAX_TOOL_STEPS` | 5 | Model-tier loop cap |
| `AGENT_MAX_VALIDATION_RETRIES` | 2 | Synthesis grounding retries |
| `AGENT_NUM_CTX` | 32768 | Ollama `options.num_ctx` on every load/request |
| `AGENT_SYNTHESIS_TIMEOUT_SECONDS` | 180 | Wall-clock synthesis only |
| `AGENT_TIMEOUT_SECONDS` | 900 in `config.py`; **300 in `.env.example`** | httpx client timeout for **all** model HTTP |

The routing token cap (900) and the routing/synthesis split exist because **4B burned its budget on tool-catalog deliberation**, not because 4B is small. A 32B model can use the same caps; they are not 4B-specific ceilings.

`tools.py` caps **backend** HTTP at `min(AGENT_TIMEOUT_SECONDS, 120)`. That is for data_query / risk / viz / comparison on loopback, not the LLM.

### `model_setup.py` `qwen3:*` alias logic

`ensure_runtime_model()` creates `{model}-agent-nothink` only when **all** of these are true:

1. `AGENT_THINKING=off`
2. `model_runtime` is unset
3. `settings.model.lower().startswith("qwen3:")`
4. `"127.0.0.1:11434"` is a substring of `AGENT_MODEL_BASE_URL`

**Name match:** `qwen3:32b`.startswith(`qwen3:`) is true. The alias path does **not** special-case `:4b`. If this function ran, it would request `qwen3:32b-agent-nothink` the same way it requests `qwen3:4b-agent-nothink`.

**Remote URL skip (this is the break):** condition 4 fails for `http://<GPU_PRIVATE_IP>:11434/v1`. The function returns settings unchanged. No alias is created. Requests go to the raw `qwen3:32b` template.

That matters because the comment in `model_setup.py` is still current for many Ollama Qwen3 manifests: the template forces a `<think>` prefix even when the OpenAI shim sends `reasoning_effort=none`. On local 4B, the alias was what made thinking-off real. On remote 32B, the service still sends native `"think": false` and a `/no_think` user prefix, but it **cannot** rewrite the remote template.

**Operational check on the GPU host (do this once, do not skip):**

```text
ollama show qwen3:32b
```

If the template still contains the forced-think suffix from `model_setup.py` (`<|im_start|>assistant` then `<think>` with no close), thinking-off is **not** guaranteed. Either create the alias **on the GPU box** (`qwen3:32b-agent-nothink`) and set `AGENT_MODEL` to that alias, or later relax the `127.0.0.1:11434` guard so the agent can `POST /api/create` remotely. That code change is out of scope here.

Also: `ensure_runtime_model()` uses a **hardcoded 300s** httpx timeout. That path does not run against a remote URL, so it is not a remote-startup risk.

---

## 2. Context window vs VRAM

The repo does **not** calculate KV-cache size anywhere. The 32768 default is documented as “Ollama otherwise loads at 4096; qwen3:4b supports far more” (`config.py` ~43–44, `HARNESS_GUARDS.md`). Historical synthesis hangs were **timeouts**, not 4096 truncation; measured prompts were ~1.4k characters.

### Qwen3-32B geometry (Hugging Face `Qwen/Qwen3-32B` `config.json`)

- `num_hidden_layers` = 64
- `num_key_value_heads` = 8 (GQA)
- `head_dim` = 128
- native context 32768 (YaRN to 131072; irrelevant here)

### KV cache at `num_ctx = 32768`

Standard inference KV (K and V, all layers, allocated for the **configured** context: Ollama sizes the cache from `num_ctx`, not from the tiny prompt):

```text
bytes = 2 * n_layers * n_kv_heads * head_dim * seq_len * bytes_per_element
```

**FP16 KV** (`bytes_per_element = 2`):

```text
2 * 64 * 8 * 128 * 32768 * 2
= 8,589,934,592 bytes
= 8.00 GiB
```

**Q8 KV** (Ollama flash-attention / quantized cache, if enabled): ~4.00 GiB  
**Q4 KV**: ~2.00 GiB

Per-token FP16 KV is `8 GiB / 32768 ≈ 256 KiB` (matches public architecture notes of ~262 KiB).

### Weights + KV on 24GB

A Q4 / Q4_K_M 32B GGUF is typically **~18–20.5 GB** of weights (32e9 × ~4.5 bits / 8 ≈ 18 GB, plus GGUF overhead). Using the user’s ~20 GB:

| `AGENT_NUM_CTX` | FP16 KV | Weights + FP16 KV | Headroom on 24GB |
|---|---:|---:|---|
| 32768 | 8.00 GiB | ~28 GB | **overflow (~4 GB short)** |
| 16384 | 4.00 GiB | ~24 GB | **none** (compute/CUDA still need 0.5–2 GB) |
| 8192 | 2.00 GiB | ~22 GB | ~2 GB: tight, often works |
| 4096 | 1.00 GiB | ~21 GB | ~3 GB: safer |

This ignores CUDA context, compute scratch, and fragmentation. Those are why “24 GB exactly” is not a working budget.

**32k context next to ~20 GB weights is not plausible on a 24GB L4** under default FP16 KV. Even Q8 KV + 20 GB weights is 24 GB with no scratch. Ollama may OOM, refuse the load, or silently come up at a smaller `context_length`. `app.py` already warns when `effective_num_ctx < configured_num_ctx`.

The agent does not need 32k. Routing catalogs and evidence JSON are small relative to 8k tokens. 32768 was an anti-4096 pin for 4B, not a measured requirement.

### Recommended `AGENT_NUM_CTX`

**8192** for first bring-up on this card.

Logic: 8k is 2× Ollama’s 4096 default (enough for this harness) and costs **2.00 GiB** FP16 KV, leaving ~2 GB after 20 GB weights. If `nvidia-smi` / `ollama ps` still shows OOM or `effective_num_ctx` clamps, drop to **4096**. Only try **16384** after confirming flash-attention + quantized KV and a stable `ollama ps` size. Do not keep **32768** unless those checks show the load actually fits.

---

## 3. Timeouts

All values below are **fixed wall-clock seconds**. None scale with model size, token count, or measured tokens/sec.

### In `services/agent/config.py`

| Env var | `AgentSettings` field | Default | Kind |
|---|---|---:|---|
| `AGENT_TIMEOUT_SECONDS` | `request_timeout_seconds` | **900** (`config.py`); **300** in `.env.example` | httpx timeout on OpenAI `/v1` **and** native Ollama clients. Covers routing, synthesis HTTP, **and** startup warmup. |
| `AGENT_SYNTHESIS_TIMEOUT_SECONDS` | `synthesis_timeout_seconds` | **180** (must be ≥ 5) | `asyncio.wait_for` around **synthesis only**. On expiry: `synthesis_timeout` → user-visible fallback / error, not a hang. |
| `AGENT_ARTIFACT_TTL_SECONDS` | `artifact_ttl_seconds` | **900** | Artifact cache TTL. Not an LLM timeout. |

There is no `AGENT_ROUTING_TIMEOUT_SECONDS`. Routing uses only the httpx client timeout (900 or 300).

### Related timeouts (not in `config.py`)

| Where | Value | Notes |
|---|---:|---|
| `model_setup.py` | 300s | Alias create; **skipped** for remote URLs |
| `tools.py` backend client | `min(request_timeout, 120)` | Loopback services, not the LLM |
| `app.py` `/health` probes | 5s | data_query / risk / viz / comparison |
| Eval `runner.py` preflight | 10s | Backend + `/v1/models` |
| Eval cases `max_elapsed_ms` | 240000 / 360000 | Case scoring, not the service |

### What looks tight for 32B

**Same GPU, 4B vs 32B:** decode is roughly proportional to parameter count (memory-bound), so ~4–8× slower is a fair planning factor. That is **not** the same as 4B-on-CPU vs 32B-on-L4. Constrained 4B synthesis on CPU was ~15–25s/turn (`HARNESS_GUARDS.md`). An L4 32B Q4 can still finish 1200 tokens inside 180s **if thinking is off and the model is already resident**.

**`AGENT_SYNTHESIS_TIMEOUT_SECONDS=180` is the one to watch.** It gates the final answer. It will trip if:

- the remote template still forces thinking (alias skipped: §1), or
- `AGENT_SYNTHESIS_THINKING=true`, or
- `AGENT_STRUCTURED_MODE=prompt` (the original 180s CPU failure mode), or
- synthesis retries (`max_validation_retries + 2` attempts, each with its own 180s budget: retries help quality, not a single slow turn).

Recommendation: keep **constrained** + **thinking off**, and set synthesis to **300s** for 32B bring-up. 180s is defensible on a warm L4 if thinking is truly off; 300s is the cheaper insurance than silent fallback-to-tool-summary. Do not raise it to 900: that just recreates the old hang.

**`AGENT_TIMEOUT_SECONDS`:** keep the **900** code default, not the 300 from `.env.example`. 300s is enough for a warm request and may be enough for a local-disk 32B load; it is tight for first-pull + VRAM alloc + warmup over the network. Routing has no tighter cap, so 900s also bounds a stuck routing call.

---

## 4. Startup / warmup

`app.py` lifespan:

1. `ensure_runtime_model()`: **no-op** for a remote `AGENT_MODEL_BASE_URL` (see §1).
2. `provider.ensure_context_loaded()`: unload (`keep_alive: 0`) then `/api/chat` with `num_predict=1` and configured `num_ctx`, `keep_alive: -1`.
3. If that raises, **the process does not become healthy**.

There is **no separate warmup timeout**. Warmup uses the same native httpx client, so it is bounded only by `AGENT_TIMEOUT_SECONDS` (900 code default / 300 if `.env.example` is copied).

There is **no systemd unit** (or other process supervisor) in this repo. If one is added later, `TimeoutStartSec` must cover **cold 32B load**, not a 4B-sized request. A first `ollama pull` plus VRAM allocation can be several minutes; a subsequent start with the blob already on the GPU host is typically tens of seconds to ~2 minutes. Pre-pull and a one-shot `ollama run qwen3:32b` on the GPU box before starting the agent removes most of this risk.

Eval `runner.py` also calls `ensure_context_loaded()` then a second 1-token warmup. Same timeout story.

---

## 5. Remote provider config (sanity check only)

These three settings, set together, are **sufficient** for the agent to call remote Ollama instead of localhost:

```text
AGENT_MODEL=qwen3:32b
AGENT_MODEL_BASE_URL=http://<GPU_PRIVATE_IP>:11434/v1
AGENT_ALLOW_REMOTE_PROVIDER=true
```

`validate()` (`config.py` 124–136):

- Remote **model** URL is rejected only when `allow_remote_provider` is false.
- When `AGENT_ALLOW_REMOTE_PROVIDER=true`, the model URL is **not** required to be loopback.
- The four backend URLs are checked in a **separate** loop and **must** remain loopback regardless of the remote-provider flag.

So these should **stay** loopback (agent and backends on the same box):

```text
DATA_QUERY_BASE_URL=http://127.0.0.1:8000
RISK_FORECASTING_BASE_URL=http://127.0.0.1:8001
VISUALIZATION_BASE_URL=http://127.0.0.1:8002
COMPARISON_BASE_URL=http://127.0.0.1:8003
```

Provider construction: OpenAI client uses `…/v1`; native client strips `/v1` and talks to `http://<GPU_PRIVATE_IP>:11434` for `/api/chat`, `/api/generate`, `/api/ps`. Constrained routing/synthesis still use the native path. The GPU Ollama must expose **both** `/v1` and native `/api/*` on 11434 (default Ollama does).

`AGENT_MODEL_API_KEY` can stay `ollama` unless the remote instance requires something else.

---

## 6. Eval suite

`services/agent/eval/cases.json` has **62** cases. `STOP_THRESHOLD = 0.50` and `MIN_MODEL_CASES_FOR_STOP = 5` in `eval/runner.py`.

### What the 50% gate actually is

It is **model-tier routing accuracy** (expected route + **exact** primary tool sequence), not answer phrasing and not overall pass rate. It was introduced after the first 4B baseline recorded **0% model-tier tool calling** (token-budget / catalog issue, later superseded). The gate exists so a broken model-tier cell cannot green-light the rest of a matrix. It is **not** a 4B-specific accuracy target. A 32B run that routes correctly should clear 50% easily; that is not the same as “32B will score higher than 4B on the full suite.”

### Scoring is mostly structure

Most cases assert route, tool list, caveats, status, and canvas view types. Deterministic cases never call the LLM for the answer text. Those will not change just because the model got bigger.

`expected_tools` is an **exact sequence** (`actual_tools == expected_tools`) unless `--disable-deterministic` loosens it. A 32B that helpfully adds a second tool **fails** the case. That already happened on 4B for `model_sacramento_tell_me_about_2024` (extra dataset reads).

### Cases that constrain answer text (not full golden transcripts)

There are **no** hardcoded full expected answers. Substring checks:

| Case | What is asserted | 4B-specific? |
|---|---|---|
| `dq_cpuc_sacramento_county_2023` | must contain `sacramento`; must not refuse | Deterministic; harness text |
| `calfire_sacramento_august_2023` | must contain `calfire_incidents count` | Deterministic template |
| `silent_filter_drop_cpuc_sacramento_august` | `sacramento`; must not say `480` (statewide leak) | Deterministic |
| `risk_county_date`, `risk_utility_date` | `chance of at least one`, `higher than about` | Deterministic risk prose |
| `risk_out_of_coverage` | `2025-12-31 and no forecast ingestion` | Deterministic coverage error |
| `unsupported_ranking_circuit_most` | `Ranking is not supported` **or** `cannot compute` | Deterministic / unsupported copy |
| `utility_not_invented_from_place` | `county` / `CAL FIRE` / `will not answer`; no SCE | Deterministic unsupported |
| `schema_retry_bound_persistent` | error fragments (`could not` / `valid query` / `retry` / `year`); must not leak `schema validation` | Model-tier fault injection |
| **`model_cpuc_tell_me_about_2023`** | must contain `cpuc` **or** `2023` **or** `480`; must **not** contain `cpuc_ignitions count:`; `expected_answer_origin=model`; `max_elapsed_ms=360000` | **Model-tier quality.** `480` is a 2023 CPUC count, not 4B phrasing. Forbidding `cpuc_ignitions count:` rejects the **harness fallback template**, which 4B hit when synthesis failed. |
| **`model_sacramento_tell_me_about_2024`** | `sacramento` **or** `11`; must not contain `calfire_incidents count:`; `expected_answer_origin=model`; `max_elapsed_ms=360000` | Same pattern. `11` is the CAL FIRE Sacramento 2024 count. |
| **`model_synthesis_bounded`** | synthesis must finish or time out; `max_elapsed_ms=240000` | Latency budget from 4B-era hangs |

`any()` on `answer_must_contain_any` means one fragment is enough. A 32B paraphrase that never says `480` but says `cpuc` still passes that check. A 32B that times out synthesis and falls back to `cpuc_ignitions count:` **fails** origin + the forbidden substring.

### Do not assume 32B is strictly better

Re-run the 62-case suite against `qwen3:32b` (thinking off, constrained) before trusting it. Risks vs 4B:

- Extra tool calls fail exact `expected_tools` (especially `cpuc_vs_us`, `count_plus_trend`, the two tell-me-about cases).
- If thinking is still on (§1), synthesis origin flips to fallback and the two `expected_answer_origin=model` cases fail.
- `model_synthesis_bounded` at 240s can fail if 32B + retries are slow, even when the service eventually answers.
- Deterministic cases should be unchanged; they are the control.

The 50% stop-gate is a **matrix** control. For a single 32B cell, read the full summary (model-tier routing, schema first/eventual, origin=model rate, fallback rate), not just “gate passed.”

---

## Recommended `.env` values for qwen3:32b

Only settings that differ from the current code default, or that must be set for remote Ollama, are called out.

```text
# Required to reach the GPU Ollama instead of localhost
AGENT_MODEL=qwen3:32b
AGENT_MODEL_BASE_URL=http://<GPU_PRIVATE_IP>:11434/v1
AGENT_ALLOW_REMOTE_PROVIDER=true

# Keep backends on the agent host (validate() requires loopback)
DATA_QUERY_BASE_URL=http://127.0.0.1:8000
RISK_FORECASTING_BASE_URL=http://127.0.0.1:8001
VISUALIZATION_BASE_URL=http://127.0.0.1:8002
COMPARISON_BASE_URL=http://127.0.0.1:8003

# VRAM: 32k FP16 KV is ~8 GiB; 20 GB Q4 weights + 8 GiB KV exceeds 24 GB.
# 8192 → ~2 GiB FP16 KV. Drop to 4096 if ollama ps / nvidia-smi still OOMs.
AGENT_NUM_CTX=8192

# Do not copy .env.example's 300: that is also the startup-warmup deadline.
AGENT_TIMEOUT_SECONDS=900

# 180s was enough for constrained 4B on CPU (~15–25s) with thinking off.
# 300s leaves room for slower 32B decode and a first cold synthesis without
# recreating the old unbounded hang (that was 900s with no wait_for).
AGENT_SYNTHESIS_TIMEOUT_SECONDS=300

# Same as today: required for thinking-off and for the 180/300s budget to mean anything.
AGENT_THINKING=off
AGENT_STRUCTURED_MODE=constrained
AGENT_SYNTHESIS_THINKING=false
```

Unchanged and fine at code defaults: `AGENT_MAX_ROUTING_TOKENS=900`, `AGENT_MAX_SYNTHESIS_TOKENS=1200`, `AGENT_MAX_COMPLETION_TOKENS=1800`, `AGENT_MAX_TOOL_STEPS=5`, `AGENT_PROVIDER=openai_compatible`, `AGENT_MODEL_API_KEY=ollama`.

**After first start, verify on the GPU host:** `ollama ps` `context_length` equals 8192 (or whatever you set), VRAM is under ~23 GB with headroom, and `ollama show qwen3:32b` does not force an open `<think>` block. If it does, create `qwen3:32b-agent-nothink` on that host and point `AGENT_MODEL` at the alias: the agent will not do that for you while the URL is remote.
