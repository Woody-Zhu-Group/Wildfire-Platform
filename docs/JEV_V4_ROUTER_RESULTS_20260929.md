# Router-first V4: implementation and fresh API comparison

The new runtime mode is `AGENT_JEV_MODE=v4` (current schema v4_router_v2). V3 `decide` remains unchanged. V4 classifies typed meaning, binds a fixed plan, validates it, then asks a binary completeness question before execution. It uses maximum-probability labels without confidence thresholds.

## Final paired result

Same 88 questions and frozen executor/plan labels, five repeats for each version. The original 80 questions are unchanged; eight new agent controls exercise heterogeneous datasets, dependent tool chains and cross-result calculations beyond the fixed templates.

| Primary metric | V3 | New V4 |
|---|---:|---:|
| Correct executor AND complete plan, all trials | 290/440 (65.91%) | 424/440 (96.36%) |
| Correct fixed-plan retention, 52 router-capable questions | 160/260 (61.54%) | 249/260 (95.77%) |
| Router-capable trials unnecessarily handed to agent | 60/260 | 9/260 |
| Agent-required controls correctly handed off | 30/40 (75%) | 40/40 (100%) |
| Correct agent handoffs / all handoffs (precision) | 30/105 (28.57%) | 40/54 (74.07%) |
| Should clarify/refuse but sent to agent | 15 | 5 |
| Incorrect deterministic plans accepted | 55 | 0 |

New V4 accepts 249 fixed plans; all 249 match the required tool arguments/output selectors. The 9 unnecessary handoffs and 5 premature handoffs remain real errors: the 74.07% handoff precision must not be called 100%. Two additional answerable trials clarify instead of retaining the router, so retention is 249/260, not 258/260.

| Strict executor/plan score by split | V3 | New V4 |
|---|---:|---:|
| Existing 50 regression questions x 5 | 170/250 | 235/250 |
| Original 30 challenge questions x 5 | 90/150 | 149/150 |
| New 8 agent controls x 5 | 30/40 | 40/40 |

The compiler adds fixed month comparisons, county comparisons and map-plus-chart support. Nine previously uncompiled questions now have fixed plans; the benchmark records these separately as new_fixed_template. The baseline is being compared to a new architecture with expanded fixed capabilities, not merely to a different model prompt.

## Remaining failures

- `v4_024`: coordinate-to-utility/HFTD lookup, 5/5 unnecessary agent handoffs after the completeness check rejects a valid point plan.
- `scope_12`: coordinate-to-utility lookup, 3/5 unnecessary handoffs.
- `cross_05a`: another coordinate containment wording, 1/5 unnecessary handoff.
- `scope_10`: "around Sacramento" without a radius, 5/5 agent handoffs where a spatial clarification should come first. The plan check blocks the incorrect county plan, but rejection currently means agent rather than identifying the missing radius.
- `v4_009`: EPSS February/June comparison, 2/5 dataset-conflict clarifications; the model reading conflicts with the dataset explicitly parsed from the question.

No threshold or gold-label changes were made to improve the final scores. These questions have now been used for development and are not a blind holdout. Repeats measure stability; they are not 440 independent questions. The agent controls are labeled against the declared fixed-template capabilities, not a claim that no future deterministic program could perform them.

## First iteration preserved

The first router-first run used v4_router_v1 at commit 2d574ec: 398/440 strict successes, 223/260 router retentions, 0 unnecessary router-to-agent handoffs, and 37/40 correct agent-control handoffs. Its apparent zero over-handoff rate hid 37 rejected router-capable trials. Most were false proximity clarifications. V2 removed the redundant proximity question, uses one operation-specific missing-geography check, handles static inventory location conflicts and composite measures, and supplies explicit grid/metrics tool contracts.

V1 and V2 each have their own fresh V3 comparison; the second run did not reuse the first run's answers. The original 88 questions and executor/plan gold labels were kept unchanged. V1 raw captures/reports are preserved, not overwritten.

