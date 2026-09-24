"""California city center points from the Census Gazetteer places file.

A city's internal point is one location inside the city. It can answer
"which utility territory or HFTD tier is this city in" and "fitted risk
near this city on a past date" with a caveat. It cannot answer counts or
lists inside a city, or whether any part of a city is in a tier; that needs
city boundary polygons. Source and vintage: data/places/README.md.
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PLACES_CSV = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "places"
    / "ca_places_gazetteer_2025.csv"
)
GAZETTEER_VINTAGE = "2025"

# Incorporated places only. Census place types 25 (city) and 43 (town).
# CDPs are left out so a name like Paradise or Mountain View resolves to the
# incorporated place, never a same-named CDP elsewhere in the state.
_INCORPORATED = frozenset({"city", "town"})

# Common names that differ from the Census place name.
_ALIASES = {"angels camp": "angels"}


@dataclass(frozen=True)
class CityPoint:
    name: str
    place_type: str
    geoid: str
    lat: float
    lon: float


def normalize_place_name(name: str) -> str:
    """Lowercase ASCII, so La Cañada Flintridge matches la canada flintridge."""
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    )
    return " ".join(ascii_name.lower().split())


@lru_cache(maxsize=1)
def _index() -> dict[str, CityPoint]:
    index: dict[str, CityPoint] = {}
    with PLACES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["place_type"] not in _INCORPORATED:
                continue
            point = CityPoint(
                name=row["name"],
                place_type=row["place_type"],
                geoid=row["geoid"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
            )
            # "El Paso de Robles (Paso Robles)" answers to both names.
            keys = [row["name"]]
            if row["name"].endswith(")") and " (" in row["name"]:
                formal, common = row["name"][:-1].split(" (", 1)
                keys = [formal, common]
            for key in keys:
                normalized = normalize_place_name(key)
                if normalized in index:
                    raise ValueError(f"Duplicate incorporated place name: {key}")
                index[normalized] = point
    for alias, target in _ALIASES.items():
        index[alias] = index[target]
    return index


def city_point(name: str) -> CityPoint | None:
    """The internal point of an incorporated California city, or None."""
    return _index().get(normalize_place_name(name))


def incorporated_names() -> frozenset[str]:
    return frozenset(_index())


@lru_cache(maxsize=4)
def county_word_places(counties: tuple[str, ...]) -> dict[str, CityPoint]:
    """Census places whose name contains a county name, such as Kings Beach.

    Keyed by normalized name. Includes census designated places (CDPs), since
    a name like Kings Beach or Plumas Lake is that place, not Kings or Plumas
    County. An incorporated place wins over a CDP of the same name.
    """
    words = [normalize_place_name(county) for county in counties]
    found: dict[str, CityPoint] = {}
    with PLACES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = normalize_place_name(row["name"])
            if name in words or not any(
                f" {word} " in f" {name} " for word in words
            ):
                continue
            point = CityPoint(
                name=row["name"],
                place_type=row["place_type"],
                geoid=row["geoid"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
            )
            current = found.get(name)
            if current is None or (
                current.place_type not in _INCORPORATED
                and row["place_type"] in _INCORPORATED
            ):
                found[name] = point
    return found


def city_point_caveat(point: CityPoint) -> str:
    kind = (
        f"incorporated {point.place_type}"
        if point.place_type in _INCORPORATED
        else "census designated place (unincorporated)"
    )
    return (
        f"This answer uses one point for {point.name}: the Census "
        f"{GAZETTEER_VINTAGE} Gazetteer internal point of the {kind} "
        f"({point.lat:.4f}, {point.lon:.4f}). Parts of the "
        "place may be in a different utility territory, HFTD tier, or grid cell. "
        "A center point outside Tier 2 or Tier 3 does not mean the whole place "
        "is outside the HFTD."
    )


IOU_TERRITORY_NOT_PROVIDER = (
    "The utility layer holds investor-owned utility service territory "
    "polygons only. They also cover some cities that run their own municipal "
    "utility, so this is the IOU territory containing the point, not "
    "necessarily who supplies power there."
)
