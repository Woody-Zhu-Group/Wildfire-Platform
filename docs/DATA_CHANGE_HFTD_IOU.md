# Data change: HFTD tier and IOU territory polygons

Status: applied to the local warehouse on 2026-09-23. Not yet applied on EC2.

## What was wrong

The warehouse loaded `wildfire.hftd_tiers` and `wildfire.iou_territories` from
two web-map files in `dataset_demo/assets/data` (`hftd.geojson` and
`iou_territories.geojson`). The loader copied them without change. The files
were made for drawing a map, not for spatial queries, and had two defects.

1. **Holes became outer polygons.** dataset_demo's `scripts/fetch_hftd.py`
   downloaded CPUC's HFTD layer as GeoJSON (`f=geojson`). That export wrote all
   436 Tier 2 holes and 30 Tier 3 holes as separate outer polygons, with zero
   holes left. The same layer as Esri JSON (`f=json`) keeps the ring roles:
   clockwise rings are boundaries, counterclockwise rings are holes. The IOU
   file had the same problem: SCE's holes for municipal utilities (Anaheim,
   Riverside and Colton, Banning, Azusa) and for BVES (Big Bear Lake) were
   extra outer polygons. So:
   - Events inside a hole (towns excluded from a tier, municipal-utility
     cities) were counted as inside the tier or territory. Point containment
     on invalid polygons depends on the GEOS version, so a point answer could
     differ between engines.
   - Areas summed the holes as area. Tier 2 came to 167,125 km2 against
     CPUC's 149,985 km2 (+11.4%).
2. **Simplification broke validity further.** `scripts/simplify_hftd.py`
   simplified at tolerance 0.001 degrees, then rounded to 5 decimals. That
   added self-intersections, collapsed rings, and overlapping parts. The IOU
   file is similarly simplified: PG&E has 1,072 vertices against 48,045 in
   the published layer. Coastal city centers (Foster City, Pismo Beach,
   Malibu, Redondo Beach, West Hollywood) fell outside the simplified
   coastline.

Visible symptom: every `ST_Intersects` against the stored HFTD geometry
failed with a GEOS TopologyException. That broke two things for Tier 2 and
Tier 3: every PSPS-in-HFTD comparison for 2021 to 2025 (event counts and
customers deenergized), and the circuit counts used as comparison
denominators.

## What changed

- **Sources.** `db/loaders/arcgis_polygons.py` reads each layer as Esri JSON
  in EPSG:4326, from a cache in `data/boundaries/*.esri.json` (gitignored),
  or from the publisher's FeatureServer when there is no cache or
  `--refresh` is given. With no cache and no network, the load stops with
  the fix in the message, before any table is touched.
- **Completeness.** Before truncating, the cached or fetched features must be
  exactly the expected set: every utility (PG&E, SCE, PacifiCorp, SDG&E, LU,
  BVES) and both tiers, each once. A partial download is refused and is not
  cached.
- **Rings.**
  - Each counterclockwise ring (hole) goes to the smallest clockwise ring
    (outer) that contains it. A hole with no containing outer ring stops the
    load.
  - Rings with fewer than 4 points are dropped and counted.
  - A ring with exactly zero area stops the load.
  - Slivers under 1 m2 are counted and kept. Tier 2 has 4, which are
    digitizing artifacts in CPUC's layer.
  - An outer ring nested directly inside another outer ring is counted and
    unioned with it, which matches Esri's nonzero winding.
  - The loader prints these counts for every feature.
- **Union.** PostGIS unions each feature's polygons. `ST_MakeValid` runs only
  on a polygon that is still invalid. On this load, it was not needed for any
  of them.
- **One transaction.** `db/loaders/load_boundaries.py` replaces
  `iou_territories` and `hftd_tiers` together. The validity and area gate
  runs on both tables inside the transaction, so a failure rolls back both.
  New IOU rows never sit beside old HFTD rows.
