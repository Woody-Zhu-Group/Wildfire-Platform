# California places (Census Gazetteer)

`ca_places_gazetteer_2025.csv` holds every California place in the US Census
Bureau Gazetteer places file, one row per place:

| column | meaning |
| --- | --- |
| `geoid` | Census place GEOID (state 06 plus 5-digit place code). Join key to TIGER/Line PLACE polygons. |
| `name` | Place name without the Census type suffix ("Chico", not "Chico city"). |
| `place_type` | `city` (LSAD 25), `town` (LSAD 43), or `CDP` (LSAD 57, census designated place). |
| `lat`, `lon` | Census internal point (`INTPTLAT`, `INTPTLONG`), WGS84 decimal degrees. |

- Source: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_gaz_place_06.txt
- Vintage: 2025 Gazetteer (downloaded 2026-09-23). US Census Bureau data, public domain.
- Rows: 1,619 (462 cities, 21 towns, 1,136 CDPs). 483 incorporated places.
- Rebuild: `python data/places/build_ca_places.py path/to/2025_gaz_place_06.txt`

The internal point is a point Census places inside the place, usually near its
center. It is not a city hall, a downtown, or a population center, and it
says nothing about the city boundary.

## How the agent uses it

`services/agent/places.py` loads the incorporated rows (city and town) only.
CDPs are skipped so a name such as Paradise or Mountain View resolves to the
incorporated place, never a same-named CDP elsewhere in the state. See
`docs/CITY_POINTS.md` for the routes that use it.

## Reconciliation with the municipality list

`_CA_CITIES` in `services/agent/routing.py` (458 names, behind the
`city_needs_place` rule) compared with the 483 incorporated Gazetteer places,
after lowercasing and folding accents (La Cañada Flintridge):

In the list, not in the Gazetteer under that name (1):

- `angels camp`: Census names the place `Angels city`. The resolver maps
  "Angels Camp" to it.

In the Gazetteer, not in the list (28):

- 25 cities that share a county name and route as counties by design:
  Alameda, Colusa, Fresno, Imperial, Los Angeles, Madera, Merced, Monterey,
  Napa, Orange, Riverside, Sacramento, San Bernardino, San Diego,
  San Francisco, San Joaquin, San Luis Obispo, San Mateo, Santa Barbara,
  Santa Clara, Santa Cruz, Sonoma, Tehama, Tulare, Ventura.
- `Angels` (the Census name for Angels Camp, above).
- `El Paso de Robles`: the formal name of Paso Robles, which is in the list.
  The resolver accepts both.
- `San Buenaventura`: the formal name of Ventura, a county name. Not in the
  list, so it does not trigger a city route.

List names that are also a CDP name elsewhere in California (8): Burbank,
El Cerrito, Fairfax, Greenfield, Live Oak, Mountain View, Paradise,
Rolling Hills. The resolver always picks the incorporated place.
