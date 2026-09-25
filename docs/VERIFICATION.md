# Frontend ↔ visualization API verification

> **Default changed 2026-09-24.** Counts in this document use the CAL FIRE default of its time, `incident_type IN ('Wildfire', 'Fire')`. The default is now every incident except the non-wildfire types (Earthquake, Flood, Hazmat), so untyped incidents are counted; see [`DATA_CHANGE_CALFIRE_DEFAULT.md`](DATA_CHANGE_CALFIRE_DEFAULT.md) for every count that changes.

Compared static files in `frontend/assets/data/` (copied from `dataset_demo`) against `http://127.0.0.1:8002` for **year=2024**, `limit=20000`.

## Results

| Check | Static | API | Status | Notes |
|-------|-------:|----:|--------|-------|
| CPUC ignitions 2024 | 741 | 741 | MATCH | |
| CPUC PGE 2024 (attribute) | 532 | 532 | MATCH | |
| EPSS outage events 2024 | 2788 | 2787 | DIFF | Warehouse drops 1 exact duplicate (documented) |
| EPSS circuits 2024 | 635 | 635 | MATCH | |
| CAL FIRE (all types, CA bbox) | 612 | 612 | MATCH | Verified with `incident_type=all` |
| CAL FIRE Wildfire/Fire only | 611 | 611 | MATCH | Demo default after this verification |
| PSPS events 2024 | 23 | 23 | MATCH | |
| time-series ignitions total | 741 | 741 | MATCH | weekly Jan-1 bins |
| time-series EPSS total | 2788 | 2787 | DIFF | same 1-row dedupe |
| time-series CAL FIRE all | 612 | 612 | MATCH | |
| Truncation at limit=20000 | — | false | OK | |
| EPSS null-geometry circuits (2024 page) | — | 0 | OK | |

## Intentional post-verification change

During A/B checks the frontend sent `incident_type=all` so counts matched the original website (no type filter).

**After verification**, [`assets/js/api-config.js`](assets/js/api-config.js) sets:

```js
window.WILDFIRE_CALFIRE_INCIDENT_TYPE = "";
```

so the API uses its **Wildfire/Fire** default. The demo is a wildfire risk platform and should not show floods/earthquakes on the map. For 2024 that removes **1** incident relative to `all` (612 → 611).

## EPSS popup

`/event-detail?dataset=circuits&id={circuit_id}&year=` returns embedded **outage rows** (same fields as the static CSV popup). `/map-layer?dataset=epss&include_outages=true` also embeds outages for the day scrubber. This is **not** a functional downgrade from the website.

PSPS `/event-detail` includes `affected_circuits` from `psps_event_circuits`.

## Static map files

Map CSV/GeoJSON under `assets/data/` were removed after this verification. Retained: `assets/data/weather_anim/` (HDWI animation; no service).
