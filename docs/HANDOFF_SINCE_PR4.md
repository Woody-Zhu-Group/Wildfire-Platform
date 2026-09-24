# Handoff: everything since PR #4

Written 2026-09-23 and revised 2026-09-24 for Stephen, who built the analysis workspace in PR #4 and has not followed the work since. It assumes you know your own workspace code (`website/src/`) and nothing else. Plain language comes first in each section, then the technical detail with the file (and function where useful) that backs each claim.

Every statement about code was checked against `main` at `41e83d5` (the merge of PR #79) on the team repo `Woody-Zhu-Group/Wildfire-Platform`. Every statement about history comes from the GitHub PR and issue list or `git log`. Facts that only Michael can see (the production host, the grants, the tooling) are marked as reported by Michael in section 13. An unmerged PR is never described as done; PR #29 is the only open PR besides this document's own PR #74, and it is marked open wherever it appears.

Sections:

1. One-page summary
2. PR timeline
3. Architecture now
4. Every question type
5. Datasets and data changes
6. Services and endpoints
7. The website
8. The harness
9. Jev
10. The slot planner
11. Evaluation
12. Production and operations
13. Work done outside the repo
14. How we work
15. Documents
16. Open work
17. Glossary

---

## 1. One-page summary

**What the product can do now that it could not at PR #4.**

- The chat (Ask) can open every one of the 18 workspace views from an answer, not just a few. Five views are new since PR #4: the modeled ignition risk surface, the model residual map, cumulative acres burned, PSPS customers affected over time, and the EPSS medical-baseline exposure card (`website/src/panelViews.ts`, PRs #13 to #17, #26).
- The workspace has a global year bar with per-panel pins, day-by-day playback on event maps, a light theme, and dataset caveats on every card and CSV (PRs #7, #8, #19, #20).
- The agent no longer gives silent partial or wrong answers. Questions that name several utilities, counties, or years are not collapsed into one call; a county or month the tool cannot apply stops the answer instead of being dropped; live, future, advice, city, and HFTD-tier questions get a clarification or refusal that says why (PR #22 and follow-ups #48, #50, #51, #58, #66, #68).
- City questions that a single point can answer (which utility territory or HFTD tier a city is in, fitted risk near a city on a past day) are answered at the city's Census center point with caveats, instead of asking for coordinates (PR #28).
- The HFTD tier and IOU territory polygons were rebuilt from CPUC's own sources with holes kept, so counts inside a tier or territory are right and PSPS-in-HFTD comparisons work again (PR #45, #69). The corrected geometry changed HFTD Tier 2 counts by 8 to 18 percent per year.
- The risk service serves the full 824-cell hindcast surface, per-cell observed counts, and model metrics regenerated from the committed fit (PRs #9 to #12, #27, #70).
- A second decision engine, Jev (TypeSafe's non-generative decision model), sits beside the regex router. It can log its decisions (shadow), pick the tool on the model path, or decide answer / clarify / refuse ahead of the router with the router as a backstop (PRs #22, #43, #46, #49). In decide mode Jev owns the disposition and the router owns the wording (PR #72), and every answer says who decided (`decision_source`, PR #71).
- The agent's language model runs only on OpenRouter (GPT-6 Luna, GPT-6 Sol fallback) with native tool calls, strict schemas, filter grounding, and multi-part coverage (PR #25). The local Ollama/qwen path and the GPU control service were removed from the codebase (PR #73).
- County, utility, and HFTD tier filters are normalized everywhere, and an unknown county is an error with close matches, never a zero (PR #76). Enum filters the question never asked for (`utility=untagged`, CAL FIRE `incident_type_mode`) are dropped (PR #79).
- A deterministic slot planner can turn a deferred multi-entity question into several exact calls without a model (PR #46), off by default.
- The published site was stale from 2026-09-18 to 2026-09-24 (two merged frontend PRs never committed their build); it is rebuilt, and a build-freshness test plus a `CLAUDE.md` rule stop it recurring (PR #75).
- A research pilot turned 155 CPUC PSPS post-event reports into a structured, sourced table with a review queue (PR #29, open, not merged).

**What changed for users.** Answers are slower to guess and quicker to ask. A missing year, place, or dataset gets one clarification that names everything missing and gives an example rephrasing (PR #51). Refusals say what is out of scope. Answers carry the same caveats the cards do. The Ask panel's Tool chain shows one line saying who decided ("Decided by Jev (0.93)", "Safety rule: live data", "Router (Jev agreed)"). On the site, the year bar drives every panel unless a panel is pinned, and answers from Ask arrive as pinned panels.

**What is live in production (deployed 2026-09-24, reported by Michael, section 13).** The EC2 backend runs current `main`; the HFTD and IOU boundaries were reloaded from CPUC sources; the agent's language model is GPT-6 Luna on OpenRouter (about 4 seconds per model-path answer instead of 3 to 12 minutes); Jev runs in decide mode through OpenRouter, owning the answer / clarify / refuse decision behind the two gates with the router as backstop; qwen, Ollama, and GPU control are gone from both the server and the code.

**Built but switched off.** Jev tool pick (`tool_pick`, `tool_pick_template`) and the slot planner (`AGENT_SLOT_PLAN`) are on `main` and default off (`services/agent/config.py`, `from_env`). Decide mode also defaults off in the code and is switched on only by the production `.env`; switching production back to shadow is one line and a restart (section 9.8). Jev's own plan mode was archived on the `jev-plan-archive` branch and is rejected at startup.

---

## 2. PR timeline

Every PR on `Woody-Zhu-Group/Wildfire-Platform` from #1 to #79, grouped by theme. Numbers not listed (#30 to #42, #44, #47, #52 to #56, #60 to #65, #67, #77, #78) are issues, covered in section 16. After PR #4 every commit on `main` arrived through a PR; the last direct commit to `main` was `615a375` on 2026-08-28, before PR #3 (`git log platform/main --first-parent`). PR #1 and #2 are the only closed unmerged PRs. PR #74 is this document.

### The workspace (Stephen)

| PR | Author | Status | What it did |
|---|---|---|---|
| #1 | StDoses72 | Closed unmerged 2026-09-16 | The first React/TypeScript workspace with 13 views. Closed with the comment "Superseded by #2". |
| #2 | StDoses72 | Closed unmerged 2026-09-17 | Continued #1 with review fixes and opt-in SQL aggregates. Superseded by #4, which brought this workspace onto the backend merged in #3 (PR #4 description). |
| #3 | ByteMasterMike | Merged 2026-09-16 | Added `/grouped-counts`, `/summary`, and `/regional-series` to Data Query for the workspace client. |
| #4 | StDoses72 | Merged 2026-09-18 | Integrated the workspace with the PR #3 aggregates, disabled unsupported filters, and connected the build to the HTTPS Data Query route. This is the baseline for this document. |

### Website panels and workspace features

| PR | Author | Status | What it did |
|---|---|---|---|
| #5 | ByteMasterMike | Merged 2026-09-18 | Consolidated per-dataset metadata into `services/shared/dataset_registry.py`. |
| #6 | ByteMasterMike | Merged 2026-09-18 | Generated the website dataset catalog and caveat JSON from that registry (`scripts/generate_frontend_registry.py`). |
| #7 | ByteMasterMike | Merged 2026-09-18 | Added the light theme toggle (`website/src/ThemeToggle.tsx`, `theme.ts`). |
| #8 | ByteMasterMike | Merged 2026-09-22 | Showed the EPSS and US ignitions caveats on cards and CSV headers. |
| #13 | ByteMasterMike | Merged 2026-09-18 | Added the EPSS medical exposure stat card (`exposure.ts`). |
| #14 | ByteMasterMike | Merged 2026-09-18 | Added cumulative CAL FIRE acres over a season (`cumulative.ts`). |
| #15 | ByteMasterMike | Merged 2026-09-18 | Added PSPS customer-events over time (`customerEvents.ts`). |
| #16 | ByteMasterMike | Merged 2026-09-18 | Added the modeled ignition risk surface map (`RiskSurfaceMap.tsx`, `GridSurfaceMap.tsx`). |
| #17 | ByteMasterMike | Merged 2026-09-18 | Added the model residual map (`ResidualMap.tsx`, `residual.ts`). |
| #18 | ByteMasterMike | Merged 2026-09-18 | Let the risk and residual grids fade so the basemap stays readable. |
| #19 | ByteMasterMike | Merged 2026-09-18 | Added the workspace year bar with per-panel pins (`globalFilters.ts`, `WorkspaceFilters.tsx`, `PanelYearPin.tsx`). |
| #20 | ByteMasterMike | Merged 2026-09-18 | Added day-by-day playback to event maps (`playback.ts`, `PlaybackControls.tsx`). |
| #26 | ByteMasterMike | Merged 2026-09-23 | Made every one of the 18 views reachable from a chat answer, and added the router-only statewide risk surface. |
| #75 | ByteMasterMike | Merged 2026-09-24 | Rebuilt the published site in `docs/`, which had been stale since 2026-09-18, and added the build-freshness check (`website/scripts/check-build.mjs`) and the `CLAUDE.md` rule. |

### Risk service

| PR | Author | Status | What it did |
|---|---|---|---|
| #9 | ByteMasterMike | Merged 2026-09-18 | Added `GET /surface`, the full 824-cell hindcast for one date. |
| #10 | ByteMasterMike | Merged 2026-09-18 | Added `GET /observed`, per-cell CPUC ignition counts by polygon containment. |
| #11 | ByteMasterMike | Merged 2026-09-18 | Added `GET /observed-training`, the same counts using the training cell assignment. |
| #12 | ByteMasterMike | Merged 2026-09-18 | Added `GET /metrics` for the model performance panel. |
| #27 | ByteMasterMike | Merged 2026-09-24 | Made risk `/health` return 503 unless a startup trial prediction ran (`readiness.py`). |
| #70 | ByteMasterMike | Merged 2026-09-24 | Regenerated the metrics table from the committed fit, made `/metrics` refuse a stale table, isolated plotting dependencies, and dropped the unused `NullBackend`. |

### Router, dates, and harness

| PR | Author | Status | What it did |
|---|---|---|---|
| #21 | ByteMasterMike | Merged 2026-09-21 | Rebased the small-model harness commits onto main (year-not-derived retries, the `qwen2.5:7b` default, GPU control with no baked-in resource). |
| #22 | ByteMasterMike | Merged 2026-09-23 | The big router fix: no partial or silently wrong answers, plus the whole Jev shadow layer with every mode off. |
| #48 | ByteMasterMike | Merged 2026-09-23 | Open ranges ("up to today", "since 2020") end at today; apostrophe years use the `%y` pivot, so `'99` is 1999. |
| #50 | ByteMasterMike | Merged 2026-09-23 | Model tool calls cannot narrow a resolved date range; bare 1900s years clarify; per-year questions keep per-year calls. |
| #51 | ByteMasterMike | Merged 2026-09-23 | One clarification asks for every missing item and ends with an example rephrasing (`clarify_missing.py`). |
| #58 | ByteMasterMike | Merged 2026-09-24 | Advice rule scope, HFTD-tier rankings, bare county words, the dead service-area regex, and the route snapshot fixture. |
| #66 | ByteMasterMike | Merged 2026-09-24 | "From March to June 2023" resolves to the full window, not March only. |
| #68 | ByteMasterMike | Merged 2026-09-24 | US-sample wording routes to `us_ignitions`; a US-sample question restricted to a state clarifies. |
| #28 | ByteMasterMike | Merged 2026-09-24 | City questions answered at the Census center point, with the shoreline snap (`places.py`). |
| #76 | ByteMasterMike | Merged 2026-09-24 | Normalized county, utility, and tier filters in every service (`services/shared/counties.py`, `services/data_query/filters.py`); an unknown county is a 400 with close matches, never 0 rows. |
| #79 | ByteMasterMike | Merged 2026-09-24 | Dropped schema-valid enum filters the question never asked for (`utility=untagged`, CAL FIRE `incident_type_mode`), and accepted the Sacramento count-plus-records tool sequence in the eval. |

### Jev and the model provider

| PR | Author | Status | What it did |
|---|---|---|---|
| #43 | ByteMasterMike | Merged 2026-09-23 | Jev `tool_pick` and `tool_pick_template` modes, count-plus-trend routed in code, holdout v1, label rules A to C, and the payload fix that sends the live tool-pick call the same glossary as the offline eval. |
| #25 | ByteMasterMike | Merged 2026-09-23 | OpenRouter backends for the language model (Luna with Sol fallback) and for Jev, filter grounding, multi-part coverage, cost logging. |
| #46 | ByteMasterMike | Merged 2026-09-24 | The deterministic slot planner (`AGENT_SLOT_PLAN`), holdouts v2 and v3 on main, label rules D and H, the payload pin test. Jev's plan mode removed and archived. |
| #49 | ByteMasterMike | Merged 2026-09-24 | `AGENT_JEV_MODE=decide`: router backstops first, then Jev's disposition behind two gates. |
| #71 | ByteMasterMike | Merged 2026-09-24 | `decision_source` on every answer and routing event, and one Tool chain line on the website saying who decided (`decisions/provenance.py`). |
| #72 | ByteMasterMike | Merged 2026-09-24 | Decide mode: Jev owns the disposition, the router owns the wording; agreed answers log Jev's confidence. |
| #73 | ByteMasterMike | Merged 2026-09-24 | Removed the Ollama/qwen path, its eval scripts, and the GPU control service; OpenRouter is the only provider; smoke test updated. |

### Data

| PR | Author | Status | What it did |
|---|---|---|---|
| #45 | ByteMasterMike | Merged 2026-09-24 | Rebuilt HFTD and IOU polygons from CPUC FeatureServers with holes kept, behind a validity and area gate (`db/loaders/arcgis_polygons.py`, `load_boundaries.py`). |
| #69 | ByteMasterMike | Merged 2026-09-24 | Geometry follow-ups: a committed test fixture, a page cap, a tolerance for degenerate rings, opt-in `simplify` on boundary downloads, risk paths read after `.env` loads. |

### Operations and documentation

| PR | Author | Status | What it did |
|---|---|---|---|
| #23 | ByteMasterMike | Merged 2026-09-23 | Updated `AGENTS.md` and added `CLAUDE.md` as tracked agent context files. |
| #24 | ByteMasterMike | Merged 2026-09-24 | Added the shadow log report, the EC2 deploy runbook, the smoke test, and the risk systemd unit. |
| #57 | ByteMasterMike | Merged 2026-09-24 | Refreshed every README and doc to match main, and added the rule that docs change in the same PR as behavior. |
| #59 | ByteMasterMike | Merged 2026-09-24 | Ignored the local Claude Code hook settings files. |

### Research

| PR | Author | Status | What it did |
|---|---|---|---|
| #29 | ByteMasterMike | **Open**, not merged | Pilot, clean test, and full run that extract structured facts from 155 CPUC PSPS post-event reports with Jev and GPT-6 Luna (`research/psps_reports/`). See section 5. |
| #74 | ByteMasterMike | **Open** | This document. |

### Branches that were never merged

- `jev-plan-archive` (team repo): Jev's own multi-tool planner (`AGENT_JEV_MODE=plan`, `planner.py`, three plan-only Jev questions), 19 commits ahead of main from merge base `85580b1`. It lost to the slot rule on the seen holdouts and had paths that answered a narrower question than asked (PR #46 description, `docs/JEV_MULTI_TOOL.md`). It also holds the v3 question text that the labels file is indexed against. Section 10.
- `research-psps-reports` (team repo): PR #29, open.

### The old fork (ByteMasterMike/Wildfire-Services)

The early Jev work started on Michael's personal fork before it moved to the team repo. Its branches and one PR (all checked on 2026-09-23):

- `main` at `615a375`: identical to the team repo's history before PR #3. Nothing on it is missing from the team repo.
- `jev-shadow` (10 commits), `panel-summary-stats` (3), `panel-medical-baseline`, `panel-ranking-comparisons`, `panel-series-modes` (2 each): every one of those commit subjects exists on the team repo's `main` under a new SHA, because the work was rebased into PRs #43 and #26. The content diff between the fork's `jev-shadow` and the team `main` is only what later PRs changed on top.
- `router-paraphrase-fixes` at `3837b68`: already an ancestor of the team `main` (PR #22).
- Fork PR #1 (`router-paraphrase-fixes` into `jev-shadow`, "Fix paraphrase router false positives"): still open on the fork, but its content merged into the team repo as PR #22. It can be closed.
- `cursor/cloud-agent-1790037892888-z1y62` (one commit, `1bac30c`): a six-line note in `_context/memory.md` about deleting merged branch names. This is the only fork content that never reached the team repo, and it is a session note, not code.

---

## 3. Architecture now

Plain language: a question from the website goes to the agent. The agent first checks a list of hard stops (live questions, future dates, advice, cities, HFTD operations no tool can do, off-topic subjects). Then, in decide mode (on in production), Jev is asked whether the question can be answered, needs a clarification, or must be refused; the router's own decision stands whenever Jev is unsure, and when both decline the same way the router's wording is what the user reads. The router extracts the year, utility, county, dataset, and dates. If the router recognized the question exactly, it runs the exact tool calls itself. If not, the slot planner may build several exact calls, or the question goes to the language model on OpenRouter, which must choose from a small list of tools and can only cite what the tools returned. Caveats attach to every successful path. The answer, its evidence, who decided, and any views come back to the website, which turns the views into workspace panels only when they cite evidence.

```
website Ask panel (POST /ask/stream, SSE)  ..  website/src/api.ts, useRemote.ts
        |
        v
services/agent/app.py  ask_stream  ->  orchestrator.AgentOrchestrator.ask
        |
        v
[1] route_question (services/agent/routing.py)
    |- hard backstops: UNSUPPORTED topics, live, future, advice, riskiest,
    |  near me, near X, county-word places, unknown county, city, HFTD tier,
    |  region, ambiguous time, out of coverage
    |- slot extraction: utilities, year(s), dataset, coords, county/counties,
    |  time_resolution (services/agent/time_resolve.py)
    |- deterministic rules (exact tool calls) or clarification/refusal,
    |  else path "model" with candidate_tools
    v
[2] AGENT_JEV_MODE=decide (on in prod since 2026-09-24): decisions/decide_mode.py
    |  backstops and regex-only rules stay with the router; otherwise
    |  Jev facts -> jev_policy.derive_outcome -> gates 0.8 (decline) / 0.9 (answer)
    |  Jev owns the disposition, the router owns the wording (PR #72)
    v
[3] AGENT_SLOT_PLAN (off in prod): eval/slot_plan.apply_slot_plan
    |  multi_entity_deferred -> several deterministic calls, or stands
    v
[4] AGENT_JEV_MODE=shadow (the mode before decide; one mode at a time):
    |  decisions/shadow.py logs Jev beside the router; never changes the answer
    v
[5a] deterministic path            [5b] model path (orchestrator._model_loop)
     tool calls from the router          provider.OpenAICompatibleProvider:
     via tools.ToolExecutor              OpenRouter only (PR #73): Luna,
     (harness_call=True)                 tool_choice required, Sol after a
                                          failed turn
                                          guards: grounding.ground_model_filters
                                          (incl. untagged and incident_type_mode),
                                          time_resolve.apply_harness_years,
                                          tools._strip_ungrounded_utilities,
                                          county normalization (PR #76),
                                          strip_harness_only_arguments,
                                          schema retry bound, max_tool_steps,
                                          uncovered_entities (no partial answer)
        \                                /
         v                              v
[6] caveats.collect_qualifications (companion calls, suppress on failure)
[7] synthesis (model path) with claims[].evidence_ids, quantity checks,
    citation repair, harness-derived arithmetic (derived.py);
    deterministic renderer otherwise (_render_deterministic)
[8] views.plan_views -> ComponentSpec[] with evidence_ids, ground_views
[9] decisions/provenance.decision_source: backstop | jev | router, with why
        |
        v
AskResponse: answer_text, status, decision_source, route, evidence,
qualifications, views, view_status, view_scope, trajectory
        |
        v
website/src/answerPanels.ts panelsFromAnswer: a view becomes a panel only if
it cites evidence and maps to a known dataset and date window; otherwise
unsupportedViewNotice. ToolTrace.tsx shows decisionSourceLabel.
```

Stage by stage:

1. **Router backstops and slots** (`services/agent/routing.py`, `route_question` and `_route_question`). The order is fixed in code: the `UNSUPPORTED` keyword table, then live wording (`_asks_live`), future phrases (`_future_refusal_phrase`, split into `risk_future_date` when a risk word is present and `unsupported_future_prediction` otherwise), advice (`_asks_for_advice`), the riskiest phrase, the city point plan (`_city_point_plan`), near me, near X, county-word places (`_unresolved_county_place`), a municipality written as a county, a city that is not a place, the HFTD constraint, northern or southern California, then ambiguous and out-of-coverage time. Slots are built once and passed on every `RouteDecision`. Every deterministic rule is guarded by `_block_unexpressed_constraints`, which refuses to answer when a named county or month cannot be applied by the matched call.
2. **Jev decide** (`services/agent/decisions/decide_mode.py`, wired in `AgentOrchestrator.ask`). Off by default in the code; on in production since 2026-09-24. Section 9 has the full policy, the wording rule, and what the logs record.
3. **Slot planner** (`services/agent/eval/slot_plan.py`, `apply_slot_plan`). Off by default and off in production. Runs after decide, only on `multi_entity_deferred`. Section 10.
4. **Jev shadow** (`services/agent/decisions/shadow.py`, `ShadowRunner`). The mode production ran on 2026-09-24 before decide was switched on; `AGENT_JEV_MODE` holds one value, so shadow and decide do not run together. Background thread pool; the user request never waits (`docs/JEV_SHADOW.md`).
5. **Tools** (`services/agent/tools.py`, `ToolExecutor.execute`; models in `services/agent/schemas.py`). Seven model-facing tools plus the router-only `risk_surface`. The deterministic path passes `harness_call=True`, which exempts the router's own calls from the window hold and lets them send harness-only arguments. County arguments are normalized to the canonical Census name before any call (`harness_county_correction`, PR #76). The model path is `AgentOrchestrator._model_loop` with the OpenRouter-only provider in `services/agent/provider.py` (PR #73). Section 8 lists the guards.
6. **Caveats** (`services/agent/caveats.py`, `collect_qualifications`). One attachment point after any successful tool path. Companion calls (the attribute versus spatial pair, CAL FIRE metadata, the CPUC versus US sample read) run here, and a failed companion suppresses the answer.
7. **Answer text.** Deterministic routes render from tool summaries (`orchestrator._render_deterministic`). The model path synthesizes with a strict schema whose claims cite `evidence_ids`; uncited numbers are rejected and, when the numbers are fine but citations are missing, repaired (`citation_repaired`). If synthesis fails, the tool summary is rendered (`synthesis_fallback_to_tool_summary`). When the question asks for a change, difference, percent change, or ratio, `derived.derive_arithmetic` adds a `harness_arithmetic` evidence item before synthesis with every value computed from the cited counts and their `source_evidence_ids`; the model cites it and never computes (`derived_evidence` trajectory event).
8. **Views** (`services/agent/views.py`, `plan_views`, `ground_views`). The harness plans views from successful primary tool results; the model never emits render code and there is no `render_view` tool. Every spec has at least one `evidence_id` (`ComponentSpec`, `min_length=1`) and its parameters must match the cited execution. Every count stat card is kept; only non-count stat cards are capped at three (`_cap_stats`), because the website has no utility-by-year comparison to fold several counts into.
9. **Who decided** (`services/agent/decisions/provenance.py`, `decision_source`, PR #71). Computed once on the final route: `backstop` with the rule id, `jev` with the disposition and confidence when Jev won, or `router` with a `why` (`jev_agreed`, `jev_below_gate`, `jev_error`, `jev_timeout`, `verified_fact`, `router_only_route`, `jev_off`, `jev_shadow`, `jev_tool_pick`, `jev_skipped`), always with the mode. It never carries Jev's raw payload.
10. **Website** (`website/src/answerPanels.ts`, `panelsFromAnswer`; `website/src/agentTrace.ts`, `decisionSourceLabel`). Section 7.

The SSE stream carries harness events only, never model prose (`services/agent/streaming.py`): `routing`, `tool_call`, `tool_result`, `synthesizing`, `answer`, `error`, and the trajectory records `filter_dropped`, `year_not_derived`, `schema_retry_bound`, `uncovered_entities_stop`, `synthesis_fallback_to_tool_summary`, `citation_repaired`, `derived_evidence` (grep of `orchestrator.py`).

---

## 4. Every question type

Plain language: the router has about eighty named outcomes. Each has a rule id that appears in the response (`route.rule`), the logs, and the eval files. Below, every rule id the router can emit, grouped by what the user gets: an answer, a clarifying question, or a refusal. The rule ids were extracted from `services/agent/routing.py` by walking every `RouteDecision(...)` call (the same method `services/agent/decisions/mapping.py` uses in `routing_rule_ids`), plus the dynamic ids `series_<mode>` and `unsupported_<key>`. Example questions come from the eval files or were routed offline with `route_question` on 2026-09-23.

Rule ids that live in tables outside `routing.py`:

- `REGEX_ONLY` in `services/agent/decisions/jev_policy.py`: 16 rules Jev is never asked to reproduce because they are tool-schema or gazetteer facts, not facts about the wording (`unexpressed_filter_constraints`, `city_needs_place`, `unknown_county`, `county_place_ambiguous`, `hftd_constraint_unavailable`, the six keyword topics `unsupported_air_quality`, `unsupported_evacuation`, `unsupported_translation`, `unsupported_personnel`, `unsupported_satellite`, `unsupported_leadership`, `unsupported_future_prediction`, `medical_exposure_missing_year`, `series_mode_missing_year`, `series_mode_missing_dataset`).
- `CONTEXT_DEFERRED_RULES` in `services/agent/decisions/schemas.py`: two router rules deliberately not yet described to Jev, because a new policy sentence changes every Jev payload hash (`county_place_ambiguous`, `unsupported_future_prediction`).
- `RULE_TO_INTENT` in `services/agent/decisions/mapping.py`: every rule id mapped to one coarse intent for Jev scoring. It also names the two orchestrator-level ids `deterministic_router_disabled` and `slot_plan`.
- `BACKSTOP_RULES` in `services/agent/decisions/decide_mode.py`: the five rules plus the eleven `unsupported_<key>` topics that decide mode never sends to Jev.

### 4.1 Answers

**Deterministic answers.** The router builds the exact tool call. `_block_unexpressed_constraints` can still turn any of these into `unexpressed_filter_constraints` or `unexpressable_county_filter`.

| Rule id | Triggers | Example | Tool call |
|---|---|---|---|
| `filtered_records` | A count or list verb, a dataset, and a year or date range | How many CAL FIRE wildfire incidents were there in Sacramento County in August 2023? | `data_query_records` count (or records with `limit` 25) |
| `ranked_records` | A ranking ask in one dataset with an allowed grouping and a year (`_route_ranking`) | Which counties had the most CAL FIRE incidents in 2023? | `data_query_rank` |
| `map` | A map word, a dataset, and a year (HFTD needs no year) | Map EPSS outages for 2024. | `visualization_create` map |
| `hdw_map` | HDW or fire weather wording on one California event layer, one year in 2020 to 2025 | Map CPUC ignitions with HDW for 2024. | `visualization_create` map with `show_hdw` |
| `map_plus_trend` | A map word and a trend word with a dataset and year | Map CAL FIRE incidents for 2024 and show the monthly trend. | map then time series |
| `time_series` | trend, time series, weekly, monthly, or daily, plus a dataset and year | Show the monthly CPUC ignition trend for 2024. | `visualization_create` time_series |
| `series_timeline` | Two or more of CPUC, EPSS, CAL FIRE named with "over time" and a year (`_timeline_datasets`) | Show ignitions and EPSS outages over time in 2024. | one series per dataset |
| `series_yearly`, `series_seasonal`, `series_cumulative_acres`, `series_customer_events`, `series_regional` | The panel phrases in `_series_mode_request` (year over year, seasonal, cumulative acres, customer events, EPSS by division) with a year | Show EPSS outages by division in 2024. | one monthly series, stamped with `series_mode` so the website opens that panel |
| `summary_stats` | summary or overview wording with a dataset and year (`_asks_summary_panel`) | summary of EPSS outages in 2024 | `data_query_records` count, `stat_mode` summary |
| `medical_exposure` | medical baseline, life support, or medically vulnerable wording with a year (`_asks_medical_exposure`) | life support customers in 2024 | `data_query_records` count on EPSS, `stat_mode` medical_exposure |
| `multi_intent_count_and_trend` | A count and a trend clause with a dataset and year | Give me the PGE ignition count and its monthly trend for 2024. | count then series (PR #43); without a dataset or year it defers to the model under the same rule id |
| `spatial_utility_count` | A count inside one utility's territory with a year (`_asks_spatial_containment`) | How many ignitions happened inside SCE's territory in 2023? | `data_query_spatial` summary |
| `coordinate_context` | Explicit coordinates plus which, what, IOU, HFTD, tier, grid, or cell | Which IOU, HFTD tier, and grid cell contain 38.58,-121.49? | `data_query_spatial` point |
| `coordinate_risk_chain` | Coordinates, a risk word, and one past day | At 38.58,-121.49, what was fitted risk on 2024-08-15? | point read, then `risk_forecast` on `$grid_cell_id` |
| `city_point_context` | Which territory, tier, or grid cell contains a city (`_city_point_plan`, PR #28) | Is the city of Chico inside a Tier 2 or Tier 3 High Fire Threat District? | `data_query_spatial` point at the Gazetteer center with `snap_shoreline` |
| `city_point_risk_chain` | Risk in, at, near, or around a city on one past day | what was the modeled ignition risk around Chico on september 2, 2020? | point read, then `risk_forecast` |
| `cell_risk` | A risk word, cell N, and one past day | Predict historical ignition risk for cell 400 on 2024-08-15. | `risk_forecast` cell |
| `county_risk` | A risk word, one county, one past day | What was fitted ignition risk in Sacramento County on 2024-08-15? | `risk_forecast` county |
| `utility_risk` | A risk word, one utility, one past day | What was PGE fitted ignition risk on 2024-08-15? | `risk_forecast` utility |
| `risk_surface` | A risk or residual grid map with a date and no place (`_risk_map_mode`, PR #26) | Show the risk surface for 2024-08-15. | `risk_surface` (router-only, `GET /surface`) |
| `utility_territory` | The service-area outline, boundary, polygon, or footprint of one utility with no count or dataset word (`_asks_territory_boundary`, label rule G) | Show the SCE utility territory boundary | `visualization_inspect` utility_territory |
| `circuit_detail` | A 9-digit circuit id with detail, outage, or circuit | Show detail for circuit 043371102 in 2024. | `visualization_inspect` event_detail |
| `period_comparison` | compare or versus, one utility, two calendar years | Compare PGE ignitions in 2023 versus 2024 | `comparison_run` periods |
| `utility_comparison` | compare or versus, two or more utilities, one year | Compare ignition counts for PGE versus SCE in 2024 | `comparison_run` utilities |
| `hftd_comparison` | compare or versus, both tiers, one year | Compare Tier 2 and Tier 3 ignition counts in 2024. | `comparison_run` regions |

**Model-path answers.** The router hands the question to the language model with a candidate tool list (`candidate_tools`). The harness guards in section 8 apply.

| Rule id | Triggers | Example | Outcome |
|---|---|---|---|
| `open_ended` | Nothing above matched | Tell me about CPUC ignitions in 2023 | Model picks from up to three candidate tools; synthesis writes prose |
| `open_comparison` | compare or versus that is not one fully specified comparison | Compare CPUC and US ignition counts in 2024 and explain the difference. | Model path; the CPUC versus US companion read is fetched by the caveat engine |
| `multi_entity_deferred` | Several utilities, several counties, enumerated years, a per-period breakdown, or a series plus a total (`_single_call_would_collapse`, `_defer_collapsed`) | How many CAL FIRE incidents were there in Napa and Sonoma County in 2020? | Model path; with `AGENT_SLOT_PLAN` on, the slot planner may rewrite it to `slot_plan` |
| `multi_intent_count_and_trend` (model form) | Count plus trend where the dataset or time is missing or not chartable | (only from eval `force_model` today) | Model path |
| `multi_intent_territory_and_map` | territory plus map or layer plus an event dataset | Show the SCE territory map with the 2023 ignitions layer. | Model path |
| `forced_eval` | Eval cases with `force_model` | (eval only) | Model path |
| `deterministic_router_disabled` | `AGENT_DISABLE_DETERMINISTIC_ROUTING` experiment flag (orchestrator rewrite) | (experiment only) | Model path with the bypassed calls recorded |
| `slot_plan` | `AGENT_SLOT_PLAN` rewrote a `multi_entity_deferred` route (`slot_plan.apply_slot_plan`) | How many PGE, SCE, and SDGE ignitions were there in 2022? | Deterministic path with the planned calls |
| `jev_decide_answer` | Decide mode: Jev answered over a router decline at or above the answer gate (`decide_mode.decide_from_answers`) | (none on dev or v1; see section 9) | Model path |

### 4.2 Clarifications

The text of every clarification is completed by `services/agent/clarify_missing.py` (`complete_clarification`, PR #51): when the slots show more than one thing missing, one message asks for all of them and ends with an example rephrasing built only from what the question named. The rule id never changes.

| Rule id | Triggers | Example | What it asks |
|---|---|---|---|
| `records_missing_year` | A count, list, or summary with a dataset and no time | What was the total number of PSPS events for PG&E? | What year or date range should I use? |
| `map_missing_year` | A map with no time, or an HDW map with no year or more than one year | Show me the map of PG&E outages | What year or date range should I map? |
| `trend_missing_year` | A trend or a multi-dataset timeline with no year | Trend of CPUC ignitions and EPSS outages | What year should I chart? |
| `map_plus_trend_missing_year` | Map plus trend with no year | Map CAL FIRE incidents and show the monthly trend. | What year should I map and chart? |
| `spatial_missing_year` | A count inside a territory with no time | How many ignitions happened inside SCE's territory? | What year or date range should I use? |
| `ranking_missing_year` | An allowed ranking with no time | which distribution circuits had the most epss events? | What year or date range should I use? |
| `ranking_missing_slots` | A ranking with no dataset or no grouping | what counties had the most utility-caused ignitions? | Which dataset and grouping should I rank? |
| `ranking_county_contradiction` | Rank counties while filtering to one named county | For Mendocino County in 2020, which counties had the most CPUC ignitions? | Rank counties statewide, or count the one county? |
| `medical_exposure_missing_year` | Medical baseline wording with no time | show me medical baseline data | What year or date range should I use? |
| `series_mode_missing_year` | A series panel phrase with no time | Show the cumulative acres chart | What year or date range should I use? |
| `series_mode_missing_dataset` | Yearly or seasonal with no CPUC, EPSS, or CAL FIRE dataset | Show a seasonal chart for 2023 | Which dataset should I chart? |
| `ambiguous_relative_time` | recent, lately, currently, or a month range that crosses a year (`resolve_time` status `ambiguous`) | What were recent ignitions for SCE? | Which calendar year or exact date range should I use? |
| `time_out_of_coverage` | A year outside 2014 to the current year, an apostrophe or bare 1900s year, or an HDW year outside 2020 to 2025 | How many PGE ignitions were there in '99? | That period is outside the warehouse |
| `missing_location` | near me with no coordinates | Show recent fires near me. | What latitude/longitude or bounding box? |
| `undefined_spatial_scope` | near, around, or close to a place with no radius, coordinates, or city point | Show me fires near Sacramento in 2024 | Provide a radius or a county or utility polygon |
| `undefined_region` | northern or southern California | Compare wildfire incidents in northern and southern California. | How should the region be defined? |
| `city_needs_place` | A city (458-name list) or a county-word census place asked for something a point cannot answer: a count, list, map, radius, part of the city | How many ignitions were there in Kings Beach in 2023? | Which coordinates, county, or utility territory? |
| `unknown_county` | A municipality written as a county (`_city_named_as_county`) | How many ignitions were there in Coronado County in 2022? | Which county should I use? |
| `county_place_ambiguous` | A county word used as a different place, or a cue-required county word without "County" (`_unresolved_county_place`, PR #58) | How many CAL FIRE incidents were there in Napa Valley in 2020? | Did you mean Napa County, or another county? |
| `hftd_constraint_unavailable` | Circuits intersected with a tier, HFTD acreage or area, or an allowed ranking restricted to a tier (`_hftd_constraint_unavailable`, `_route_ranking`) | Rank EPSS circuits in Tier 3 by outages in 2023 | Rank statewide, or map one HFTD tier? |
| `ambiguous_risk_metric` | riskiest, most risky, highest risk | Which utility is riskiest? | Which risk measure and period? |
| `ambiguous_risk_place` | A risk word with a county and a utility both named | What was the fitted ignition risk in Sacramento County and in PGE territory on 2024-08-15? | Score the county or the utility territory? |
| `risk_missing_place` | A risk word with no cell, county, utility, coordinates, city, or grid-map word | How risky was it? | Which place should I score? |
| `forecast_missing_date` | A risk place with no single past day (`_risk_date_clarification`) | Show the risk surface for Sacramento County | Which past date through 2025-12-31? |
| `risk_future_date` | A risk word with tomorrow, next summer, this week, or a date after 2025-12-31 (`_FORWARD_RELATIVE`, `_date_after_risk_coverage`) | What will the ignition risk be in Butte County next summer? | The model scores historical dates only; which past date? |
| `unexpressed_filter_constraints` | A deterministic match whose call cannot carry a named county or month, when the dataset is not the US sample (`_block_unexpressed_constraints`) | Map HFTD Tier 2 in Sacramento County for 2024 | Switch dataset, narrow the window another way, or drop the filter? |
| `unexpressable_county_filter` (clarification form) | A US-sample question restricted to a state (PR #68, label rules F and H) | How many sampled wildfire ignitions of all causes occurred in California in 2016? | The sample has no state column; offers the national count or a CPUC or CAL FIRE California count |

Decide mode can also return a clarification with a Jev reason id; those ids come from `jev_policy.derive_outcome` and reuse the ids above. Since PR #72, when the router also clarified, the router's rule and text stand and Jev's rule goes to the log only; Jev's own text appears only when Jev changes the disposition (a clarify over a router answer or refusal), and it then goes through the same `complete_clarification` composition with the router's slots.

### 4.3 Refusals

| Rule id | Triggers | Example | Why |
|---|---|---|---|
| `unsupported_cpz` | circuit protection zone wording | Which CPZ had the most ignitions in 2024? | No CPZ polygons in the warehouse |
| `unsupported_cost` | cost, price, budget, dollars, economic, premiums | How much did home insurance premiums rise after the 2024 fires? | No cost data |
| `unsupported_air_quality` | air quality | What was the air quality index during the 2024 Park Fire? | Not in the warehouse |
| `unsupported_evacuation` | evacuat... | What is the best evacuation route out of Paradise? | Not in the warehouse |
| `unsupported_translation` | translate into a language | Translate 'how many fires' into Spanish. | Out of scope |
| `unsupported_personnel` | firefighter counts, personnel | How many firefighters were deployed to the 2024 Park Fire? | Not in the warehouse |
| `unsupported_satellite` | satellite imagery or infrared | Show me the satellite infrared image of the 2024 fire. | No imagery |
| `unsupported_leadership` | CEO, chief executive | Who is the CEO of PG&E? | Out of scope |
| `unsupported_optimization` | optimize, optimal, schedule, allocate, or advice about what a utility or the CPUC should do (`_asks_for_advice`, PR #58) | Should PG&E expand its EPSS program? | Read-only data system, no recommendations |
| `unsupported_damage` | property damage, expected or insured loss, fatalities | What property damage costs were attributed to utility-caused fires by year? | Not in the warehouse |
| `unsupported_live_web` | the `live_web` keyword pattern, right now, today, current, or live wording with no explicit past year (`_asks_live`) | What active fires are burning right now? | Historical records only |
| `unsupported_future_prediction` | A forward phrase, will, expected, predict, or forecast of events or counts with no risk word (`_future_refusal_phrase`) | How many ignitions will there be in 2030? | The system reports history; it does not predict events |
| `unsupported_ranking` | A ranking by change over time, by cell or division, or any dataset and grouping outside the allowed set | From 2017 to 2021, which counties experienced the biggest increase in CAL FIRE incident totals? | Only CPUC by county or utility, CAL FIRE by county, EPSS by circuit |
| `unsupported_rank_cross_dataset` | A ranking naming two datasets | Which county had the most CAL FIRE incidents and CPUC ignitions in 2023? | Datasets count different things |
| `unsupported_rank_us_state` | Rank by state, or rank the US sample | Which state had the most US ignitions in 2022? | The sample has no state column |
| `unsupported_rank_epss_utility` | Rank utilities by EPSS | Which utility had the most EPSS outages in 2024? | EPSS is PG&E only |
| `unexpressable_county_filter` (refusal form) | A county with a fire or ignition count on a dataset that has no county column, or a US-sample question restricted to a county | How many wildfires in Sacramento last year? | Will not answer with a statewide count that ignores the county |

Decide mode can also refuse with three Jev-only ids: `prompt_injection` (Jev's injection fact above 0.5), `other_off_topic` (Jev's off-topic choice), and `unsupported_other_measure` (a measure the warehouse cannot return, for a count, rank, compare, trend, or list intent). Their text comes from `_REASON_TEXT` and `_GENERIC_UNSUPPORTED` in `decide_mode.py`.

### 4.4 Cross-cutting behaviors

- **Multi-entity deferral.** `_single_call_would_collapse` in `routing.py` defers when a question names more than one utility or county, enumerates years that are not one matched range (`_enumerated_years`), asks per period, uses a breakdown word, asks for a series and a total, or asks for a map with a breakdown. The deferral is the model path (`multi_entity_deferred`) unless the slot planner plans it (section 10).
- **County and city handling.** One county scan feeds both the single `county` slot and the `counties` list, so they always agree (PR #58, issue #42). Several counties never collapse to one. Cities: 458 municipality names in `_CA_CITIES` plus 32 county-word census designated places; a bare county word ("in Trinity") or a non-place phrase ("Napa Valley") clarifies with `county_place_ambiguous`; "Orange County" still answers as that county (`docs/CITY_POINTS.md`). Cities that share a county name (Sacramento, Fresno, Los Angeles, and 22 more) route as counties by design (`data/places/README.md`). Every county value that reaches a service is normalized to the Census name (`services/shared/counties.py`, `normalize_county`, PR #76): "Butte County", "butte", "LA", and "SLO" resolve; an unknown value is a 400 with close matches ("Did you mean Butte or Sutter?"), never an empty result, and the agent shows that suggestion instead of a zero.
- **Date handling** (`services/agent/time_resolve.py`, `resolve_time`): explicit years, calendar days, month plus year, month ranges inside one year (PR #66, `explicit_month_range_in_year`), year ranges, relative years (last year, N years ago), open ranges ending today ("from January 2024 up to today", "since 2020", PR #48, `open_ended_range`), apostrophe years with the `%y` pivot (`'24` is 2024, `'99` is 1999, then out of coverage), bare 1900s years out of coverage (PR #50), vague words ambiguous, and per-year questions marked `per_year` so the harness keeps one call per year. Coverage is 2014 through the current year (`DATA_YEAR_MIN`). A month range that crosses a year ("November to February 2023") is ambiguous and asks for the years.
- **US-sample routing** (PR #68, `_names_us_sample`): "US ignitions", "national sample", "ignition sample", FireCastRL, "sampled ... ignitions", and "all causes" beside "ignitions" resolve to `us_ignitions`, unless CPUC, "utility-caused", or a utility name comes right before "ignitions". A state restriction clarifies; a county restriction refuses.
- **Statewide risk surface** (PR #26): a risk or residual grid map with a date and no place calls `risk_surface`, a tool kept out of the model's tool list and the Jev payloads (`HARNESS_TOOL_MODELS` in `schemas.py`). Decide mode leaves such a route with the router (`decide_mode.router_only_tools`).

---

## 5. Datasets and data changes

### 5.1 Warehouse tables

Plain language: one PostGIS database (`wildfire` schema, `db/schema.sql`) holds the event records and the boundaries. Loaders in `db/loaders/` truncate and reload each table from the read-only `dataset_demo` sources, except the two boundary tables, which now come from CPUC's servers. Table comments in `db/schema.sql` are the source for these descriptions.

| Table | What it holds |
|---|---|
| `wildfire.circuits` | PG&E circuit line geometry from `epss_circuits.geojson`, 822 unique 9-digit TEXT ids with leading zeros, division and substation. |
| `wildfire.epss_outages` | PG&E EPSS fast-trip outage events (point, dates, county, cause, division, customer minutes, medical baseline and life support counts). PG&E only. No FK to circuits; orphans are reported. |
| `wildfire.psps_events` | PSPS de-energization polygons with utility, dates, and customers de-energized. |
| `wildfire.psps_event_circuits` | Circuits associated with each PSPS event. |
| `wildfire.cpuc_ignitions` | The primary CPUC ignition points with a utility tag; `county` is added at load by point-in-polygon against `wildfire.counties`. |
| `wildfire.cpuc_ignitions_with_time` | A secondary CPUC file with time of day and no utility tag; membership differs from the primary by about 180 rows each way, kept separate. |
| `wildfire.calfire_incidents` | CAL FIRE incident-map points with type, acres, county, utility tag, dates. Non-wildfire types are retained and filtered in queries. |
| `wildfire.hftd_tiers` | CPUC HFTD Tier 2 and Tier 3 polygons, rebuilt from Esri JSON with holes kept (PR #45), with audit columns `geom_source`, `publisher_area_m2`, `source_url`, `source_edited_at`. |
| `wildfire.iou_territories` | CPUC IOU service territory polygons (PGE, SCE, SDGE, PacifiCorp, Liberty, BVES), rebuilt the same way. |
| `wildfire.counties` | Census cartographic county polygons, California only. |
| `wildfire.grid_cells` | The 824-cell 0.24 degree risk grid with SW-corner lat/lon, polygon, and centroid. |
| `wildfire.us_ignitions` | The FireCastRL / IRWIN all-cause CONUS sample, about 33,457 positives, 2014 to 2025. Not a census, not comparable to CPUC. |

Dataset metadata (aliases, styles, allowed filters, allowed rank pairs, caveat ids) lives in `services/shared/dataset_registry.py` (PR #5), and the website's dataset catalog and caveat JSON are generated from it (`scripts/generate_frontend_registry.py`, PR #6, `tests/test_frontend_registry_generated.py` fails when they are stale).

### 5.2 The HFTD and IOU rebuild (PR #45, follow-ups in #69)

Plain language: the old boundary files were made for drawing a map, not for counting. They had turned every hole (a town excluded from a tier, a city with its own municipal utility) into an extra filled polygon, and they were simplified in a way that made the geometry invalid. So Tier 2 was 11.4 percent too big, cities like Anaheim counted as inside SCE, and every intersection query against HFTD threw an error, which broke PSPS-in-HFTD comparisons.

What changed (`docs/DATA_CHANGE_HFTD_IOU.md`):

- `db/loaders/arcgis_polygons.py` reads CPUC's FeatureServers as Esri JSON (which keeps ring roles), from a gitignored cache in `data/boundaries/` or from the network with `--refresh`. Holes go to the smallest containing outer ring; degenerate rings stop the load; slivers under 1 m2 are kept (Tier 2 has 4); one PG&E ring over Suisun Marsh is overridden to territory because the publisher's area counts it and it holds 23 EPSS outages (`RingOverride`).
- `db/loaders/load_boundaries.py` replaces both tables in one transaction behind a gate: every geometry valid, EPSG:3310 area within 0.1 percent of the publisher's `Shape__Area`. A failure rolls back both.
- `python -m db.loaders.rebuild_boundaries [--fetch-only | --refresh]` seeds the cache or reloads the two tables. `load_all` keeps the old boundary rows and exits non-zero if the boundaries fail (`tests/test_load_all.py`).
- The visualization map layers draw a simplified copy; every count and containment query uses the full geometry. Data Query's `GET /hftd` and `GET /iou-territories` return full geometry by default with an opt-in `simplify` parameter (PR #69).

Areas after the rebuild match CPUC's to within 0.0001 percent (Tier 2 149,984.71 km2 against 149,984.70; Tier 3 32,326.98; PGE 185,940.74; SCE 135,343.07). Counts that changed, all measured on the local warehouse on 2026-09-23 (`docs/DATA_CHANGE_HFTD_IOU.md`, before and after tables):

- CPUC ignitions in Tier 2: down 8 to 18 percent per year (2024: 125 to 106). Tier 3: down 2 to 10 percent.
- EPSS outages in Tier 2: down 11 to 14 percent (2024: 1,446 to 1,292).
- CAL FIRE incidents in Tier 2: down 5 to 13 percent; acres changed less.
- PSPS events and customers in either tier, and HFTD circuit denominators: previously an error, now values (Tier 2 circuits 618, Tier 3 279).
- IOU territory counts: a few events per region-year at most. PGE 2024 CPUC ignitions stay 532 by attribute and 536 by spatial containment, so that gap is real.
- 30 city center-point answers changed (12 for IOU, 18 for HFTD): Anaheim, Azusa, Banning, Colton, Riverside are no longer SCE (they are holes); Redding, Ukiah, Grass Valley are no longer Tier 2; Placerville is Tier 2 not Tier 3. `tests/test_city_point_answers.py` pins them.

The doc's "When the corrected data applies" section records 2026-09-24 as the EC2 date (PR #73); its status line at the top still reads "Not yet applied on EC2" and needs the same edit.

### 5.3 The places gazetteer and the shoreline snap (PR #28)

`data/places/ca_places_gazetteer_2025.csv` holds all 1,619 California places from the Census 2025 Gazetteer with their internal point (`data/places/README.md`). `services/agent/places.py` resolves the 483 incorporated cities and towns plus 32 county-word census designated places. City routes call `/spatial/point` with `snap_shoreline=true`: a center point that no polygon contains is snapped to the one polygon within 50 m for IOU territories and 150 m for counties, never to an HFTD tier or a grid cell, never from inside a hole, and never when two candidates are within range (`services/data_query/queries.py`, `spatial_point`, `SHORE_SNAP_IOU_M`, `SHORE_SNAP_COUNTY_M`). Albany's point is 3.5 m outside PG&E's polygon on the Bay shore, which is why this exists. The snap is disclosed in the response metadata and the `city_shoreline_snap` caveat. The flag is a harness-only argument stripped from every non-router call (`schemas.harness_only_arguments`, `tools.strip_harness_only_arguments`).

### 5.4 The corrected model metrics (issue #60, PR #70)

Plain language: the risk service's metrics endpoint used to serve a table from an earlier session whose cNHPP row was identical to the NHPP row, which is what you get when the memory parameter xi is zero. It did not describe the committed fit.

`python -m services.risk_forecasting.evaluate_metrics` now regenerates `outputs/metrics_table.csv` from the committed `artifacts/cnhpp_params.npz` (xi 0.2, trained 2020 to 2023, validated on 2024) and refuses to write unless the cNHPP log likelihood reproduces the file's `val_log_likelihood`. `GET /metrics` returns 503 if the table's `params_sha256` does not match, if the cNHPP log likelihood differs, or if cNHPP equals NHPP while xi is not zero (`services/risk_forecasting/app.py`, `load_verified_cnhpp_row`).

| Model | Log-likelihood (2024) | Top 5% precision | Top 1% precision | AUC | Lift top 5% |
|---|---|---|---|---|---|
| HPP | -5204.19 | n/a | n/a | 0.500 | n/a |
| NHPP | -4877.62 | 0.00534 | 0.00299 | 0.759 | 2.18 |
| cNHPP (xi 0.2) | -4873.63 | 0.00486 | 0.00349 | 0.760 | 1.98 |

The cNHPP minus NHPP gap of about 4 is inside the day-bootstrap interval in `outputs/model_comparison.csv`, so the two remain a statistical tie, as the README caveats say (`services/risk_forecasting/README.md`).

### 5.5 The PSPS reports dataset (PR #29, open, not merged)

Plain language: after every PSPS event a utility files a long PDF report with the CPUC. Michael built a pipeline that reads every PG&E, SCE, and SDG&E report the CPUC lists (155 events, 2017 to 2026) and fills a table of nine facts per event, each with its source page. Jev answers five categorical questions from a handful of retrieved pages; GPT-6 Luna reads the numbers and must cite a page and a quote; code computes the duration. Uncertain values go to a review queue.

On the `research-psps-reports` branch (`research/psps_reports/`, FINDINGS.md, `round3/README.md`, `round3/REVIEW_GUIDE.md`):

- Three rounds: a 10-report pilot used for design (Jev 42 of 50, Luna 40 of 40), a 15-report clean test (Jev 67 of 75, 50 of 51 right at confidence 0.9 or above; Luna 55 of 60), and the full run with a 10-report never-read accuracy sample (80 of 90 values right, numbers 40 of 40, the review rule flags 9 of the 10 errors, 69 of 70 unflagged values right). All labels are one reviewer's.
- Review queue: 286 flagged items across 145 events, split between two reviewers with a 40-item overlap (`review_reviewer_A.csv`, `review_reviewer_B.csv`, `agreement.py`). About 5.5 to 8 hours per reviewer. Reviewer B has not been assigned; section 16 suggests you.
- Cost of all three rounds: $2.66.
- 22 hand-checked internal contradictions inside the reports (start times, customer totals, county counts) plus 28 automatic candidates (`round3/contradictions.csv`).

**Cross-check against the warehouse** (`round3/warehouse_crosscheck.py`, `warehouse_crosscheck.csv`, read-only queries against `wildfire.psps_events`, which holds 56 PG&E, SCE, and SDG&E events from October 2021 to November 2025): all 56 warehouse events match a report one to one; inside that date range the dataset has 22 more events, all notifications with no shutoff, which the warehouse does not record. Customers de-energized match exactly on 37 of 56, differ by under 2 percent on 15, and differ a lot on 4 where the report explains the gap (total versus unique customers for SDG&E, one utility's report counting another utility's customers). First shutoff date matches on 51 of 53 and last restoration on 50 of 55; the misses trace to a few late or third-party customers. County comparison is weak because the warehouse has no county field. Conclusion in the findings memo: the two sources agree on which events happened and on nearly all dates and disagree on definitions, so a combined panel should carry both a total and a unique customer count and say whose customers are counted.

### 5.6 Filter normalization (PR #76, follow-ups #77 and #78)

Plain language: a filter value that matched nothing used to return zero rows, and the agent read that zero as a real count ("0 incidents in Butte County" when the model sent "Butte County" and the warehouse stores "Butte"). Now the value is resolved to the canonical name first, and a value that resolves to nothing is an error that names the close matches.

- `services/shared/counties.py`: the 58 Census county names, known aliases, and `normalize_county`, which strips a trailing "County" or "Co.", ignores case and punctuation, and raises `UnknownCountyError` with the closest names. It never returns a name outside the canonical list.
- `services/data_query/filters.py`: `parse_county` (400 with suggestions), `parse_tier` ("tier 3", "T3", "3", "HFTD Tier 3" all resolve), `parse_utility` (full names such as "Southern California Edison" and "Bear Valley Electric Service" resolve). Applied on every Data Query, Visualization, and Comparison endpoint that takes a county (PR #76 lists them), so the three services agree.
- Agent side: county arguments are normalized before the call and logged as `harness_county_correction`; a backend unknown-county or unknown-utility 400 becomes a recoverable `unknown_county` or `unknown_utility` tool error, the model may retry with the suggestion, and if it keeps the bad value the user sees the suggestion, never a zero (`services/agent/tools.py`, `orchestrator.py`, `tests/agent/test_county_arguments.py`).
- Follow-ups #77 and #78 (branch `filter-followups`, open PR): EPSS `outage_type` and `cause` and a single CAL FIRE `incident_type` resolve against the values stored in the warehouse (`services/shared/stored_values.py`, `SELECT DISTINCT` cached per process), ignoring case and spacing, and an unmatched value is a 400 with close matches. Under a written rule (2026-09-24), an EPSS cause code and its word form are one cause: VEG and Vegetation, UNK and Unknown, 3RD and 3rd Party match together and display as the word; EF is ambiguous and left alone. A CAL FIRE county filter, ranking, grouping, or comparison counts a multi-county incident such as "Shasta, Tehama" in every county it lists (`services/shared/calfire_county.py`), so Shasta 2020 is 6, not 4. County-scoped CAL FIRE responses report `multi_county_incidents`, and the agent adds the `calfire_multi_county` caveat saying how many and that county totals can exceed the statewide total. What changed, why, and every changed count: [`docs/DATA_CHANGE_CALFIRE_COUNTIES.md`](DATA_CHANGE_CALFIRE_COUNTIES.md).

---

## 6. Services and endpoints

Five FastAPI apps. The endpoint list is from the `@app.get` and `@app.post` decorators in each `app.py` on `main`, compared with the same files at the PR #4 merge (`ff21406`). "New" means added since PR #4.

**Data Query, port 8000** (`services/data_query/app.py`): `/health`, `/rank`, `/grouped-counts`, `/summary`, `/regional-series`, `/ignitions`, `/us-ignitions`, `/epss/outages`, `/psps/events`, `/psps/events/{event_name}/circuits`, `/calfire/incidents`, `/circuits`, `/circuits/{circuit_id}`, `/hftd`, `/iou-territories`, `/spatial/point`, `/spatial/summary`. No new endpoints since PR #4 (the three aggregates arrived in PR #3). Changed: `/spatial/point` takes `snap_shoreline` (PR #28); `/hftd` and `/iou-territories` take `simplify` (PR #69); CPUC rows carry `county`; every `county`, `utility`, and `tier` parameter is normalized and an unknown county is a 400 with close matches (PR #76).

**Visualization, port 8002** (`services/visualization/app.py`): `/health`, `/map-layer`, `/time-series`, `/utility-territory`, `/event-detail`. No new endpoints. Changed: `/map-layer?dataset=hftd` and `/utility-territory` draw simplified copies of the rebuilt geometry (PR #45); `/map-layer` and `/time-series` use the same county, utility, and tier parsers as Data Query (PR #76).

**Comparison, port 8003** (`services/comparison/app.py`): `/health`, `/compare-utilities`, `/compare-regions`, `/compare-periods`. No new endpoints. HFTD regions now return values instead of errors (PR #45); county regions and county scopes are normalized (PR #76).

**Historical Risk, port 8001** (`services/risk_forecasting/app.py`): `/health` (changed: 503 with `failed_stage` unless the startup trial prediction passed, PR #27), `/predict`, and **new**: `/surface` (PR #9), `/observed` (PR #10), `/observed-training` (PR #11), `/metrics` (PR #12, hardened in PR #70). CORS was added in PR #16 so the website can call it. A systemd unit for it exists since PR #24. County and utility place names are normalized the same way (`services/risk_forecasting/place.py`, PR #76).

**Agent, port 8004** (`services/agent/app.py`): `/health`, `POST /ask`, `POST /ask/stream`, `/artifacts/{ref}`. No new endpoints. Changed: `/health` reports the risk service's degraded detail and `failed_stage` (PR #27), and its model block is the model name plus provider health (the `thinking`, `structured_mode`, and `num_ctx` fields went with the Ollama path, PR #73). Every `/ask` response and every `/ask/stream` routing event carries `decision_source` (PR #71). `POST /ask` is kept unchanged for the eval runner.

**Removed:** the GPU control service on port 8005 (`services/gpu_control/`, four endpoints) and its systemd unit were deleted in PR #73. Its `/health`, `/gpu/status`, `/gpu/start`, and `/gpu/stop` no longer exist anywhere.

---

## 7. The website

### 7.1 The 18 panel views

`PANEL_VIEWS` in `website/src/panelViews.ts` on `main`, against the 13 in the same file at PR #4:

| Category | View id | Title | Since |
|---|---|---|---|
| Map | `events-map` | Wildfire events | PR #4 |
| Map | `outages-map` | Outage circuits | PR #4 |
| Map | `psps-map` | PSPS areas | PR #4 |
| Map | `weather-map` | Fire weather | PR #4 |
| Map | `risk-surface-map` | Modeled ignition risk surface | **new**, PR #16 |
| Map | `residual-map` | Model residual map | **new**, PR #17 |
| Time series | `events-time` | Event trends | PR #4 |
| Time series | `annual-time` | Year comparison | PR #4 |
| Time series | `regional-time` | Regional trends | PR #4 |
| Time series | `seasonal-time` | Seasonal profile | PR #4 |
| Time series | `cumulative-acres` | Cumulative acres burned within a season | **new**, PR #14 |
| Time series | `customer-events` | Customers affected over time | **new**, PR #15 |
| Comparison | `county-comparison` | County ranking | PR #4 |
| Comparison | `utility-comparison` | Utility comparison | PR #4 |
| Comparison | `cause-comparison` | Cause breakdown | PR #4 |
| Record table | `event-records` | Event records | PR #4 |
| Stat card | `summary-stats` | Summary metrics | PR #4 |
| Stat card | `medical-exposure` | Medical baseline and life support customers affected by EPSS outages | **new**, PR #13 |

`currentView` derives the view id from settings (`mapMode` risk or residual, `seriesMode`, `groupBy`, `statMode`), and `panelDatasets` returns no dataset for the risk surface and `cpuc` for the residual map.

### 7.2 How chat opens panels

Plain language: the agent never tells the website what to draw in free text. It returns typed view specs, each pointing at the tool result it came from. The website keeps a spec only if that pointer is present and the spec maps cleanly onto one of the 18 views; otherwise the answer text stands alone and a small notice says the view is not supported here.

- **The spec wrapper.** `views.py` returns `PlannedViews` (`views`, `view_status` of `applied`, `planner_fallback`, or `none`, and `view_scope`), dumped into the `AskResponse` (`dump_planned`). Each `ComponentSpec` has a `type` (map, time_series, comparison, record_table, stat_card, spatial_context), `params` validated by the per-type model, `evidence_ids` with at least one entry, and `artifact_refs`. The TypeScript mirror is `website/src/agentContracts.ts` (PR #26 added `show_hdw`, `map_mode`, `risk_date`, `series_mode`, `datasets`, and the stat card's `stat_mode`, `view_id`, and date fields).
- **The evidence requirement.** `ground_views` rejects a spec whose `evidence_ids` are not primary tool results, and each `_ground_*` function checks the parameters against the cited execution (a `risk_date` no cited risk call scored is rejected). On the website, `hasEvidence` in `answerPanels.ts` drops any view without a non-empty string evidence list; a multi-dataset timeline needs one evidence id per dataset.
- **Grounding on the website.** `panelsFromAnswer` builds a panel only when it can resolve the dataset to a known `DATASETS` entry and a full date window; it never fills in a dataset or date. Ranking comparisons become the county, utility, or cause comparison; `series_mode` opens the matching series view; `map_mode` risk or residual opens the grid maps for the cited day; `stat_mode` summary or medical_exposure opens those cards; scalar stat cards keep `answerStat` and are not persisted. Non-ranking comparisons and spatial-context specs produce the `unsupportedViewNotice`. `website/tests/answer-panel-reach.test.ts` proves every one of the 18 views is reachable from a planner output.
- Panels added from Ask arrive pinned to the dates the tools ran (`filterMode: 'override'`), so the year bar does not overwrite them.

### 7.3 Year bar, playback, clarifications

- **Year bar** (`website/src/globalFilters.ts`, `WorkspaceFilters.tsx`, `PanelYearPin.tsx`, PR #19): years 2014 to 2025, default 2024, stored under `wildfire-workspace-global-v1`. `effectiveSettings` overlays the year's window on a panel's filters when it inherits; a pinned panel (`filterMode: override`) shows a `Pinned: YYYY` badge. Year comparison, seasonal profile, and agent scalar cards never follow the bar (`panelUsesGlobalYear`).
- **Playback** (`website/src/playback.ts`, `PlaybackControls.tsx`, PR #20): event maps toggle between Full range and Day by day; day-by-day filters already-paged features locally by start date; the HDW player shares the same controls. Empty days show a chip instead of a blank map.
- **Clarifications.** An answer with `status` clarification or unsupported arrives as assistant text with no views (`plan_views` returns none unless the status is answer). The clarification text now names every missing item and an example rephrasing (PR #51). Qualifications not already in the answer text are appended to the message (`App.tsx`).
- **Tool chain.** `ToolTrace.tsx` shows the streamed and final trajectory in a collapsed disclosure; the final trajectory replaces the streamed one. Since PR #71 it also shows one line saying who made the answer, clarify, or refuse decision (`decisionSourceLabel` in `agentTrace.ts`): "Decided by Jev (0.93)", "Safety rule: live data", or "Router (Jev below confidence gate)". It is read from the streamed routing event until the answer arrives and never appears in the answer text. The wire type is `AgentDecisionSource` in `agentContracts.ts`.
- **Build freshness.** The committed site in `docs/` was written on 2026-09-18 and never rebuilt after PRs #26 and #71 merged, so the published page lacked the chat-to-panel routing keys and the decision line until PR #75 rebuilt it. `website/scripts/check-build.mjs` now builds into a temporary directory and lists every file missing, stale, or different in `docs/`; `tests/build-freshness.test.ts` runs it inside `npm test`, and `npm run check-build` runs it alone. The `CLAUDE.md` rule: any PR that changes website source commits the rebuilt `docs/index.html` and `docs/assets/workspace/` in the same PR.

### 7.4 What changed in the code you wrote

51 files under `website/src` and `website/tests` changed between PR #4 and `510d2fd` (2,665 insertions, 343 deletions, `git diff --stat ff21406 510d2fd`); PRs #71 and #75 then touched `ToolTrace.tsx`, `agentTrace.ts`, `agentContracts.ts`, `package.json`, and added `scripts/check-build.mjs` plus two tests. The changes to files you authored:

- `App.tsx`: the `GlobalFiltersContext` provider and its persistence, the `ThemeToggle` in a new site header, stricter validation of saved panels for the new settings fields. `applyAnswer` and the notice are unchanged in shape.
- `state.tsx`: `PanelSettings` gained `filterMode`, `mapMode`, `mapView`, `playbackDate`, `riskDate`, `statMode`, and two more `seriesMode` values; `GlobalFiltersContext` and `useGlobalFilters` were added.
- `PanelWorkspace.tsx`: the year bar above the grid, the pin control in the header, and `effectiveSettings` applied before content renders.
- `panelViews.ts`: five new views, `currentView` and `panelDatasets` extended.
- `agentContracts.ts` and `answerPanels.ts`: the wire contract additions above and the adapters for rankings, series modes, grid maps, summary and medical cards, HDW, and timelines (`answerPanels.ts` grew by 214 lines).
- `EventMap.tsx` and `HdwPlayer.tsx`: playback extracted into shared controls; `RecordPanels.tsx`: the medical exposure card; `data.ts`: `DATASETS` generated from the registry with a local overlay; `exports.ts` and `caveats.ts`: the EPSS and US ignitions caveat strings on cards and CSV; `index.css`: theme tokens; `api.ts`: the risk API URL (`VITE_RISK_URL`).
- `ToolTrace.tsx` and `agentTrace.ts`: the decision line (PR #71); `agentContracts.ts`: `AgentDecisionSource` and `decision_source` on `AgentAnswer`.
- New tests: `answer-panel-reach`, `answer-panels`, `cumulative`, `customer-events`, `event-map-playback`, `exposure`, `global-year-filter`, `grid-surface`, `residual`, `risk-surface`, `theme`, `decision-source`, `build-freshness` (27 test files). PR #75 reported 116 Node tests passing.

Build and test commands: `cd website && npm ci && npm test && npm run build`, plus `npm run check-build` (Node 24; `website/README.md`). The workspace storage key is still `wildfire-workspace-v1`.

---

## 8. The harness

Plain language: the language model is not trusted. Code around it decides which tools it may call, fixes or rejects arguments the question does not support, refuses to answer partially, and checks that every number in the prose came from a tool.

### 8.1 Every guard

| Guard | Where | What it does |
|---|---|---|
| Candidate tools | `routing.candidate_tools` | The model sees at most three tools chosen from the wording, never all seven. |
| Filter grounding | `services/agent/grounding.py`, `ground_model_filters` (PR #25) | Drops a model-proposed `circuit_id`, `tier`, `hftd_tier`, `county`, `lat`, or `lon` that the question text or router slots do not support, and sentinel values (all-zero ids, 0,0 coordinates, empty strings). Named tier numbers keep only those tiers; "which tier" keeps both. Each drop is a `filter_dropped` event. Deterministic router calls are not touched. |
| Enum grounding | `grounding.question_allows_untagged`, `question_incident_type_modes` (PR #79) | A schema-valid enum value is still an invented filter when the question never asked for it: `utility=untagged` runs only when the question mentions untagged, unattributed, or non-utility records; CAL FIRE `incident_type_mode` `all` or `untyped` runs only when the question asks for every type or for records with no type. Injected eval faults record the arguments that would have run. |
| Utility grounding | `tools._strip_ungrounded_utilities` | A utility filter must appear in the question or slots; place names are never coerced to an IOU. Attaches the `utility_filter_stripped` caveat. |
| County normalization | `services/shared/counties.py`, `tools.py` (`harness_county_correction`), `orchestrator.py` (PR #76) | County arguments are resolved to the canonical Census name before the call; an unresolvable value is sent as written so the backend rejects it with suggestions; the `unknown_county` and `unknown_utility` error codes are recoverable, and a model that keeps the bad value ends in an error that shows the suggestion, never a zero. |
| Year and range guards | `time_resolve.apply_harness_years` and `_hold_resolved_window` (PRs #21, #48, #50) | Out-of-coverage years always fail; a wrong model year is overridden with the harness year; an invented year with no harness year is rejected (`year_not_derived`); with `hold_window` a model call may not narrow a resolved span ("2021 to 2025" with `year=2023` becomes the full span); per-year questions keep per-year calls; listed years reject unlisted ones. Each correction is a `harness_time_correction` event. |
| Harness-only argument stripping | `schemas.harness_only_arguments`, `tools.strip_harness_only_arguments` (PR #28) | Fields hidden from the model schema (today only `snap_shoreline`) are removed from every non-router call and logged as `harness_arguments_stripped`. |
| Schema validation and retry bound | `tools.ToolExecutor.execute`, `orchestrator._model_loop` (`schema_retry_bound`) | Pydantic validates arguments before HTTP; an identical non-transient failure fingerprint twice blocks that tool. `AGENT_MAX_VALIDATION_RETRIES` bounds schema retries. |
| Tool-call limit | `AGENT_MAX_TOOL_STEPS` (default 5) in `_model_loop` | Caps model turns, not identical-failure retries. |
| Multi-part coverage, no partial answer | `grounding.named_entities`, `uncovered_entities`; `_model_loop` (`uncovered_entities_continue`, `uncovered_entities_stop`) (PR #25; always on since PR #73) | After a successful turn the loop asks the model for any named utility, county, year, or tier no call covered; if any are still uncovered at the limit the answer is an error naming them, never a partial answer. |
| Risk not substituted | `orchestrator._ask_routed` | A risk question whose model path reached no successful `risk_forecast` is refused rather than answered with a count. |
| Response contract checks | `tools.py` (`_require_dict`, `_require_int`) | Backend HTTP 200 responses are contract-checked so partial data cannot degrade into an answer (eval case `detect_partial_200`). |
| Evidence ids in synthesis | `orchestrator._synthesize`, `provider.strict_agent_answer_schema` | The answer schema requires `claims[].evidence_ids`; cited ids must be real evidence; uncited numbers fail the quantity check (`_quantity_mismatches`, `_curated_evidence_numbers`); missing citations with correct numbers are repaired (`citation_repaired`); otherwise synthesis falls back to the tool summary. A record-list sample size ("returned 10 records") is accepted (PR #25). Changes, differences, percent changes, and ratios are grounded only through the `harness_arithmetic` evidence item (`services/agent/derived.py`). |
| Readable answers | `orchestrator._ensure_readable_answer` | A bare number is rewritten through the deterministic renderer with dataset, year, and scope. |
| Unexpressed constraints | `routing._block_unexpressed_constraints` | Deterministic rules refuse rather than drop a named county or month. |
| Artifact store | `services/agent/artifacts.py` | Full payloads stay out of model context in a bounded 15-minute store (`AGENT_ARTIFACT_TTL_SECONDS`). |
| Model offline | `orchestrator._provider_available`, `MODEL_OFFLINE_ANSWER` | Model-path questions return a clear offline sentence with HTTP 200; deterministic routes keep working. |
| Cancel and timeout | `app.ask_stream`, `AGENT_TIMEOUT_SECONDS`, `AGENT_SYNTHESIS_TIMEOUT_SECONDS` | Cancellation is honored between steps; synthesis has its own timeout. |

The August 2026 record of these guards and the runs that motivated them is `services/agent/eval/HARNESS_GUARDS.md` (historical, with a status note).

### 8.2 The model path on OpenRouter (PR #25, hosted-only since PR #73, `docs/OPENROUTER.md`)

OpenRouter is the only provider. `AGENT_LLM_PROVIDER` accepts only `openrouter`, `OPENROUTER_API_KEY` is required, and startup fails with a clear message until `AGENT_ALLOW_REMOTE_PROVIDER=true` confirms that questions may leave the host (`services/agent/config.py`, `validate`). Routing and synthesis go to `https://openrouter.ai/api/v1/chat/completions` (`provider.OpenAICompatibleProvider._complete_hosted`). The Ollama client, the JSON call envelope (`constrained.py`), the no-think alias (`model_setup.py`), the context warmup, and the `num_ctx` and `structured_mode` settings were deleted in PR #73.

- **Tool choice.** Routing sends native `tools` with `tool_choice: "required"`, so the model cannot answer without a tool. The runner still records no-tool or direct-answer attempts.
- **Strict nullable schemas.** Every optional tool field is nullable and listed in `required` (`strict_nullable_tool`), and nulls are dropped before the harness sees the call (`drop_null_arguments`). Without this, Luna filled optional filters with placeholders (37 invented values on the 14 forced-model cases; 0 after).
- **Several calls per turn** are allowed; `parallel_tool_calls` is not sent because OpenRouter does not list it for Luna. `provider.require_parameters` keeps requests on hosts that honor `tool_choice` and `response_format`.
- **Fallback model.** Routing turns after a failed or empty turn, synthesis attempts 2 and later, and one retry after an HTTP error use `AGENT_LLM_FALLBACK_MODEL` (default `openai/gpt-6-sol`, `_complete_hosted_with_fallback`, `_turn_model`). A continuation after a success stays on Luna.
- **Synthesis** uses `response_format` `json_schema` with `strict: true` and the same answer schema.
- **Cost logging.** Every hosted request prints an `llm_usage` line with tokens, computed cost, and OpenRouter's reported cost; prices with source URLs are in `services/agent/pricing.py` (Luna $0.10 in and $0.50 out per million tokens; Sol $2.00 and $10.00; Jev $0.042 in, checked 2026-09-23).

Measured on the 14 forced-model dev cases after all fixes: 14 of 14 pass, 0 invented filters, every number matches SQL, p50 4.5 s, p95 7.5 s, $0.022 (`docs/OPENROUTER.md`). On the 105 model-path holdout questions (v1, v2, v3, one pass, Jev off): 48 pass; 29 of the 47 wrong answers were questions labeled clarify or refuse, which is what decide mode exists to fix. The rerun after PRs #73 and #76 ($0.038, dev and holdout data) matched SQL on every holdout number, and `ho_006` (Butte 9, Shasta 4) passed because the harness normalized the model's "Butte County"; the two runner differences it surfaced (a count-plus-records sequence on the Sacramento case, and `utility=untagged` on `recover_503`) were fixed in PR #79.

### 8.3 Every caveat and when it attaches

All from `services/agent/caveats.py`, `collect_qualifications`. Static text is in `CAVEAT_TEXT`; the others are built at collect time.

| Caveat id | Attaches when |
|---|---|
| `cpuc_utility_caused` | Any successful CPUC ignition read (`_is_cpuc_ignitions`). |
| `us_ignitions_sample` | Any successful US ignitions read; the service's `sample_geography` note is appended. A CPUC versus US compare question companion-fetches the US read so this attaches even with one primary call (`_ensure_cpuc_us_companions`). |
| `epss_pge_only` | Any EPSS read (`_uses_epss`). |
| `calfire_missingness` | Any CAL FIRE read; the null incident-type and utility-tag counts come from metadata, fetched by a companion call when missing. A failed fetch suppresses the answer. |
| `calfire_map_feed_counts` | A CAL FIRE answer that spans 2023 and 2024, or compares CAL FIRE counts across two or more years (`_needs_calfire_map_feed_caveat`). |
| `ignition_definition_<utility>` (and `_period_a`, `_period_b`) | Every utility-scoped CPUC ignition count, paired with the same-period spatial containment count by a companion call, in either direction (`_is_attribute_utility_ignition`, `_is_spatial_comparison_ignition`, `_is_spatial_utility_ignition`). One caveat per utility lists the pair for every period whose companion ran; before this it kept only the first period. |
| `cnhpp_grid_resolution`, `cnhpp_contagion_tie` | Any successful `risk_forecast` or `risk_surface` (`_needs_cnhpp_risk_caveats`). |
| `cnhpp_cell_461` | A risk answer whose scored cells include cell 461. |
| `utility_filter_stripped` | The executor removed an invented utility filter. |
| `city_center_point`, `iou_territory_not_provider` | A city resolved to its Gazetteer point and the point read ran (`city_point_for_question`, PR #28). |
| `city_shoreline_snap` | That point read was snapped to a nearby territory or county; names the layer and distance. |

Caveat text for the website cards and CSVs comes from `shared/dataset_caveats.json`, generated from the registry (PR #6), so the two places agree.

---

## 9. Jev

### 9.1 What it is and why we use it

Plain language: Jev is a model from TypeSafe (their System One API) that does not write text. You give it a state (the question, today's date, a short glossary) and a list of small questions, each either a yes/no with a probability (a "Noul") or a choice among named options with a probability each ("Choice"). It answers every question with a label and a number. We use it because the regex router is exact but brittle on wording, and a language model is flexible but cannot be trusted to say "I should not answer this". Jev sits between: it is asked small, unambiguous facts about the question ("does it name a specific place?", "what single result is it asking for?"), and code applies the policy. The lessons that hold (`CLAUDE.md`): ask small mutually exclusive facts and let code apply policy; overlapping yes/no facts land in the 0.2 to 0.8 band; Jev's confidence is relative to the options offered, so a missing option makes it confidently wrong; a Noul's confidence is max(p, 1 - p).

Code lives in `services/agent/decisions/` (`README.md` there lists every module). `backend.py` defines the protocol; `typesafe_backend.py` holds `TypeSafeBackend` and `OpenRouterJevBackend`, imports the SDK only inside a call, disables SDK retries, and redacts keys. Mode `off` never imports the SDK (`test_mode_off_never_imports_sdk_or_builds_runner`).

### 9.2 The early work in the fork, the determinism study, the schema ablations

The work began on Michael's fork (`ByteMasterMike/Wildfire-Services`, branch `jev-shadow`) in mid September 2026 and moved into the team repo as PRs #22 and #43 (section 2). The first schema, v2, asked every routing question in one call with a long domain context.

**Determinism** (`docs/JEV_DETERMINISM.md`, 2026-09-22, `jev-1.13.0`): the SDK has no seed, temperature, or deterministic flag. Twenty stored payloads were sent ten times each; the HTTP body was byte-identical every time (proved by canonical hashing, `decisions/canonical.py`), so any answer change is model variance. Choice labels flipped only in the 0.5 to 0.6 confidence bucket (1 of 6); from 0.6 up, including every answer at 0.8 or above (n 87), the flip rate was 0. Noul yes/no decisions never flipped (n 171). Probabilities still move by roughly 0.01 to 0.17 even when the label does not. This is the noise floor every later measurement is read against.

**Schema ablations** (`services/agent/eval/runs/jev_ablation_summary.md`, `eval/jev_ablation.py`, five repeats on dev): v3 split the questions into small per-topic calls (facts, topic, places, tool pick; `decisions/v3.py`). Five configurations were compared on disposition, intent, dataset, tool pick, and clarify reason:

| Config | Disposition majority / flip | Tool pick | Tokens |
|---|---|---|---|
| v2_full | 81.0% / 6.7% | 100% / 6.7% | 4887 |
| v3_split | 84.8% / 5.7% | 53.3% / 13.3% | 3607 |
| v3_single | 83.8% / 3.8% | 86.7% / 0% | 2969 |
| v3_no_glossary | 79.0% / 7.6% | 80% / 6.7% | 3457 |
| v3_policy_context | 87.6% / 0% | 100% / 6.7% | 6040 |

`v3_policy_context` won: the highest disposition majority with no flips across five repeats, because putting the policy paragraph on every small call helped, while one big call was worse on tool pick and dropping the glossary hurt paraphrases. Its one weakness, a derived clarify reason at 38.5 percent, was fixed by `v3_hybrid`, which adds the v2 direct `clarify_reason` Choice (76.9 percent, flip 0). `v3_hybrid` is the default `AGENT_JEV_ABLATION` and the payload every mode sends.

### 9.3 The payload bug

Plain language: the live tool-pick call was not sending Jev the same context the offline evaluation sent. Offline, Jev saw the policy glossary; live, it saw only a short tool list, and with that little context it chose "clarify" on questions that already named a year. So the offline scores did not describe what production would do.

Fix: commit `2ad599d` on `main` ("Send the live tool_pick call with the same policy glossary as the offline hybrid", from fork commit `b7e9393`, PR #43). `v3.tool_pick_call` now builds the live request from the same `calls_for_config` the ablation uses, and `test_live_tool_pick_payload_matches_the_offline_hybrid_call` in `tests/agent/test_jev_tool_pick.py` fails if they diverge. `tests/agent/test_jev_payload_pins.py` (PR #46) goes further: it stores the payload hashes for three questions from `main` at `c624c58` and fails on any change to a question, option, glossary, or context, so a context change is always a deliberate, measured act.

### 9.4 The head-to-head against qwen

The comparison script `jev_vs_qwen.py` joined a shadow log with a qwen eval run on the question text and compared Jev's tool pick with qwen's first-turn and final tools. Michael reports the EC2 result: both picked the right first tool on 13 of 14 model-path questions, but qwen2.5:7b on the CPU host took 146 to 745 seconds per question while Jev's calls take about 0.4 s (p50 between 370 and 470 ms in every recorded run). The run file from that comparison was local to the host and is not committed, so the numbers here are as reported, not reproduced from the repo. The script itself was removed with the local model path in PR #73; `services/agent/eval/jev_shadow_report.py` now carries the tool-pick comparison against whatever model the trajectory shows (`docs/JEV_SHADOW.md`).

### 9.5 Every mode and both backends

`AGENT_JEV_MODE` (`services/agent/config.py`, `validate`):

| Mode | What it does | Default |
|---|---|---|
| `off` | Never builds a runner, never imports the SDK. | yes |
| `shadow` | `ShadowRunner` sends the v3_hybrid calls in the background and writes `routing`, `tool_pick`, `outcome`, `dropped`, and `wiring_error` rows to `AGENT_JEV_LOG_PATH`. Answers, tools, caveats, views, and eval scores are unchanged. Sample rate, concurrency, daily cap, and timeout apply. Production ran this on 2026-09-24 before switching to decide. | |
| `tool_pick` | On the model path, a Jev tool pick at or above `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE` (0.8) chooses the tool and `tool_pick_mode.py` fills arguments from router slots; anything missing, a two-part question, or an error falls back to the model loop. The shadow log's path label for the model loop is still the historical string `qwen`, kept so older logs parse. | |
| `tool_pick_template` | Same, then a template writes the answer for count, list, map, trend, rank, spatial context, and a simple comparison. | |
| `decide` | Router backstops first, then Jev's disposition behind two gates. Section 9.7. **On in production since 2026-09-24.** | |
| `plan` | Archived on `jev-plan-archive`; rejected at startup. | |

`verify`, `fallback`, and `route` are reserved names that abort startup.

`AGENT_JEV_BACKEND`: `typesafe` (default, `api.typesafe.ai`, key `TYPESAFE_API_KEY`, model `jev-latest`, served `jev-1.13.0`) or `openrouter` (`POST https://openrouter.ai/api/v1/systemone`, key `OPENROUTER_API_KEY`, pinned model `typesafe/jev-1.13-20260917`). Both use the same SDK client and body; `test_openrouter_jev_request_carries_the_same_state_questions_and_options` compares the wire bodies. Measured on dev, one pass each (`runs/jev_backend_compare_20260923T214759Z.json`): label accuracy 96.3 percent against 96.8 percent, 98.9 percent agreement, identical mean confidence 0.906 against 0.907, p50 469 ms against 437 ms, p95 851 ms against 1294 ms, $0.03 each. Production uses the OpenRouter backend because TypeSafe's direct API ran out of credits (HTTP 402 on 2026-09-23, `docs/JEV_DECIDE.md`).

### 9.6 The v3 questions and the policy

`services/agent/decisions/v3.py` builds three disposition calls (a fourth, `tool_pick`, only on the model path):

- **facts**: nine Nouls in `FACT_NOULS`: `has_time_scope`, `vague_time`, `future_time`, `names_specific_place`, `vague_proximity`, `broad_region`, `asks_risk`, `names_risk_metric`, `prompt_injection`.
- **topic**: Choices `off_topic` (cpz, cost_or_budget, optimization_or_scheduling, damage_or_loss, live_or_web, other_off_topic, on_topic), `intent` (count, records_list, map, trend, map_plus_trend, compare, rank, risk, spatial_context, territory_boundary, circuit_detail, exploratory_overview, multi_intent, other), `dataset` (the eight warehouse datasets plus multiple and none), `rank_dimension`, `measure` (event_count, record_list, acres_burned, customers_affected, historical_risk, supported_rate, other_measure), the Nouls `is_multi_intent` and `mentions_multiple_datasets`, and in `v3_hybrid` the direct `clarify_reason` Choice.
- **places**: one Noul per utility and a 58-county Choice plus none.

Each call carries the per-call policy subset of `DOMAIN_CONTEXT` (`schemas.context_for_call`, `POLICY_SENTENCES`; PR #26 introduced the subsets).

`jev_policy.derive_outcome` turns the facts into an outcome in the router's own order: prompt injection; live-or-web that is really a missing location; the off-topic rules; `other_measure` on a measure-gated intent (count, rank, compare, trend, records_list; a judgment word like worst becomes `ambiguous_risk_metric`); the riskiest phrase; vague proximity; broad region; vague time when no time parsed; out of coverage; risk with a future date, no place, or no day; the ranking rules mirrored from `_route_ranking` (`_ranking_rule`, `ALLOWED_RANK_TRIPLES`); a county on a dataset with no county column; a county and a utility on risk; the missing-year gates by intent (`_lacks_year`, where a parsed year beats a weak Noul); then answer. Every hit records the margins of the facts behind it, and the outcome's confidence is the smallest margin.

### 9.7 Decide mode and the two gates (PR #49, `docs/JEV_DECIDE.md`)

`decide_mode.decide_from_answers` is pure so the runtime and the offline replay share it:

1. `BACKSTOP_RULES` (live, risk future date, future prediction, city needs place, HFTD constraint, every `unsupported_<key>`) decide without calling Jev.
2. `REGEX_ONLY` rules and routes using a router-only tool (`risk_surface`) stay with the router.
3. Otherwise Jev's outcome is compared with the router's. A Jev clarify or refuse wins at or above the decline gate `AGENT_JEV_DECIDE_MIN_CONFIDENCE` (0.8), unless it asks for a time or place the router already resolved (`contradicts_slot`). A Jev answer over a router decline wins only at or above the answer gate `AGENT_JEV_DECIDE_ANSWER_CONFIDENCE` (0.9) measured on the facts behind the router's rule (`_RULE_FACTS`), and never when the router's resolver proved the time or place missing in code (`code_verified`; `time_out_of_coverage` has no Jev fact and is never overridden). Below a gate, on a timeout (`AGENT_JEV_TIMEOUT_SECONDS`, 3 s, one bounded pool of 8 threads), or on any error, the router stands.
4. **Jev owns the disposition, the router owns the wording** (PR #72). When Jev wins a decline and the router declined the same way (both clarify, or both refuse), the final decision keeps the router's text, rule, reason, and clarify-all-missing additions; Jev's rule goes to the log only (`wording: "router"`). When Jev changes the disposition, its clarification goes through `complete_clarification` with the router's slots (`wording: "jev"`). The production case that forced this: "Show PSPS events around Santa Rosa" got Jev's generic "What latitude/longitude or bounding box should I use?" (Jev `missing_location` at 0.88) instead of the router's `undefined_spatial_scope` question plus the missing year. On the 307 stored rows the rule changed 9 texts (7 `missing_location` to `undefined_spatial_scope`, 2 `ranking_missing_year` to `ranking_missing_slots`) and no disposition or winner.
5. An answer proceeds as before: the router's call if it has one, else the model path (`jev_decide_answer`). When both answer, the log now records Jev's answer confidence, the lowest confidence among the decline facts Jev answered no to (`decide_mode.answer_confidence`).

Why the gates are asymmetric: a wrong answer is worse than a clarifying question, so a Jev answer over a decline needs more confidence than a Jev decline over an answer. The decline gate is the 0.8 used by every Jev mode. The answer gate was swept from 0.8 to never on dev and v1 (`runs/jev_decide_answer_gate_sweep.json`): those sets contain no case where Jev answers over a router decline at any gate, so they cannot choose a value, and 0.9 is a stated default one step above the decline gate, not a measured optimum. v3 was not used to choose either gate.

Replayed from stored calls with the default gates, router as of PR #79 (`runs/jev_decide_replay.json`, regenerated in PR #72): decide beats both the router alone and Jev alone on every set (dev 0.964, v1 0.952, v2 0.929, v3 0.800 against router 0.934, 0.905, 0.881, 0.723), Jev's wins fixing 14 decisions and breaking 0. None of these sets is clean.

### 9.8 Decide mode in production: what the logs show, and how to switch back

Decide mode was switched on in production on 2026-09-24 (reported by Michael, section 13), with Jev reached through the OpenRouter backend. Each request leaves two records:

- `decision_source` on the response and the streamed routing event (`decisions/provenance.py`): `backstop` plus the rule when a hard backstop fired; `jev` plus the disposition and confidence when Jev won; `router` plus a `why` otherwise. The Ask panel shows it as one Tool chain line. When Jev and the router agree the router is recorded as the decider (`jev_agreed`) with Jev's disposition and confidence; that is the common case.
- A `jev_decide` line on stdout and in `AGENT_JEV_LOG_PATH` for every disagreement, every Jev decline whose rule differs from the final rule, and every error or timeout: the router path and rule, Jev's disposition, rule, and confidence, the `winner` (`router` or `jev`), the `why` (`agree`, `gate`, `below_gate`, `contradicts_slot`, `code_verified`, `regex_only`, `router_only_tool`, `backstop`, `error`, `timeout`), and `wording` (`router`, `jev`, or null). `services/agent/eval/shadow_report.py` reads the same file.

The production log itself lives on the host (`/home/ubuntu/wildfire-logs/`) and is not in the repo. What the repo records from it is the one finding that drove PR #72: the Santa Rosa question where Jev won a clarification the router had also made, and the user got Jev's narrower text. The closest committed picture of the winner and why distribution is the replay of the 307 stored questions (`runs/jev_decide_replay.json`, every row's `decide` entry):

| Winner and why | Rows of 307 |
|---|---|
| router, agree | 188 |
| router, backstop (Jev not asked) | 59 |
| router, below_gate | 29 |
| jev, gate | 26 |
| router, contradicts_slot | 3 |
| router, code_verified | 1 |
| router, regex_only | 1 |

So on that mix Jev changes the outcome on about 8 percent of questions, always toward a clarification or refusal, and the backstops keep about a fifth of questions away from Jev entirely.

**Switching back to shadow** takes one line and a restart, with no code change and no rollback: on the host set `AGENT_JEV_MODE=shadow` in `/home/ubuntu/Wildfire-Services/.env` (or `off` to stop calling Jev), then `sudo systemctl restart wildfire-agent`, and confirm with the runbook's environment check that the mode reached the process (`docs/DEPLOY_RUNBOOK.md`, sections 5.1, 5.2, and 5.4). The runbook's section 5 still says not to set `decide` in production; that sentence predates the switch and is one of the doc fixes in section 16.

### 9.9 The measurement rules and the noise floor

From `CLAUDE.md`, `docs/JEV_BACKLOG.md`, and the tests:

- **Payload pins.** Live and offline payloads must hash the same (`test_live_tool_pick_payload_matches_the_offline_hybrid_call`, `test_jev_payload_pins.py`). Re-pinning is a deliberate context change.
- **Repeats.** Evals run one pass by default. A change to a Jev question's wording or options runs `--repeats 5` on that question and reports the flip rate (`eval/jev_repeat.py`).
- **Context reports.** A change to the policy sentences reports label accuracy and mean confidence before and after on dev, v1, and v2, because the gates run on confidence (`runs/context_report_pr43.json`, `context_report_pr46.json`; the PR #43 description holds the tables).
- **Route reports.** Every router change reports how many routes changed across `cases.json`, `jev_paraphrases.json`, and every holdout.
- **The noise floor.** From the determinism study, labels at confidence 0.8 or above do not flip and probabilities wander by up to about 0.17. So a one-pass difference of a few rows on a field with a recorded flip rate is not evidence; the PR #43 report's v1 clarify_reason drop from 0.708 to 0.583 (3 rows of 24, the field with the highest flip rate) is recorded as unresolved for that reason (`docs/JEV_BACKLOG.md` item 8).

### 9.10 Why tuning on holdout v3 stopped, and why Jev owns the decision

Holdout v3 was labeled independently from written policy (section 11), and label rules E and F were written from its disagreements. After that the router fixes in PR #22 were also written from v3 disagreements, so v3 became partly tuned. On 2026-09-23 it was frozen (`jev_holdout_v3_labels_chatgpt.json`, the `frozen` record): no router, policy, question, or label change may use v3, and production shadow logs are the next clean test.

The reason Jev now owns answer / clarify / refuse with the router as backstop is in the numbers. On the 65 certain v3 rows the combined decider scored 56 of 65 against 51 for Jev alone and 45 for the router alone (PR #22 description, `jev_holdout_v3_independent_score.json`). On the 105 model-path holdout questions run through Luna, 29 of the 47 wrong answers were questions labeled clarify or refuse: once the router sends a question to the model path, nothing there can decline, because routing forces a tool call (`docs/OPENROUTER.md`). The router keeps the decisions Jev cannot express (the backstops and `REGEX_ONLY`), and Jev decides the rest behind the gates. That design went live on 2026-09-24.

### 9.11 The backlog (`docs/JEV_BACKLOG.md`)

Ten deferred items, each with why it waits and what to measure when it lands: (1) a policy sentence for `unsupported_future_prediction`; (2) a Jev fact for the statewide risk surface so decide does not ask for a place; (3) ranking phrasings the router misses (largest increase, worst months); (4) the Jev side of asking for every missing item at once (the router side landed in PR #51); (5) four v3 rows where Jev misses a clarify; (6) a `prompt_injection` false positive on "Optimize next week's PSPS schedule" (scores 0.61 to 0.64); (7) the decider as a runtime mode (landed as decide; what remains is measurement on shadow logs); (8) a five-repeat check of clarify_reason on v1; (9) a scorer mapping from router topics to `other_off_topic`; (10) `unsupported_future_prediction` as an option of the topic Choice. Its status line still says v2 and v3 files are not on main; they have been since PR #46, so that line is stale.

---

## 10. The slot planner

Plain language: when a question names several utilities, counties, or years, one tool call would drop something, so the router hands it to the model. The slot planner is a deterministic alternative: it builds one exact call per combination from the slots the router already extracted, without any model. It refuses to plan unless every constraint in the question is represented, so it can never answer a narrower question than the one asked.

`services/agent/eval/slot_plan.py`, applied by `apply_slot_plan` in `AgentOrchestrator.ask` only when `AGENT_SLOT_PLAN` is on, the model is not forced, and the route is `multi_entity_deferred` (`docs/JEV_MULTI_TOOL.md`):

- Plans: one `data_query_records` count per combination of named utilities, counties, and separately named years, each carrying the resolved window; a monthly `visualization_create` series (plus a count when a total is asked); or a `data_query_rank` by county. At most 10 calls, each with its own evidence id and caveats.
- **The invariant** (`_unrepresented`): the plan stands only if every call reads the resolved dataset; each utility and county is carried by some call, together they cover all, and the dataset can filter on it (registry `allowed_filters`; US ignitions and PSPS have no county filter); every window equals the resolved window or the calls cover exactly the named years with full-year windows; a named month is inside the window; a quarter, half, or season falls back; map, list, series, and by-county wording is matched by the call type; acres, customers, or a rate cannot be carried by a count; US-sample wording restricted to a state falls back (label rule H). `fallback_reason` names the first failing check. `tests/agent/test_slot_plan_fallbacks.py` holds every reviewer question and probe.
- With decide also on, decide runs first and the planner acts only on a question decide left as an answer.

With the flag on, 25 of the 55 deferred eval rows plan and 30 fall back (PR #46).

**Why Jev's planner was archived.** Jev's own plan mode asked three extra questions (breakdown, output form, also chart) and built calls from Jev facts plus slots (`planner.py`). On the seen holdouts it lost to the slot rule, and it had known paths that answered a narrower question than asked (PR #46 description). Because a planner's whole value is that it never narrows the question, it was removed from `main` and kept intact on `jev-plan-archive` (19 commits, including the frozen v3 question text). `AGENT_JEV_MODE=plan` is rejected at startup so the name cannot be reused by accident.

---

## 11. Evaluation

### 11.1 Every eval set and its status

All under `services/agent/eval/`. Status as recorded in `CLAUDE.md` and the file headers on `main`.

| File | Rows | What it is | Status |
|---|---|---|---|
| `cases.json` | 107 (14 `force_model`) | The runner's end-to-end cases: route, tools, caveats, status, views, plus fault scenarios | Dev, used for tuning |
| `jev_paraphrases.json` | 41 | Paraphrases with Jev labels (disposition, intent, dataset, facts) | Dev, used for tuning |
| `jev_holdout.json` (v1) | 97, of which 67 without `needs_human_review` | Jev-labeled holdout | Seen, now development data |
| `jev_holdout_raw.json` | 100 | The generated questions v1 was labeled from, with intended category | Source |
| `jev_holdout_v2.json` | 65 (43 without review flag), with `gold_plan` | Second holdout, frozen before the planner was built | Seen, now development data |
| `jev_holdout_v2_raw.json` | 74 | Source questions | Source |
| `jev_holdout_v3_questions.json` | 88 | Question text only, ids `hv3_001` to `hv3_088` | **Frozen** |
| `jev_holdout_v3_labels_chatgpt.json` | 88 labels plus 3 records (rules E and F, the frozen marker) | The only source of v3 labels; 23 marked uncertain, 65 certain | **Frozen**, partly tuned |
| `jev_holdout_v3_raw.json` | 95 | Source questions | Source |
| `jev_holdout_v3_independent_score.json` | | The scoring record of the v3 rounds (router alone, Jev alone, combined, rule E round, final rules E and F) | Record |
| `jev_blind_questions.json` | 0 cases | Reserved for questions written by team members who have not seen the routing code; labels filled in later by someone else | Empty |
| `jev_reword.example.json` | | Example alternate criteria for `jev_offline_eval --reword` | Tooling |
| `v3_labeling_packet.md` | | The policy-only packet v3 was labeled from | Record |
| `runs/` | | Stored run outputs: qwen runs since August, Luna runs, the ablation summary, context reports, the decide store and replay, the backend comparison | Record |
| August 2026 runner artifacts | | `REPORT.md`, `summary.json`, `summary.csv` (written by `runner.py`), `PI_SUMMARY.md`, `ROUTING_EXPERIMENT.md`, `HARNESS_GUARDS.md`, and the diagnostic outputs `diagnostic_matrix_results.json`, `diagnostic_full_schema_results.json`, `diagnostic_residual_fixes_results.json`, `flake_probe_results.json`, `synthesis_probe_results.json`, `cpuc_vs_us_diagnosis.json`, `hang_probe_sacramento.json` | Historical (qwen3:4b era, before PR #4) |

Production shadow logs are the next clean test set. Any accuracy claim must say which set it came from and whether that set is clean or already used for tuning (`CLAUDE.md`).

### 11.2 How the holdouts were made

Michael had a separate model that never saw the repository generate the questions with an intended category; those are the `*_raw.json` files (100, 74, and 95 questions). For v1 and v2 the labels were written against the router's policy and marked `needs_human_review` where uncertain. For v3, `v3_labeling_packet.md` states every house policy and every tool's limits without any router trace or model answer, and ChatGPT labeled the 88 questions from that packet alone; the 23 it marked uncertain are excluded, leaving 65 certain rows (`jev_decide_replay.py` docstring, `docs/JEV_DECIDE.md`). `tests/agent/test_holdout_v3_questions.py` keeps the questions file and the labels file aligned and asserts the frozen marker.

### 11.3 Label rules A through H

Every gold label change is a written rule with an author and date, recorded on the row with its previous label (`CLAUDE.md`: never change a gold label without a written rule).

| Rule | What it decided | Where | Rows changed |
|---|---|---|---|
| A | A question missing two things (time and place, or time and region) accepts either matching clarify reason | `label_rules.clarify_alternatives` (PR #43) | Widens matching; no labels rewritten |
| B | "Respectively", "how many ... and how many", or two years with "how many" asks for separate counts, so `data_query_records` is an acceptable tool | `label_rules.separate_count_question`, `tool_alternatives` | Widens matching |
| C | A fragment with a dataset and a year and no verb accepts count or records_list as its intent | `label_rules.fragment_without_verb`, `intent_alternatives` | Widens matching |
| D | A comparison and the matching per-entity counts return the same numbers, so either plan matches the gold plan | `label_rules.plans_equivalent` (PR #46) | Widens matching |
| E | Written label decisions for holdout v3 (Michael, 2026-09-23): rows 14, 24, 35 become `hftd_constraint_unavailable`; 38, 40, 42, 45, 48 become `unsupported_other_measure`; 51 air quality; 46 evacuation; 54 and 64 `unsupported_future_prediction` | `jev_holdout_v3_labels_chatgpt.json` rule record | 12 v3 rows applied; rows 5, 29, 36 left pending for rule F |
| F | A prediction of future events or counts is `unsupported_future_prediction`; hv3_029 answers because the comparison tool returns nulls with reasons; hv3_005 and hv3_036 clarify with `unexpressable_county_filter` because the US sample cannot filter by state; ho_061 stays `risk_future_date` because hotspot is a risk word; `needs_human_review` cleared on every row F decided | same file, plus `jev_holdout.json` and `jev_holdout_v2.json` | v3 rows 5, 29, 36, 53, 59, 62; v1 ho_056, ho_065; v2 hv2_050, hv2_057, hv2_062 |
| G | A utility service-area outline, boundary, polygon, or footprint with no count and no dataset word is the territory boundary map (Michael, 2026-09-23; PR #58) | `cases.json` (`router_sce_service_area`), `jev_paraphrases.json` (`para_territory_boundary`) | 2 rows (one only changed its recorded router path) |
| H | A US-sample question restricted to a state is clarify / `unexpressable_county_filter`, matching rule F (Michael, 2026-09-23; PR #46) | `jev_holdout.json`, `jev_holdout_v2.json` | ho_021, ho_030, hv2_011 |
| I | An EPSS question naming a utility other than PG&E is clarify / `epss_non_pge_utility`, because EPSS covers PG&E only and a count would be absent, not zero (Michael, 2026-09-24; PR #82) | `jev_holdout.json`, `jev_holdout_v2.json` | ho_080, hv2_002 |
| J | A US-sample question restricted to a utility is clarify / `us_sample_utility_filter`, because the sample has no utility column; the clarification offers the national sample or that utility's CPUC ignitions (Michael, 2026-09-24; PR pending) | router only; no eval row names a utility with the US sample | 0 rows |

### 11.4 The route snapshot, the false-positive list, and independent review

- **Route snapshot** (`tests/agent/fixtures/route_snapshot.json`, `tests/agent/route_snapshot.py`, PR #58): the path and rule for all 245 dev and holdout v1 questions. The test fails whenever a router change moves a route without the fixture being regenerated by `python -m tests.agent.route_snapshot`, which is the route report `CLAUDE.md` requires. It has been regenerated deliberately twice since (PR #28 for `ho_073`, PR #68 for three US-sample rows).
- **False-positive list** (`tests/agent/test_router_false_positives.py`, PRs #22 and #58): reviewer probes where a backstop once swallowed an in-scope question ("Will you show me a map of 2024 CPUC ignitions?" must map, "How many PG&E ignitions were there from January 2024 up to today?" must count). Every new backstop gets a negative probe there (issue #41).
- **Independent review.** Every PR was reviewed in a separate Claude Code session that had not written the code, before merging. The reviews produced the issues in section 16 (each says "From the independent review of PR #22", "Follow-up from #45", "From the review of PR 58") and the review-fix commits inside PRs #28, #46, and #68. PR #46 was reworked after two reviews. The PSPS dataset uses the same idea with two human reviewers and an overlap to measure agreement.

### 11.5 How to run tests and evals, and what runs cost

- `pytest tests/agent`: 638 tests collected on `main` at `41e83d5` (`pytest --collect-only`); the whole `tests/` tree collects 983, of which the data-query, boundary, and risk tests need the warehouse, the services on their ports, or the risk data files. One agent test needs Data Query on port 8000 and is marked `requires_service` (PR #24). `tests/agent/conftest.py` sets a fake `OPENROUTER_API_KEY` and the remote gate so settings load offline (PR #73).
- Website: `cd website && npm test` (27 test files, 116 tests as of PR #75, including the build-freshness check) and `npm run check-build`.
- Route report: `python -m tests.agent.route_snapshot > tests/agent/fixtures/route_snapshot.json` and compare.
- Offline Jev scoring with no API calls: `python -m services.agent.eval.jev_offline_eval` reads the shadow log; `jev_decide_replay replay` replays decide mode from the stored calls in `runs/jev_decide_store.json`; `_rescore_rules.py` rescores stored answers with rules A to C.
- Live Jev: `jev_decide_replay capture` (about $0.08 for 307 questions), `jev_backend_compare` ($0.03 per backend per pass on dev), `jev_ablation`, `jev_repeat`. Jev costs $0.042 per million input tokens and output is free; a full dev, v1, and v2 context report cost about $0.12 (`context_report_pr43.json`).
- Live model runs: `AGENT_ALLOW_REMOTE_PROVIDER=true python -m services.agent.eval.runner --models openai/gpt-6-luna --case-ids ...` (the 14 forced-model cases cost $0.02 to $0.08; the 105 holdout questions through `hosted_holdout_run.py` cost $0.22). Since PR #73 the runner defaults to the production model, has one cell per `--models` entry, and no longer takes `--thinking` or `--modes`; every model-path case spends OpenRouter credits, so run it only when asked and with a budget (`CLAUDE.md`). An eval case may list `accepted_tool_sequences` with an `expectation_note` when more than one tool sequence is correct (PR #79).
- Stop and report if TypeSafe or OpenRouter spend in a session passes $5 (`CLAUDE.md`).

---

## 12. Production and operations

Plain language: one EC2 backend host runs the six services under systemd; a CloudFront distribution in front of it serves the website from GitHub Pages and proxies `/api/...` to the services. Michael reaches the host through AWS Session Manager, not SSH.

**Services and ports** (`deploy/systemd/*.service`, `docs/DEPLOY_RUNBOOK.md`): `wildfire-data-query` 8000, `wildfire-risk-forecasting` 8001 (unit added in PR #24), `wildfire-visualization` 8002, `wildfire-comparison` 8003, `wildfire-agent` 8004, `wildfire-frontend` 8765 (the older `frontend/` app, not the Pages site). The `wildfire-gpu-control` unit was deleted in PR #73; if a copy is still installed on the host, the runbook says to disable and remove it. Each unit reads `/home/ubuntu/Wildfire-Services/.env` as its `EnvironmentFile` (`deploy/systemd/SYSTEMD_SETUP.md`). The repo path on the host is `/home/ubuntu/Wildfire-Services`; an eval worktree is at `/home/ubuntu/jev-eval` (`CLAUDE.md`).

**Env settings in use (names only, per Michael's report and the code defaults):** `OPENROUTER_API_KEY`, `AGENT_ALLOW_REMOTE_PROVIDER` (true, or the agent will not start), `AGENT_LLM_PROVIDER` (openrouter, the only value), `AGENT_LLM_MODEL` and `AGENT_LLM_FALLBACK_MODEL` (defaults Luna and Sol), `AGENT_JEV_MODE` (decide since 2026-09-24), `AGENT_JEV_BACKEND` (openrouter), `AGENT_JEV_MODEL` (pinned), `AGENT_JEV_DAILY_CALL_CAP` (500), `AGENT_JEV_LOG_PATH` (outside the repo, `/home/ubuntu/wildfire-logs/`), the four service base URLs, the `POSTGRES_*` settings, and the `RISK_FORECASTING_*` paths. The Ollama and GPU settings were removed from the server config and from the code. Never print `.env`, `TYPESAFE_API_KEY`, or `OPENROUTER_API_KEY`.

**Every env var the code reads** (from `os.getenv` in `services/agent/config.py`, `services/risk_forecasting/config.py`, `shared/db.py`, `shared/paths.py`, and `.env.example`): agent `OPENROUTER_API_KEY`, `AGENT_ALLOW_REMOTE_PROVIDER`, `AGENT_LLM_PROVIDER`, `AGENT_LLM_MODEL`, `AGENT_LLM_FALLBACK_MODEL`, `AGENT_SYNTHESIS_THINKING`, `AGENT_TIMEOUT_SECONDS`, `AGENT_SYNTHESIS_TIMEOUT_SECONDS`, `AGENT_MAX_COMPLETION_TOKENS`, `AGENT_MAX_ROUTING_TOKENS`, `AGENT_MAX_SYNTHESIS_TOKENS`, `AGENT_MAX_TOOL_STEPS`, `AGENT_MAX_VALIDATION_RETRIES`, `AGENT_SEED`, `AGENT_TEMPERATURE`, `AGENT_ARTIFACT_TTL_SECONDS`, `AGENT_DISABLE_DETERMINISTIC_ROUTING`, `AGENT_SLOT_PLAN`, `AGENT_JEV_MODE`, `AGENT_JEV_BACKEND`, `AGENT_JEV_MODEL`, `AGENT_JEV_TIMEOUT_SECONDS`, `AGENT_JEV_SAMPLE_RATE`, `AGENT_JEV_MAX_CONCURRENCY`, `AGENT_JEV_DAILY_CALL_CAP`, `AGENT_JEV_LOG_PATH`, `AGENT_JEV_LOG_MAX_MB`, `AGENT_JEV_ABLATION`, `AGENT_JEV_TOOL_PICK_MIN_CONFIDENCE`, `AGENT_JEV_DECIDE_MIN_CONFIDENCE`, `AGENT_JEV_DECIDE_ANSWER_CONFIDENCE`, `TYPESAFE_API_KEY`, `DATA_QUERY_BASE_URL`, `RISK_FORECASTING_BASE_URL`, `VISUALIZATION_BASE_URL`, `COMPARISON_BASE_URL`; database `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`, `DATASET_DEMO_DATA_DIR`, `GRID_CELL_SPACING_DEG`; risk `RISK_FORECASTING_ROOT`, `RISK_FORECASTING_DATA_DIR`, `RISK_FORECASTING_ARTIFACTS_DIR`, `LOOKBACK_DAYS`, `TRAIN_YEARS`, `VAL_YEAR`. Removed in PR #73 and now rejected or ignored: `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_MODEL_BASE_URL`, `AGENT_MODEL_API_KEY`, `AGENT_MODEL_RUNTIME`, `AGENT_THINKING`, `AGENT_STRUCTURED_MODE`, `AGENT_NUM_CTX`, and every `GPU_*` and `AWS_*` setting. The website build reads `VITE_VISUALIZATION_URL`, `VITE_AGENT_URL`, `VITE_DATA_QUERY_URL`, `VITE_RISK_URL` (`website/src/api.ts`).

**Deploy runbook** (`docs/DEPLOY_RUNBOOK.md`, PR #24, updated in PR #73): record the running commit in `/home/ubuntu/deploy_history.txt`; `git pull --ff-only origin main` (origin on the host is the platform repo); reinstall requirements and recopy systemd units if they changed; restart the six units; health checks on every port; the smoke test; Jev `.env` settings and log verification; the daily shadow report; locking port 8004 to the CloudFront origin-facing prefix list; rotating the TypeSafe key without echoing it; rollback to the recorded commit. Its service table and model-tier note now match the OpenRouter setup. Two parts are still older than the current state: the "Merge and deploy order" section describes the PR #22 era, and section 5 still says not to set `decide` in production (section 16).

**Smoke test** (`scripts/smoke_test.sh`, updated in PR #73): health on 8000 to 8004 and 8765, then six `POST /ask` route checks: a PG&E 2024 count (`filtered_records`), a three-utility count (must not answer one utility), "What utility service territory contains Modesto?" (expects `city_point_context` with the `city_center_point` and `iou_territory_not_provider` caveats), "How many CAL FIRE incidents were there in Modesto in 2023?" (must still clarify with `city_needs_place`), an EPSS utility ranking (`unsupported_rank_epss_utility`), and a live question (`unsupported_live_web`). The default ask timeout is 120 s, since each answer takes seconds on the hosted model.

**Shadow and decide report** (`services/agent/eval/shadow_report.py`): reads the JSONL log and its rotated backups and prints question counts, router versus Jev disposition with the most confident disagreements, the confidence distribution and the share under 0.8, tool-pick decisions, and estimated cost at $0.042 per million input tokens. Stop and report if estimated spend passes $5. The production log is the first clean test of the router and of decide mode; record the first report before anyone tunes against it.

**HFTD reload on the host**: `python -m db.loaders.rebuild_boundaries` with the two cached Esri JSON files copied to `data/boundaries/` (they are gitignored). The reload takes an exclusive lock for about 4 seconds.

---

## 13. Work done outside the repo

These facts come from Michael and cannot be seen in the code. They are current as of 2026-09-24.

- **Production deploy on 2026-09-24.** EC2 moved from PR #15 to current `main`. The HFTD and IOU boundaries were reloaded from CPUC sources on the host, with every area within 0.001 percent of CPUC's. The smoke test passed except the then-outdated Modesto check, which PR #73 fixed the same day (section 12).
- **The agent's language model moved off qwen.** It switched from local `qwen2.5:7b` on a CPU instance to GPT-6 Luna on OpenRouter (Sol fallback). Model-path answers went from 3 to 12 minutes to about 4 seconds. PR #73 then removed the qwen path from the code, so OpenRouter is the only provider and `docs/OPENROUTER.md` records the switch date.
- **Jev in production.** Jev first ran in shadow mode through the OpenRouter backend with a pinned model and a 500-question daily cap; later on 2026-09-24 decide mode was switched on, so Jev now owns the answer / clarify / refuse decision through OpenRouter with the router as backstop. The first production finding (a narrower Jev clarification replacing the router's) was fixed by PR #72. Section 9.8 has what the logs record and how to switch back.
- **qwen and GPU control removed.** All Ollama and GPU settings were removed from the server config, the CPU model instance (172.31.6.133) and the old GPU instance are retired, and PR #73 deleted the GPU control service and the Ollama path from the repo. `CLAUDE.md`, `AGENTS.md`, and the runbook were updated in the same PR.
- **TypeSafe credits.** TypeSafe's direct API ran out of credits (HTTP 402 from 2026-09-23), which is why Jev runs through OpenRouter.
- **Funding.** A Hillclimb AI grant provided $5,000 in OpenRouter credits and 3 months of Claude Max 20x.
- **Still open on the server.** Restricting port 8004 to CloudFront (runbook section 6) and revoking the old TypeSafe key (runbook section 7).
- **Tooling.** Development moved from Cursor to Claude Code, running up to six sessions in parallel, each in its own git worktree, with independent review sessions before merging (section 14).

---

## 14. How we work

- **Claude Code sessions.** Each piece of work runs in its own Claude Code session and its own git worktree (`git worktree add ../wf-<name> -b <branch> platform/main`), so sessions never share a working tree. Reviews run in a separate session that did not write the code.
- **The rules files.** `CLAUDE.md` (project instructions, wins on conflict) and `AGENTS.md` (learned preferences and workspace facts shared with other tools) are read at the start of every session. The hard rules: no em dashes anywhere; push only to `platform` (the team repo), never to `origin` (Michael's old fork); never change `main` directly, work on branches, open PRs, do not merge; never print or commit `TYPESAFE_API_KEY`, `.env`, or any credential; never change a gold label without a written rule; evals run one pass unless Jev wording changes; do not run the eval runner unless asked with a budget, because every model-path case spends OpenRouter credits; stop at $5 of API spend; every routing change reports route changes across every eval set; every accuracy claim names its set and whether it is clean; every PR that changes behavior updates the affected docs in the same PR and lists them; any PR that changes website source commits the rebuilt `docs/` in the same PR (PR #75); never describe unmerged work as done.
- **Reviews before merging.** A PR is opened with "Do not merge until review". An independent session reviews it; blocking findings are fixed on the branch, non-blocking ones become issues. Merges happen on GitHub after review, never from the host.
- **Merge order.** After each merge, the next branch is rebased onto `platform/main`, `pytest tests/agent` is rerun, and route changes are reported (`CLAUDE.md`, "Branches and merge order"). Deploys pull `main` only.
- **Verification habits** (`AGENTS.md`): verify data by content, not filenames; prefer failing loudly to adjusting tests; ground numbers against SQL or the source; for visual work verify against the rendered result; never call `grid_data_prep.load_year()`; never modify `models.py` or `grid_data_prep.py`.

---

## 15. Documents

Every file in `docs/` on `main`, plus this one:

| File | Covers |
|---|---|
| `docs/README.md` | The built GitHub Pages site: preview, rebuild, data behavior, the year bar, Ask view support. |
| `docs/CANVAS.md` | Working reference for the older `frontend/` Historical Map canvas and its six component types (identical copy in `frontend/`). |
| `docs/CANVAS_PANEL_PROPOSAL.md` | Superseded canvas proposal with a status note on what shipped where. |
| `docs/VERIFICATION.md` | The August frontend versus visualization API count verification (historical). |
| `docs/CITY_POINTS.md` | City questions answered at the Census center point: routes, caveats, the shoreline snap, county-word places, what still clarifies, what a city-boundary layer would take. |
| `docs/DATA_CHANGE_HFTD_IOU.md` | The HFTD and IOU rebuild: what was wrong, sources, ring rules, the gate, before and after counts, deploy notes. |
| `docs/DEPLOY_RUNBOOK.md` | The EC2 deploy, health, shadow mode, port lock, key rotation, and rollback runbook. |
| `docs/JEV_SHADOW.md` | Jev modes off, shadow, tool_pick, tool_pick_template; every `AGENT_JEV_*` variable; logs and reports; privacy; the tie note. |
| `docs/JEV_DETERMINISM.md` | The repeat study: byte-identical requests, flip rates by confidence. |
| `docs/JEV_DECIDE.md` | Decide mode: order, gates, the wording rule, slot and code-verified rules, what the log records, `decision_source`, threads, the gate sweep, replay and live results. |
| `docs/JEV_MULTI_TOOL.md` | The slot planner, its invariant, and why Jev's planner was archived. |
| `docs/JEV_BACKLOG.md` | Ten deferred Jev changes with what to measure. |
| `docs/OPENROUTER.md` | The OpenRouter LLM and Jev backends, prices, every measured run, the enum grounding rule, and the record of the 2026-09-24 production switch. |
| `docs/dataset-comparison-cpuc-calfire-us.md` | Why CPUC, CAL FIRE, and US ignitions cannot be compared or combined (August 2026 memo with a status note). |
| `docs/HANDOFF_SINCE_PR4.md` | This document. |

The root `README.md` has a documentation index that also points at the service READMEs, `services/agent/SECURITY.md`, `services/agent/eval/HARNESS_GUARDS.md`, and `ROUTING_EXPERIMENT.md`. `DATA_STATUS.md`, `AWS_MIGRATION_AUDIT.md`, and `AGENT_MODEL_UPGRADE_NOTES.md` at the root are historical with status notes.

---

## 16. Open work

### Open PRs

- **#29** `research-psps-reports`: the PSPS post-event reports dataset (section 5.5). Not merged. Adds files under `research/psps_reports/` only.
- **#74** `handoff-doc`: this document.

### Open issues

| Issue | Title | Notes |
|---|---|---|
| #34 | `jev_noop_diff` compares the wrong keys | The no-op diff cannot separate shadow effects from LLM variance as claimed. |
| #35 | Shadow: validate `TYPESAFE_API_KEY` at startup, not on the first call | Less urgent now that production uses the OpenRouter backend, but the same applies to a missing key there. |
| #36 | Shadow: the `_admitted` map is unbounded when no outcome arrives | A leak on error or disconnect paths. |
| #37 | Shadow: the daily cap counts user questions, not API calls | One question is up to four calls; the 500 cap in production is therefore about 2,000 calls. |
| #38 | Shadow: the JSONL log writer is not safe across workers | Single-worker requirement is undocumented. |
| #39 | Make `typesafe-sdk` an optional dependency | Every install pins it. |
| #41 | Test gaps from the PR 22 review | Partly addressed by PR #58; the port-8000 isolation item is done by PR #24's marker. |
| #44 | Router: route series-plus-total questions to the deterministic count-plus-series pair | Today they defer to the model. |
| #62 | Agent: Jev calls are not covered by `AGENT_ALLOW_REMOTE_PROVIDER` | A documented gap in `SECURITY.md`; decide whether shadow should require the opt-in. |
| #67 | Router: a tier ranking with a bare ignitions dataset refuses as `unsupported_ranking` instead of the tier clarification | Found in the review of PR #58. |
| #77 | Exact-match free-text filters can return 0 for a near-miss: EPSS `outage_type` and `cause`, CAL FIRE `incident_type` | Addressed in the `filter-followups` PR (open): stored values from `SELECT DISTINCT`, case-insensitive matching, 400 with close matches. |
| #78 | Multi-county CAL FIRE values like "Shasta, Tehama" are excluded by an exact county filter | Addressed in the `filter-followups` PR (open): 63 rows across 48 multi-county values now count in every listed county, with a caveat. No gold label stores a changed count and routing is unchanged. |

Closed since #30: #30, #31, #32, #42, #47 (PR #58); #33, #40 (PR #48); #52, #53, #54, #55, #56, #61 (PR #69); #60, #63, #64, #65 (PR #70).

### Good starting tasks for Stephen

1. **Reviewer B for the PSPS review queue.** Take `research/psps_reports/round3/review_reviewer_B.csv` on the PR #29 branch, follow `round3/REVIEW_GUIDE.md` (163 items, 40 of them shared with reviewer A, about 5.5 to 8 hours, decide before looking at the answer key), then run `round3/agreement.py` on the overlap. This gives the dataset its first second-reader accuracy figure.
2. **Decide log review** once the first production report exists: the production `jev_decide` log is the first clean test of the router and of decide mode, so the job is to read it (winner, why, wording, and the `decision_source` mix), not tune against it, and record the baseline numbers. Section 9.8 says what each record holds.
3. **Small doc fixes left after PR #73**: the status line at the top of `docs/DATA_CHANGE_HFTD_IOU.md` still says "Not yet applied on EC2" although its body records 2026-09-24; `docs/DEPLOY_RUNBOOK.md` section 5 still says not to set `decide` in production and its "Merge and deploy order" describes the PR #22 era; `docs/JEV_BACKLOG.md` still opens by saying the v2 and v3 files are not on main. Each is a few lines.
4. **Issues #77 and #78** are addressed in the `filter-followups` PR. Reviewing it is a good first look at how a data decision travels through the three services, the agent caveats, and the website.
5. **EPSS cause `EF`**: the one 2021 row coded `EF` is left alone because it could be "Equipment" (2022 wording) or "Equipment Failure/Involved" (2023 on); those two words are also separate causes. Deciding whether they are one cause needs a written rule from Michael ([`docs/DATA_CHANGE_CALFIRE_COUNTIES.md`](DATA_CHANGE_CALFIRE_COUNTIES.md)).
6. **Issue #44**: extend the deterministic count-plus-series pair to series-plus-total wording. Router work with a route report; the eval rows are named in the issue.
7. **Issue #67**: the tier ranking with a bare "ignitions" dataset. A one-function router fix with tests in `test_router_false_positives.py`.
8. **Website follow-ups you know best**: the non-ranking comparison and spatial-context views are still "not supported here yet" in `answerPanels.ts`; county and utility are not on the year bar. A model performance card for `GET /metrics` is in the open `metrics-card` PR (a 19th view, reachable from chat through the router-only `risk_metrics` tool); it is not on main until that merges. Remember the build rule: rebuild `docs/` in the same PR.

---

## 17. Glossary

- **Agent**: the FastAPI service on port 8004 (`services/agent/`) that routes one question to the read-only services and returns an answer with evidence, caveats, and views.
- **Ask, Ask panel**: the website's chat, which calls `POST /ask/stream`.
- **Backstop**: a router rule that fires before Jev is consulted (`BACKSTOP_RULES`): live, future, advice, city, HFTD constraint, and the unsupported topics.
- **Caveat, qualification**: a disclosure attached to an answer (section 8.3). The response field is `qualifications`.
- **Choice**: a Jev question with named options; the answer is one option with a probability.
- **cNHPP, NHPP, HPP**: the convolutional non-homogeneous Poisson process ignition-risk model on the 824-cell grid, and its two baselines. cNHPP versus NHPP is a statistical tie.
- **CPUC**: California Public Utilities Commission; also the utility-caused ignition dataset it publishes.
- **Decide mode**: `AGENT_JEV_MODE=decide`, where Jev decides answer, clarify, or refuse behind two gates with the router as backstop; Jev owns the disposition and the router owns the wording. On in production since 2026-09-24.
- **decision_source**: the field on every answer and routing event that says who decided (`backstop`, `jev`, or `router` with a why), shown as one line in the Ask panel's Tool chain.
- **Deterministic route**: a question the router answers with exact tool calls, no model.
- **Dev set**: `cases.json` plus `jev_paraphrases.json`, used for tuning.
- **EPSS**: PG&E's Enhanced Powerline Safety Settings (fast-trip) outages. PG&E only.
- **Evidence id**: the id of one tool execution; every claim and every view must cite one.
- **Gate**: a confidence threshold. Decline gate 0.8, answer gate 0.9, tool-pick gate 0.8.
- **Harness**: the code around the language model that validates, grounds, bounds, and checks (section 8).
- **HDW**: the Hot-Dry-Windy index playback cubes, 2020 to 2025, a surface approximation, not a forecast.
- **HFTD**: CPUC High Fire-Threat District, Tier 2 and Tier 3 polygons.
- **Holdout v1, v2, v3**: the three generated question sets; v1 and v2 are seen, v3 is frozen.
- **IOU**: investor-owned utility; the service territory polygons for PGE, SCE, SDGE, PacifiCorp, Liberty, BVES.
- **Jev**: TypeSafe's non-generative typed-decision model (System One API).
- **Label rule**: a written, dated, attributed rule that changes or widens a gold label (A to H).
- **Luna, Sol**: OpenAI's GPT-6 Luna (primary) and GPT-6 Sol (fallback) on OpenRouter, the agent's only language-model provider since PR #73.
- **Model path**: a question the router hands to the language model with candidate tools.
- **Noul**: a Jev yes/no question; the answer is a probability, and confidence is max(p, 1 - p).
- **OpenRouter**: the hosted API gateway used for Luna, Sol, and now Jev.
- **Payload hash**: the canonical JSON hash of a Jev request, pinned in tests so context changes are deliberate.
- **Platform, origin**: the `platform` remote is the team repo (`Woody-Zhu-Group/Wildfire-Platform`); `origin` is Michael's old fork. Push only to `platform`.
- **PSPS**: Public Safety Power Shutoff events.
- **Route report**: the count of eval questions whose path or rule changed under a router change.
- **Rule id**: the name of the router outcome (`route.rule`), section 4.
- **Shadow mode**: Jev runs in the background and is logged beside the router; answers do not change.
- **Slot**: a value the router extracted from the question: utilities, year(s), dataset, coords, county, counties, time resolution, start and end dates.
- **Slot planner**: the deterministic multi-entity planner behind `AGENT_SLOT_PLAN`.
- **Shoreline snap**: moving a city center point that sits just off a coastline into the one nearby territory or county polygon, within strict limits.
- **Spec, ComponentSpec**: a typed view the harness plans from tool results, with evidence ids.
- **Tool pick**: Jev choosing the first tool on the model path (`tool_pick` modes).
- **US ignitions, the sample**: the FireCastRL / IRWIN all-cause CONUS classification sample; not a census, not comparable to CPUC.
- **v3_hybrid**: the winning Jev question layout: small per-topic calls with the policy context plus the direct clarify-reason Choice.
- **View planner**: `services/agent/views.py`, the only thing that writes views.
- **Warehouse**: the PostGIS database (`wildfire` schema).
- **Worktree**: a separate checkout of the repo for one branch and one session.
