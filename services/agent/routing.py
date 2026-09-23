"""Conservative deterministic routing before model invocation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.agent.time_resolve import DATA_YEAR_MIN, month_from_text, resolve_time


@dataclass
class RouteDecision:
    path: str
    rule: str
    reason: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    answer: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)


UTILITY_PATTERNS = {
    "PGE": r"\b(?:pge|pg\s*&\s*e|pg\s+and\s+e|pacific gas(?:(?: and| &) electric)?)\b",
    "SCE": r"\b(?:sce|socal edison|southern california edison|edison)\b",
    "SDGE": r"\b(?:sdge|sdg\s*&\s*e|san diego gas(?:(?: and| &) electric)?)\b",
    "PACIFICORP": r"\bpacificorp\b",
    "Liberty": r"\bliberty\b",
    "BVES": r"\b(?:bves|bear valley(?: electric)?)\b",
}

# California counties used for place/county constraint detection. Bare city
# names that coincide with a county seat (Sacramento, Fresno, …) are treated
# as county constraints so the router never silently drops them.
_CA_COUNTIES = (
    "Alameda",
    "Alpine",
    "Amador",
    "Butte",
    "Calaveras",
    "Colusa",
    "Contra Costa",
    "Del Norte",
    "El Dorado",
    "Fresno",
    "Glenn",
    "Humboldt",
    "Imperial",
    "Inyo",
    "Kern",
    "Kings",
    "Lake",
    "Lassen",
    "Los Angeles",
    "Madera",
    "Marin",
    "Mariposa",
    "Mendocino",
    "Merced",
    "Modoc",
    "Mono",
    "Monterey",
    "Napa",
    "Nevada",
    "Orange",
    "Placer",
    "Plumas",
    "Riverside",
    "Sacramento",
    "San Benito",
    "San Bernardino",
    "San Diego",
    "San Francisco",
    "San Joaquin",
    "San Luis Obispo",
    "San Mateo",
    "Santa Barbara",
    "Santa Clara",
    "Santa Cruz",
    "Shasta",
    "Sierra",
    "Siskiyou",
    "Solano",
    "Sonoma",
    "Stanislaus",
    "Sutter",
    "Tehama",
    "Trinity",
    "Tulare",
    "Tuolumne",
    "Ventura",
    "Yolo",
    "Yuba",
)

# Cities that are not county names. A county-seat that shares the county name
# (Sacramento, Fresno) stays a county. These names are not a grid cell, a
# county polygon, or a utility territory, so the router must ask which of
# those to use instead of answering with a statewide or county layer.
_CA_CITIES = (
    "rancho santa margarita",
    "rolling hills estates",
    "la canada flintridge",
    "palos verdes estates",
    "rancho palos verdes",
    "san juan capistrano",
    "south san francisco",
    "desert hot springs",
    "carmel-by-the-sea",
    "san juan bautista",
    "hawaiian gardens",
    "huntington beach",
    "la habra heights",
    "rancho cucamonga",
    "santa fe springs",
    "south lake tahoe",
    "twentynine palms",
    "westlake village",
    "american canyon",
    "california city",
    "fountain valley",
    "huntington park",
    "los altos hills",
    "manhattan beach",
    "west sacramento",
    "cathedral city",
    "citrus heights",
    "east palo alto",
    "imperial beach",
    "mountain house",
    "portola valley",
    "rancho cordova",
    "south el monte",
    "south pasadena",
    "west hollywood",
    "arroyo grande",
    "beverly hills",
    "big bear lake",
    "crescent city",
    "grand terrace",
    "half moon bay",
    "hermosa beach",
    "jurupa valley",
    "laguna niguel",
    "lake elsinore",
    "mammoth lakes",
    "mission viejo",
    "monterey park",
    "moreno valley",
    "mountain view",
    "national city",
    "newport beach",
    "pacific grove",
    "pleasant hill",
    "rancho mirage",
    "redondo beach",
    "rolling hills",
    "santa clarita",
    "scotts valley",
    "thousand oaks",
    "agoura hills",
    "apple valley",
    "baldwin park",
    "bell gardens",
    "corte madera",
    "del rey oaks",
    "farmersville",
    "garden grove",
    "grass valley",
    "grover beach",
    "hidden hills",
    "hillsborough",
    "indian wells",
    "laguna beach",
    "laguna hills",
    "laguna woods",
    "los alamitos",
    "monte sereno",
    "mount shasta",
    "palm springs",
    "port hueneme",
    "redwood city",
    "rohnert park",
    "san clemente",
    "san fernando",
    "santa monica",
    "sierra madre",
    "solana beach",
    "sutter creek",
    "walnut creek",
    "yucca valley",
    "aliso viejo",
    "amador city",
    "angels camp",
    "bakersfield",
    "canyon lake",
    "carpinteria",
    "chino hills",
    "chula vista",
    "culver city",
    "diamond bar",
    "foster city",
    "lake forest",
    "lemon grove",
    "mill valley",
    "morgan hill",
    "nevada city",
    "orange cove",
    "palm desert",
    "paso robles",
    "pico rivera",
    "pismo beach",
    "placerville",
    "point arena",
    "porterville",
    "san anselmo",
    "san gabriel",
    "san jacinto",
    "san leandro",
    "santa maria",
    "santa paula",
    "shasta lake",
    "signal hill",
    "simi valley",
    "suisun city",
    "temple city",
    "victorville",
    "watsonville",
    "west covina",
    "westminster",
    "westmorland",
    "yorba linda",
    "atascadero",
    "bellflower",
    "buena park",
    "burlingame",
    "calipatria",
    "chowchilla",
    "cloverdale",
    "costa mesa",
    "dana point",
    "el cerrito",
    "el segundo",
    "emeryville",
    "fort bragg",
    "fort jones",
    "greenfield",
    "healdsburg",
    "livingston",
    "loma linda",
    "long beach",
    "marysville",
    "menlo park",
    "montebello",
    "pleasanton",
    "ridgecrest",
    "san carlos",
    "san marcos",
    "san marino",
    "san rafael",
    "santa rosa",
    "seal beach",
    "sebastopol",
    "south gate",
    "st. helena",
    "susanville",
    "union city",
    "villa park",
    "yountville",
    "belvedere",
    "blue lake",
    "brentwood",
    "calabasas",
    "calistoga",
    "camarillo",
    "claremont",
    "clearlake",
    "coachella",
    "cupertino",
    "daly city",
    "dos palos",
    "el centro",
    "elk grove",
    "encinitas",
    "escondido",
    "fairfield",
    "firebaugh",
    "fullerton",
    "guadalupe",
    "hawthorne",
    "hollister",
    "holtville",
    "inglewood",
    "irwindale",
    "king city",
    "kingsburg",
    "la mirada",
    "la puente",
    "la quinta",
    "lafayette",
    "lancaster",
    "livermore",
    "los altos",
    "los banos",
    "los gatos",
    "mcfarland",
    "montclair",
    "morro bay",
    "oceanside",
    "palo alto",
    "paramount",
    "patterson",
    "pittsburg",
    "placentia",
    "red bluff",
    "rio vista",
    "riverbank",
    "roseville",
    "san bruno",
    "san dimas",
    "san pablo",
    "san ramon",
    "sand city",
    "santa ana",
    "sausalito",
    "sunnyvale",
    "tehachapi",
    "vacaville",
    "waterford",
    "wheatland",
    "yuba city",
    "adelanto",
    "alhambra",
    "anderson",
    "atherton",
    "beaumont",
    "berkeley",
    "bradbury",
    "brisbane",
    "buellton",
    "calexico",
    "calimesa",
    "campbell",
    "capitola",
    "carlsbad",
    "cerritos",
    "coalinga",
    "commerce",
    "corcoran",
    "coronado",
    "danville",
    "dunsmuir",
    "eastvale",
    "el cajon",
    "el monte",
    "ferndale",
    "fillmore",
    "glendale",
    "glendora",
    "gonzales",
    "hercules",
    "hesperia",
    "highland",
    "industry",
    "la habra",
    "la palma",
    "la verne",
    "lakeport",
    "lakewood",
    "larkspur",
    "lawndale",
    "live oak",
    "loyalton",
    "maricopa",
    "martinez",
    "millbrae",
    "milpitas",
    "monrovia",
    "montague",
    "moorpark",
    "murrieta",
    "oroville",
    "pacifica",
    "palmdale",
    "paradise",
    "pasadena",
    "petaluma",
    "piedmont",
    "plymouth",
    "redlands",
    "richmond",
    "rio dell",
    "rosemead",
    "san jose",
    "saratoga",
    "stockton",
    "temecula",
    "torrance",
    "trinidad",
    "tulelake",
    "whittier",
    "wildomar",
    "williams",
    "woodlake",
    "woodland",
    "woodside",
    "alturas",
    "anaheim",
    "antioch",
    "arcadia",
    "artesia",
    "atwater",
    "banning",
    "barstow",
    "belmont",
    "benicia",
    "brawley",
    "burbank",
    "clayton",
    "compton",
    "concord",
    "corning",
    "cypress",
    "del mar",
    "escalon",
    "fairfax",
    "fontana",
    "fortuna",
    "fremont",
    "gardena",
    "gridley",
    "gustine",
    "hanford",
    "hayward",
    "hughson",
    "isleton",
    "jackson",
    "la mesa",
    "lathrop",
    "lemoore",
    "lincoln",
    "lindsay",
    "lynwood",
    "manteca",
    "maywood",
    "mendota",
    "menifee",
    "modesto",
    "needles",
    "norwalk",
    "oakdale",
    "oakland",
    "ontario",
    "parlier",
    "portola",
    "redding",
    "reedley",
    "rocklin",
    "salinas",
    "seaside",
    "shafter",
    "soledad",
    "solvang",
    "stanton",
    "tiburon",
    "truckee",
    "turlock",
    "vallejo",
    "visalia",
    "willits",
    "willows",
    "windsor",
    "winters",
    "yucaipa",
    "albany",
    "arcata",
    "auburn",
    "avalon",
    "avenal",
    "bishop",
    "blythe",
    "carson",
    "clovis",
    "colfax",
    "colton",
    "corona",
    "cotati",
    "covina",
    "cudahy",
    "delano",
    "dinuba",
    "dorris",
    "downey",
    "duarte",
    "dublin",
    "eureka",
    "exeter",
    "folsom",
    "fowler",
    "gilroy",
    "goleta",
    "irvine",
    "kerman",
    "lomita",
    "lompoc",
    "loomis",
    "malibu",
    "marina",
    "moraga",
    "newark",
    "newman",
    "novato",
    "oakley",
    "orinda",
    "orland",
    "oxnard",
    "perris",
    "pinole",
    "pomona",
    "rialto",
    "sanger",
    "santee",
    "sonora",
    "tustin",
    "upland",
    "vernon",
    "walnut",
    "arvin",
    "azusa",
    "biggs",
    "ceres",
    "chico",
    "chino",
    "colma",
    "davis",
    "dixon",
    "hemet",
    "huron",
    "indio",
    "norco",
    "poway",
    "ripon",
    "selma",
    "tracy",
    "ukiah",
    "vista",
    "wasco",
    "yreka",
    "bell",
    "brea",
    "etna",
    "galt",
    "ione",
    "lodi",
    "ojai",
    "ross",
    "taft",
    "weed",
)

_CITY_NOT_COUNTY = re.compile(
    r"\b(?:"
    + "|".join(re.escape(name) for name in sorted(set(_CA_CITIES), key=len, reverse=True))
    + r")\b",
    re.I,
)

# These municipality names are also ordinary words. Match them only with a
# place cue, so "pine needles" or "the utility industry" is not a city.
_AMBIGUOUS_CITIES = frozenset(
    {"industry", "commerce", "weed", "needles", "paradise", "coronado"}
)
_CITY_CUE_BEFORE = re.compile(
    r"(?:"
    r"\b(?:city|town)\s+of"
    r"|\bdowntown"
    r"|\bout\s+of"
    r"|\b(?:in|at|near|around|outside|inside|into|serving|contains|containing)"
    r")\s+$",
    re.I,
)
# Orange is a county and a color. Bare "orange" is not the county.
_COUNTY_REQUIRES_QUALIFIER = frozenset({"orange"})


def _city_match(lower: str):
    """A non-county city used as a place, or None.

    A name followed by County is a county phrase, not the city. Ambiguous
    names need a place cue such as "in Weed" or "Weed, California".
    """
    for match in _CITY_NOT_COUNTY.finditer(lower):
        name = match.group(0).lower()
        after = lower[match.end() :]
        if re.match(r"\s+county\b", after):
            continue
        if name in _AMBIGUOUS_CITIES:
            before = lower[: match.start()]
            cued = _CITY_CUE_BEFORE.search(before) or re.match(
                r",?\s*california\b", after
            )
            if not cued:
                continue
        return match
    return None


def _city_named_as_county(lower: str) -> str | None:
    """A municipality written as if it were a county, such as Weed County."""
    for match in _CITY_NOT_COUNTY.finditer(lower):
        if re.match(r"\s+county\b", lower[match.end() :]):
            return match.group(0)
    return None

# Datasets whose warehouse tables expose a county column.
_COUNTY_CAPABLE_DATASETS = {
    "calfire_incidents",
    "cpuc_ignitions",
    "epss_outages",
    "psps_events",
    "circuits",
}

_VIZ_DATASET_NAME = {
    "cpuc_ignitions": "ignitions",
    "epss_outages": "epss",
    "psps_events": "psps",
    "calfire_incidents": "calfire",
}
_TIME_SERIES_VIZ = frozenset(
    {"ignitions", "us_ignitions", "epss", "psps", "calfire"}
)

UNSUPPORTED = {
    "cpz": r"\b(?:cpz|circuit protection zone)\b",
    "cost": r"\b(?:cost|price|budget|dollars?|economic|premiums?)\b",
    "air_quality": r"\bair quality\b",
    "evacuation": r"\bevacuat",
    "translation": r"\btranslat",
    "personnel": r"\b(?:firefighters?|personnel|human resources)\b",
    "satellite": r"\bsatellite\b",
    "leadership": r"\b(?:ceo|chief executive)\b",
    "optimization": r"\b(?:optimi[sz]e|optimal|schedule|allocate)\b",
    "damage": r"\b(?:property damage|expected loss|insured loss|fatalit)\b",
    "live_web": (
        r"\b(?:current active fires?|active fires?.*right now|live fires?|"
        r"web search|according to the web|today'?s fires?)\b"
    ),
}

_LIVE_NOW = re.compile(
    r"\b(?:right now|today(?:'s)?(?:\s+weather)?|current|live)\b",
    re.I,
)
_FUTURE_DATE = re.compile(
    r"\b(?:tomorrow|next\s+summer|next\s+year|future\s+years?|"
    r"this\s+fall|upcoming)\b|"
    r"\bwill\b|"
    r"\bexpected\b|"
    r"\bpredict(?:ed|ion|ing|s)?\b|"
    r"\bforecast(?:ed|s|ing)?\b",
    re.I,
)
_PAST_FORECAST = re.compile(
    r"\b(?:was|were|been)\b(?:\s+\w+){0,4}\s+"
    r"(?:forecast|predict)(?:ed|ion|ing|s)?\b|"
    r"\b(?:forecast|predict)(?:ed|ion|ing|s)?\s+(?:was|were)\b",
    re.I,
)
_ADVICE = re.compile(
    r"\b(?:should|recommend(?:s|ed)?|penali[sz]e[sd]?|best\s+strategy)\b",
    re.I,
)
# The asked object is modeled risk. Predict and forecast alone are not risk
# words here: a forecast of event counts is a prediction, not a risk score.
_RISK_OBJECT = re.compile(
    r"\b(?:risk|risky|riskiness|ignition probability|probability of ignition)\b",
    re.I,
)

UNSUPPORTED_ANSWERS = {
    "future_prediction": (
        "This system reports historical records only and does not predict "
        "future events or counts. I can count or chart past years through the "
        "latest loaded data. Which past period should I use?"
    ),
    "ranking": (
        "Ranking is not supported for that grouping. I can rank counties or "
        "utilities in CPUC ignitions, counties in CAL FIRE incidents, or "
        "circuits in EPSS outages — one dataset at a time. I cannot rank "
        "across datasets, rank EPSS by utility, or rank US ignitions by state."
    ),
}

ALL_MODEL_TOOLS = [
    "data_query_records",
    "data_query_rank",
    "data_query_spatial",
    "visualization_create",
    "visualization_inspect",
    "risk_forecast",
    "comparison_run",
]


def candidate_tools(question: str) -> list[str]:
    """Return the smallest plausible catalog without choosing tool arguments."""
    lower = " ".join(question.lower().split())
    has_count = _has_quantity_op(lower)
    has_map = _asks_map_view(lower)
    has_trend = bool(
        re.search(r"\b(?:trend|time series|weekly|monthly|daily)\b", lower)
    )
    has_view = has_map or has_trend
    if _asks_ranking(lower):
        return ["data_query_rank"]
    if has_count and has_view:
        return ["data_query_records", "visualization_create"]
    if has_view:
        return ["visualization_create"]
    if re.search(r"\b(?:spatial|spatially|inside|contain)\b", lower):
        return ["data_query_spatial"]
    if (
        re.search(r"\bcpuc\b", lower)
        and re.search(r"\b(?:us|national)\s+ignitions?\b", lower)
    ):
        return ["data_query_records"]
    # Comparing two named warehouse datasets is never comparison_run.
    named_datasets = _datasets(lower)
    if len(named_datasets) >= 2 and re.search(
        r"\b(?:compare|comparison|versus|\bvs\.?\b)\b", lower
    ):
        return ["data_query_records"]

    candidates: list[str] = []

    def add(name: str) -> None:
        if name not in candidates:
            candidates.append(name)

    if has_view:
        add("visualization_create")
    if re.search(r"\b(?:detail|territor|event id|circuit id)\b", lower):
        add("visualization_inspect")
    if _wants_risk(lower):
        add("risk_forecast")
        add("data_query_spatial")
    if re.search(
        r"\b(?:spatial|spatially|inside|contain|coordinate|latitude|longitude|hftd)\b",
        lower,
    ):
        add("data_query_spatial")
    # Bare "ignition" in "ignition risk" is not a count question.
    if re.search(
        r"\b(?:how many|count|number of|list|records?|outages?|incidents?|psps|epss|cal\s*fire)\b",
        lower,
    ) or (
        re.search(r"\bignitions?\b", lower) and not _wants_risk(lower)
    ):
        add("data_query_records")
    if _asks_ranking(lower):
        add("data_query_rank")
    if re.search(r"\b(?:compare|comparison|versus|\bvs\.?\b|ratio|period)\b", lower):
        add("comparison_run")

    if not candidates:
        return ["data_query_records", "comparison_run", "visualization_create"]
    return candidates[:3]


def _utilities(text: str) -> list[str]:
    found = [
        utility
        for utility, pattern in UTILITY_PATTERNS.items()
        if re.search(pattern, text, re.I)
    ]
    return found


_BREAKDOWN = re.compile(
    r"\b(?:each|every|per)\s+months?\b|"
    r"\b(?:each|every|per)\s+years?\b|"
    r"\b(?:each|every|per)\s+count(?:y|ies)\b|"
    r"\b(?:each|every|per)\s+utilit(?:y|ies)\b|"
    r"\bby\s+months?\b|"
    r"\bby\s+years?\b|"
    r"\bby\s+count(?:y|ies)\b|"
    r"\bby\s+utilit(?:y|ies)\b|"
    r"\byear[- ]by[- ]year\b|"
    r"\bannual(?:ly)?\b|"
    r"\bin each month\b"
)


def _counties(text: str) -> list[str]:
    """Every county named in the question, not just the first."""
    lower = " ".join(text.lower().split())
    found: list[str] = []
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name.lower())}\s+county\b", lower):
            found.append(name)
    if found:
        return found
    if re.search(r"\b(?:near|around|close to)\b", lower):
        return []
    scrubbed = lower
    for pattern in UTILITY_PATTERNS.values():
        scrubbed = re.sub(pattern, " ", scrubbed, flags=re.I)
    scrubbed = " ".join(scrubbed.split())
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        if name.lower() in _COUNTY_REQUIRES_QUALIFIER:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", scrubbed) and name not in found:
            found.append(name)
    return found


def _single_call_would_collapse(
    lower: str,
    utilities: list[str],
    counties: list[str],
    *,
    kind: str,
) -> bool:
    """True when one tool call would drop a named entity or flatten a breakdown."""
    if len(utilities) > 1 or len(counties) > 1:
        return True
    named_years = set(re.findall(r"\b20\d{2}\b", lower))
    if kind == "count" and (len(named_years) > 1 or _BREAKDOWN.search(lower)):
        return True
    if kind == "series" and re.search(
        r"\bannual(?:ly)?\b|\bper\s+years?\b|\byear[- ]by[- ]year\b|\beach\s+years?\b",
        lower,
    ):
        return True
    if kind == "map" and _BREAKDOWN.search(lower):
        return True
    return False


def _defer_collapsed(
    slots: dict[str, Any],
) -> RouteDecision:
    return RouteDecision(
        "model",
        "multi_entity_deferred",
        "A single call would drop a named entity or collapse a breakdown",
        slots=slots,
    )


def _county(text: str) -> str | None:
    """Extract a county / county-seat place constraint from the question."""
    lower = " ".join(text.lower().split())
    # Prefer explicit "X County" phrasing.
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}\s+county\b"
        if re.search(pattern, lower):
            return name
    # Bare county / seat name (e.g. "in sacramento") — skip vague spatial
    # phrasing which has its own clarification path.
    if re.search(r"\b(?:near|around|close to)\b", lower):
        return None
    # Strip IOU phrases so "San Diego Gas & Electric" is not a county hit.
    scrubbed = lower
    for pattern in UTILITY_PATTERNS.values():
        scrubbed = re.sub(pattern, " ", scrubbed, flags=re.I)
    scrubbed = " ".join(scrubbed.split())
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        if name.lower() in _COUNTY_REQUIRES_QUALIFIER:
            continue
        if re.search(rf"\b{re.escape(name.lower())}\b", scrubbed):
            return name
    return None


def _time_filter_args(time_resolution) -> dict[str, Any]:
    """Build year or start/end args without silently widening a month window."""
    start = time_resolution.start_date
    end = time_resolution.end_date
    year = time_resolution.year
    if start and end:
        full_year = (
            start.endswith("-01-01")
            and end.endswith("-12-31")
            and start[:4] == end[:4]
            and year is not None
            and year == int(start[:4])
        )
        if not full_year:
            out: dict[str, Any] = {"start_date": start, "end_date": end}
            # Keep year= alongside a month window so weekly viz schemas that
            # require year still validate; warehouse filters AND the bounds.
            if year is not None:
                out["year"] = year
            return out
    if year is not None:
        return {"year": year}
    if start and end:
        return {"start_date": start, "end_date": end}
    return {}


def _default_series_interval(lower: str, time_resolution) -> str:
    """Honor explicit interval words; otherwise match the view planner's window rule."""
    if "monthly" in lower:
        return "monthly"
    if "daily" in lower:
        return "daily"
    if "weekly" in lower:
        return "weekly"
    start = getattr(time_resolution, "start_date", None)
    end = getattr(time_resolution, "end_date", None)
    if isinstance(start, str) and isinstance(end, str):
        try:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        except ValueError:
            days = 0
        if days > 62:
            return "monthly"
    return "weekly"


