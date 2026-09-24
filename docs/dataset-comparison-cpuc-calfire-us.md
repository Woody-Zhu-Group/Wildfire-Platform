# CPUC, CAL FIRE, and US ignitions: what we can (and cannot) claim

**Warehouse snapshot:** PostGIS `localhost:5433/wildfire`, queried 2026-08-13.  
**Reproducible via:** `analysis/compare_cpuc_calfire_us.py` (repo-root `PYTHONPATH`, `PYTHONIOENCODING=utf-8`).  
**This is a findings memo, not a product spec.**

> **Status note (2026-09-23):** The findings below are a dated snapshot and are
> not re-derived. On the code side: the `cpuc_utility_caused` and
> `us_ignitions_sample` texts quoted under "Caveats currently attached" are
> still the current `CAVEAT_TEXT` entries (`services/agent/caveats.py:16-24`),
> and `calfire_missingness` is still built from the null counts
> (`services/agent/caveats.py:157-164`). The recommended revisions to those
> three are not implemented. Since this memo, a separate
> `calfire_map_feed_counts` caveat covers the 2023 to 2024 CAL FIRE count jump
> (`services/agent/caveats.py:29`, attached by `_needs_calfire_map_feed_caveat`
> at `services/agent/caveats.py:559`; see
> [`analysis/calfire-2024-jump.md`](../analysis/calfire-2024-jump.md)). The
> `us_ignitions` service metadata still reports the state-PIP shares (0.4015 /
> 0.5872) in `services/shared/dataset_registry.py:71-76`, not the county
> `ST_Covers` 40.09%.

---

## What we should believe

These three tables all describe California fire activity and **do not describe the same events**. CPUC is a utility-attributed ignition list (small fires included). CAL FIRE in this warehouse is an *incident* catalog biased toward fires large enough to be typed and posted (median **53 acres** under the default Wildfire/Fire filter). US ignitions is a FireCastRL **positive-class sample** of IRWIN-derived points, California-heavy, with synthetic controls already stripped. They should not be added, ratioed, or trained as if they were three views of one census.

The open sampling-rate question (*what fraction of actual California fires does US ignitions capture?*) **cannot be answered from these tables**. In 2020–2024 (complete years for US), California US points outnumber default CAL FIRE incidents **7.5 to 1**, and only **~1%** of those US points sit within 5 km and ±3 days of a CAL FIRE Wildfire/Fire incident. A sample cannot be a 7× oversample of its supposed census and also fail a generous spatial-temporal join. US ignitions is not usable as a cNHPP cell-day census, and it is not a measurable subsample of CAL FIRE or CPUC.

Cross-checks against previously published warehouse figures all held: US total **33,457**; PGE 2024 CPUC attribute **532** vs spatial **536**; CAL FIRE null `incident_type` **1,234**.

---

## Findings

### 1. The three datasets record different objects

| Dataset | What a row is | Cause scope | Reporting threshold (what we can see) | California scope |
|---|---|---|---|---|
| `wildfire.cpuc_ignitions` | One utility-attributed ignition (point + date) | Utility-caused / utility-tagged only (PGE, SCE, SDGE, PACIFICORP) | No acres field. Counts exceed CAL FIRE in every overlapping year, consistent with small ignitions that never become posted incidents | Already CA IOU reports. **3,743 / 3,745** points tagged to a Census CA county; **2** fall outside (Nevada-side border; south of the US–Mexico line) |
| `wildfire.calfire_incidents` | One posted incident (point, often a named fire) | All-cause. Optional `utility` tag is missing on **282 / 3,747** rows | Default Wildfire/Fire filter: median **53.5 acres** (all years) / **53.0 acres** (2020–2025). Only **64 / 2,509** default rows are under 10 acres | CA incident feed. **15 / 2,509** default dated points miss every Census county polygon |
| `wildfire.us_ignitions` | One positive FireCastRL 75-day sequence, dated at the first `Wildfire=Yes` day | All-cause. No utility, no cause, no acres, no name, no IRWIN ID | Classification training positives, not a size threshold we can observe | CONUS sample. CA via `ST_Covers` vs `wildfire.counties`: **13,413 / 33,457 = 40.09%**. No `state` column on the table |