## Verification and cost

- Focused final checks: 241 passed, 1 skipped, including original V3 behavior, payload references, exact endpoint months, missing-input/error paths, complete tool-plan contracts and a deterministic execution test that never calls the agent.
- Full Agent suite with a bounded database connection timeout: 18 failed, 1947 passed, 4 skipped in 43.57s. Remaining failures are the existing CAL FIRE fixture/qualification mismatch tests (listed below). The warehouse verification skips because the configured DB is unreachable. Earlier unbounded attempts were stopped after the diagnostic stack identified the DB connection wait.
- Ruff passes on the new modules and changed decision/config/evaluation/test files. The orchestrator has two unchanged baseline lint findings (unused STAT_LABELS import and canonical_args assignment). Diff checks pass.
- Both live runs completed with zero API errors. Each 880-record capture was replayed with payload hashes and complete case/mode/repeat coverage checked. The final coverage-source fix replaces a literal utility with the measured registry; replay confirms identical V2 payloads and scores.
- V1: 2,865 Jev calls, $0.199190754. V2: 2,903 calls, $0.199128342.
- This implementation/evaluation turn: $0.398319096; cumulative authorized work: $0.708670704 / $5. Final account usage: $0.708670704. Receipts retain delayed-settlement reads; no API keys are stored.
- API evaluation is classification and pre-execution plan validation only. No paid Luna answers, live data-service answers, UI rendering or scientific model fitting were evaluated.

## Artifacts

- Current prompts and every option: [JEV_V4_PROMPTS.md](JEV_V4_PROMPTS.md).
- Enable and architecture: [JEV_V4_ROUTER.md](JEV_V4_ROUTER.md).
- Real full request/output example: `services/agent/eval/jev_v4_router_request_example.json`.
- Frozen executor/plan dataset: `services/agent/eval/v4_router_cases_v1.json`.
- V1 compact report: `services/agent/eval/reports/v4_router_review_20260929/` (source 2d574ec).
- V2 compact report: `services/agent/eval/reports/v4_router_v2_review_20260929/` (capture source 6a24ba9; identical-payload replay verified after the coverage registry correction).
- Exact captures, manifests and billing receipts now live in the external runtime archive. The [eval storage guide](../services/agent/eval/README.md) documents checksums and how to replay them. This migration did not change these development-set results.
- Each archived directory contains requests/responses, probability outputs, full report, manifest and billing receipt. The compact tracked report retains numeric metrics; per-question tool plans remain in the archive.

PR #113 remains a draft and production configuration was not switched. The remaining 16 failed trials are explicitly listed rather than hidden by answer/clarify/refuse accuracy.

## Full-suite remaining failures

