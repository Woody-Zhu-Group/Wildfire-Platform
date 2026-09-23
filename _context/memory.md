# Session memory (Wildfire Services)

## 2026-08-08 — National ignition layer (`us_ignitions`)

- Extract: 33,471 positives → 343 full-sentinel (all negative; 0 positive) → 0 partial-sentinel → 14 exact dedupe → **33,457** loaded.
- Near-dupes at 4dp after exact dedupe: **1 group / 1 extra row** (exact UNIQUE leaves almost nothing behind).
- Table `wildfire.us_ignitions`; data_query `/us-ignitions`; viz `dataset=us_ignitions` teal `#0f766e`.
- Frontend Historical Map toggle off by default + info strip; auto-CONUS only at default CA view with no utility/county filter; “Zoom to national extent” on demand; restore CA on toggle-off only if auto-zoomed.
- Gitignore: `Wildfire_Dataset.csv` + `us_ignitions_extracted.csv`.
- **Geographic skew (state PIP):** CA **13,432 / 33,457 = 40.1%** overall; **2,225 / 3,789 = 58.7%** of 2024. Census West ≈73% / ≈78%. Caveat in info strip + `sample_geography` API meta. Script: `data/north_america/_us_ignitions_state_breakdown.py`.

## 2026-08-08 — US Wildfire Dataset inventory (FireCastRL / Kaggle)

- File: `data/north_america/Wildfire_Dataset.csv` (1.13 GB, 9,509,925 rows). Classification corpus: 126,795 × 75-day windows; Wildfire Yes/No only; no IRWIN IDs.
- Positives: 33,471 sequences (15 Yes days); negatives: 93,324 (synthesized — not panel zeros). Paper claimed 50,720/76,080 — CSV counts differ.
- Sentinel 32767 on 25,725 rows (=343 full sequences). Temps are Kelvin despite Kaggle saying °C.
- Covariates: sph/vs/fm100 match CA; tmmn/tmmx≈TMP; has vpd; missing NDVI.
- Cannot reconstruct cNHPP panel from this alone — need raw IRWIN + full weather grid. National 0.24° ≈25k cells / ~100M cell-days; GRIDMET 4km ≈900k cells / ~3.6B cell-days.
- Canvas: us-wildfire-dataset-inventory.canvas.tsx

## 2026-08-07 — Frontend API wiring + comparison service

- Slim `frontend/` copy of dataset_demo (no website_plots; sibling path for Planning Tool). Historical Map → visualization :8002. HDWI stays on weather_anim.
- Viz enhancements: CORS; EPSS `include_outages`; event-detail circuits→outages, psps→affected_circuits.
- CAL FIRE: verified with incident_type=all, then switched api-config to wildfire default ("").
- Verification 2024: only intentional DIFF is EPSS 2788 vs 2787 (dedupe). See frontend/VERIFICATION.md.
- Comparison service :8003 — compare-utilities/regions/periods; null+reason never zero for N/A.

## 2026-08-09 — Agent prototype staged baseline

- Added `services/agent/` (:8004): conservative deterministic router, six grouped read-only HTTP tools, Pydantic validation, response-contract checks, bounded retry loop, in-memory artifact refs, grounded output guard, deterministic caveat engine, README + SECURITY.
- Every utility-scoped CPUC ignition count is paired with same-period attribute/spatial values for any utility. CAL FIRE missingness, US-sample/CA-skew, and EPSS PG&E-only caveats are harness-injected; companion failure suppresses the answer.
- Eval runner: 27 cases, selectable model/thinking/output subsets, gzip raw logs + hashes, checkpoint resume, and 50% model-tier stop gate. `tool_choice` absence is prominent; tracks no-tool responses separately from valid direct answers.
- Staged result (`qwen3:4b`, thinking off, prompt): stopped after 26/27 when 5/6 planned model-tier cases had run and 50% became unreachable. Deterministic routing **100% (21/21)**; model-tier routing **0% (0/5)**; schema first/eventual **0%/0%**; 15/15 model turns emitted no tool call, 0 valid direct-answer attempts; deterministic required caveats **100%**, aggregate caveat rate **66.7%**; refusal/clarification **100%**; injected recovery **0%** because no tool call occurred. Model-call p50/p95 ≈136s/207s; cumulative measured baseline runtime ≈2,211s.
- Ollama OpenAI-compatible endpoint lacks `tool_choice`. Installed Qwen3 template forced thinking despite `reasoning_effort=none`; thinking-off uses an identical-weight template alias with a pre-closed thinking block, but 4B still emitted long deliberative prose rather than tool calls. Full 8-cell matrix was correctly not run.
- Report: `services/agent/eval/REPORT.md`; canvas: `agent-baseline-feasibility.canvas.tsx`; tests: `tests/agent/test_harness.py` (6 passed). Obsidian MCP discovery was unavailable at session end, so this local memory file was updated directly.

