# Comparison service

Cross-utility / region / period metric aggregates for the wildfire warehouse.

## Run

```powershell
# from the repo root
$env:PYTHONPATH = "."
uvicorn services.comparison.app:app --port 8003 --app-dir .
```

Docs: http://127.0.0.1:8003/docs

## Endpoints

| Path | Purpose |
|------|---------|
| `GET /health` | DB ping + metric/definition notes |
| `GET /compare-utilities` | Metric per utility. `utilities` (comma-separated), `metric`, `start_date`, `end_date` required |
| `GET /compare-regions` | Metric per county or HFTD tier. `region_type=county\|hftd`, `regions` (comma-separated), `metric`, `start_date`, `end_date` required |
| `GET /compare-periods` | Same scope, two date ranges + delta. `scope_type=utility\|county\|hftd`, `scope`, `metric`, `period_a_start`, `period_a_end`, `period_b_start`, `period_b_end` required |

All three also take `normalize` (default `none`) and `ignition_definition`.

### Metrics

`ignition_count`, `epss_outage_count`, `epss_to_ignition_ratio`, `calfire_incident_count`, `acres_burned`, `psps_event_count`, `customers_deenergized`

### Normalization

`normalize=none|per_circuit|per_km2`. The response always labels which form was returned (`value`, `raw_value`, `denominator`). Unavailable denominators → `value: null` + `reason` (never a silent zero). The canvas Comparison view must draw a **hatched placeholder** for those bars, not an empty axis plus a floating “no data” (see [`frontend/CANVAS.md`](../../frontend/CANVAS.md)).

### Definitions

- **Ignitions:** `ignition_definition=attribute` (default for utilities) or `spatial` (`ST_Within`; default for HFTD).
- **EPSS:** PG&E-only. Non-PGE → `null` + reason.
- **CAL FIRE:** `Wildfire` / `Fire` only (untyped excluded).
- **County:** attribute on EPSS/CAL FIRE (a CAL FIRE incident that lists several counties, such as "Shasta, Tehama", counts, with its full acreage, in each; `meta.multi_county_incidents` gives the distinct multi-county incidents across the compared counties or periods, and `meta.notes` says county totals can exceed the statewide total); CPUC ignitions use load-time Census PIP `county`. PSPS still has no county column → null + reason. `per_circuit` returns null + `REASON_CIRCUITS_PGE` for a county, or for a utility with no circuits in the PG&E EPSS inventory. County `per_km2` is not wired yet (`REASON_NO_COUNTY_AREA`) even though `wildfire.counties` now exists.
- **No CPZ** in this warehouse.
