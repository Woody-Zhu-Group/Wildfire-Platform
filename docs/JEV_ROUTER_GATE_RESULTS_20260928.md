# Router-gate classification comparison, 2026-09-28

**Recommendation: keep production on v3 `decide`. Do not deploy either
experimental policy yet.** The new gate avoids the reported span-count mistake,
but it rejects too many valid exact plans. The previous v4 policy over-clarifies.

## Scope and evidence

- 36 development questions, five repeats per Jev variant: 540 scored decisions
  and 1,260 API calls. No API errors. No generative-agent answers or data queries
  were executed by this classification evaluation.
- Provider: OpenRouter, model `typesafe/jev-1.13-20260917`. All variants use the
  same questions and date. Frozen historical gold sets were not edited.
- This is development data, not a clean holdout. Three questions were used in
  a wiring preflight; tool descriptions were added to the gate payload before
  the full capture. No prompt or threshold was tuned after the full capture.
- The full capture reports 2,007,760 input tokens, costing $0.08432592 at the
  input rate confirmed by account usage. Including the initial smoke call and
  wiring preflight, final key usage was **$0.085872528**, below the $5 budget.
  The immediate end-of-run account snapshot lagged billing; the later account
  read matched the accumulated input-token charges. No key is stored in reports.

Machine report: [report.json](../services/agent/eval/runs/router_gate_review_20260928/report.json).
Exact queries, requests, answers, probabilities and timing:
[captures.jsonl.gz](../services/agent/eval/runs/router_gate_review_20260928/captures.jsonl.gz).
The [manifest](../services/agent/eval/runs/router_gate_review_20260928/manifest.json)
pins the versioned case file; it is not a copy of user data or credentials.
The [billing receipt](../services/agent/eval/runs/router_gate_review_20260928/billing_receipt.json)
reconciles the complete session, including preflight calls.

## Comparison

Shared-intent scores cover 24 questions whose labels exist in all Jev versions,
repeated five times. Metrics/surface-only labels and out-of-domain questions
without an applicable intent label are excluded from this common score.

| Variant | Shared-intent correct | Mean intent confidence | Broad disposition correct | Cases with variation across repeats |
|---|---:|---:|---:|---:|
| Rule router | 14/24 (58.3%) | Not applicable | 31/36 (86.1%) | Deterministic |
| Production v3 `decide` | 120/120 (100%) | 0.989 | 170/180 (94.4%) | 2/36 |
| Previous v4 experiment | 120/120 (100%) | 0.963 | 94/180 (52.2%) | 7/36 |
| New `router_gate` | 120/120 (100%) | 0.970 | 155/180 (86.1%) | 4/36 |

The broad disposition score distinguishes answer, clarification and refusal.
It **cannot detect a wrong answer plan**: v3 still keeps a scalar span count on
the #108 change questions, even though Jev correctly reads `compare`. An agent
handoff is also not proof that the final answer will be correct. The 180 trials
are repetitions of 36 questions, not 180 independent questions or an OOS claim.

V3 retains all **70/70 valid original deterministic plans**, whereas the new
gate retains 21/70. V3 also retains **25 incomplete original plans** under the
development set's router-fit labels: five repeats each of v4_001, v4_002,
v4_003 (span totals for change questions), v4_018 (map without its requested
trend), and v4_036 (utility scalar instead of a restricted statewide grid).
These are plan-level findings, not 25 executed production answers. They explain
why strong intent and disposition scores alone do not resolve issue #108.

## Does the new gate correctly judge the router plan?

- Raw proposal-fit labels: **139/180 correct (77.2%)**, mean confidence **0.680**.
- This matched-payload evaluation collected labels even for guarded questions.
  The runtime would skip Jev for those questions. On the actually eligible
  subset, proposal-fit labels are **129/150 correct (86.0%)**. Both denominators
  are reported; neither changes the exact-plan retention result below.
