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
- Two cities, a city plus a county, or a city plus explicit coordinates.
- Names that are not in the 458-name municipality list (for example CDPs).
- Common-word city names (Industry, Commerce, Weed, Needles, Paradise,
  Coronado) still need a place cue such as "the city of Weed" or
  "Needles, California". Without one they are not treated as cities.
- County names still route as counties, and a county name inside a city
  name (West Sacramento, South San Francisco, Mount Shasta) is not taken as
  that county.

## Findings from the local warehouse (2026-09-23)

Point reads at a few city internal points:

- The IOU polygons cover municipal-utility cities: Redding, Roseville,
  Palo Alto, Lodi, Healdsburg, Ukiah, and Lompoc return PGE, and Anaheim
  and Colton return SCE. Burbank, Pasadena, and Glendale return no IOU.
  So "which utility serves X" is not answered from this layer.
- Paradise's internal point is outside Tier 3 by about 67 m and returns no
  tier, although the area around it is Tier 3. This is the clearest case for
  the boundary work below.
- Coronado's internal point returns no county and no grid cell. A Coronado
  risk question fails loudly at the risk call rather than guessing.
- Both `wildfire.hftd_tiers` rows fail `ST_IsValid`. Point containment gave
  the same Paradise answer after `ST_MakeValid`, but polygon overlays need
  valid geometry.

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
