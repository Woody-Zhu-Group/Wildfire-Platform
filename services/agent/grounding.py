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
from typing import Any

TIER_FIELDS = ("tier", "hftd_tier")
COORD_FIELDS = ("lat", "lon")
# Fields that are filters. Structural fields (dataset, kind, metric, interval) are not.
FILTER_FIELDS = ("circuit_id", "county", *TIER_FIELDS, *COORD_FIELDS)

_TIER_RE = re.compile(r"\btier\s*([23])\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DIGITS_RE = re.compile(r"\d{3,}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _question_tiers(question: str) -> set[str]:
    return {f"Tier {match}" for match in _TIER_RE.findall(question or "")}


def _question_numbers(question: str) -> list[float]:
    return [float(value) for value in _NUMBER_RE.findall(question or "")]


def _question_circuits(question: str) -> set[str]:
    return {digits.zfill(9) for digits in _DIGITS_RE.findall(question or "")}


def _is_sentinel(value: Any) -> bool:
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or (stripped.isdigit() and set(stripped) == {"0"})
    return False


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
    allowed = set(utilities or [])
    value = arguments.get("utility")
    if isinstance(value, str) and value.strip() and value.strip() not in allowed:
        drops.append({"field": "utility", "value": value, "reason": "not_in_slots"})
    for item in arguments.get("utilities") or []:
        if isinstance(item, str) and item not in allowed:
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
