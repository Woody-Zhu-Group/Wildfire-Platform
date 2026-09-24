"""Conservative deterministic routing before model invocation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.agent.clarify_missing import (
    complete_clarification,
    rank_slots_question,
    series_dataset_question,
)
from services.agent.places import (
    GAZETTEER_VINTAGE,
    CityPoint,
    city_point,
    county_word_places,
)
from services.agent.time_resolve import (
    DATA_YEAR_MIN,
    explicit_month_range_in_year,
    explicit_month_year_range,
    month_from_text,
    named_months,
    resolve_time,
)
from services.shared.dataset_registry import (
    SERIES_DATASETS,
    ALL_CAUSES_AFTER_IGNITIONS_PATTERN,
    ALL_CAUSES_BEFORE_IGNITIONS_PATTERN,
    BARE_IGNITIONS_PATTERN,
    BARE_OUTAGES_PATTERN,
    CALIFORNIA_COUNTIES,
    COUNTIES_NEEDING_QUALIFIER,
    COUNT_MAP_DATASETS,
    CPUC_OR_UTILITY_BEFORE_IGNITIONS_PATTERN,
    DATASET_QUESTION_PATTERNS,
    EVENT_DATASET_WORDS,
    HDW_YEARS,
    HFTD_TIER_NAMES,
    IGNITION_QUALIFIER_PATTERN,
    LAYER_VIZ_KEYS,
    SAMPLE_BEFORE_IGNITIONS_PATTERN,
    STAT_LABELS,
    TIER_MENTION_PATTERN,
    US_SAMPLE_NAMED_PATTERN,
    UTILITY_ADVICE_SUBJECT_WORDS,
    UTILITY_CLARIFY_LABELS,
    UTILITY_PATTERNS,
)


@dataclass
class RouteDecision:
    path: str
    rule: str
    reason: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    answer: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)


# UTILITY_PATTERNS (imported above) holds how a question names each utility.
# California counties used for place/county constraint detection. Bare city
# names that coincide with a county seat (Sacramento, Fresno, …) are treated
# as county constraints so the router never silently drops them.
_CA_COUNTIES = CALIFORNIA_COUNTIES

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
    {
        "industry", "commerce", "weed", "needles", "paradise", "coronado",
        "santa ana", "winters", "marina", "vista", "bell", "ross", "davis",
        "live oak", "highland",
    }
)
# An ambiguous name that continues into a longer phrase is not that city:
# Marina del Rey is not Marina, and Santa Ana winds are not Santa Ana.
_CITY_CONTINUES = re.compile(r"\s+(?:del|de\s+la|de|winds?)\b", re.I)
_CITY_CUE_BEFORE = re.compile(
    r"(?:"
    r"\b(?:city|town)\s+of"
    r"|\bdowntown"
    r"|\bout\s+of"
    r"|\b(?:in|at|near|around|outside|inside|into|serving|contains|containing)"
    r")\s+$",
    re.I,
)
# Orange, Kings, Lake, Mono, Trinity, Glenn, and Alpine need the word County
# to count as the county (the set is defined with the other county names).
_COUNTY_REQUIRES_QUALIFIER = COUNTIES_NEEDING_QUALIFIER
# A bare county name followed by a place noun is part of a longer place name
# (Kings Canyon, Trinity Alps, Mono Basin, Lake Tahoe), not the county.
_COUNTY_PLACE_NOUN_AFTER = re.compile(
    r"\s+(?:canyon|river|creek|basin|alps|valley|peak|mountains?|forest|"
    r"national\s+(?:park|forest)|park|tahoe|lake|reservoir|island|beach|"
    r"pass|bay)\b",
    re.I,
)


def _city_match(lower: str):
    """A non-county city used as a place, or None.

    A name followed by County is a county phrase, not the city. Ambiguous
    names need a place cue such as "in Weed" or "Weed, California".
    """
    return next(_city_matches(lower), None)


def _city_matches(lower: str):
    for match in _CITY_NOT_COUNTY.finditer(lower):
        name = match.group(0).lower()
        after = lower[match.end() :]
        if re.match(r"\s+county\b", after):
            continue
        if name in _AMBIGUOUS_CITIES:
            if _CITY_CONTINUES.match(after):
                continue
            before = lower[: match.start()]
            cued = _CITY_CUE_BEFORE.search(before) or re.match(
                r",?\s*california\b", after
            )
            if not cued:
                continue
        yield match


def _city_named_as_county(lower: str) -> str | None:
    """A municipality written as if it were a county, such as Weed County."""
    for match in _CITY_NOT_COUNTY.finditer(lower):
        if re.match(r"\s+county\b", lower[match.end() :]):
            return match.group(0)
    return None


# A city center point answers "which territory or tier is this city in" and
# "fitted risk near this city on a past date". It cannot answer counts or
# lists in a city, a radius around it, or whether part of it is in a tier.
_CITY_POINT_RADIUS = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:km|mi|miles?|kilometers?|kilometres?)\b|"
    r"\bradius\b|\bwithin\b|\bmiles?\b|\bkilometers?\b"
)
_CITY_POINT_PARTIAL = re.compile(
    r"\b(?:parts?|portions?|partly|partially|entire(?:ly)?|whole|all\s+of|"
    r"much\s+of|percent(?:age)?|share\s+of|fraction|boundar\w*|city\s+limits|"
    r"overlap\w*|intersect\w*|straddl\w*|span\w*|split|"
    r"address(?:es)?|neighbou?rhoods?|streets?|parcels?|homes?|houses?|"
    r"residen\w*|propert(?:y|ies)|downtown|uptown|suburbs?|outskirts|"
    r"edge|east|west|north|south|eastern|western|northern|southern|"
    r"buildings?|blocks?|acreage|square\s+miles?|sq\.?\s*mi)\b"
)
_CITY_POINT_OTHER_INTENT = re.compile(
    r"\b(?:trend|time series|weekly|monthly|daily|compar\w*|versus|vs\.?|"
    r"history|historically|over time|since|between|each|every|per)\b"
)
_CITY_POINT_EVENT_NOUN = re.compile(
    r"\b(?:ignitions?|outages?|incidents?|events?|fires?|wildfires?|psps|epss|"
    r"circuits?|shutoffs?|de-?energi\w*|acres?|records?)\b"
)
# Phrases whose "fire" or "ignition" is not an event count.
_CITY_POINT_NOT_EVENTS = re.compile(
    r"\b(?:(?:ignition|fire|wildfire)\s+(?:risk|probability|intensity)|"
    r"probability\s+of\s+(?:an?\s+)?(?:ignition|fire|wildfire)|"
    r"(?:high\s+)?fire\s+threat(?:\s+districts?)?)\b"
)
# Territory containment, not service. The IOU layer covers cities that run
# their own municipal utility (Redding and Palo Alto fall in the PG&E polygon),
# so "which utility serves Redding" is not a point question.
_CITY_POINT_CONTEXT = re.compile(
    r"\b(?:iou|territor\w*|hftd|high\s+fire\s+threat|"
    r"fire\s+threat\s+district|tier\s*[123]?|grid\s+cell)\b"
)
_CITY_POINT_SERVICE = re.compile(
    r"\b(?:serv(?:e|es|ed|ing|ice|iced)|provider|provides?|supplie[sd]|"
    r"suppl(?:y|ies)|delivers?|power\s+company|electricity\s+(?:to|for))\b"
)
_NEAR = re.compile(r"\b(?:near|around|close\s+to|nearby)\b")


@dataclass(frozen=True)
class _CityPointPlan:
    kind: str  # "context" or "risk"
    point: CityPoint


def _city_point_plan(text: str, lower: str) -> _CityPointPlan | None:
    """A city question one city center point can answer, or None.

    None keeps the city_needs_place clarification: the city is not in the
    Gazetteer file, several cities or a county are named, or the question
    asks for counts, lists, maps, a radius, or part of a city.
    """
    matches = list(_city_matches(lower))
    if not matches:
        return None
    names = {match.group(0).lower() for match in matches}
    if len(names) != 1:
        return None
    name = next(iter(names))
    point = city_point(name) or _COUNTY_WORD_CDPS.get(name)
    if point is None:
        return None
    # Drop the city name first, so West Sacramento is not Sacramento County
    # and South Lake Tahoe is not "south".
    without_city = lower[: matches[0].start()] + " " + lower[matches[0].end() :]
    if (
        _coords(text) is not None
        or _counties(without_city)
        or _city_named_as_county(lower)
    ):
        return None
    if _CITY_POINT_RADIUS.search(lower) or _CITY_POINT_PARTIAL.search(without_city):
        return None
    if (
        _has_quantity_op(lower)
        or _has_list_op(lower)
        or _asks_map_view(lower)
        or _asks_ranking(lower)
        or _CITY_POINT_OTHER_INTENT.search(lower)
    ):
        return None
    if _CITY_POINT_EVENT_NOUN.search(_CITY_POINT_NOT_EVENTS.sub(" ", lower)):
        return None
    if _wants_risk(lower):
        return _CityPointPlan("risk", point)
    if _NEAR.search(lower):
        return None
    if _CITY_POINT_SERVICE.search(lower.replace("service territor", "territor")):
        return None
    if _CITY_POINT_CONTEXT.search(lower):
        return _CityPointPlan("context", point)
    return None


def city_point_for_question(question: str) -> CityPoint | None:
    """The city center point the router would use for this question, or None."""
    text = question.strip()
    plan = _city_point_plan(text, text.lower())
    return plan.point if plan else None


def _city_point_slot(point: CityPoint) -> dict[str, Any]:
    return {
        "name": point.name,
        "place_type": point.place_type,
        "geoid": point.geoid,
        "lat": point.lat,
        "lon": point.lon,
        "source": f"census_gazetteer_{GAZETTEER_VINTAGE}_internal_point",
    }


# Datasets whose warehouse tables expose a county column.
_COUNTY_CAPABLE_DATASETS = {
    "calfire_incidents",
    "cpuc_ignitions",
    "epss_outages",
    "psps_events",
    "circuits",
}

_VIZ_DATASET_NAME = {
    key: LAYER_VIZ_KEYS[key]
    for key in ("cpuc_ignitions", "epss_outages", "psps_events", "calfire_incidents")
}
_TIME_SERIES_VIZ = frozenset(
    {"ignitions", "us_ignitions", "epss", "psps", "calfire"}
)

UNSUPPORTED = {
    "cpz": r"\b(?:cpzs?|circuit protection zones?)\b",
    "cost": r"\b(?:cost|price|budget|dollars?|economic|premiums?)\b",
    "air_quality": r"\bair quality\b",
    "evacuation": r"\bevacuat",
    # Translate to a grid cell, incidents that involved firefighters, and a
    # satellite basemap are in scope, so these keys need their own context.
    "translation": (
        r"\btranslat\w*\b(?=.*\b(?:spanish|english|chinese|french|german|japanese|"
        r"korean|vietnamese|tagalog|languages?)\b)|"
        r"\b(?:spanish|english|chinese|french|german|japanese|korean|vietnamese|"
        r"tagalog)\b.*\btranslat\w*|"
        r"\btranslat\w*\s+(?:this|that|it|the\s+(?:answer|response|result))\b"
    ),
    "personnel": (
        r"\b(?:how many|number of|count of|total)\s+(?:\w+\s+){0,2}firefighters?\b|"
        r"\bfirefighters?\s+(?:were\s+|was\s+|are\s+)?(?:deployed|assigned|staffed|"
        r"dispatched|on scene)\b|\bpersonnel\b|\bhuman resources\b"
    ),
    "satellite": (
        r"\bsatellite\s+(?:\w+\s+)?(?:images?|imagery|infrared|photos?|pictures?|"
        r"views?|data|feeds?|detections?)\b|\b(?:infrared|thermal)\s+satellite\b"
    ),
    "leadership": r"\b(?:ceo|chief executive)\b",
    "optimization": r"\b(?:optimi[sz]e|optimal|schedule|allocate)\b",
    "damage": (
        r"\b(?:property damages?|expected loss(?:es)?|insured loss(?:es)?|fatalit(?:y|ies))\b"
    ),
    # Live wording (_LIVE_WEB_LIVE) or web wording (_LIVE_WEB_WEB), set below.
    "live_web": "",
}
_LIVE_WEB_LIVE = (
    r"\b(?:current active fires?|active fires?.*right now|live fires?|today'?s fires?)\b"
)
_LIVE_WEB_WEB = r"\b(?:web search|according to the web)\b"
UNSUPPORTED["live_web"] = f"{_LIVE_WEB_LIVE}|{_LIVE_WEB_WEB}"

# Hard safety rules versus topic judgments (issue #97). Live or real-time data
# and future prediction stay router backstops in every mode: an answer from the
# warehouse would present history as the present or as a forecast. Every other
# unsupported-topic keyword (cost, leadership, optimization, damage, CPZ, air
# quality, evacuation, translation, personnel, satellite, and web-search
# wording) is a judgment about what the question asks for, and a keyword also
# matches a passing mention ("budget meeting", "CEO testified"). In decide mode
# Jev's off_topic fact decides these; the keyword rule is the fallback when Jev
# is below the gate, errors, or is off. The advice rule (_asks_for_advice) is
# not a keyword and stays with the router: off_topic offers no advice option,
# so Jev cannot name it (docs/JEV_DECIDE.md).
TOPIC_JUDGMENT_KEYS = frozenset(key for key in UNSUPPORTED if key != "live_web")
TOPIC_JUDGMENT_RULES = frozenset(f"unsupported_{key}" for key in TOPIC_JUDGMENT_KEYS)

# Live wording. "current" counts only next to a live noun, "live" only next
# to fires, outages, or conditions, and none of it fires when the question
# names an explicit past year or date, except "right now".
_LIVE_NOW = re.compile(
    r"\btoday(?:'s)?\b|"
    r"\bcurrent(?:ly)?\s+(?:\w+\s+){0,2}?(?:risk|conditions?|outages?|fires?|"
    r"weather|status|situation|alerts?)\b|"
    r"\b(?:fires?|outages?|conditions?)\s+(?:are\s+|is\s+)?live\b|"
    r"\blive\s+(?:fires?|outages?|conditions?|status|feed|data)\b",
    re.I,
)
_RIGHT_NOW = re.compile(r"\bright now\b", re.I)


def _asks_live(lower: str) -> bool:
    if _RIGHT_NOW.search(lower):
        return True
    # An explicit year or date in the text makes today or current historical
    # ("up to today" from 2024). A resolved relative date does not count.
    if re.search(r"\b20\d{2}\b", lower):
        return False
    return bool(_LIVE_NOW.search(lower))


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
# Advice: a utility or the CPUC is the subject of should or recommend
# ("the CPUC should", "should PG&E", "recommend that SCE"), a strategy for a
# utility, or the CPUC penalizing one. "Should I use", "would you recommend
# for SCE", and "should I expect" are data questions.
_ADVICE_SUBJECT = (
    r"(?:the\s+)?(?:cpuc|commission|utilit(?:y|ies)|" + UTILITY_ADVICE_SUBJECT_WORDS + ")"
)
_ADVICE = re.compile(
    rf"\b{_ADVICE_SUBJECT}\b(?:\s+\w+){{0,3}}?\s+(?:should|ought\s+to|must)\b"
    r"(?!\s+(?:i|we|you|one)\b)|"
    rf"\b(?:should|must)\s+{_ADVICE_SUBJECT}\b|"
    rf"\brecommend(?:s|ed)?\s+(?:that\s+)?{_ADVICE_SUBJECT}\b|"
    rf"\b(?:best|optimal|right)\s+strateg(?:y|ies)\s+for\s+{_ADVICE_SUBJECT}\b|"
    rf"\bpenali[sz]e[sd]?\s+{_ADVICE_SUBJECT}\b|"
    rf"\b{_ADVICE_SUBJECT}\b(?:\s+\w+){{0,3}}?\s+penali[sz]e[sd]?\b",
    re.I,
)
# The asked object is modeled risk. Predict and forecast alone are not risk
# words here: a forecast of event counts is a prediction, not a risk score.
_RISK_OBJECT = re.compile(
    r"\b(?:risk|risky|riskiness|hotspots?|ignition probability|"
    r"probability of ignition)\b",
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
        "circuits in EPSS outages, one dataset at a time. I cannot rank "
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
        rf"\b(?:how many|count|number of|list|records?|outages?|incidents?|{EVENT_DATASET_WORDS})\b",
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


_COUNTY_PLACE_CUE = re.compile(
    r"\b(?:in|at|near|around|for|within|inside|across|throughout|from)\s+$", re.I
)


def _qualified_counties(lower: str) -> list[str]:
    """Counties named with the word County, or in an "X and Y counties" list."""
    found: list[str] = []
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name.lower())}\s+county\b", lower):
            found.append(name)
    # "Lake and Napa counties": the plural qualifies every name before it.
    for match in re.finditer(r"\bcounties\b", lower):
        window = lower[max(0, match.start() - 80) : match.start()]
        for name in sorted(_CA_COUNTIES, key=len, reverse=True):
            if name not in found and re.search(rf"\b{re.escape(name.lower())}\b", window):
                found.append(name)
    return found


def _unresolved_county_place(text: str) -> str | None:
    """A county word used as a different place, with no qualified county.

    "Napa Valley", "Kern River", and "Shasta Lake" are places, not the county
    of the same name. "In Trinity" without the word County is ambiguous. With
    no county qualified anywhere in the question, ask which county was meant
    instead of answering statewide or for a county the user did not name.
    """
    lower = " ".join(text.lower().split())
    if _qualified_counties(lower):
        return None
    scrubbed = lower
    for pattern in UTILITY_PATTERNS.values():
        scrubbed = re.sub(pattern, " ", scrubbed, flags=re.I)
    scrubbed = " ".join(scrubbed.split())
    # A Census place whose name holds a county word (Lake Forest, Kings Beach,
    # Shasta Lake) is that place, not the county.
    if _COUNTY_WORD_PLACE_RE is not None:
        scrubbed = _COUNTY_WORD_PLACE_RE.sub(" ", scrubbed)
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        for match in re.finditer(rf"\b{re.escape(name.lower())}\b", scrubbed):
            after = scrubbed[match.end() :]
            noun = _COUNTY_PLACE_NOUN_AFTER.match(after)
            if noun:
                return (name.lower() + noun.group(0)).strip()
            if name.lower() in _COUNTY_REQUIRES_QUALIFIER and _COUNTY_PLACE_CUE.search(
                scrubbed[: match.start()]
            ):
                return name.lower()
    return None


def _counties(text: str) -> list[str]:
    """Every county named in the question, not just the first.

    _county derives the single slot from this list, so the two always agree.
    """
    lower = " ".join(text.lower().split())
    found = _qualified_counties(lower)
    # Keep scanning bare names: "Napa and Sonoma County" names two counties.
    if re.search(r"\b(?:near|around|close to)\b", lower):
        return found
    scrubbed = lower
    for pattern in UTILITY_PATTERNS.values():
        scrubbed = re.sub(pattern, " ", scrubbed, flags=re.I)
    scrubbed = " ".join(scrubbed.split())
    for name in sorted(_CA_COUNTIES, key=len, reverse=True):
        if name.lower() in _COUNTY_REQUIRES_QUALIFIER:
            continue
        if name in found:
            continue
        for match in re.finditer(rf"\b{re.escape(name.lower())}\b", scrubbed):
            if _COUNTY_PLACE_NOUN_AFTER.match(scrubbed[match.end():]):
                continue
            found.append(name)
            break
    return found


_PER_PERIOD_ASK = re.compile(
    r"\b(?:which|what)\s+(?:year|month)s?\b|"
    r"\b(?:highest|lowest|most|fewest|peak|busiest)\s+(?:\w+\s+){0,3}?(?:year|month)s?\b",
    re.I,
)
_YEAR_RANGE = re.compile(
    r"\b(20\d{2})\s*(?:to|through|until|-|\u2013)\s*(20\d{2})\b|"
    r"\bbetween\s+(20\d{2})\s+and\s+(20\d{2})\b",
    re.I,
)


def _enumerated_months(lower: str) -> bool:
    """True when several calendar months are named apart from one month range.

    "July 2024 and August 2024" is two windows; "July to August 2024" is one.
    A single count over both would silently merge the months.
    """
    if len(named_months(lower)) <= 1:
        return False
    return explicit_month_range_in_year(lower) is None and explicit_month_year_range(lower) is None


def _enumerated_years(lower: str) -> bool:
    """True when the named years are not exactly one matched range.

    "from 2018 to 2020" is one window. More than two named years, or a named
    year outside every matched range ("from 2018 to 2020 and in 2022"), is an
    enumeration that one windowed call would silently drop.
    """
    named = sorted({int(item) for item in re.findall(r"\b20\d{2}\b", lower)})
    if len(named) <= 1:
        return False
    if len(named) > 2:
        return True
    ranges = []
    for match in _YEAR_RANGE.finditer(lower):
        start = int(match.group(1) or match.group(3))
        end = int(match.group(2) or match.group(4))
        ranges.append((min(start, end), max(start, end)))
    if not ranges:
        return True
    return any(not any(lo <= year <= hi for lo, hi in ranges) for year in named)


# A series asked per year: one interval series would collapse the per-year totals.
_PER_YEAR_SERIES = re.compile(
    r"\bannual(?:ly)?\b|\bper\s+years?\b|\byear[- ]by[- ]year\b|\beach\s+years?\b"
)
# Interval words of a series ("by month"). Removed before the breakdown check
# on a chart plus its total, since they name the chart's step, not a breakdown.
_INTERVAL_WORDS = re.compile(r"\b(?:by|per)\s+(?:month|week|day)s?\b")


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
    # "from 2018 to 2020" is one window with start and end dates. Enumerated
    # years, a range plus another year, or a breakdown word defer.
    enumerated = _enumerated_years(lower) or _enumerated_months(lower)
    # "Which year had the most" over a range is a per-year breakdown.
    per_period = _PER_PERIOD_ASK.search(lower) and len(named_years) > 1
    if kind == "count" and (enumerated or per_period or _BREAKDOWN.search(lower)):
        return True
    if kind == "series" and _PER_YEAR_SERIES.search(lower):
        return True
    # A chart plus a total is two results; one series call would drop the total.
    if kind == "series" and _asks_series_and_total(lower):
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
    """The one county the question names, from the same scan as _counties.

    Several named counties give None, so no single-county hint is ever built
    from a list.
    """
    counties = _counties(text)
    return counties[0] if len(counties) == 1 else None


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
    if "monthly" in lower or re.search(r"\bby month\b|\bper month\b", lower):
        return "monthly"
    if "daily" in lower or re.search(r"\bby day\b|\bper day\b", lower):
        return "daily"
    if "weekly" in lower or re.search(r"\bby week\b|\bper week\b", lower):
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
    epss_utility = next(
        (
            str(args.get("utility"))
            for _tool, args in tool_calls
            if args.get("dataset") in {"epss_outages", "epss"}
            and args.get("utility")
            and str(args.get("utility")) != "PGE"
        ),
        None,
    )
    if epss_utility:
        # EPSS rows exist only for PG&E. Another utility's EPSS read would come
        # back as 0 or an empty series, which is absent data, not zero events.
        return RouteDecision(
            "clarification",
            "epss_non_pge_utility",
            f"EPSS is PG&E-only; {epss_utility} has no EPSS rows",
            answer=(
                f"EPSS outages in this warehouse are PG&E-only, so there are no "
                f"{epss_utility} EPSS rows: that result would be absent, not zero. "
                f"Do you want PG&E's EPSS outages for that period, or "
                f"{epss_utility}'s PSPS events or CPUC ignitions instead?"
            ),
            slots=slots,
        )
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
    today = date.today()
    data_max = today.year
    ahead = [year for year in years if year > data_max]
    if ahead:
        return str(max(ahead))
    if not _FUTURE_DATE.search(lower):
        return None
    # Bare "will" is not a forward token: "Will you show me a map of 2024"
    # asks about a covered year.
    forward = re.search(
        r"\b(?:upcoming|this\s+fall|tomorrow|next\s+(?:year|summer|month)|"
        r"future\s+years?)\b",
        lower,
    )
    if _PAST_FORECAST.search(lower) and not forward:
        return None
    # Every year or date in the text is inside completed coverage: a bare
    # mention of the current year is not, a full date on or before today is.
    iso_dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    iso_ok = True
    for item in iso_dates:
        try:
            iso_ok = iso_ok and date.fromisoformat(item) <= today
        except ValueError:
            iso_ok = False
    bare_current_year = re.search(rf"(?<![\d-]){data_max}(?![\d-])", text)
    covered = (
        bool(years)
        and all(DATA_YEAR_MIN <= year <= data_max for year in years)
        and not bare_current_year
        and iso_ok
    )
    if covered and not forward:
        return None
    if re.search(r"\bexpected\s+value\b", lower) and covered and not forward:
        return None
    if re.search(r"\bhistorical\b", lower) and covered and not forward:
        return None
    match = _FUTURE_DATE.search(lower)
    return match.group(0) if match else None


# Passive and object forms: a recommended strategy, penalties for a utility,
# something required of a utility, whether it is advisable for a utility.
_ADVICE_OBJECT = re.compile(
    rf"\brecommend(?:s|ed|ation)?\b(?:\s+\w+){{0,4}}?\s+strateg(?:y|ies)\b|"
    rf"\bpenalt(?:y|ies)\b(?:\s+\w+){{0,6}}?\s+(?:for|to|on|against)\s+{_ADVICE_SUBJECT}\b|"
    rf"\bshould\s+apply\s+to\s+{_ADVICE_SUBJECT}\b|"
    rf"\bshould\b(?:\s+\w+){{1,6}}?\s+(?:be\s+)?(?:required|mandated|imposed|ordered|expected)"
    rf"\s+(?:of|for|on|by|from)\s+{_ADVICE_SUBJECT}\b|"
    rf"\b(?:advisable|wise|prudent|appropriate)\s+for\s+{_ADVICE_SUBJECT}\b",
    re.I,
)
_ADVICE_ANY_SUBJECT = re.compile(rf"\b{_ADVICE_SUBJECT}\b", re.I)


def _asks_for_advice(text: str) -> bool:
    """What a utility or the CPUC should do. Quoted 'should' does not count.

    The utility or the CPUC must be the subject of should or recommend, or
    the object of a recommended strategy, a penalty, a requirement, or an
    advisability question. A question where the analyst is the subject
    ("should I use", "would you recommend for SCE") is a data question.
    """
    bare = _strip_quotes(text)
    if _ADVICE.search(bare):
        return True
    return bool(_ADVICE_OBJECT.search(bare) and _ADVICE_ANY_SUBJECT.search(bare))


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


# Event datasets that can sit next to bare "ignitions" as a second dataset.
# Context layers (HFTD, circuits, territories) do not make ignitions a list.
_EVENT_DATASETS_BESIDE_IGNITIONS = frozenset(
    {"epss_outages", "psps_events", "calfire_incidents"}
)
_IGNITION_QUALIFIER = IGNITION_QUALIFIER_PATTERN
# US-sample wording (FireCastRL) and the CPUC or utility words that override it.
_US_SAMPLE_NAMED = US_SAMPLE_NAMED_PATTERN
_SAMPLE_BEFORE_IGNITIONS = SAMPLE_BEFORE_IGNITIONS_PATTERN
_ALL_CAUSES_BEFORE_IGNITIONS = ALL_CAUSES_BEFORE_IGNITIONS_PATTERN
_ALL_CAUSES_AFTER_IGNITIONS = ALL_CAUSES_AFTER_IGNITIONS_PATTERN
_CPUC_OR_UTILITY_BEFORE_IGNITIONS = CPUC_OR_UTILITY_BEFORE_IGNITIONS_PATTERN


def _names_us_sample(text: str) -> bool:
    """True when the question names the US ignitions sample.

    Sample and all-causes wording counts only beside "ignitions", and not when
    CPUC or a utility names whose ignitions they are.
    """
    lower = " ".join(text.lower().split())
    if _US_SAMPLE_NAMED.search(lower):
        return True
    for match in re.finditer(r"\bignitions?\b", lower):
        before, after = lower[: match.start()], lower[match.end() :]
        if _CPUC_OR_UTILITY_BEFORE_IGNITIONS.search(before):
            continue
        if (
            _SAMPLE_BEFORE_IGNITIONS.search(before)
            or _ALL_CAUSES_BEFORE_IGNITIONS.search(before)
            or _ALL_CAUSES_AFTER_IGNITIONS.search(after)
        ):
            return True
    return False
_US_STATE_NAME = re.compile(
    r"\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new\s+hampshire|new\s+jersey|"
    r"new\s+mexico|new\s+york|north\s+carolina|north\s+dakota|ohio|oklahoma|"
    r"oregon|pennsylvania|rhode\s+island|south\s+carolina|south\s+dakota|"
    r"tennessee|texas|utah|vermont|virginia|washington|west\s+virginia|"
    r"wisconsin|wyoming)\b",
    re.I,
)


def _has_bare_ignitions(text: str) -> bool:
    """True when some "ignitions" is not qualified as US, CAL FIRE, EPSS, or PSPS."""
    return any(
        not _IGNITION_QUALIFIER.search(text[: match.start()])
        for match in re.finditer(r"\bignitions?\b", text, re.I)
    )


def _datasets(text: str) -> list[str]:
    candidates: list[str] = ["us_ignitions"] if _names_us_sample(text) else []
    for key, pattern in DATASET_QUESTION_PATTERNS:
        if re.search(pattern, text, re.I):
            candidates.append(key)
    if not candidates and re.search(BARE_IGNITIONS_PATTERN, text, re.I):
        candidates.append("cpuc_ignitions")
    elif (
        "cpuc_ignitions" not in candidates
        and set(candidates) & _EVENT_DATASETS_BESIDE_IGNITIONS
        and _has_bare_ignitions(text)
    ):
        # "ignitions and EPSS outages" names two datasets; bare ignitions are CPUC.
        candidates.append("cpuc_ignitions")
    # Bare "outages" (without PSPS/EPSS) is treated as EPSS for map/count routing.
    if not candidates and re.search(BARE_OUTAGES_PATTERN, text, re.I):
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


# The ranking phrase itself names a change: largest increase, biggest drop,
# most growth, grew the most. A change word elsewhere in the sentence
# ("after the fast-trip changes") is not a change ranking.
_CHANGE_OVER_TIME = re.compile(
    r"\b(?:largest|biggest|greatest|highest|most|smallest|lowest|least|sharpest|"
    r"fastest|steepest)\s+(?:\w+\s+)?(?:increases?|decreases?|drops?|changes?|"
    r"growth|declines?|rises?|gains?|jumps?|falls?|reductions?|deltas?|"
    r"differences?)\b|"
    r"\b(?:grew|increased|decreased|dropped|declined|rose|fell|changed)\s+"
    r"(?:the\s+)?(?:most|least|fastest)\b",
    re.I,
)


def _asks_medical_exposure(lower: str) -> bool:
    """True for the EPSS medical-baseline / life-support panel, not a generic outage ask."""
    return bool(
        re.search(
            r"\b(?:medical\s+baseline|life[\s-]support|medically\s+vulnerable)\b",
            lower,
        )
    )


def _asks_summary_panel(lower: str) -> bool:
    """True for a multi-metric summary, not a single how-many total."""
    return bool(re.search(r"\b(?:summary|overview) of\b", lower))


# A series or chart request that also asks for a total. One series call would
# drop the total. With one known dataset and window it takes the deterministic
# count-plus-series pair (multi_intent_count_and_trend, issue 44); otherwise it
# defers as the count path does.
_SERIES_WORD = re.compile(
    r"\b(?:chart|plot|graph|series|trend|over time|monthly|weekly|daily|"
    r"by month|by week|by day)\b",
    re.I,
)
_TOTAL_ASK = re.compile(
    r"\bannual\s+totals?\b|\boverall\s+(?:count|total|number)\b|"
    r"\btotal\s+(?:count|number)\b|\band\s+how\s+many\b|"
    r"\b(?:plus|include|including|as well as|along with)\s+(?:the\s+|an?\s+)?"
    r"(?:\w+\s+)?(?:total|count|sum)\b",
    re.I,
)


def _asks_series_and_total(lower: str) -> bool:
    return bool(_SERIES_WORD.search(lower) and _TOTAL_ASK.search(lower))


def _series_mode_request(lower: str) -> tuple[str, str | None] | None:
    """Map a series-panel phrase to (series_mode, fixed warehouse dataset).

    A None dataset means the question must name CPUC, EPSS, or CAL FIRE.
    """
    if re.search(r"\bcumulative acres\b|\bacres burned over the year\b", lower):
        return ("cumulative_acres", "calfire_incidents")
    if re.search(r"\bcustomer events?\b|\bcustomers affected\b", lower):
        return ("customer_events", "psps_events")
    if re.search(r"\bepss\b", lower) and re.search(r"\bby region\b", lower):
        return ("regional", "epss_outages")
    if re.search(r"\bby division\b", lower) and re.search(
        r"\b(?:epss|outages?)\b", lower
    ):
        return ("regional", "epss_outages")
    if re.search(r"\byear over year\b|\bannual totals?\b", lower):
        return ("yearly", None)
    if re.search(r"\bseasonal\b|\bby month of year\b", lower):
        return ("seasonal", None)
    return None


def _asks_hdw(lower: str) -> bool:
    """True for the Hot-Dry-Windy playback overlay, the only weather layer on the map."""
    return bool(
        re.search(r"\bhdw\b|\bhot[\s-]+dry[\s-]+windy\b|\bfire[\s-]weather\b", lower)
    )


def _asks_residual(lower: str) -> bool:
    """Observed minus modeled ignitions on the cNHPP grid."""
    return bool(
        re.search(
            r"\bresiduals?\b|\b(?:observed|actual)\s+(?:vs\.?|versus)\s+"
            r"(?:expected|predicted|modell?ed)\b",
            lower,
        )
    )


def _risk_map_mode(lower: str) -> str | None:
    """Grid map for a risk question: the residual map or the risk surface."""
    if _asks_residual(lower):
        return "residual"
    # Bare "where" is not enough here; it reads as advice as often as a map ask.
    if re.search(r"\b(?:surface|maps?|mapped|heat\s*map)\b", lower):
        return "risk"
    return None


_HDW_EVENT_DATASETS = frozenset(
    {"cpuc_ignitions", "epss_outages", "psps_events", "calfire_incidents"}
)
# Datasets the workspace timeline can overlay on one axis.
_TIMELINE_DATASETS = ("cpuc_ignitions", "epss_outages", "calfire_incidents")


def _timeline_datasets(text: str, lower: str) -> list[str] | None:
    """Two or more chartable datasets named in one trend ask, else None."""
    if not re.search(r"\b(?:trends?|time series|timeline|over time)\b", lower):
        return None
    named = _datasets(text)
    if len(named) < 2 or any(item not in _TIMELINE_DATASETS for item in named):
        return None
    return named


def _asks_ranking(lower: str) -> bool:
    return bool(
        re.search(
            r"\brank(?:s|ed|ing)?\b|"
            r"\b(?:circuit|count(?:y|ies)|utilit(?:y|ies)|states?|division|cell)s?\s+"
            r"with\s+the\s+(?:most|highest|largest|greatest|biggest)\b|"
            r"\b(?:which|what)\s+(?:circuit|count(?:y|ies)|utilit(?:y|ies)|states?|"
            r"division|cell)s?\b.{0,60}\b(?:most|highest|largest|greatest|biggest|top)\b|"
            r"\bhad\s+the\s+(?:most|highest|largest|greatest|biggest)\b|"
            r"\b(?:most|highest|largest|biggest)\s+(?:\w+\s+){0,4}"
            r"(?:outages?|ignitions?|incidents?|fires?|acres)\b|"
            r"\btop\s+\d+\s+(?:circuit|count(?:y|ies)|utilit|states?)",
            lower,
        )
    )


_TIER_MENTION = TIER_MENTION_PATTERN


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
        # Label rule G: a utility service-area outline, boundary, polygon, or
        # footprint with no count and no dataset word is the territory map.
        return bool(
            re.search(r"\b(?:service[- ]area|outline|boundary|polygon|footprint)\b", lower)
            and not _has_quantity_op(lower)
            and not re.search(
                r"\b(?:ignitions?|outages?|incidents?|events?|"
                + EVENT_DATASET_WORDS
                + r"|risk|compare|versus|trend|time series)\b",
                lower,
            )
        )
    if _has_quantity_op(lower):
        return False
    if re.search(r"\b(?:compare|versus|\bvs\.?\b|trend|time series|forecast|predict)\b", lower):
        return False
    if _asks_map_view(lower) and re.search(
        rf"\b(?:ignitions?|outages?|incidents?|{EVENT_DATASET_WORDS})\b", lower
    ):
        return False
    # Boundary asks: "territory map", "show ... territory", "what is the
    # territory", or a bare territory with no dataset word. A boundary noun
    # (polygon, outline, service area) only ever appears here next to the
    # word territory, and those questions already match the rules below, so
    # a separate noun check was dead code (issue 32). Service-area phrasing
    # without the word territory never reaches this function.
    if re.search(r"\b(?:map|show|display|draw)\b.*\bterritor|\bterritor\w*\b.*\b(?:map|layer)\b", lower):
        return True
    # "What is the SCE territory?" / "SCE utility territory"
    if re.search(r"\b(?:utility|iou)\s+territor|\bterritor\w*\s+for\b", lower):
        return True
    if re.search(r"\b(?:what|where)\b.*\bterritor", lower):
        return True
    # Bare "... territory" with a utility and no other dataset/operation.
    if not re.search(
        rf"\b(?:ignitions?|outages?|incidents?|{EVENT_DATASET_WORDS}|risk)\b", lower
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
        dataset_name = LAYER_VIZ_KEYS.get(dataset, "ignitions")
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


_DATASET_LABELS = {
    key: STAT_LABELS[key]
    for key in (
        "cpuc_ignitions",
        "calfire_incidents",
        "epss_outages",
        "psps_events",
        "us_ignitions",
    )
}


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

    # "Largest increase" ranks a change between periods. A count rank would
    # silently answer a different metric, so refuse it.
    if _CHANGE_OVER_TIME.search(lower):
        return RouteDecision(
            "unsupported",
            "unsupported_ranking",
            "Ranking by change over time is not available",
            answer=(
                "Ranking by change over time is not supported. I can rank "
                "counties, utilities, or circuits by a count or acres for one "
                "year or date range, or compare two periods for one place. "
                "Which do you want?"
            ),
            slots=slots,
        )

    group_by = _rank_dimension(lower)
    named = _datasets(text)
    # "Tier 3 circuits" names the HFTD constraint, not the circuits table and
    # not the grouping, so a tier ranking keeps its real dataset and dimension.
    tier_constraint = bool(_TIER_MENTION.search(lower))
    if tier_constraint:
        # "Tier 3 circuits" and "HFTD Tier 2 areas" name the constraint, not
        # the circuits or hftd tables.
        named = [item for item in named if item not in {"circuits", "hftd"}]
        if group_by is None:
            group_by = _rank_dimension(re.sub(r"\bcircuits?\b", " ", lower))
        # The router's dataset came from the same scan, so hftd or circuits
        # there is the constraint too and the dataset is unresolved. With no
        # other dataset named, resolve the rest of the question as a ranking
        # without the tier would: bare "ignitions" is CPUC ignitions and bare
        # "outages" is EPSS (issue 67). Nothing resolved asks for the dataset.
        if dataset in {"circuits", "hftd"}:
            dataset = None
        if not named:
            without_tier = re.sub(r"\bcircuits?\b", " ", _TIER_MENTION.sub(" ", text))
            named = _datasets(without_tier)
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
            answer=rank_slots_question(),
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

    # An allowed ranking restricted to an HFTD tier: no ranking tool takes a
    # tier argument, so ask rather than rank statewide and drop it.
    if tier_constraint:
        label = _DATASET_LABELS.get(dataset, dataset)
        return RouteDecision(
            "clarification",
            "hftd_constraint_unavailable",
            "No tool restricts a ranking to an HFTD tier",
            answer=(
                f"I can rank {label} by {group_by} statewide, but no tool "
                "restricts a ranking to an HFTD tier. Rank statewide, or map "
                "one HFTD tier?"
            ),
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


def _city_point_route(
    plan: _CityPointPlan,
    *,
    text: str,
    time_resolution,
    slots: dict[str, Any],
) -> RouteDecision:
    """Spatial context or the spatial to risk chain at a city center point."""
    # A city center can sit just off a mapped shoreline (Albany is 3.5 m
    # outside PG&E's polygon), so city points ask for the shoreline snap.
    point_call = (
        "data_query_spatial",
        {
            "kind": "point",
            "lat": plan.point.lat,
            "lon": plan.point.lon,
            "snap_shoreline": True,
        },
    )
    if plan.kind == "context":
        return RouteDecision(
            "deterministic",
            "city_point_context",
            f"{plan.point.name} resolved to its Census Gazetteer internal point",
            tool_calls=[point_call],
            slots=slots,
        )
    lower = text.lower()
    on_date = _single_risk_date(text, time_resolution)
    if (
        _forward_relative_phrase(lower)
        or _date_after_risk_coverage(on_date)
        or not on_date
    ):
        return _risk_date_clarification(
            text=text,
            time_resolution=time_resolution,
            reason="City forecast requires a scoreable past date",
            slots=slots,
        )
    return RouteDecision(
        "deterministic",
        "city_point_risk_chain",
        f"{plan.point.name} resolved to its Census Gazetteer internal point, "
        "and a date fully specifies the spatial to risk chain",
        tool_calls=[
            point_call,
            # cell_id is resolved from the first result by the orchestrator.
            ("risk_forecast", {"cell_id": "$grid_cell_id", "date": on_date}),
        ],
        slots=slots,
    )


def route_question(
    question: str, *, force_model: bool = False, skip_topic_judgments: bool = False
) -> RouteDecision:
    """Route one question. A clarification asks for every missing item at once.

    The rule and path come from _route_question alone; only the clarification
    text is completed, from the same slots.

    skip_topic_judgments: route as if no topic-judgment keyword matched (the
    TOPIC_JUDGMENT_KEYS patterns and web-search wording). Decide mode uses it
    when Jev reads the question as on topic. Live wording, future prediction,
    and the advice rule still refuse.
    """
    decision = _route_question(
        question, force_model=force_model, skip_topic_judgments=skip_topic_judgments
    )
    if decision.path == "clarification":
        decision.answer = complete_clarification(
            decision.rule,
            " ".join(question.strip().split()),
            decision.slots,
            decision.answer,
        )
    return decision


# Risk model performance: a question about how well the fitted risk model
# scores, answered from the persisted HPP, NHPP, and cNHPP evaluation. The
# evaluation is statewide on one held-out year, so the route fires only when
# the question names no place, cell, tier, or time (see _route_question);
# otherwise it falls through as before rather than dropping the constraint.
_MODEL_NAMES = re.compile(r"\b(c?nhpp|hpp)\b")
_RISK_MODEL_SUBJECT = re.compile(
    r"\b(?:c?nhpp|hpp"
    r"|(?:(?:fire|wildfire|ignition)\s+)?risk\s+model"
    r"|ignition\s+model|fitted\s+model|hindcast\s+model|point[- ]process\s+model"
    r"|(?:risk\s+)?forecast(?:ing)?\s+model)s?\b"
)
_MODEL_PERFORMANCE = re.compile(
    r"\b(?:accura\w*|perform\w*|metrics?|evaluat\w*|auc|roc"
    r"|log[- ]?likelihood|precision|lift|validat\w*|skill|goodness\s+of\s+fit"
    r"|how\s+(?:good|well|reliable|trustworthy)|reliab\w*)\b"
)
_MODEL_PERFORMANCE_BLOCKERS = re.compile(r"\b(?:cells?|grid|tiers?|hftd|circuits?|territor\w*)\b")


# "the model predict", "the model's predictions": a bare "model" is the risk
# model only beside a predict word (elsewhere it can mean the chat model).
_PREDICT_WORD = re.compile(r"\bpredict(?:ed|ions?|ing|s)?\b")
_BARE_MODEL = re.compile(r"\b(?:the|this|your)\s+model(?:'s|s)?\b")


def _asks_model_performance(lower: str) -> bool:
    if _MODEL_PERFORMANCE_BLOCKERS.search(lower):
        return False
    if len(set(_MODEL_NAMES.findall(lower))) >= 2:
        return True
    if not _MODEL_PERFORMANCE.search(lower):
        return False
    if _RISK_MODEL_SUBJECT.search(lower):
        return True
    return bool(_PREDICT_WORD.search(lower) and _BARE_MODEL.search(lower))


def _predict_word_is_model_skill(text: str, lower: str) -> bool:
    """True when the only forward token is a predict word in a question about
    how well the model predicts: no forward phrase, will, forecast, or year
    after coverage. Such a question is about skill, not a future prediction.
    """
    if any(int(year) > date.today().year for year in re.findall(r"\b(20\d{2})\b", text)):
        return False
    tokens = [match.group(0) for match in _FUTURE_DATE.finditer(lower)]
    return (
        bool(tokens)
        and all(_PREDICT_WORD.fullmatch(token) for token in tokens)
        and _asks_model_performance(lower)
    )


def _route_question(
    question: str, *, force_model: bool = False, skip_topic_judgments: bool = False
) -> RouteDecision:
    text = " ".join(question.strip().split())
    lower = text.lower()
    utilities = _utilities(text)
    time_resolution = resolve_time(text)
    year = time_resolution.year
    years = list(time_resolution.years)
    dataset = _dataset(text)
    coords = _coords(text)
    counties = _counties(text)
    # Several named counties never collapse to one hint.
    county = _county(text) if len(counties) <= 1 else None
    slots = {
        "utilities": utilities,
        "year": year,
        "years": years,
        "dataset": dataset,
        "coords": coords,
        "county": county,
        "counties": counties,
        "time_resolution": time_resolution.as_slot(),
        "start_date": time_resolution.start_date,
        "end_date": time_resolution.end_date,
    }

    for key, pattern in UNSUPPORTED.items():
        if skip_topic_judgments:
            if key in TOPIC_JUDGMENT_KEYS:
                continue
            if key == "live_web":
                pattern = _LIVE_WEB_LIVE
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

    if _asks_live(lower):
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
    if future_phrase and _predict_word_is_model_skill(text, lower):
        future_phrase = None
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
                "Which risk measure and time period should I use, for example "
                "ignition count, CAL FIRE incidents, EPSS outages, or fitted cell risk?"
            ),
        )
    city_plan = _city_point_plan(text, lower)
    if city_plan is not None:
        slots["city_point"] = _city_point_slot(city_plan.point)
        # A county name inside the city name (West Sacramento) is not a county.
        slots["county"] = None
    if (
        not force_model
        and city_plan is None
        and _asks_model_performance(lower)
        and not utilities
        and not counties
        and not coords
        and time_resolution.status == "none"
        and dataset in (None, "cpuc_ignitions")
    ):
        return RouteDecision(
            "deterministic",
            "risk_model_metrics",
            "Risk model performance is the persisted statewide evaluation (GET /metrics)",
            tool_calls=[("risk_metrics", {})],
            slots={**slots, "stat_mode": "model_metrics"},
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
        and city_plan is None
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
    county_place = _unresolved_county_place(text)
    if county_place:
        county_word = county_place.split()[0].title()
        return RouteDecision(
            "clarification",
            "county_place_ambiguous",
            "A county word is used as a different place, or without the word County",
            slots=slots,
            answer=(
                f"{county_place.title()} is not a county filter in this warehouse. "
                f"Did you mean {county_word} County, or another county?"
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
    # Coordinates are the place; a city name beside them is a label.
    city = _city_match(lower) if coords is None else None
    if city and city_plan is None:
        return RouteDecision(
            "clarification",
            "city_needs_place",
            "A city that is not a county name is not a query layer",
            slots=slots,
            answer=(
                f"{city.group(0).title()} is "
                + (
                    "a community (census designated place)"
                    if city.group(0).lower() in _COUNTY_WORD_CDPS
                    else "a city"
                )
                + ", not a county or utility "
                "territory. Which coordinates, county, or utility territory "
                "should I use? I will not answer with a statewide or county layer."
            ),
        )
    # A ranking that mentions a tier reaches _route_ranking first, where an
    # unsupported ranking is refused before the tier constraint is considered.
    # A city point asks which tier one point is in, not a tier measurement.
    if (
        city_plan is None
        and _hftd_constraint_unavailable(lower)
        and not _asks_ranking(lower)
    ):
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

    if _asks_medical_exposure(lower):
        time_args = _time_filter_args(time_resolution)
        medical_slots = {
            **slots,
            "dataset": "epss",
            "stat_mode": "medical_exposure",
            "view_id": "medical-exposure",
        }
        if not time_args:
            return RouteDecision(
                "clarification",
                "medical_exposure_missing_year",
                "Medical exposure panel lacks a time period",
                answer="What year or date range should I use?",
                slots=medical_slots,
            )
        args: dict[str, Any] = {
            "dataset": "epss_outages",
            "result_mode": "count",
            **time_args,
        }
        if utilities:
            args["utility"] = utilities[0]
        if county:
            args["county"] = county
        tool_calls = [("data_query_records", args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=tool_calls,
            slots=medical_slots,
            rule="medical_exposure",
            reason="Medical baseline or life support asks for the EPSS exposure panel",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "medical_exposure",
            "Medical baseline or life support asks for the EPSS exposure panel",
            tool_calls=tool_calls,
            slots=medical_slots,
        )

    if force_model:
        return RouteDecision(
            "model",
            "forced_eval",
            "Evaluation case forces model tier",
            slots=slots,
        )

    if city_plan is not None:
        return _city_point_route(
            city_plan, text=text, time_resolution=time_resolution, slots=slots
        )

    # Label rules F and H: the US ignitions sample has no state column, so a
    # US-sample question restricted to a state clarifies rather than answering
    # with the national count. This holds when the sample is one of several
    # datasets named ("the sampled California ignitions inside HFTD").
    if dataset == "us_ignitions" or "us_ignitions" in _datasets(text):
        state = _US_STATE_NAME.search(lower)
        if state:
            name = " ".join(word.capitalize() for word in state.group(0).split())
            limit = (
                f"The US ignitions sample has no state or county column, so I "
                f"cannot count only the {name} events."
            )
            if "cpuc_ignitions" in _datasets(text):
                # The question compares CPUC with the sample: offer both sides.
                offer = (
                    " I can put CPUC's California utility-ignition count beside "
                    "the national sample count for the same period. They count "
                    "different things (utility-reported ignitions against an "
                    "all-cause national sample) and are not directly comparable. "
                    "Should I do that?"
                )
            else:
                offer = (
                    " I can give the national sample count for the same period, "
                    "or a CPUC utility-ignition or CAL FIRE incident count for "
                    "California. Which should I use?"
                )
            return RouteDecision(
                "clarification",
                "unexpressable_county_filter",
                f"Question restricts the US ignitions sample to {name}, which it cannot filter by",
                answer=limit + offer,
                slots=slots,
            )

    # Label rule J: the US sample has no utility column either, so a US-sample
    # question restricted to a utility clarifies on every route (map, count,
    # series, rank) rather than passing the utility to a tool that cannot apply
    # it or dropping it and answering with the national count.
    if utilities and (dataset == "us_ignitions" or "us_ignitions" in _datasets(text)):
        name = UTILITY_CLARIFY_LABELS.get(utilities[0], utilities[0])
        return RouteDecision(
            "clarification",
            "us_sample_utility_filter",
            f"Question restricts the US ignitions sample to {name}, which it cannot filter by",
            answer=(
                f"The US ignitions sample has no utility column, so I cannot count "
                f"or map only the {name} events in it. I can give the national "
                f"sample for the same period, or {name}'s CPUC utility ignitions. "
                f"They count different things (an all-cause national sample "
                f"against utility-reported ignitions). Which should I use?"
            ),
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
    explicit_pair = has_count_clause and has_trend_clause
    if explicit_pair and (len(utilities) > 1 or len(counties) > 1):
        # One pair carries one utility and one county; a second named one
        # would be dropped from both calls.
        return _defer_collapsed(slots)
    # A chart plus a total ("plus the annual total", "and how many overall") is
    # the same two results. It takes the pair only when one dataset, at most one
    # utility and county, and one window are known and nothing asks for a
    # breakdown; otherwise the series branch defers it below (issue 44). The
    # breakdown and per-year checks run with the total ask and the interval
    # words removed, so "annual total" and "by month" do not read as per-year
    # or per-month breakdowns while "by county", "each year", and "annual"
    # charts still defer.
    without_total = _INTERVAL_WORDS.sub(" ", _TOTAL_ASK.sub(" ", lower))
    series_and_total = (
        _asks_series_and_total(lower)
        and len(utilities) <= 1
        and len(counties) <= 1
        and not _enumerated_years(lower)
        and not _BREAKDOWN.search(without_total)
        and not _PER_YEAR_SERIES.search(without_total)
    )
    if explicit_pair or series_and_total:
        time_args = _time_filter_args(time_resolution)
        viz_dataset = COUNT_MAP_DATASETS.get(dataset or "", "")
        if dataset and time_args and viz_dataset in _TIME_SERIES_VIZ:
            interval = _default_series_interval(lower, time_resolution)
            records_args: dict[str, Any] = {
                "dataset": dataset,
                "result_mode": "count",
                **time_args,
            }
            series_args: dict[str, Any] = {
                "kind": "time_series",
                "dataset": viz_dataset,
                "interval": interval,
                **time_args,
            }
            if len(utilities) == 1:
                records_args["utility"] = utilities[0]
                series_args["utility"] = utilities[0]
            if county and dataset in _COUNTY_CAPABLE_DATASETS:
                records_args["county"] = county
                series_args["county"] = county
            tool_calls = [
                ("data_query_records", records_args),
                ("visualization_create", series_args),
            ]
            blocked = _block_unexpressed_constraints(
                question=text,
                tool_calls=tool_calls,
                slots=slots,
                rule="multi_intent_count_and_trend",
                reason="Count and time series are both explicit",
            )
            if blocked:
                return blocked
            return RouteDecision(
                "deterministic",
                "multi_intent_count_and_trend",
                "Question explicitly requires both a scalar read and a time series",
                tool_calls=tool_calls,
                slots=slots,
            )
        if explicit_pair:
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
            rf"\b(?:ignitions?|outages?|incidents?|{EVENT_DATASET_WORDS})\b", lower
        )
    ):
        return RouteDecision(
            "model",
            "multi_intent_territory_and_map",
            "Question explicitly requires territory plus an event map layer",
            slots=slots,
        )

    # A residual map is scored like risk: one place and one past day. Every
    # branch below returns, so the grid slot never leaks into a non-risk route.
    risk_asked = _wants_risk(lower) or _asks_residual(lower)
    if risk_asked:
        grid_mode = _risk_map_mode(lower)
        if grid_mode:
            slots = {**slots, "map_mode": grid_mode}

    # Fixed two-step coordinate → cell → risk chain.
    if coords and risk_asked:
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
    wants_risk = risk_asked
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
        # A grid map with no place is the statewide surface for that day.
        if slots.get("map_mode"):
            if (
                _forward_relative_phrase(lower)
                or _date_after_risk_coverage(on_date)
                or not on_date
            ):
                return _risk_date_clarification(
                    text=text,
                    time_resolution=time_resolution,
                    reason="Statewide risk surface requires a scoreable past date",
                    slots=slots,
                )
            return RouteDecision(
                "deterministic",
                "risk_surface",
                "Grid map with a date and no place scores the statewide surface",
                tool_calls=[("risk_surface", {"date": on_date})],
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

    timeline = _timeline_datasets(text, lower)
    if timeline and not utilities and not county and not has_count_clause:
        timeline_slots = {
            **slots,
            "series_mode": "timeline",
            "timeline_datasets": [_VIZ_DATASET_NAME[item] for item in timeline],
        }
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "trend_missing_year",
                "Multi-dataset timeline lacks a year",
                answer="What year should I chart?",
                slots=timeline_slots,
            )
        interval = _default_series_interval(lower, time_resolution)
        timeline_calls = [
            (
                "visualization_create",
                {
                    "kind": "time_series",
                    "dataset": _VIZ_DATASET_NAME[item],
                    "interval": interval,
                    **time_args,
                },
            )
            for item in timeline
        ]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=timeline_calls,
            slots=timeline_slots,
            rule="series_timeline",
            reason="Trend names several chartable datasets",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "series_timeline",
            "Trend names several chartable datasets",
            tool_calls=timeline_calls,
            slots=timeline_slots,
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
                            "regions": list(HFTD_TIER_NAMES),
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

    # HDW exists only as a playback overlay on one California event layer.
    # With no named layer this falls through rather than picking one.
    if (
        _asks_hdw(lower)
        and dataset in _HDW_EVENT_DATASETS
        and not has_count_clause
        and not re.search(r"\b(?:trend|time series)\b", lower)
    ):
        hdw_slots = {**slots, "show_hdw": True}
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "map_missing_year",
                "HDW map lacks a year",
                answer=(
                    "What year should I map with HDW? Daily HDW covers "
                    f"{min(HDW_YEARS)} through {max(HDW_YEARS)}."
                ),
                slots=hdw_slots,
            )
        window_years = {
            int(value[:4])
            for value in (time_args.get("start_date"), time_args.get("end_date"))
            if value
        } or {int(time_args["year"])}
        if len(window_years) > 1:
            return RouteDecision(
                "clarification",
                "map_missing_year",
                "HDW playback runs one year at a time",
                answer="HDW plays one year at a time. Which year should I map?",
                slots=hdw_slots,
            )
        if not window_years <= HDW_YEARS:
            return RouteDecision(
                "clarification",
                "time_out_of_coverage",
                "No HDW playback file for that year",
                answer=(
                    f"Daily HDW covers {min(HDW_YEARS)} through {max(HDW_YEARS)}. "
                    "Which year in that range should I map?"
                ),
                slots=hdw_slots,
            )
        hdw_args: dict[str, Any] = {
            "kind": "map",
            "dataset": _VIZ_DATASET_NAME[dataset],
            **time_args,
        }
        if utilities:
            hdw_args["utility"] = utilities[0]
        if county and dataset in _COUNTY_CAPABLE_DATASETS:
            hdw_args["county"] = county
        hdw_calls = [("visualization_create", hdw_args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=hdw_calls,
            slots=hdw_slots,
            rule="hdw_map",
            reason="HDW overlay on a named event layer and year",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "hdw_map",
            "HDW overlay on a named event layer and year",
            tool_calls=hdw_calls,
            slots=hdw_slots,
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
        viz_dataset = _VIZ_DATASET_NAME.get(dataset, dataset)
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
        rule, reason = "map", "Explicit map, dataset, and time filter"
        if has_count_clause and viz_dataset in _TIME_SERIES_VIZ:
            # A map plus a count ("and how many there were") is two results.
            # The count runs with the map's filters; the map alone would
            # silently drop the number. Only event datasets count this way,
            # the US sample included; hftd and circuits do not. A US-sample
            # map with a utility never reaches here (label rule J).
            count_args: dict[str, Any] = {
                "dataset": dataset,
                "result_mode": "count",
                **time_args,
            }
            if utilities:
                count_args["utility"] = utilities[0]
            if county and dataset in _COUNTY_CAPABLE_DATASETS:
                count_args["county"] = county
            tool_calls = [("data_query_records", count_args), *tool_calls]
            rule, reason = "multi_intent_count_and_map", "Explicit map and count with one dataset and window"
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=tool_calls,
            slots=slots,
            rule=rule,
            reason=reason,
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            rule,
            reason,
            tool_calls=tool_calls,
            slots=slots,
        )

    if _asks_summary_panel(lower) and dataset:
        summary_slots = {**slots, "dataset": dataset, "stat_mode": "summary"}
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "records_missing_year",
                "Summary panel lacks a time period",
                answer="What year or date range should I use?",
                slots=summary_slots,
            )
        summary_args: dict[str, Any] = {
            "dataset": dataset,
            "result_mode": "count",
            **time_args,
        }
        if utilities:
            summary_args["utility"] = utilities[0]
        if county and dataset in _COUNTY_CAPABLE_DATASETS:
            summary_args["county"] = county
        summary_calls = [("data_query_records", summary_args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=summary_calls,
            slots=summary_slots,
            rule="summary_stats",
            reason="Summary or overview asks for the live summary panel",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            "summary_stats",
            "Summary or overview asks for the live summary panel",
            tool_calls=summary_calls,
            slots=summary_slots,
        )

    series_request = _series_mode_request(lower)
    if series_request is not None and _asks_series_and_total(lower):
        return _defer_collapsed(slots)
    if series_request is not None:
        series_mode, fixed_dataset = series_request
        chartable = set(SERIES_DATASETS)
        warehouse_dataset = fixed_dataset or (
            dataset if dataset in chartable else None
        )
        mode_slots = {
            **slots,
            "dataset": warehouse_dataset,
            "series_mode": series_mode,
        }
        if warehouse_dataset is None:
            return RouteDecision(
                "clarification",
                "series_mode_missing_dataset",
                "Yearly and seasonal charts need CPUC, EPSS, or CAL FIRE",
                answer=series_dataset_question(),
                slots=mode_slots,
            )
        time_args = _time_filter_args(time_resolution)
        if not time_args:
            return RouteDecision(
                "clarification",
                "series_mode_missing_year",
                "Series panel lacks a time period",
                answer="What year or date range should I use?",
                slots=mode_slots,
            )
        viz_dataset = _VIZ_DATASET_NAME.get(warehouse_dataset, warehouse_dataset)
        series_args: dict[str, Any] = {
            "kind": "time_series",
            "dataset": viz_dataset,
            "interval": "monthly",
            **time_args,
        }
        if utilities:
            series_args["utility"] = utilities[0]
        if county and warehouse_dataset in _COUNTY_CAPABLE_DATASETS:
            series_args["county"] = county
        series_calls = [("visualization_create", series_args)]
        blocked = _block_unexpressed_constraints(
            question=text,
            tool_calls=series_calls,
            slots=mode_slots,
            rule=f"series_{series_mode}",
            reason=f"Series panel {series_mode} for {warehouse_dataset}",
        )
        if blocked:
            return blocked
        return RouteDecision(
            "deterministic",
            f"series_{series_mode}",
            f"Series panel {series_mode} for {warehouse_dataset}",
            tool_calls=series_calls,
            slots=mode_slots,
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
        viz_dataset = _VIZ_DATASET_NAME.get(dataset, dataset)
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


# ---- County-word Census places -------------------------------------------------
# Built last because it asks _unresolved_county_place which place names the
# county-word rule would misread. A Census place whose name holds a county
# word wins over that rule:
# - incorporated ones (Lake Forest, Shasta Lake, South Lake Tahoe, Sutter
#   Creek, Monterey Park, Imperial Beach) are already in _CA_CITIES;
# - census designated places that the rule would misread (Kings Beach, Plumas
#   Lake, Butte Valley, Butte Creek Canyon, Orange Park Acres, Monterey Park
#   Tract) are added to the city matcher and resolve to their CDP point.
# A bare county word ("in Trinity") still clarifies.
_COUNTY_WORD_PLACE_RE: re.Pattern | None = None
_ALL_COUNTY_WORD_PLACES = county_word_places(tuple(_CA_COUNTIES))
_COUNTY_WORD_CDPS: dict[str, CityPoint] = {
    name: point
    for name, point in _ALL_COUNTY_WORD_PLACES.items()
    if point.place_type == "CDP"
    and name not in _CA_CITIES
    and _unresolved_county_place(f"in {point.name}")
}
_COUNTY_WORD_PLACE_RE = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(name)
        for name in sorted(
            {name for name in _ALL_COUNTY_WORD_PLACES if name in _CA_CITIES}
            | set(_COUNTY_WORD_CDPS),
            key=len,
            reverse=True,
        )
    )
    + r")\b"
)
_CITY_NOT_COUNTY = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(name)
        for name in sorted(set(_CA_CITIES) | set(_COUNTY_WORD_CDPS), key=len, reverse=True)
    )
    + r")\b",
    re.I,
)
