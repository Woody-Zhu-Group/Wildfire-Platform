# SUPERSEDED

This document is **superseded**. It assumed a **fixed Leaflet map** with widgets stacked in the Ask aside. The approved direction inverts that: the left surface is an agent-addressable canvas of fixed, schema-validated components; the Ask panel stays question / trail / prose / qualifications / audit. The map is one component (the existing Leaflet instance), not a permanent backdrop.

Do not implement from this file. See the agent-canvas plan (ViewPlanner first; no `render_view` tool; no second map; no void left pane; no CPUC+US overlay).

**Status (2026-09-23).** The body below is kept as the original proposal. What shipped instead:

- `frontend/` (local Historical Map): the planner in `services/agent/views.py` (`plan_views`) emits six component types (Map, TimeSeries, Comparison, RecordTable, StatCard, SpatialContext) that render on the left canvas, not in the Ask aside. Renderers and layouts are in `frontend/assets/js/sect-fasttrip-psps.js`; `frontend/assets/js/historical-agent-panel.js` applies `payload.views` and keeps the prose, qualifications, CSV download and collapsed audit `<details>` in the aside. The map is the existing Leaflet instance, reused as a component. See [`CANVAS.md`](CANVAS.md).
- `website/` (React workspace published to `docs/`): Ask appends grounded views as ordinary workspace panels (`website/src/answerPanels.ts`: map, risk/residual grid map, time series, ranking comparison, record table, stat cards). Non-ranking comparison and spatial-context views show a "not supported here yet" notice. A collapsed Tool chain disclosure (`website/src/ToolTrace.tsx`) covers the audit role.
- Not built as proposed: the aside widget registry, MapSyncNote, and a separate SpatialCounts widget (spatial summaries render as StatCards).

---

# Canvas-style agent panel: design proposal (no implementation)


Meeting feedback: the left panel should be **dynamic with canvas-style components**, rather than a fixed layout. This document is the design to review before any code.

## What “canvas-style” might mean

The phrase came from a meeting and is ambiguous. Three readings:

1. **Your read (this proposal’s default):** components appear and arrange from what was asked. A comparison surfaces a comparison component; a trend surfaces a chart; a count surfaces a stat card. Closest analogue: ChatGPT/Claude *artifacts*, or a dashboard that materializes widgets per answer.
2. **Cursor Canvas:** a live React surface beside the chat (`*.canvas.tsx`) for analytical artifacts. That is a Cursor IDE product, not something the public Historical Map page can host. Using it would put the panel in the IDE, not on the website.
3. **Infinite / freeform canvas:** pan-zoom cards on a 2D board (Miro, Obsidian Canvas). That fights the current map+filters layout and the single-exchange model.

If the meeting meant (2) or (3), this proposal is the wrong shape. Flag that before building. The rest assumes (1): **typed, question-driven widgets in the existing left panel**.

## Current structure (what we would be changing)

Today the Historical Map is:

- A **global controls strip** (year, county, utility, layer toggles, Download data).
- A **map card** with an optional **Ask data** aside (the agent panel).
- A **summary chart** under the map (time / bar / donut for the *map’s* filtered year).
- The agent panel is a **fixed stack**: question → progress trail → one answer card (prose + qualifications + map-sync note + audit). Map sync is a side effect, not a panel component.

The agent already returns `status`, `route`, `answer_text`, `qualifications`, `evidence[]` (tool + args + summary), and `artifacts[]` (ref + kind + raw payload behind `GET /artifacts/{ref}`). Selection does not require new backend fields.

The exchange model is **one question, no history**. Opening a new ask clears the previous result.

## Component types and response mapping

Keep a small catalog. Prefer one primary widget plus optional secondaries, not a kitchen-sink dashboard.

| Component | When it appears | Driven by |
|-----------|-----------------|-----------|
| **AnswerHero** | Always on `answer` / `clarification` / `unsupported` / `error` | `status` + `answer_text` |
| **QualificationStrip** | `qualifications.length > 0` | caveat engine, not the model |
| **StatCard** | Count question (`result_mode=count`) or risk scalar | `data_query_records` summary.total; `risk_forecast` risk |
| **RecordTable** | List / sample rows (`result_mode=records`) | artifact `data[]` (full), fallback evidence `records` (capped at 5) |
| **ComparisonTable** | Utility / region / period compare | `comparison_run` `results` or `period_a`/`period_b`/`delta` |
| **TrendChart** | Time series | `visualization_create` kind=`time_series` + artifact `buckets` |
| **SpatialContext** | Point lookup | `data_query_spatial` kind=`point` (IOU, HFTD, cell, county) |
| **SpatialCounts** | Polygon summary | `data_query_spatial` kind=`summary` `counts` |
| **MapSyncNote** | Map filters actually changed | existing `applyAgentView` result (not a second map) |
| **Unsupported / Clarify** | `status` in {unsupported, clarification, error} | `status` + `route.reason`; no data widgets |
| **AuditDrawer** | Always, collapsed | `route`, tools, origin; never competes with the answer |

**Not a panel component:** a second Leaflet map. The page already has the map. Agent map tools should keep syncing the existing map (year / layer / utility / county) and only show MapSyncNote in the panel.

**Download CSV** stays a panel action whenever a table-like widget or map-feature table exists. Count-only StatCards do not get a CSV (same rule as the UI quick win).

### Mapping rules (priority)