## 2026-08-09 — Tool-call isolation corrected baseline interpretation

- Minimal direct `qwen3:4b` tool test (`get_current_year`, no args) succeeded on both Ollama endpoints: OpenAI `/v1/chat/completions` produced `finish_reason=tool_calls` in 60.9s (373 completion tokens); native `/api/chat` produced `message.tool_calls` in 55.3s (373 eval tokens).
- The template-only `qwen3:4b-agent-nothink` alias also called the minimal tool (38.3s), though it emitted the same call twice. The alias is not suppressing tool calling.
- Directly replaying the agent's exact static system prompt + six compact tool schemas (outside the orchestrator) reproduced the failure: 1,470 prompt tokens + the full 600-token completion cap, `finish_reason=length`, zero tool calls. The model spent the completion re-reading/second-guessing the catalog.
- Conclusion: the 0/5 model-tier baseline is **not valid evidence of a qwen3:4b capability ceiling**. It is primarily a harness prompt/catalog/token-budget integration failure. Native vs OpenAI-compatible transport does not fix it; both work in isolation and behave similarly.

## 2026-08-11 — Hang root cause + flake rates

- Sacramento County 2024 hang: tools succeed (CAL FIRE count=11), then under default `structured_mode=prompt` synthesis does not return within 180s. Original hang = unbounded wait (HTTP timeout up to 900s). Fix that ends the user hang: `synthesis_timeout` + `synthesis_fallback_to_tool_summary`. Not num_ctx (was 32768; tiny ~1.4k prompt chars). Constrained mode returns tokens but grounding retries can still fall back.
- Flake probe (10x, constrained, seed=42): `cpuc_vs_us` **10/10 fail** (always skips US companion); `count_plus_trend` **0/10 fail**. Artifact: `services/agent/eval/flake_probe_results.json`.
- Invented-utility strip surfaces via `utility_filter_stripped` in `payload.qualifications`; panel renders Qualifications section below the answer.

## 2026-08-11 — Synthesis prose fix

- Grounding: allow any evidence-JSON number; normalize `1,234` → `1234` (was rejecting Sacramento briefs).
- Default `AGENT_STRUCTURED_MODE=constrained`; prose-first synthesis prompt; exploratory sample enrichment + caveats in prompt.
- Synthesis thinking supported but CPU timeout >180s → local default `AGENT_SYNTHESIS_THINKING=false`.
- Eval headline: `synthesis_fallback_rate`. Probe: Sacramento + CPUC 2023 produced policymaker briefs with `answer_origin=model`; `cpuc_vs_us` still often skips the US tool call (routing), though synthesis prose is honest about missing US evidence.

## 2026-08-11 — Full suite + cpuc_vs_us diagnosis

- Suite `synthesis-quality-20260811`: **55/57**, model completed **8/11 (72.7%)**, harness rescued **3/11 (27.3%)**. Failures: `cpuc_vs_us`, `collision_wrong_kind_model_repair`.
- `cpuc_vs_us`: baseline always one CPUC call then synth; explicit dual-dataset hint → two calls but both fail args (`county=null`, invented tier). Intent promptable; success not. Updated `PI_SUMMARY.md`.

## 2026-08-11 — Caveat path-independence

- 93.1% caveat rate was not “model path skips caveats”: `cpuc_vs_us` had CPUC-only evidence (early synth) so `us_ignitions_sample` never had a US read; `collision_wrong_kind_model_repair` had zero tools → empty quals.
- Fix: single `collect_qualifications` in `ask()` after successful tools; CPUC↔US questions companion-fetch missing dataset; slot-grounded `comparison_run` when model stalls with full utility+year slots.
- Re-run `caveat-path-independent` (`cpuc_vs_us`, `collision_wrong_kind_model_repair`): caveat_surfacing_rate **100%**.

## 2026-08-11 — Synthesis grounding vs caveats

- Observed fallbacks were mostly **not timeouts**. CPUC failed grounding with empty `unsupported_numbers`/`quantity_mismatches` → bad/missing claim citations after exploratory enrichment (2 evidence IDs). Sacramento sometimes `model_error` (empty), sometimes citation/number path.
- Fixes: caveat numbers in allow-list; slim synthesis evidence (no `event_date` day tokens); neutralize prompt example numbers; **citation_repaired** when numbers pass but claims/IDs are wrong (stop falling back to tool summary). Eval `expected_answer_origin=model` on `model_cpuc_tell_me_about_2023` + `model_sacramento_tell_me_about_2024`; runner scores `answer_origin_pass`.
- Confirm run `synth-origin-confirm`: both **origin=model**, synthesis_fallback_rate **0**, CPUC used `citation_repaired`.

## 2026-08-13 — County inference + viz/UI + canvas proposal

