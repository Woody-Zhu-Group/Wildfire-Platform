"""Drop model-proposed filters that the question and router slots do not support.

Generalizes the utility rule in tools._strip_ungrounded_utilities to the other
filters a model can invent: circuit ids, HFTD tiers, counties, coordinates,
and sentinel values (all-zero ids, 0,0 coordinates, empty strings).

Utilities stay with the executor rule so the utility_filter_stripped caveat is
unchanged. Dates stay with time_resolve.apply_harness_years, which already
overrides wrong years and rejects invented ones.

Only model-proposed arguments pass through here; deterministic router calls do not.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from services.agent.time_resolve import call_window
from services.shared.dataset_registry import (
    ALL_INCIDENT_TYPES_PATTERN,
    CALIFORNIA_COUNTIES,
    DEFAULT_INCIDENT_TYPE_MODE,
    HFTD_TIER_NAMES,
    TIER_LIST_PATTERN,
    TIER_WORD_PATTERN,
    UNTAGGED_UTILITY,
    UNTAGGED_UTILITY_PATTERN,
    UNTYPED_INCIDENT_PATTERN,
    UTILITY_PATTERNS,
)

TIER_FIELDS = ("tier", "hftd_tier")
COORD_FIELDS = ("lat", "lon")
# Fields that are filters. Structural fields (dataset, kind, metric, interval) are not.
FILTER_FIELDS = ("circuit_id", "county", *TIER_FIELDS, *COORD_FIELDS)

# Enum values that are valid for the tool but change what is counted. Each is
# grounded only when the question asks for it in words; a model that picks
# one on its own is inventing a filter even though the schema accepts it.
UNTAGGED = UNTAGGED_UTILITY
_UNTAGGED_RE = UNTAGGED_UTILITY_PATTERN
_ALL_TYPES_RE = ALL_INCIDENT_TYPES_PATTERN
_UNTYPED_RE = UNTYPED_INCIDENT_PATTERN

# "tier 2", "tier 2 or 3", "tiers 2 and 3", "tier 2/tier 3".
_TIER_RE = TIER_LIST_PATTERN
_TIER_WORD_RE = TIER_WORD_PATTERN
ALL_TIERS = set(HFTD_TIER_NAMES)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DIGITS_RE = re.compile(r"\d{3,}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def named_tiers(question: str) -> set[str]:
    """Tiers the question names by number."""
    return {
        f"Tier {digit}"
        for match in _TIER_RE.findall(question or "")
        for digit in match
        if digit
    }


def _question_tiers(question: str) -> set[str]:
    """Tiers a filter may use: the named ones, or both when the question asks
    across tiers without a number ("which hftd tier covered the most circuits")."""
    named = named_tiers(question)
    if named:
        return named
    return set(ALL_TIERS) if _TIER_WORD_RE.search(question or "") else set()


def _question_numbers(question: str) -> list[float]:
    return [float(value) for value in _NUMBER_RE.findall(question or "")]


def _question_circuits(question: str) -> set[str]:
    return {digits.zfill(9) for digits in _DIGITS_RE.findall(question or "")}


def _is_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or (stripped.isdigit() and set(stripped) == {"0"})
    return False


def question_allows_untagged(question: str) -> bool:
    """True when the question asks about untagged, unattributed, or non-utility records."""
    return _UNTAGGED_RE.search(question or "") is not None


def question_incident_type_modes(question: str) -> set[str]:
    """CAL FIRE incident_type_mode values the question supports.

    The default (Wildfire and Fire types) is always allowed. "all" needs the
    question to ask for every type or non-wildfire records; "untyped" needs it
    to ask for records with no incident type.
    """
    modes = {DEFAULT_INCIDENT_TYPE_MODE}
    text = question or ""
    if _ALL_TYPES_RE.search(text):
        modes.add("all")
    if _UNTYPED_RE.search(text):
        modes.add("untyped")
    return modes


def utility_grounded(value: str, *, question: str, utilities: list[str] | None) -> bool:
    """A utility filter is grounded by the router slots, or by untagged wording."""
    name = value.strip()
    if not name:
        return False
    if name == UNTAGGED:
        return question_allows_untagged(question)
    return name in set(utilities or [])


def _county_grounded(value: str, question: str, county_slot: str | None) -> bool:
    name = _norm(value).removesuffix(" county")
    if county_slot and _norm(county_slot).removesuffix(" county") == name:
        return True
    return bool(name) and re.search(rf"\b{re.escape(name)}\b", _norm(question)) is not None


def ground_model_filters(
    arguments: dict[str, Any],
    *,
    question: str,
    county: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return arguments without ungrounded filters, plus one record per drop."""
    if not isinstance(arguments, dict):
        return arguments, []
    filled = dict(arguments)
    drops: list[dict[str, Any]] = []

    def drop(field: str, reason: str) -> None:
        drops.append({"field": field, "value": filled.pop(field), "reason": reason})

    # Any empty or all-zero string filter is a sentinel, whatever the field.
    for field in [name for name, value in filled.items() if _is_sentinel(value)]:
        if field in FILTER_FIELDS or field in {"utility", "scope"}:
            drop(field, "sentinel")

    value = filled.get("circuit_id")
    if value is not None and str(value).strip().zfill(9) not in _question_circuits(question):
        drop("circuit_id", "not_in_question")

    tiers = _question_tiers(question)
    for field in TIER_FIELDS:
        value = filled.get(field)
        if value is not None and str(value) not in tiers:
            drop(field, "not_in_question")

    value = filled.get("county")
    if value is not None and not _county_grounded(str(value), question, county):
        drop("county", "not_in_question")

    # A valid enum value the question never asked for is still an invented
    # filter: "untyped" counts only records with no type, "all" adds
    # non-wildfire records. Dropping it returns the tool to its default.
    value = filled.get("incident_type_mode")
    if value is not None and str(value) not in question_incident_type_modes(question):
        drop("incident_type_mode", "not_in_question")

    # "untagged" is a utility value that means no utility; only a question
    # about untagged or unattributed records grounds it. Named IOUs stay with
    # the executor rule and its caveat.
    value = filled.get("utility")
    if isinstance(value, str) and value.strip() == UNTAGGED and not question_allows_untagged(question):
        drop("utility", "not_in_question")

    coords = [filled.get(field) for field in COORD_FIELDS]
    if any(item is not None for item in coords):
        numbers = _question_numbers(question)
        zero_zero = all(isinstance(item, (int, float)) and item == 0 for item in coords)
        for field, item in zip(COORD_FIELDS, coords):
            if item is None:
                continue
            grounded = isinstance(item, (int, float)) and any(
                abs(abs(float(item)) - abs(number)) < 1e-4 for number in numbers
            )
            if zero_zero or not grounded:
                drop(field, "sentinel" if zero_zero else "not_in_question")
    return filled, drops


