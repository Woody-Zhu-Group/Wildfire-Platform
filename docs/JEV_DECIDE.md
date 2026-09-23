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
| `AGENT_JEV_TIMEOUT_SECONDS` | `3` | Per Jev call; the whole decide step gives up after this plus 1 s |
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
   - Jev clarify or refuse at or above the decline gate (`AGENT_JEV_DECIDE_MIN_CONFIDENCE`,
     0.8) returns that clarification or refusal with Jev's reason.
   - Jev answer where the router declined wins only at or above the separate, higher answer
     gate (`AGENT_JEV_DECIDE_ANSWER_CONFIDENCE`, 0.9), measured on the facts behind the
     router's rule (for example `vague_proximity` for `undefined_spatial_scope`). The gates
     are asymmetric because a wrong answer is worse than a clarifying question. The question
     then takes the model path, since a declined route has no deterministic call. Rules decided in code from the question text
     (`time_out_of_coverage`) have no Jev fact and are never overridden.
   - Below the relevant gate, on a timeout, or on any Jev error, the router's decision stands.
4. **When the final disposition is answer**, the question proceeds exactly as today: the
   router's deterministic call if it has one, otherwise the model path.

Confidence of a Jev decision is the lowest confidence among the facts behind the rule that
fired (`_RULE_FACTS`). A Noul counts as max(p, 1 - p); a Choice uses its own confidence.

Every router versus Jev disagreement, and every Jev error or timeout, is logged as a
`jev_decide` JSON line on stdout and in `AGENT_JEV_LOG_PATH`, with the router path and
rule, Jev's disposition, rule, and confidence, the winner, and why (`gate`, `below_gate`,
`error`, `timeout`). The response slots carry the same summary under `jev_decide`.

## Replay and live check (2026-09-23)

`python -m services.agent.eval.jev_decide_replay capture | replay | live`.

Stored calls: no stored Jev answers existed for the current v3_hybrid payload (every
earlier store predates the `measure` question), so one pass was captured first:
`services/agent/eval/runs/jev_decide_store.json`, TypeSafe `jev-latest` (served
`jev-1.13.0`), 307 questions, 0 errors, $0.079. Holdout rows marked `needs_human_review` are
left out. v3 uses the 65 rows the independent ChatGPT labels made certain.

Disposition accuracy with the default gates, decline 0.8 and answer 0.9, replayed from the
store with no new Jev calls (`runs/jev_decide_replay.json`):

| Set | n | Status | Router alone | Jev alone | Decide | Jev won (fixed / broke) |
|---|---|---|---|---|---|---|
| dev | 137 | used for tuning | 0.934 | 0.964 | 0.964 | 7 (4 / 0) |
| v1 | 63 | seen, now development data | 0.905 | 0.937 | 0.952 | 7 (3 / 0) |
| v2 | 42 | seen, now development data | 0.857 | 0.857 | 0.905 | 4 (2 / 0) |
| v3 | 65 | **tuned** | 0.692 | 0.723 | 0.769 | 9 (5 / 0) |

Across all four sets Jev's wins fix 14 decisions and break 0. With a single 0.8 gate for
both directions (the first version) they fixed 15 and broke 1; the answer gate reverted the
only two answer-over-decline overrides, both in v3: `hv3_021` (Jev answered at 0.88 over the
router's correct `unsupported_ranking`, now kept) and `hv3_003` (Jev answered at 0.85 over the
router's `unexpressable_county_filter` and the label is answer, now also kept by the router).
v3 decide accuracy is unchanged at 0.769 because one fix and one break were both reverted.

None of these sets is clean; production shadow logs are the next clean test. The gates were
not tuned on them (decline gates 0.7 and 0.9 were run once, before the answer gate, only to
show sensitivity: v1 decide 0.984 and 0.937).

Where Jev wins:
- Fixed: prompt injection and off-topic questions the router sent to the model path
  (`inj_ignore_instructions`, `fp_orange_glow`, `ho_054` cost), missing years on open
  comparisons (`ho_035`, `hv2_045`), `undefined_region` for "up north".
- Broke none with the answer gate. Under the first, symmetric gate it broke `hv3_021`
  ("which counties had the largest increase"): the router refused with
  `unsupported_ranking` and Jev answered at 0.88. The 0.9 answer gate now keeps the router.
- Both declined but with different reasons in some rows: for "fires near Sacramento" the
  router's `undefined_spatial_scope` (asks for a radius) becomes Jev's `missing_location`
  (asks for coordinates). The disposition is the same; the clarifying question changes.

Live pass on dev only (`runs/jev_decide_live_dev.json`), runtime `decide_live` against the
replay of the same store: 136 of 137 final decisions match, 135 of 137 Jev dispositions
match, 0 errors, Jev asked on 113 (24 exempt), p50 373 ms and p95 467 ms per question when
asked, $0.029. The one difference, `collision_map_pge_outages_no_year`: `has_time_scope` came
back 0.8 live against 0.7 in the store, so under the first, symmetric gate the runtime
answered a map question that has no year instead of asking for one. That live pass predates
the answer gate and was not rerun; its recorded answer confidence is 0.8, below the 0.9
answer gate, so the runtime now keeps the router's `map_missing_year` clarification for that
response. Jev is not fully repeatable near a gate, which is the reason the answer gate sits
higher.
