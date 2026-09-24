# Multi-entity questions: the slot planner

The router defers a question that names several utilities, several counties, several explicit years, or a per-period breakdown as `multi_entity_deferred`, because one deterministic call would drop an entity or flatten the breakdown. `AGENT_SLOT_PLAN` (default off) lets code turn some of those deferrals into several deterministic calls built only from router slots. Jev is not called.

Jev's own planner (`AGENT_JEV_MODE=plan`, the breakdown, output_form, and also_chart questions, `planner.py`) lost to the slot rule on the seen holdouts and had known paths that answered a narrower question. It is kept on the `jev-plan-archive` branch and is not on main.

With `AGENT_JEV_MODE=decide` also on, decide runs first on the router's own decision, and the slot planner then acts only on a question decide left as an answer; a Jev clarification or refusal is never planned (`docs/JEV_DECIDE.md`).

## What the slot rule plans

`services/agent/eval/slot_plan.py`, applied in the orchestrator only when `AGENT_SLOT_PLAN` is on and the model is not forced, and only to a `multi_entity_deferred` route. It builds candidate calls from router slots:

- counts: one `data_query_records` count for every combination of the named utilities, the named counties, and the separately named years, each call carrying the resolved date window (including a month or other sub-year window)
- a per-month breakdown: one monthly `visualization_create` series over the resolved window, plus a count when the question also asks for a total
- a by-county ask: one `data_query_rank` by county, count metric, over the resolved window

At most 10 calls. Each call keeps its own evidence id and caveats attach per tool.

## The invariant, and when it falls back

A candidate plan stands only if every slot and constraint the router resolved is represented in the planned calls (`_unrepresented` in `slot_plan.py`). Otherwise the rule returns no plan and the deferral stands, so a plan never answers a narrower question than the one asked. The checks:

- dataset: every call reads the resolved dataset
- each utility and each county: every call carries one of them, together the calls cover all of them, and the dataset can filter on it (from `allowed_filters` in the dataset registry; US ignitions and PSPS have no county filter)
- date window: every call's window equals the resolved window, or, for separately named years, the calls cover exactly those years with full-year windows. A month named in the question must fall inside the window, a month window across several years falls back, and a part of a year the router does not resolve (a quarter, a half, a season) falls back
- output form: map or where wording needs a map call, list or records wording needs record calls, series wording (monthly, trend, chart) needs a series call and a series needs that wording, and a by-county ask needs the rank
- measure: acres, customers, or a rate cannot be carried by a count, so they fall back
- US sample: US-sample wording (sampled, all causes, national) must resolve to `us_ignitions`; the router sometimes resolves it to `cpuc_ignitions`, and then the plan falls back. The US sample has no state filter, so a US-sample question restricted to a state (California) also falls back (label rule H)
- EPSS: an EPSS call for a utility other than PG&E falls back (`epss_non_pge_utility`, label rule I), because EPSS is PG&E-only and that count would read as zero when the data is absent

`fallback_reason(question)` returns the first check a plan would fail. `tests/agent/test_slot_plan_fallbacks.py` holds the reviewer questions, the earlier fallback cases, and probes for each check; `tests/agent/test_slot_plan.py` covers the plans that stand.

## Label rule D

`label_rules.plans_equivalent`: a comparison and the matching per-entity counts return the same numbers, so either plan matches the gold plan. Gold plans live in `gold_plan` on the eval rows.
