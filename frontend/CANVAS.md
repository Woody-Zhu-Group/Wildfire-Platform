# Historical Map canvas

Working reference for the agent-driven left surface on the Wildfire & Outage Map tab. Not a changelog. The superseded aside-widget design is in [`CANVAS_PANEL_PROPOSAL.md`](CANVAS_PANEL_PROPOSAL.md).

Code: `services/agent/views.py` (planner + grounding), `frontend/assets/js/sect-fasttrip-psps.js` (host, layouts, renderers), `frontend/assets/js/historical-agent-panel.js` (applies `payload.views`). CSS/JS for this surface stays under `#sfps-tab-historical` / `#historical-canvas-host`. Planning Tool is out of scope.

## What it is

The left column is a **canvas**. The Leaflet map is one component among six, not a fixed surface the agent can only filter. Browse default is still today’s map plus “Events over time.” Agent-down is browse-only; `:8004` is not required to see the map.

| Type | Role |
|---|---|
| **Map** | The existing Leaflet instance (`#historical-map`). Year, layers, utility, county, extent. Same object through apply and restore (`_leaflet_id` must not change). |
| **TimeSeries** | One dataset’s asked series (daily / weekly / monthly). Replaces the browse chart; title **Asked series**. |
| **Comparison** | Bar + value table from `comparison_run` (`utilities` / `regions` / `periods`) or `data_query_rank` (`kind=ranking`). |
| **RecordTable** | Preview of matching rows (cap 25). Full export stays Download CSV in Ask. |
| **StatCard** | 1–3 scalars (`count` / `risk` / `spatial_metric`) from tool summaries. No artifact fetch. |
| **SpatialContext** | Point facts: coordinates, IOU, HFTD tier, county, grid cell. |

HDWI, day scrubber, and HFTD legend are Map chrome, not components. Max **two** visual components plus the StatCard strip. Planner order: stats → map or spatial → series, comparison, or records. CPUC and US ignitions never share one chart.

## How selection works

`ViewPlanner` (`plan_views` in `services/agent/views.py`) builds specs from **successful primary tool results**. The model does not pick components. There is no `render_view` tool.

Why: tool selection is already the weak path on `qwen3:4b`. A wrong tool call fails loudly. A wrong visual paints and looks authoritative. Selection therefore uses the same guarantees as tools (schema, validation, grounding) with a different producer: the harness, analogous to caveats.

Optional overlay: synthesis JSON may include `views`. The harness would ground that array and **discard it on any failure**, then use the planner. Missing `views` is not an error. Fail closed; do not block the answer on it. Today the overlay is not wired; the planner is the only producer. If you add it, keep that discard-on-failure rule.

Clarification / unsupported / error emit no views. Keep-until-replaced: a new Ask replaces the canvas when `views` arrive; do not flash empty.

## Grounding

Every parameter must trace to what the tools actually ran (executed arguments / summaries), not the question text. A chart of 2019 against 2024 evidence is a **grounding failure**, not a silent clamp to 2024. StatCard `value` must equal the cited number. `artifact_refs` must be in this response’s artifacts.

`view_status` on the Ask envelope:

| Value | Meaning |
|---|---|
| `applied` | Grounded specs; frontend paints them. |
| `planner_fallback` | Schema or grounding failed. Answer still stands. Canvas stays on the previous view (or browse). Do not paint a guessed visual. |
| `none` | No visual for this turn (no primary tools, or non-answer status). |

Frontend also refuses unknown `type`s. Busy state covers the in-flight request only.

## Layout

Map **collapses** (instance kept, card hidden) for TimeSeries, Comparison, RecordTable, SpatialContext, and remaining StatCard-only answers (risk, partial-year counts, spatial summaries). Year-scoped geographic counts keep the map. **Show map** restores browse (map + Events over time). Touching year / layer / county / utility while a non-map answer is up also restores browse. Do not silently retarget an agent chart to a query the tools did not run.

**Filter strip greys** while the map is collapsed (`data-map-filters="idle"`): muted controls, grey-blue background, caption “Inactive” followed by “these filters apply to the map” (the `::after` rule in `frontend/assets/css/sect-fasttrip-psps.css`). Browse and agent Map keep the strip bright. The strip still shows browse year/layers; greying is what stops that from reading as the answer’s filters.

**Stale banner** (“Map no longer matches this answer …”) fires when the visible map’s year/utility/county diverges from `view_scope`. Suppressed while Ask is in flight, while the map is collapsed, on StatCard-only, and on clarification / unsupported / error (those have not answered anything, so there is nothing for the map to be stale against). After Show map, it can appear if chrome no longer matches a successful answer.

**Split:** Map + trend (or map + another secondary) is `split-vertical` (62fr / 38fr rows in `frontend/assets/css/sect-fasttrip-psps.css`). Hide the browse year-chart so two charts cannot disagree. After collapse or split, `invalidateSize` then refit territory / auto-fit (see bugs below).

