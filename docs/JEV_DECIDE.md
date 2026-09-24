# Jev decide mode

`AGENT_JEV_MODE=decide` is the runtime version of the combined decider that was scored
offline (`services/agent/eval/_v3_gap_score.py`): router backstops first, then Jev's derived
disposition. It is off by default. Code: `services/agent/decisions/decide_mode.py`, wired in
`AgentOrchestrator.ask` right after `route_question`.

| Variable | Default | Meaning |
|---|---|---|
| `AGENT_JEV_MODE` | `off` | `decide` turns this on |
| `AGENT_JEV_DECIDE_MIN_CONFIDENCE` | `0.8` | Decline gate: a Jev clarify or refuse wins over the router at or above this |
| `AGENT_JEV_DECIDE_ANSWER_CONFIDENCE` | `0.9` | Answer gate: a Jev answer wins over a router clarify or refuse only at or above this |
| `AGENT_JEV_TIMEOUT_SECONDS` | `3` | The decide step gives up after this many seconds and the router stands |
| `AGENT_JEV_BACKEND`, `AGENT_JEV_MODEL` | `typesafe`, `jev-latest` | Same backend settings as shadow mode |

Forced-model eval requests (`force_model=True`) skip decide mode.

## Order

1. **Router hard backstops decide first.** Jev is not called. The exact set
   (`BACKSTOP_RULES`):
   - `unsupported_live_web`
   - `risk_future_date`
   - `unsupported_future_prediction`
   - `city_needs_place`
   - `hftd_constraint_unavailable`
   - the explicit unsupported topics, one per `routing.UNSUPPORTED` key:
     `unsupported_cpz`, `unsupported_cost`, `unsupported_air_quality`,
     `unsupported_evacuation`, `unsupported_translation`, `unsupported_personnel`,
     `unsupported_satellite`, `unsupported_leadership`, `unsupported_optimization`,
     `unsupported_damage`
2. **Routes Jev cannot express are decided by the router.** Jev is not called. A route is
   exempt when its rule is in `jev_policy.REGEX_ONLY`, or when its deterministic call uses a
   tool outside Jev's tool vocabulary (`schemas.TOOL_MODELS`); today that is `risk_surface`,
   so Jev never asks for a place on a statewide surface question.
3. **Otherwise Jev's derived disposition decides.** The three v3_hybrid disposition calls
   (facts, topic, places; the same payloads the offline hybrid sends, no tool_pick call) go
   through `jev_policy.derive_outcome`, including the measure gate.
   - Jev clarify or refuse at or above the decline gate (0.8) returns that clarification or
     refusal with Jev's reason.
   - **A Jev decline never contradicts a slot the router resolved.** A Jev clarification
     about the time (`*_missing_year`, `ambiguous_relative_time`, `forecast_missing_date`)
     is ignored when the router resolved the time, and one about the place
     (`missing_location`, `risk_missing_place`) is ignored when the router resolved a
     county, utility, or coordinates (why: `contradicts_slot`).
   - **Code-verified facts win.** When the router's own resolver proved the time missing or
     ambiguous, or found no place, a Jev answer cannot override that decline (why:
     `code_verified`). `time_out_of_coverage` has no Jev fact and is never overridden.
   - Otherwise a Jev answer where the router declined wins only at or above the higher
     answer gate (0.9), measured on the facts behind the router's rule (for example
     `vague_proximity` for `undefined_spatial_scope`). The question then takes the model
     path, since a declined route has no deterministic call.
   - Below the relevant gate, on a timeout, or on any Jev error, the router's decision stands.
4. **When the final disposition is answer**, the question proceeds exactly as today: the
   router's deterministic call if it has one, otherwise the model path.

Confidence of a Jev decision is the lowest confidence among the facts behind the rule that
fired (`_RULE_FACTS`). A Noul counts as max(p, 1 - p); a Choice uses its own confidence.

Jev reasons reuse the router's wording where the router has the same rule, including
`risk_future_date` and `unexpressable_county_filter`. The router has no prompt-injection
rule, so a Jev `prompt_injection` refusal uses the router's generic unsupported answer.

Every router versus Jev disagreement, and every Jev error or timeout, is logged as a
`jev_decide` JSON line on stdout and in `AGENT_JEV_LOG_PATH`, with the router path and
rule, Jev's disposition, rule, and confidence, the winner, and why (`gate`, `below_gate`,
`contradicts_slot`, `code_verified`, `error`, `timeout`). The response slots carry the same
summary under `jev_decide`.

