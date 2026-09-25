# Data change: the CAL FIRE default counts untyped incidents

**Decision:** Michael, 2026-09-24. The default CAL FIRE population changes from
"incidents typed Wildfire or Fire" to "every incident except the known
non-wildfire types".

**Applies from:** the deploy of the release that contains branch
`calfire-default`. The decision is dated 2026-09-24. Until that release is
deployed, production answers still use the old default. Answers given before
the deploy are not revised. Every figure below was measured on 2026-09-24
against the local warehouse (`wildfire.calfire_incidents`, 3,747 rows).

## What changed

| | Before | After |
|---|---|---|
| Default rows | `incident_type IN ('Wildfire', 'Fire')` | `incident_type IS NULL OR incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat')` |
| Rows counted (all years) | 2,509 | 3,743 |
| Incidents with no type recorded | excluded (1,234) | counted (1,234) |
| Where it is defined | `CALFIRE_DEFAULT_INCIDENT_TYPES` | `CALFIRE_NON_WILDFIRE_INCIDENT_TYPES` in `services/shared/naming.py`, read through `services/shared/dataset_registry.py` |
| Missingness caveat (`calfire_missingness`) | Every CAL FIRE answer: "CAL FIRE has 1,234 records without incident type and 282 without utility tags; default counts include only Wildfire/Fire incident types." | Only when the counted result includes untyped incidents: "N of the counted CAL FIRE incidents have no incident type recorded.", then a sentence for the `incident_type_mode` the count used. Default: "CAL FIRE counts include every incident except the known non-wildfire types (Earthquake, Flood, Hazmat), so incidents with no type are counted." All types: "This count includes every incident type, non-wildfire types too, so incidents with no type are counted." Untyped only: "This count is of incidents with no type recorded only." Only the default text names the excluded types. |
| Utility-tag caveat (`calfire_untagged_utility`) | Part of the sentence above: the table-wide 282 on every CAL FIRE answer | Its own caveat, only on utility-scoped CAL FIRE results, shown only when its number is above zero. On a count by utility tag (a plain utility filter, a utility comparison, or a period comparison scoped to one utility, with the default attribute definition): "N CAL FIRE incidents in the same period and scope have no utility tag recorded and are not counted toward any utility." (`untagged_incidents_excluded`; for a comparison, summed over its periods). On `include_untagged`, `utility=untagged`, a territory spatial summary, or a spatial comparison: "N of the counted CAL FIRE incidents have no utility tag recorded, so the tag does not attribute them to any utility." (`untagged_incidents_counted`, reported on every CAL FIRE result). |

A plain filter such as `utility=PGE` counts only incidents tagged PG&E, so
the caveat states the untagged incidents it leaves out: the same query with
the utility clause replaced by "no utility tag" (same period, county, type,
and acreage filters). Checked against SQL in `tests/test_calfire_default.py`:

| Filter | Year | Incidents counted | Untagged in the same period and scope |
|---|---|---:|---:|
| `utility=PGE` | 2017 | 257 | 31 |
| `utility=SCE` | 2017 | 96 | 31 |
| `utility=PGE` | 2020 | 162 | 26 |
| `utility=SCE` | 2020 | 53 | 26 |

The other utility scopes state the untagged incidents they counted, for
example `include_untagged=true` (SCE 2020: 26 of 79) and `utility=untagged`
(281). A utility-scoped CAL FIRE result that does not report the figure its
case needs suppresses the answer, as for the untyped figure.

### Distinct incident types in the warehouse

Measured with `SELECT incident_type, count(*) FROM wildfire.calfire_incidents GROUP BY 1`.

| Stored type | Rows | Default before | Default after | Why |
|---|---:|---|---|---|
| Wildfire | 2,471 | counted | counted | wildfire |
| (no type recorded) | 1,234 | excluded | counted | Michael's decision: only known non-wildfire types are excluded |
| Fire | 38 | counted | counted | wildfire |
| Flood | 2 | excluded | excluded | not a vegetation fire: "Honey Flooding" (2018-11-30, Butte) and "Eaton Flood" (2025-02-13, Los Angeles), post-fire flooding |
| Earthquake | 1 | excluded | excluded | not a vegetation fire: "7.0 Earthquake" (2024-12-05), a USGS event off Petrolia |
| Hazmat | 1 | excluded | excluded | not a vegetation fire: "Garden Grove HAZMAT" (2026-05-22, Orange) |

A type that appears in a later load is counted until it is added to
`CALFIRE_NON_WILDFIRE_INCIDENT_TYPES`. `db/loaders/validate.py` prints any
stored type that is in neither the excluded list nor
`CALFIRE_REVIEWED_WILDFIRE_INCIDENT_TYPES` (Wildfire, Fire), and
`tests/test_validate.py` fails on one.

### Untyped rows that are counted but may not be wildfires

1,210 of the 1,234 untyped incidents have "Fire" in the name. Of the other
24, most are lightning or named complexes ("Modoc Lightning Complex",
"Eclipse Complex"). Two read as non-wildfire by name and are counted under the
rule, because the rule excludes by recorded type, not by name:

- "Oroville Spillway" (2017-02-07, Butte, 0 acres)
- "King Incident" (2017-06-24, Tulare, 0 acres)

Michael confirmed on 2026-09-24 that both stay counted under the rule, as
documented here. The code does not special-case them.

## Deploying

