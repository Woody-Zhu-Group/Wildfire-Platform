# V3 vs latest V4: fresh paired classification run, 2026-09-29

Latest V4 scored 381/400 (95.25%) and V3 scored 350/400 (87.50%) on the frozen 80-question set. Both scored 220/220 on shared-vocabulary intent labels. The remaining failures occur after intent selection, in topic/scope decisions and policy handling.

## Protocol

- Frozen source commit: `2c053bf1ec6a86a644d1c1eac1add038c13c4acc`.
- Dataset: `services/agent/eval/jev_v3_v4_comparison_v1.json`; 50 known regression questions plus 30 newly authored questions in 15 contrast pairs. Labels were not changed during or after this run.
- V3 is the original `decide_v3` policy with its confidence gates and router fallback. Latest V4 is the experimental `v4_scope_argmax`: highest-probability Choice labels, binary facts at 0.5, no confidence rejection, and the revised missing-geographic-scope question. It is not the separate `router_gate` mode.
- Same Jev model `typesafe/jev-1.13-20260917`, reference date 2026-09-29, same questions, five repeats per version; versions interleaved and order alternated each round. All three payloads fetched independently for each trial.
- 800 completed trials, 2,400 fresh Jev calls, zero API errors. All 800 payload hashes and replay decisions verified offline. No Luna planning, data queries, final answers or UI execution.
- No model/prompt/threshold tuning after capture. The 30 new questions were designed with knowledge of earlier failures: this is a controlled development comparison, not a blind holdout. Five repeats do not make 400 independent questions; contrast pairs are correlated.

## Results

| Metric | V3 | Latest V4 |
|---|---:|---:|
| Disposition, all 80 questions x 5 | 350/400 (87.50%) | 381/400 (95.25%) |
| Regression, 50 questions x 5 | 225/250 (90.00%) | 241/250 (96.40%) |
| New challenges, 30 questions x 5 | 125/150 (83.33%) | 140/150 (93.33%) |
| Shared intent labels, 44 questions x 5 | 220/220 (100%) | 220/220 (100%) |
| Agent/model handoffs | 75 | 196 |
| Deterministic tool plans | 205 | 50 |
| Clarifications | 70 | 85 |
| Refusals | 50 | 69 |
| API errors | 0 | 0 |
| Queries with any intent/path/rule variation across repeats | 3/80 | 5/80 |

Disposition means answer / clarification / unsupported. Both deterministic and model paths count as answer; it does not certify a valid executable plan or a correct final answer. Shared intent excludes V4-only model_metrics/risk_surface labels and unannotated intent cases from both denominators; all those questions remain in disposition scoring.

Paired trials: V4 correct / V3 wrong = 50; V3 correct / V4 wrong = 19; both correct = 331; both wrong = 0. Net gain is 31/400, or 7.75 percentage points, on this designed set. This does not establish a population-wide improvement or justify deployment.

V3 has 35 answer attempts where clarification was required. V4 has none of that category here, but makes 9 incorrect CPZ refusals, 5 unnecessary spatial clarifications, and 5 live-data refusals where location clarification was needed.

## Remaining V4 failures and cause

1. `v4_026` (4/5) and `cross_06a` (5/5): circuit inventory is misread as CPZ. Intent is correctly circuit_detail, but the separate off_topic Choice ranks cpz first. Example: cpz 0.45 vs on_topic 0.42. Without confidence rejection, that top topic produces unsupported_cpz.
2. `scope_12` (5/5): a point-containment query with explicit coordinates is incorrectly considered vague proximity. missing_geographic_scope is correctly false (0.11-0.12), but the unchanged vague_proximity fact is 0.51-0.55 and triggers undefined_spatial_scope.
3. `cross_05b` (5/5): "my current location" is classified as live_or_web (top probability 0.42-0.54), so V4 refuses before using its correctly identified missing location (0.95-0.96). It should request a location. V3 receives coarse credit for clarification, but its ambiguous_relative_time wording asks the wrong question; neither version is fully satisfactory on this query.

V4 fixes V3 disposition failures involving unexpressible county model metrics, territory-filtered risk grids, unnamed counties, statewide-grid paraphrases, and a historical count mentioned in budget context. Main intent classification alone would miss these failures.

## Static tool-plan audit

Every emitted deterministic plan was reviewed against the frozen per-question requirements, including map/stat selectors. Detailed arguments and review reasons are in `plan_review.json`.

| Emitted deterministic plans | V3 | Latest V4 |
|---|---:|---:|
| Pass supplied plan requirements | 160/205 | 50/50 |
| Fail supplied plan requirements | 45/205 | 0/50 |

The denominators differ because V4 delegates far more queries. The 50 V4 plans cover only 10 special-tool questions repeated five times; the 196 model handoffs were not executed or scored as passing plans. This is a pre-orchestration audit: downstream guards may still modify or reject V3 proposals.

V3 plan failures (each five repeats):