- CPUC `county` is load-time Census TIGER PIP (`wildfire.counties`, CA only). Live DB: **3743 resolved / 2 outside / 3745**. Loader tags on every reload. `/ignitions?county=` and `/spatial/point` return county (`county_unavailable=false`). Agent no longer refuses CPUC county questions; US ignitions still unsupported. Eval case `dq_cpuc_sacramento_county_2023`.
- Map hues: CAL FIRE `#b91c1c` (red, acres via size; indigo reverted — too close to PSPS blue); HFTD both tiers `#d97706` with opacity; CPUC orange / US teal / EPSS purple / PSPS blue unchanged. HDWI stays a sequential danger scale. The meeting constraint was red+green as a value judgment, not “no red.”
- Agent panel: CSV when tabular tool data exists; origin badge moved into audit; answer text is the primary read. Ghost-button `margin-top` reset in flex rows.
- Canvas-style panel **not built**. Design in `frontend/CANVAS_PANEL_PROPOSAL.md` (typed widgets from tool/status; wrap answer card; stack in aside; single-exchange replaces, does not accumulate). Meeting phrase may mean Cursor Canvas or a freeform board — flagged there.
- Eval `county-inference` (59 cases, qwen3:4b / thinking off / constrained): **57/59**. Deterministic routing **100%**. New CPUC Sacramento 2023 case **passed** (count 3). CPUC Sacramento August 2023 now **answers** (count 1) instead of refusing. Failures unchanged in kind: `cpuc_vs_us` (one CPUC tool, US came in as companion) and `model_sacramento_tell_me_about_2024` (extra dataset reads). Untagged CPUC points: id 671 PGE 2021-05-16 (38.82, −119.65, Nevada side of the CA border) and id 3655 SDGE 2021-07-14 (32.50, −116.56, south of the US–Mexico line).

## 2026-08-13 — Agent canvas ViewPlanner (slice 2, no UI)

- `frontend/CANVAS_PANEL_PROPOSAL.md` superseded: left canvas-host, not widgets in the Ask aside; no `render_view` tool; no second Leaflet; no CPUC+US overlay; no void left pane.
- Harness `services/agent/views.py` plans grounded specs from successful primary tools. `AskResponse` gained `views`, `view_status` (`applied` | `planner_fallback` | `none`), `view_scope` (year/utility/county/dates). Invalid specs are dropped (`planner_fallback`); the answer still stands. Not on `AgentAnswer`.
- Stale-view helper `scope_diverges` is ready for later UI: when status is `none`/`planner_fallback` and answer year/utility/county differs from the canvas, mark it (label or dim). Busy state only covers the in-flight request.
- Narrow eval `view-planner` (11 cases): **11/11** `view_pass`, `view_accuracy` 1.0. Types: count→stat_card, map→map, trend→time_series, point→spatial_context, risk→stat_card, list PSPS→record_table, compare→comparison, unsupported→[]/`none`. Qualification companions do not emit views. Run: `services/agent/eval/runs/qwen3-4b__thinking-off__constrained__view-planner/`.
- Next slices: canvas-host layout, map-as-component, StatCard + TimeSeries, then stale chrome. Do not start until this slice is reviewed.

## 2026-08-13 — Live /ask + canvas-host layout (no Map-as-component yet)

- Recycled agent `:8004`. Live `POST /ask` for PGE 2024 ignitions: `view_status=applied`, one `stat_card` 532, `view_scope` year 2024 / PGE. Specs are on the HTTP envelope, not only in-process eval.
- Canvas host wraps map + browse chart (`#historical-canvas-host`, `data-layout=browse`). Ask stays 360px. Default empty state is today's map+chart, stats slot hidden, no void. Agent views are not wired.
- `relayoutCanvas` + ResizeObserver: Ask open 1047→807px then back; `setCanvasLayout('split-vertical')` fires `layout:split-vertical:settle`. Not only Ask-open.
- Browse E2E (Ask closed): year 2025→2024, Sacramento + PGE, US/HFTD toggles, weather scrub to June 29 2024, PSPS polygon popup (PG&E 12/09/24). Restored to 2025 / all / Ask closed / browse.

## 2026-08-13 — Planning Tool 404 on 8765 (not canvas DOM)

- Symptom: Planning Tool “No plot images available.” Canvas-host layout did **not** change Planning Tool markup or shared `.sfps-split` CSS.
- Cause: `python -m http.server 8765` from `frontend/` 404s `/dataset_demo/assets/website_plots/manifest.json`. `data-plots-base="../../dataset_demo/..."` collapses above the origin to `/dataset_demo/...`.
- Fix: `python frontend/serve.py` (default 8765) serves frontend at `/` and mounts sibling `dataset_demo` at `/dataset_demo/`. Canvas CSS scoped under `#sfps-tab-historical`.
- After every later canvas slice: open Planning Tool first and confirm the three method maps render.

## 2026-08-13 — Map-as-component (report before StatCard)

