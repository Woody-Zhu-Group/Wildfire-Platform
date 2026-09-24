# Data change: multi-county CAL FIRE incidents and EPSS cause codes

Status: code change in PR #81 (issues #77 and #78). No warehouse rows change;
only how the services match, group, and display existing values. Takes effect
on EC2 when the backend is redeployed from main.

## 1. Multi-county CAL FIRE incidents

### What was wrong

`wildfire.calfire_incidents.county` is the incident-map feed's county text.
A fire that crossed county lines lists every county it burned in, comma
separated. 63 rows do this, across 48 distinct values ("Shasta, Tehama",
"Napa, Sonoma", "Mendocino, Humboldt, Trinity, Tehama, Glenn, Lake, Colusa").
Every county filter was an exact match (`lower(c.county) = lower(%s)`), so
these incidents counted in no county at all. Shasta 2020 answered 4 while the
Zogg and Point fires, both "Shasta, Tehama", were left out. The rankings and
grouped counts also showed "Shasta, Tehama" as its own "county".

### What changed

A county filter now matches the split list (`services/shared/calfire_county.py`),
so an incident counts in every county it lists. This applies everywhere a
CAL FIRE county is used:

- Data Query: `/calfire/incidents`, `/rank` (by county, count and acres),
  `/grouped-counts` (by county, and with a county filter), `/summary`.
- Visualization: `/map-layer` and `/time-series` for `calfire`.
- Comparison: `/compare-regions` and `/compare-periods` county scopes for
  `calfire_incident_count` and `acres_burned`.
- Website: the Comparison panel accepts CAL FIRE county rows that sum above
  the incident total and shows the note; browser aggregation splits the same
  way.

A county ranking or grouping gives each listed county the incident's full
count and full acreage, because the feed does not say how the fire split.
County totals can therefore add up to more than the statewide total. In 2020,
for example, the CAL FIRE county rows sum to 281, while the state had 257
incidents (13 of them multi-county). Statewide counts are unchanged, since
each incident counts once there. CPUC ignitions and EPSS outages store one
county per row and are unchanged.

### Why

A CPUC analyst who asks for Shasta County's 2020 fires expects the Zogg Fire
to be among them. Leaving it out undercounted every county a large fire
touched, and it did so silently. Counting the incident in each listed county
answers the county question correctly; the cost is that county totals no
longer add up to the state total, which the caveat says.

### New metadata and caveat

Every county-scoped CAL FIRE response carries `multi_county_incidents` (how
many of the counted incidents list more than one county) and
`multi_county_rule: counted_in_each_listed_county`. Grouped counts and
comparisons also carry the note. When the number is above 0, the agent adds
the `calfire_multi_county` caveat, for example:

> 2 of the counted CAL FIRE incidents list more than one county (for example
> "Shasta, Tehama"). Each is counted, with its full acreage, in every county
> it lists, so county totals can add up to more than the statewide total.

### Counts that change

"Default" is the services' default filter (`incident_type` Wildfire or Fire);
"All types" is `incident_type=all`. 116 county-years change: 100 under the
default and all 116 with `incident_type=all` (16 change only there, because
their multi-county rows have no incident type). "Mexico" comes from the 2019
"Border 10 Fire" ("Mexico, San Diego"). It appears as a ranking group but is
not a California county, so no county filter can request it.

No eval gold label stores a count that changes. The only numeric CAL FIRE
county label is Sacramento 2024 = 11, and Sacramento's only multi-county row
is from 2013.

