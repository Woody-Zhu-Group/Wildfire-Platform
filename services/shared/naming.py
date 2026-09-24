"""Naming conventions: the one place names, aliases, and spellings are defined.

Owned and re-exported by ``services.shared.dataset_registry``; import these
names from the registry. Dataset keys, dataset aliases, and dataset labels live
on the registry's ``DatasetSpec`` entries; everything else a service, the
router, the harness, a loader, or the frontend generator needs to spell a
utility, a county, an HFTD tier, an EPSS cause, or a CAL FIRE incident type
is defined here, once.

Where two consumers used different spellings of the same thing, both are kept
under separate names with a comment rather than merged: this module moved
existing values and must not change what any caller accepts or prints.

This module imports only the standard library so every package (services,
db loaders, scripts) can import it without a cycle.

``tests/test_naming_single_source.py`` fails if another module defines its
own list of utility, county, tier, or dataset names or aliases.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

# Warehouse utility codes (the values stored in every ``utility`` column).
# Mixed case is the stored spelling: Liberty is title case, the rest upper.
UTILITY_CODES: tuple[str, ...] = ("PGE", "SCE", "SDGE", "PACIFICORP", "Liberty", "BVES")
KNOWN_UTILITIES = frozenset(UTILITY_CODES)
# Filter keyword for rows with no utility attribution.
UNTAGGED_UTILITY = "untagged"

# The fitted cNHPP risk model covers these three IOU territories only.
RISK_MODEL_UTILITIES = frozenset({"PGE", "SCE", "SDGE"})

# Accepted spellings for a utility filter value (data_query ``parse_utility``).
# Keys are the caller's value upper-cased with & . - , and spaces removed.
UTILITY_FILTER_KEYS: dict[str, str] = {
    "PGE": "PGE",
    "PACIFICGASANDELECTRIC": "PGE",
    "PACIFICGASELECTRIC": "PGE",
    "SCE": "SCE",
    "SOUTHERNCALIFORNIAEDISON": "SCE",
    "EDISON": "SCE",
    "SDGE": "SDGE",
    "SANDIEGOGASANDELECTRIC": "SDGE",
    "SANDIEGOGASELECTRIC": "SDGE",
    "PACIFICORP": "PACIFICORP",
    "PACIFICPOWER": "PACIFICORP",
    "LIBERTY": "Liberty",
    "LIBERTYUTILITIES": "Liberty",
    "BVES": "BVES",
    "BEARVALLEY": "BVES",
    "BEARVALLEYELECTRIC": "BVES",
    "BEARVALLEYELECTRICSERVICE": "BVES",
    "BEARVALLEYELECTRICSERVICES": "BVES",
    "UNTAGGED": UNTAGGED_UTILITY,
}
# A trailing corporate word ``parse_utility`` drops before a second lookup.
UTILITY_FILTER_SUFFIXES: tuple[str, ...] = (
    "COMPANY",
    "CORPORATION",
    "UTILITIES",
    "UTILITY",
    "INC",
    "CORP",
)

# Model-argument repairs in the agent harness (``argument_normalize``). Keys
# are the value stripped and upper-cased only. Discrepancy: narrower than
# UTILITY_FILTER_KEYS (no EDISON, PACIFIC POWER, BEAR VALLEY, suffixes, and
# "PACIFIC GAS AND ELECTRIC" without the San Diego "AND" form). Kept as is:
# widening it would change which model arguments the harness repairs.
UTILITY_ARGUMENT_ALIASES: dict[str, str] = {
    "PG&E": "PGE",
    "PGE": "PGE",
    "PACIFIC GAS & ELECTRIC": "PGE",
    "PACIFIC GAS AND ELECTRIC": "PGE",
    "SCE": "SCE",
    "SOUTHERN CALIFORNIA EDISON": "SCE",
    "SDG&E": "SDGE",
    "SDGE": "SDGE",
    "SAN DIEGO GAS & ELECTRIC": "SDGE",
    "PACIFICORP": "PACIFICORP",
    "LIBERTY": "Liberty",
    "BVES": "BVES",
    "UNTAGGED": UNTAGGED_UTILITY,
}

# Publisher UtilityID in the CPUC IOU service-territory layer to the
# warehouse code (``db/loaders/load_iou.py``). "LU" appears only there.
IOU_PUBLISHER_UTILITY_CODES: dict[str, str] = {
    "PG&E": "PGE",
    "SCE": "SCE",
    "PacifiCorp": "PACIFICORP",
    "SDG&E": "SDGE",
    "LU": "Liberty",
    "BVES": "BVES",
}

# Display labels. Codes not listed display as the code itself.
# data_query grouped counts and the website filter show these two.
UTILITY_DISPLAY_LABELS: dict[str, str] = {"PGE": "PG&E", "SDGE": "SDG&E"}
# Utilities the workspace pads grouped-count rows with, as display labels.
WORKSPACE_UTILITIES: tuple[str, ...] = ("PG&E", "SCE", "SDG&E")
_UTILITY_CODE_BY_LABEL = {label: code for code, label in UTILITY_DISPLAY_LABELS.items()}


def group_code_and_label(group_by: str, value: str) -> dict[str, str]:
    """The ``code`` and ``label`` fields of one /rank or /grouped-counts row.

    For ``group_by="utility"`` the value may be a code (``PGE``, as /rank
    returns) or a display label (``PG&E``, as /grouped-counts returns); both
    give ``{"code": "PGE", "label": "PG&E"}``. A utility value the registry
    does not list, and a missing-value placeholder, is its own code and label.
    Every other grouping (county, circuit, cause) uses the value as both.
    """
    if group_by != "utility":
        return {"code": value, "label": value}
    code = _UTILITY_CODE_BY_LABEL.get(value, value)
    return {"code": code, "label": UTILITY_DISPLAY_LABELS.get(code, code)}
# Short labels in clarification text ("PG&E territory"). Discrepancy: adds
# PacifiCorp and "Bear Valley" beyond UTILITY_DISPLAY_LABELS; SCE and Liberty
# fall through to the code.
UTILITY_CLARIFY_LABELS: dict[str, str] = {
    "PGE": "PG&E",
    "SDGE": "SDG&E",
    "PACIFICORP": "PacifiCorp",
    "BVES": "Bear Valley",
}
# Full names in Jev question wording (pinned in Jev payload hashes).
UTILITY_FULL_NAMES: dict[str, str] = {
    "PGE": "Pacific Gas and Electric",
    "SCE": "Southern California Edison",
    "SDGE": "San Diego Gas and Electric",
    "PACIFICORP": "PacifiCorp",
    "Liberty": "Liberty Utilities",
    "BVES": "Bear Valley Electric Service",
}
# Possessive names for the point-context sentence. Discrepancy: "&" where
# UTILITY_FULL_NAMES says "and"; both are shown to users as written.
UTILITY_POSSESSIVE_NAMES: dict[str, str] = {
    "PGE": "Pacific Gas & Electric's",
    "SCE": "Southern California Edison's",
    "SDGE": "San Diego Gas & Electric's",
    "PACIFICORP": "PacifiCorp's",
    "Liberty": "Liberty Utilities'",
    "BVES": "Bear Valley Electric Service's",
}

# How a question names each utility (router slot extraction and every
# harness check that scrubs utility words). Dict order is the slot order.
UTILITY_PATTERNS: dict[str, str] = {
    "PGE": r"\b(?:pge|pg\s*&\s*e|pg\s+and\s+e|pacific gas(?:(?: and| &) electric)?)\b",
    "SCE": r"\b(?:sce|socal edison|southern california edison|edison)\b",
    "SDGE": r"\b(?:sdge|sdg\s*&\s*e|san diego gas(?:(?: and| &) electric)?)\b",
    "PACIFICORP": r"\bpacificorp\b",
    "Liberty": r"\bliberty\b",
    "BVES": r"\b(?:bves|bear valley(?: electric)?)\b",
}
# Utility words that make a utility the subject of an advice or penalty
# question (router advice refusal). Discrepancy: a different word set from
# UTILITY_PATTERNS (no bare "edison" or "socal edison"). Kept as written.
UTILITY_ADVICE_SUBJECT_WORDS = (
    r"pge|pg\s*&\s*e|sce|sdge|"
    r"sdg\s*&\s*e|pacificorp|liberty|bear\s+valley|bves|"
    r"pacific\s+gas|southern\s+california\s+edison|san\s+diego\s+gas"
)
# How a question asks for rows with no utility attribution.
UNTAGGED_UTILITY_PATTERN = re.compile(
    r"\b(?:untagged|un-?attributed|not attributed|unassigned|no utility|"
    r"without (?:a |an |any )?utility|non-?utility|not tagged|missing (?:a |the )?utility|"
    r"unknown utility|no (?:known |named )?utility)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Counties
# ---------------------------------------------------------------------------

# Census TIGER names, as stored in ``wildfire.counties`` and every county
# column. Order is alphabetical and is part of the Jev county option order.
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
# A trailing county word a filter value may carry ("Butte County", "Butte Co.").
COUNTY_SUFFIX_PATTERN = re.compile(r"\s+(?:county|co\.?|cnty)$", re.I)

# Orange is a county and a color, and Kings, Lake, Mono, Trinity, Glenn, and
# Alpine are ordinary words or parts of other place names. In a question these
# need the word County to count as the county. Lowercase.
COUNTIES_NEEDING_QUALIFIER = frozenset(
    {"orange", "kings", "lake", "mono", "trinity", "glenn", "alpine"}
)

# ---------------------------------------------------------------------------
# HFTD tiers
# ---------------------------------------------------------------------------

# Stored ``tier`` values (the publisher's HFTD attribute), keyed by number.
HFTD_TIER_BY_NUMBER: dict[str, str] = {"2": "Tier 2", "3": "Tier 3"}
HFTD_TIER_NAMES: tuple[str, ...] = tuple(HFTD_TIER_BY_NUMBER.values())
HFTD_TIERS = frozenset(HFTD_TIER_NAMES)

# Question wording. Router: any HFTD or tier mention.
TIER_MENTION_PATTERN = re.compile(r"\bhftd\b|\bhigh fire threat|\btier\s*[23]\b", re.I)
# Grounding: "tier 2", "tier 2 or 3", "tiers 2 and 3", "tier 2/tier 3".
TIER_LIST_PATTERN = re.compile(
    r"\btiers?\s*([23])(?:\s*(?:,|and|or|&|/)\s*(?:tier\s*)?([23]))?\b",
    re.IGNORECASE,
)
TIER_WORD_PATTERN = re.compile(r"\btiers?\b", re.IGNORECASE)
# Jev tool pick: each tier digit named after "tier" (question already lowercase).
TIER_DIGIT_PATTERN = r"tier\s*([23])"

# ---------------------------------------------------------------------------
# EPSS causes and outage types
# ---------------------------------------------------------------------------

# EPSS cause codes (written rule from Michael, 2026-09-24): a cause code and
# its word form are the same cause. Filters and groupings match both, and
# results display the word form. The codes appear only in the 2021 rows; from
# 2022 the source writes words. Applied only to unambiguous pairs:
#   VEG (1 row)  -> Vegetation (1,042)
#   UNK (6 rows) -> Unknown (3,724)
#   3RD (1 row)  -> 3rd Party (914)
# Left alone: EF (1 row, 2021). It reads as "Equipment Failure", but two word
# forms could claim it: "Equipment" (290 rows, 2022 only) and "Equipment
# Failure/Involved" (986 rows, 2023 on). "Equipment" and "Equipment
# Failure/Involved" are two words, not a code and a word, so the rule does
# not merge them either.
EPSS_CAUSE_CODE_WORDS: dict[str, str] = {
    "VEG": "Vegetation",
    "UNK": "Unknown",
    "3RD": "3rd Party",
}
EPSS_CAUSE_CODES_LEFT_ALONE: dict[str, str] = {
    "EF": 'ambiguous between "Equipment" (2022) and "Equipment Failure/Involved" (2023 on)',
}
# Load-time rule (``db/loaders/load_epss.py``): these source spellings,
# compared stripped and lowercased, are stored as "Unknown". Separate from
# the query-time UNK code above; both land on the same word.
EPSS_UNKNOWN_CAUSE_SOURCE_SPELLINGS = frozenset({"unknown", "unknown cause"})
EPSS_UNKNOWN_CAUSE = "Unknown"

# EPSS outage types have no fixed list: filters resolve against the stored
# values (``services/shared/stored_values.py``).

# ---------------------------------------------------------------------------
# CAL FIRE incident types
# ---------------------------------------------------------------------------

# The default CAL FIRE population: these stored incident types.
CALFIRE_DEFAULT_INCIDENT_TYPES: tuple[str, ...] = ("Wildfire", "Fire")
# The default as a comma-separated parameter value, as echoed in meta.
CALFIRE_DEFAULT_INCIDENT_TYPE_PARAM = ",".join(CALFIRE_DEFAULT_INCIDENT_TYPES)
# incident_type filter keywords, lowercase: no type filter, or NULL types.
CALFIRE_INCIDENT_TYPE_KEYWORDS = frozenset({"all", "untyped"})
# Agent ``incident_type_mode`` values; the first is the default.
INCIDENT_TYPE_MODES: tuple[str, ...] = ("wildfire_default", "all", "untyped")
DEFAULT_INCIDENT_TYPE_MODE = INCIDENT_TYPE_MODES[0]


def calfire_default_type_sql(column: str) -> str:
    """WHERE fragment for the default incident types, e.g. ``c.incident_type IN ('Wildfire', 'Fire')``."""
    quoted = ", ".join(f"'{value}'" for value in CALFIRE_DEFAULT_INCIDENT_TYPES)
    return f"{column} IN ({quoted})"


# Question wording for the two non-default modes.
ALL_INCIDENT_TYPES_PATTERN = re.compile(
    r"\b(?:all (?:calfire |cal fire )?(?:incident |record )?types|every (?:incident |record )?type|"
    r"any (?:incident |record )?type|regardless of (?:incident |record )?type|"
    r"all (?:calfire |cal fire )?records|including non-?wildfire|non-?wildfire|"
    r"all incidents(?: of any type)?|not (?:just|only) wildfires?)\b",
    re.IGNORECASE,
)
UNTYPED_INCIDENT_PATTERN = re.compile(
    r"\b(?:untyped|no incident type|without (?:an |any )?incident type|"
    r"missing (?:an |the |their )?(?:incident )?type|unknown (?:incident )?type|"
    r"null (?:incident )?type|no type)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Dataset wording in questions (keys, aliases, and labels are on DatasetSpec)
# ---------------------------------------------------------------------------

# Router dataset cues, in candidate order (after the US sample check).
DATASET_QUESTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("epss_outages", r"\bepss\b|\bfast[- ]trip\b"),
    ("psps_events", r"\bpsps\b|\bpublic safety power shutoff"),
    ("calfire_incidents", r"\bcal\s*fire\b|\bcalfire\b"),
    ("cpuc_ignitions", r"\bcpuc\b|\butility[- ](?:caused|attributed|tagged)\b"),
    ("circuits", r"\bcircuits?\b"),
    ("hftd", r"\bhftd\b|\bhigh fire threat"),
    ("iou_territories", r"\biou territor|\butility territor"),
)
# Regex alternation naming the EPSS, PSPS, and CAL FIRE event datasets. The
# router and the harness build their event-word checks from it.
EVENT_DATASET_WORDS = r"epss|psps|cal\s*fire"
# Bare "ignitions" with no other cue reads as CPUC ignitions; bare "outages"
# (no PSPS or EPSS) reads as EPSS outages.
BARE_IGNITIONS_PATTERN = r"\bignitions?\b"
BARE_OUTAGES_PATTERN = r"\boutages?\b"

# Jev expected-fact dataset cues. Discrepancy: narrower than the router's
# (no fast-trip, PSPS long form, territories; adds "us ignitions"). Kept as is.
EXPECTED_FACT_DATASET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("cpuc_ignitions", r"\b(?:cpuc|utility-caused ignitions?)\b"),
    ("calfire_incidents", r"\b(?:cal\s*fire|calfire)\b"),
    ("epss_outages", r"\bepss\b"),
    ("psps_events", r"\bpsps\b"),
    ("us_ignitions", r"\bus ignitions?\b"),
    ("circuits", r"\bcircuits?\b"),
    ("hftd", r"\bhftd\b"),
)

# Wording that names the US ignitions sample (FireCastRL) outright.
US_SAMPLE_NAMED_PATTERN = re.compile(
    r"\b(?:us|u\.s\.|national)\s+(?:wildfire\s+)?ignitions?\b|"
    r"\b(?:us|u\.s\.|national)\s+(?:ignitions?\s+)?sample\b|"
    r"\bignitions?\s+sample\b|"
    r"\bfirecast",
    re.I,
)
# "sampled/sample ... ignitions" with up to three words between, never across
# "of" ("a sample of PGE ignitions" is list wording).
SAMPLE_BEFORE_IGNITIONS_PATTERN = re.compile(
    r"\bsampled?\s+(?:(?!of\b)[\w&'-]+\s+){0,3}$", re.I
)
ALL_CAUSES_BEFORE_IGNITIONS_PATTERN = re.compile(
    r"\ball[- ]causes?\s+(?:wildfire\s+)?$", re.I
)
ALL_CAUSES_AFTER_IGNITIONS_PATTERN = re.compile(
    r"^[^.?!]{0,80}?\b(?:of|from)\s+all\s+causes\b", re.I
)
# CPUC or a utility named right before "ignitions" means CPUC ignitions, even
# with sample or all-causes wording around it.
CPUC_OR_UTILITY_BEFORE_IGNITIONS_PATTERN = re.compile(
    r"(?:\bcpuc\b|\butility[- ](?:caused|attributed|tagged)\b|"
    + "|".join(f"(?:{pattern})" for pattern in UTILITY_PATTERNS.values())
    + r")(?:\W+[\w&'-]+){0,2}\W*$",
    re.I,
)
# A word right before "ignitions" that already names whose ignitions they are.
IGNITION_QUALIFIER_PATTERN = re.compile(
    r"(?:\bus|\bnational|\bsampled?|\ball[- ]causes?|\bcal\s*fire|\bcalfire|\bepss|\bpsps)"
    r"\s+(?:wildfire\s+)?$",
    re.I,
)

# ---------------------------------------------------------------------------
# Measures: what a ranking or comparison can order by
# ---------------------------------------------------------------------------
# Which measures each grouping supports is on the dataset registry
# (RANK_MEASURES, COMPARE_MEASURES); the names and words for them are here.

# One label per measure, keyed by the comparison metric name.
MEASURE_LABELS: dict[str, str] = {
    "ignition_count": "CPUC ignition counts",
    "calfire_incident_count": "CAL FIRE incident counts",
    "acres_burned": "CAL FIRE acres burned",
    "psps_event_count": "PSPS event counts",
    "customers_deenergized": "customers de-energized in PSPS events",
    "epss_outage_count": "EPSS outage counts (PG&E only)",
    "epss_to_ignition_ratio": "EPSS outages per CPUC ignition (PG&E only)",
}
# Measures people ask for that no dataset stores, in clarification wording.
MEASURES_NOT_IN_DATA: tuple[str, ...] = ("damage", "fatalities", "destroyed structures")

# Words that name a measure a tool returns: a count (its events or a count
# word), acres, customers affected, or a ratio. One word each, lower case.
MEASURE_TERMS = frozenset(
    {
        # counts
        "ignition", "ignitions", "fire", "fires", "wildfire", "wildfires",
        "incident", "incidents", "outage", "outages", "event", "events",
        "shutoff", "shutoffs", "deenergization", "deenergizations",
        "energization", "energizations", "number", "count", "counts",
        "total", "totals", "frequency", "frequently", "often", "times",
        # acres
        "acre", "acres", "acreage", "area",
        # customers affected
        "customer", "customers", "deenergized", "energized",
        # ratio
        "ratio",
    }
)
# Data vocabulary that narrows a measure without naming one: dataset, utility,
# place, grouping, and period words, and the risk and change measures other
# routes own. A ranking phrase made only of these and measure terms resolves.
MEASURE_QUALIFIER_WORDS = frozenset(
    {
        "the", "a", "an", "of", "their", "its", "each", "all", "any", "how",
        "many", "much", "utility", "utilities", "caused", "attributed",
        "tagged", "cpuc", "cal", "calfire", "epss", "psps", "us", "national",
        "sample", "sampled", "reported", "recorded", "burned", "affected",
        "fast", "trip", "power", "public", "safety", "de", "distribution",
        "circuit", "circuits", "county", "counties", "hftd", "tier", "tiers",
        "high", "threat", "wildland", "forest", "iou", "territory", "record",
        "records", "year", "years", "yearly", "annual", "month", "months",
        "monthly", "season", "seasons", "day", "days", "recent", "single",
        "individual", "one", "overall", "combined", "cumulative", "state",
        "states", "division", "divisions", "cell", "cells", "grid",
        # risk and change measures, which other routes answer or refuse
        "risk", "risks", "fitted", "predicted", "modeled", "forecast",
        "probability", "intensity", "increase", "increases", "decrease",
        "decreases", "change", "changes", "growth", "rise", "drop", "decline",
        "jump",
    }
)
# Measures no dataset stores. A ranking phrase naming one is left to the
# unsupported-topic and other-measure refusals.
NOT_IN_DATA_MEASURE_PATTERN = re.compile(
    r"^(?:damages?|damaged|fatalit\w*|deaths?|dead|injur\w*|structures?|buildings?|"
    r"homes?|cost\w*|dollars?|loss|losses|rates?|per|response|durations?|hours?|"
    r"minutes?|causes?|smoke|evacuat\w*|people|population|residents?|spend\w*|"
    r"time|lengths?)$"
)
# Superlative-looking words that are not superlatives, or that order by time
# or distance rather than by a measure.
NOT_A_MEASURE_SUPERLATIVE = frozenset(
    {
        "latest", "earliest", "newest", "oldest", "nearest", "closest",
        "farthest", "furthest", "forest", "interest", "request", "harvest",
        "contest", "protest", "suggest", "honest", "modest", "southwest",
        "northwest", "midwest", "digest", "arrest", "invest", "other",
        "rather", "later", "earlier", "sooner",
    }
)


__all__ = sorted(
    name
    for name, value in list(globals().items())
    if not name.startswith("_") and name not in {"annotations", "re"}
    and not isinstance(value, type(re))
)
