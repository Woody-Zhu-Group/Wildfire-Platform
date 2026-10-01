# V4 without confidence gates: paired offline comparison

Follow-up: the [2026-09-29 live geography-question comparison](JEV_V4_SCOPE_RESULTS_20260929.md)
replaces broadness with missing required geographic information, while retaining
argmax selection. The results below remain the original confidence ablation.

The same 180 real v4 responses (36 development queries, five repeats each) were
replayed twice: once through the original policy and once with confidence
gating disabled. No API calls, new prompts, new labels or fabricated confidence
values were used. Additional cost: **$0**.

## Exact change

- Each Choice uses its highest-probability option. Confidence remains in the
  trace but cannot block execution. All Choice labels already matched their
  maximum probability in this capture, so **zero labels changed**.
- Binary Noul facts choose true at probability >= 0.5 and false below it. This
  is a binary classification boundary, not a confidence gate. Setting every
  threshold to zero would incorrectly turn false facts into true facts.
- Missing/invalid responses, unsupported labels, dates, places, capabilities
  and evidence requirements remain enforced. Production v3 and router_gate are
  unchanged. The original v4 policy remains the default reference for comparison.
- Maximum-probability ties retain the provider's chosen maximum (otherwise
  lexical order). There were no Choice ties in this captured set.

## Results

| Metric | Original v4 | V4 argmax, no confidence gates | V3 reference |
|---|---:|---:|---:|
| Broad disposition agreement | 94/180 (52.2%) | **169/180 (93.9%)** | 170/180 (94.4%) |
| Clarifications | 106 | **27** | 15 |
| Questions with variable path/rule across five repeats | 7/36 | **2/36** | 2/36 |
| V4 intent labels correct, all applicable questions | 155/155 | **155/155** | Different vocabulary |

Removing the gates corrected 75 previously mismatched dispositions and created
zero new mismatches under this coarse scoring rule. That does not mean every
behavior became safer: four previously uncertain clarifications became wrong
CPZ refusals. The same predictions now reach the underlying policy rules.

The remaining **11 mismatches concern three questions**:

| Case | Query | Remaining mismatch |
|---|---|---|
| v4_021 | Show the statewide fitted ignition risk surface for 2024-08-15. | 5/5 clarify `undefined_region` because `broad_region` is true, although statewide grid output is supported. |
| v4_022 | Map the model residuals across California for 2024-08-15. | 2/5 hit the same broad-region rule. |
| v4_026 | Inspect circuit 043371102. | 4/5 select `off_topic=cpz`, leading to an incorrect refusal of an ordinary circuit lookup. |

The intent is correctly selected in these cases. The failures arise from other
topic/scope facts and their policy interaction. They were deliberately left
unchanged to isolate this ablation.

## Interpretation

On these development captures, the earlier v4 result was dominated by confidence
gating, not wrong top-intent choices. Without those gates, its broad disposition
score is close to v3. A difference of one result out of 180 repeated trials is
not evidence that either version generalizes better.

This is a paired replay, **not a new live run, clean holdout, or end-to-end
answer-quality test**. It does not measure Luna's generated answers or database
values. A supported `model` route counts as an answer disposition, not proof
that the final answer is correct. No production mode was enabled or merged.

## Reproduce

```powershell
python -m services.agent.eval.v4_argmax --output <new-report.json>
```

The runner verifies every reused v4 request against its payload hash and refuses
to overwrite an existing report. It preserves the original capture. The
[machine report](../services/agent/eval/runs/router_gate_review_20260928/v4_argmax_report.json)
contains all 180 before/after decisions and correctness flags. The raw API
responses remain in the original `captures.jsonl.gz` beside it.

Validation: 75 focused policy, replay, v3 and router-gate tests passed, one
existing test skipped. New/modified Python files pass Ruff and diff checks.
