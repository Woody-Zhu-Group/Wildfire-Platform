"""The CAL FIRE utility-tag caveat (``calfire_untagged_utility``).

Two cases, both shown only when their number is above zero:

- A plain utility filter (one named utility by its tag, untagged incidents
  not included) counts no untagged incident. The caveat states how many CAL
  FIRE incidents in the same period and scope have no utility tag and so are
  not counted toward any utility (``untagged_incidents_excluded``).
- Any other utility scope (``include_untagged``, ``utility=untagged``, a
  territory spatial summary, a utility comparison, or a period comparison
  scoped to a utility) states how many counted incidents have no utility tag
  (``untagged_incidents_counted``).

A result that does not report the figure its case needs suppresses the answer.
The PG&E and SCE numbers below are the 2017 and 2020 values that
``tests/test_calfire_default.py`` checks against SQL.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.shared.dataset_registry import (
    CALFIRE_UNTAGGED_COUNTED_KEY,
    CALFIRE_UNTAGGED_EXCLUDED_KEY,
    CALFIRE_UNTYPED_COUNTED_KEY,
)


def _execution(tool: str, arguments: dict[str, Any], summary: dict[str, Any], **kwargs):
    from services.agent.tools import ToolExecution

    return ToolExecution(
        tool=tool,
        arguments=arguments,
        ok=True,
        summary=summary,
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        **kwargs,
    )


def _records(
    utility: str | None,
    *,
    counted: int | None = 0,
    excluded: int | None = None,
    include_untagged: bool = False,
    year: int = 2020,
    total: int = 0,
    tool: str = "data_query_records",
    **kwargs,
):
    metadata: dict[str, Any] = {CALFIRE_UNTYPED_COUNTED_KEY: 0}
    if counted is not None:
        metadata[CALFIRE_UNTAGGED_COUNTED_KEY] = counted
    if excluded is not None:
        metadata[CALFIRE_UNTAGGED_EXCLUDED_KEY] = excluded
    arguments: dict[str, Any] = {"dataset": "calfire_incidents", "result_mode": "count", "year": year}
    if utility:
        arguments["utility"] = utility
    if include_untagged:
        arguments["include_untagged"] = True
    return _execution(
        tool,
        arguments,
        {
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "total": total,
            "returned": 1,
            "filters": dict(arguments),
            "records": [],
            "metadata": metadata,
        },
        **kwargs,
    )


def _collect(executions):
    from services.agent.artifacts import ArtifactStore
    from services.agent.caveats import collect_qualifications
    from services.agent.config import AgentSettings
    from services.agent.tools import ToolExecutor

    executor = ToolExecutor(AgentSettings.from_env(), ArtifactStore(60))
    quals, _companions, error = asyncio.run(
        collect_qualifications(executions, executor, request_id="untagged", start_attempt=1)
    )
    return {item["id"]: item["text"] for item in quals}, error


# ------------------------------------------------ plain utility filter


@pytest.mark.parametrize(
    "utility,year,total,excluded",
    [("PGE", 2017, 257, 31), ("SCE", 2017, 96, 31), ("PGE", 2020, 162, 26), ("SCE", 2020, 53, 26)],
)
def test_plain_filter_states_the_untagged_incidents_left_out(utility, year, total, excluded):
    quals, error = _collect([_records(utility, year=year, total=total, excluded=excluded)])
    assert error is None
    assert quals["calfire_untagged_utility"] == (
        f"{excluded} CAL FIRE incidents in the same period and scope have no utility tag "
        "recorded and are not counted toward any utility."
    )
    assert chr(0x2014) not in quals["calfire_untagged_utility"]


def test_plain_filter_rank_and_map_are_plain_filters_too():
    for tool in ("data_query_rank", "visualization_create"):
        quals, error = _collect([_records("PGE", excluded=26, tool=tool)])
        assert error is None
        assert quals["calfire_untagged_utility"].startswith("26 CAL FIRE incidents in the same")


def test_plain_filter_singular_and_several_results():
    quals, _ = _collect([_records("PGE", excluded=1)])
    assert quals["calfire_untagged_utility"] == (
        "1 CAL FIRE incident in the same period and scope has no utility tag "
        "recorded and is not counted toward any utility."
    )
    quals, _ = _collect([_records("PGE", year=2017, excluded=31), _records("SCE", excluded=26)])
    assert quals["calfire_untagged_utility"] == (
        "CAL FIRE incidents in the same period and scope with no utility tag recorded, "
        "not counted toward any utility: 31, 26 across these results."
    )


def test_plain_filter_with_no_untagged_incidents_in_scope_has_no_caveat():
    quals, error = _collect([_records("PGE", excluded=0)])
    assert error is None
    assert "calfire_untagged_utility" not in quals


def test_plain_filter_without_the_excluded_figure_suppresses_the_answer():
    _quals, error = _collect([_records("PGE", excluded=None)])
    assert error == "CAL FIRE result did not report how many incidents in its scope have no utility tag."


# ------------------------------------------------ other utility scopes (unchanged)


def test_include_untagged_states_the_counted_untagged_incidents():
    # SCE 2020 with untagged incidents included: 79 counted, 26 untagged.
    quals, error = _collect([_records("SCE", counted=26, include_untagged=True, total=79)])
    assert error is None
    assert quals["calfire_untagged_utility"] == (
        "26 of the counted CAL FIRE incidents have no utility tag recorded, "
        "so the tag does not attribute them to any utility."
    )
    quals, _ = _collect([_records("SCE", counted=1, include_untagged=True)])
    assert quals["calfire_untagged_utility"].startswith("1 of the counted CAL FIRE incidents has ")


def test_untagged_filter_states_the_counted_untagged_incidents():
    quals, error = _collect([_records("untagged", counted=281)])
    assert error is None
    assert quals["calfire_untagged_utility"].startswith("281 of the counted CAL FIRE incidents have ")


def test_not_attached_without_a_utility_scope():
    quals, error = _collect([_records(None, counted=281)])
    assert error is None
    assert "calfire_untagged_utility" not in quals


def test_utility_scoped_result_without_the_counted_figure_suppresses_the_answer():
    _quals, error = _collect([_records("SCE", counted=None, include_untagged=True)])
    assert error == "CAL FIRE result did not report how many counted incidents have no utility tag."


def test_statewide_result_without_the_figures_is_not_suppressed():
    _quals, error = _collect([_records(None, counted=None)])
    assert error is None


def test_qualification_companions_do_not_add_the_caveat():
    quals, _ = _collect([_records("SCE", excluded=26, qualification_call=True)])
    assert "calfire_untagged_utility" not in quals


def test_utility_comparison_and_territory_summary_are_utility_scoped():
    comparison = _execution(
        "comparison_run",
        {"kind": "utilities", "utilities": ["PGE", "SCE"], "metric": "calfire_incident_count"},
        {
            "kind": "utilities",
            "metric": "calfire_incident_count",
            "results": [],
            "metadata": {CALFIRE_UNTYPED_COUNTED_KEY: 0, CALFIRE_UNTAGGED_COUNTED_KEY: 3},
        },
    )
    quals, error = _collect([comparison])
    assert error is None
    assert quals["calfire_untagged_utility"].startswith("3 of the counted")

    periods = _execution(
        "comparison_run",
        {"kind": "periods", "scope_type": "utility", "scope": "PGE", "metric": "acres_burned"},
        {
            "kind": "periods",
            "metric": "acres_burned",
            "metadata": {CALFIRE_UNTYPED_COUNTED_KEY: 0, CALFIRE_UNTAGGED_COUNTED_KEY: 4},
        },
    )
    quals, _ = _collect([periods])
    assert quals["calfire_untagged_utility"].startswith("4 of the counted")

    summary = _execution(
        "data_query_spatial",
        {"kind": "summary", "utility": "SCE", "start_date": "2020-01-01", "end_date": "2020-12-31"},
        {
            "kind": "summary",
            "region": {"kind": "utility", "id": "SCE"},
            "counts": {"ignitions": 0, "epss_outages": 0, "calfire_incidents": 40},
            "metadata": {CALFIRE_UNTYPED_COUNTED_KEY: 0, CALFIRE_UNTAGGED_COUNTED_KEY: 5},
        },
    )
    quals, error = _collect([summary])
    assert error is None
    assert quals["calfire_untagged_utility"].startswith("5 of the counted")


def test_county_comparison_is_not_utility_scoped():
    comparison = _execution(
        "comparison_run",
        {"kind": "regions", "region_type": "county", "regions": ["Butte"], "metric": "calfire_incident_count"},
        {
            "kind": "regions",
            "metric": "calfire_incident_count",
            "results": [],
            "metadata": {CALFIRE_UNTYPED_COUNTED_KEY: 0, CALFIRE_UNTAGGED_COUNTED_KEY: 9},
        },
    )
    quals, _ = _collect([comparison])
    assert "calfire_untagged_utility" not in quals


def test_tool_summary_keeps_the_untagged_meta():
    from services.agent.tools import _select_metadata

    kept = _select_metadata({CALFIRE_UNTAGGED_COUNTED_KEY: 5, CALFIRE_UNTAGGED_EXCLUDED_KEY: 26})
    assert kept == {CALFIRE_UNTAGGED_COUNTED_KEY: 5, CALFIRE_UNTAGGED_EXCLUDED_KEY: 26}
