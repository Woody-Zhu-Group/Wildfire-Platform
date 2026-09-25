# Frontend (slim copy of dataset_demo)

Local UI wired to the **visualization service** (`:8002`) for the Wildfire & Outage Map tab. Planning Tool images are **not** vendored here.

## Requirements

1. PostGIS up and loaded (`docker compose up -d` from repo root).
2. Visualization API:

```powershell
cd "C:\AI Coding Projects\Wildfire Services"
$env:PYTHONPATH = "."
uvicorn services.visualization.app:app --port 8002 --app-dir .
```

3. Sibling **`dataset_demo`** checkout next to this repo (for Planning Tool PNGs only).

## Serve

Do **not** run `python -m http.server` from `frontend/`. Browsers resolve `data-plots-base="../../dataset_demo/assets/website_plots"` to `/dataset_demo/...` on the origin, which 404s when the server root is `frontend/` and the Planning Tool shows “No plot images available.”

Use the dual-root helper (keeps `http://127.0.0.1:8765/index.html` for canvas work and mounts the sibling plots):

```powershell
cd "C:\AI Coding Projects\Wildfire Services"
python frontend/serve.py
```

Open: http://127.0.0.1:8765/index.html

Alternative: serve the **parent** of both repos:

```powershell
cd "C:\AI Coding Projects"
python -m http.server 5500
```

Open: http://127.0.0.1:5500/Wildfire%20Services/frontend/

- Historical map data → `http://127.0.0.1:8002` (see `assets/js/api-config.js`)
- Fire-weather HDWI animation → local `assets/data/weather_anim/`
- Planning Tool → `../../dataset_demo/assets/website_plots/` (sibling; mounted at `/dataset_demo/` by `serve.py`)

If you clone **only** Wildfire-Services, the Planning Tool tab will fail to load images/manifest; the map tab still works when `:8002` is up.

## Canvas-slice verification

After **each** Wildfire & Outage Map canvas change, before treating the slice as done:

1. Serve with `python frontend/serve.py` (not `http.server` from `frontend/`).
2. Open the **Planning Tool** tab (default) and confirm the three method maps render, not “No plot images available.”
3. Then verify the canvas slice on the map tab.

Canvas CSS/JS must stay scoped to `#sfps-tab-historical` / `#historical-canvas-host`. Do not reuse Planning Tool containers (`.sfps-split`, `#planning-controls`, `#method-compare`).

Asked series and browse **Events over time** share one Plotly node. This `frontend/` copy measures asked-series height with `autosize: false` (`measureChartPlotHeight` in `assets/js/sect-fasttrip-psps.js`) and can `Plotly.Plots.resize` the browse chart. GitHub Pages (`docs/index.html`) now serves the React workspace built from `website/` and no longer loads these scripts. An older, unloaded copy under `docs/assets/js/` isolated the two charts (asked series `autosize: true`; browse a 380px host with `autosize: false`, a year-pinned x-axis, and no `Plots.resize` on Bar/Donut). Do not copy that resize path onto Bar/Donut here without the same isolation.

Working reference for the agent-driven left surface (six components, planner, grounding, layout, nulls, known bugs): [`CANVAS.md`](CANVAS.md). The aside-widget design in [`CANVAS_PANEL_PROPOSAL.md`](CANVAS_PANEL_PROPOSAL.md) is superseded.

## Config (one-line deploy change)

[`assets/js/api-config.js`](assets/js/api-config.js):

```js
window.WILDFIRE_API_BASE = "http://127.0.0.1:8002";
window.WILDFIRE_AGENT_BASE = "http://127.0.0.1:8004";
window.WILDFIRE_DATA_QUERY_BASE = "http://127.0.0.1:8000"; // record-table stale refetch
window.WILDFIRE_CALFIRE_INCIDENT_TYPE = ""; // API default (every incident except non-wildfire types); "all" was used only during verification
```

## US Ignitions layer

Historical Map toggle **US Ignitions (IRWIN / all-cause)** (off by default) loads `dataset=us_ignitions`. Info strip explains all-cause / sample / not-comparable-to-CPUC / CA concentration (~59% of 2024). Auto-zooms to CONUS only from the default California view with no utility/county filter; otherwise the view is left alone. **Zoom to national extent** is on the strip for on-demand use. This `frontend/` copy draws the layer in teal `#0f766e` (layer config, markers, clusters, chart, stat card, and strip swatch in `assets/js/sect-fasttrip-psps.js` and `assets/css/sect-fasttrip-psps.css`); it does not use the `#dc2626` style the visualization API returns for `us_ignitions`.

Map datasets use one hue each (CPUC burnt orange, CAL FIRE red, US ignitions teal `#0f766e`, EPSS purple, PSPS blue, HFTD amber with opacity for tier). CAL FIRE magnitude is bubble size, not a second color.

The Ask-data panel can download CSV when an answer produced tabular tool data (records, comparison rows, time-series buckets, spatial counts). Count-only answers do not. Agent visuals on the left canvas: [`CANVAS.md`](CANVAS.md). Ask stays enabled whenever the agent `/health` endpoint is reachable, even if the hosted model is down; the banner then says counts, maps, and rankings still work. The old GPU start/stop strip was removed with the GPU instance.

## Static map files

Map CSV/GeoJSON were removed after verification (see [`VERIFICATION.md`](VERIFICATION.md)). Retained: `assets/data/weather_anim/` for the HDWI animation.

## Service unavailable

If `/health` or layer fetches fail, a banner appears above the map. The map will not silently stay blank.