**Established (verified in schema/docs/extract, not re-derived from the CSV):** US extract drops synthetic negatives (0 Yes days) and full-sentinel sequences; the loaded table is events-only (`UNIQUE (latitude, longitude, event_date)`; 33,457 = 33,457 distinct triples). FireCastRL is not a cell-day panel. No CPZ polygons ship in `dataset_demo` (HFTD Tier 2/3 only). CPUC `county` is load-time Census TIGER PIP, not a source CSV column. CPUC `utility=` (attribute) is not the same as `/spatial/summary` (polygon containment).

**Newly measured here:** CAL FIRE default Wildfire/Fire is **2,509 / 3,747**. Of those, **825 (32.9%)** have `is_calfire_incident = false`. The data-query default **does not** filter on that flag. We cannot determine from this warehouse what `false` means (federal / local / other). Non-wildfire types retained: Flood 2, Earthquake 1, Hazmat 1.

**Fields that block identity matching:** none of the three tables share an incident ID. CPUC has no acres or name. US has no cause, acres, county, or IRWIN ID. CAL FIRE `date_only_created` is incident creation, not ignition time.

### 2. Yearly counts are not on a common clock, and the default CAL FIRE filter empties the 2010s

**Filters for Table 1**

- CPUC: all rows in `wildfire.cpuc_ignitions` (CA-scoped utility ignitions; 0 geom-null, 0 date-null).
- CAL FIRE default: `incident_type IN ('Wildfire','Fire')`; year from `date_only_created`; **2** rows with null `date_only_created` are excluded from every yearly cell (they still sit in the table).
- CAL FIRE all dated: any `incident_type` including NULL, `date_only_created` not null.
- CAL FIRE untyped: `incident_type IS NULL` and dated.
- US CONUS: all `wildfire.us_ignitions`.
- US CA: `ST_Covers(wildfire.counties.geom, us.geom)` (same PIP as CPUC county tagging). Prior state-polygon + 0.5° nearest fallback was **13,432** CA; county `ST_Covers` is **13,413** (19-point difference, 0.06 pp). 2024 CA is **2,225** either way.

**Table 1. Event counts by year**

| Year | CPUC | CAL FIRE default | CAL FIRE all dated | CAL FIRE untyped | US CONUS | US CA |
|-----:|-----:|-----------------:|-------------------:|-----------------:|---------:|------:|
| 2009 | 0 | 1 | 1 | 0 | 0 | 0 |
| 2013 | 0 | 0 | 141 | 141 | 0 | 0 |
| 2014 | 0 | 1 | 76 | 75 | 1,581 | 347 |
| 2015 | 0 | 2 | 99 | 97 | 2,445 | 358 |
| 2016 | 0 | 0 | 155 | 155 | 2,609 | 275 |
| 2017 | 0 | 11 | 429 | 418 | 2,857 | 620 |
| 2018 | 0 | 28 | 303 | 274 | 2,798 | 754 |
| 2019 | 0 | 207 | 263 | 56 | 2,867 | 891 |
| **2020** | **684** | **257** | 259 | 2 | 3,211 | **1,346** |
| **2021** | **672** | **172** | 186 | 14 | 3,518 | **1,808** |
| **2022** | **613** | **150** | 150 | 0 | 3,725 | **2,262** |
| **2023** | **480** | **133** | 133 | 0 | 3,695 | **2,239** |
| **2024** | **741** | **611** | 612 | 0 | 3,789 | **2,225** |
| 2025 | 555 | 555 | 556 | 0 | 362 | 288 |
| 2026 | 0 | 381 | 382 | 0 | 0 | 0 |

Years 2010–2012 are empty in all three (omitted). CPUC coverage is **2020-01-01 … 2025-12-24**. US coverage is **2014-03-10 … 2025-02-05** (2025 is January–February only). CAL FIRE `date_only_created` runs **2009-05-24 … 2026-08-03**.

**Reading Table 1**

- Overlapping complete years for all three: **2020–2024**. 2025 is not a fair US year.
- CPUC has **no** pre-2020 rows in this warehouse. US CA and CAL FIRE untyped do.
- Default CAL FIRE is near-empty before 2019 because **1,216 / 1,234** untyped rows sit in 2013–2018. Using the default filter as a 2010s census is wrong. All-dated 2017 is 429; default Wildfire/Fire is 11.
- 2024 CAL FIRE default jumps 133 → 611. Monthly mass is May–October (July 182), not a January dump. We cannot determine from this warehouse whether that is a reporting-practice change, a feed vintage change, or a real fire year. Do not treat 2023 vs 2024 as a clean trend without an external check.
- 2025 CPUC **555** and CAL FIRE default **555** are the same integer and **not** the same fires (Finding 3).
- US CA share of 2024 CONUS: **2,225 / 3,789 = 58.7%** (matches the established sample-geography figure).

