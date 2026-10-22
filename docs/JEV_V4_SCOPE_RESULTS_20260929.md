# Missing-geographic-scope question: live paired comparison

**The revised question improves this development comparison.** It distinguishes
large but defined areas from missing location information. Production v3 and
router_gate remain unchanged; this is an unmerged v4 experiment without
confidence gates.

## What changed

The old binary fact was `broad_region`:

> The question refers to a broad region such as northern California or up north rather than a specific county or utility.

It was mapped to an `undefined_region` clarification. A statewide grid could
satisfy that statement despite having a perfectly defined geographic scope.

The replacement is `missing_geographic_scope`, a Noul yes/no judgment. Its exact
instructions are:

> The request still lacks geographic scope information required for its requested operation. Judge missing or unresolved location or boundary information, not the size of the area. A named statewide area, county, utility territory, grid cell, or coordinates is defined. A count or list without any requested geographic restriction uses the dataset's full coverage. A statewide grid or model-performance query does not need a narrower location. An inventory lookup by identifier needs no geographic filter. A needed point or local location, an unnamed county, or an undefined boundary needs clarification.

Yes maps to a request for the missing location/boundary; no continues through
the existing checks. Choices use argmax, and Noul facts use p >= 0.5. There is
no confidence rejection in either experimental arm. Missing responses, date and
capability checks still apply. The old question remains available as a baseline;
the new facts payload is versioned `v4_scope_v1` in `decisions/v4_scope.py`.

## Method and cost

- Original 36 development queries plus 14 geographic controls written before
  implementation, five repeats each: **250 paired trials and 1,000 API calls**.
- Each pair sends old facts, new facts, one shared topic call and one shared
  places call. Only the one fact and facts-version tag differ in the request
  definitions. Old/new facts order alternates across repeats.
- Provider/model: OpenRouter `typesafe/jev-1.13-20260917`; date 2026-09-29.
- Both arms were freshly captured. Today's old-question baseline is 171/180;
  the earlier 169/180 came from a different capture and is not this A/B control.
- No API errors. No prompt, threshold or expected label was tuned after capture.
- Cost: **$0.05025972** for 1,196,660 input tokens. Cumulative authorized work:
  **$0.136132248 of the $5 cap**. A later account read reconciles charges because
  the immediate end-of-run usage snapshot lagged billing.

These are development sets, not independent holdouts. Five repeats measure
consistency, not five times as many independent questions. This evaluates
classification and policy disposition, not final answers, SQL results or maps.

## Results

| Set | Old broad-region question | New missing-scope question |
|---|---:|---:|
| Original 36 queries | 171/180 (95.0%) | **177/180 (98.3%)** |
| 14 geographic controls | 55/70 (78.6%) | **65/70 (92.9%)** |
| Combined | 226/250 (90.4%) | **242/250 (96.8%)** |

The five supported statewide-map questions, including the new paraphrases,
improved from **14/25 to 25/25** correct dispositions. The query that asks for
events in "the county" without naming it improved from 0/5 to 5/5 correct
clarifications.

The new binary fact itself scored **63/70 (90.0%)** on controls with explicit
missing-scope labels, with mean Noul confidence **0.857**:

- 23 true positives, 7 false negatives, 40 true negatives, 0 false positives.
- The seven misses were still handled by another existing spatial-scope check.
  The binary fact alone is therefore not a complete replacement for all checks.
- The old fact asks about a different property, so it is not scored against the
  new fact's missing-information labels.

For completeness, mean Noul confidence (`max(p, 1-p)`, reported but never used
as a gate) is 0.883 old versus 0.794 new on the original set, and 0.803 versus
0.857 on the controls. These refer to different predicates and should not be
treated as directly comparable calibration accuracy.

## Improvements, remaining failures and paired variability

The live paired results contain **17 improvements and 1 regression**. The
regression was a coordinate lookup where the unchanged `vague_proximity` fact
crossed 0.5 between the old/new facts calls. Another repeat of that same query
changed in the opposite direction. This is why shared topic/places outputs do
not eliminate all within-facts variability.

An additional **offline attribution check** kept every old fact except the
replaced geography fact, using the new missing-scope probability for that one
field. It gave 16 improvements, zero regressions, and the same 242/250 overall
score. This is a composed replay, not a separately captured live pipeline.

Remaining failures:

- Original set: one coordinate lookup blocked by `vague_proximity`, plus two
  ordinary circuit lookups refused as CPZ by `off_topic`.
- Controls: all five repeats of "Which utility territory contains 38.5, -121.5?"
  were blocked by the unchanged `vague_proximity` check. The new missing-scope
  fact correctly read the coordinates as sufficient.

The result supports replacing broadness with missing required information.
It does not show that all geographic checks or topic distinctions are solved,
and it is not evidence of production-wide 98.3% answer accuracy. The remaining
rules were not changed to improve this score.

## Reproduction and artifacts

```powershell
python -m services.agent.eval.v4_scope_compare
python -m services.agent.eval.v4_scope_compare --run --repeats 5 --cap-usd 1 --output <new-directory>
python -m services.agent.eval.v4_scope_compare --replay <captures.jsonl.gz> --output <new-directory>
```

The default command makes no network calls. Live calls require an explicit
budget and new output directory. Replay validates the stored payloads and makes
no API calls. The original v3 and v4 payloads are preserved.

Artifacts: [report](../services/agent/eval/runs/v4_scope_review_20260929/report.json),
[complete requests and responses](../services/agent/eval/runs/v4_scope_review_20260929/captures.jsonl.gz),
[manifest](../services/agent/eval/runs/v4_scope_review_20260929/manifest.json), and
[billing receipt](../services/agent/eval/runs/v4_scope_review_20260929/billing_receipt.json).

Verification: 89 focused tests passed, one existing test skipped; the original
v3 payload pins and all 398 route snapshots remain unchanged. Offline replay
validated all 250 paired captures and reproduced the metrics without new API
calls. Ruff and diff integrity checks pass. The capture was checked to exclude
the configured API key.
