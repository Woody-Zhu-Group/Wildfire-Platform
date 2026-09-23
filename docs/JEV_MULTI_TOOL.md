# Multi-tool plans

Jev answers small facts. Code builds the plan. Jev does not pick a list of tools and does not write arguments.

`AGENT_JEV_MODE=plan` is off unless set. It uses the same confidence gate as `tool_pick` (default 0.8) and `_render_deterministic` writes one sentence per successful call. `off`, `shadow`, `tool_pick`, and `tool_pick_template` stay as they are.

The first holdout has been read. Do not report its disposition as a clean number.

## Facts

Reuse `intent`, `dataset`, and `rank_dimension` when they already say what is asked. Add these on the facts call, not on the tool_pick call, so the existing tool_pick payload hash does not change:

- Nouls: `wants_count`, `wants_list`, `wants_time_series`, `wants_map`, `wants_ranking`, `wants_comparison`
- Choice `breakdown`: `none`, `by_month`, `by_week`, `by_year`, `by_county`, `by_utility`, `by_cause`
- Noul `is_multi_part`: the question asks for more than one result, such as a count and a chart

Roughly 200 extra input tokens per question, all on the facts call. Six short Nouls are about 20 tokens each, and the breakdown Choice is about 80 tokens.

## Slots the router resolves today

`route_question` already fills:

- `utilities`: every named utility, as a list
- `years`: every year `resolve_time` returns, including a relative range
- `year`: one year when the parser has a single year
- `start_date` and `end_date`
- `dataset`: one dataset, or null when two catalogs are named
- `county`: one county
- `coords`: one latitude and longitude pair

It cannot return several counties. `_county` keeps the first name only. Adding a list means collecting every county mention the same way `_utilities` does, and teaching the planner to emit one call per county or a regions comparison when the schema allows it.

## Planner

`plan_calls(facts, slots)` returns an ordered list of `(tool, arguments)` or a fallback reason. It does not read `candidate_tools`. It builds the set itself.

- `by_month` or `by_week` becomes one `visualization_create` time series at that interval.
- `by_year` becomes one `data_query_records` count per year in `slots["years"]`. Two years and one scope can be `comparison_run` kind `periods` instead.
- Several utilities and a count become one `data_query_records` count per utility, or one `comparison_run` kind `utilities` when the metric and a single year are supported.
- `by_county` with a ranking intent becomes `data_query_rank` with `group_by=county`.
- A count plus a chart becomes `data_query_records` then `visualization_create`.

The router should hand the planner any question it now marks `multi_entity_deferred`: more than one utility, more than one county, more than one explicit year on a count, or a per-month, per-year, per-county, or per-utility breakdown. The planner replaces that fallback when every part can be planned. Until `plan` mode is on, those questions stay on the model path.

Execution reuses the two-tool path that `multi_intent_count_and_trend` already uses. Each call keeps its own `evidence_id`. Caveats attach per tool. `_render_deterministic` writes one sentence per result.

## Limits

A plan has at most 6 calls. Fall back to the model path when:

- a required slot is missing
- a planned tool cannot express the request
- the plan would exceed 6 calls
- any planning fact used in the plan is below 0.8

No partial plans. If any part of the question cannot be planned, fall back rather than answer half. A rendered number must appear in tool evidence.

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
