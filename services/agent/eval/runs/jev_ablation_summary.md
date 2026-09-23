# Jev ablation, jev-1.13.0, 2026-09-22

Five repeats. v2_full is from the clean sequential run. The four v3 configs are from the rerun after connection retries and a cap of three in-flight calls. An earlier parallel burst reset the connection and is not used.

Majority-vote accuracy and flip rate. n is the number of scored cases.

| config | disposition | intent | dataset | tool_pick | clarify_reason | tokens | p50 | p95 |
|---|---|---|---|---|---|---:|---:|---:|
| v2_full | 81.0% / 6.7% (105) | 98.5% / 1.5% (66) | 100% / 0% (20) | 100% / 6.7% (15) | 100% / 7.7% (13) | 4887 | 430ms | 737ms |
| v3_split | 84.8% / 5.7% (105) | 93.9% / 0% (66) | 85% / 10% (20) | 53.3% / 13.3% (15) | 15.4% / 23.1% (13) | 3607 | 374ms | 652ms |
| v3_single | 83.8% / 3.8% (105) | 92.4% / 1.5% (66) | 90% / 5% (20) | 86.7% / 0% (15) | 15.4% / 7.7% (13) | 2969 | 370ms | 535ms |
| v3_no_glossary | 79.0% / 7.6% (105) | 93.9% / 0% (66) | 100% / 5% (20) | 80% / 6.7% (15) | 15.4% / 15.4% (13) | 3457 | 402ms | 737ms |
| v3_policy_context | 87.6% / 0% (105) | 97.0% / 1.5% (66) | 95% / 0% (20) | 100% / 6.7% (15) | 38.5% / 0% (13) | 6040 | 393ms | 714ms |

Paraphrase disposition: v2_full 85.4% flip 4.9%, v3_split 85.4% flip 7.3%, v3_single 85.4% flip 0%, v3_no_glossary 78.0% flip 7.3%, v3_policy_context 90.2% flip 0% (n=41).

v3_policy_context is the winner: highest disposition majority, and that majority did not flip across five repeats. Putting the v2 policy paragraph on every small call helped. Putting every v3 question in one call (v3_single) was a bit worse than split calls and much worse on tool pick than the policy-context run. Dropping the glossary hurt paraphrase disposition. Derived clarify_reason is weak in every v3 config because it is inferred from facts, while v2 asked for the reason directly and scored 100%.

Shadow default is `v3_hybrid`: the same policy-context calls, plus the v2 `clarify_reason` Choice. When the derived disposition is clarify, that direct answer is the scored reason.

Hybrid rerun, five repeats, jev-1.13.0:

| config | disposition | intent | dataset | tool_pick | clarify_reason | tokens | p50 / p95 |
|---|---|---|---|---|---|---:|---|
| v3_policy_context | 87.6% / flip 0% (105) | 95.5% / 4.5% (66) | 95% / 0% (20) | 100% / 0% (15) | 38.5% / 7.7% (13) | 6040 | 399 / 735 ms |
| v3_hybrid | 87.6% / flip 0% (105) | 97.0% / 3.0% (66) | 95% / 0% (20) | 100% / 6.7% (15) | 76.9% / 0% (13) | 6663 | 403 / 701 ms |

Paraphrase disposition is 90.2% flip 0 for both (n=41). Paraphrase clarify_reason is 66.7% for policy context and 100% for hybrid (n=3).

The only tool_pick flip is `detect_partial_200`: visualization_create at 0.32, 0.38, 0.25, and 0.31, and clarify at 0.28. Original confidence is 0.32. No flip had confidence >= 0.8, so that gate passes.

## Go / no-go

| gate | result |
|---|---|
| Parse mismatches, wiring errors, unexpected options, payload hash mismatches all 0 | PASS on the identity and repeat runs (0). This ablation retried connection errors and did not log a hash mismatch. |
| No-op diff clean | pending EC2 run |
| tool_pick majority >= 95% and >= qwen, flip < 5% at confidence >= 0.8 | majority 100% (n=15) on v3_policy_context. Overall flip is 6.7%. The >= 0.8 confidence bucket was not split out here. qwen comparison pending EC2 run |
| Derived disposition majority >= 90% on paraphrases | PASS, 90.2% (n=41), flip 0 |
| Choice flip < 5% at confidence >= 0.8 | PASS on the earlier 20x10 v2 repeat (0 flips at >= 0.7). Not remeasured inside this ablation. |

Phase 4 still waits on the EC2 no-op diff and the qwen comparison.