def _month_expressed(args: dict[str, Any], month_number: int) -> bool:
    start = args.get("start_date")
    end = args.get("end_date")
    if not (isinstance(start, str) and isinstance(end, str)):
        return False
    try:
        return int(start[5:7]) == month_number and int(end[5:7]) == month_number
    except (TypeError, ValueError, IndexError):
        return False


def _county_expressed(args: dict[str, Any], county: str) -> bool:
    value = args.get("county")
    return isinstance(value, str) and value.lower() == county.lower()


def _block_unexpressed_constraints(
    *,
    question: str,
    tool_calls: list[tuple[str, dict[str, Any]]],
    slots: dict[str, Any],
    rule: str,
    reason: str,
) -> RouteDecision | None:
    """Refuse a deterministic answer that would silently drop asked filters."""
    dropped: list[str] = []
    county = slots.get("county")
    if county and not any(
        _county_expressed(args, county) for _tool, args in tool_calls
    ):
        dropped.append("county")
    month_hit = month_from_text(question)
    if month_hit is not None:
        month_number, _month_name = month_hit
        slot_start = slots.get("start_date")
        slot_end = slots.get("end_date")
        if not any(
            _month_expressed(args, month_number)
            or (
                bool(slot_start)
                and bool(slot_end)
                and args.get("start_date") == slot_start
                and args.get("end_date") == slot_end
            )
            for _tool, args in tool_calls
        ):
            dropped.append("month")
    if not dropped:
        return None
    labels = " and ".join(dropped)
    dataset = slots.get("dataset")
    if "county" in dropped and dataset in {
        "us_ignitions",
        None,
    } and "cpuc" not in question.lower():
        return RouteDecision(
            "unsupported",
            "unexpressable_county_filter",
            (
                f"Question constrains {labels}, but {dataset or 'this dataset'} "
                "cannot apply a county filter"
            ),
            answer=(
                "County filtering is not available for US ignitions "
                "(no county column). Ask for a CPUC or CAL FIRE county count, "
                "or drop the county constraint. I will not answer with a broader "
                f"statewide count that ignores {labels}."
            ),
            slots=slots,
        )
    return RouteDecision(
        "clarification",
        "unexpressed_filter_constraints",
        f"Matched rule cannot express asked {labels} constraints",
        answer=(
            f"I can see {labels} in your question, but the matched read cannot "
            f"apply {'those filters' if len(dropped) > 1 else 'that filter'}. "
            "Should I switch dataset (for example CAL FIRE for county), "
            "narrow the date window another way, or drop those constraints? "
            "I will not answer with a broader unfiltered result."
        ),
        slots=slots,
    )


