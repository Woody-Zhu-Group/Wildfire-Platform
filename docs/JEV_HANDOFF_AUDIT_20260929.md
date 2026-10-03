# Correction: router retention and agent handoff audit

The prior 95.25% figure measures answer/clarify/refuse agreement, not correct router-versus-agent routing. It must not be used to claim that latest V4 achieves the requested router-first objective. The tested `v4_scope_argmax` policy normally hands ordinary supported intents directly to the model; it does not evaluate an existing router plan. The separate `router_gate` mode does evaluate a concrete plan but was not the latest variant tested and still contains confidence gates.

This audit reuses all 800 recorded trials without API calls, preserves the frozen questions and original metrics, and adds explicit handoff categories in `handoff_audit.json`. Labels below were reviewed after results were observed, so this is a diagnostic audit, not a new blind benchmark.

## What the 196 V4 handoffs contain

| Category | Handoffs | Interpretation |
|---|---:|---|
| Original query already produces a complete router plan | 106 | Confirmed unnecessary handoff |
| Existing on-topic recovery produces the complete router plan | 10 | Confirmed unnecessary handoff; budget context need not trigger agent planning |
| Existing router template works after a checked meaning-preserving rephrasing | 35 | Capability exists, but automatic normalization is missing; an observed integration gap, not proof a general agent is needed |
| No complete plan verified from the current router templates | 45 | Agent-planning candidates only; neither necessity nor final agent execution was verified |

**At least 116/196 (59.18%) of V4 handoffs are unnecessary without adding normalization.** Even if every remaining handoff were justified, handoff precision could be at most 80/196 (40.82%) under that criterion. With the 35 normalization-gap trials counted against the user's capability-based objective, 151/196 (77.04%) go to operations already expressible by existing fixed templates. Only 45/196 (22.96%) remain candidates; 22.96% is an upper bound, not a measured successful-agent rate.

Example confirmed waste, each five repeats: SCE 2024 ignition count, monthly PG&E ignition chart, Butte County CAL FIRE map, July 2024 EPSS record list, CPUC county ranking, county fitted risk, utility boundary lookup. Existing exact tool calls are stored alongside every annotation.

The 35 normalization-gap handoffs cover seven queries: `v4_001`, `v4_002`, `v4_003`, `v4_004`, `cross_01b` (annual endpoint comparisons), `cross_01a` (one span total), and `cross_05a` (point containment). The original router fails or emits a bad plan, but checked equivalent phrasing produces a complete fixed plan. Thus even a gate asking only whether the original proposal is valid is not enough to achieve capability-based routing.

## V3 comparison and retention

| Handoffs by reviewed category | V3 | Latest V4 |
|---|---:|---:|
| Total agent handoffs | 75 | 196 |
| Original query has complete router plan | 0 | 106 |
| Existing topic-recovery plan available | 0 | 10 |
| Existing template, normalization gap | 25 | 35 |
| Should clarify/refuse before any answer planning | 15 | 0 |
| Current router has no verified complete plan | 35 | 45 |

There are 43 queries (215 repeats) with demonstrated fixed-template capability: 31 direct, 2 recoverable, and 10 normalization cases (including 3 special-tool queries V4 handles directly). V3 emits a correct deterministic plan for 160/215 (74.42%); latest V4 does so for 50/215 (23.26%). These are static plan-retention rates, not final-answer accuracy. V3 also retains incorrect comparison/map/grid proposals, as documented in the original plan review; retaining all proposals blindly is not a solution.

For the narrower 33 queries already directly handled or covered by the existing topic-recovery mechanism, V3 correctly retains 160/165 (96.97%); latest V4 retains 35/165 (21.21%). V4 unnecessarily hands off 116/165 and wrongly clarifies/refuses 14/165. V3's other five trials wrongly refuse the budget-context count.

## Cause and corrected acceptance criteria

- `jev_first.decide_from_answers` maps ordinary intent labels to candidate tools and returns `model`; the offline latest-V4 experiment removed router participation for most common tasks. This is an architecture mismatch with the requested gate, not evidence that the user queries intrinsically need an agent.
- The prior dataset collapsed both executors into `answer`. The reported score rewarded an agent handoff even when an exact router plan existed. That was the wrong primary metric.
- Before another paid run, fix and freeze executor labels separately from disposition: router-capable, requires clarification/refusal, and requires planning beyond supported fixed templates. Preserve parameters and requested outputs in every router-capability witness.
- A corrected Jev flow should identify the operation and slots against the router's supported templates, attempt the fixed plan, validate completeness, and hand off only if no complete supported fixed plan is available. It must not require a generative agent merely to repair a wording mismatch. Keep V3 as its own mode; do not reintroduce a confidence threshold the user asked to remove.
- Primary metrics must be unnecessary-handoff rate, router retention among router-capable queries, incomplete-plan acceptance, and handoff precision/recall on an independently reviewed needs-agent category. Clarifications/refusals must be checked for the right missing input or unsupported capability, separately. Final answer quality remains a separate execution test.

