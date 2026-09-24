# City questions answered at a city center point

Before this change, any question naming a California city that is not also a
county name got the `city_needs_place` clarification ("which coordinates,
county, or utility territory should I use?"). Some of those questions can be
answered from one point, so the router now resolves the city to its Census
Gazetteer internal point and calls the existing spatial or risk tool.

Data: `data/places/ca_places_gazetteer_2025.csv`, described in
`data/places/README.md`. Resolver: `services/agent/places.py`.

## What is answered

| Question shape | Route | Tool calls |
| --- | --- | --- |
| Which IOU or utility territory contains a city | `city_point_context` | `data_query_spatial` point |
| Which HFTD tier a city is in, is a city in Tier 2 or 3 | `city_point_context` | `data_query_spatial` point |
| Which grid cell a city is in | `city_point_context` | `data_query_spatial` point |
| Fitted risk in, at, near, or around a city on one past day | `city_point_risk_chain` | `data_query_spatial` point, then `risk_forecast` on its cell |

A city risk question with no day, or with a day after 2025-12-31, gets the
existing risk date clarification (asks for a past date, not for coordinates).
"Near" and "around" are accepted for risk only, because the fitted risk is a
0.24 degree cell aggregate anyway.

Every answer on these routes carries two caveats, tied to the point read's
arguments (`caveats.py`):

- `city_center_point`: the answer uses one point, the Census 2025 Gazetteer
  internal point of the incorporated city. Parts of the city may be in a
  different territory, tier, or cell, and a center outside Tier 2 or 3 does
  not mean the whole city is outside the HFTD.
- `iou_territory_not_provider`: the utility layer is IOU service territory
  polygons only, and it covers some cities that run their own municipal
  utility.

## What still clarifies

- Counts, lists, maps, rankings, trends, or comparisons "in" a city. No
  dataset has a city field, and there is no city polygon.
- A radius ("within 10 miles of Chico"). No tool takes a radius.
- Part of a city ("is part of Chico in Tier 3", "what share of", "any
  portion"), or somewhere inside it (an address, a residence, downtown, the
  north side).
- Who supplies power ("which utility serves Redding"). See the finding below.
- Two cities, or a city plus a county. With explicit coordinates, the
  coordinates are the place and the city name is only a label.
- Names that are not in the 458-name municipality list, and CDPs in
  general, except the county-word places below.
- Common-word city names (Industry, Commerce, Weed, Needles, Paradise,
  Coronado) still need a place cue such as "the city of Weed" or
  "Needles, California". Without one they are not treated as cities.
- County names still route as counties, and a county name inside a city
  name (West Sacramento, South San Francisco, Mount Shasta) is not taken as
  that county.

## County-word places

A Census place whose name holds a county word is that place, not the county.
Main's `county_place_ambiguous` rule asks "Did you mean Lake County?" for
"Lake Forest"; recognized place names now win over it:

- **Incorporated places** already in the municipality list: Lake Forest,
  Shasta Lake, South Lake Tahoe, Sutter Creek, Monterey Park, Imperial
  Beach, West Sacramento. These route as cities.
- **Census designated places** that the county-word rule would otherwise
  misread (32 in the 2025 Gazetteer, computed from the rule itself).
  Examples: Kings Beach, Plumas Lake, Lake Arrowhead, Lake Isabella, Lake Los
  Angeles, Trinity Center, Mono City, Orange Park Acres. They route as places
  and resolve to their CDP internal point. Their caveat and clarification
  call them a "census designated place" or "community", not a city.
- **A bare county word** ("in Trinity", "in Kings") or a non-place phrase
  ("Napa Valley", "Kern River") still gets `county_place_ambiguous`.

A count in one of these places still clarifies (`city_needs_place`), for
example "How many CAL FIRE incidents were there in Shasta Lake in 2020?".
CDPs outside this set stay unresolved, because many share a name with an
incorporated place elsewhere (Paradise, Burbank, Mountain View).

## Shoreline points

Some Census city centers sit just off a mapped coastline. For example,
Albany's is 3.5 m outside PG&E's polygon on the Bay shore. City routes
therefore call `/spatial/point` with `snap_shoreline=true`. That flag is
hidden from the model's tool schema and is off for explicit coordinates.
Harness-only arguments (fields left out of the model-facing schema,
currently just `snap_shoreline`) are stripped from every call that does not
come from the router. So a model, or a qualification read, cannot turn the
snap on for its own point read, and the stripping is logged as
`harness_arguments_stripped`.

With the flag on, a point that no polygon contains is snapped to the one
nearby polygon, under these limits:

| Layer | Limit | Why |
|---|---|---|
| IOU territory | 50 m | Every shoreline miss among the 483 city points is at most 41.8 m: Albany 3.5, South San Francisco 8.6, Avalon 31.2, Morro Bay 41.8. The nearest city truly outside every IOU is Los Angeles at 265.8 m (LADWP), then Vernon at 572.1 m. So 50 m covers the shoreline cases with a 5x margin below the first real gap. |
| County | 150 m | The county layer is Census 1:500k cartographic boundaries, generalized at the coast. Its shoreline misses reach 142 m (Coronado; also Santa Monica 75, Avalon 107, Monterey 30, Manhattan Beach 8, South San Francisco 7). |
| HFTD tier | never | A tier edge is a regulatory boundary, not a coastline. Paradise's center is 47.6 m outside Tier 3 and must stay "no tier". |
| Grid cell | never | A missing cell means the fitted model has no cell there. The nearest cell is 2.6 km from Santa Monica, 6.6 km from Manhattan Beach, 1.6 km from Los Angeles, and 0.6 km from Coronado. |

Guards, so a point is never put in a territory it is clearly outside of:
- **Never from a hole.** A point inside an IOU's outer boundary but in one
  of its holes is never snapped. That covers municipal utilities inside
  SCE, such as Anaheim, Colton, Azusa, Riverside and Banning, however close
  the hole's edge is.
- **Exactly one candidate.** A snap needs exactly one polygon of that layer
  within the limit. A point in water between two territories or counties is
  left alone.
- **No IOU is said plainly.** When no IOU territory contains the point, even
  after the snap, the answer says "No investor-owned utility (IOU) territory
  contains the center point of Anaheim." instead of reporting `IOU=None`.
- **Disclosed.** The response metadata records each snap and its distance,
  and the answer carries a `city_shoreline_snap` caveat that names it.

A missing grid cell only blocks an answer that needs one: a risk question,
or a question about the grid cell. Santa Monica, Manhattan Beach, Los
Angeles, and Coronado are outside the fitted grid, so their territory and tier questions
are answered and their risk questions ask for a point inside coverage.

## Findings from the local warehouse (after PR #45, 2026-09-23)

Point reads at city centers on the rebuilt HFTD and IOU geometry:

- The IOU polygons still cover some municipal-utility cities. Redding,
  Roseville, Palo Alto, Lodi, Healdsburg, Ukiah, and Lompoc return PGE, so
  "which utility serves X" is still not answered from this layer. Anaheim,
  Colton, Azusa, Riverside, and Banning are now correctly outside SCE (they
  are holes), and Burbank, Pasadena, Glendale, Los Angeles, and Vernon are
  outside every IOU.
- 30 city answers changed with PR #45. `docs/DATA_CHANGE_HFTD_IOU.md` lists
  them, and `tests/test_city_point_answers.py` pins the new values.
- Paradise's center is still outside Tier 3, now by 47.6 m. The town is a
  hole in Tier 3. This remains the clearest case for the boundary work
  below.
- Coronado's center (32.6567, -117.1564, in San Diego Bay) now snaps to San
  Diego County (142 m) but has no grid cell. Its territory and tier are
  answered, and a risk question clarifies.

## Not built: "is part of Chico in Tier 3"

Answering questions about part of a city needs city boundary polygons.
What that would take:

1. **Boundaries.** Census TIGER/Line PLACE polygons for California,
   `tl_2025_06_place.zip` from
   https://www2.census.gov/geo/tiger/TIGER2025/PLACE/, same vintage as the
   Gazetteer and joined on `geoid`. Prefer TIGER/Line over the 1:500k
   cartographic boundary file: the generalized file moves edges by tens of
   meters, which is the same scale as the Paradise miss above. Public domain.
2. **Loader and table.** `db/loaders/load_places.py` modeled on
   `load_counties.py` (download once, cache under `data/boundaries/`, keep
   the zip gitignored). Table `wildfire.places (geoid TEXT PRIMARY KEY, name,
   place_type, geom geometry(MultiPolygon, 4326))` with a GIST index.
   Incorporated places at minimum; CDPs optional.
3. **Valid HFTD geometry.** Store `ST_MakeValid(geom)` for `hftd_tiers` (and
   check `iou_territories`) at load. Overlays on invalid polygons can return
   wrong intersections without raising an error.
4. **Query.** A `/spatial/place?geoid=` endpoint in `data_query` that returns,
   for one place: each HFTD tier it intersects with the intersected area and
   share of city area, the same for IOU territories, grid cells, and
   counties. Compute area in California Albers (EPSG:3310), not in degrees.
   Apply a sliver tolerance (for example ignore overlaps under 0.1% of city
   area or under 1 hectare, or test with a small negative buffer), since
   city limits and HFTD polygons were digitized separately and share edges
   that do not line up.
5. **Agent.** Add `kind="place"` with `geoid` to `DataQuerySpatialArgs`, a
   router rule (for example `city_place_overlap`) for "part of", "any
   portion", and "what share of" a city, and a renderer line such as
   "Chico: 0.0% Tier 3, 2.1% Tier 2 by area". The same polygon would also
   allow counts "in" a city for point datasets (`ST_Covers(place.geom,
   event.geom)`).
6. **Caveats.** City limits change by annexation. A 2025 boundary applied to
   2019 events is the 2025 city, not the 2019 city. The HFTD layer vintage
   should be stated with the answer.
7. **Tests and checks.** Paradise must intersect Tier 3. Redding must
   intersect more than one tier. Report the sliver tolerance's effect on a
   sample of cities before relying on share answers. Every rendered share
   must trace to the endpoint's evidence.

Open question before building: whether "part of the city" answers should
report any overlap or only overlaps above the sliver tolerance. That choice
decides the answer for cities that only touch a tier along an edge.