- Map is a ViewPlanner component. `applyAgentView` is `setMapParams`. Leaflet `_leaflet_id` stayed 3 across agent apply and user takeover. `highlight_ids` remains unimplemented.
- Agent map → `viewSource=agent`, writes year/layers/utility/county/extent through existing controls (exclusive layers). User chrome → `viewSource=user`, map follows immediately. Non-map view + chrome restores browse layout without retargeting an agent chart. Agent down = browse only.
- Planner extent: `us_ignitions` + no CA filter → `conus`; county → `auto_fit`; utility → `territory`. Tests 11 passed.
- Stale chrome when `view_scope` diverges: “Map no longer matches this answer (2024, PGE · showing 2025, PGE).”
- invalidateSize: Ask open 1047→807px; Ask close back; `map:setParams:settle` recorded. Serve with `python frontend/serve.py`.
- Planning Tool three method maps still render after this slice.

## 2026-08-13 — HDWI “Loading…” caption never cleared

- Not a `serve.py` path issue. `GET /assets/data/weather_anim/weather_anim_YYYY.json` is 200 from local `frontend/assets/data/weather_anim/`.
- Caption stayed on “Loading…” because `updateWeatherAnimUI()` ran only at the end of `drawWeatherContours()`, which returned early when Fire-Weather was off (agent exclusive layers, or the user toggle). Year change still set the loading text.
- Fix: update caption/scrubber as soon as year JSON is ready; contours stay gated on the layer. Scrub to 180 → June 29 2024; day 0 vs 180 marker counts differ (0 vs 6 clusters). Planning Tool maps still 1571×1785.

## 2026-08-13 — StatCard strip

- Canvas host `#historical-canvas-stats`: 1–3 cards, ~88px, above the primary. Kinds `count` / `risk` / `spatial_metric`. Values from the spec (tool summary), no artifact fetch.
- Spatial summary → three cards (CPUC ignitions 536, EPSS 2,787, CAL FIRE 360 for PGE 2024). Planner caps stats at 3. Tests: `tests/agent/test_views.py` 12 passed.
- StatCard-only answers set `data-layout=stats`, collapse the Leaflet viewport (Ask header stays), hide the browse chart. `_leaflet_id` stayed **3**. Count (532) no longer leaves an idle map slot.
- User chrome / map `setParams` restores browse and clears the strip. Browse with Ask closed: stats hidden, map 1047×380.
- Recycled agent `:8004`. Planning Tool maps still 1571×1785. Next slice: TimeSeries (not StatCard follow-ups).

## 2026-08-13 — StatCard collapse (whole card)

- `data-layout=stats` hides the entire canvas stage (map card header included), not only the Leaflet viewport. Host `align-self: flex-start` so it does not stretch into an empty white region. Leaflet stays in the DOM (`_leaflet_id` 3).
- Stale banner is suppressed while the map is collapsed.
- **Show map** under the strip restores browse. Year/layer chrome still restores too. Stale banner can appear after the map is visible again.

## 2026-08-13 — TimeSeries (asked series)

- v1: one TimeSeries component, one dataset. Planner still drops CPUC vs US overlay; also keeps only the first series when two same-family series arrive. Tests: `tests/agent/test_views.py` **14 passed** (map+series keeps both; two CPUC series keep first).
- Frontend fetches `GET /artifacts/{ref}` (full `/time-series` payload with `buckets`). Chart title **Asked series**; browse Time/Bar/Donut hidden. Browse “Events over time” is not shown at the same time, so the two charts cannot disagree.
- TimeSeries-only (eval `trend_calfire_monthly_2024`): `data-layout=series`, map card hidden, Show map visible. Live: 12 monthly CAL FIRE buckets, yMax 182, artifact `GET` 200, `_leaflet_id` stayed **3**.
- Map + trend: `data-layout=split-vertical`, row tracks **58% / 42%** (398px / 288px of the stage). Leaflet `invalidateSize` on the split; map **1428×198** (reduced from browse 380px height). CPUC monthly series 12 buckets. Show map restored browse **807×380** (Ask open), title back to Events over time.
- Live “map and monthly trend” questions still hit the deterministic **map** rule first (one tool). The split is what `applyCanvasViews` does when the planner emits both specs.
- Recycled agent `:8004`. Planning Tool maps still 1571×1785. Next slice: Comparison (not TimeSeries follow-ups).

## 2026-08-13 — CPUC vs CAL FIRE vs US ignitions comparison (research memo)

