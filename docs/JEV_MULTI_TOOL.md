# Multi-tool plans

Jev answers small facts. Code builds the plan. Jev does not pick a list of tools and does not write arguments.

`AGENT_JEV_MODE=plan` is off unless set. It uses the same confidence gate as `tool_pick` (default 0.8) and `_render_deterministic` writes one sentence per successful call. `off`, `shadow`, `tool_pick`, and `tool_pick_template` stay as they are.

The first holdout has been read. Do not report its disposition as a clean number.

## Facts

The plan is built from choices, not from overlapping yes/no facts. `intent`, `dataset`, and `rank_dimension` already live on the topic call. `breakdown` stays on the facts call, and is sent only in plan mode: shadow, tool_pick, tool_pick_template, and any decide mode send exactly the nine-Noul facts call measured for PR 43. The `wants_*` Nouls and `is_multi_part` were removed: a count per year is both a count and a series, so those facts sat in the 0.2 to 0.8 band and the all-facts gate blocked the plan. How many years, utilities, or counties are named comes from router slots, not from Jev.

Two additions, only where intent plus breakdown cannot pick one plan:

- Choice `output_form`, on the facts call: `single_number`, `record_list`, `time_series`, `map`, `ranking`, `comparison`. Used only when `intent` is `multi_intent`, `other`, or `exploratory_overview`. A normal count, list, trend, map, rank, or comparison already names the form, so those plans do not gate on this Choice.
- Noul `also_chart`: "Besides the main result, the question also asks for a chart." Used only when the main form is a number or a comparison. A trend, map, rank, or list is already the chart or is not a chart, so those plans do not gate on it. Confidence is `max(p, 1-p)`. A low probability is a confident no.

The gate uses confidence in the chosen side, and only for facts that plan depends on:

| Plan | Facts |
| --- | --- |
| per-year counts | dataset. Intent is not gated when it is count or trend, and breakdown is not gated when it is none or by_year, because those readings are the same per-year plan. also_chart does not add a call. |
| per-utility counts | intent, dataset, breakdown, also_chart |
| per-county counts | intent, dataset, breakdown, also_chart |
| monthly or weekly series | intent, dataset, breakdown |
| rank | intent, dataset, breakdown, rank_dimension |
| count plus chart | intent, dataset, breakdown, also_chart |
| list | intent, dataset, breakdown |
| comparison | intent, dataset, breakdown, also_chart |
| single count | intent, dataset, breakdown, also_chart |
| map | intent, dataset, breakdown |

`breakdown` is on every row because `by_month` and `none` are different plans. `also_chart` is not on a series, map, rank, or list. `output_form` is added to the dependency list only when intent does not already name the form. `comparison_run` periods is still only for exactly two years. Three or more years are one count per year.

## Slots the router resolves today

`route_question` already fills:

- `utilities`: every named utility, as a list
- `years`: every year `resolve_time` returns, including a relative range
- `year`: one year when the parser has a single year
- `start_date` and `end_date`
- `dataset`: one dataset, or null when two catalogs are named
- `county`: one county, and only when exactly one county is named
- `counties`: every county the question names
- `coords`: one latitude and longitude pair

`_county` still returns the first name. The planner reads `counties` and emits one records count per county when a count is asked. A single records call still takes one county, so the plan is several calls, capped at 6.

## Planner

`plan_calls(facts, slots)` returns an ordered list of `(tool, arguments)` or a fallback reason. It does not read `candidate_tools`. It builds the set itself.

- `by_month` or `by_week` becomes one `visualization_create` time series at that interval.
- `by_year` becomes one `data_query_records` count per year in `slots["years"]`. `comparison_run` kind `periods` is used only for exactly two years. Three or more named years are one count per year.
- A list question with one dataset and one time scope becomes one `data_query_records` call with `result_mode=records`.
- Several utilities and a count become one `data_query_records` count per utility, or one `comparison_run` kind `utilities` when the metric and a single year are supported.
- `by_county` with a ranking intent becomes `data_query_rank` with `group_by=county`.
- A count plus a chart becomes `data_query_records` then `visualization_create`.

