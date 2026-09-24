# CAL FIRE 2023→2024 jump: reporting artifact, not a 4.6× fire year

**Warehouse:** PostGIS `localhost:5433/wildfire`, queried 2026-08-14.  
**Repro:** `python analysis/calfire_2024_jump.py` (JSON beside this file).  
**Source file:** sibling `dataset_demo/assets/data/calfire_incidents.csv` (3747 rows = table count).

> **Status note (2026-09-23):** The findings below are a dated snapshot and are
> not re-derived. "No caveat code was changed in this task" was true of the
> investigation. The agent now carries a trend caveat,
> `calfire_map_feed_counts` (`services/agent/caveats.py:29`), attached when a
> CAL FIRE answer spans 2023 and 2024 or compares counts across years
> (`_needs_calfire_map_feed_caveat`, `services/agent/caveats.py:559`). Its
> wording differs from the draft under "Caveat the agent should attach". The
> Wildfire/Fire default is still applied at query time
> (`services/data_query/queries.py:329-330`).

---

## Conclusion

The 133 → 611 jump (**4.59×**) under the default Wildfire/Fire filter is **in the CAL FIRE incident-map feed**, not introduced by our loader or `incident_type` filter.

It is **not** a 4.6× increase in California wildfires. Official CAL FIRE Redbook counts went **7,386 → 8,110** (**+10%**). Acres burned did rise sharply (**332,822 → 1,077,711**, **3.2×**), driven in large part by the Park Fire (429,603 acres).

What changed in *this table* is how many incidents CAL FIRE **posts on the incident map**. In 2023 the map listed **1.8%** of Redbook fires and still captured **~97%** of official acres. In 2024 it listed **7.5%** of Redbook fires and captured **~95%** of acres. Same kind of product (notable posted incidents, not a census) with a **higher posting rate from 2024 onward** (2025 stays high at 555).

**Hypothesis 1 (real 4.6× fire-count year):** rejected for counts; acres really were worse.  
**Hypothesis 4 (type field newly populated):** rejected.  
**Hypothesis 5 (our scraper/loader):** rejected as the cause of the jump.  
**Hypothesis 2 (feed pruning of 2023):** does not explain the cliff.  
**Hypothesis 3 (smaller incidents posted):** supported as the main composition change, together with more large fires actually occurring.

The agent should not report a 2023-to-2024 CAL FIRE *count* trend from this warehouse as California fire activity.

---

## What this table is