| County | Year | Default before | Default after | All types before | All types after |
|---|---|---|---|---|---|
| Alameda | 2020 | 2 | 3 | 2 | 3 |
| Alameda | 2024 | 13 | 14 | 13 | 15 |
| Alpine | 2021 | 2 | 3 | 2 | 3 |
| Amador | 2014 | 0 | 1 | 0 | 1 |
| Amador | 2021 | 2 | 3 | 2 | 3 |
| Amador | 2022 | 0 | 1 | 0 | 1 |
| Butte | 2021 | 3 | 4 | 3 | 4 |
| Butte | 2024 | 15 | 16 | 15 | 16 |
| Calaveras | 2022 | 1 | 2 | 1 | 2 |
| Calaveras | 2025 | 1 | 2 | 1 | 2 |
| Colusa | 2013 | 0 | 0 | 0 | 1 |
| Colusa | 2018 | 0 | 2 | 1 | 3 |
| Colusa | 2020 | 3 | 4 | 3 | 4 |
| Colusa | 2024 | 2 | 3 | 2 | 3 |
| Contra Costa | 2020 | 6 | 7 | 6 | 7 |
| El Dorado | 2013 | 0 | 0 | 3 | 4 |
| El Dorado | 2014 | 0 | 1 | 4 | 5 |
| El Dorado | 2016 | 0 | 0 | 3 | 4 |
| El Dorado | 2021 | 3 | 4 | 4 | 5 |
| El Dorado | 2022 | 1 | 2 | 1 | 2 |
| Fresno | 2020 | 7 | 8 | 7 | 8 |
| Fresno | 2025 | 58 | 59 | 58 | 59 |
| Glenn | 2018 | 0 | 1 | 3 | 4 |
| Glenn | 2020 | 3 | 6 | 3 | 6 |
| Humboldt | 2020 | 4 | 5 | 4 | 5 |
| Humboldt | 2022 | 2 | 3 | 2 | 3 |
| Humboldt | 2024 | 7 | 8 | 7 | 9 |
| Kern | 2013 | 0 | 0 | 3 | 4 |
| Kern | 2024 | 60 | 63 | 60 | 63 |
| Kern | 2026 | 44 | 45 | 44 | 45 |
| Lake | 2013 | 0 | 0 | 8 | 9 |
| Lake | 2014 | 0 | 0 | 1 | 2 |
| Lake | 2015 | 0 | 0 | 4 | 5 |
| Lake | 2018 | 3 | 5 | 8 | 10 |
| Lake | 2020 | 1 | 3 | 1 | 3 |
| Lake | 2024 | 7 | 8 | 7 | 8 |
| Lassen | 2021 | 3 | 4 | 3 | 4 |
| Los Angeles | 2018 | 0 | 1 | 7 | 8 |
| Los Angeles | 2024 | 33 | 35 | 33 | 35 |
| Los Angeles | 2025 | 16 | 18 | 17 | 19 |
| Los Angeles | 2026 | 19 | 20 | 19 | 20 |
| Madera | 2020 | 6 | 7 | 6 | 7 |
| Mariposa | 2024 | 7 | 8 | 7 | 8 |
| Mendocino | 2018 | 0 | 2 | 5 | 7 |
| Mendocino | 2020 | 7 | 9 | 7 | 9 |
| Mexico | 2019 | 1 | 2 | 1 | 2 |
| Modoc | 2020 | 2 | 3 | 2 | 3 |
| Monterey | 2017 | 0 | 0 | 13 | 14 |
| Monterey | 2019 | 4 | 5 | 6 | 7 |
| Monterey | 2023 | 2 | 3 | 2 | 3 |
| Monterey | 2025 | 8 | 9 | 8 | 9 |
| Napa | 2014 | 0 | 0 | 1 | 2 |
| Napa | 2015 | 1 | 1 | 3 | 4 |
| Napa | 2017 | 0 | 3 | 3 | 6 |
| Napa | 2018 | 1 | 2 | 4 | 5 |
| Napa | 2020 | 2 | 4 | 2 | 4 |
| Napa | 2021 | 1 | 2 | 1 | 2 |
| Nevada | 2021 | 1 | 2 | 1 | 2 |
| Orange | 2024 | 0 | 1 | 0 | 1 |
| Placer | 2016 | 0 | 0 | 2 | 3 |
| Placer | 2021 | 2 | 3 | 2 | 3 |
| Placer | 2022 | 6 | 7 | 6 | 7 |
| Plumas | 2021 | 3 | 4 | 3 | 4 |
| Riverside | 2020 | 24 | 26 | 24 | 26 |
| Riverside | 2021 | 14 | 15 | 17 | 18 |
| Riverside | 2024 | 72 | 74 | 72 | 74 |
| Riverside | 2026 | 42 | 43 | 42 | 43 |
| Sacramento | 2013 | 0 | 0 | 0 | 1 |
| San Bernardino | 2020 | 6 | 8 | 6 | 8 |
| San Bernardino | 2024 | 30 | 32 | 30 | 32 |
| San Bernardino | 2026 | 18 | 19 | 18 | 19 |
| San Diego | 2019 | 12 | 13 | 16 | 17 |
| San Diego | 2021 | 9 | 10 | 10 | 11 |
| San Joaquin | 2020 | 2 | 3 | 2 | 3 |
| San Joaquin | 2024 | 8 | 9 | 8 | 9 |
| San Joaquin | 2025 | 4 | 5 | 4 | 5 |
| San Luis Obispo | 2017 | 0 | 0 | 24 | 25 |
| San Luis Obispo | 2019 | 5 | 6 | 9 | 10 |
| San Luis Obispo | 2023 | 6 | 7 | 6 | 7 |
| San Luis Obispo | 2024 | 18 | 20 | 18 | 20 |
| San Mateo | 2020 | 0 | 1 | 0 | 1 |
| Santa Barbara | 2017 | 2 | 3 | 6 | 7 |
| Santa Clara | 2020 | 6 | 7 | 6 | 7 |
| Santa Cruz | 2020 | 0 | 1 | 0 | 1 |
| Shasta | 2018 | 0 | 2 | 14 | 16 |
| Shasta | 2020 | 4 | 6 | 4 | 6 |
| Shasta | 2021 | 9 | 11 | 10 | 12 |
| Siskiyou | 2020 | 4 | 5 | 4 | 5 |
| Siskiyou | 2021 | 8 | 9 | 8 | 9 |
| Solano | 2013 | 0 | 0 | 1 | 2 |
| Solano | 2017 | 0 | 1 | 3 | 4 |
| Solano | 2020 | 4 | 5 | 4 | 5 |
| Sonoma | 2013 | 0 | 0 | 4 | 5 |
| Sonoma | 2017 | 0 | 2 | 7 | 9 |
| Sonoma | 2020 | 2 | 4 | 2 | 4 |
| Sonoma | 2021 | 1 | 2 | 1 | 2 |
| Stanislaus | 2020 | 3 | 4 | 3 | 4 |
| Stanislaus | 2025 | 6 | 7 | 6 | 7 |
| Tehama | 2020 | 4 | 8 | 4 | 8 |
| Tehama | 2021 | 4 | 6 | 4 | 6 |
| Tehama | 2024 | 14 | 15 | 14 | 15 |
| Trinity | 2018 | 0 | 2 | 2 | 4 |
| Trinity | 2020 | 3 | 4 | 3 | 4 |
| Trinity | 2021 | 4 | 6 | 4 | 6 |
| Trinity | 2022 | 0 | 1 | 0 | 1 |
| Trinity | 2024 | 2 | 3 | 2 | 3 |
| Tulare | 2024 | 14 | 15 | 14 | 15 |
| Tuolumne | 2024 | 9 | 10 | 9 | 10 |
| Tuolumne | 2025 | 11 | 12 | 11 | 12 |
| Ventura | 2013 | 0 | 0 | 1 | 2 |
| Ventura | 2017 | 0 | 1 | 2 | 3 |
| Ventura | 2018 | 2 | 3 | 3 | 4 |
| Ventura | 2024 | 5 | 6 | 5 | 6 |
| Ventura | 2025 | 7 | 9 | 7 | 9 |
| Yolo | 2018 | 0 | 1 | 2 | 3 |
| Yolo | 2020 | 0 | 1 | 0 | 1 |