Selection is **status first, then tool + summary.kind / result_mode**, not the English question.

1. If `status !== answer` → AnswerHero + Unsupported/Clarify. Stop.
2. Else inspect successful, non-qualification evidence in order:
   - `comparison_run` → ComparisonTable (primary)
   - `data_query_rank` → ComparisonTable (primary; same bar + value table, `kind=ranking`)
   - `visualization_create` + `kind=time_series` → TrendChart (primary)
   - `visualization_create` + `kind=map` → MapSyncNote only; do not duplicate the map
   - `data_query_spatial` + `kind=point` → SpatialContext
   - `data_query_spatial` + `kind=summary` → SpatialCounts
   - `risk_forecast` → StatCard (risk)
   - `data_query_records` + `result_mode=count` → StatCard (count)
   - `data_query_records` + `result_mode=records` → RecordTable
3. If two primaries collide (e.g. count + trend from `count_plus_trend`), **stack**: StatCard then TrendChart. Do not merge them into one widget.
4. AnswerHero always wraps the stack (see below). QualificationStrip and AuditDrawer always follow.

Artifact kind is a confirmation signal (`data_query_records` payload vs `visualization_create` geojson), not the first selector. Tool + summary.kind is enough and is already on the SSE `answer` event.

## Layout: stack, replace, or beside the map?

**Stack inside the agent aside. Do not replace the map. Do not hide the summary chart by default.**

- The map and the year/layer controls remain the geographic context for the whole tab.
- Panel widgets **replace each other across questions** (single-exchange), and **stack within one answer** when the tools justify it.
- The existing Plotly chart under the map stays the *map-filter* chart. An agent TrendChart in the panel is the *question’s* series (correct buckets, correct filters). Today’s map-sync notice exists because those two series are not the same; a panel TrendChart is how we stop lying with the map chart.

Awkward but acceptable: two charts visible (panel trend + map year series). Label the panel chart “Asked series” and keep the map chart titled “Events over time” for the visible layers. Do not auto-hide the map chart; users still use it without the agent.

## Single-exchange model

There is no conversation history, so this is **not** a growing canvas of cards from Q1, Q2, Q3.

- Each Ask **clears** previous widgets (same as `clearExchange` today).
- No pin / compare-previous-answer unless we add history later.
- “Canvas” here means **adaptive composition per turn**, not an accumulating board.

If the meeting wanted a persistent board of past answers, that is a different project (chat memory + layout persistence). Call that out before building.

## Wrap vs replace the answer card

**Wrap, do not replace.**

The answer sentence is the primary content (the UI quick win already pushes this). Widgets are evidence, not a substitute for grounded prose and qualifications.

```
[ AnswerHero: the sentence ]
[ Primary widget: StatCard | ComparisonTable | TrendChart | … ]
[ Optional secondary widget ]
[ QualificationStrip ]
[ MapSyncNote ]
[ AuditDrawer ]
```

Replacing the answer with only a chart would hide caveats (`cpuc_utility_caused`, CAL FIRE missingness, attribute vs spatial) and break eval cases that assert on `answer_text`.

## What gets awkward in the current frontend

1. **The agent panel is a narrow aside** (~fixed width next to the map). Comparison tables and Plotly trends need horizontal room. Options: allow the aside to grow, or render wide widgets in the map-main column under the map header. Growing the aside is simpler and keeps “the answer” in one place.
2. **`historical-agent-panel.js` is a single renderer** (`renderResult` builds one `<article>`). A canvas panel wants a widget registry (`components/*.js`) keyed by the mapping table. That is a real split, not a CSS tweak.
3. **Map sync mutates global filters.** A county count will change the map’s county dropdown. That is useful, but a StatCard for Sacramento + a map still showing statewide layers (if sync fails) will disagree. Sync must include `county` (it today keys year / dataset / utility).
4. **Evidence summaries truncate records at 5.** Tables must fetch `GET /artifacts/{ref}` for the full page the tool actually returned (`limit` still applies). Count-only answers have no table on purpose.
5. **Deterministic vs model paths** produce the same evidence shape, so widgets should not care about `answer_origin`. Origin stays in AuditDrawer.
6. **CSS is a fixed column stack** (`.sfps-agent-panel` flex column). Adding typed blocks is easy; adding a 2D freeform canvas is not. Do not introduce drag-layout without a product reason.
7. **No build step.** New widgets should stay vanilla JS + Plotly (already loaded) or small DOM helpers. A React Canvas in this folder would be a new toolchain.

## Recommended first slice (when you approve)

Not in this pass. When building:

1. Extract a widget registry and render AnswerHero + StatCard + ComparisonTable + TrendChart from the existing answer payload.
2. Fetch artifacts for tables/charts; keep summary fallback.
3. Pass `county` through `applyAgentView`.
4. Leave map, layer toggles, and the under-map chart in place.

Out of scope until we know the meeting meaning: pinboards, multi-turn history, a second map, Cursor `.canvas.tsx` files for the website.

## Open questions for review

- Confirm reading (1) vs Cursor Canvas vs infinite canvas.
- Is a panel TrendChart allowed to coexist with the map chart, or should the map chart hide while the agent is showing a series?
- Should StatCard show the companion attribute/spatial pair as a two-number card, or keep that only in qualifications / ComparisonTable?
- Wide tables: grow the aside, or drop tables into the map column?