- **Load gate.** Every geometry must pass `ST_IsValid`, and its EPSG:3310
  area must be within 0.1% of the publisher's `Shape__Area`.
- **Audit columns.**
  - `geom_source` keeps the old dataset_demo geometry. It is NULL where
    dataset_demo is absent, as on EC2.
  - `publisher_area_m2`, `source_url` and `source_edited_at` record the
    reference.
- **Commands.**
  - `python -m db.loaders.rebuild_boundaries [--fetch-only | --refresh]`
    seeds the cache or reloads just these two tables.
  - `load_all` uses the same combined load. If the boundaries fail (no
    source, or a failed gate), `load_all` keeps their previous rows, loads
    every other table, and exits non-zero.
- **Web map.** The Historical Map and the website draw these layers from the
  database through the visualization service (`/map-layer?dataset=hftd` and
  `/utility-territory`), not from files. Those two queries now draw
  `ST_SimplifyPreserveTopology(geom, 0.001)` with 5-decimal coordinates.
  That keeps the Tier 2 payload at about 1.0 MB instead of 2.8 MB, and SCE's
  at 69 KB instead of 3.0 MB. Bounds and centers still use the full
  geometry. Every count and containment query uses the full geometry. Only
  the older fast-trip PSPS section of `frontend/` still reads the dataset_demo
  files directly.

### Sources