- `v4_001`, `v4_002`, `v4_003`: span totals instead of changes between periods.
- `v4_018`, `cross_07b`: map returned in the plan without the requested monthly chart.
- `scope_13`, `cross_04b`: unresolved county dropped, leaving a statewide query.
- `v4_036`, `cross_09b`: scalar utility risk substituted for a filtered statewide grid.

V4 sends the period-comparison and combined-map/chart queries to the model, and clarifies the unresolved/unsupported filters. This avoids the above deterministic proposals here; it does not prove the model will answer correctly. Final quality, latency and total cost need a separate end-to-end evaluation.

## Cost and artifacts

- Input tokens: 4,148,080; token-accounted cost: $0.174219360. Later account delta: $0.174219360.
- Prior authorized work: $0.136132248; cumulative token-accounted spend: $0.310351608, within the $5 total limit. This run used a $1 stop cap.
- One zero-call setup attempt failed because the disposable runtime lacked the SDK; this incurred no Jev tokens/charge and was not included as a scored trial.
- Artifacts: `services/agent/eval/runs/jev_v3_v4_comparison_v1/{manifest.json,captures.jsonl.gz,report.json,plan_review.json,billing_receipt.json}`. Captures include queries, requests, raw probability outputs and hashes. No credentials are stored.
- Focused dataset/runner tests: 6 passed. Full capture replay: 800 records verified, no API calls.

Production modes were not changed, and PR #113 remains a draft. The next design discussion should address the three V4 failure classes above before considering broader execution evaluation.

## Per-question disposition results