def _year(text: str, *, today: date | None = None) -> int | None:
    resolution = resolve_time(text, today=today)
    if resolution.status in {"explicit", "relative_year"}:
        # Month windows still expose year= for slots; callers needing the
        # narrowed window should use resolve_time().start_date/end_date.
        return resolution.year
    if resolution.status == "relative_range" and len(resolution.years) == 1:
        return resolution.years[0]
    return None


def _years(text: str, *, today: date | None = None) -> list[int]:
    resolution = resolve_time(text, today=today)
    return list(resolution.years)


def _coords(text: str) -> tuple[float, float] | None:
    match = re.search(
        r"(?<!\d)(-?\d{1,2}\.\d+)\s*[,/]\s*(-?\d{2,3}\.\d+)(?!\d)",
        text,
    )
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _iso_date(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def _wants_risk(lower: str) -> bool:
    """True when the asked object is modeled ignition risk, not a count.

    ``riskiest`` / ``most risky`` / ``highest risk`` are handled earlier as
    ambiguous metric clarifications and are excluded here.
    """
    if re.search(r"\b(?:riskiest|most risky|highest risk)\b", lower):
        return False
    return bool(
        re.search(
            r"\b(?:risky|riskiness|risk|forecast|predict|"
            r"ignition probability|probability of ignition)\b",
            lower,
        )
    )


def _single_risk_date(text: str, time_resolution) -> str | None:
    """ISO day only when the question names one calendar day."""
    start = getattr(time_resolution, "start_date", None)
    end = getattr(time_resolution, "end_date", None)
    if start and end and start == end:
        return start
    return _iso_date(text)


_FORWARD_RELATIVE = re.compile(
    r"\b(?P<phrase>today|tonight|tomorrow|this\s+week|next\s+week|"
    r"this\s+weekend|next\s+weekend)\b"
)
_RISK_COVARIATE_END = date(2025, 12, 31)
_RISK_COVERAGE_LIMIT = (
    "This model scores historical dates only. Weather and vegetation data "
    "end 2025-12-31 and there's no forecast ingestion"
)


def _strip_quotes(text: str) -> str:
    return re.sub(r"(?:\"[^\"]*\"|'[^']*'|“[^”]*”)", " ", text)


def _future_refusal_phrase(text: str, lower: str) -> str | None:
    """Forward modal, expectation, or a year after warehouse coverage.

    A past-tense forecast, an expected value in a covered year, and a
    predict/forecast of historical risk on a covered date stay historical.
    Years before coverage stay on the out-of-coverage clarify.
    """
    years = [int(item) for item in re.findall(r"\b(20\d{2})\b", text)]
    data_max = date.today().year
    ahead = [year for year in years if year > data_max]
    if ahead:
        return str(max(ahead))
    if not _FUTURE_DATE.search(lower):
        return None
    forward = re.search(
        r"\b(?:will|upcoming|this\s+fall|tomorrow|next\s+(?:year|summer|month)|"
        r"future\s+years?)\b",
        lower,
    )
    if _PAST_FORECAST.search(lower) and not forward:
        return None
    covered = bool(years) and all(DATA_YEAR_MIN <= year <= data_max for year in years)
    if re.search(r"\bexpected\s+value\b", lower) and covered and not forward:
        return None
    if re.search(r"\bhistorical\b", lower) and covered and not forward:
        return None
    match = _FUTURE_DATE.search(lower)
    return match.group(0) if match else None


def _asks_for_advice(text: str) -> bool:
    """What a utility or the CPUC should do. Quoted 'should' does not count."""
    bare = _strip_quotes(text)
    if not _ADVICE.search(bare):
        return False
    return bool(
        re.search(
            r"\b(?:cpuc|utilit(?:y|ies)|pge|pg\s*&\s*e|sce|sdge|pacificorp|"
            r"liberty|bear valley|bves)\b",
            bare,
            re.I,
        )
    )


def _forward_relative_phrase(lower: str) -> str | None:
    match = _FORWARD_RELATIVE.search(lower)
    return match.group("phrase") if match else None


def _date_after_risk_coverage(on_date: str | None) -> bool:
    if not on_date:
        return False
    try:
        return date.fromisoformat(on_date) > _RISK_COVARIATE_END
    except ValueError:
        return False


def _risk_date_clarification(
    *,
    text: str,
    time_resolution,
    reason: str,
    slots: dict[str, Any],
) -> RouteDecision:
    """Limitation first: not a formatting problem, a coverage limit."""
    lower = text.lower()
    phrase = _forward_relative_phrase(lower)
    on_date = _single_risk_date(text, time_resolution)
    if phrase:
        return RouteDecision(
            "clarification",
            "risk_future_date",
            "Fitted risk has no forecast ingestion for a forward date",
            answer=(
                f"{_RISK_COVERAGE_LIMIT}, so I can't answer about {phrase}. "
                "Which past date should I score?"
            ),
            slots=slots,
        )
    if _date_after_risk_coverage(on_date):
        return RouteDecision(
            "clarification",
            "risk_future_date",
            "Asked date is after covariate coverage",
            answer=(
                f"{_RISK_COVERAGE_LIMIT}, so I can't score {on_date}. "
                "Which past date should I score?"
            ),
            slots=slots,
        )
    return RouteDecision(
        "clarification",
        "forecast_missing_date",
        reason,
        answer=(
            f"{_RISK_COVERAGE_LIMIT}, so I need one past calendar day "
            "through 2025-12-31. Which past date should I score?"
        ),
        slots=slots,
    )


def _range_for_year(year: int) -> tuple[str, str]:
    return f"{year}-01-01", f"{year}-12-31"


def _dataset(text: str) -> str | None:
    candidates = _datasets(text)
    return candidates[0] if len(set(candidates)) == 1 else None


def _datasets(text: str) -> list[str]:
    candidates: list[str] = []
    checks = [
        ("us_ignitions", r"\b(?:us|national)\s+ignitions?\b"),
        ("epss_outages", r"\bepss\b|\bfast[- ]trip\b"),
        ("psps_events", r"\bpsps\b|\bpublic safety power shutoff"),
        ("calfire_incidents", r"\bcal\s*fire\b|\bcalfire\b"),
        ("cpuc_ignitions", r"\bcpuc\b|\butility[- ](?:caused|attributed|tagged)\b"),
        ("circuits", r"\bcircuits?\b"),
        ("hftd", r"\bhftd\b|\bhigh fire threat"),
        ("iou_territories", r"\biou territor|\butility territor"),
    ]
    for key, pattern in checks:
        if re.search(pattern, text, re.I):
            candidates.append(key)
    if not candidates and re.search(r"\bignitions?\b", text, re.I):
        candidates.append("cpuc_ignitions")
    # Bare "outages" (without PSPS/EPSS) is treated as EPSS for map/count routing.
    if not candidates and re.search(r"\boutages?\b", text, re.I):
        candidates.append("epss_outages")
    return list(dict.fromkeys(candidates))


def _has_quantity_op(lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:how many|count|number of|tally|total number|total of)\b|"
            r"\bclose to\s+\d+\b",
            lower,
        )
    )