- Memo: `docs/dataset-comparison-cpuc-calfire-us.md`. Script: `analysis/compare_cpuc_calfire_us.py` (+ JSON results). No product/caveat-code changes; no commit.
- Cross-checks held: US 33,457; PGE 2024 attr 532 vs spatial 536; CAL FIRE null type 1,234 / null utility 282; CPUC county 3743/2.
- US CA via `ST_Covers` vs `wildfire.counties`: **13,413 / 33,457 = 40.09%** (state-PIP was 13,432; 2024 CA 2,225 either way). Loaded table is positives only (controls dropped at extract).
- Complete overlap years 2020–2024: CPUC 3,190; CAL FIRE default Wildfire/Fire 1,323; US CA 9,880. US/CAL FIRE count ratio **7.47** — not a sampling fraction. 2025 US is Jan 1–Feb 5 only.
- Proximity (geography ST_DWithin, CAL FIRE `date_only_created`, default Wildfire/Fire): CPUC→CAL FIRE 1.4–5.6% across 1/5/10 km × ±1/3/7 d; US CA→CAL FIRE 0.5–3.0%. Both directions unmatched. Matching is proximity, not identity (no shared IDs).
- CAL FIRE is an incident catalog (median 53 acres); 1,216/1,234 untyped rows are 2013–2018; 825/2,509 default Wildfire/Fire have `is_calfire_incident=false` and still enter API default. 2024 default jump 133→611: see 2026-08-14 memo (map-feed posting rate, not a 4.6× fire year).
- County ratios do not hold still (pre-specified: Sacramento, LA, Butte, Lake, Imperial, SF). LA US:CAL FIRE ≈29; Sacramento CAL FIRE > CPUC = US.
- Caveat recs: keep “not comparable” / sample-not-census; **do not** attach a US sampling rate vs CAL FIRE; add measured proximity and the 2013–2018 untyped hole. US ignitions not for cNHPP.

## 2026-08-14 — CAL FIRE 2023→2024 jump

- Memo: `analysis/calfire-2024-jump.md`. Script: `analysis/calfire_2024_jump.py`. No caveat-code change; no commit. Task 2 (cNHPP harness) not started — report Task 1 first.
- Jump is in the incident-map feed (`mapdataall.csv`), not loader/`incident_type`. HTML archives match warehouse (2023: 133; 2024: 611 default / 612 all-dated).
- Official Redbook: 7,386 fires / 332,822 acres (2023) vs 8,110 / 1,077,711 (2024). Count +10%; acres 3.2× (Park Fire). Warehouse acres track Redbook (~97% / ~95%). Map listed 1.8% of 2023 fires vs 7.5% of 2024.
- H1 4.6× fire-count year: rejected. H4 type population: rejected (all-dated jumps with default). H5 scraper: rejected (first CSV 2026-07-23 already 133/612; loader inserts all rows). H2 pruning: does not explain the cliff (2025 still 555; 2023 rows still live). H3 posting mix: supported (median acres 70→43; n<100 acres 71→422); large fires also rose (n≥1000: 18→54).
- Agent caveat when a CAL FIRE count crosses 2023–2024: this is the map feed, not the Redbook census; do not read 133→611 as California fire occurrence.

## 2026-08-14 — CAL FIRE map-feed caveat coded

- `calfire_map_feed_counts` in `services/agent/caveats.py` (path-independent, after the primary-tool loop). Separate from `calfire_missingness`.
- Attaches when a primary CAL FIRE answer spans 2023–2024 **or** compares CAL FIRE **counts** across any years. Qualification-call metadata fetches are ignored.
- Does **not** attach on single-year 2023 or 2024 counts, August 2023 Sacramento, or monthly 2024 TimeSeries. Acres 2021 vs 2022: no. Acres 2023 vs 2024: yes (spans the boundary) but the text says acre comparisons remain valid (95–97% vs Redbook); only count-based year-to-year comparisons do not.
- Text: fire.ca.gov incident-map feed ≠ Redbook; posting threshold dropped 2024 (median 70→43; sub-100-acre 71→422); 133→611 is posting, not occurrence.
- Tests: `tests/agent/test_synthesis_and_caveats.py`. Guard note in `HARNESS_GUARDS.md` #9. Do not add to 2024-only / 2023-only eval `required_caveats`. Do not run the 27-case matrix.

## 2026-08-14 — Task 2 risk service state (report only; no code)

- cNHPP is **built and wired at cell/day**: params load, `:8001` healthy (`model_loaded=true`), `GET /predict?cell_id=400&date=2024-08-15` → risk **0.00465**, agent `risk_forecast` calls it, canvas is a bare StatCard. Not integrated for county/utility/place, tomorrow, regional aggregation, or interpretable risk.
- **Degraded** = startup `load_fitted_model()` failed (`_model is None`); `/predict` then 503. Agent health becomes degraded; frontend “some backends degraded.” Live check today: **ok**, not degraded. POST `/predict` is 405 — tool uses GET.
- Params `services/risk_forecasting/artifacts/cnhpp_params.npz`: 2084 bytes, current at `bf7ba6c` (not the first `6b58596` 1462-byte commit). Post–Dec 2020 weather drop + val-selected xi. Keys: xi=0.2, beta (6,), means/stds (5,), train 2020–2023, val 2024. β ≈ [-5.244, 0.549, 0.229, 0.091, 0.364, -0.015]. Grid 824 cells; W nnz 3922, ~3.76 avg neighbors. Cell 400 at 37.63, −120.03.
- Agent: `RiskForecastArgs` is `cell_id` 0–823 + `date` (+ optional lookback). No county/utility/latlon/aggregation. Routes: `cell_risk`, `coordinate_risk_chain` (spatial → cell), “which utility is riskiest?” → clarification. Deterministic prose: “Cell N risk on DATE: λ (lookback D days).”
- Canvas: StatCard kind `risk`, scientific notation if &lt;0.01, map collapsed, no percentile/history. `coordinate_to_risk` also emits SpatialContext (wired, not live-checked).
- Coverage: weather/veg through **2025-12-31**; 2026 dates fail “no weather rows.” Dec 2–31 2020 excluded. All four suspected gaps **confirmed**.
- Do not build place-based risk / aggregation / eval / canvas until this report is accepted.