def audit_executed_filters(
    tool: str,
    arguments: dict[str, Any],
    *,
    question: str,
    utilities: list[str] | None,
    county: str | None,
) -> list[dict[str, Any]]:
    """Eval check: every filter value that ran must come from the question or slots.

    Covers the fields above plus utility, which the executor strips at run time.
    """
    _grounded, drops = ground_model_filters(arguments, question=question, county=county)
    drops = [d for d in drops if not (d["field"] == "utility" and d["value"] == UNTAGGED)]
    value = arguments.get("utility")
    if isinstance(value, str) and value.strip() and not utility_grounded(
        value, question=question, utilities=utilities
    ):
        drops.append({"field": "utility", "value": value, "reason": "not_in_slots"})
    for item in arguments.get("utilities") or []:
        if isinstance(item, str) and not utility_grounded(item, question=question, utilities=utilities):
            drops.append({"field": "utilities", "value": item, "reason": "not_in_slots"})
    for drop in drops:
        drop["tool"] = tool
    return drops


def score_executed_filters(
    question: str,
    calls: list[dict[str, Any]],
    *,
    utilities: list[str] | None,
    county: str | None,
) -> dict[str, Any]:
    """Eval check over executed primary calls (trajectory tool_call events).

    invented: a filter value that ran but is not in the question or slots.
    missing: a slot the question names (utility, county) that no successful call used.
    """
    invented: list[dict[str, Any]] = []
    for call in calls:
        invented.extend(
            audit_executed_filters(
                str(call.get("tool")),
                call.get("arguments") or {},
                question=question,
                utilities=utilities,
                county=county,
            )
        )
    ok_calls = [call for call in calls if call.get("ok")]
    missing: list[str] = []
    if ok_calls:
        used_utilities: set[str] = set()
        used_counties: set[str] = set()
        for call in ok_calls:
            args = call.get("arguments") or {}
            if isinstance(args.get("utility"), str):
                used_utilities.add(args["utility"])
            scope = args.get("scope")
            if isinstance(scope, str):
                if args.get("scope_type") == "county":
                    used_counties.add(_norm(scope).removesuffix(" county"))
                else:
                    used_utilities.add(scope)
            used_utilities.update(item for item in args.get("utilities") or [] if isinstance(item, str))
            if isinstance(args.get("county"), str):
                used_counties.add(_norm(args["county"]).removesuffix(" county"))
            used_counties.update(
                _norm(item).removesuffix(" county")
                for item in args.get("regions") or []
                if isinstance(item, str)
            )
        missing.extend(f"utility:{name}" for name in utilities or [] if name not in used_utilities)
        if county and _norm(county).removesuffix(" county") not in used_counties:
            missing.append(f"county:{county}")
    return {"invented": invented, "missing": missing, "pass": not invented and not missing}