- **Deploy the services and the agent together.** The agent requires
  `untyped_incidents_counted` on every CAL FIRE result and
  `untagged_incidents_counted` on every utility-scoped one (and
  `untagged_incidents_excluded` on a plain utility filter), and suppresses
  the answer when a service does not report them. An agent running against
  services from before this change would suppress every CAL FIRE answer.
  Restart all of `wildfire-data-query`, `wildfire-visualization`,
  `wildfire-comparison`, and `wildfire-agent` from the same commit
  (`docs/DEPLOY_RUNBOOK.md`, section 3).
- **PR #93 merged first (2026-09-24), so this branch was rebased onto it.**
  PR #93's CAL FIRE default query definition already calls
  `calfire_default_type_sql`, so it measures the new default; only its
  reason wording read the removed `CALFIRE_DEFAULT_INCIDENT_TYPES` and now
  reads "of the default incident types (every type except Earthquake, Flood,
  Hazmat)". `shared/dataset_coverage.json` was regenerated with
  `python -m db.loaders.coverage`. Only the CAL FIRE default entry changed:
  3,743 rows instead of 2,509, 2013 (141) and 2016 (155) now have rows, and
  BVES now has default rows (2013: 2, 2015: 1) as does Liberty from 2014.

### What the coverage change changes

Compared with `platform/main` at the PR #93 merge (`f0aa04b`):

- **Eval rows, probes, and smoke questions:** none. Router path, rule,
  answer text, tool arguments, and the executor's coverage gap for every
  planned call are identical for all 426 questions (cases.json 107,
  jev_paraphrases.json 41, holdouts v1 97, v2 65, v3 88, issue #97 probes 22,
  smoke 6). The decide replay report is byte-identical. No label changes.