**Table 2. Preferred complete-year window (2020–2024)**

| Filter | N | Notes |
|---|---:|---|
| CPUC, all rows, 2020–2024 | 3,190 | Utility-attributed ignitions |
| CAL FIRE default Wildfire/Fire, dated, 2020–2024 | 1,323 | Median acres 53 in the 2020–2025 window |
| US CA `ST_Covers` counties, 2020–2024 | 9,880 | Full US years; 2025 dropped |
| CPUC / CAL FIRE | 2.41 | Not a subset relationship |
| US CA / CAL FIRE | 7.47 | Cannot be a sampling fraction of CAL FIRE |

Including incomplete US 2025 (three-way 2020–2025) yields CPUC 3,745, CAL FIRE default 1,878, US CA 10,168, US/CAL FIRE **5.41**. That ratio is *lower* only because US 2025 is truncated. Prefer Table 2.

### 3. Spatial-temporal overlap is small in both directions, at every threshold we tried

Matching is **not** identity. There are no shared IDs. A “match” means: at least one row in dataset B with `ST_DWithin(geography, meters)` and `|date_B − date_A| ≤ D` days.

**Date columns used**

| Table | Date | Why |
|---|---|---|
| CPUC | `event_date` | Ignition date; never null |
| US | `event_date` | First Yes day of the 75-day sequence; never null |
| CAL FIRE | `date_only_created` | Only dated field that is an incident start/create date; 1970-01-01 sentinels are NULL (**2** rows dropped) |

CAL FIRE creation can lag ignition, so ±7 days is already generous. CPUC and US have no time-of-day on these tables (`cpuc_ignitions_with_time` was not used; membership differs by ~180 rows and has no utility tag).

**Universes (geom and date present; nothing silently dropped)**

| Universe | N | Drops |
|---|---:|---|
| CPUC | 3,745 | geom-null 0, date-null 0 |
| CAL FIRE default Wildfire/Fire, dated | 2,509 | geom-null 0; **2** undated excluded from matching |
| US CA `ST_Covers` | 13,413 | geom-null 0, date-null 0; **0** intersects-but-not-covers |

CAL FIRE matching uses the **Wildfire/Fire default**, not all-types. Pair windows are years with at least one row on **both** sides (so 2016, which has 0 default CAL FIRE, is absent from CAL FIRE↔US; that year still has 275 US CA points, unmatched by construction).

**Table 3. CPUC ↔ CAL FIRE (years 2020–2025). CPUC n = 3,745. CAL FIRE default n = 1,878.**

| Distance | Time | CPUC matched | CPUC unmatched | CPUC % | CAL FIRE matched | CAL FIRE unmatched | CAL FIRE % |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 km | ±1 d | 51 | 3,694 | 1.36 | 52 | 1,826 | 2.77 |
| 1 km | ±3 d | 52 | 3,693 | 1.39 | 53 | 1,825 | 2.82 |
| 1 km | ±7 d | 52 | 3,693 | 1.39 | 53 | 1,825 | 2.82 |
| 5 km | ±1 d | 91 | 3,654 | 2.43 | 89 | 1,789 | 4.74 |
| 5 km | ±3 d | 103 | 3,642 | 2.75 | 101 | 1,777 | 5.38 |
| 5 km | ±7 d | 109 | 3,636 | 2.91 | 107 | 1,771 | 5.70 |
| 10 km | ±1 d | 127 | 3,618 | 3.39 | 121 | 1,757 | 6.44 |
| 10 km | ±3 d | 164 | 3,581 | 4.38 | 157 | 1,721 | 8.36 |
| 10 km | ±7 d | 208 | 3,537 | 5.55 | 202 | 1,676 | 10.76 |

Loosening distance moves the rate more than loosening time. At 5 km / ±3 d, matched CPUC points have mean **1.02** CAL FIRE partners (2 of 103 have >1). This is not a many-to-one artifact.

**Table 4. CPUC ↔ US CA (years 2020–2025). CPUC n = 3,745. US CA n = 10,168 (includes truncated 2025).**