def _asks_map_view(lower: str) -> bool:
    """True when the asked object is locations, not a scalar count.

    Location phrasing outranks a count the question also implies. Bare
    ``where`` is included; territory-boundary detection still requires a
    dataset before this becomes a map.
    """
    return bool(
        re.search(
            r"\b(?:map|layer|plot on a map|locations?\s+of|"
            r"(?:see|show(?:\s+me)?)\s+where)\b|"
            r"\bwhere\b",
            lower,
        )
    )


def _has_list_op(lower: str) -> bool:
    return bool(re.search(r"\b(?:list|show me)\b", lower))


def _asks_ranking(lower: str) -> bool:
    return bool(
        re.search(
            r"\brank(?:s|ed|ing)?\b|"
            r"\b(?:circuit|count(?:y|ies)|utilit(?:y|ies)|states?|division|cell)s?\s+"
            r"with\s+the\s+(?:most|highest|largest|greatest)\b|"
            r"\b(?:which|what)\s+(?:circuit|count(?:y|ies)|utilit(?:y|ies)|states?|"
            r"division|cell)s?\b.{0,60}\b(?:most|highest|largest|greatest|top)\b|"
            r"\bhad\s+the\s+(?:most|highest|largest|greatest)\b|"
            r"\b(?:most|highest|largest)\s+(?:\w+\s+){0,4}"
            r"(?:outages?|ignitions?|incidents?|fires?|acres)\b|"
            r"\btop\s+\d+\s+(?:circuit|count(?:y|ies)|utilit|states?)",
            lower,
        )
    )