## 2026-08-14 — Place-based cNHPP risk (approved build)

- `GET /predict` accepts exactly one of `cell_id` | `lat`+`lon` | `county` | `utility` (PGE/SCE/SDGE) plus `date`. Place cells from PostGIS (`place.py`). Batch `predict_grid` scores all 824 λ in one forward.
- Primary `risk` is P(≥1) = `1 - exp(-sum(λ))`; `expected_count` = `sum(λ)`. Cell 400 on 2024-08-15: λ=`0.00464752`, P(≥1)=`0.00463674`. Sacramento County: 11 cells, P(≥1)=`0.06625`, expected=`0.06854`.
- Coverage end from files: **2025-12-31**. 2026 → HTTP 400 `Covariates end 2025-12-31 and no forecast ingestion exists...`. Dec 2–31 2020 → corrupt HRRR message. No live HRRR.
- Percentiles: local month-history P(≥1) (August 2020–2025, n=186); statewide = place mean λ vs 824 cells that date. Year covariates + λ vectors cached in-process.
- Agent: `RiskForecastArgs` exactly-one-place; routes `county_risk` / `utility_risk`; 3 StatCards (P≥1, local %, statewide %); `unit=percentile` renders as integer percent. Caveats `cnhpp_grid_resolution`, `cnhpp_contagion_tie` on every successful `risk_forecast`; `cnhpp_cell_461` only when 461 is scored.
- Eval IDs: `risk_county_date`, `risk_utility_date`, `risk_out_of_coverage`; `risk_cell_date` required_caveats updated. Did not run the 27-case matrix.
- Tests: 50 passed (`test_risk_forecasting`, `test_synthesis_and_caveats`, `test_harness`, `test_views`). Planning Tool: `serve.py` serves three method maps at **1571×1785** (HTTP); rendered tab screenshot unverified (browser MCP could not open a tab).

## 2026-08-14 — Live HRRR ingestion size (not building ingest)

- No in-repo HRRR scraper. Runtime reads static `grid_weather_YYYY.csv` + `daily_gridded_CA_YYYY.nc` (gitignored). Raw `California_HRRR_daily_*.csv` lived on a Drive folder; `prep_hrrr_grid.py` is a standalone local extract (01Z convention, TMP median 200–330 K).
- Last scoreable date **2025-12-31**. 2020 ends Dec 1 (corrupt export dropped). Veg is lab/Marco NetCDF, not HRRR — no tomorrow NDVI/fm100.
- Manual batch refresh: **days** (ops habit) if the external CSV extractor still exists.
- Automated daily **analysis** ingest: **1–2 weeks** (new GRIB fetch + grid extract + cron; not a scraper tweak).
- Veg automation: **weeks+** (separate product).
- Tomorrow/this-week **forecast**: **genuinely hard** — forecast GRIBs ≠ analysis used in training; veg lag; 90-day window; model is a research baseline (cNHPP/NHPP tie, SPFH semantics). Separate initiative, not an extension of current Wildfire Services.
- Place-based UI on this stack remains a **historical replay tool** until Bucket D exists.

## 2026-08-14 — Place-based cNHPP risk shipped (historical only)

- `GET /predict` accepts exactly one of cell_id / lat+lon / county / utility. `risk` is P(≥1) = `1-exp(-∑λ)`; `expected_count` is ∑λ. Local month-history percentile + statewide mean-λ percentile. Coverage after last weather day: `Covariates end 2025-12-31 and no forecast ingestion exists.` Dec 2–31 2020 names the corrupt export.
- Sample 2024-08-15: cell 400 P(≥1)≈0.00464 (λ=0.00465), local 39th / statewide 72nd. Sacramento County 11 cells, P(≥1)≈0.066, local 78th / statewide 90th.
- Agent: `county_risk`, `utility_risk`; 3 StatCards (P≥1, local %, statewide %); caveats `cnhpp_grid_resolution`, `cnhpp_contagion_tie`, `cnhpp_cell_461` if scored. Eval: `risk_county_date`, `risk_utility_date`, `risk_out_of_coverage`. View types are three `stat_card`s (plus `spatial_context` on `coordinate_to_risk`).
- Planning Tool after `?v=place-risk`: three method maps rendered, natural size **1571×1785**. Restart `:8001` if it still has the old process. No commit. No 27-case matrix.