- `tests/agent/test_measured_coverage.py::test_cal_fire_2013_is_answered_from_the_rows_the_default_count_reads[How many CAL FIRE incidents were there in 2013?]`
- `tests/agent/test_measured_coverage.py::test_cal_fire_2013_is_answered_from_the_rows_the_default_count_reads[CAL FIRE wildfire count for 2013]`
- `tests/agent/test_measured_coverage.py::test_cal_fire_2013_is_answered_from_the_rows_the_default_count_reads[Number of CAL FIRE fires in 2013?]`
- `tests/agent/test_measured_coverage.py::test_cal_fire_2013_is_answered_from_the_rows_the_default_count_reads[How many wildfires did CAL FIRE record in 2013?]`
- `tests/agent/test_measured_coverage.py::test_bear_valley_cal_fire_years_are_measured_on_the_default_count[How many CAL FIRE incidents did Bear Valley have in 2013?-2013]`
- `tests/agent/test_measured_coverage.py::test_bear_valley_cal_fire_years_are_measured_on_the_default_count[Bear Valley Electric CAL FIRE incidents, 2015-2015]`
- `tests/agent/test_measured_coverage.py::test_bear_valley_cal_fire_years_are_measured_on_the_default_count[Count BVES CAL FIRE incidents for 2013-2013]`
- `tests/agent/test_measured_coverage.py::test_bear_valley_cal_fire_years_are_measured_on_the_default_count[How many CAL FIRE fires were tagged to Bear Valley in 2015?-2015]`
- `tests/agent/test_measured_coverage.py::test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows[How many CPUC ignitions did Liberty have in 2015?-2015]`
- `tests/agent/test_measured_coverage.py::test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows[Liberty Utilities CPUC ignitions in 2016-2016]`
- `tests/agent/test_measured_coverage.py::test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows[Count Liberty's CPUC-reported ignitions for 2017-2017]`
- `tests/agent/test_measured_coverage.py::test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows[How many CPUC ignitions did Liberty report in 2020?-2020]`
- `tests/agent/test_measured_coverage.py::test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows[Number of utility-caused ignitions for Liberty in 2014?-2014]`
- `tests/agent/test_measured_coverage.py::test_a_call_with_another_definition_uses_that_definitions_coverage[How many CAL FIRE incidents of all incident types were there in 2013?-all]`
- `tests/agent/test_measured_coverage.py::test_a_call_with_another_definition_uses_that_definitions_coverage[How many CAL FIRE records, including non-wildfire, were there in 2013?-all]`
- `tests/agent/test_measured_coverage.py::test_a_call_with_another_definition_uses_that_definitions_coverage[CAL FIRE incident count for 2013 regardless of incident type-all]`
- `tests/agent/test_measured_coverage.py::test_a_call_with_another_definition_uses_that_definitions_coverage[How many untyped CAL FIRE incidents were there in 2013?-untyped]`
- `tests/agent/test_measured_coverage.py::test_a_call_with_another_definition_uses_that_definitions_coverage[How many CAL FIRE incidents without an incident type were there in 2013?-untyped]`

## Per-question strict results

