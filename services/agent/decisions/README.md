# Jev decision layer

Code that asks Jev (TypeSafe System One) for routing facts and tool picks. It is
used only when `AGENT_JEV_MODE` is not `off`. Nothing outside this package
imports `typesafe_sdk`, and the default `off` mode never imports it.

## Modes on main

`AGENT_JEV_MODE` accepts `off` (default), `shadow`, `tool_pick`, and
`tool_pick_template` (`services/agent/config.py`, `validate()`). `verify`,
`fallback`, and `route` are reserved names that abort startup.

- `shadow`: `shadow.py` runs Jev in the background next to the regex router
  and logs both. Answers, tool calls, caveats, and eval scores do not change.
- `tool_pick`: on the model path, Jev picks the tool and `tool_pick_mode.py`
  fills arguments from router slots. Below `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE`
  (default 0.8), or on any error or missing slot, the LLM tool loop runs
  (logged with the historical path label `qwen`).
- `tool_pick_template`: same gate; a template writes the answer for simple
  intents instead of LLM synthesis.

`AGENT_JEV_BACKEND` is `typesafe` (default, `api.typesafe.ai`) or `openrouter`
(same request body sent to OpenRouter).
- `decide`: router backstops first, then Jev's derived disposition behind a decline
  gate and a higher answer gate. Jev owns the disposition and the router owns the
  wording: when both decline the same way the router's text stands, and a Jev
  clarification that changes the disposition goes through clarify-all-missing; see [`docs/JEV_DECIDE.md`](../../../docs/JEV_DECIDE.md)
  and `decide_mode.py`. Jev's plan mode was archived on the `jev-plan-archive` branch and is not accepted; the deterministic slot planner (`AGENT_SLOT_PLAN`, `services/agent/eval/slot_plan.py`) runs after decide when both are on.

Operating guide: [`docs/JEV_SHADOW.md`](../../../docs/JEV_SHADOW.md). OpenRouter
backend: [`docs/OPENROUTER.md`](../../../docs/OPENROUTER.md). Deferred work:
[`docs/JEV_BACKLOG.md`](../../../docs/JEV_BACKLOG.md).

## Modules

| File | Role |
|---|---|
| `backend.py` | `QuestionSpec`, `Answer`, `DecisionResult`, and the `DecisionBackend` protocol. No SDK types. |
| `typesafe_backend.py` | `TypeSafeBackend` and `OpenRouterJevBackend`; `make_backend()` picks one from `AGENT_JEV_BACKEND`. Imports `typesafe_sdk` only inside a call, disables SDK retries, and redacts keys from errors. |
| `schemas.py` | Schema v2 question catalog, `POLICY_SENTENCES`, `DOMAIN_CONTEXT`, and `CONTEXT_DEFERRED_RULES` (router rules not yet described to Jev). |
| `v3.py` | Schema v3: small per-topic calls, the glossaries, `calls_for_config()` for the `AGENT_JEV_ABLATION` layouts, and `tool_pick_call()` used by the tool_pick modes. |
| `jev_policy.py` | `derive_outcome()`: turns Jev's atomic facts into a routing outcome. `REGEX_ONLY` lists rules that stay in the router. `needs_time_window()` limits the missing-year gates to intents whose tools take a time window. |
| `expected_facts.py` | Expected fact labels derived from question text, for scoring. |
| `mapping.py` | Projects router decisions and eval cases onto Jev's label space (`regex_labels`, `derive_case_labels`, `agreement`). |
| `shadow.py` | `ShadowRunner` and `get_runner()`: background thread pool, sample rate, concurrency and daily caps, timeout. User requests never wait on it. |
| `shadow_log.py` | Append-only JSONL log at `AGENT_JEV_LOG_PATH`, rotated by size (5 backups), redacts `TYPESAFE_API_KEY`. |
| `decide_mode.py` | `AGENT_JEV_MODE=decide`: `BACKSTOP_RULES`, router-only exemptions, the decline and answer gates, the slot-contradiction, slot-unused (`route_uses`), and code-verified rules, reason texts, the bounded shared executor, and `decide_live()` / `decide_from_answers()`. |
| `tool_pick_mode.py` | `decide_tool_pick()`, slot-filled arguments per tool, the multi-tool refusal, template intents, and `tool_pick_decision` log lines. |
| `canonical.py` | Canonical JSON bytes and hashes, so a replay can prove two payloads are the same. |
| `integrity.py` | Parses raw Jev answers without substituting defaults; question hashes, replay mismatch checks, and scoring helpers. |

Tests that import this package: `tests/agent/test_jev_shadow.py`,
`tests/agent/test_jev_tool_pick.py`, `tests/agent/test_jev_policy.py`, and
`tests/agent/test_openrouter_provider.py`.