## 2. EPSS cause codes

### What was wrong

`wildfire.epss_outages.cause` mixes short codes and words for the same cause.
The codes occur only in the 9 rows from 2021; from 2022 the source writes
words. A filter or grouping treated `VEG` and `Vegetation` as two causes, so
a Vegetation count missed the 2021 row and a cause breakdown listed both.

### Every code and word pair in the column

| Stored value | Rows | Years | Pair |
|---|---|---|---|
| `VEG` | 1 | 2021 | Vegetation |
| Vegetation | 1,042 | 2022 to 2025 | |
| `UNK` | 6 | 2021 | Unknown |
| Unknown | 3,724 | 2022 to 2025 | |
| `3RD` | 1 | 2021 | 3rd Party |
| 3rd Party | 914 | 2022 to 2025 | |
| `EF` | 1 | 2021 | ambiguous: Equipment or Equipment Failure/Involved |
| Equipment | 290 | 2022 | |
| Equipment Failure/Involved | 986 | 2023 to 2025 | |
| Animal | 1,347 | 2022 to 2025 | no code form |
| Company Initiated | 1,046 | 2022 to 2025 | no code form |
| Environmental/External | 293 | 2022 to 2025 | no code form |

### The rule (Michael, 2026-09-24)

A cause code and its word form are the same cause. Filters and groupings
match both, and results display the word form. Recorded as
`EPSS_CAUSE_CODE_WORDS` in `services/shared/dataset_registry.py` (see
`services/shared/README.md`) and applied by `services/shared/epss_causes.py`
and `parse_cause`.

Applied to the three unambiguous pairs: VEG to Vegetation, UNK to Unknown,
3RD to 3rd Party.

Left alone: `EF`. It reads as "Equipment Failure", but two word forms could
claim it, "Equipment" (2022 only) and "Equipment Failure/Involved" (2023 on).
Those two are words, not a code and a word, so this rule does not merge them
either. They look like a wording change between years, but merging them
needs its own written rule.

### What changed

- `cause=veg`, `cause=VEG`, and `cause=Vegetation` all resolve to Vegetation
  and match both stored spellings, on Data Query `/epss/outages` and the
  Visualization `/map-layer`.
- Record lists, embedded map outages, event detail, and `/grouped-counts` by
  cause show the word form. `EF` still shows as `EF`.
- An unknown cause returns a 400 whose suggestions are word forms
  ("Did you mean Vegetation?").

### Counts that change

| Cause | Before (word only) | After | 2021 before | 2021 after |
|---|---|---|---|---|
| Vegetation | 1,042 | 1,043 | 0 | 1 |
| Unknown | 3,724 | 3,730 | 0 | 6 |
| 3rd Party | 914 | 915 | 0 | 1 |

Every other cause and every year from 2022 on is unchanged. The EPSS total
(9,651 loaded rows) is unchanged. No eval question asks for an EPSS cause count.