| Distance | Time | CPUC matched | CPUC unmatched | CPUC % | US CA matched | US CA unmatched | US CA % |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 km | ±1 d | 72 | 3,673 | 1.92 | 71 | 10,097 | 0.70 |
| 1 km | ±3 d | 77 | 3,668 | 2.06 | 76 | 10,092 | 0.75 |
| 1 km | ±7 d | 88 | 3,657 | 2.35 | 86 | 10,082 | 0.85 |
| 5 km | ±1 d | 144 | 3,601 | 3.85 | 149 | 10,019 | 1.47 |
| 5 km | ±3 d | 193 | 3,552 | 5.15 | 216 | 9,952 | 2.12 |
| 5 km | ±7 d | 270 | 3,475 | 7.21 | 352 | 9,816 | 3.46 |
| 10 km | ±1 d | 246 | 3,499 | 6.57 | 269 | 9,899 | 2.65 |
| 10 km | ±3 d | 363 | 3,382 | 9.69 | 462 | 9,706 | 4.54 |
| 10 km | ±7 d | 543 | 3,202 | 14.50 | 827 | 9,341 | 8.13 |

The 10 km / ±7 d cell is the *least* defensible as “same fire.” Even there, **85.5% of CPUC ignitions** have no US CA neighbor. Asymmetry is the expected direction (US is all-cause and larger) but the absolute CPUC→US rate stays low.

**Table 5. CAL FIRE default ↔ US CA (years 2014–2015, 2017–2025). CAL FIRE n = 2,127. US CA n = 13,138.**

| Distance | Time | CAL FIRE matched | CAL FIRE unmatched | CAL FIRE % | US CA matched | US CA unmatched | US CA % |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 km | ±1 d | 70 | 2,057 | 3.29 | 70 | 13,068 | 0.53 |
| 1 km | ±3 d | 75 | 2,052 | 3.53 | 75 | 13,063 | 0.57 |
| 1 km | ±7 d | 80 | 2,047 | 3.76 | 82 | 13,056 | 0.62 |
| 5 km | ±1 d | 125 | 2,002 | 5.88 | 127 | 13,011 | 0.97 |
| 5 km | ±3 d | 150 | 1,977 | 7.05 | 158 | 12,980 | 1.20 |
| 5 km | ±7 d | 188 | 1,939 | 8.84 | 209 | 12,929 | 1.59 |
| 10 km | ±1 d | 180 | 1,947 | 8.46 | 190 | 12,948 | 1.45 |
| 10 km | ±3 d | 244 | 1,883 | 11.47 | 260 | 12,878 | 1.98 |
| 10 km | ±7 d | 327 | 1,800 | 15.37 | 398 | 12,740 | 3.03 |

If US CA were a sample of the same physical incidents CAL FIRE posts, US→CAL FIRE at 1 km / ±1 d would be high. It is **0.53%**. CAL FIRE→US at the loosest cell is **15.4%**, so a large majority is still unmatched. We should not call any cell in Tables 3–5 a match *rate of the same fires*. They are upper bounds on crude proximity.

**Matching is unreliable as identity, and that is a finding.** Different event definitions, different date semantics, no shared keys, and US is a sample rather than a census. The sensitivity grid does not hide a high-match regime we failed to pick.

### 4. US sampling rate vs CAL FIRE cannot be estimated; US is not a cNHPP panel

**What we verified about controls.** `extract_us_ignitions.py` keeps sequences with 15 Yes days, drops 0-Yes (synthesized negatives) and full-sentinel blocks. The loaded table has no control flag because controls were never inserted. We can tell events from controls: **the warehouse is events-only**.

**Why a CAL FIRE denominator does not yield a sampling fraction**

1. Count ratio in complete years (2020–2024): US CA **9,880** / CAL FIRE default **1,323** = **7.47**. A subsample of a census cannot be 7× the census.
2. Proximity: US CA → CAL FIRE is **0.5–3.0%** across the grid in Table 5. The extra US points are not the same incidents sitting slightly off the CAL FIRE coordinate.
3. CAL FIRE is not an ignition census. Under the default filter, 2020–2025 median acres = 53; **43 / 1,878** are under 10 acres. CPUC already has more CA rows than CAL FIRE in every overlapping year.
4. We do **not** have a complete IRWIN/NIFC CA ignition list in this warehouse, so we also cannot estimate US / IRWIN.