def named_counties(question: str, county_slot: str | None) -> list[str]:
    """The router's county slot plus every county the question names.

    The router keeps one county; "Butte County and Shasta County" or
    "Riverside, San Bernardino, and Los Angeles counties" name several. Bare names
    count only when the question says county or counties.
    """
    found: list[str] = [county_slot] if county_slot else []
    lower = _norm(question)
    if not re.search(r"\bcount(?:y|ies)\b", lower):
        return found
    scrubbed = lower
    for pattern in UTILITY_PATTERNS.values():
        scrubbed = re.sub(pattern, " ", scrubbed, flags=re.I)
    for name in sorted(CALIFORNIA_COUNTIES, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}\b"
        if re.search(pattern, scrubbed):
            scrubbed = re.sub(pattern, " ", scrubbed)
            if name not in found:
                found.append(name)
    return found


def named_entities(
    question: str,
    *,
    utilities: list[str] | None,
    county: str | None,
    years: list[int] | None,
    months: list[str] | None = None,
) -> dict[str, list[Any]]:
    """Entities a multi-part question names, each of which must be covered by a call.

    ``months`` are calendar months named as separate periods (``YYYY-MM``,
    ``named_month_periods``): "July 2023 and August 2023" names two, and a
    July read alone does not answer it.
    """
    tiers = sorted(named_tiers(question))
    return {
        "utility": list(utilities or []),
        "county": named_counties(question, county),
        "year": sorted(set(years or [])),
        "tier": tiers if len(tiers) > 1 else [],
        "month": list(dict.fromkeys(months or [])),
    }


def _years_in_range(start: Any, end: Any) -> set[int]:
    try:
        first, last = int(str(start)[:4]), int(str(end)[:4])
    except (TypeError, ValueError):
        return set()
    return set(range(first, last + 1)) if first <= last else set()


def _month_of(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def _months_covered(arguments: dict[str, Any], named: list[str]) -> set[str]:
    """Named months (``YYYY-MM``) a call reads, when it reads nothing else.

    A call covers a named month when its window overlaps that month and lies
    wholly inside the named months. A July call covers July only, so a July
    and August question is not answered from July alone. One call over July
    and August covers both, as a written span covers each of its years. A
    call over all of 2023, or over October 2022 to October 2023 for "October
    2022 and October 2023", reads months the question never named and covers
    none of them.
    """
    window = call_window(arguments)
    if window is None or not named:
        return set()
    start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
    months: list[str] = []
    day = date(start.year, start.month, 1)
    while day <= end:
        months.append(_month_of(day))
        day = date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
    if any(month not in named for month in months):
        return set()
    return set(months)


def _covered(arguments: dict[str, Any], tool: str) -> dict[str, set[Any]]:
    covered: dict[str, set[Any]] = {"utility": set(), "county": set(), "year": set(), "tier": set()}
    args = arguments or {}
    group_by = args.get("group_by")
    if tool == "data_query_rank" and group_by == "utility":
        covered["utility"].add("*")
    if tool == "data_query_rank" and group_by == "county":
        covered["county"].add("*")
    if isinstance(args.get("utility"), str):
        covered["utility"].add(args["utility"])
    covered["utility"].update(u for u in args.get("utilities") or [] if isinstance(u, str))
    scope = args.get("scope")
    if isinstance(scope, str):
        key = "county" if args.get("scope_type") == "county" else "utility"
        covered[key].add(_norm(scope).removesuffix(" county") if key == "county" else scope)
    if isinstance(args.get("county"), str):
        covered["county"].add(_norm(args["county"]).removesuffix(" county"))
    for region in args.get("regions") or []:
        if not isinstance(region, str):
            continue
        if args.get("region_type") == "hftd":
            covered["tier"].add(region)
        else:
            covered["county"].add(_norm(region).removesuffix(" county"))
    for field in TIER_FIELDS:
        if isinstance(args.get(field), str):
            covered["tier"].add(args[field])
    if isinstance(args.get("year"), int):
        covered["year"].add(args["year"])
    covered["year"] |= _years_in_range(args.get("start_date"), args.get("end_date"))
    for prefix in ("period_a", "period_b"):
        covered["year"] |= _years_in_range(args.get(f"{prefix}_start"), args.get(f"{prefix}_end"))
    if isinstance(args.get("date"), str):
        covered["year"] |= _years_in_range(args["date"], args["date"])
    return covered


def uncovered_entities(
    entities: dict[str, list[Any]],
    calls: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    """Named entities no successful primary call covered, as "kind:value" labels."""
    covered: dict[str, set[Any]] = {
        "utility": set(),
        "county": set(),
        "year": set(),
        "tier": set(),
        "month": set(),
    }
    for tool, arguments in calls:
        for kind, values in _covered(arguments, tool).items():
            covered[kind] |= values
        covered["month"] |= _months_covered(arguments, list(entities.get("month") or []))
    missing: list[str] = []
    for kind, values in entities.items():
        if not values:
            continue
        if "*" in covered[kind]:
            continue
        for value in values:
            key = _norm(str(value)).removesuffix(" county") if kind == "county" else value
            if key not in covered[kind]:
                missing.append(f"{kind}:{value}")
    return missing