- **Tests:** every test still passes, but these PR #93 cases read the
  measured file and now take the other branch:
  - `test_cal_fire_2013_is_answered_from_the_rows_the_default_count_reads`
    (4 questions): was the not-covered clarification ("no rows between 2009
    and 2014"), now the deterministic count (141).
  - `test_bear_valley_cal_fire_years_are_measured_on_the_default_count`, 2013 and 2015 (4 questions):
    was not covered for Bear Valley, now answered (2 and 1). Three of the four
    route deterministically ("Count BVES CAL FIRE incidents for 2013", "How
    many CAL FIRE incidents did Bear Valley have in 2013?", "How many CAL
    FIRE fires were tagged to Bear Valley in 2015?" moved from clarification
    to `filtered_records`); the fourth goes to the model path on both.
  - `test_bear_valley_psps_and_cpuc_are_not_covered`, the two questions with
    no year: the clarification now also offers "Bear Valley's CAL FIRE
    incidents" (the PSPS and CPUC not-covered sentence is unchanged).
  - `test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows`, 2014 to 2017: the
    clarification now offers "Liberty's CAL FIRE incidents in <year>" (2016
    routes to the model path on both, where the executor makes the offer).
  - `test_a_year_between_measured_rows_is_not_covered`: now checks 2010 to
    2012 with "between 2009 and 2013" (was 2010 to 2013, "and 2014").
  - Unrelated to coverage: `tests/agent/test_coverage_one_source.py` faked a
    spatial summary without the CAL FIRE figures the real service now
    reports; the fake now returns them (both 0), and its answers are
    unchanged.

## What does not change

- Every count for incidents created in 2022 to 2026: those years have no
  untyped incidents, and the four excluded rows were excluded before too.
  The 2023 to 2024 map-feed jump (133 to 611) in `calfire_map_feed_counts`
  is the same under both defaults; only its wording changed from
  "Wildfire/Fire count" to "incident count".
- `incident_type=all` (3,747) and `incident_type=untyped` (1,234).
- Gold labels. The only eval rows that store a CAL FIRE count are
  `model_sacramento_tell_me_about_2024` (Sacramento County 2024: 11 before and
  after) and `rank_calfire_counties_2023` (2023 is unchanged).
- Routes: 0 of 398 eval questions changed path or rule, and the router's tool
  arguments are identical (cases.json 107, jev_paraphrases.json 41, holdout v1
  97, v2 65, v3 88).

## Reproducing an old value

The old default is still reachable as an explicit list of stored types:
`incident_type=Wildfire,Fire` on `/calfire/incidents`, `/rank`,
`/map-layer`, and `/time-series`. The agent's `incident_type_mode` keeps its
three values; `wildfire_default` now means the new default.

## Counts that change

Before is `incident_type IN ('Wildfire', 'Fire')`; after is the new default.
Rows with no created date (`date_only_created` null) are listed as "no date";
year filters never include them. Acres are `SUM(acres_burned)`.

### Statewide, by created year

Every year is listed; 2022 onward is unchanged.

| Created year | Count before | Count after | Change | Untyped counted | Acres before | Acres after |
|---|---:|---:|---:|---:|---:|---:|
| 2009 | 1 | 1 | 0 | 0 | 122 | 122 |
| 2013 | 0 | 141 | +141 | 141 | 0 | 497,485 |
| 2014 | 1 | 76 | +75 | 75 | 4,240 | 296,982 |
| 2015 | 2 | 99 | +97 | 97 | 78,919 | 414,202 |
| 2016 | 0 | 155 | +155 | 155 | 0 | 452,101 |
| 2017 | 11 | 429 | +418 | 418 | 462,383 | 1,449,419 |
| 2018 | 28 | 302 | +274 | 274 | 1,188,329 | 1,569,513 |
| 2019 | 207 | 263 | +56 | 56 | 267,549 | 286,810 |
| 2020 | 257 | 259 | +2 | 2 | 2,941,860 | 2,942,034 |
| 2021 | 172 | 186 | +14 | 14 | 2,289,814 | 2,292,321 |
| 2022 | 150 | 150 | 0 | 0 | 278,246 | 278,246 |
| 2023 | 133 | 133 | 0 | 0 | 322,983 | 322,983 |
| 2024 | 611 | 611 | 0 | 0 | 1,025,720 | 1,025,720 |
| 2025 | 555 | 555 | 0 | 0 | 527,707 | 527,707 |
| 2026 | 381 | 381 | 0 | 0 | 206,467 | 206,467 |
| no date | 0 | 2 | +2 | 2 | 0 | 57 |
| **All** | **2,509** | **3,743** | **+1,234** | **1,234** | | |

### By utility tag (attribute definition), all years

| Utility tag | Count before | Count after | Change | Acres before | Acres after |
|---|---:|---:|---:|---:|---:|
| BVES | 0 | 3 | +3 | 0 | 3,041 |
| Liberty | 4 | 10 | +6 | 118,989 | 128,941 |
| PACIFICORP | 113 | 174 | +61 | 954,768 | 1,219,754 |
| PGE | 1,482 | 2,254 | +772 | 6,318,201 | 8,052,500 |
| SCE | 587 | 816 | +229 | 1,543,166 | 1,961,120 |
| SDGE | 133 | 205 | +72 | 60,542 | 109,295 |
| (untagged) | 190 | 281 | +91 | 598,673 | 1,087,518 |

### By utility tag and created year (changed cells only)

| Created year | Utility tag | Count before | Count after | Change |
|---|---|---:|---:|---:|
| 2013 | BVES | 0 | 2 | +2 |
| 2013 | PACIFICORP | 0 | 5 | +5 |
| 2013 | PGE | 0 | 88 | +88 |
| 2013 | SCE | 0 | 32 | +32 |
| 2013 | SDGE | 0 | 7 | +7 |
| 2013 | (untagged) | 0 | 7 | +7 |
| 2014 | Liberty | 0 | 2 | +2 |
| 2014 | PACIFICORP | 0 | 6 | +6 |
| 2014 | PGE | 1 | 38 | +37 |
| 2014 | SCE | 0 | 9 | +9 |
| 2014 | SDGE | 0 | 10 | +10 |
| 2014 | (untagged) | 0 | 11 | +11 |
| 2015 | BVES | 0 | 1 | +1 |
| 2015 | Liberty | 0 | 1 | +1 |
| 2015 | PACIFICORP | 0 | 4 | +4 |
| 2015 | PGE | 2 | 62 | +60 |
| 2015 | SCE | 0 | 15 | +15 |
| 2015 | SDGE | 0 | 6 | +6 |
| 2015 | (untagged) | 0 | 10 | +10 |
| 2016 | Liberty | 0 | 1 | +1 |
| 2016 | PACIFICORP | 0 | 7 | +7 |
| 2016 | PGE | 0 | 111 | +111 |
| 2016 | SCE | 0 | 22 | +22 |
| 2016 | SDGE | 0 | 8 | +8 |
| 2016 | (untagged) | 0 | 6 | +6 |
| 2017 | Liberty | 0 | 2 | +2 |
| 2017 | PACIFICORP | 0 | 24 | +24 |
| 2017 | PGE | 8 | 257 | +249 |
| 2017 | SCE | 1 | 96 | +95 |
| 2017 | SDGE | 0 | 19 | +19 |
| 2017 | (untagged) | 2 | 31 | +29 |
| 2018 | PACIFICORP | 2 | 15 | +13 |
| 2018 | PGE | 19 | 197 | +178 |
| 2018 | SCE | 5 | 48 | +43 |
| 2018 | SDGE | 1 | 17 | +16 |
| 2018 | (untagged) | 1 | 25 | +24 |
| 2019 | PACIFICORP | 12 | 13 | +1 |
| 2019 | PGE | 116 | 156 | +40 |
| 2019 | SCE | 43 | 52 | +9 |
| 2019 | SDGE | 13 | 17 | +4 |
| 2019 | (untagged) | 23 | 25 | +2 |
| 2020 | SCE | 52 | 53 | +1 |
| 2020 | SDGE | 6 | 7 | +1 |
| 2021 | PACIFICORP | 14 | 15 | +1 |
| 2021 | PGE | 96 | 103 | +7 |
| 2021 | SCE | 33 | 36 | +3 |
| 2021 | SDGE | 10 | 11 | +1 |
| 2021 | (untagged) | 18 | 20 | +2 |
| no date | PGE | 0 | 2 | +2 |

### By IOU territory (spatial definition, `ST_Within`) and created year (changed cells only)

These are the counts `/spatial/summary` and the spatial utility comparison return.

| IOU territory | Created year | Count before | Count after | Change |
|---|---|---:|---:|---:|
| BVES | 2013 | 0 | 2 | +2 |
| BVES | 2015 | 0 | 1 | +1 |
| Liberty | 2014 | 0 | 2 | +2 |
| Liberty | 2015 | 0 | 1 | +1 |
| Liberty | 2016 | 0 | 1 | +1 |
| Liberty | 2017 | 0 | 2 | +2 |
| PACIFICORP | 2013 | 0 | 5 | +5 |
| PACIFICORP | 2014 | 0 | 6 | +6 |
| PACIFICORP | 2015 | 0 | 4 | +4 |
| PACIFICORP | 2016 | 0 | 7 | +7 |
| PACIFICORP | 2017 | 0 | 24 | +24 |
| PACIFICORP | 2018 | 2 | 16 | +14 |
| PACIFICORP | 2019 | 12 | 13 | +1 |
| PACIFICORP | 2021 | 14 | 15 | +1 |
| PGE | 2013 | 0 | 88 | +88 |
| PGE | 2014 | 1 | 38 | +37 |
| PGE | 2015 | 2 | 62 | +60 |
| PGE | 2016 | 0 | 111 | +111 |
| PGE | 2017 | 8 | 257 | +249 |
| PGE | 2018 | 19 | 197 | +178 |
| PGE | 2019 | 116 | 156 | +40 |
| PGE | 2021 | 96 | 103 | +7 |
| PGE | no date | 0 | 2 | +2 |
| SCE | 2013 | 0 | 32 | +32 |
| SCE | 2014 | 0 | 9 | +9 |
| SCE | 2015 | 0 | 15 | +15 |
| SCE | 2016 | 0 | 23 | +23 |
| SCE | 2017 | 1 | 98 | +97 |
| SCE | 2018 | 5 | 49 | +44 |
| SCE | 2019 | 43 | 53 | +10 |
| SCE | 2020 | 53 | 54 | +1 |
| SCE | 2021 | 34 | 37 | +3 |
| SDGE | 2013 | 0 | 7 | +7 |
| SDGE | 2014 | 0 | 10 | +10 |
| SDGE | 2015 | 0 | 6 | +6 |
| SDGE | 2016 | 0 | 8 | +8 |
| SDGE | 2017 | 0 | 19 | +19 |
| SDGE | 2018 | 1 | 17 | +16 |
| SDGE | 2019 | 13 | 17 | +4 |
| SDGE | 2020 | 6 | 7 | +1 |
| SDGE | 2021 | 10 | 11 | +1 |

### By HFTD tier (spatial) and created year (changed cells only)

| HFTD tier | Created year | Count before | Count after | Change |
|---|---|---:|---:|---:|
| Tier 2 | 2013 | 0 | 65 | +65 |
| Tier 2 | 2014 | 1 | 43 | +42 |
| Tier 2 | 2015 | 2 | 48 | +46 |
| Tier 2 | 2016 | 0 | 81 | +81 |
| Tier 2 | 2017 | 6 | 193 | +187 |
| Tier 2 | 2018 | 13 | 133 | +120 |
| Tier 2 | 2019 | 82 | 102 | +20 |
| Tier 2 | 2021 | 87 | 92 | +5 |
| Tier 3 | 2013 | 0 | 39 | +39 |
| Tier 3 | 2014 | 0 | 13 | +13 |
| Tier 3 | 2015 | 0 | 25 | +25 |
| Tier 3 | 2016 | 0 | 28 | +28 |
| Tier 3 | 2017 | 3 | 93 | +90 |
| Tier 3 | 2018 | 8 | 54 | +46 |
| Tier 3 | 2019 | 36 | 40 | +4 |
| Tier 3 | 2021 | 30 | 33 | +3 |

### By county, all years (changed counties only)

A multi-county incident ("Shasta, Tehama") counts in every county it lists,
as every county filter, ranking, and comparison does. 57 of 61 counties
change. Unchanged: Imperial, Mexico, State of Nevada, State of Oregon.

| County | Count before | Count after | Change | Acres before | Acres after |
|---|---:|---:|---:|---:|---:|
| Alameda | 49 | 67 | +18 | 402,127 | 406,727 |
| Alpine | 3 | 5 | +2 | 291,792 | 291,792 |
| Amador | 21 | 29 | +8 | 231,995 | 232,831 |
| Butte | 57 | 109 | +52 | 1,626,637 | 1,661,334 |
| Calaveras | 32 | 50 | +18 | 29,020 | 31,411 |
| Colusa | 15 | 18 | +3 | 1,515,510 | 1,515,683 |
| Contra Costa | 42 | 60 | +18 | 400,627 | 405,970 |
| Del Norte | 3 | 7 | +4 | 95,154 | 95,691 |
| El Dorado | 37 | 62 | +25 | 309,952 | 416,318 |
| Fresno | 168 | 219 | +51 | 571,305 | 783,467 |
| Glenn | 15 | 20 | +5 | 1,464,504 | 1,467,213 |
| Humboldt | 26 | 48 | +22 | 1,109,306 | 1,127,293 |
| Inyo | 15 | 24 | +9 | 38,260 | 51,201 |
| Kern | 166 | 220 | +54 | 225,752 | 343,731 |
| Kings | 15 | 20 | +5 | 5,252 | 59,400 |
| Lake | 39 | 74 | +35 | 1,875,047 | 1,982,944 |
| Lassen | 61 | 84 | +23 | 1,087,375 | 1,230,601 |
| Los Angeles | 110 | 144 | +34 | 305,351 | 382,572 |
| Madera | 60 | 92 | +32 | 387,690 | 417,624 |
| Marin | 6 | 11 | +5 | 5,206 | 5,488 |
| Mariposa | 42 | 61 | +19 | 55,724 | 253,616 |
| Mendocino | 38 | 55 | +17 | 1,534,103 | 1,536,876 |
| Merced | 77 | 89 | +12 | 10,302 | 20,999 |
| Modoc | 40 | 65 | +25 | 123,511 | 199,355 |
| Mono | 12 | 16 | +4 | 13,634 | 30,553 |
| Monterey | 51 | 88 | +37 | 90,859 | 240,375 |
| Napa | 25 | 41 | +16 | 670,683 | 711,872 |
| Nevada | 14 | 30 | +16 | 5,343 | 8,723 |
| (no county) | 1 | 10 | +9 | 49 | 233,525 |
| Orange | 9 | 18 | +9 | 80,337 | 93,965 |
| Placer | 36 | 47 | +11 | 102,943 | 138,603 |
| Plumas | 11 | 21 | +10 | 1,156,559 | 1,165,764 |
| Riverside | 279 | 387 | +108 | 182,621 | 258,704 |
| Sacramento | 33 | 41 | +8 | 9,066 | 10,789 |
| San Benito | 26 | 44 | +18 | 6,803 | 10,901 |
| San Bernardino | 102 | 147 | +45 | 236,265 | 326,463 |
| San Diego | 133 | 204 | +71 | 60,464 | 109,202 |
| San Joaquin | 25 | 28 | +3 | 438,192 | 438,704 |
| San Luis Obispo | 86 | 141 | +55 | 119,131 | 210,581 |
| San Mateo | 5 | 7 | +2 | 86,620 | 86,711 |
| Santa Barbara | 35 | 53 | +18 | 502,731 | 546,602 |
| Santa Clara | 32 | 59 | +27 | 404,382 | 410,911 |
| Santa Cruz | 7 | 11 | +4 | 86,764 | 87,204 |
| Shasta | 70 | 122 | +52 | 1,486,705 | 1,641,071 |
| Sierra | 6 | 8 | +2 | 64,654 | 65,569 |
| Siskiyou | 79 | 121 | +42 | 762,533 | 1,007,342 |
| Solano | 37 | 50 | +13 | 419,658 | 424,167 |
| Sonoma | 21 | 35 | +14 | 591,862 | 616,560 |
| Stanislaus | 28 | 41 | +13 | 412,414 | 423,451 |
| Sutter | 1 | 4 | +3 | 80 | 2,930 |
| Tehama | 60 | 101 | +41 | 2,645,751 | 2,679,542 |
| Trinity | 27 | 40 | +13 | 1,887,842 | 1,932,285 |
| Tulare | 63 | 94 | +31 | 67,911 | 144,793 |
| Tuolumne | 43 | 63 | +20 | 41,435 | 340,363 |
| Ventura | 34 | 45 | +11 | 448,475 | 455,837 |
| Yolo | 11 | 20 | +9 | 454,488 | 471,987 |
| Yuba | 33 | 45 | +12 | 14,804 | 16,676 |

### By county and created year (every changed cell)

308 of 662 county-year cells change.

| Created year | County | Count before | Count after | Change |
|---|---|---:|---:|---:|
| 2013 | Alameda | 0 | 3 | +3 |
| 2013 | Amador | 0 | 1 | +1 |
| 2013 | Butte | 0 | 5 | +5 |
| 2013 | Calaveras | 0 | 2 | +2 |
| 2013 | Colusa | 0 | 1 | +1 |
| 2013 | Contra Costa | 0 | 3 | +3 |
| 2013 | El Dorado | 0 | 4 | +4 |
| 2013 | Fresno | 0 | 5 | +5 |
| 2013 | Glenn | 0 | 1 | +1 |
| 2013 | Humboldt | 0 | 2 | +2 |
| 2013 | Inyo | 0 | 1 | +1 |
| 2013 | Kern | 0 | 4 | +4 |
| 2013 | Lake | 0 | 9 | +9 |
| 2013 | Lassen | 0 | 1 | +1 |
| 2013 | Los Angeles | 0 | 4 | +4 |
| 2013 | Madera | 0 | 4 | +4 |
| 2013 | Mariposa | 0 | 1 | +1 |
| 2013 | Mendocino | 0 | 3 | +3 |
| 2013 | Merced | 0 | 1 | +1 |
| 2013 | Modoc | 0 | 3 | +3 |
| 2013 | Monterey | 0 | 1 | +1 |
| 2013 | Napa | 0 | 3 | +3 |
| 2013 | Nevada | 0 | 1 | +1 |
| 2013 | (no county) | 0 | 2 | +2 |
| 2013 | Orange | 0 | 1 | +1 |
| 2013 | Placer | 0 | 2 | +2 |
| 2013 | Plumas | 0 | 1 | +1 |
| 2013 | Riverside | 0 | 18 | +18 |
| 2013 | Sacramento | 0 | 1 | +1 |
| 2013 | San Benito | 0 | 1 | +1 |
| 2013 | San Bernardino | 0 | 10 | +10 |
| 2013 | San Diego | 0 | 7 | +7 |
| 2013 | San Joaquin | 0 | 1 | +1 |
| 2013 | San Luis Obispo | 0 | 6 | +6 |
| 2013 | San Mateo | 0 | 1 | +1 |
| 2013 | Santa Barbara | 0 | 2 | +2 |
| 2013 | Santa Clara | 0 | 2 | +2 |
| 2013 | Shasta | 0 | 5 | +5 |
| 2013 | Siskiyou | 0 | 2 | +2 |
| 2013 | Solano | 0 | 2 | +2 |
| 2013 | Sonoma | 0 | 5 | +5 |
| 2013 | Stanislaus | 0 | 1 | +1 |
| 2013 | Tehama | 0 | 4 | +4 |
| 2013 | Tulare | 0 | 2 | +2 |
| 2013 | Tuolumne | 0 | 3 | +3 |
| 2013 | Ventura | 0 | 2 | +2 |
| 2013 | Yolo | 0 | 1 | +1 |
| 2013 | Yuba | 0 | 1 | +1 |
| 2014 | Butte | 0 | 1 | +1 |
| 2014 | Calaveras | 0 | 2 | +2 |
| 2014 | Contra Costa | 0 | 1 | +1 |
| 2014 | El Dorado | 1 | 5 | +4 |
| 2014 | Fresno | 0 | 3 | +3 |
| 2014 | Humboldt | 0 | 2 | +2 |
| 2014 | Inyo | 0 | 1 | +1 |
| 2014 | Kern | 0 | 3 | +3 |
| 2014 | Lake | 0 | 2 | +2 |
| 2014 | Los Angeles | 0 | 3 | +3 |
| 2014 | Madera | 0 | 3 | +3 |
| 2014 | Mariposa | 0 | 2 | +2 |
| 2014 | Modoc | 0 | 4 | +4 |
| 2014 | Monterey | 0 | 2 | +2 |
| 2014 | Napa | 0 | 2 | +2 |
| 2014 | Nevada | 0 | 3 | +3 |
| 2014 | Orange | 0 | 1 | +1 |
| 2014 | Riverside | 0 | 1 | +1 |
| 2014 | San Bernardino | 0 | 2 | +2 |
| 2014 | San Diego | 0 | 10 | +10 |
| 2014 | San Luis Obispo | 0 | 1 | +1 |
| 2014 | Santa Barbara | 0 | 1 | +1 |
| 2014 | Shasta | 0 | 9 | +9 |
| 2014 | Siskiyou | 0 | 4 | +4 |
| 2014 | Solano | 0 | 1 | +1 |
| 2014 | Tehama | 0 | 2 | +2 |
| 2014 | Trinity | 0 | 2 | +2 |
| 2014 | Tuolumne | 0 | 3 | +3 |
| 2014 | Yolo | 0 | 1 | +1 |
| 2015 | Alameda | 0 | 1 | +1 |
| 2015 | Alpine | 0 | 1 | +1 |
| 2015 | Amador | 0 | 1 | +1 |
| 2015 | Butte | 1 | 5 | +4 |
| 2015 | Calaveras | 0 | 1 | +1 |
| 2015 | Contra Costa | 0 | 1 | +1 |
| 2015 | El Dorado | 0 | 1 | +1 |
| 2015 | Fresno | 0 | 2 | +2 |
| 2015 | Humboldt | 0 | 4 | +4 |
| 2015 | Kern | 0 | 1 | +1 |
| 2015 | Lake | 0 | 5 | +5 |
| 2015 | Lassen | 0 | 4 | +4 |
| 2015 | Los Angeles | 0 | 2 | +2 |
| 2015 | Madera | 0 | 3 | +3 |
| 2015 | Mendocino | 0 | 3 | +3 |
| 2015 | Merced | 0 | 1 | +1 |
| 2015 | Modoc | 0 | 3 | +3 |
| 2015 | Monterey | 0 | 2 | +2 |
| 2015 | Napa | 1 | 4 | +3 |
| 2015 | Nevada | 0 | 2 | +2 |
| 2015 | (no county) | 0 | 2 | +2 |
| 2015 | Orange | 0 | 1 | +1 |
| 2015 | Placer | 0 | 1 | +1 |
| 2015 | Plumas | 0 | 1 | +1 |
| 2015 | Riverside | 0 | 3 | +3 |
| 2015 | San Benito | 0 | 2 | +2 |
| 2015 | San Bernardino | 0 | 5 | +5 |
| 2015 | San Diego | 0 | 6 | +6 |
| 2015 | San Luis Obispo | 0 | 2 | +2 |
| 2015 | Santa Barbara | 0 | 2 | +2 |
| 2015 | Shasta | 0 | 7 | +7 |
| 2015 | Siskiyou | 0 | 3 | +3 |
| 2015 | Solano | 0 | 2 | +2 |
| 2015 | Stanislaus | 0 | 1 | +1 |
| 2015 | Tehama | 0 | 3 | +3 |
| 2015 | Trinity | 0 | 3 | +3 |
| 2015 | Tulare | 0 | 3 | +3 |
| 2015 | Tuolumne | 0 | 3 | +3 |
| 2015 | Ventura | 0 | 1 | +1 |
| 2015 | Yuba | 0 | 2 | +2 |
| 2016 | Alameda | 0 | 1 | +1 |
| 2016 | Alpine | 0 | 1 | +1 |
| 2016 | Amador | 0 | 4 | +4 |
| 2016 | Butte | 0 | 9 | +9 |
| 2016 | Calaveras | 0 | 6 | +6 |
| 2016 | Contra Costa | 0 | 1 | +1 |
| 2016 | Del Norte | 0 | 1 | +1 |
| 2016 | El Dorado | 0 | 4 | +4 |
| 2016 | Fresno | 0 | 11 | +11 |
| 2016 | Humboldt | 0 | 3 | +3 |
| 2016 | Inyo | 0 | 2 | +2 |
| 2016 | Kern | 0 | 10 | +10 |
| 2016 | Lake | 0 | 6 | +6 |
| 2016 | Lassen | 0 | 1 | +1 |
| 2016 | Los Angeles | 0 | 4 | +4 |
| 2016 | Madera | 0 | 3 | +3 |
| 2016 | Mendocino | 0 | 2 | +2 |
| 2016 | Merced | 0 | 2 | +2 |
| 2016 | Modoc | 0 | 2 | +2 |
| 2016 | Mono | 0 | 1 | +1 |
| 2016 | Monterey | 0 | 7 | +7 |
| 2016 | Napa | 0 | 1 | +1 |
| 2016 | Nevada | 0 | 2 | +2 |
| 2016 | Orange | 0 | 2 | +2 |
| 2016 | Placer | 0 | 3 | +3 |
| 2016 | Plumas | 0 | 1 | +1 |
| 2016 | Riverside | 0 | 1 | +1 |
| 2016 | Sacramento | 0 | 3 | +3 |
| 2016 | San Benito | 0 | 3 | +3 |
| 2016 | San Bernardino | 0 | 4 | +4 |
| 2016 | San Diego | 0 | 7 | +7 |
| 2016 | San Joaquin | 0 | 1 | +1 |
| 2016 | San Luis Obispo | 0 | 4 | +4 |
| 2016 | Santa Barbara | 0 | 3 | +3 |
| 2016 | Santa Clara | 0 | 4 | +4 |
| 2016 | Shasta | 0 | 5 | +5 |
| 2016 | Siskiyou | 0 | 6 | +6 |
| 2016 | Solano | 0 | 1 | +1 |
| 2016 | Sonoma | 0 | 1 | +1 |
| 2016 | Stanislaus | 0 | 1 | +1 |
| 2016 | Sutter | 0 | 1 | +1 |
| 2016 | Tehama | 0 | 6 | +6 |
| 2016 | Tulare | 0 | 4 | +4 |
| 2016 | Tuolumne | 0 | 3 | +3 |
| 2016 | Ventura | 0 | 4 | +4 |
| 2016 | Yolo | 0 | 3 | +3 |
| 2016 | Yuba | 0 | 1 | +1 |
| 2017 | Alameda | 0 | 5 | +5 |
| 2017 | Amador | 0 | 1 | +1 |
| 2017 | Butte | 0 | 18 | +18 |
| 2017 | Calaveras | 0 | 5 | +5 |
| 2017 | Colusa | 0 | 1 | +1 |
| 2017 | Contra Costa | 0 | 3 | +3 |
| 2017 | Del Norte | 0 | 3 | +3 |
| 2017 | El Dorado | 1 | 9 | +8 |
| 2017 | Fresno | 0 | 24 | +24 |
| 2017 | Glenn | 0 | 1 | +1 |
| 2017 | Humboldt | 0 | 6 | +6 |
| 2017 | Kern | 0 | 24 | +24 |
| 2017 | Kings | 0 | 3 | +3 |
| 2017 | Lake | 0 | 8 | +8 |
| 2017 | Lassen | 0 | 8 | +8 |
| 2017 | Los Angeles | 0 | 13 | +13 |
| 2017 | Madera | 0 | 9 | +9 |
| 2017 | Marin | 0 | 2 | +2 |
| 2017 | Mariposa | 0 | 12 | +12 |
| 2017 | Mendocino | 1 | 4 | +3 |
| 2017 | Merced | 0 | 1 | +1 |
| 2017 | Modoc | 0 | 9 | +9 |
| 2017 | Mono | 0 | 2 | +2 |
| 2017 | Monterey | 0 | 14 | +14 |
| 2017 | Napa | 3 | 6 | +3 |
| 2017 | Nevada | 1 | 7 | +6 |
| 2017 | (no county) | 0 | 2 | +2 |
| 2017 | Orange | 0 | 2 | +2 |
| 2017 | Plumas | 0 | 5 | +5 |
| 2017 | Riverside | 1 | 52 | +51 |
| 2017 | Sacramento | 0 | 3 | +3 |
| 2017 | San Benito | 0 | 3 | +3 |
| 2017 | San Bernardino | 0 | 17 | +17 |
| 2017 | San Diego | 0 | 19 | +19 |
| 2017 | San Luis Obispo | 0 | 25 | +25 |
| 2017 | San Mateo | 0 | 1 | +1 |
| 2017 | Santa Barbara | 3 | 7 | +4 |
| 2017 | Santa Clara | 0 | 8 | +8 |
| 2017 | Santa Cruz | 0 | 1 | +1 |
| 2017 | Shasta | 0 | 10 | +10 |
| 2017 | Sierra | 0 | 2 | +2 |
| 2017 | Siskiyou | 0 | 16 | +16 |
| 2017 | Solano | 1 | 4 | +3 |
| 2017 | Sonoma | 2 | 9 | +7 |
| 2017 | Stanislaus | 0 | 6 | +6 |
| 2017 | Tehama | 0 | 11 | +11 |
| 2017 | Trinity | 0 | 6 | +6 |
| 2017 | Tulare | 0 | 12 | +12 |
| 2017 | Tuolumne | 0 | 5 | +5 |
| 2017 | Ventura | 1 | 3 | +2 |
| 2017 | Yolo | 0 | 1 | +1 |
| 2017 | Yuba | 1 | 6 | +5 |
| 2018 | Alameda | 1 | 8 | +7 |
| 2018 | Amador | 1 | 2 | +1 |
| 2018 | Butte | 1 | 12 | +11 |
| 2018 | Calaveras | 1 | 3 | +2 |
| 2018 | Colusa | 2 | 3 | +1 |
| 2018 | Contra Costa | 1 | 9 | +8 |
| 2018 | El Dorado | 2 | 5 | +3 |
| 2018 | Fresno | 0 | 5 | +5 |
| 2018 | Glenn | 1 | 4 | +3 |
| 2018 | Humboldt | 0 | 4 | +4 |
| 2018 | Inyo | 0 | 4 | +4 |
| 2018 | Kern | 0 | 8 | +8 |
| 2018 | Kings | 0 | 1 | +1 |
| 2018 | Lake | 5 | 10 | +5 |
| 2018 | Lassen | 1 | 10 | +9 |
| 2018 | Los Angeles | 1 | 8 | +7 |
| 2018 | Madera | 0 | 9 | +9 |
| 2018 | Marin | 0 | 2 | +2 |
| 2018 | Mariposa | 1 | 5 | +4 |
| 2018 | Mendocino | 2 | 7 | +5 |
| 2018 | Merced | 0 | 6 | +6 |
| 2018 | Modoc | 0 | 4 | +4 |
| 2018 | Mono | 0 | 1 | +1 |
| 2018 | Monterey | 0 | 9 | +9 |
| 2018 | Napa | 2 | 5 | +3 |
| 2018 | Nevada | 0 | 1 | +1 |
| 2018 | (no county) | 0 | 3 | +3 |
| 2018 | Orange | 1 | 3 | +2 |
| 2018 | Placer | 0 | 2 | +2 |
| 2018 | Plumas | 0 | 2 | +2 |
| 2018 | Riverside | 0 | 24 | +24 |
| 2018 | Sacramento | 0 | 1 | +1 |
| 2018 | San Benito | 0 | 8 | +8 |
| 2018 | San Bernardino | 0 | 7 | +7 |
| 2018 | San Diego | 1 | 17 | +16 |
| 2018 | San Joaquin | 1 | 2 | +1 |
| 2018 | San Luis Obispo | 1 | 14 | +13 |
| 2018 | Santa Barbara | 1 | 7 | +6 |
| 2018 | Santa Clara | 0 | 9 | +9 |
| 2018 | Santa Cruz | 0 | 2 | +2 |
| 2018 | Shasta | 2 | 16 | +14 |
| 2018 | Siskiyou | 1 | 10 | +9 |
| 2018 | Solano | 0 | 3 | +3 |
| 2018 | Sonoma | 0 | 1 | +1 |
| 2018 | Stanislaus | 0 | 1 | +1 |
| 2018 | Sutter | 0 | 1 | +1 |
| 2018 | Tehama | 1 | 14 | +13 |
| 2018 | Trinity | 2 | 4 | +2 |
| 2018 | Tulare | 0 | 5 | +5 |
| 2018 | Tuolumne | 0 | 3 | +3 |
| 2018 | Ventura | 3 | 4 | +1 |
| 2018 | Yolo | 1 | 3 | +2 |
| 2019 | Alameda | 8 | 9 | +1 |
| 2019 | Butte | 5 | 9 | +4 |
| 2019 | Contra Costa | 8 | 9 | +1 |
| 2019 | Fresno | 2 | 3 | +1 |
| 2019 | Humboldt | 0 | 1 | +1 |
| 2019 | Kern | 3 | 6 | +3 |
| 2019 | Kings | 0 | 1 | +1 |
| 2019 | Monterey | 5 | 7 | +2 |
| 2019 | Napa | 2 | 3 | +1 |
| 2019 | Nevada | 0 | 1 | +1 |
| 2019 | Placer | 2 | 5 | +3 |
| 2019 | Riverside | 25 | 32 | +7 |
| 2019 | San Diego | 13 | 17 | +4 |
| 2019 | San Luis Obispo | 6 | 10 | +4 |
| 2019 | Santa Clara | 7 | 11 | +4 |
| 2019 | Shasta | 8 | 9 | +1 |
| 2019 | Siskiyou | 7 | 9 | +2 |
| 2019 | Solano | 4 | 5 | +1 |
| 2019 | Stanislaus | 5 | 7 | +2 |
| 2019 | Sutter | 0 | 1 | +1 |
| 2019 | Tehama | 7 | 9 | +2 |
| 2019 | Tulare | 2 | 7 | +5 |
| 2019 | Yolo | 2 | 3 | +1 |
| 2019 | Yuba | 1 | 4 | +3 |
| 2020 | San Diego | 6 | 7 | +1 |
| 2020 | Ventura | 3 | 4 | +1 |
| 2021 | El Dorado | 4 | 5 | +1 |
| 2021 | Inyo | 1 | 2 | +1 |
| 2021 | Kern | 8 | 9 | +1 |
| 2021 | Los Angeles | 9 | 10 | +1 |
| 2021 | Madera | 4 | 5 | +1 |
| 2021 | Mendocino | 4 | 5 | +1 |
| 2021 | Riverside | 15 | 18 | +3 |
| 2021 | San Benito | 0 | 1 | +1 |
| 2021 | San Diego | 10 | 11 | +1 |
| 2021 | Santa Cruz | 4 | 5 | +1 |
| 2021 | Shasta | 11 | 12 | +1 |
| 2021 | Stanislaus | 0 | 1 | +1 |
| no date | Marin | 0 | 1 | +1 |
| no date | Merced | 0 | 1 | +1 |

## How this was measured

```sql
-- before / after per created year; swap the GROUP BY for utility, county, or region
SELECT EXTRACT(YEAR FROM c.date_only_created)::int,
       count(*) FILTER (WHERE c.incident_type IN ('Wildfire', 'Fire')) AS before,
       count(*) FILTER (WHERE c.incident_type IS NULL
                           OR c.incident_type NOT IN ('Earthquake', 'Flood', 'Hazmat')) AS after
FROM wildfire.calfire_incidents c
GROUP BY 1 ORDER BY 1;
```

County rows split the county list the same way the services do
(`services/shared/calfire_county.py`). `tests/test_calfire_default.py`
asserts every statewide year total above through the data query service, and
spot values through each other path: PG&E all years (2,254), Butte 2018 (12),
Humboldt 2024 (8), the PG&E territory in 2017 (257), rankings, grouped counts,
summaries, the map and time series, and comparisons.
