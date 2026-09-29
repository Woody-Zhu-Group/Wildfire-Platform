# V3 / latest V4 comparison set v1

80 questions: 50 observed regression cases and 30 new questions in 15 contrast pairs. Labels were frozen before live capture and remain a reviewable draft, not an independently annotated gold standard. The completed fresh V3/V4 comparison is in [the September 29 results](JEV_V3_V4_RESULTS_20260929.md). The JSON's unrun status describes the dataset at freeze time; its contents were preserved for hash verification.

## Versions and protocol

- V3: existing `decide_v3`, including its original confidence gates and router fallback.
- Latest V4: `v4_scope_argmax`, highest-probability Choice labels, binary facts at 0.5, no confidence rejection, and `missing_geographic_scope`. This is the offline experimental policy, not `router_gate` and not a replacement of production `decide`.
- Same pinned Jev model, exact questions, single-turn context, and reference date 2026-09-29. All dates are explicit; no relative-date gold label depends on the wall clock.
- Five repeats per question per version; alternate version ordering each round. Capture all three schema calls independently for each version, including request/response/probabilities. No reuse of September 28/29 model outputs as fresh results.
- Freeze/review the JSON and Git revision before capture. Report regression and new_challenge separately; do not retune labels or prompts after seeing results. These new contrast cases were designed with knowledge of earlier failures, so they are not a blind holdout.
- 800 version trials / 2,400 Jev API calls at five repeats. Repeats measure stability; the independent question count is 80, with correlated contrast pairs. No Luna or backend tools run.

## Scoring

1. Shared-vocabulary intent accuracy: exclude V4-only risk_surface/model_metrics labels from BOTH denominators. Keep these queries in the other metrics.
2. Disposition accuracy: answer / clarification / unsupported. Both model and deterministic map to answer. Error is always incorrect. Report each split/category, unnecessary clarification, unsafe answering, path counts and repeat stability.
3. Plan review, separate from disposition: review each emitted deterministic tool plan and view against the per-question requirements below. A span total is not a change between periods; a map alone is not map-plus-trend. Record pass/fail and reason. Runner exports these rows with review pending; it does not automatically certify plan completeness. A model handoff is not_executed, not a passing plan.
4. For special operations, require an executable risk_metrics/risk_surface path; the ordinary agent tool catalog lacks them. Final answer/data correctness and end-to-end cost remain unmeasured until a separate execution evaluation.

## Run

From the repository root (normal project Python dependencies required), preview without API calls:

```powershell
python -m services.agent.eval.router_gate_compare --cases services/agent/eval/jev_v3_v4_comparison_v1.json
```

When running the authorized classification comparison, use a NEW output directory and an explicit remaining budget cap. Example cap $1 (within the previously authorized cumulative $5):

```powershell
python -m services.agent.eval.router_gate_compare --cases services/agent/eval/jev_v3_v4_comparison_v1.json --run --repeats 5 --cap-usd 1 --output services/agent/eval/runs/jev_v3_v4_comparison_v1
```

The ignored `.env` supplies the API key. No keys belong in this dataset or results. `report.json` includes per-question plans and per-split/category counts. Backend failure or budget stop means the run is incomplete, not a full comparison. Offline replay uses `--replay <captures.jsonl.gz> --output <new-directory>` with the same `--cases` and `--repeats`; hashes and complete case/mode/repeat coverage are checked.

## Reviewable questions