| ID | Query | Expected | V3 correct / 5 | V4 correct / 5 |
|---|---|---|---:|---:|
| v4_001 | How did the number of PG&E ignitions change from 2019 to 2023? | answer | 5 | 5 |
| v4_002 | What happened to SCE's CPUC ignition count between 2020 and 2023? | answer | 5 | 5 |
| v4_003 | How much did SDG&E's ignition tally move from 2020 to 2024? | answer | 5 | 5 |
| v4_004 | Give the numerical difference between PG&E's 2020 and 2023 ignition counts. | answer | 5 | 5 |
| v4_005 | Compare Butte and Shasta County ignition counts for 2023. | answer | 5 | 5 |
| v4_006 | Did EPSS outages go up from September 2023 to September 2024? | answer | 5 | 5 |
| v4_007 | How did PG&E's ignition count change from March 2021 to November 2022? | answer | 5 | 5 |
| v4_008 | By how much did PG&E ignitions differ from July to August 2024? | answer | 5 | 5 |
| v4_009 | Compare EPSS counts in February and June 2024. | answer | 5 | 5 |
| v4_010 | How many PG&E ignitions were there from 2020 through 2023? | answer | 5 | 5 |
| v4_011 | Total EPSS outages from September 2023 through September 2024. | answer | 5 | 5 |
| v4_012 | Count all SCE ignitions in 2024. | answer | 5 | 5 |
| v4_013 | For our budget meeting, how many PG&E ignitions were recorded in 2024? | answer | 5 | 5 |
| v4_014 | Plot monthly PG&E ignition counts from 2020 through 2023. | answer | 5 | 5 |
| v4_015 | Show where CAL FIRE incidents occurred in Butte County in 2024. | answer | 5 | 5 |
| v4_016 | List the individual EPSS outages in July 2024. | answer | 5 | 5 |
| v4_017 | Rank California counties by CPUC ignition count in 2024. | answer | 5 | 5 |
| v4_018 | Map CPUC ignitions and plot their monthly counts for 2024. | answer | 5 | 5 |
| v4_019 | How well does the fitted ignition model perform? | answer | 5 | 5 |
| v4_020 | Compare the held-out evaluation scores of HPP, NHPP and cNHPP. | answer | 5 | 5 |
| v4_021 | Show the statewide fitted ignition risk surface for 2024-08-15. | answer | 5 | 5 |
| v4_022 | Map the model residuals across California for 2024-08-15. | answer | 5 | 5 |
| v4_023 | What was the fitted ignition risk in Butte County on 2024-08-15? | answer | 5 | 5 |
| v4_024 | Which utility territory and HFTD tier contain 38.5, -121.5? | answer | 5 | 5 |
| v4_025 | Show the PG&E utility service-area boundary. | answer | 5 | 5 |
| v4_026 | Inspect circuit 043371102. | answer | 5 | 1 |
| v4_027 | Should PG&E be held responsible for its 2023 ignitions? | unsupported | 5 | 5 |
| v4_028 | Which utility should the CPUC penalize based on its wildfire record? | unsupported | 5 | 5 |
| v4_029 | What should SCE do about its ignition history? | unsupported | 5 | 5 |
| v4_030 | What were PG&E wildfire mitigation costs in 2024? | unsupported | 5 | 5 |
| v4_031 | Which fires are burning currently? | unsupported | 5 | 5 |
| v4_032 | How many PG&E ignitions were there? | clarification | 5 | 5 |
| v4_033 | What was the fitted ignition risk in Butte County in 2024? | clarification | 5 | 5 |
| v4_034 | Show the statewide risk surface for 2027-08-15. | clarification, unsupported | 5 | 5 |
| v4_035 | Show model evaluation scores just for Butte County. | clarification | 0 | 5 |
| v4_036 | Show the statewide risk grid restricted to SCE territory on 2024-08-15. | clarification | 0 | 5 |
| scope_01 | Render fitted ignition intensity for every California grid cell on 2024-08-15. | answer | 0 | 5 |
| scope_02 | I need the full California model-risk map for 2024-08-15. | answer | 5 | 5 |
| scope_03 | Display model residuals over the entire state of California on 2024-08-15. | answer | 5 | 5 |
| scope_04 | Map CAL FIRE incidents across Los Angeles County in 2024. | answer | 5 | 5 |
| scope_05 | Count 2024 CPUC ignitions attributed to PG&E. | answer | 5 | 5 |
| scope_06 | How many CAL FIRE incidents occurred in 2024? | answer | 5 | 5 |
| scope_07 | What was the fitted ignition risk on 2024-08-15? | clarification | 5 | 5 |
| scope_08 | Show CPUC ignitions near my house in 2024. | clarification | 5 | 5 |
| scope_09 | Count CPUC ignitions up north in 2024. | clarification | 5 | 5 |
| scope_10 | Map CAL FIRE incidents around Sacramento in 2024. | clarification | 5 | 5 |
| scope_11 | What was the fitted ignition risk at 38.5, -121.5 on 2024-08-15? | answer | 5 | 5 |
| scope_12 | Which utility territory contains 38.5, -121.5? | answer | 5 | 0 |
| scope_13 | How many CPUC ignitions were recorded in the county in 2024? | clarification | 0 | 5 |
| scope_14 | Compare CAL FIRE incident counts in my county and the neighboring county for 2024. | clarification | 0 | 5 |
| cross_01a | Give one total for SCE CPUC ignitions over 2021 through 2023. | answer | 5 | 5 |
| cross_01b | Give the change in SCE CPUC ignition counts between 2021 and 2023. | answer | 5 | 5 |
| cross_02a | Count all PG&E CPUC ignitions from April through June 2024. | answer | 5 | 5 |
| cross_02b | Compare PG&E CPUC ignition counts in April and June 2024. | answer | 5 | 5 |
| cross_03a | Map CPUC ignitions throughout California for 2023. | answer | 5 | 5 |
| cross_03b | Map CPUC ignitions somewhere up north for 2023. | clarification | 5 | 5 |
| cross_04a | Count CAL FIRE incidents in Shasta County during 2023. | answer | 5 | 5 |
| cross_04b | Count CAL FIRE incidents in that county during 2023. | clarification | 0 | 5 |
| cross_05a | Identify the utility territory containing the point 39.0, -121.0. | answer | 5 | 5 |
| cross_05b | Identify the utility territory containing my current location. | clarification | 5 | 0 |
| cross_06a | Open the inventory details for circuit ID 043371102. | answer | 5 | 0 |
| cross_06b | Open the circuit protection zone polygon for circuit ID 043371102. | unsupported | 5 | 5 |
| cross_07a | Map PG&E CPUC ignitions in 2023. | answer | 5 | 5 |
| cross_07b | Map PG&E CPUC ignitions in 2023 and chart their monthly counts. | answer | 5 | 5 |
| cross_08a | Show the saved held-out scores for the fitted ignition models. | answer | 5 | 5 |
| cross_08b | Show the saved held-out scores for the fitted ignition models for Shasta County only. | clarification | 0 | 5 |
| cross_09a | Display the fitted ignition risk grid over all California on 2024-07-15. | answer | 0 | 5 |
| cross_09b | Display the fitted ignition risk grid over only PG&E territory on 2024-07-15. | clarification | 0 | 5 |
| cross_10a | Give the fitted ignition risk for Shasta County on 2024-07-15. | answer | 5 | 5 |
| cross_10b | Give the fitted ignition risk for Shasta County for 2024. | clarification | 5 | 5 |
| cross_11a | List CAL FIRE incidents in Shasta County in July 2024. | answer | 5 | 5 |
| cross_11b | List CAL FIRE incidents burning right now in Shasta County. | unsupported | 5 | 5 |
| cross_12a | For a budget presentation, count CPUC ignitions attributed to SCE in 2023. | answer | 0 | 5 |
| cross_12b | For a budget presentation, calculate dollars SCE should spend to prevent ignitions. | unsupported | 5 | 5 |
| cross_13a | How many CPUC ignitions were attributed to SDG&E in 2023? | answer | 5 | 5 |
| cross_13b | Should SDG&E be blamed for the CPUC ignitions attributed to it in 2023? | unsupported | 5 | 5 |
| cross_14a | List PG&E EPSS outage records for June 2024. | answer | 5 | 5 |
| cross_14b | List PG&E EPSS outage records. | clarification | 5 | 5 |
| cross_15a | Show the California fitted risk surface on 2024-06-15. | answer | 5 | 5 |
| cross_15b | Show the California fitted risk surface on 2027-06-15. | clarification, unsupported | 5 | 5 |