**What we can say instead.** US ignitions is a California-heavy classification sample of IRWIN-derived positives (40.09% of rows in CA by county PIP; 58.7% of 2024). It is useful as a map layer with that caveat. It is **not** a cell-day census, has no grid index, has synthetic-control design in the source corpus, and does not line up with CPUC or CAL FIRE events. It should not be concatenated into cNHPP training (`events_YYYY.csv`) or treated as an all-cause label for the 824-cell CA grid.

### 5. County relationships do not hold still

Counties were chosen *before* seeing the counts, for contrasting profiles: Sacramento (urban/valley), Los Angeles (large metro / IOU), Butte and Lake (high-fire north), Imperial and San Francisco (sparse). All three datasets use **spatial** county: `ST_Covers` vs Census TIGER `wildfire.counties` (NAME, no “County” suffix). CAL FIRE `incident_county` text is shown as a check; **55** default rows list multiple counties in text.

**Filters for Table 6:** years **2020–2025**; CPUC `county =`; CAL FIRE default Wildfire/Fire, dated, spatial county; US CA spatial county. 2025 US is still truncated, so county US counts are slightly low relative to a full 2025.

**Table 6. County counts, 2020–2025**

| County | CPUC | CAL FIRE spatial | CAL FIRE text exact | US CA | CPUC : CAL FIRE | US : CAL FIRE |
|---|---:|---:|---:|---:|---:|---:|
| Sacramento | 13 | 27 | 27 | 13 | 0.48 | 0.48 |
| Los Angeles | 264 | 78 | 79 | 2,294 | 3.39 | 29.41 |
| Butte | 110 | 44 | 43 | 392 | 2.50 | 8.91 |
| Lake | 26 | 25 | 24 | 73 | 1.04 | 2.92 |
| Imperial | 0 | 6 | 5 | 6 | 0 | 1.00 |
| San Francisco | 4 | 0 | 0 | 1 | n/a | n/a |
| **Statewide (same years)** | **3,745** | **1,878** | n/a | **10,168** | **1.99** | **5.41** |

The statewide CPUC:CAL FIRE ≈ 2 and US:CAL FIRE ≈ 5 **do not** describe these counties. Los Angeles is US-heavy (US 29× CAL FIRE). Sacramento is the reverse (CAL FIRE > CPUC = US). Imperial has no CPUC rows. San Francisco has no default CAL FIRE point inside the county polygon. Text vs spatial CAL FIRE differs by 0–1 in this set.

**Table 7. County proximity at one mid cell (5 km, ±3 d), 2020–2025**

| County | CPUC n | CPUC matched to CAL FIRE | CPUC % | US n | US matched to CAL FIRE | US % | CAL FIRE spatial n |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sacramento | 13 | 0 | 0.0 | 13 | 0 | 0.0 | 27 |
| Los Angeles | 264 | 1 | 0.38 | 2,294 | 5 | 0.22 | 78 |
| Butte | 110 | 6 | 5.45 | 392 | 4 | 1.02 | 44 |
| Lake | 26 | 1 | 3.85 | 73 | 0 | 0.0 | 25 |
| Imperial | 0 | 0 | n/a | 6 | 0 | 0.0 | 6 |
| San Francisco | 4 | 0 | 0.0 | 1 | 0 | 0.0 | 0 |

County n is small except LA / Butte. We should not over-interpret 0% vs 5%. What holds is the *absence* of a high-match county in this set. LA’s huge US pile is not sitting on CAL FIRE incidents.

---

## Caveats currently attached in `services/agent/caveats.py`

No code was changed. Recommendations only.

### CPUC: `cpuc_utility_caused`

> CPUC ignitions in this warehouse are utility-caused / utility-attributed only; they are not all-cause wildfire counts and are not comparable to CAL FIRE or US ignitions.

**Revise, keep the claim.** The definitional sentence is correct and now has measurements: 2020–2024 CPUC 3,190 vs CAL FIRE default 1,323 vs US CA 9,880, and CPUC→CAL FIRE proximity 1.4–5.6% across the grid. Optional addition: attribute `utility=` ≠ spatial territory containment (PGE 2024 **532** vs **536**, re-verified).

### US: `us_ignitions_sample`

Base text (when metadata `notes` is absent):

> US ignitions are an all-cause FireCastRL classification sample, not a complete census and not comparable to CPUC utility ignitions.

Service metadata also injects California-heavy ≈40% / ≈59% of 2024.

