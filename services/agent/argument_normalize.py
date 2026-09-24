"""Deterministic argument repairs applied before schema validation.

These are harness guarantees: alias spelling and omitted year fill from
slots the router already extracted. They must stay byte-stable and must not
invent filters the question did not imply.
"""

from __future__ import annotations

from typing import Any

from services.shared.dataset_registry import (
    ARGUMENT_RECORDS_DATASET_ALIASES as RECORDS_DATASET_ALIASES,
    ARGUMENT_VIZ_DATASET_ALIASES as VISUALIZATION_DATASET_ALIASES,
    UTILITY_ARGUMENT_ALIASES as UTILITY_ALIASES,
)

# The alias maps (human and cross-catalog spellings to canonical schema
# values) are naming conventions defined in the registry.

def normalize_model_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Map near-miss aliases; does not invent missing temporal fields."""
    normalized = dict(arguments)

    # Models often emit county="" / circuit_id=""; treat blanks as omitted.
    for key, value in list(normalized.items()):
        if value is None:
            normalized.pop(key, None)
        elif isinstance(value, str) and value.strip() == "":
            normalized.pop(key, None)

    def utility(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return UTILITY_ALIASES.get(value.strip().upper(), value)

    if "utility" in normalized:
        normalized["utility"] = utility(normalized["utility"])
    if isinstance(normalized.get("utilities"), list):
        normalized["utilities"] = [
            utility(value) for value in normalized["utilities"]
        ]
    if (
        tool == "comparison_run"
        and normalized.get("scope_type") == "utility"
        and "scope" in normalized
    ):
        normalized["scope"] = utility(normalized["scope"])

    if "dataset" in normalized and isinstance(normalized["dataset"], str):
        key = " ".join(normalized["dataset"].strip().lower().split())
        if tool == "visualization_create":
            normalized["dataset"] = VISUALIZATION_DATASET_ALIASES.get(
                key, normalized["dataset"]
            )
            # Utility-scoped CPUC trends are never the US sample.
            if (
                normalized.get("utility")
                and normalized["dataset"] == "us_ignitions"
            ):
                normalized["dataset"] = "ignitions"
        elif tool in {"data_query_records", "data_query_rank"}:
            normalized["dataset"] = RECORDS_DATASET_ALIASES.get(
                key, normalized["dataset"]
            )
        elif tool == "visualization_inspect":
            normalized["dataset"] = VISUALIZATION_DATASET_ALIASES.get(
                key, normalized["dataset"]
            )

    # tier is only valid for hftd; models sometimes attach it to ignition counts.
    if (
        tool == "data_query_records"
        and normalized.get("dataset") != "hftd"
        and "tier" in normalized
    ):
        normalized.pop("tier", None)
    # incident_type_mode is CAL FIRE-only; models sometimes attach it elsewhere.
    if (
        tool in {"data_query_records", "data_query_rank"}
        and normalized.get("dataset") != "calfire_incidents"
    ):
        normalized.pop("incident_type_mode", None)

    return normalized


def fill_year_from_slot(
    tool: str,
    arguments: dict[str, Any],
    *,
    year: int | None,
) -> dict[str, Any]:
    """Fill an omitted year only when the question implies exactly one year.

    Safe when:
    - ``year`` is a single extracted slot from the question, and
    - the model omitted both ``year`` and any ``start_date``/``end_date``.

    Unsafe (skipped) when the model already supplied any temporal field, so we
    never overwrite a deliberate range or silently change the asked window.
    For ``data_query_spatial`` kind=summary, which requires dates rather than
    ``year``, a missing range is filled as the full calendar year.
    """
    if year is None:
        return arguments
    filled = dict(arguments)
    # Grammar truncation sometimes emits year=2 instead of 2024. Values below
    # the schema floor cannot be intentional calendar years, so replace them.
    if isinstance(filled.get("year"), int) and filled["year"] < 1900:
        filled["year"] = year
        return filled
    has_year = filled.get("year") is not None
    has_start = filled.get("start_date") not in (None, "")
    has_end = filled.get("end_date") not in (None, "")
    if has_year or has_start or has_end:
        return filled

    if tool == "data_query_spatial" and filled.get("kind") == "summary":
        filled["start_date"] = f"{year}-01-01"
        filled["end_date"] = f"{year}-12-31"
        return filled

    if tool == "comparison_run":
        # comparison_run uses start/end (or period_*), not a year field.
        if filled.get("kind") == "periods":
            return filled
        filled["start_date"] = f"{year}-01-01"
        filled["end_date"] = f"{year}-12-31"
        return filled

    if tool in {
        "data_query_records",
        "data_query_rank",
        "visualization_create",
        "visualization_inspect",
    }:
        filled["year"] = year
        return filled

    return filled


def repair_comparison_kind(
    arguments: dict[str, Any],
    *,
    year: int | None = None,
    years: list[int] | None = None,
    utilities: list[str] | None = None,
) -> dict[str, Any]:
    """Coerce an obviously wrong comparison kind from router slots.

    Two named utilities and a single year window cannot be ``kind=periods``
    (that needs two date ranges). Repair before schema validation so the model
    does not die on a recoverable shape error that the slots already solve.
    """
    if not isinstance(arguments, dict):
        return arguments
    filled = dict(arguments)
    kind = filled.get("kind")
    util_slot = list(utilities or [])
    year_slot = year
    years_slot = list(years or ([] if year is None else [year]))

    # Model forgot kind but named two utilities.
    if kind is None and len(util_slot) >= 2 and year_slot is not None:
        kind = "utilities"
        filled["kind"] = "utilities"

    if (
        kind == "periods"
        and len(util_slot) >= 2
        and len(years_slot) <= 1
        and year_slot is not None
    ):
        filled["kind"] = "utilities"
        filled["utilities"] = util_slot[: max(2, len(util_slot))]
        filled.setdefault("start_date", f"{year_slot}-01-01")
        filled.setdefault("end_date", f"{year_slot}-12-31")
        for key in (
            "scope_type",
            "scope",
            "period_a_start",
            "period_a_end",
            "period_b_start",
            "period_b_end",
        ):
            filled.pop(key, None)
        return filled

    if filled.get("kind") == "utilities":
        if util_slot and not filled.get("utilities"):
            filled["utilities"] = util_slot
        if year_slot is not None:
            filled.setdefault("start_date", f"{year_slot}-01-01")
            filled.setdefault("end_date", f"{year_slot}-12-31")
    return filled


def fill_utility_from_slot(
    tool: str,
    arguments: dict[str, Any],
    *,
    utilities: list[str] | None,
) -> dict[str, Any]:
    """Fill a single omitted utility when the question names exactly one.

    Skipped for ``us_ignitions`` counts (utility is not that dataset's filter)
    and whenever the model already set ``utility``.
    """
    if not utilities or len(utilities) != 1:
        return arguments
    if arguments.get("utility") not in (None, ""):
        return arguments
    if tool not in {
        "data_query_records",
        "data_query_spatial",
        "visualization_create",
        "visualization_inspect",
    }:
        return arguments
    filled = dict(arguments)
    if tool == "data_query_records" and filled.get("dataset") == "us_ignitions":
        return filled
    filled["utility"] = utilities[0]
    if (
        tool == "visualization_create"
        and filled.get("dataset") == "us_ignitions"
    ):
        # Utility-scoped trend is CPUC ignitions, not the US sample.
        filled["dataset"] = "ignitions"
    return filled


def prepare_tool_arguments(
    tool: str,
    arguments: dict[str, Any],
    *,
    year: int | None = None,
    years: list[int] | None = None,
    utilities: list[str] | None = None,
    fill_aliases: bool = True,
    fill_year: bool = False,
    fill_utility: bool = False,
    repair_comparison: bool = True,
) -> dict[str, Any]:
    """Apply selected harness repairs. Flags isolate which fix is under test."""
    normalized = dict(arguments)
    if fill_aliases:
        normalized = normalize_model_arguments(tool, normalized)
    if fill_utility:
        normalized = fill_utility_from_slot(
            tool, normalized, utilities=utilities
        )
    if tool == "comparison_run" and repair_comparison:
        normalized = repair_comparison_kind(
            normalized, year=year, years=years, utilities=utilities
        )
    if fill_year:
        normalized = fill_year_from_slot(tool, normalized, year=year)
    return normalized