**Threads.** All decide requests share one bounded pool (`decide_mode.MAX_WORKERS`, 8
threads). A question waits at most `AGENT_JEV_TIMEOUT_SECONDS` for its three calls; calls
that have not started are cancelled, and a call already running keeps its worker only until
the backend's own timeout ends it. Timed-out requests therefore cannot pile up threads
(`test_timed_out_jev_calls_do_not_leak_threads`).

## Choosing the gates

The asymmetric principle stays: a Jev answer over a router decline needs more confidence
than a Jev decline over a router answer, because a wrong answer is worse than a clarifying
question. The decline gate is the 0.8 default used across the Jev modes.

The answer gate was first justified with v3 rows. v3 is frozen, so that justification is
withdrawn: **v3 was not used to choose either gate.** The answer gate was swept on dev and
v1 only, from the stored calls, with the decline gate at 0.8
(`python -m services.agent.eval.jev_decide_replay sweep`,
`runs/jev_decide_answer_gate_sweep.json`):

| Answer gate | dev accuracy (n 137) | dev fixed / broken | v1 accuracy (n 63) | v1 fixed / broken | Answer overrides |
|---|---|---|---|---|---|
| 0.80 | 0.964 | 4 / 0 | 0.952 | 3 / 0 | 0 |
| 0.85 | 0.964 | 4 / 0 | 0.952 | 3 / 0 | 0 |
| 0.90 | 0.964 | 4 / 0 | 0.952 | 3 / 0 | 0 |
| 0.95 | 0.964 | 4 / 0 | 0.952 | 3 / 0 | 0 |
| never (1.01) | 0.964 | 4 / 0 | 0.952 | 3 / 0 | 0 |

Dev and v1 contain no question where Jev answers over a router decline at any gate, so
they cannot choose a value. 0.9 is kept as a stated default one step above the decline gate,
not as a measured optimum. Production shadow logs are where the answer gate can be measured.

## Replay and live check (2026-09-23)

`python -m services.agent.eval.jev_decide_replay capture | replay | sweep | live`.

Stored calls: `services/agent/eval/runs/jev_decide_store.json`, TypeSafe `jev-latest`
(served `jev-1.13.0`), 307 questions, 0 errors, $0.079. Holdout rows marked
`needs_human_review` are left out. v3 uses the 65 rows the independent ChatGPT labels made
certain, read from `jev_holdout_v3_questions.json` on `jev-multi-tool`.

Disposition accuracy with the default gates, decline 0.8 and answer 0.9, replayed from the
store with no new Jev calls (`runs/jev_decide_replay.json`):

| Set | n | Status | Router alone | Jev alone | Decide | Jev won (fixed / broke) |
|---|---|---|---|---|---|---|
| dev | 137 | used for tuning | 0.934 | 0.964 | 0.964 | 7 (4 / 0) |
| v1 | 63 | seen, now development data | 0.905 | 0.937 | 0.952 | 7 (3 / 0) |
| v2 | 42 | seen, now development data | 0.857 | 0.857 | 0.905 | 4 (2 / 0) |
| v3 | 65 | **tuned**, reported only, not used for any choice | 0.692 | 0.723 | 0.769 | 9 (5 / 0) |

Across all four sets Jev's wins fix 14 decisions and break 0. The slot and code-verified
rules changed the recorded reason on two rows and no final decision:
`timeline_missing_year` (router `trend_missing_year`, Jev answered at 0.47, now
`code_verified`) and `hv3_079` (router `forecast_missing_date`, Jev asked for a place the
router resolved, now `contradicts_slot`).

None of these sets is clean; production shadow logs are the next clean test.

Where Jev wins:
- Fixed: prompt injection and off-topic questions the router sent to the model path
  (`inj_ignore_instructions`, `fp_orange_glow`, `ho_054` cost), missing years on open
  comparisons (`ho_035`, `hv2_045`), `undefined_region` for "up north".
- Broke none.
- Both declined but with different reasons in some rows: for "fires near Sacramento" the
  router's `undefined_spatial_scope` (asks for a radius) becomes Jev's `missing_location`
  (asks for coordinates). The disposition is the same; the clarifying question changes.

Live pass on dev only, with both gates (`runs/jev_decide_live_dev.json`): runtime
`decide_live` against the replay of the same store. On 2026-09-23 the TypeSafe account had
no credits (every call returned HTTP 402 and the router stood on all 113, which exercised the
error fallback), so the pass ran through the OpenRouter backend with the same Jev build
(`AGENT_JEV_BACKEND=openrouter`, `AGENT_JEV_MODEL=typesafe/jev-1.13-20260917`). 137 of 137
final decisions match the TypeSafe replay, 136 of 137 Jev dispositions match, 0 errors, Jev
asked on 113 (24 exempt), p50 381 ms and p95 646 ms per question when asked, $0.029.