| Layer | Publisher | Source | Data last edited |
|---|---|---|---|
| HFTD Tier 2 and Tier 3 | CPUC | [CPUC_High_Fire_Threat_District FeatureServer](https://services2.arcgis.com/VofPZYDe2pLxSP5G/ArcGIS/rest/services/CPUC_High_Fire_Threat_District/FeatureServer/0) (layer `CPUC_Fire_Threat_Map_Tiers2_3_V3_08_19_2021r`) | 2025-03-10 |
| IOU service territories | CPUC | [IOU_Service_Territories FeatureServer](https://services2.arcgis.com/VofPZYDe2pLxSP5G/arcgis/rest/services/IOU_Service_Territories/FeatureServer/0) (layer `IOU_Service_Territory_20240812`) | 2026-01-09 |

CPUC publishes the HFTD map files at
<https://files.cpuc.ca.gov/safety/fire-threat_map/2021/GIS_Files/>. CPUC does
not publish a tier area in square miles, so the reference is the layer's own
`Shape__Area` (EPSG:3310, square meters). The IOU layer's `Shape__Area` is in
square US survey feet (California Teale Albers, wkid 102599).

### One ring override

PG&E's published Esri JSON marks one 181 km2 ring over Suisun Marsh and
Grizzly Island as a hole. The same layer's `Shape__Area` counts that ring as
PG&E territory, and the ring holds 23 PG&E EPSS outages and 1 PG&E circuit.
The loader therefore treats it as an outer ring (`RingOverride` in
`arcgis_polygons.py`, with the reason recorded). Without the override, PG&E
passes the gate at -0.0975%, just inside the limit, but it would drop those
outages from PG&E's territory.

The override cannot flip any other ring:
- It must match exactly one PG&E hole that contains the point
  (-122.0402, 38.0816).
- That hole's spherical area must be within 0.5% of 181.22 km2 (181.33 km2
  in EPSG:3310). The next-largest hole in either layer is 135 km2.
- It stops the load if it matches no hole, more than one hole, or no
  feature.

### Areas after the rebuild (km2, EPSG:3310)

| Region | Before (sum of parts) | After | Publisher | After vs publisher |
|---|---|---|---|---|
| HFTD Tier 2 | 167,125.37 | 149,984.71 | 149,984.70 | +0.0000% |
| HFTD Tier 3 | 32,972.81 | 32,326.98 | 32,326.97 | +0.0000% |
| PGE | 186,196.21 | 185,940.74 | 185,940.74 | 0.0000% |
| SCE | 136,702.88 | 135,343.07 | 135,343.07 | 0.0000% |
| PACIFICORP | 28,700.04 | 28,648.65 | 28,648.68 | -0.0001% |
| SDGE | 11,427.12 | 11,396.24 | 11,396.24 | 0.0000% |
| Liberty | 3,841.76 | 3,834.83 | 3,834.83 | 0.0000% |
| BVES | 203.34 | 203.49 | 203.49 | 0.0000% |

Anything divided by these areas changes too. For example, per-km2 rates for
Tier 2 rise by 11.4%.

## Before and after counts

"Before" runs the service predicate against `geom_source`, the geometry the
services used until this change. "After" runs it against the rebuilt `geom`.
The predicates are the ones the services use:
- `ST_Within` for point events;
- `ST_Intersects` for PSPS polygons, circuits, and grid cells;
- `ST_Contains` for city points, as in `/spatial/point`. For city points the
  table lists every region that contains the point, because `/spatial/point`
  returns one match with no ordering.

Counts are by calendar year of the event date. They were measured on the
local warehouse on 2026-09-23.

### HFTD tiers

**CPUC ignitions** (data_query /spatial/summary, comparison ignitions (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2020 | 165 | 139 | -26 (-15.8%) |
| HFTD Tier 2 | 2021 | 142 | 117 | -25 (-17.6%) |
| HFTD Tier 2 | 2022 | 91 | 79 | -12 (-13.2%) |
| HFTD Tier 2 | 2023 | 70 | 61 | -9 (-12.9%) |
| HFTD Tier 2 | 2024 | 125 | 106 | -19 (-15.2%) |
| HFTD Tier 2 | 2025 | 74 | 68 | -6 (-8.1%) |
| HFTD Tier 3 | 2020 | 88 | 84 | -4 (-4.5%) |
| HFTD Tier 3 | 2021 | 71 | 69 | -2 (-2.8%) |
| HFTD Tier 3 | 2022 | 62 | 61 | -1 (-1.6%) |
| HFTD Tier 3 | 2024 | 62 | 60 | -2 (-3.2%) |
| HFTD Tier 3 | 2025 | 29 | 26 | -3 (-10.3%) |

1 other region-year is unchanged.

**EPSS outages** (data_query /spatial/summary; comparison EPSS for HFTD)

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2022 | 1,225 | 1,058 | -167 (-13.6%) |
| HFTD Tier 2 | 2023 | 1,255 | 1,112 | -143 (-11.4%) |
| HFTD Tier 2 | 2024 | 1,446 | 1,292 | -154 (-10.7%) |
| HFTD Tier 2 | 2025 | 1,206 | 1,046 | -160 (-13.3%) |

6 other region-years are unchanged.

**CAL FIRE incidents** (data_query /spatial/summary, comparison CAL FIRE (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2018 | 15 | 13 | -2 (-13.3%) |
| HFTD Tier 2 | 2019 | 93 | 82 | -11 (-11.8%) |
| HFTD Tier 2 | 2020 | 113 | 105 | -8 (-7.1%) |
| HFTD Tier 2 | 2021 | 98 | 87 | -11 (-11.2%) |
| HFTD Tier 2 | 2022 | 77 | 71 | -6 (-7.8%) |
| HFTD Tier 2 | 2023 | 65 | 62 | -3 (-4.6%) |
| HFTD Tier 2 | 2024 | 238 | 216 | -22 (-9.2%) |
| HFTD Tier 2 | 2025 | 228 | 215 | -13 (-5.7%) |
| HFTD Tier 2 | 2026 | 134 | 125 | -9 (-6.7%) |
| HFTD Tier 3 | 2021 | 31 | 30 | -1 (-3.2%) |
| HFTD Tier 3 | 2023 | 34 | 32 | -2 (-5.9%) |
| HFTD Tier 3 | 2024 | 93 | 90 | -3 (-3.2%) |
| HFTD Tier 3 | 2025 | 63 | 64 | +1 (+1.6%) |
| HFTD Tier 3 | 2026 | 45 | 44 | -1 (-2.2%) |

10 other region-years are unchanged.

**CAL FIRE acres** (comparison acres burned (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2018 | 960,250 | 945,187 | -15,063 (-1.6%) |
| HFTD Tier 2 | 2019 | 108,379 | 100,945 | -7,434 (-6.9%) |
| HFTD Tier 2 | 2020 | 2,139,221 | 2,138,587 | -634 (-0.0%) |
| HFTD Tier 2 | 2021 | 1,967,206 | 1,848,048 | -119,158 (-6.1%) |
| HFTD Tier 2 | 2022 | 127,943 | 108,201 | -19,742 (-15.4%) |
| HFTD Tier 2 | 2023 | 209,131 | 208,938 | -193 (-0.1%) |
| HFTD Tier 2 | 2024 | 761,816 | 757,824 | -3,992 (-0.5%) |
| HFTD Tier 2 | 2025 | 183,450 | 182,254 | -1,196 (-0.7%) |
| HFTD Tier 2 | 2026 | 115,645 | 99,898 | -15,747 (-13.6%) |
| HFTD Tier 3 | 2018 | 125,526 | 125,329 | -197 (-0.2%) |
| HFTD Tier 3 | 2021 | 238,218 | 238,058 | -160 (-0.1%) |
| HFTD Tier 3 | 2023 | 5,387 | 5,179 | -208 (-3.9%) |
| HFTD Tier 3 | 2024 | 157,920 | 157,747 | -173 (-0.1%) |
| HFTD Tier 3 | 2025 | 185,438 | 185,458 | +20 (+0.0%) |
| HFTD Tier 3 | 2026 | 16,863 | 16,850 | -13 (-0.1%) |

9 other region-years are unchanged.

**PSPS events** (comparison PSPS events (HFTD))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2021 | error | 7 | was an error |
| HFTD Tier 2 | 2022 | error | 3 | was an error |
| HFTD Tier 2 | 2023 | error | 5 | was an error |
| HFTD Tier 2 | 2024 | error | 23 | was an error |
| HFTD Tier 2 | 2025 | error | 20 | was an error |
| HFTD Tier 3 | 2021 | error | 6 | was an error |
| HFTD Tier 3 | 2022 | error | 2 | was an error |
| HFTD Tier 3 | 2023 | error | 7 | was an error |
| HFTD Tier 3 | 2024 | error | 13 | was an error |
| HFTD Tier 3 | 2025 | error | 14 | was an error |

**PSPS customers** (comparison customers deenergized (HFTD))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2021 | error | 115,138 | was an error |
| HFTD Tier 2 | 2022 | error | 15,784 | was an error |
| HFTD Tier 2 | 2023 | error | 38,694 | was an error |
| HFTD Tier 2 | 2024 | error | 243,983 | was an error |
| HFTD Tier 2 | 2025 | error | 601,432 | was an error |
| HFTD Tier 3 | 2021 | error | 114,400 | was an error |
| HFTD Tier 3 | 2022 | error | 15,575 | was an error |
| HFTD Tier 3 | 2023 | error | 39,037 | was an error |
| HFTD Tier 3 | 2024 | error | 237,923 | was an error |
| HFTD Tier 3 | 2025 | error | 597,095 | was an error |

**US ignitions** (no service reads this today; measured for completeness)

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | 2014 | 170 | 156 | -14 (-8.2%) |
| HFTD Tier 2 | 2015 | 189 | 178 | -11 (-5.8%) |
| HFTD Tier 2 | 2016 | 128 | 121 | -7 (-5.5%) |
| HFTD Tier 2 | 2017 | 196 | 167 | -29 (-14.8%) |
| HFTD Tier 2 | 2018 | 169 | 156 | -13 (-7.7%) |
| HFTD Tier 2 | 2019 | 286 | 267 | -19 (-6.6%) |
| HFTD Tier 2 | 2020 | 394 | 349 | -45 (-11.4%) |
| HFTD Tier 2 | 2021 | 404 | 351 | -53 (-13.1%) |
| HFTD Tier 2 | 2022 | 473 | 391 | -82 (-17.3%) |
| HFTD Tier 2 | 2023 | 471 | 390 | -81 (-17.2%) |
| HFTD Tier 2 | 2024 | 404 | 340 | -64 (-15.8%) |
| HFTD Tier 2 | 2025 | 52 | 45 | -7 (-13.5%) |
| HFTD Tier 3 | 2015 | 78 | 77 | -1 (-1.3%) |
| HFTD Tier 3 | 2016 | 75 | 72 | -3 (-4.0%) |
| HFTD Tier 3 | 2017 | 119 | 123 | +4 (+3.4%) |
| HFTD Tier 3 | 2019 | 138 | 135 | -3 (-2.2%) |
| HFTD Tier 3 | 2020 | 246 | 245 | -1 (-0.4%) |
| HFTD Tier 3 | 2021 | 219 | 199 | -20 (-9.1%) |
| HFTD Tier 3 | 2022 | 267 | 225 | -42 (-15.7%) |
| HFTD Tier 3 | 2023 | 222 | 199 | -23 (-10.4%) |
| HFTD Tier 3 | 2024 | 260 | 210 | -50 (-19.2%) |
| HFTD Tier 3 | 2025 | 43 | 36 | -7 (-16.3%) |

2 other region-years are unchanged.

**Circuits** (comparison circuit denominators (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| HFTD Tier 2 | all | error | 618 | was an error |
| HFTD Tier 3 | all | error | 279 | was an error |

### IOU territories

**CPUC ignitions** (data_query /spatial/summary, comparison ignitions (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| PGE | 2020 | 507 | 509 | +2 (+0.4%) |
| PGE | 2021 | 484 | 485 | +1 (+0.2%) |
| SCE | 2021 | 168 | 167 | -1 (-0.6%) |
| SCE | 2023 | 86 | 87 | +1 (+1.2%) |
| SCE | 2025 | 113 | 112 | -1 (-0.9%) |

19 other region-years are unchanged.

**EPSS outages** (data_query /spatial/summary; comparison EPSS for HFTD)

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| PGE | 2025 | 2,355 | 2,354 | -1 (-0.0%) |

17 other region-years are unchanged.

**CAL FIRE incidents** (data_query /spatial/summary, comparison CAL FIRE (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| SCE | 2017 | 2 | 1 | -1 (-50.0%) |
| SCE | 2019 | 44 | 43 | -1 (-2.3%) |
| SCE | 2020 | 54 | 53 | -1 (-1.9%) |
| SCE | 2021 | 36 | 34 | -2 (-5.6%) |
| SCE | 2022 | 28 | 29 | +1 (+3.6%) |
| SCE | 2023 | 44 | 43 | -1 (-2.3%) |
| SCE | 2024 | 177 | 175 | -2 (-1.1%) |
| SCE | 2025 | 106 | 104 | -2 (-1.9%) |
| SCE | 2026 | 108 | 107 | -1 (-0.9%) |
| SDGE | 2019 | 14 | 13 | -1 (-7.1%) |

36 other region-years are unchanged.

**CAL FIRE acres** (comparison acres burned (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| SCE | 2017 | 281,938 | 281,893 | -45 (-0.0%) |
| SCE | 2019 | 37,636 | 37,386 | -250 (-0.7%) |
| SCE | 2020 | 533,004 | 528,767 | -4,237 (-0.8%) |
| SCE | 2021 | 21,986 | 21,802 | -184 (-0.8%) |
| SCE | 2022 | 49,030 | 49,150 | +120 (+0.2%) |
| SCE | 2023 | 109,308 | 109,205 | -103 (-0.1%) |
| SCE | 2024 | 302,390 | 301,712 | -678 (-0.2%) |
| SCE | 2025 | 58,337 | 58,255 | -82 (-0.1%) |
| SCE | 2026 | 34,637 | 34,612 | -25 (-0.1%) |
| SDGE | 2019 | 2,781 | 1,274 | -1,507 (-54.2%) |

36 other region-years are unchanged.

**US ignitions** (no service reads this today; measured for completeness)

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| Liberty | 2020 | 11 | 10 | -1 (-9.1%) |
| PACIFICORP | 2015 | 44 | 43 | -1 (-2.3%) |
| PACIFICORP | 2017 | 30 | 29 | -1 (-3.3%) |
| PACIFICORP | 2018 | 18 | 19 | +1 (+5.6%) |
| PACIFICORP | 2024 | 50 | 49 | -1 (-2.0%) |
| PGE | 2014 | 138 | 139 | +1 (+0.7%) |
| PGE | 2015 | 163 | 164 | +1 (+0.6%) |
| PGE | 2016 | 124 | 125 | +1 (+0.8%) |
| PGE | 2018 | 139 | 138 | -1 (-0.7%) |
| PGE | 2019 | 266 | 268 | +2 (+0.8%) |
| PGE | 2021 | 705 | 707 | +2 (+0.3%) |
| PGE | 2023 | 1,050 | 1,053 | +3 (+0.3%) |
| PGE | 2024 | 912 | 913 | +1 (+0.1%) |
| SCE | 2014 | 100 | 99 | -1 (-1.0%) |
| SCE | 2015 | 94 | 91 | -3 (-3.2%) |
| SCE | 2016 | 88 | 84 | -4 (-4.5%) |
| SCE | 2017 | 397 | 389 | -8 (-2.0%) |
| SCE | 2018 | 532 | 529 | -3 (-0.6%) |
| SCE | 2019 | 511 | 513 | +2 (+0.4%) |
| SCE | 2020 | 738 | 730 | -8 (-1.1%) |
| SCE | 2021 | 929 | 914 | -15 (-1.6%) |
| SCE | 2022 | 987 | 967 | -20 (-2.0%) |
| SCE | 2023 | 937 | 912 | -25 (-2.7%) |
| SCE | 2024 | 1,066 | 1,030 | -36 (-3.4%) |
| SCE | 2025 | 175 | 169 | -6 (-3.4%) |
| SDGE | 2021 | 35 | 34 | -1 (-2.9%) |

39 other region-years are unchanged.

**Circuits** (comparison circuit denominators (spatial))

| Region | Year | Before | After | Change |
|---|---|---|---|---|
| SCE | all | 4 | 2 | -2 (-50.0%) |

3 other region-years are unchanged.

### Grid cells used for utility risk

- PGE: 385 before, 385 after
- SCE: 309 before, 309 after
- SDGE: 26 before, 26 after

### City center points whose answer changes (483 incorporated places checked)

Points: the Census 2025 Gazetteer internal points (`INTPTLAT`, `INTPTLONG`) of the 483 California incorporated places, meaning cities and towns (LSAD 25 and 43), from <https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_gaz_place_06.txt>. They are the same points as `data/places/ca_places_gazetteer_2025.csv` in PR #28, filtered to `place_type` city or town. Each cell lists every region containing the point.

**IOU territory** (12 cities)

| City | Before | After |
|---|---|---|
| Albany | PGE | none |
| Anaheim | SCE | none |
| Azusa | SCE | none |
| Banning | SCE | none |
| Big Bear Lake | BVES, SCE | BVES |
| Colton | SCE | none |
| Foster City | none | PGE |
| Malibu | none | SCE |
| Pismo Beach | none | PGE |
| Redondo Beach | none | SCE |
| Riverside | SCE | none |
| West Hollywood | none | SCE |

**HFTD tier** (18 cities)

| City | Before | After |
|---|---|---|
| Anderson | Tier 2 | none |
| Auburn | Tier 2 | none |
| Banning | Tier 2, Tier 3 | Tier 2 |
| Colfax | Tier 2 | none |
| Dunsmuir | Tier 2, Tier 3 | Tier 3 |
| Grass Valley | Tier 2 | none |
| Jackson | Tier 2 | none |
| Lompoc | Tier 2 | none |
| Loyalton | Tier 2 | none |
| Placerville | Tier 3 | Tier 2 |
| Redding | Tier 2 | none |
| Scotts Valley | Tier 3 | none |
| Shasta Lake | Tier 2 | none |
| Sutter Creek | Tier 2 | none |
| Tehachapi | Tier 2, Tier 3 | Tier 3 |
| Truckee | Tier 2, Tier 3 | Tier 3 |
| Ukiah | Tier 2 | none |
| Willits | Tier 2 | none |

PGE 2024 CPUC ignitions: 532 by attribute (utility = PGE) and 536 by spatial containment, both before and after the rebuild. The 4-ignition gap between the two definitions is real, not a geometry artifact.

Where the old geometry had two containing regions (Big Bear Lake; Banning, Dunsmuir, Tehachapi and Truckee for HFTD), `/spatial/point` returned one of them with no fixed order. On invalid polygons the answer also depended on the GEOS version.

## When the corrected data applies

- **Local warehouse:** answers from 2026-09-23 on use the corrected
  geometry.
- **EC2 (production):** still serves the old geometry. The correction
  applies from the date this change is deployed and
  `python -m db.loaders.rebuild_boundaries` is run there. Record that date
  here when it happens.
- **Earlier answers:** any spatial HFTD or IOU answer given before that
  date used the old geometry. That includes counts, areas, per-area rates,
  and point lookups. The tables above show how large the differences are.
- **Source vintages:** these don't change. The HFTD map is still CPUC's v3
  map of 2021-08-19, and the IOU layer is still `IOU_Service_Territory_20240812`.
  What changes is how faithfully the warehouse represents them.

## Deploy notes

1. **Seed the source cache (required).** On a machine with network access,
   run `python -m db.loaders.rebuild_boundaries --fetch-only`. It downloads
   both layers, checks that every utility and both tiers are present, and
   writes `data/boundaries/cpuc_hftd.esri.json` and
   `data/boundaries/cpuc_iou_service_territories.esri.json`. Copy both files
   to the same path on the EC2 host. The files are gitignored and are not
   deployed by git. Keep them with the deploy, because they are what the
   warehouse was built from.
2. **Reload.** On EC2, run `python -m db.loaders.rebuild_boundaries`. It
   checks the cache before connecting, applies the schema (this adds the new
   columns with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), then replaces
   both tables in one transaction behind the gate. It exits non-zero and
   changes nothing if the cache is missing or incomplete, an override does
   not match, or the gate fails.
3. **Brief lock.** The reload truncates `iou_territories` and `hftd_tiers`
   inside its transaction. That takes an ACCESS EXCLUSIVE lock, so any query
   that reads either table (spatial summaries, comparisons, point lookups,
   map layers, utility risk) waits until the reload commits. That took 4.0 seconds on
   the local warehouse (measured 2026-09-23). Run it outside analyst hours, or accept a
   short pause.
4. **Full loads.** `load_all` uses the same combined load. Without a seeded
   cache and without network, it keeps the old boundary rows, loads every
   other table, and exits non-zero with this section's reference.
5. **geom_source.** EC2 has no dataset_demo, so `geom_source` is NULL there.
   The audit copy exists only where dataset_demo was present at load time.
6. **Record the date.** Record the EC2 reload date in "When the corrected
   data applies" above.
7. **Deploy order with PR #28.** PR #28 (city center points) answers "which
   IOU territory or HFTD tier is this city in" from these tables. Its answers
   are only as right as this geometry, so deploy this reload with or before
   #28.