- Four raw mistaken acceptances all concerned the SCE-restricted statewide
  risk-grid request. Their confidence was only 0.02 to 0.27, so the 0.9 acceptance
  gate prevented those exact calls from executing.
- There were 70 trials with valid deterministic router plans. Only **21/70
  (30%)** retained exact execution; the other **49/70 (70%)** went to the agent.
- At the current threshold, no incorrectly accepted deterministic plan was
  executed in this set. This does not establish safety on unseen questions.

Observed examples (real Jev responses):

| Query | Jev fit reading | Runtime outcome |
|---|---|---|
| How did the number of PG&E ignitions change from 2019 to 2023? | `agent`, confidence 0.98 to 0.99 in all five repeats; intent `compare` | Agent planning instead of the router's single span count. Actual data coverage still needs executor validation. |
| How many PG&E ignitions were there from 2020 through 2023? | `router`, confidence 0.89 to 0.92 | Only two repeats retain the valid exact count; three unnecessarily go to the agent. |
| Plot monthly PG&E ignition counts from 2020 through 2023. | `router`, confidence 0.88 to 0.92 | Three retain the correct exact chart; two go to the agent. |
| How well does the fitted ignition model perform? | Fit labels vary, confidence 0.02 to 0.13 | All five go to the agent, despite an existing valid `risk_metrics` call. |
| Show the statewide risk grid restricted to SCE territory on 2024-08-15. | Four low-confidence `router` readings and one `agent` reading | All five go to the agent; the mismatched utility scalar is not directly executed. |

Confidence and option probability are distinct API fields. For the model
performance example, one response gave `agent` probability 0.57 but confidence
0.13. Both are retained in the capture; the runtime uses the reported confidence.

## Offline threshold sensitivity, without additional API calls

| Router-acceptance threshold | Valid exact plans retained | Incorrect exact plans executed | Agent handoffs |
|---|---:|---:|---:|
| 0.9 (current) | 21/70 | 0 | 129/180 |
| 0.8 | 35/70 | 0 | 115/180 |
| 0.7 | 38/70 | 0 | 112/180 |
| 0.5 | 45/70 | 0 | 105/180 |

This is a sensitivity analysis on the same development records, not a selected
new threshold or independent validation. Merely reducing the threshold does not
resolve the gate's uncertainty about legitimate capabilities.

## Interpretation and verification

Jev's basic intent classification is strong on these questions. The difficult
part is judging whether a concrete API plan fully satisfies that intent. This
supports investigating a narrower intent-versus-plan compatibility decision,
with capability checks in code, before adding more broad confidence gates.
That alternative has not been implemented or claimed as validated here.

The previous v4 returned 106 clarifications out of 180 trials, versus 15 for
v3. Its many independent confidence checks substantially reduce answer coverage.
The new gate preserves fallbacks and exact plans in code, but its actual fit
readings still send too much work to the agent. Neither experiment is ready
for production on this evidence.

The false rejections of model-metrics and statewide-surface plans are especially
important: those harness tools are not in the ordinary seven-tool agent catalog.
Handing these questions to that agent can lose access to the requested capability,
not merely add latency. Their final answers have not been validated. A future
revision must address this capability boundary before rollout.

Regression checks: the split #107/#109 branch passed 120 tests and was merged
as PR #114. The mode work passed 261 focused tests with one skip before the
additional end-to-end gate test; the gate's dedicated suite then passed 15
tests. The full agent suite had 1,850 passes, four skips and the same 19 known
CAL FIRE-fixture/environment failures (including unavailable netCDF4 for the
metrics test). All 398 original route snapshots remain identical. Historical
v3 replay retains its previous per-set scores; those sets are already used for
development and do not add clean accuracy evidence for the new gate.
The final focused gate, v3, payload-pin, provenance and endpoint rerun passed
179 tests with one skip. Offline replay verified all 540 captured payloads
against the unchanged request builders without making new API calls.
