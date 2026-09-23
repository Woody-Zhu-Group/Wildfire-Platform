# Multi-entity questions: the slot planner

The router defers a question that names several utilities, several counties, several explicit years, or a per-period breakdown as `multi_entity_deferred`, because one deterministic call would drop an entity or flatten the breakdown. `AGENT_SLOT_PLAN` (default off) lets code turn some of those deferrals into several deterministic calls built only from router slots. Jev is not called.

Jev's own planner (`AGENT_JEV_MODE=plan`, the breakdown, output_form, and also_chart questions, `planner.py`) lost to the slot rule on the seen holdouts and had known paths that answered a narrower question. It is kept on the `jev-plan-archive` branch and is not on main.

## What the slot rule plans

`services/agent/eval/slot_plan.py`, applied in the orchestrator only when `AGENT_SLOT_PLAN` is on and the model is not forced, and only to a `multi_entity_deferred` route:

- several utilities, one dataset, one year: one `data_query_records` count per utility, carrying a single named county when the dataset has a county column
- several counties, one dataset, one year: one count per county
- several explicit years, one dataset, at most one utility and one county: one count per year, carrying the utility and the county
- a per-month breakdown with one dataset and one year: one monthly `visualization_create` series, plus a count when the question also asks for a total
- a by-county ask with one dataset and one year: one `data_query_rank` by county, count metric

At most 10 calls. Each call keeps its own evidence id and caveats attach per tool.

## When it falls back instead

The rule returns no plan, and the deferral stands, whenever a plan would answer a narrower question than the one asked:

- a single named county on a dataset with no county column
- a map or a where question, which the slot rule cannot build
- a sub-year window (a month name, a date range inside a year) across several years, because per-year counts would drop the window
- a monthly or per-period breakdown together with several utilities or counties
- a metric other than a count: acres, customers, a rate per circuit or per area
- a question whose dataset the router did not resolve

`tests/agent/test_slot_plan.py` holds the reviewer questions for each of these.

## Label rule D

`label_rules.plans_equivalent`: a comparison and the matching per-entity counts return the same numbers, so either plan matches the gold plan. Gold plans live in `gold_plan` on the eval rows.