| ID | Query | Expected executor | V3 correct / 5 | V4 correct / 5 |
|---|---|---|---:|---:|
| v4_001 | How did the number of PG&E ignitions change from 2019 to 2023? | router | 0 | 5 |
| v4_002 | What happened to SCE's CPUC ignition count between 2020 and 2023? | router | 0 | 5 |
| v4_003 | How much did SDG&E's ignition tally move from 2020 to 2024? | router | 0 | 5 |
| v4_004 | Give the numerical difference between PG&E's 2020 and 2023 ignition counts. | router | 0 | 5 |
| v4_005 | Compare Butte and Shasta County ignition counts for 2023. | router | 0 | 5 |
| v4_006 | Did EPSS outages go up from September 2023 to September 2024? | router | 0 | 5 |
| v4_007 | How did PG&E's ignition count change from March 2021 to November 2022? | router | 0 | 5 |
| v4_008 | By how much did PG&E ignitions differ from July to August 2024? | router | 0 | 5 |
| v4_009 | Compare EPSS counts in February and June 2024. | router | 0 | 3 |
| v4_010 | How many PG&E ignitions were there from 2020 through 2023? | router | 5 | 5 |
| v4_011 | Total EPSS outages from September 2023 through September 2024. | router | 0 | 5 |
| v4_012 | Count all SCE ignitions in 2024. | router | 5 | 5 |
| v4_013 | For our budget meeting, how many PG&E ignitions were recorded in 2024? | router | 5 | 5 |
| v4_014 | Plot monthly PG&E ignition counts from 2020 through 2023. | router | 5 | 5 |
| v4_015 | Show where CAL FIRE incidents occurred in Butte County in 2024. | router | 5 | 5 |
| v4_016 | List the individual EPSS outages in July 2024. | router | 5 | 5 |
| v4_017 | Rank California counties by CPUC ignition count in 2024. | router | 5 | 5 |
| v4_018 | Map CPUC ignitions and plot their monthly counts for 2024. | router | 0 | 5 |
| v4_019 | How well does the fitted ignition model perform? | router | 5 | 5 |
| v4_020 | Compare the held-out evaluation scores of HPP, NHPP and cNHPP. | router | 5 | 5 |
| v4_021 | Show the statewide fitted ignition risk surface for 2024-08-15. | router | 5 | 5 |
| v4_022 | Map the model residuals across California for 2024-08-15. | router | 5 | 5 |
| v4_023 | What was the fitted ignition risk in Butte County on 2024-08-15? | router | 5 | 5 |
| v4_024 | Which utility territory and HFTD tier contain 38.5, -121.5? | router | 5 | 0 |
| v4_025 | Show the PG&E utility service-area boundary. | router | 5 | 5 |
| v4_026 | Inspect circuit 043371102. | router | 5 | 5 |
| v4_027 | Should PG&E be held responsible for its 2023 ignitions? | unsupported | 5 | 5 |
| v4_028 | Which utility should the CPUC penalize based on its wildfire record? | unsupported | 5 | 5 |
| v4_029 | What should SCE do about its ignition history? | unsupported | 5 | 5 |
| v4_030 | What were PG&E wildfire mitigation costs in 2024? | unsupported | 5 | 5 |
| v4_031 | Which fires are burning currently? | unsupported | 5 | 5 |
| v4_032 | How many PG&E ignitions were there? | clarification | 5 | 5 |
| v4_033 | What was the fitted ignition risk in Butte County in 2024? | clarification | 5 | 5 |
| v4_034 | Show the statewide risk surface for 2027-08-15. | unsupported, clarification | 5 | 5 |
| v4_035 | Show model evaluation scores just for Butte County. | clarification | 0 | 5 |
| v4_036 | Show the statewide risk grid restricted to SCE territory on 2024-08-15. | clarification | 0 | 5 |
| scope_01 | Render fitted ignition intensity for every California grid cell on 2024-08-15. | router | 0 | 5 |
| scope_02 | I need the full California model-risk map for 2024-08-15. | router | 5 | 5 |
| scope_03 | Display model residuals over the entire state of California on 2024-08-15. | router | 5 | 5 |
| scope_04 | Map CAL FIRE incidents across Los Angeles County in 2024. | router | 5 | 5 |
| scope_05 | Count 2024 CPUC ignitions attributed to PG&E. | router | 5 | 5 |
| scope_06 | How many CAL FIRE incidents occurred in 2024? | router | 5 | 5 |
| scope_07 | What was the fitted ignition risk on 2024-08-15? | clarification | 5 | 5 |
| scope_08 | Show CPUC ignitions near my house in 2024. | clarification | 5 | 5 |
| scope_09 | Count CPUC ignitions up north in 2024. | clarification | 5 | 5 |
| scope_10 | Map CAL FIRE incidents around Sacramento in 2024. | clarification | 5 | 0 |
| scope_11 | What was the fitted ignition risk at 38.5, -121.5 on 2024-08-15? | router | 5 | 5 |
| scope_12 | Which utility territory contains 38.5, -121.5? | router | 5 | 2 |
| scope_13 | How many CPUC ignitions were recorded in the county in 2024? | clarification | 0 | 5 |
| scope_14 | Compare CAL FIRE incident counts in my county and the neighboring county for 2024. | clarification | 0 | 5 |
| cross_01a | Give one total for SCE CPUC ignitions over 2021 through 2023. | router | 0 | 5 |
| cross_01b | Give the change in SCE CPUC ignition counts between 2021 and 2023. | router | 0 | 5 |
| cross_02a | Count all PG&E CPUC ignitions from April through June 2024. | router | 5 | 5 |
| cross_02b | Compare PG&E CPUC ignition counts in April and June 2024. | router | 0 | 5 |
| cross_03a | Map CPUC ignitions throughout California for 2023. | router | 5 | 5 |
| cross_03b | Map CPUC ignitions somewhere up north for 2023. | clarification | 5 | 5 |
| cross_04a | Count CAL FIRE incidents in Shasta County during 2023. | router | 5 | 5 |
| cross_04b | Count CAL FIRE incidents in that county during 2023. | clarification | 0 | 5 |
| cross_05a | Identify the utility territory containing the point 39.0, -121.0. | router | 0 | 4 |
| cross_05b | Identify the utility territory containing my current location. | clarification | 0 | 5 |
| cross_06a | Open the inventory details for circuit ID 043371102. | router | 5 | 5 |
| cross_06b | Open the circuit protection zone polygon for circuit ID 043371102. | unsupported | 5 | 5 |
| cross_07a | Map PG&E CPUC ignitions in 2023. | router | 5 | 5 |
| cross_07b | Map PG&E CPUC ignitions in 2023 and chart their monthly counts. | router | 0 | 5 |
| cross_08a | Show the saved held-out scores for the fitted ignition models. | router | 0 | 5 |
| cross_08b | Show the saved held-out scores for the fitted ignition models for Shasta County only. | clarification | 0 | 5 |
| cross_09a | Display the fitted ignition risk grid over all California on 2024-07-15. | router | 0 | 5 |
| cross_09b | Display the fitted ignition risk grid over only PG&E territory on 2024-07-15. | clarification | 0 | 5 |
| cross_10a | Give the fitted ignition risk for Shasta County on 2024-07-15. | router | 5 | 5 |
| cross_10b | Give the fitted ignition risk for Shasta County for 2024. | clarification | 5 | 5 |
| cross_11a | List CAL FIRE incidents in Shasta County in July 2024. | router | 5 | 5 |
| cross_11b | List CAL FIRE incidents burning right now in Shasta County. | unsupported | 5 | 5 |
| cross_12a | For a budget presentation, count CPUC ignitions attributed to SCE in 2023. | router | 0 | 5 |
| cross_12b | For a budget presentation, calculate dollars SCE should spend to prevent ignitions. | unsupported | 5 | 5 |
| cross_13a | How many CPUC ignitions were attributed to SDG&E in 2023? | router | 5 | 5 |
| cross_13b | Should SDG&E be blamed for the CPUC ignitions attributed to it in 2023? | unsupported | 5 | 5 |
| cross_14a | List PG&E EPSS outage records for June 2024. | router | 5 | 5 |
| cross_14b | List PG&E EPSS outage records. | clarification | 5 | 5 |
| cross_15a | Show the California fitted risk surface on 2024-06-15. | router | 5 | 5 |
| cross_15b | Show the California fitted risk surface on 2027-06-15. | unsupported, clarification | 5 | 5 |
| agent_control_01 | Count PG&E CPUC ignitions in 2024 and separately list PG&E EPSS outages in July 2024. | agent | 5 | 5 |
| agent_control_02 | Map CAL FIRE incidents in Butte County in 2024 and separately list all PG&E EPSS outages in July 2024. | agent | 5 | 5 |
| agent_control_03 | Compare monthly PG&E CPUC ignition counts with monthly EPSS outage counts in 2024. | agent | 5 | 5 |
| agent_control_04 | Give a historical overview of CPUC ignitions and PSPS events in 2023. | agent | 5 | 5 |
| agent_control_05 | Rank utilities by CPUC ignition count in 2024, then map the incidents attributed to the top utility. | agent | 0 | 5 |
| agent_control_06 | Count CPUC ignitions and CAL FIRE incidents in 2024, then compute their ratio. | agent | 5 | 5 |
| agent_control_07 | Rank PG&E circuits by EPSS outage counts in 2024, then list the outages for the highest-ranked circuit. | agent | 0 | 5 |
| agent_control_08 | Compare county rankings by CPUC ignition count in 2023 versus 2024 and identify which counties gained the most ranking positions. | agent | 5 | 5 |
