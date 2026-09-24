"""Deterministic qualifications derived from service metadata and companion reads."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from services.agent.tools import ToolExecution, ToolExecutor
from services.shared.dataset_registry import DATASETS

# Static catalog text. Dynamic caveats (CAL FIRE missingness counts,
# US sample notes from service meta, ignition definition pairs) format
# additional strings at collect time. Registry stores ids only.
CAVEAT_TEXT = {
    "cpuc_utility_caused": (
        "CPUC ignitions in this warehouse are utility-caused / "
        "utility-attributed only; they are not all-cause wildfire "
        "counts and are not comparable to CAL FIRE or US ignitions."
    ),
    "us_ignitions_sample": (
        "US ignitions are an all-cause FireCastRL classification sample, "
        "not a complete census and not comparable to CPUC utility ignitions."
    ),
    "epss_pge_only": (
        "EPSS data in this warehouse is PG&E-only. Other utilities are "
        "not zero; comparison results must be null with a reason."
    ),
    "calfire_map_feed_counts": (
        "CAL FIRE rows in this warehouse are the fire.ca.gov incident-map "
        "feed, not CAL FIRE's Redbook census. The map's posting threshold "
        "dropped in 2024 (median acreage 70 to 43; sub-100-acre incidents "
        "71 to 422). The 133 to 611 Wildfire/Fire count change is a posting "
        "change, not a change in fire occurrence. Acreage totals in the feed "
        "track the Redbook at 95–97% in both years, so acre-based comparisons "
        "remain valid; count-based year-to-year comparisons do not."
    ),
    "cnhpp_grid_resolution": (
        "cNHPP is fitted on a 0.24° (~25 km) grid, coarser than circuits. "
        "Place answers are cell aggregates, not circuit-level risk."
    ),
    "cnhpp_contagion_tie": (
        "cNHPP vs NHPP out-of-sample ΔLL is a statistical tie; the "
        "contagion term is not contributing. Regional P(≥1) assumes "
        "independent Poisson cells."
    ),
        "cnhpp_cell_461": (
        "Grid cell 461 has no vegetation data (all-NaN NDVI/fm100); "
        "its score is mean-filled on those covariates."
    ),
}


async def collect_qualifications(
    executions: list[ToolExecution],
    executor: ToolExecutor,
    *,
    request_id: str,
    start_attempt: int,
    question: str | None = None,
) -> tuple[list[dict[str, str]], list[ToolExecution], str | None]:
    """Attach qualifications from successful tool evidence.

    Companions (attribute/spatial pairs, CAL FIRE metadata, CPUC↔US sample
    reads) are fetched here so caveat coverage does not depend on which
    routing path or how many primary tools the model happened to emit.
    """
    qualifications: list[dict[str, str]] = []
    companion: list[ToolExecution] = []
    seen: set[str] = set()
    attempt = start_attempt

    def add(identifier: str, text: str, source: str) -> None:
        if identifier in seen:
            return
        seen.add(identifier)
        qualifications.append({"id": identifier, "text": text, "source": source})

    working = [item for item in executions if item.ok]
    cross, attempt, cross_error = await _ensure_cpuc_us_companions(
        working,
        executor,
        question=question,
        request_id=request_id,
        attempt=attempt,
    )
    if cross_error:
        return qualifications, companion + cross, cross_error
    companion.extend(cross)
    working = working + [item for item in cross if item.ok]

    for execution in working:
        summary = execution.summary
        metadata = summary.get("metadata") or {}

        if getattr(execution, "stripped_utilities", None):
            names = ", ".join(execution.stripped_utilities)
            add(
                "utility_filter_stripped",
                (
                    f"Ignored invented utility filter(s) {names} that were not "
                    "named in the question. Place names are not IOUs."
                ),
                "harness_utility_grounding",
            )

        # Path-independent: any successful CPUC ignition read gets the
        # utility-caused definition caveat (det and model answer_origin alike).
        if _is_cpuc_ignitions(execution):
            for cid in DATASETS["cpuc_ignitions"].caveat_ids:
                add(cid, CAVEAT_TEXT[cid], "dataset_definition")

        # US ignitions carry complete sample/census caveats in metadata.
        if _is_us_ignitions(execution):
            geography = metadata.get("sample_geography") or {}
            note = geography.get("note")
            base = metadata.get("notes") or CAVEAT_TEXT["us_ignitions_sample"]
            if note:
                base = f"{base} {note}"
            for cid in DATASETS["us_ignitions"].caveat_ids:
                add(cid, str(base), "service_response.meta")

        # CAL FIRE metadata is present on data_query but not all viz/comparison calls.
        if _uses_calfire(execution):
            null_types = metadata.get("null_incident_type_count")
            null_utility = metadata.get("null_utility_records_in_table")
            if null_types is None or null_utility is None:
                attempt += 1
                extra = await executor.execute(
                    "data_query_records",
                    {
                        "dataset": "calfire_incidents",
                        "result_mode": "count",
                        "incident_type_mode": "all",
                        "limit": 1,
                    },
                    request_id=request_id,
                    attempt=attempt,
                    qualification_call=True,
                )
                companion.append(extra)
                if not extra.ok:
                    return (
                        qualifications,
                        companion,
                        "CAL FIRE qualification metadata could not be retrieved.",
                    )
                extra_meta = extra.summary.get("metadata") or {}
                null_types = extra_meta.get("null_incident_type_count")
                null_utility = extra_meta.get("null_utility_records_in_table")
            if null_types is None or null_utility is None:
                return (
                    qualifications,
                    companion,
                    "CAL FIRE qualification metadata was incomplete.",
                )
            add(
                "calfire_missingness",
                (
                    f"CAL FIRE has {int(null_types):,} records without incident type "
                    f"and {int(null_utility):,} without utility tags; default counts "
                    "include only Wildfire/Fire incident types."
                ),
                "service_response.meta",
            )

        if _uses_epss(execution):
            for cid in DATASETS["epss_outages"].caveat_ids:
                add(cid, CAVEAT_TEXT[cid], "service_response.meta")

        # General utility-scoped ignition ambiguity: every utility and date range.
        if _is_attribute_utility_ignition(execution):
            utilities = _utility_scopes(execution)
            if utilities:
                companion_args = _spatial_companion_args(execution, utilities[0])
                if companion_args is None:
                    return (
                        qualifications,
                        companion,
                        "Could not construct the required spatial ignition comparison.",
                    )
                attempt += 1
                extra = await executor.execute(
                    companion_args[0],
                    companion_args[1],
                    request_id=request_id,
                    attempt=attempt,
                    qualification_call=True,
                )
                companion.append(extra)
                if not extra.ok:
                    return (
                        qualifications,
                        companion,
                        "Spatial ignition qualification call failed; answer suppressed.",
                    )
                pairs = _definition_pairs(execution, extra, utilities)
                if not pairs:
                    return (
                        qualifications,
                        companion,
                        "Attribute/spatial ignition values were incomplete.",
                    )
                for label, utility, attribute_value, spatial_value in pairs:
                    add(
                        f"ignition_definition_{label.lower().replace(' ', '_')}",
                        (
                            f"{label} utility-attributed CPUC ignitions: "
                            f"{attribute_value:,}. Ignitions spatially contained in the "
                            f"{utility} territory for the same period: {spatial_value:,}. "
                            "These are different definitions, not interchangeable counts."
                        ),
                        "companion_service_call",
                    )

        # Spatial comparison_run is the inverse: primary is spatial, companion is
        # attribute-tagged. Without this branch, territory-based comparisons
        # silently omit the dual-definition caveat.
        if _is_spatial_comparison_ignition(execution):
            utilities = _utility_scopes(execution)
            if utilities:
                companion_args = _attribute_companion_args(execution)
                if companion_args is None:
                    return (
                        qualifications,
                        companion,
                        "Could not construct the required attribute ignition comparison.",
                    )
                attempt += 1
                extra = await executor.execute(
                    companion_args[0],
                    companion_args[1],
                    request_id=request_id,
                    attempt=attempt,
                    qualification_call=True,
                )
                companion.append(extra)
                if not extra.ok:
                    return (
                        qualifications,
                        companion,
                        "Attribute ignition qualification call failed; answer suppressed.",
                    )
                pairs = _definition_pairs(extra, execution, utilities)
                if not pairs:
                    return (
                        qualifications,
                        companion,
                        "Attribute/spatial ignition values were incomplete.",
                    )
                for label, utility, attribute_value, spatial_value in pairs:
                    add(
                        f"ignition_definition_{label.lower().replace(' ', '_')}",
                        (
                            f"{label} utility-attributed CPUC ignitions: "
                            f"{attribute_value:,}. Ignitions spatially contained in the "
                            f"{utility} territory for the same period: {spatial_value:,}. "
                            "These are different definitions, not interchangeable counts."
                        ),
                        "companion_service_call",
                    )

        # A primary spatial utility summary is equally ambiguous; fetch the
        # matching attribute-tagged count and report both definitions.
        if _is_spatial_utility_ignition(execution):
            region = execution.summary.get("region") or {}
            utility = str(region.get("id") or "")
            start = execution.summary.get("start_date")
            end = execution.summary.get("end_date")
            attempt += 1
            extra = await executor.execute(
                "data_query_records",
                {
                    "dataset": "cpuc_ignitions",
                    "result_mode": "count",
                    "utility": utility,
                    "start_date": start,
                    "end_date": end,
                    "limit": 1,
                },
                request_id=request_id,
                attempt=attempt,
                qualification_call=True,
            )
            companion.append(extra)
            if not extra.ok:
                return (
                    qualifications,
                    companion,
                    "Attribute ignition qualification call failed; answer suppressed.",
                )
            pairs = _definition_pairs(extra, execution, [utility])
            if not pairs:
                return (
                    qualifications,
                    companion,
                    "Attribute/spatial ignition values were incomplete.",
                )
            label, util, attribute_value, spatial_value = pairs[0]
            add(
                f"ignition_definition_{label.lower()}",
                (
                    f"{label} utility-attributed CPUC ignitions: "
                    f"{attribute_value:,}. Ignitions spatially contained in the "
                    f"{util} territory for the same period: {spatial_value:,}. "
                    "These are different definitions, not interchangeable counts."
                ),
                "companion_service_call",
            )

    if _needs_cnhpp_risk_caveats(working):
        add(
            "cnhpp_grid_resolution",
            CAVEAT_TEXT["cnhpp_grid_resolution"],
            "model_limitation",
        )
        add(
            "cnhpp_contagion_tie",
            CAVEAT_TEXT["cnhpp_contagion_tie"],
            "model_limitation",
        )
        if _risk_includes_cell_461(working):
            add(
                "cnhpp_cell_461",
                CAVEAT_TEXT["cnhpp_cell_461"],
                "data_gap",
            )

    if _needs_calfire_map_feed_caveat(working):
        cid = "calfire_map_feed_counts"
        if cid in DATASETS["calfire_incidents"].caveat_ids:
            add(cid, CAVEAT_TEXT[cid], "dataset_definition")

    return qualifications, companion, None


def _asks_cpuc_us_compare(question: str | None) -> bool:
    """True when the user asks to compare CPUC and US ignition figures."""
    if not question:
        return False
    lower = " ".join(question.lower().split())
    has_cpuc = bool(re.search(r"\bcpuc\b", lower))
    has_us = bool(
        re.search(r"\b(?:us|national)\s+ignitions?\b", lower)
        or (re.search(r"\bus\b", lower) and re.search(r"\bignitions?\b", lower))
    )
    return has_cpuc and has_us


def _time_filter_from_execution(execution: ToolExecution) -> dict[str, Any]:
    args = execution.arguments or {}
    out: dict[str, Any] = {}
    for key in ("year", "start_date", "end_date"):
        value = args.get(key)
        if value is not None and value != "":
            out[key] = value
    return out


async def _ensure_cpuc_us_companions(
    executions: list[ToolExecution],
    executor: ToolExecutor,
    *,
    question: str | None,
    request_id: str,
    attempt: int,
) -> tuple[list[ToolExecution], int, str | None]:
    """Fetch the missing side of a CPUC↔US compare so sample caveats can attach.

    Model routing often synthesizes after the first successful CPUC count and
    never calls US. Caveats must not depend on that second primary call.
    """
    if not _asks_cpuc_us_compare(question):
        return [], attempt, None
    if not executions:
        return [], attempt, None

    has_cpuc = any(_is_cpuc_ignitions(item) for item in executions)
    has_us = any(_is_us_ignitions(item) for item in executions)
    if has_cpuc and has_us:
        return [], attempt, None

    seed = executions[0]
    for item in executions:
        if (not has_us and _is_cpuc_ignitions(item)) or (
            not has_cpuc and _is_us_ignitions(item)
        ):
            seed = item
            break
    time_args = _time_filter_from_execution(seed)
    if not time_args:
        return (
            [],
            attempt,
            "CPUC/US comparison lacks a time window for the missing dataset read.",
        )

    added: list[ToolExecution] = []
    if not has_us:
        attempt += 1
        extra = await executor.execute(
            "data_query_records",
            {
                "dataset": "us_ignitions",
                "result_mode": "count",
                "limit": 1,
                **time_args,
            },
            request_id=request_id,
            attempt=attempt,
            qualification_call=True,
        )
        added.append(extra)
        if not extra.ok:
            return (
                added,
                attempt,
                "US ignitions qualification call failed; answer suppressed.",
            )
    if not has_cpuc:
        attempt += 1
        extra = await executor.execute(
            "data_query_records",
            {
                "dataset": "cpuc_ignitions",
                "result_mode": "count",
                "limit": 1,
                **time_args,
            },
            request_id=request_id,
            attempt=attempt,
            qualification_call=True,
        )
        added.append(extra)
        if not extra.ok:
            return (
                added,
                attempt,
                "CPUC ignitions qualification call failed; answer suppressed.",
            )
    return added, attempt, None


def _is_cpuc_ignitions(execution: ToolExecution) -> bool:
    """True for warehouse CPUC ignition reads (records, maps, ignition metrics)."""
    if execution.qualification_call:
        return False
    dataset = execution.summary.get("dataset")
    if dataset in {"cpuc_ignitions", "ignitions"}:
        return True
    return (
        execution.tool == "comparison_run"
        and execution.summary.get("metric") in {
            "ignition_count",
            "epss_to_ignition_ratio",
        }
    )


def _is_us_ignitions(execution: ToolExecution) -> bool:
    """True for US sample reads, including qualification companions."""
    return execution.summary.get("dataset") == "us_ignitions"


def _uses_calfire(execution: ToolExecution) -> bool:
    summary = execution.summary
    if summary.get("dataset") == "calfire_incidents":
        return True
    if summary.get("dataset") == "calfire":
        return True
    return summary.get("metric") in {"calfire_incident_count", "acres_burned"}


def _year_from_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _calfire_covered_years(execution: ToolExecution) -> set[int] | None:
    """Inclusive calendar years on a primary CAL FIRE call.

    ``None`` means the call has no time filter (full table, includes 2023–2024).
    Qualification companions are ignored by the caller.
    """
    args = execution.arguments or {}
    filters = (execution.summary or {}).get("filters") or {}
    summary = execution.summary or {}
    years: set[int] = set()
    for source in (args, filters, summary):
        year = _year_from_value(source.get("year"))
        if year is not None:
            years.add(year)
        for start_key, end_key in (
            ("start_date", "end_date"),
            ("period_a_start", "period_a_end"),
            ("period_b_start", "period_b_end"),
        ):
            start = _year_from_value(source.get(start_key))
            end = _year_from_value(source.get(end_key))
            if start is not None and end is not None:
                years.update(range(min(start, end), max(start, end) + 1))
            elif start is not None:
                years.add(start)
            elif end is not None:
                years.add(end)
    return years or None


def _is_calfire_count_comparison(execution: ToolExecution) -> bool:
    """True when the CAL FIRE result is a count (not acres)."""
    metric = (execution.summary or {}).get("metric") or execution.arguments.get("metric")
    if metric == "acres_burned":
        return False
    if metric == "calfire_incident_count":
        return True
    if (execution.summary or {}).get("kind") == "time_series":
        return True
    if execution.tool == "data_query_rank":
        return (execution.summary or {}).get("metric") == "count"
    return execution.tool == "data_query_records"


def _is_risk_forecast(execution: ToolExecution) -> bool:
    return (
        execution.tool in {"risk_forecast", "risk_surface"}
        and execution.ok
        and not execution.qualification_call
    )


def _needs_cnhpp_risk_caveats(executions: list[ToolExecution]) -> bool:
    return any(_is_risk_forecast(item) for item in executions)


def _risk_includes_cell_461(executions: list[ToolExecution]) -> bool:
    for item in executions:
        if not _is_risk_forecast(item):
            continue
        summary = item.summary or {}
        if summary.get("includes_cell_461"):
            return True
        if summary.get("cell_id") == 461:
            return True
        cell_ids = summary.get("cell_ids") or []
        if 461 in cell_ids:
            return True
        args = item.arguments or {}
        if args.get("cell_id") == 461:
            return True
    return False


def _needs_calfire_map_feed_caveat(executions: list[ToolExecution]) -> bool:
    """Attach when a CAL FIRE answer spans 2023–2024 or compares counts across years."""
    count_years: set[int] = set()
    spans_boundary = False
    for item in executions:
        if item.qualification_call or not item.ok or not _uses_calfire(item):
            continue
        covered = _calfire_covered_years(item)
        if covered is None or (2023 in covered and 2024 in covered):
            spans_boundary = True
        if _is_calfire_count_comparison(item):
            if covered is None:
                count_years.update((2023, 2024))
            else:
                count_years.update(covered)
    return spans_boundary or len(count_years) >= 2


def _uses_epss(execution: ToolExecution) -> bool:
    summary = execution.summary
    if summary.get("dataset") in {"epss_outages", "epss"}:
        return True
    return summary.get("metric") in {
        "epss_outage_count",
        "epss_to_ignition_ratio",
    }


def _comparison_ignition_definition(execution: ToolExecution) -> str:
    metadata = execution.summary.get("metadata") or {}
    return str(
        execution.arguments.get("ignition_definition")
        or metadata.get("ignition_definition")
        or "attribute"
    )


def _is_attribute_utility_ignition(execution: ToolExecution) -> bool:
    summary = execution.summary
    if (
        execution.tool in {"data_query_records", "data_query_rank"}
        and summary.get("dataset") == "cpuc_ignitions"
        and execution.arguments.get("utility")
    ):
        return True
    if execution.tool != "comparison_run" or summary.get("metric") != "ignition_count":
        return False
    return _comparison_ignition_definition(execution) == "attribute" and (
        execution.arguments.get("kind") in {"utilities", "periods"}
    )


def _is_spatial_comparison_ignition(execution: ToolExecution) -> bool:
    if execution.tool != "comparison_run" or execution.summary.get("metric") != "ignition_count":
        return False
    return _comparison_ignition_definition(execution) == "spatial" and (
        execution.arguments.get("kind") in {"utilities", "periods"}
    )


def _is_spatial_utility_ignition(execution: ToolExecution) -> bool:
    if execution.tool != "data_query_spatial":
        return False
    if execution.summary.get("kind") != "summary":
        return False
    region = execution.summary.get("region") or {}
    counts = execution.summary.get("counts") or {}
    return region.get("kind") == "utility" and "ignitions" in counts


def _utility_scopes(execution: ToolExecution) -> list[str]:
    if execution.tool in {"data_query_records", "data_query_rank"}:
        value = execution.arguments.get("utility")
        return [str(value)] if value and value != "untagged" else []
    if execution.arguments.get("kind") == "periods":
        if execution.arguments.get("scope_type") == "utility":
            return [str(execution.arguments.get("scope"))]
        return []
    utilities = execution.arguments.get("utilities") or []
    return [str(item) for item in utilities]


def _spatial_companion_args(
    execution: ToolExecution, utility: str
) -> tuple[str, dict[str, Any]] | None:
    if execution.tool in {"data_query_records", "data_query_rank"}:
        year = execution.arguments.get("year")
        start = execution.arguments.get("start_date")
        end = execution.arguments.get("end_date")
        if year is not None:
            start, end = f"{year}-01-01", f"{year}-12-31"
        start = start or date(1900, 1, 1).isoformat()
        end = end or date(2100, 12, 31).isoformat()
        return (
            "data_query_spatial",
            {
                "kind": "summary",
                "utility": utility,
                "start_date": start,
                "end_date": end,
            },
        )
    args = dict(execution.arguments)
    args["ignition_definition"] = "spatial"
    return "comparison_run", args


def _attribute_companion_args(
    execution: ToolExecution,
) -> tuple[str, dict[str, Any]] | None:
    if execution.tool != "comparison_run":
        return None
    args = dict(execution.arguments)
    args["ignition_definition"] = "attribute"
    return "comparison_run", args


def _definition_pairs(
    attribute: ToolExecution,
    spatial: ToolExecution,
    utilities: list[str],
) -> list[tuple[str, str, int | float, int | float]]:
    if attribute.tool == "data_query_records":
        a_value = attribute.summary.get("total")
        s_value = (spatial.summary.get("counts") or {}).get("ignitions")
        if a_value is None or s_value is None:
            return []
        return [(utilities[0], utilities[0], a_value, s_value)]

    if attribute.arguments.get("kind") == "periods":
        pairs = []
        utility = utilities[0]
        for key, label in (("period_a", f"{utility} period A"), ("period_b", f"{utility} period B")):
            a_value = (attribute.summary.get(key) or {}).get("value")
            s_value = (spatial.summary.get(key) or {}).get("value")
            if a_value is None or s_value is None:
                return []
            pairs.append((label, utility, a_value, s_value))
        return pairs

    a_values = {
        str(row.get("key")): row.get("value")
        for row in attribute.summary.get("results") or []
    }
    s_values = {
        str(row.get("key")): row.get("value")
        for row in spatial.summary.get("results") or []
    }
    pairs = []
    for utility in utilities:
        if a_values.get(utility) is None or s_values.get(utility) is None:
            return []
        pairs.append((utility, utility, a_values[utility], s_values[utility]))
    return pairs
