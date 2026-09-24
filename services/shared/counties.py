"""Canonical California county names and the one place they are normalized.

The warehouse stores county names as the Census TIGER names in
``wildfire.counties`` ("Butte", "Los Angeles", "San Luis Obispo"). Every
filter that takes a county must resolve the caller's value to one of these
names or fail loudly. Before this module, "Butte County" matched nothing and
the services answered 0 instead of an error.
"""

from __future__ import annotations

import difflib
import re

CALIFORNIA_COUNTIES: tuple[str, ...] = (
    "Alameda", "Alpine", "Amador", "Butte", "Calaveras", "Colusa", "Contra Costa",
    "Del Norte", "El Dorado", "Fresno", "Glenn", "Humboldt", "Imperial", "Inyo",
    "Kern", "Kings", "Lake", "Lassen", "Los Angeles", "Madera", "Marin", "Mariposa",
    "Mendocino", "Merced", "Modoc", "Mono", "Monterey", "Napa", "Nevada", "Orange",
    "Placer", "Plumas", "Riverside", "Sacramento", "San Benito", "San Bernardino",
    "San Diego", "San Francisco", "San Joaquin", "San Luis Obispo", "San Mateo",
    "Santa Barbara", "Santa Clara", "Santa Cruz", "Shasta", "Sierra", "Siskiyou",
    "Solano", "Sonoma", "Stanislaus", "Sutter", "Tehama", "Trinity", "Tulare",
    "Tuolumne", "Ventura", "Yolo", "Yuba",
)

# Known short forms and spellings, keyed by their normalized form (lowercase,
# punctuation removed, single spaces, no trailing "county"). Only
# abbreviations that mean one county belong here: "SB" could be Santa
# Barbara, San Bernardino, or San Benito, so it is rejected with all three.
COUNTY_ALIASES: dict[str, str] = {
    "la": "Los Angeles",
    "l a": "Los Angeles",
    "los angeles co": "Los Angeles",
    "sf": "San Francisco",
    "san fran": "San Francisco",
    "slo": "San Luis Obispo",
    "san bernadino": "San Bernardino",
    "san berdoo": "San Bernardino",
    "eldorado": "El Dorado",
    "contracosta": "Contra Costa",
    "delnorte": "Del Norte",
    "sanjoaquin": "San Joaquin",
    "santa clara co": "Santa Clara",
    "st clara": "Santa Clara",
    "st cruz": "Santa Cruz",
    "st barbara": "Santa Barbara",
}

_SUFFIX = re.compile(r"\s+(?:county|co\.?|cnty)$", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


class UnknownCountyError(ValueError):
    """A county value that matches no California county."""

    def __init__(self, value: str, suggestions: list[str]) -> None:
        self.value = value
        self.suggestions = suggestions
        hint = (
            " Did you mean " + " or ".join(suggestions) + "?"
            if suggestions
            else " Use a California county name such as Butte or Los Angeles."
        )
        super().__init__(f"unknown county {value!r}; it matches no California county.{hint}")


def _key(value: str) -> str:
    text = " ".join(str(value).replace("_", " ").split()).strip()
    text = _SUFFIX.sub("", text).strip()
    text = _PUNCT.sub(" ", text.lower())
    return " ".join(text.split())


_BY_KEY: dict[str, str] = {_key(name): name for name in CALIFORNIA_COUNTIES}
_BY_KEY.update({name.lower().replace(" ", ""): name for name in CALIFORNIA_COUNTIES})


def _initials(name: str) -> str:
    return "".join(word[0] for word in name.lower().split())


def county_suggestions(value: str, *, limit: int = 3) -> list[str]:
    """Closest canonical county names for an unmatched value.

    An abbreviation that is the initials of several counties ("SB") lists
    every one of them, so the caller can see the choice.
    """
    key = _key(value)
    if not key:
        return []
    compact = key.replace(" ", "")
    initials = [name for name in CALIFORNIA_COUNTIES if _initials(name) == compact]
    if initials:
        return initials
    keys = list(_BY_KEY)
    close = difflib.get_close_matches(key, keys, n=limit * 2, cutoff=0.6)
    found: list[str] = []
    for item in close:
        name = _BY_KEY[item]
        if name not in found:
            found.append(name)
    # A value that is a prefix or a substring of a county (or vice versa) is
    # a likely truncation or extra word.
    for name in CALIFORNIA_COUNTIES:
        low = name.lower()
        if name not in found and (low.startswith(key) or key in low or low in key):
            found.append(name)
    return found[:limit]


def normalize_county(value: str) -> str:
    """Resolve any spelling of a California county to its canonical name.

    Strips a trailing "County", ignores case and punctuation, and applies the
    known aliases. Raises UnknownCountyError, with close matches, for anything
    else. Never returns a value that is not in CALIFORNIA_COUNTIES.
    """
    key = _key(value)
    if not key:
        raise UnknownCountyError(value, [])
    name = _BY_KEY.get(key) or COUNTY_ALIASES.get(key) or _BY_KEY.get(key.replace(" ", ""))
    if name is None:
        raise UnknownCountyError(value, county_suggestions(value))
    return name