## 2026-08-14 — Place-based risk routes were not reachable

- Live check before fix: `:8001` PID 12200 and `:8004` PID 27400 had been up since ~10:05 AM — **old build**. `/predict` returned only `cell_id/date/risk/xi/lookback_days` (no aggregation/percentiles). Agent had no `county_risk`.
- Even in new source, eval-case wording worked; natural phrasing did not. `wants_risk` required `\brisk\b` so **risky** missed. `_iso_date` required `YYYY-MM-DD`. `resolve_time` treated `2024-08-15` as year 2024 (full year) and `August 15th 2024` as August 1–31. Model then answered a count.
- Fix: parse calendar days in `time_resolve`; `_wants_risk` includes risky / ignition probability; risk routes use the single day; model path refuses a count substitute if `risk_forecast` was not called. Tests use phrasings that were not used to write the rules.
- Recycled `:8001` and `:8004`. Live `/ask`: both user questions → `county_risk` / `2024-08-15` / P(≥1)≈0.066 over 11 cells. “How risky was PG&E territory on August 15, 2024?” → `utility_risk`.

## 2026-08-14 — Risk answer precision / prose

- Lead sentence is interpretable: “Sacramento County had a 6.6% chance of at least one ignition on 2024-08-15. That's higher than about 78% of August days there since 2020, and higher than about 90% of California that day.” Formula, cell count, expected_count, and local n live in the audit “How this was answered” block.
- Percentile cards emit rounded integers and render as 78th / 90th (`?v=risk-prose`). Probability card stays 4 decimals (0.0662). Agent recycled. Planning Tool maps still 1571×1785.

## 2026-08-14 — Out-of-coverage risk clarification + stale suppress

- Forward risk phrasing (today / tonight / tomorrow / this week / next week / this weekend / next weekend) and dates after 2025-12-31 route `risk_future_date`. Month-only still `forecast_missing_date`. Both lead with the coverage limit, then ask for a past date — never “predict a historical date.”
- Live: “What's the fire risk in Sacramento County tomorrow?” → “This model scores historical dates only. Weather and vegetation data end 2025-12-31 and there's no forecast ingestion, so I can't answer about tomorrow. Which past date should I score?”
- Stale banner now suppresses on clarification / unsupported / error the same way it does when the map is collapsed (`lastAnswerStatus`; do not treat clarification `view_scope` as an answered scope). Verified: 2025 statewide map stays put, `#historical-canvas-stale` hidden. Cache-bust `?v=risk-clarify`. Planning Tool method maps still 1571×1785.

## 2026-08-14 — Risk trail summary rounding

- Ask activity trail was `Risk 0.06624759278909353 (cell null)` on county/utility scores. `summarizeResult` now uses the same 4-decimal / scientific rule as the probability card, shows `(cell N)` only when `cell_id` is present, and otherwise `· 11 cells` from `cell_count`. Cache-bust `?v=risk-trail`.

## 2026-08-21 — Single-dataset ranking

- `GET /rank` lives on data_query (not comparison). Allowed pairs: CPUC county/utility count; CAL FIRE county count or acres; EPSS circuit count. Skipped: US-by-state (no state column), EPSS-by-utility (PGE-only).
- Ties: include everyone tied with the Nth row; hard cap 25 with `ties_cut`. Answer says “top 10 of 36” and “11 shown because of ties.”
- Agent: `data_query_rank`, deterministic `ranked_records`, Comparison canvas. Cross-dataset rank stays unsupported.
- CAL FIRE 2023 county GROUP BY: EXPLAIN ANALYZE ~1.2 ms; no new index.
- Eval: `unsupported_ranking_circuit_most` now expects a rank answer; added `rank_calfire_counties_2023` and `unsupported_rank_cross_dataset`.

## 2026-08-21 — GPU control + agent survives missing model

- Agent lifespan no longer dies if Ollama/warmup fails. Binds `:8004`. Model-tier path short-circuits to HTTP 200 `status=error` with an offline sentence. Lazy `ensure_context_loaded` on first `complete()`. `/health` stays cheap `/v1/models` (no warmup).
- Ask stays enabled whenever agent `/health` is reachable. Banner when model is down: "Counts, maps, and rankings work now; open-ended questions need the GPU." GPU ready re-probes health (no reload).
- New `services/gpu_control/` on **8005**: `GET /gpu/status`, `POST /gpu/start|stop` with `X-GPU-Control-Token`. Missing env token → POST 503. No implicit GPU start from Ask/health. EBS ~$20/month still bills when EC2 is stopped.
- Frontend strip above Ask form; token via `prompt()`, not stored. `WILDFIRE_GPU_CONTROL_BASE`. Cache-bust `?v=gpu-control`.
- IAM for `wildfire-backend-ssm-role` is documented only (not applied): Start/Stop on `i-09526a2a9268135f2`; DescribeInstances on `*` (no resource-level ARN).
- Planning Tool method maps still render 1571×1785 after the slice.