| ID | Split / contrast | Query | Accepted disposition | Plan requirement / label rationale |
|---|---|---|---|---|
| v4_001 | regression / comparison | How did the number of PG&E ignitions change from 2019 to 2023? | answer | Preserve each named utility/county and both comparison periods; a single total over their union is insufficient. |
| v4_002 | regression / comparison | What happened to SCE's CPUC ignition count between 2020 and 2023? | answer | Preserve each named utility/county and both comparison periods; a single total over their union is insufficient. |
| v4_003 | regression / comparison | How much did SDG&E's ignition tally move from 2020 to 2024? | answer | Preserve each named utility/county and both comparison periods; a single total over their union is insufficient. |
| v4_004 | regression / comparison | Give the numerical difference between PG&E's 2020 and 2023 ignition counts. | answer | Preserve each named utility/county and both comparison periods; a single total over their union is insufficient. |
| v4_005 | regression / comparison | Compare Butte and Shasta County ignition counts for 2023. | answer | Preserve each named utility/county and both comparison periods; a single total over their union is insufficient. |
| v4_006 | regression / month_endpoints | Did EPSS outages go up from September 2023 to September 2024? | answer | Compare separate complete endpoint months, preserving utilities and dataset; do not sum the intervening span. |
| v4_007 | regression / month_endpoints | How did PG&E's ignition count change from March 2021 to November 2022? | answer | Compare separate complete endpoint months, preserving utilities and dataset; do not sum the intervening span. |
| v4_008 | regression / month_endpoints | By how much did PG&E ignitions differ from July to August 2024? | answer | Compare separate complete endpoint months, preserving utilities and dataset; do not sum the intervening span. |
| v4_009 | regression / month_endpoints | Compare EPSS counts in February and June 2024. | answer | Compare separate complete endpoint months, preserving utilities and dataset; do not sum the intervening span. |
| v4_010 | regression / count_controls | How many PG&E ignitions were there from 2020 through 2023? | answer | Return one total for exactly the requested dataset, utility and full inclusive period. |
| v4_011 | regression / count_controls | Total EPSS outages from September 2023 through September 2024. | answer | Return one total for exactly the requested dataset, utility and full inclusive period. |
| v4_012 | regression / count_controls | Count all SCE ignitions in 2024. | answer | Return one total for exactly the requested dataset, utility and full inclusive period. |
| v4_013 | regression / count_controls | For our budget meeting, how many PG&E ignitions were recorded in 2024? | answer | Return one total for exactly the requested dataset, utility and full inclusive period. |
| v4_014 | regression / views | Plot monthly PG&E ignition counts from 2020 through 2023. | answer | Preserve all requested outputs (map, time chart, records or ranking) and every named filter. |
| v4_015 | regression / views | Show where CAL FIRE incidents occurred in Butte County in 2024. | answer | Preserve all requested outputs (map, time chart, records or ranking) and every named filter. |
| v4_016 | regression / views | List the individual EPSS outages in July 2024. | answer | Preserve all requested outputs (map, time chart, records or ranking) and every named filter. |
| v4_017 | regression / views | Rank California counties by CPUC ignition count in 2024. | answer | Preserve all requested outputs (map, time chart, records or ranking) and every named filter. |
| v4_018 | regression / views | Map CPUC ignitions and plot their monthly counts for 2024. | answer | Preserve all requested outputs (map, time chart, records or ranking) and every named filter. |
| v4_019 | regression / special_tools | How well does the fitted ignition model perform? | answer | Model scores require risk_metrics; statewide grid/residuals require risk_surface with the correct kind/date; a county score is not a statewide surface. |
| v4_020 | regression / special_tools | Compare the held-out evaluation scores of HPP, NHPP and cNHPP. | answer | Model scores require risk_metrics; statewide grid/residuals require risk_surface with the correct kind/date; a county score is not a statewide surface. |
| v4_021 | regression / special_tools | Show the statewide fitted ignition risk surface for 2024-08-15. | answer | Model scores require risk_metrics; statewide grid/residuals require risk_surface with the correct kind/date; a county score is not a statewide surface. |
| v4_022 | regression / special_tools | Map the model residuals across California for 2024-08-15. | answer | Model scores require risk_metrics; statewide grid/residuals require risk_surface with the correct kind/date; a county score is not a statewide surface. |
| v4_023 | regression / special_tools | What was the fitted ignition risk in Butte County on 2024-08-15? | answer | Model scores require risk_metrics; statewide grid/residuals require risk_surface with the correct kind/date; a county score is not a statewide surface. |
| v4_024 | regression / lookups | Which utility territory and HFTD tier contain 38.5, -121.5? | answer | Preserve the requested point, inventory identifier or boundary; circuit inventory is not CPZ. |
| v4_025 | regression / lookups | Show the PG&E utility service-area boundary. | answer | Preserve the requested point, inventory identifier or boundary; circuit inventory is not CPZ. |
| v4_026 | regression / lookups | Inspect circuit 043371102. | answer | Preserve the requested point, inventory identifier or boundary; circuit inventory is not CPZ. |
| v4_027 | regression / unsupported_advice | Should PG&E be held responsible for its 2023 ignitions? | unsupported | Do not invent a responsibility judgment, penalty recommendation or operational advice. |
| v4_028 | regression / unsupported_advice | Which utility should the CPUC penalize based on its wildfire record? | unsupported | Do not invent a responsibility judgment, penalty recommendation or operational advice. |
| v4_029 | regression / unsupported_advice | What should SCE do about its ignition history? | unsupported | Do not invent a responsibility judgment, penalty recommendation or operational advice. |
| v4_030 | regression / unsupported_topics | What were PG&E wildfire mitigation costs in 2024? | unsupported | Do not fabricate unavailable live information, budget or CPZ data. |
| v4_031 | regression / unsupported_topics | Which fires are burning currently? | unsupported | Do not fabricate unavailable live information, budget or CPZ data. |
| v4_032 | regression / constraints | How many PG&E ignitions were there? | clarification | Keep every requested restriction; explain the missing or unsupported input instead of silently broadening the request. |
| v4_033 | regression / constraints | What was the fitted ignition risk in Butte County in 2024? | clarification | Keep every requested restriction; explain the missing or unsupported input instead of silently broadening the request. |
| v4_034 | regression / constraints | Show the statewide risk surface for 2027-08-15. | clarification, unsupported | Keep every requested restriction; explain the missing or unsupported input instead of silently broadening the request. |
| v4_035 | regression / constraints | Show model evaluation scores just for Butte County. | clarification | Keep every requested restriction; explain the missing or unsupported input instead of silently broadening the request. |
| v4_036 | regression / constraints | Show the statewide risk grid restricted to SCE territory on 2024-08-15. | clarification | Keep every requested restriction; explain the missing or unsupported input instead of silently broadening the request. |
| scope_01 | regression / geographic_scope | Render fitted ignition intensity for every California grid cell on 2024-08-15. | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_02 | regression / geographic_scope | I need the full California model-risk map for 2024-08-15. | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_03 | regression / geographic_scope | Display model residuals over the entire state of California on 2024-08-15. | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_04 | regression / geographic_scope | Map CAL FIRE incidents across Los Angeles County in 2024. | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_05 | regression / geographic_scope | Count 2024 CPUC ignitions attributed to PG&E. | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_06 | regression / geographic_scope | How many CAL FIRE incidents occurred in 2024? | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_07 | regression / geographic_scope | What was the fitted ignition risk on 2024-08-15? | clarification | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_08 | regression / geographic_scope | Show CPUC ignitions near my house in 2024. | clarification | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_09 | regression / geographic_scope | Count CPUC ignitions up north in 2024. | clarification | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_10 | regression / geographic_scope | Map CAL FIRE incidents around Sacramento in 2024. | clarification | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_11 | regression / geographic_scope | What was the fitted ignition risk at 38.5, -121.5 on 2024-08-15? | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_12 | regression / geographic_scope | Which utility territory contains 38.5, -121.5? | answer | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_13 | regression / geographic_scope | How many CPUC ignitions were recorded in the county in 2024? | clarification | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| scope_14 | regression / geographic_scope | Compare CAL FIRE incident counts in my county and the neighboring county for 2024. | clarification | Preserve the exact requested geography and output; do not invent a county, point or radius. |
| cross_01a | new_challenge / contrast_01 | Give one total for SCE CPUC ignitions over 2021 through 2023. | answer | One total over the inclusive 2021-2023 span, utility SCE, CPUC records. |
| cross_01b | new_challenge / contrast_01 | Give the change in SCE CPUC ignition counts between 2021 and 2023. | answer | Separate 2021 and 2023 counts and their difference; a span total fails. |
| cross_02a | new_challenge / contrast_02 | Count all PG&E CPUC ignitions from April through June 2024. | answer | One count from April 1 through June 30, including May. |
| cross_02b | new_challenge / contrast_02 | Compare PG&E CPUC ignition counts in April and June 2024. | answer | Separate April 1-30 and June 1-30 counts; May is not a comparison endpoint. |
| cross_03a | new_challenge / contrast_03 | Map CPUC ignitions throughout California for 2023. | answer | Statewide CPUC event map for 2023; no county is required. |
| cross_03b | new_challenge / contrast_03 | Map CPUC ignitions somewhere up north for 2023. | clarification | Ask for a defined geographic area; do not invent northern California boundaries. |
| cross_04a | new_challenge / contrast_04 | Count CAL FIRE incidents in Shasta County during 2023. | answer | CAL FIRE count, Shasta County, 2023, dataset default incident definition. |
| cross_04b | new_challenge / contrast_04 | Count CAL FIRE incidents in that county during 2023. | clarification | No conversation history is supplied; ask which county. |
| cross_05a | new_challenge / contrast_05 | Identify the utility territory containing the point 39.0, -121.0. | answer | Point containment at lat 39.0, lon -121.0; no distance is needed. |
| cross_05b | new_challenge / contrast_05 | Identify the utility territory containing my current location. | clarification | No location/device context is supplied; request a point or location. |
| cross_06a | new_challenge / contrast_06 | Open the inventory details for circuit ID 043371102. | answer | Circuit inventory lookup preserving the leading zero; not a CPZ request. |
| cross_06b | new_challenge / contrast_06 | Open the circuit protection zone polygon for circuit ID 043371102. | unsupported | CPZ polygons are unavailable; do not substitute circuit lines or HFTD polygons. |
| cross_07a | new_challenge / contrast_07 | Map PG&E CPUC ignitions in 2023. | answer | Map CPUC records with utility PG&E and year 2023. |
| cross_07b | new_challenge / contrast_07 | Map PG&E CPUC ignitions in 2023 and chart their monthly counts. | answer | Both a map and monthly count chart with the same utility/year filters. |
| cross_08a | new_challenge / contrast_08 | Show the saved held-out scores for the fitted ignition models. | answer | Read saved risk_metrics; no new model fit and no event-count substitute. |
| cross_08b | new_challenge / contrast_08 | Show the saved held-out scores for the fitted ignition models for Shasta County only. | clarification | County-restricted metrics are unavailable; do not return global scores as county scores. |
| cross_09a | new_challenge / contrast_09 | Display the fitted ignition risk grid over all California on 2024-07-15. | answer | risk_surface for 2024-07-15 with risk kind, covering the full grid. |
| cross_09b | new_challenge / contrast_09 | Display the fitted ignition risk grid over only PG&E territory on 2024-07-15. | clarification | A territory-filtered grid is unsupported; do not substitute a scalar territory score. |
| cross_10a | new_challenge / contrast_10 | Give the fitted ignition risk for Shasta County on 2024-07-15. | answer | County fitted risk at the exact supported day. |
| cross_10b | new_challenge / contrast_10 | Give the fitted ignition risk for Shasta County for 2024. | clarification | An exact day is required; do not choose a day or aggregate daily risk silently. |
| cross_11a | new_challenge / contrast_11 | List CAL FIRE incidents in Shasta County in July 2024. | answer | Historical individual incident records with county and complete July window. |
| cross_11b | new_challenge / contrast_11 | List CAL FIRE incidents burning right now in Shasta County. | unsupported | Live fire status is unavailable; historical records are not a substitute. |
| cross_12a | new_challenge / contrast_12 | For a budget presentation, count CPUC ignitions attributed to SCE in 2023. | answer | The deliverable is a historical count; budget is context, not the requested measure. |
| cross_12b | new_challenge / contrast_12 | For a budget presentation, calculate dollars SCE should spend to prevent ignitions. | unsupported | No cost/optimization model or spending recommendation is available. |
| cross_13a | new_challenge / contrast_13 | How many CPUC ignitions were attributed to SDG&E in 2023? | answer | Historical attribute-based count; include normal attribution caveats. |
| cross_13b | new_challenge / contrast_13 | Should SDG&E be blamed for the CPUC ignitions attributed to it in 2023? | unsupported | Recorded attribution does not establish normative blame or liability. |
| cross_14a | new_challenge / contrast_14 | List PG&E EPSS outage records for June 2024. | answer | Individual outage records, PG&E, complete June 2024 window. |
| cross_14b | new_challenge / contrast_14 | List PG&E EPSS outage records. | clarification | Ask for the required time window; do not silently select a year. |
| cross_15a | new_challenge / contrast_15 | Show the California fitted risk surface on 2024-06-15. | answer | risk_surface for a supported past day, preserving the statewide grid output. |
| cross_15b | new_challenge / contrast_15 | Show the California fitted risk surface on 2027-06-15. | clarification, unsupported | No future prediction; either decline or request a supported past day. |