def _hftd_constraint_unavailable(lower: str) -> bool:
    """Circuit inventory crossed with a tier, or a request to measure HFTD area.

    A count of events inside one tier is a spatial summary, even if the
    question mentions the circuits those events occurred on. A single-tier
    HFTD map still uses the word "areas" for the layer itself, so only
    acreage, square miles, or a singular "area" count as a measurement.
    """
    mentions_tier = bool(
        re.search(r"\bhftd\b|\bhigh fire threat|\btier\s*[23]\b", lower)
    )
    if not mentions_tier:
        return False
    if re.search(r"\bacreage\b|\bsquare miles?\b|\barea of\b|\bhftd area\b", lower):
        return True
    if not re.search(r"\bcircuits?\b", lower):
        return False
    event_count = _has_quantity_op(lower) and re.search(
        r"\b(?:events?|outages?|ignitions?|incidents?|fires?)\b", lower
    )
    return not event_count


def _rank_dimension(lower: str) -> str | None:
    hits: list[str] = []
    if re.search(r"\bcircuits?\b", lower):
        hits.append("circuit")
    if re.search(r"\bcount(?:y|ies)\b", lower):
        hits.append("county")
    if re.search(r"\butilit(?:y|ies)\b", lower):
        hits.append("utility")
    if re.search(r"\bstates?\b", lower):
        hits.append("state")
    if re.search(r"\bdivisions?\b", lower) and not re.search(
        r"\bwhat division\b|\btell me what division\b", lower
    ):
        hits.append("division")
    if re.search(r"\bcells?\b", lower) and not re.search(r"\bgrid cell\b", lower):
        hits.append("cell")
    # "circuit ... and tell me what division" is circuit ranking, not division.
    if "circuit" in hits and "division" in hits:
        hits = [item for item in hits if item != "division"]
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def _rank_metric(lower: str, dataset: str | None) -> str:
    if dataset == "calfire_incidents" and re.search(r"\bacres\b", lower):
        return "acres_burned"
    return "count"


def _asks_spatial_containment(lower: str) -> bool:
    return bool(
        re.search(
            r"\b(?:spatially|inside|within)\b.*\bterritor|\bterritor\w*\b.*\b(?:spatially|inside|within)\b|\binside\b.*\b(?:iou|utility)\b",
            lower,
        )
        or (
            re.search(r"\bterritor", lower)
            and re.search(r"\b(?:ignitions?|outages?|incidents?|events?)\b", lower)
            and _has_quantity_op(lower)
        )
    )


def _asks_territory_boundary(lower: str) -> bool:
    """True only when the user wants the polygon/boundary, not a count inside it."""
    if not re.search(r"\bterritor", lower):
        return False
    if _has_quantity_op(lower):
        return False
    if re.search(r"\b(?:compare|versus|\bvs\.?\b|trend|time series|forecast|predict)\b", lower):
        return False
    if _asks_map_view(lower) and re.search(
        r"\b(?:ignitions?|outages?|incidents?|epss|psps|cal\s*fire)\b", lower
    ):
        return False
    # Boundary asks: territory alone, or "territory map/boundary/geometry".
    if re.search(r"\b(?:boundary|polygon|geometry|footprint|service area|service-area|outline)\b", lower):
        return True
    if re.search(r"\b(?:map|show|display|draw)\b.*\bterritor|\bterritor\w*\b.*\b(?:map|layer)\b", lower):
        return True
    # "What is the SCE territory?" / "SCE utility territory"
    if re.search(r"\b(?:utility|iou)\s+territor|\bterritor\w*\s+for\b", lower):
        return True
    if re.search(r"\b(?:what|where)\b.*\bterritor", lower):
        return True
    # Bare "... territory" with a utility and no other dataset/operation.
    if not re.search(
        r"\b(?:ignitions?|outages?|incidents?|epss|psps|cal\s*fire|risk)\b", lower
    ):
        return True
    return False


def _ignition_definition(lower: str) -> str:
    if re.search(r"\b(?:spatial|spatially|inside)\b|\bterritor", lower):
        return "spatial"
    return "attribute"