## 2026-08-23 — gpu_control dotenv + empty US ignitions

- `gpu_control/config.py` now `load_dotenv(REPO_ROOT / ".env")` at import and in `from_env()`. Live bug: token in `.env` was invisible until shell-exported. Test: `tests/gpu_control/test_config.py`.
- US Ignitions toggle stays visible. Empty successful fetch shows “No data loaded for this layer yet.” in the layer strip. Local warehouse still has 33,457 rows so the empty line stays hidden here; deploy table is empty.
- Systemd units in `deploy/systemd/` (data-query, visualization, comparison, agent, gpu-control, frontend). Ollama left alone. `SYSTEMD_SETUP.md` notes `:8004/health` can lag `systemctl is-active` by several minutes after reboot.

## 2026-08-23 — GPU start actually loads the model

- Live bug: `/gpu/start` only called `StartInstances`; status stuck at `loading_model` with 0 MiB VRAM until someone asked a question (or ran `ollama run` by hand).
- Background bring-up after start returns: poll Ollama `/api/ps` → agent's `ensure_context_loaded()` if not resident → `POST :8004/ask` with “How many CPUC ignitions were there in 2023?”. `ready` only after pre-fire `status=answer`; failure is `error` + `reason`. Frontend polling unchanged.

## 2026-09-18 — Event map day-by-day playback

- Optional `Full range` / `Day by day` toggle on EventDataMap (CPUC, EPSS, CAL FIRE, plus PSPS/US ignitions because they share the same map). Default remains full range.
- Shared `PlaybackControls` + `usePlayback` extracted from HDW; HDW year/source/frames unchanged. Event playback filters already-paged `getLayer` features locally via `featuresOnDate`; EPSS daily refetches once with `include_outages=true`.
- Empty days keep the basemap and a muted `No events on this date.` chip (not LoadState). PSPS day-by-day is starts-that-day, not duration-active.
- Branch `event-map-playback`; do not merge until asked.

## 2026-09-21 — Jev shadow mode (not on the answer path)

- `AGENT_JEV_MODE` defaults to `off`. Shadow logs Jev decisions beside the regex router and does not change Ask responses, SSE events, or eval scores. `verify` / `fallback` / `route` are rejected at startup.
- Typesafe SDK 0.7.1. Retries disabled (`RetryPolicy(max_retries=0)`). County Choice is 59 options, under the 255 limit. `AGENT_AUDIT.md` was not in the repo; labels were taken from `routing.py`.
- `pytest tests/agent` 118 passed, then shadow tests 15 passed after the list-versus-count label tweak. Live offline eval and `--replay` skipped: `TYPESAFE_API_KEY` unset. Scored eval could not start: Ollama `:11434` and services `:8001`–`:8003` were down.
- Label fix: fault/bounded cases use the question's route, not the harness error. Clarify and unsupported intents are null. Unknown rules are `unmapped_rule`, not intent `other`. `risk_out_of_coverage` is clarify `risk_future_date`. EC2 Python was not read: an SSH attempt to the EC2 host timed out and there is no AWS CLI (the host is reached through SSM, see CLAUDE.md).

## 2026-09-22 — Jev shadow phase 2

- Schema `v2`. Context is built from `POLICY_SENTENCES` (590 words) and states every clarify/refuse rule. Six review rows are labeled. `utility_not_invented_from_place` accepts either the refusal or a CAL FIRE count. Tool pick is model-route only (n=14).
- Live offline eval pinned `jev-1.13.0`. Parse mismatches, unexpected options, and wiring errors were 0. Replay of 20 identical payloads had 15 mismatches above 0.05 probability or a choice change, so that replay is not a clean wiring proof.
- Deployed Python is recorded as 3.12. Local vermin minimum for the Jev modules is 3.8. No local 3.12 interpreter.

## 2026-09-22 — Jev phase 3 (facts, not a rulebook)

- `jev-1.13.0` has no seed, temperature, or deterministic mode in SDK 0.7.1. Identical canonical payloads (hash mismatches 0) still move low-confidence choice labels. Confidence >= 0.7 did not flip in a 20x10 repeat. Notes in `docs/JEV_DETERMINISM.md`.
- Schema v3 questions live in `services/agent/decisions/v3.py`. `jev_policy.py` turns those facts into a disposition. Ablation winner is `v3_policy_context` (disposition 87.6%, paraphrase disposition 90.2%, tool_pick 100%). Shadow uses that layout. The daily cap counts questions. Router paraphrase fixes are a separate branch.