No runtime policy was changed and no API calls were made for this audit. A new paid comparison of the same misaligned V4 would not resolve the design error. The existing total API spend remains $0.310351608.

## Per-question capability evidence

| ID | Capability category | Query | Reason |
|---|---|---|---|
| v4_001 | router_template_normalization_gap | How did the number of PG&E ignitions change from 2019 to 2023? | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| v4_002 | router_template_normalization_gap | What happened to SCE's CPUC ignition count between 2020 and 2023? | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| v4_003 | router_template_normalization_gap | How much did SDG&E's ignition tally move from 2020 to 2024? | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| v4_004 | router_template_normalization_gap | Give the numerical difference between PG&E's 2020 and 2023 ignition counts. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| v4_005 | agent_planning_candidate | Compare Butte and Shasta County ignition counts for 2023. | Router has no county-comparison candidate in its explicit-comparison builder; requires a county comparison or separate scoped reads. |
| v4_006 | agent_planning_candidate | Did EPSS outages go up from September 2023 to September 2024? | Existing router comparison candidate uses whole calendar years, not two endpoint months. |
| v4_007 | agent_planning_candidate | How did PG&E's ignition count change from March 2021 to November 2022? | Existing router comparison candidate uses whole calendar years, not two endpoint months. |
| v4_008 | agent_planning_candidate | By how much did PG&E ignitions differ from July to August 2024? | Existing router comparison candidate uses whole calendar years, not two endpoint months. |
| v4_009 | agent_planning_candidate | Compare EPSS counts in February and June 2024. | Existing router comparison candidate uses whole calendar years, not two endpoint months. |
| v4_010 | router_direct | How many PG&E ignitions were there from 2020 through 2023? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_011 | agent_planning_candidate | Total EPSS outages from September 2023 through September 2024. | Raw and explicitly dated paraphrases both defer through multi_entity_deferred/open_ended; no verified complete fixed-router span-count plan. |
| v4_012 | router_direct | Count all SCE ignitions in 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_013 | router_existing_topic_recovery | For our budget meeting, how many PG&E ignitions were recorded in 2024? | Budget is presentation context. The existing skip_topic_judgments recovery produces the exact count plan; V3 already has this recovery mechanism. It must only be used after an on-topic judgment. |
| v4_014 | router_direct | Plot monthly PG&E ignition counts from 2020 through 2023. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_015 | router_direct | Show where CAL FIRE incidents occurred in Butte County in 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_016 | router_direct | List the individual EPSS outages in July 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_017 | router_direct | Rank California counties by CPUC ignition count in 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_018 | agent_planning_candidate | Map CPUC ignitions and plot their monthly counts for 2024. | Current matching router plan emits only a map; monthly chart is omitted. |
| v4_019 | router_direct | How well does the fitted ignition model perform? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_020 | router_direct | Compare the held-out evaluation scores of HPP, NHPP and cNHPP. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_021 | router_direct | Show the statewide fitted ignition risk surface for 2024-08-15. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_022 | router_direct | Map the model residuals across California for 2024-08-15. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_023 | router_direct | What was the fitted ignition risk in Butte County on 2024-08-15? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_024 | router_direct | Which utility territory and HFTD tier contain 38.5, -121.5? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_025 | router_direct | Show the PG&E utility service-area boundary. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_026 | router_direct | Inspect circuit 043371102. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| v4_027 | clarify_or_refuse | Should PG&E be held responsible for its 2023 ignitions? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_028 | clarify_or_refuse | Which utility should the CPUC penalize based on its wildfire record? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_029 | clarify_or_refuse | What should SCE do about its ignition history? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_030 | clarify_or_refuse | What were PG&E wildfire mitigation costs in 2024? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_031 | clarify_or_refuse | Which fires are burning currently? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_032 | clarify_or_refuse | How many PG&E ignitions were there? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_033 | clarify_or_refuse | What was the fitted ignition risk in Butte County in 2024? | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_034 | clarify_or_refuse | Show the statewide risk surface for 2027-08-15. | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_035 | clarify_or_refuse | Show model evaluation scores just for Butte County. | No answer-planning handoff is warranted before the required clarification or refusal. |
| v4_036 | clarify_or_refuse | Show the statewide risk grid restricted to SCE territory on 2024-08-15. | No answer-planning handoff is warranted before the required clarification or refusal. |
| scope_01 | router_template_normalization_gap | Render fitted ignition intensity for every California grid cell on 2024-08-15. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| scope_02 | router_direct | I need the full California model-risk map for 2024-08-15. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_03 | router_direct | Display model residuals over the entire state of California on 2024-08-15. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_04 | router_direct | Map CAL FIRE incidents across Los Angeles County in 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_05 | router_direct | Count 2024 CPUC ignitions attributed to PG&E. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_06 | router_direct | How many CAL FIRE incidents occurred in 2024? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_07 | clarify_or_refuse | What was the fitted ignition risk on 2024-08-15? | No answer-planning handoff is warranted before the required clarification or refusal. |
| scope_08 | clarify_or_refuse | Show CPUC ignitions near my house in 2024. | No answer-planning handoff is warranted before the required clarification or refusal. |
| scope_09 | clarify_or_refuse | Count CPUC ignitions up north in 2024. | No answer-planning handoff is warranted before the required clarification or refusal. |
| scope_10 | clarify_or_refuse | Map CAL FIRE incidents around Sacramento in 2024. | No answer-planning handoff is warranted before the required clarification or refusal. |
| scope_11 | router_direct | What was the fitted ignition risk at 38.5, -121.5 on 2024-08-15? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_12 | router_direct | Which utility territory contains 38.5, -121.5? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| scope_13 | clarify_or_refuse | How many CPUC ignitions were recorded in the county in 2024? | No answer-planning handoff is warranted before the required clarification or refusal. |
| scope_14 | clarify_or_refuse | Compare CAL FIRE incident counts in my county and the neighboring county for 2024. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_01a | router_template_normalization_gap | Give one total for SCE CPUC ignitions over 2021 through 2023. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| cross_01b | router_template_normalization_gap | Give the change in SCE CPUC ignition counts between 2021 and 2023. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| cross_02a | router_direct | Count all PG&E CPUC ignitions from April through June 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_02b | agent_planning_candidate | Compare PG&E CPUC ignition counts in April and June 2024. | Existing router comparison candidate uses whole calendar years, not two endpoint months. |
| cross_03a | router_direct | Map CPUC ignitions throughout California for 2023. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_03b | clarify_or_refuse | Map CPUC ignitions somewhere up north for 2023. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_04a | router_direct | Count CAL FIRE incidents in Shasta County during 2023. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_04b | clarify_or_refuse | Count CAL FIRE incidents in that county during 2023. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_05a | router_template_normalization_gap | Identify the utility territory containing the point 39.0, -121.0. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| cross_05b | clarify_or_refuse | Identify the utility territory containing my current location. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_06a | router_direct | Open the inventory details for circuit ID 043371102. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_06b | clarify_or_refuse | Open the circuit protection zone polygon for circuit ID 043371102. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_07a | router_direct | Map PG&E CPUC ignitions in 2023. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_07b | agent_planning_candidate | Map PG&E CPUC ignitions in 2023 and chart their monthly counts. | Current matching router plan emits only a map; monthly chart is omitted. |
| cross_08a | router_template_normalization_gap | Show the saved held-out scores for the fitted ignition models. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| cross_08b | clarify_or_refuse | Show the saved held-out scores for the fitted ignition models for Shasta County only. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_09a | router_template_normalization_gap | Display the fitted ignition risk grid over all California on 2024-07-15. | Existing fixed-router template can express this request after a manually checked, meaning-preserving rephrasing. This is an offline capability witness; automatic normalization is not implemented or evaluated here. |
| cross_09b | clarify_or_refuse | Display the fitted ignition risk grid over only PG&E territory on 2024-07-15. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_10a | router_direct | Give the fitted ignition risk for Shasta County on 2024-07-15. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_10b | clarify_or_refuse | Give the fitted ignition risk for Shasta County for 2024. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_11a | router_direct | List CAL FIRE incidents in Shasta County in July 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_11b | clarify_or_refuse | List CAL FIRE incidents burning right now in Shasta County. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_12a | router_existing_topic_recovery | For a budget presentation, count CPUC ignitions attributed to SCE in 2023. | Budget is presentation context. The existing skip_topic_judgments recovery produces the exact count plan; V3 already has this recovery mechanism. It must only be used after an on-topic judgment. |
| cross_12b | clarify_or_refuse | For a budget presentation, calculate dollars SCE should spend to prevent ignitions. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_13a | router_direct | How many CPUC ignitions were attributed to SDG&E in 2023? | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_13b | clarify_or_refuse | Should SDG&E be blamed for the CPUC ignitions attributed to it in 2023? | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_14a | router_direct | List PG&E EPSS outage records for June 2024. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_14b | clarify_or_refuse | List PG&E EPSS outage records. | No answer-planning handoff is warranted before the required clarification or refusal. |
| cross_15a | router_direct | Show the California fitted risk surface on 2024-06-15. | The unchanged router already emits a complete plan for the original question, verified in the earlier static plan audit. |
| cross_15b | clarify_or_refuse | Show the California fitted risk surface on 2027-06-15. | No answer-planning handoff is warranted before the required clarification or refusal. |