The router should hand the planner any question it now marks `multi_entity_deferred`: more than one utility, more than one county, more than one explicit year on a count, or a per-month, per-year, per-county, or per-utility breakdown. The planner replaces that fallback when every part can be planned. Until `plan` mode is on, those questions stay on the model path.

Execution reuses the two-tool path that `multi_intent_count_and_trend` already uses. Each call keeps its own `evidence_id`. Caveats attach per tool. `_render_deterministic` writes one sentence per result.

## Limits

A plan has at most 6 calls, except a per-entity count plan, which may use 10. Planning runs only when the derived disposition is answer. A clarify, an unsupported topic, or a prompt-injection outcome does not produce calls. Fall back to the model path when:

- a required slot is missing
- a planned tool cannot express the request
- the plan would exceed 6 calls
- any planning fact is unresolved. For a yes/no fact the gate uses confidence in the chosen side, `max(p, 1 - p)`. A probability of 0.08 is a confident no. A probability between 0.2 and 0.8 is unresolved and the plan falls back
- the finished plan does not cover every requested component or every named entity

No partial plans. After the calls are built, the planner checks count, list, series, map, ranking, comparison, and each named utility, county, and year. If any requested part is missing, it falls back. A rendered number must appear in tool evidence.

## Spatial lookup then risk, not built

A coordinate plus a past day is already a deterministic two-step chain in the router: `data_query_spatial` returns the grid cell, then `risk_forecast` scores that cell. The planner cannot emit that pair yet. The cell id is not in the question. It exists only after the spatial call returns, and `plan_calls` has to write every argument before any tool runs. Wiring it would mean a second planning step that reads the spatial result, checks the cell id is present, and only then builds the risk call. If the spatial call fails or returns no cell, the whole plan falls back. That second step is not implemented.

## Gaps the seen holdout exposed

These are not a license to tune that holdout. They are tool limits.

- No yearly series interval. `visualization_create` allows daily, weekly, or monthly. Per-year counts are several records calls, or a periods comparison for exactly two years. That is a workaround, not a new interval.
- `comparison_run` kind `periods` holds two ranges, not three. Three named years need one count per year.
- `comparison_run` kind `utilities` needs the utilities and one date range. It can carry several utilities in one call when the metric exists.
- `data_query_rank` allows CPUC by county or utility, CAL FIRE by county, and EPSS by circuit. Other rankings are refusals. Several calls do not make an illegal ranking legal.
- A records call takes one utility and one county. Several entities need several calls.
- The national ignition set has no county column. A county count there cannot be planned.

## Known issue, not tuned here

`prompt_injection` is 0.65 on "Optimize next week's PSPS schedule." That fires before the off-topic rule and nulls `unsupported_topic` even though `off_topic` is `optimization_or_scheduling` at 1.00.

## Projection from stored answers

This is an estimate, not a new measurement. Dev tool_pick where the gold tool was actually offered was 14/14. The seen holdout tool_pick where the gold tool was offered was 18/19. Most of the remaining misses never offered the gold tool.

| Set | Would fix | Still fall back | Still wrong |
|---|---|---|---|
| Dev tool_pick misses | 0 of the offered misses (there are none) | Questions the router now defers, until plan mode is on | Rankings the schema refuses |
| Seen holdout tool_pick | About 4 of 11, where slots already hold every utility or every year and the intent was rank or compare | County pairs, because the router stores one county | A ranking that is not an allowed triple, and a three-year span answered as one list |
| Seen holdout partial router answers | 3 (monthly series, per-year counts, per-utility counts plus a chart) | 0 if the planner accepts them | 0 if the plan is all or nothing |

The first holdout has been read. Do not treat its disposition as clean.