def compile_selected_tools(
    question: str,
    selected_tools: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    """Compile deterministic slots only for tools the model selected.

    Unadopted prototype paired with schemas.openai_selector_tools; not wired
    into the orchestrator, which still requires model-authored arguments.
    """
    text = " ".join(question.strip().split())
    lower = text.lower()
    selected = set(selected_tools)
    year = _year(text)
    utilities = _utilities(text)
    datasets = _datasets(text)
    coords = _coords(text)
    calls: list[tuple[str, dict[str, Any]]] = []

    shadow = route_question(question)
    if shadow.path == "deterministic":
        for tool, arguments in shadow.tool_calls:
            if tool in selected:
                calls.append((tool, arguments))
        if calls:
            return calls

    if "data_query_records" in selected:
        for dataset in datasets or ["cpuc_ignitions"]:
            arguments: dict[str, Any] = {
                "dataset": dataset,
                "result_mode": (
                    "records"
                    if re.search(r"\b(?:list|show|sample|records?)\b", lower)
                    else "count"
                ),
            }
            if year:
                arguments["year"] = year
            if utilities and dataset != "us_ignitions":
                arguments["utility"] = utilities[0]
            if dataset == "calfire_incidents":
                arguments["incident_type_mode"] = "wildfire_default"
            calls.append(("data_query_records", arguments))

    if "data_query_spatial" in selected:
        if coords:
            calls.append(
                (
                    "data_query_spatial",
                    {"kind": "point", "lat": coords[0], "lon": coords[1]},
                )
            )
        elif year and utilities:
            start_date, end_date = _range_for_year(year)
            calls.append(
                (
                    "data_query_spatial",
                    {
                        "kind": "summary",
                        "utility": utilities[0],
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )
            )

    if "visualization_create" in selected:
        dataset = datasets[0] if datasets else "cpuc_ignitions"
        dataset_name = {
            "cpuc_ignitions": "ignitions",
            "us_ignitions": "us_ignitions",
            "epss_outages": "epss",
            "psps_events": "psps",
            "calfire_incidents": "calfire",
            "hftd": "hftd",
        }.get(dataset, "ignitions")
        arguments = {
            "kind": (
                "map"
                if _asks_map_view(lower)
                else "time_series"
            ),
            "dataset": dataset_name,
        }
        if year:
            arguments["year"] = year
        if utilities:
            arguments["utility"] = utilities[0]
        interval = next(
            (
                value
                for value in ("daily", "weekly", "monthly")
                if re.search(rf"\b{value}\b", lower)
            ),
            None,
        )
        if interval:
            arguments["interval"] = interval
        calls.append(("visualization_create", arguments))

    # Preserve the expected semantic order for composed count-plus-view requests.
    order = {
        "data_query_records": 0,
        "data_query_spatial": 1,
        "visualization_create": 2,
        "visualization_inspect": 3,
        "risk_forecast": 4,
        "comparison_run": 5,
        "data_query_rank": 6,
    }
    return sorted(calls, key=lambda item: order[item[0]])


def _route_ranking(
    *,
    text: str,
    lower: str,
    slots: dict[str, Any],
    dataset: str | None,
    utilities: list[str],
    county: str | None,
    time_resolution,
) -> RouteDecision | None:
    """Deterministic ranking, or a specific refusal. None if not a rank question."""
    if not _asks_ranking(lower):
        return None

    group_by = _rank_dimension(lower)
    named = _datasets(text)
    # "circuit" is the grouping dimension, not the circuits inventory table.
    if group_by == "circuit":
        named = [item for item in named if item != "circuits"]
    unique_datasets = list(dict.fromkeys(named))
    dataset = unique_datasets[0] if len(unique_datasets) == 1 else dataset

    if len(unique_datasets) >= 2:
        return RouteDecision(
            "unsupported",
            "unsupported_rank_cross_dataset",
            "Ranking cannot mix warehouse datasets",
            answer=(
                "Ranking cannot mix datasets. CPUC ignitions, CAL FIRE incidents, "
                "and US ignitions count different things and are not comparable. "
                "Ask for a ranking in one dataset."
            ),
            slots=slots,
        )

    if group_by == "state" or dataset == "us_ignitions":
        return RouteDecision(
            "unsupported",
            "unsupported_rank_us_state",
            "US ignitions have no state attribute",
            answer=(
                "US ignitions have no state attribute in this warehouse, so I "
                "cannot rank by state. I can count the US sample for a year, "
                "or rank counties in CAL FIRE or CPUC instead."
            ),
            slots=slots,
        )

    if group_by == "utility" and dataset == "epss_outages":
        return RouteDecision(
            "unsupported",
            "unsupported_rank_epss_utility",
            "EPSS is PG&E-only; no utility dimension to rank",
            answer=(
                "EPSS outages in this warehouse are PG&E-only; there is no "
                "utility dimension to rank. I can rank EPSS circuits, or "
                "compare named utilities on a metric that exists for them."
            ),
            slots=slots,
        )

    if group_by in {"cell", "division"}:
        return RouteDecision(
            "unsupported",
            "unsupported_ranking",
            "That ranking dimension is not available",
            answer=UNSUPPORTED_ANSWERS["ranking"],
            slots=slots,
        )

    if not dataset or not group_by:
        return RouteDecision(
            "clarification",
            "ranking_missing_slots",
            "Ranking needs one dataset and one grouping dimension",
            answer=(
                "Which dataset and grouping should I rank? I can rank counties "
                "or utilities in CPUC ignitions, counties in CAL FIRE incidents, "
                "or circuits in EPSS outages — for one year or date range."
            ),
            slots=slots,
        )

    metric = _rank_metric(lower, dataset)
    allowed = {
        ("cpuc_ignitions", "county", "count"),
        ("cpuc_ignitions", "utility", "count"),
        ("calfire_incidents", "county", "count"),
        ("calfire_incidents", "county", "acres_burned"),
        ("epss_outages", "circuit", "count"),
    }
    if (dataset, group_by, metric) not in allowed:
        return RouteDecision(
            "unsupported",
            "unsupported_ranking",
            "That dataset and grouping cannot be ranked",
            answer=UNSUPPORTED_ANSWERS["ranking"],
            slots=slots,
        )

    time_args = _time_filter_args(time_resolution)
    if not time_args:
        return RouteDecision(
            "clarification",
            "ranking_missing_year",
            "Ranking lacks a time period",
            answer="What year or date range should I use?",
            slots=slots,
        )

    if group_by == "county" and county:
        return RouteDecision(
            "clarification",
            "ranking_county_contradiction",
            "Cannot rank counties while filtering to one named county",
            answer=(
                "I can rank counties statewide, or count one named county. "
                "Which do you want?"
            ),
            slots=slots,
        )

    args: dict[str, Any] = {
        "dataset": dataset,
        "group_by": group_by,
        "metric": metric,
        **time_args,
    }
    if utilities and group_by != "utility":
        args["utility"] = utilities[0]
    if county and dataset == "epss_outages":
        args["county"] = county
    if county and dataset == "cpuc_ignitions" and group_by == "utility":
        args["county"] = county
    if dataset == "calfire_incidents":
        args["incident_type_mode"] = "wildfire_default"

    tool_calls = [("data_query_rank", args)]
    blocked = _block_unexpressed_constraints(
        question=text,
        tool_calls=tool_calls,
        slots=slots,
        rule="ranked_records",
        reason="Dataset, grouping dimension, and year are explicit",
    )
    if blocked:
        return blocked
    return RouteDecision(
        "deterministic",
        "ranked_records",
        "Dataset, grouping dimension, and year are explicit",
        tool_calls=tool_calls,
        slots=slots,
    )


def route_question(question: str, *, force_model: bool = False) -> RouteDecision:
    text = " ".join(question.strip().split())
    lower = text.lower()
    utilities = _utilities(text)
    time_resolution = resolve_time(text)
    year = time_resolution.year
    years = list(time_resolution.years)
    dataset = _dataset(text)
    coords = _coords(text)
    county = _county(text)
    slots = {
        "utilities": utilities,
        "year": year,
        "years": years,
        "dataset": dataset,
        "coords": coords,
        "county": county,
        "time_resolution": time_resolution.as_slot(),
        "start_date": time_resolution.start_date,
        "end_date": time_resolution.end_date,
    }

    for key, pattern in UNSUPPORTED.items():
        if re.search(pattern, lower, re.I):
            return RouteDecision(
                "unsupported",
                f"unsupported_{key}",
                "No read-only backend service provides the requested information",
                slots=slots,
                answer=UNSUPPORTED_ANSWERS.get(
                    key,
                    (
                        "This system cannot answer that question with its available "
                        "read-only wildfire services."
                    ),
                ),
            )

    if _LIVE_NOW.search(lower):
        return RouteDecision(
            "unsupported",
            "unsupported_live_web",
            "No read-only backend service provides the requested information",
            slots=slots,
            answer=UNSUPPORTED_ANSWERS.get(
                "live_web",
                (
                    "This system cannot answer that question with its available "
                    "read-only wildfire services."
                ),
            ),
        )
    future_phrase = _future_refusal_phrase(text, lower)
    if future_phrase and _RISK_OBJECT.search(lower):
        return RouteDecision(
            "clarification",
            "risk_future_date",
            "Fitted risk has no forecast ingestion for a forward date",
            slots=slots,
            answer=(
                f"{_RISK_COVERAGE_LIMIT}, so I can't answer about {future_phrase}. "
                "Which past date should I score?"
            ),
        )
    if future_phrase:
        return RouteDecision(
            "unsupported",
            "unsupported_future_prediction",
            "No read-only backend service predicts future events or counts",
            slots=slots,
            answer=UNSUPPORTED_ANSWERS["future_prediction"],
        )
    if _asks_for_advice(text):
        return RouteDecision(
            "unsupported",
            "unsupported_optimization",
            "No read-only backend service provides the requested information",
            slots=slots,
            answer=UNSUPPORTED_ANSWERS.get(
                "optimization",
                (
                    "This system cannot answer that question with its available "
                    "read-only wildfire services."
                ),
            ),
        )

    if re.search(r"\b(?:riskiest|most risky|highest risk)\b", lower):
        return RouteDecision(
            "clarification",
            "ambiguous_risk_metric",
            "Risk could mean fitted cell intensity, ignition count, incidents, or outages",
            slots=slots,
            answer=(
                "Which risk measure and time period should I use—for example "
                "ignition count, CAL FIRE incidents, EPSS outages, or fitted cell risk?"
            ),
        )
    if re.search(r"\bnear me\b", lower) and _coords(text) is None:
        return RouteDecision(
            "clarification",
            "missing_location",
            "A location is required",
            slots=slots,
            answer="What latitude/longitude or bounding box should I use?",
        )
    # "near/around/close to X" without an explicit radius or coordinates is an
    # undefined spatial scope; do not silently invent county containment.
    proximity_is_numeric = re.search(
        r"\b(?:around|close to|near)\s+(?:20\d{2}|a\s+)?\d+\b", lower
    )
    if (
        re.search(r"\b(?:near|around|close to)\b", lower)
        and not re.search(r"\bnear me\b", lower)
        and not proximity_is_numeric
        and _coords(text) is None
        and not re.search(
            r"(?:\b\d+(?:\.\d+)?\s*(?:km|mi|miles?|kilometers?)\b|\bradius\b)",
            lower,
        )
    ):
        return RouteDecision(
            "clarification",
            "undefined_spatial_scope",
            "Near/around requires an explicit radius or polygon",
            slots=slots,
            answer=(
                "How should that nearby area be defined? Provide a radius "
                "(for example 25 km) or a county/utility polygon to use."
            ),
        )
    named_county = _city_named_as_county(lower)
    if named_county:
        return RouteDecision(
            "clarification",
            "unknown_county",
            "A municipality was written as a county",
            slots=slots,
            answer=(
                f"{named_county.title()} County is not a county in this warehouse. "
                "Which county should I use?"
            ),
        )
    city = _city_match(lower)
    if city:
        return RouteDecision(
            "clarification",
            "city_needs_place",
            "A city that is not a county name is not a query layer",
            slots=slots,
            answer=(
                f"{city.group(0).title()} is a city, not a county or utility "
                "territory. Which coordinates, county, or utility territory "
                "should I use? I will not answer with a statewide or county layer."
            ),
        )
    if _hftd_constraint_unavailable(lower):
        return RouteDecision(
            "clarification",
            "hftd_constraint_unavailable",
            "No tool intersects circuits with an HFTD tier or measures HFTD area",
            slots=slots,
            answer=(
                "No tool can intersect circuits with an HFTD tier, or measure "
                "HFTD area or acreage. I can map one HFTD tier, or list circuits "
                "for one utility. Which of those do you want?"
            ),
        )
    region_text = lower
    for pattern in UTILITY_PATTERNS.values():
        region_text = re.sub(pattern, " ", region_text, flags=re.I)
    if re.search(r"\b(?:northern|southern)\s+california\b", region_text):
        return RouteDecision(
            "clarification",
            "undefined_region",
            "Northern/southern California boundaries are not defined by a service",
            slots=slots,
            answer=(
                "How should northern and southern California be defined? "
                "The warehouse has no such region polygons."
            ),
        )

    if time_resolution.status == "ambiguous":
        return RouteDecision(
            "clarification",
            "ambiguous_relative_time",
            time_resolution.reason or "Relative time could not be resolved",
            answer=(
                "Which calendar year or exact date range should I use? "
                "Vague phrases like recent, lately, or currently are not mapped "
                "to a year automatically."
            ),
            slots=slots,
        )
    if time_resolution.status == "out_of_coverage":
        return RouteDecision(
            "clarification",
            "time_out_of_coverage",
            time_resolution.reason or "Resolved year outside data coverage",
            answer=(
                time_resolution.reason
                or "That time period is outside the years available in the warehouse."
            ),
            slots=slots,
        )

    if force_model:
        return RouteDecision(
            "model",
            "forced_eval",
            "Evaluation case forces model tier",
            slots=slots,
        )

    ranking_decision = _route_ranking(
        text=text,
        lower=lower,
        slots=slots,
        dataset=dataset,
        utilities=utilities,
        county=county,
        time_resolution=time_resolution,
    )
    if ranking_decision is not None:
        return ranking_decision

    # Explicit compositions need orchestration rather than silently dropping a clause.
    has_count_clause = _has_quantity_op(lower)

    # County + fire/ignition count without a county-capable dataset must not
    # fall through to the model (which may invent an IOU) or answer statewide.
    if (
        county
        and has_count_clause
        and dataset not in _COUNTY_CAPABLE_DATASETS
        and re.search(r"\b(?:wildfires?|ignitions?|fires?)\b", lower)
    ):
        return RouteDecision(
            "unsupported",
            "unexpressable_county_filter",
            (
                f"Question constrains county={county}, but "
                f"{dataset or 'the default ignition read'} cannot apply it"
            ),
            answer=(
                "County filtering needs a dataset that stores county. "
                "Ask for a CAL FIRE county incident count, a CPUC county ignition "
                "count, or drop the county constraint. I will not answer with a "
                "broader statewide count that ignores county."
            ),
            slots=slots,
        )
    has_trend_clause = bool(
        re.search(r"\b(?:trend|time series|weekly|monthly|daily)\b", lower)
    )
    if has_count_clause and has_trend_clause:
        return RouteDecision(
            "model",
            "multi_intent_count_and_trend",
            "Question explicitly requires both a scalar read and a time series",
            slots=slots,
        )
    if (
        re.search(r"\bterritor", lower)
        and re.search(r"\b(?:map|layer)\b", lower)
        and re.search(
            r"\b(?:ignitions?|outages?|incidents?|epss|psps|cal\s*fire)\b", lower
        )
    ):
        return RouteDecision(
            "model",
            "multi_intent_territory_and_map",
            "Question explicitly requires territory plus an event map layer",
            slots=slots,
        )

    # Fixed two-step coordinate → cell → risk chain.
    if coords and _wants_risk(lower):
        on_date = _single_risk_date(text, time_resolution)
        if (
            _forward_relative_phrase(lower)
            or _date_after_risk_coverage(on_date)
            or not on_date
        ):
            return _risk_date_clarification(
                text=text,
                time_resolution=time_resolution,
                reason="Historical forecast requires a scoreable past date",
                slots=slots,
            )
        return RouteDecision(
            "deterministic",
            "coordinate_risk_chain",
            "Coordinates and date fully specify the spatial→risk chain",
            tool_calls=[
                (
                    "data_query_spatial",
                    {"kind": "point", "lat": coords[0], "lon": coords[1]},
                ),
                # cell_id is resolved from the first result by the orchestrator.
                ("risk_forecast", {"cell_id": "$grid_cell_id", "date": on_date}),
            ],
            slots=slots,
        )

    if coords and re.search(r"\b(?:which|what|iou|hftd|tier|grid|cell)\b", lower):
        return RouteDecision(
            "deterministic",
            "coordinate_context",
            "Explicit coordinates and spatial-context terms",
            tool_calls=[
                (
                    "data_query_spatial",
                    {"kind": "point", "lat": coords[0], "lon": coords[1]},
                )
            ],
            slots=slots,
        )

    # Risk by explicit cell and date.
    cell_match = re.search(r"\bcell(?:_id)?\s*(\d{1,3})\b", lower)
    wants_risk = _wants_risk(lower)
    if wants_risk and cell_match:
        on_date = _single_risk_date(text, time_resolution)
        if (
            _forward_relative_phrase(lower)
            or _date_after_risk_coverage(on_date)
            or not on_date
        ):
            return _risk_date_clarification(
                text=text,
                time_resolution=time_resolution,
                reason="Cell forecast requires a scoreable past date",
                slots=slots,
            )
        return RouteDecision(
            "deterministic",
            "cell_risk",
            "Explicit cell and date",
            tool_calls=[
                (
                    "risk_forecast",
                    {"cell_id": int(cell_match.group(1)), "date": on_date},
                )
            ],
            slots=slots,
        )

    # County or utility + risk + one calendar day (exactly one place).
    if wants_risk and not coords:
        on_date = _single_risk_date(text, time_resolution)
        if county and len(utilities) == 1:
            return RouteDecision(
                "clarification",
                "ambiguous_risk_place",
                "County and utility both named; risk accepts exactly one place",
                answer=(
                    "Should I score the county or the utility territory? "
                    "Fitted risk accepts exactly one place."
                ),
                slots=slots,
            )
        if county:
            if (
                _forward_relative_phrase(lower)
                or _date_after_risk_coverage(on_date)
                or not on_date
            ):
                return _risk_date_clarification(
                    text=text,
                    time_resolution=time_resolution,
                    reason="County forecast requires a scoreable past date",
                    slots=slots,
                )
            return RouteDecision(
                "deterministic",
                "county_risk",
                "Explicit county and date",
                tool_calls=[
                    ("risk_forecast", {"county": county, "date": on_date}),
                ],
                slots=slots,
            )
        if len(utilities) == 1:
            if (
                _forward_relative_phrase(lower)
                or _date_after_risk_coverage(on_date)
                or not on_date
            ):
                return _risk_date_clarification(
                    text=text,
                    time_resolution=time_resolution,
                    reason="Utility forecast requires a scoreable past date",
                    slots=slots,
                )
            return RouteDecision(
                "deterministic",
                "utility_risk",
                "Explicit utility and date",
                tool_calls=[
                    (
                        "risk_forecast",
                        {"utility": utilities[0], "date": on_date},
                    ),
                ],
                slots=slots,
            )
        return RouteDecision(
            "clarification",
            "risk_missing_place",
            "Fitted risk needs a cell, county, utility, or coordinates",
            answer=(
                "Which place should I score? Fitted ignition risk accepts "
                "a grid cell, a county, a utility territory (PGE, SCE, or SDGE), "
                "or latitude/longitude, plus one historical calendar day."
            ),
            slots=slots,
        )

    # Explicit comparisons.
    if re.search(r"\bcompare|versus|\bvs\.?\b", lower):
        metric: str | None = None
        if "epss-to-ignition" in lower or "epss to ignition" in lower:
            metric = "epss_to_ignition_ratio"
        elif "epss" in lower or "outage" in lower:
            metric = "epss_outage_count"
        elif "cal fire" in lower or "calfire" in lower:
            metric = "calfire_incident_count"
        elif (
            "ignition" in lower
            or "wildfire activity" in lower
            or re.search(r"\bwildfire(?:s)?\b", lower)
        ):
            metric = "ignition_count"

        # Two calendar years + one utility → periods. Two utilities + one year
        # → utilities. Never infer periods from a single relative year.
        if metric and len(years) == 2 and len(utilities) == 1:
            a_start, a_end = _range_for_year(years[0])
            b_start, b_end = _range_for_year(years[1])
            return RouteDecision(
                "deterministic",
                "period_comparison",
                "Metric, scope, and both periods are explicit",
                tool_calls=[
                    (
                        "comparison_run",
                        {
                            "kind": "periods",
                            "scope_type": "utility",
                            "scope": utilities[0],
                            "metric": metric,
                            "period_a_start": a_start,
                            "period_a_end": a_end,
                            "period_b_start": b_start,
                            "period_b_end": b_end,
                            "ignition_definition": _ignition_definition(lower),
                        },
                    )
                ],
                slots=slots,
            )

        if (
            metric
            and len(utilities) >= 2
            and year is not None
            and "us ignition" not in lower
        ):
            start, end = _range_for_year(year)
            return RouteDecision(
                "deterministic",
                "utility_comparison",
                "Metric, utilities, and year are explicit",
                tool_calls=[
                    (
                        "comparison_run",
                        {
                            "kind": "utilities",
                            "utilities": utilities,
                            "metric": metric,
                            "start_date": start,
                            "end_date": end,
                            "normalize": (
                                "per_circuit" if "per circuit" in lower else "none"
                            ),
                            "ignition_definition": _ignition_definition(lower),
                        },
                    )
                ],
                slots=slots,
            )

        tiers = re.findall(r"tier\s*([23])", lower)
        if metric and len(set(tiers)) == 2 and year is not None:
            start, end = _range_for_year(year)
            return RouteDecision(
                "deterministic",
                "hftd_comparison",
                "Metric, HFTD tiers, and year are explicit",
                tool_calls=[
                    (
                        "comparison_run",
                        {
                            "kind": "regions",
                            "region_type": "hftd",
                            "regions": ["Tier 2", "Tier 3"],
                            "metric": metric,
                            "start_date": start,
                            "end_date": end,
                        },
                    )
                ],
                slots=slots,
            )
        return RouteDecision(
            "model",
            "open_comparison",
            "Comparison is not a single fully specified backend comparison",
            slots=slots,
        )

    # Explicit map / trend.
    # Map + named trend must fire both tools. Interval words alone ("monthly
    # map") are not a trend ask — those stay map-only.
    # Location phrasing (where / locations of) is a map ask, even if the
    # question also sounds like a count.
    has_map_clause = _asks_map_view(lower)
    has_named_trend = bool(re.search(r"\b(?:trend|time series)\b", lower))
    if has_map_clause and has_named_trend and dataset:
        viz_dataset = _VIZ_DATASET_NAME.get(dataset, dataset)
        if viz_dataset in _TIME_SERIES_VIZ:
            time_args = _time_filter_args(time_resolution)
            if not time_args:
                return RouteDecision(
                    "clarification",
                    "map_plus_trend_missing_year",
                    "Map plus trend lacks a year/date range",
                    answer="What year should I map and chart?",
                    slots=slots,
                )
            interval = _default_series_interval(lower, time_resolution)
            shared: dict[str, Any] = {
                "dataset": viz_dataset,
                **time_args,
            }
            if utilities:
                shared["utility"] = utilities[0]
            if county and dataset in _COUNTY_CAPABLE_DATASETS:
                shared["county"] = county
            tool_calls = [
                ("visualization_create", {"kind": "map", **shared}),
                (
                    "visualization_create",
                    {"kind": "time_series", "interval": interval, **shared},
                ),
            ]
            blocked = _block_unexpressed_constraints(
                question=text,
                tool_calls=tool_calls,
                slots=slots,
                rule="map_plus_trend",
                reason="Explicit map, trend, dataset, and year",
            )
            if blocked:
                return blocked
            return RouteDecision(
                "deterministic",
                "map_plus_trend",
                "Explicit map, trend, dataset, and year",
                tool_calls=tool_calls,
                slots=slots,
            )

    if has_map_clause and dataset:
        if _single_call_would_collapse(
            lower, utilities, _counties(text), kind="map"
        ):
            return _defer_collapsed(slots)
        time_args = _time_filter_args(time_resolution)
        if not time_args and dataset != "hftd":
            return RouteDecision(
                "clarification",
                "map_missing_year",
                "Map request lacks a year/date range",
                answer="What year or date range should I map?",
                slots=slots,
            )
        viz_dataset = {
            "cpuc_ignitions": "ignitions",
            "epss_outages": "epss",
            "psps_events": "psps",
            "calfire_incidents": "calfire",
        }.get(dataset, dataset)
        args: dict[str, Any] = {
            "kind": "map",
            "dataset": viz_dataset,
            **time_args,
        }
        if utilities:
            args["utility"] = utilities[0]
        if county and dataset in _COUNTY_CAPABLE_DATASETS:
            args["county"] = county
        tool_calls = [("visualization_create", args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=tool_calls,
            slots=slots,
            rule="map",
            reason="Explicit map, dataset, and time filter",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "map",
            "Explicit map, dataset, and time filter",
            tool_calls=tool_calls,
            slots=slots,
        )

    if re.search(r"\b(?:trend|time series|weekly|monthly|daily)\b", lower) and dataset:
        if _single_call_would_collapse(
            lower, utilities, _counties(text), kind="series"
        ):
            return _defer_collapsed(slots)
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "trend_missing_year",
                "Time series lacks a year",
                answer="What year should I chart?",
                slots=slots,
            )
        interval = _default_series_interval(lower, time_resolution)
        viz_dataset = {
            "cpuc_ignitions": "ignitions",
            "epss_outages": "epss",
            "psps_events": "psps",
            "calfire_incidents": "calfire",
        }.get(dataset, dataset)
        args = {
            "kind": "time_series",
            "dataset": viz_dataset,
            "interval": interval,
            **time_args,
        }
        if utilities:
            args["utility"] = utilities[0]
        if county and dataset in _COUNTY_CAPABLE_DATASETS:
            args["county"] = county
        tool_calls = [("visualization_create", args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=tool_calls,
            slots=slots,
            rule="time_series",
            reason="Explicit trend, dataset, and year",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "time_series",
            "Explicit trend, dataset, and year",
            tool_calls=tool_calls,
            slots=slots,
        )

    # Quantity inside a utility territory is spatial containment, never the
    # bare territory polygon lookup (and never attribute-only utility=).
    if (
        has_count_clause
        and _asks_spatial_containment(lower)
        and len(utilities) == 1
        and dataset in {None, "cpuc_ignitions", "epss_outages", "calfire_incidents"}
    ):
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "spatial_missing_year",
                "Spatial containment count lacks a time period",
                answer="What year or date range should I use?",
                slots=slots,
            )
        # Spatial summary uses dates, not year=.
        if "year" in time_args:
            start, end = _range_for_year(int(time_args["year"]))
            spatial_time = {"start_date": start, "end_date": end}
        else:
            spatial_time = {
                "start_date": time_args["start_date"],
                "end_date": time_args["end_date"],
            }
        tool_calls = [
            (
                "data_query_spatial",
                {
                    "kind": "summary",
                    "utility": utilities[0],
                    **spatial_time,
                },
            )
        ]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=tool_calls,
            slots=slots,
            rule="spatial_utility_count",
            reason="Quantity operation outranks territory keyword; use spatial summary",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "spatial_utility_count",
            "Quantity operation outranks territory keyword; use spatial summary",
            tool_calls=tool_calls,
            slots=slots,
        )

    # Territory boundary only when that is the asked object (no count/map of events).
    if _asks_territory_boundary(lower) and len(utilities) == 1:
        return RouteDecision(
            "deterministic",
            "utility_territory",
            "Explicit utility territory boundary request",
            tool_calls=[
                (
                    "visualization_inspect",
                    {"kind": "utility_territory", "utility": utilities[0]},
                )
            ],
            slots=slots,
        )

    circuit_match = re.search(r"\b(\d{9})\b", text)
    if circuit_match and re.search(r"\b(?:detail|outage|circuit)\b", lower):
        args: dict[str, Any] = {
            "kind": "event_detail",
            "dataset": "circuits",
            "record_id": circuit_match.group(1),
        }
        if year:
            args["year"] = year
        return RouteDecision(
            "deterministic",
            "circuit_detail",
            "Explicit circuit identifier",
            tool_calls=[("visualization_inspect", args)],
            slots=slots,
        )

    # Straight count/list. A bare "ignitions" defaults to CPUC only when a
    # utility is named; otherwise it remains ambiguous and goes to the model.
    is_count = _has_quantity_op(lower)
    is_list = _has_list_op(lower)
    if (is_count or is_list) and dataset:
        if _single_call_would_collapse(
            lower, utilities, _counties(text), kind="count"
        ):
            return _defer_collapsed(slots)
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "records_missing_year",
                "Filtered read lacks a time period",
                answer="What year or date range should I use?",
                slots=slots,
            )
        args: dict[str, Any] = {
            "dataset": dataset,
            "result_mode": "count" if is_count else "records",
            **time_args,
        }
        if not is_count:
            args["limit"] = 25
        if utilities:
            args["utility"] = utilities[0]
        if county and dataset in _COUNTY_CAPABLE_DATASETS:
            args["county"] = county
        tool_calls = [("data_query_records", args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=tool_calls,
            slots=slots,
            rule="filtered_records",
            reason="Dataset, operation, and year are explicit",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "filtered_records",
            "Dataset, operation, and year are explicit",
            tool_calls=tool_calls,
            slots=slots,
        )

    return RouteDecision(
        "model",
        "open_ended",
        "No high-confidence deterministic rule matched",
        slots=slots,
    )