**Revise, keep the claim.** Add, in substance: (1) the loaded table is **positives only** (synthetic controls were dropped at extract); (2) CA share by `ST_Covers` vs `wildfire.counties` is **13,413 / 33,457 = 40.09%** (state-PIP was 13,432 / 40.15%; 2024 CA 2,225 / 58.7% either method); (3) **do not state a sampling fraction vs CAL FIRE**: US CA is larger than default CAL FIRE and <4% of US CA points fall within 10 km / ±7 d of a CAL FIRE Wildfire/Fire incident; (4) not a cell-day census, not for cNHPP. The current “not comparable to CPUC” line is supported (CPUC→US 1.9–14.5% depending on threshold; even 14.5% is the loosest cell).

### CAL FIRE: `calfire_missingness`

> CAL FIRE has 1,234 records without incident type and 282 without utility tags; default counts include only Wildfire/Fire incident types.

**Keep the numbers** (re-verified). **Revise the implication.** Default vs all-dated is not a small footnote in 2013–2018 (2017: 11 vs 429). Also unstated today: **2** undated rows; median **53.5 acres** under the default filter; **825 / 2,509** default Wildfire/Fire rows have `is_calfire_incident = false` and still enter default counts. Insufficient evidence to explain the 2024 jump (133 → 611).

### Attribute vs spatial (already injected per utility)

PGE 2024 532 vs 536 **keep**. Not newly in doubt.

---

## Methods

### Data

Live PostGIS 16 (`wildfire` schema). Source map files remain in sibling `dataset_demo/` (read-only). US extract: `data/north_america/Wildfire_Dataset.csv` → `us_ignitions_extracted.csv` (both gitignored). Analysis does not modify `models.py`, `grid_data_prep.py`, or agent caveat code.

### California for US ignitions

No state column. Primary filter: `ST_Covers(c.geom, u.geom)` against `wildfire.counties` (58 counties, `statefp = '06'`), matching CPUC county tagging. Cross-check: 0 intersects-but-not-covers. Established alternate: PublicaMundi state GeoJSON + 0.5° nearest fallback (`data/north_america/_us_ignitions_state_breakdown.json`) = 13,432 CA. We report both; matching and county tables use county `ST_Covers`.

### Matching

Temp tables with `geometry::geography` and GIST indexes. One join per pair at 10 km / ±7 d; tighter cells counted in Python from those pairs (EXISTS / any-neighbor, both directions). Distances in meters on geography. Years restricted to the pairwise overlap listed under each table. CAL FIRE side of matching = Wildfire/Fire default, dated.

### County slices

Pre-specified list, not selected for a narrative. Spatial PIP for all three. CAL FIRE text county reported as a diagnostic only.

### Cross-checks (live)

| Check | Expected | Measured |
|---|---|---|
| US total after extract | ~33,457 | **33,457** |
| PGE 2024 CPUC attribute | ~532 | **532** |
| PGE 2024 CPUC spatial (`ST_Within` PGE territory) | ~536 | **536** |
| CAL FIRE null `incident_type` | 1,234 | **1,234** |
| CAL FIRE null `utility` | 282 | **282** |
| CPUC county resolved / outside | 3,743 / 2 | **3,743 / 2** |
| US 2024 CA share | ≈58.7% | **2,225 / 3,789 = 58.72%** |

### What we could not determine

- Sampling fraction of US vs a true CA ignition census (IRWIN/NIFC complete list is not in this warehouse; CAL FIRE is the wrong denominator).
- Whether a US point and a CAL FIRE incident are the same IRWIN record (no IRWIN ID on `us_ignitions`).
- Cause overlap (US has no cause; CPUC is utility-attributed by construction).
- Meaning of `is_calfire_incident = false` on 825 default Wildfire/Fire rows.
- Why CAL FIRE default counts jump in 2024.
- Unique 1–1 fire identity; we only measured proximity.
- Time-of-day alignment (`cpuc_ignitions_with_time` not used).
- CPZ overlay (not in `dataset_demo`).

---

## Bottom line for the meeting

Use CPUC for utility-attributed ignition questions, CAL FIRE for posted wildfire *incidents* with the Wildfire/Fire default and the 2013–2018 untyped hole stated, and US ignitions as a CA-heavy IRWIN sample on the map. Do not compare their counts. Do not estimate a US sampling rate from CAL FIRE. Do not train cNHPP on US ignitions.