**Width:** Comparison, TimeSeries, and RecordTable span the canvas (space left of Ask). SpatialContext also spans; its five facts lay out **horizontally**. StatCard is a strip above the primary visual: one card sizes to its content (capped at about a third of the row); two or three share the row. Year-scoped geographic counts emit Map as well as the strip; the count tool’s filters retarget the existing Leaflet layer; no second agent call. Partial-year counts emit TimeSeries for the same window (daily if ≤62 days, otherwise monthly clipped to start/end) via `GET /time-series`, not a second agent tool. A zero-event month is a flat line, not a missing chart. Full-year counts keep Map only. Risk scalars and spatial-summary cards stay map-less. Map-less layouts hug content; Ask is taken out of the flex line (`position: absolute`) so it does not leave a tall empty column beside the card. Below 1400px Ask stays in flow under the canvas. Count-derived maps fit the filtered points (`auto_fit`) rather than the utility territory bbox.

Location phrasing (`where`, `see where`, `locations of`, `map of`) produces a Map even when the question also sounds like a count.

## Null handling

Nulls are never zeros. Unavailable is a first-class result, not an omitted category.

- **Comparison bars:** present values are solid bars. Missing values are a **short hatched grey stub** with one `"no data"` label inside the stub. Hover is the reason string, not the stub height. The table shows a dash placeholder (U+2014) plus the reason (e.g. EPSS is PG&E-only).
- **Record cells:** missing acres/containment etc. show the same dash placeholder, not blank or `0`.
- **SpatialContext:** HFTD absent is italic **none**; missing evidence is **unresolved**. Those are different.

A missing bar and a zero-height bar look identical. If Plotly drops the category or you plot `y: 0` / `y: null` without a visible placeholder, a two-utility comparison reads as one bar, the exact failure the null-with-reason rule exists to prevent.

## Bugs that recur

**Plotly drops null categories.** `y: null` removes the x label. Keep the category (`categoryarray`) and draw the hatched stub instead. Do not use annotations *and* bar `text` together (double “no data”).

**`textContent = ""` on a Plotly host** destroys the SVG and leaves `.data` intact, so later `Plotly.react` / `Plots.resize` thinks a plot is there. Error paths must `Plotly.purge` first. Same class of bug: emptying the node to show a string.

**`fitBounds` then `invalidateSize`.** Fitting while the map is still at browse aspect ratio, then calling `invalidateSize`, preserves the wrong zoom. Sequence: layout change → `invalidateSize` → settle (~180ms) → refit `territory` / `auto_fit`.

**HDWI caption coupled to contour drawing.** Caption stayed on “Loading…” because UI updates ran only at the end of `drawWeatherContours()`, which returned early when Fire-Weather was off (agent exclusive layers). Update caption/scrubber when the year JSON is ready; gate contours on the layer.

Visual checks: screenshot the rendered page. An element existing or a CSS property being set is not verification. If you cannot inspect pixels, say **implemented, not visually confirmed**. Distinguish a live Ask from applying views through the canvas API.

## Known rough edges

From the post-six-component pass. Closed items stay listed so they are not reopened.

**Coded, not live-checked**

2. Map + RecordTable / SpatialContext (`split-vertical` + point pin). Planner can emit both; the usual questions do not, so the pin path was not exercised.
4. StatCard + SpatialContext together (`coordinate_to_risk`). Wired, not exercised.
5. Stale artifact refetch (404 on `GET /artifacts/{ref}` → grounded params to viz / data_query, with a one-line refresh note).
6. EPSS / PSPS / US Ignitions column sets. Defaults exist; only CAL FIRE was shown live. (CPUC lat/lon as a Location column did land.)
7. Map `highlight_ids` is in the schema and skipped in `setMapParams`. A records answer does not light matching points.

**Closed: do not regress**

1. Truncation copy: table and answer agree when the set fits (`11 matching records`, not “representative”).
3. Comparison null bars: hatched stub, not a missing category.
8. Filter strip vs answer: idle greying while the map is collapsed.

## Verification order

1. **Planning Tool first.** It broke twice from work scoped to the map tab. It depends on sibling `dataset_demo/assets/website_plots` served by `python frontend/serve.py` (port 8765, mounts `/dataset_demo/`). Plain `http.server` from `frontend/` 404s the plots. Confirm three method maps at natural size 1571×1785, not “No plot images available.”
2. **Browse with the agent down.** Year, county, utility, layer toggles, HDWI scrub, click-to-inspect. Leaflet stays one instance.
3. **Agent cases** with `:8004` up. Live Ask for anything visual. Suggested: map (“I'd like to see where PG&E's CPUC ignitions happened in 2024”), count+map (“How many CPUC ignitions did PG&E have in 2024?”: map plus compact strip, auto_fit to points), partial-year count+series (“How many CAL FIRE incidents were in Sacramento County in August 2023?”: daily Aug 1–31, including a flat zero month), TimeSeries (CAL FIRE monthly 2024), Comparison (EPSS PGE vs SCE 2024; check the SCE hatch), RecordTable (CAL FIRE Sacramento 2024), SpatialContext (`38.58,-121.49`).

Serve: `python frontend/serve.py`. Agent `:8004`, visualization `:8002`, data_query `:8000`, comparison `:8003` as needed.