The CSV is `https://incidents.fire.ca.gov/imapdata/mapdataall.csv`, the developer dump for [fire.ca.gov/incidents](https://www.fire.ca.gov/incidents), not the Redbook. CAL FIRE’s own incident pages say most fires are contained quickly and “no information will generally be provided” on the site.

Live HTML archives (fetched 2026-08-14) match the warehouse:

| Year | HTML incident list | Warehouse default Wildfire/Fire | Warehouse all dated |
|-----:|-------------------:|--------------------------------:|--------------------:|
| 2023 | 133 entries (page header: **7,386** wildfires / **332,822** acres) | 133 | 133 |
| 2024 | 612 table rows including 1 Earthquake | 611 | 612 |

The page headers are the census. The tables are the map feed. We store the latter.

---

## Hypothesis tests

### 1. It’s real (4.6× more fires)

**External ground truth (CAL FIRE Redbook, not our warehouse)**

| Source | 2023 fires | 2023 acres | 2024 fires | 2024 acres | Fire-count ratio |
|---|---:|---:|---:|---:|---:|
| CAL FIRE Redbook (all agencies) | 7,386 | 332,822 | 8,110 | 1,077,711 | **1.10** |
| CAL FIRE + local contracts only | 5,744 | 29,907 | 6,928 | 588,782 | 1.21 |
| Wikipedia / preliminary 2023 | 7,127 | 324,917 | 8,110 | 1,077,711 | 1.14 |
| FRAP statewide perimeters added | 284 (Firep23_1) | n/a | 548 (Firep24_1) | n/a | 1.93 |
| **This warehouse, default filter** | **133** | **322,983** | **611** | **1,025,720** | **4.59** |

Redbook 2023: [2023_redbook_final.pdf](https://www.fire.ca.gov/our-impact/statistics). Redbook 2024: [2024_redbook_final.pdf](https://www.fire.ca.gov/our-impact/statistics). FRAP from CAL FIRE Fire Perimeters release notes.

**For:** 2024 *was* a worse **acreage** year (Park Fire; most acres since 2021). Warehouse acre totals track Redbook acres (97% in 2023, 95% in 2024). Fires ≥1,000 acres in the map feed: 18 → 54.

**Against a 4.6× count year:** every official fire-*count* series is ~1.1–1.9×, not 4.6×. Using 133 vs 611 as “California had 4.6 times as many fires” would be a false finding.

### 2. Data vintage / feed pruning

**Test:** `date_last_update` by create year; whether older years decay smoothly; whether git snapshots of the CSV show 2023 shrinking.

| Create year | n (default) | Median days created → last update | Last-update year mix |
|---:|---:|---:|---|
| 2020 | 257 | 6.6 | still touched as late as 2025-08-14 |
| 2021 | 172 | 6.1 | |
| 2022 | 150 | 6.2 | |
| 2023 | 133 | 2.9 | **114 in 2023, 18 in 2024, 1 in 2025** |
| 2024 | 611 | 3.0 | 99 updated in 2025 |
| 2025 | 555 | 1.9 | |

Four CSV commits in `dataset_demo` (2026-07-23 … 2026-08-03) all have **133 / 612** for 2023 / 2024. Only 2026 rows change. We have never observed 2023 being pruned in our copies.

**For a mild older-year thinning:** 2020→2023 default counts decline 257 → 172 → 150 → 133. Untyped 2013–2018 is a separate hole (already in the comparison memo).

**Against pruning as the 2023→2024 cliff:** 2023 rows are still in the live feed and on the 2023 archive page. Update lag for 2023 is the same order as 2024. 2025 is still high (555). A prune-the-past process would not jump from 133 to 611 and stay there.

### 3. Reporting threshold / posting mix

**Filters:** `incident_type IN ('Wildfire','Fire')` and `date_only_created` not null.

| Year | n | Median acres | P10 | P75 | % &lt;100 acres | n &lt;100 | n ≥100 | n ≥1,000 | Sum acres |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 150 | 91.5 | 21 | 323 | 51.5 | 69 | 65 | 17 | 278,246 |
| 2023 | 133 | **70** | 15 | 296 | **58.7** | **71** | **50** | **18** | 322,983 |
| 2024 | 611 | **43** | 11 | 150 | **69.3** | **422** | **187** | **54** | 1,025,720 |
| 2025 | 555 | 30 | 10 | 86 | 76.6 | 425 | 130 | 36 | 527,707 |

Under-10-acre share only goes 0.8% → 2.5% (not the whole story). The mass of new rows is **10–100 acres**: 71 → 422. Median drops 70 → 43 and stays lower in 2025–2026 (30–31).

Large posted fires also rose (18 → 54 at ≥1,000 acres; 50 → 187 at ≥100). That piece lines up with a real worse acreage year, not only a threshold change.

Posted share of the Redbook census:

| Year | Map-feed n / Redbook fires | Map-feed acres / Redbook acres |
|---:|---:|---:|
| 2023 | 133 / 7,386 = **1.8%** | 322,983 / 332,822 = **97.0%** |
| 2024 | 611 / 8,110 = **7.5%** | 1,025,720 / 1,077,711 = **95.2%** |

The feed remains an **acreage-covering** sample of notable incidents. In 2024 CAL FIRE put **about four times as many of the small-and-medium fires** on the map without covering more of the state’s acres (already ~95%).

Monthly shape is fire-season, not a dump: 2024 May–October = 65, 137, 182, 88, 53, 45.

**Supported:** posting mix shifted toward smaller incidents starting 2024 and continuing in 2025. Cannot name a published acre threshold from CAL FIRE docs; the distribution change is the evidence.

### 4. `incident_type` population

| Year | Default Wildfire/Fire | All dated | Untyped | Other type |
|---:|---:|---:|---:|---:|
| 2022 | 150 | 150 | 0 | 0 |
| 2023 | 133 | 133 | 0 | 0 |
| 2024 | 611 | 612 | 0 | 1 (Earthquake) |
| 2025 | 555 | 556 | 0 | 1 |

2023 is 132 Wildfire + 1 Fire. 2024 is 609 Wildfire + 2 Fire + 1 Earthquake. The default filter and the all-types series **jump together**. Untyped mass is 2013–2018, not 2023.

**Rejected.**

### 5. Scraper / loader change on our side

- Workflow `dataset_demo/.github/workflows/refresh-calfire.yml` added **2026-07-23**. It `curl`s `mapdataall.csv` and runs `scripts/tag_calfire_utility.py` (IOU PIP tag only; does not drop rows).
- Four CSV commits: 2023=133 and 2024=612 in **every** snapshot. We did not create the jump between refreshes.
- `db/loaders/load_calfire.py` truncates and inserts every CSV row. Wildfire/Fire is a **query-time** default in `services/data_query/queries.py`, not a load filter.
- Jump is already in the raw CSV year field `incident_dateonly_created`.

**Rejected** as introducing 133→611. (We also have no 2023-vintage scrape; we cannot prove what the map feed contained in December 2023.)

---

## Still undetermined

- The exact CAL FIRE policy or CMS change that raised the 2024 posting rate. No public changelog found.
- Whether some 2023 map incidents were removed *before* our first scrape in July 2026. Current HTML archive = 133, so if pruning happened it happened on CAL FIRE’s side and is now the public record.
- Why 2019–2020 map counts (207, 257) are higher than 2021–2023 without a Redbook count crash of that size. Possibly earlier posting-mix variation; out of scope here.

---

## Caveat the agent should attach

When a CAL FIRE count or comparison **crosses 2023–2024** (or presents those two years as a trend), attach something like:

> CAL FIRE rows in this warehouse are incidents from the public incident-map feed (`mapdataall.csv`), not CAL FIRE’s annual Redbook census. The feed listed 133 Wildfire/Fire incidents in 2023 and 611 in 2024 (4.6×). Official Redbook counts were 7,386 fires / 332,822 acres (2023) vs 8,110 fires / 1,077,711 acres (2024). Do not read the map-feed count ratio as a California fire-occurrence trend. The 2024 map list includes many more sub-100-acre incidents; acres in the feed still track the Redbook totals.

Existing `calfire_missingness` (1,234 untyped / 282 untagged; default Wildfire/Fire) stays true and does **not** explain this jump. Add a separate trend caveat; do not overload the missingness sentence.

No caveat code was changed in this task.
