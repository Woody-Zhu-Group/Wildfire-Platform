"""The CAL FIRE untyped caveat: how many counted incidents have no type recorded.

The default counts every CAL FIRE incident except the registry's non-wildfire
types, so incidents with no type are counted. Every CAL FIRE result reports
``untyped_incidents_counted``; the caveat (id ``calfire_missingness``) appears
only when that figure is above zero, and a CAL FIRE result that does not
report it suppresses the answer rather than guess.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.shared.dataset_registry import CALFIRE_UNTYPED_COUNTED_KEY


def _execution(
    tool: str,
    summary: dict[str, Any],
    *,
    qualification_call: bool = False,
    arguments: dict[str, Any] | None = None,
):
    from services.agent.tools import ToolExecution

    return ToolExecution(
        tool=tool,
        arguments=arguments or {},
        ok=True,
        summary=summary,
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        qualification_call=qualification_call,
    )


def _calfire_count(untyped: int | None, *, total: int = 259, **kwargs):
    metadata: dict[str, Any] = {}
    if untyped is not None:
        metadata[CALFIRE_UNTYPED_COUNTED_KEY] = untyped
    return _execution(
        "data_query_records",
        {
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "total": total,
            "returned": 1,
            "filters": {"year": 2020},
            "records": [],
            "metadata": metadata,
        },
        arguments={"dataset": "calfire_incidents", "result_mode": "count", "year": 2020},
        **kwargs,
    )


def _collect(executions):
    from services.agent.artifacts import ArtifactStore
    from services.agent.caveats import collect_qualifications
    from services.agent.config import AgentSettings
    from services.agent.tools import ToolExecutor

    executor = ToolExecutor(AgentSettings.from_env(), ArtifactStore(60))
    quals, _companions, error = asyncio.run(
        collect_qualifications(executions, executor, request_id="untyped", start_attempt=1)
    )
    return {item["id"]: item["text"] for item in quals}, error


def test_caveat_states_the_counted_untyped_incidents():
    # 2017 statewide: 429 counted, 418 with no type recorded.
    quals, error = _collect([_calfire_count(418, total=429)])
    assert error is None
    assert quals["calfire_missingness"] == (
        "418 of the counted CAL FIRE incidents have no incident type recorded. "
        "CAL FIRE counts include every incident except the known non-wildfire "
        "types (Earthquake, Flood, Hazmat), so incidents with no type are counted."
    )
    assert chr(0x2014) not in quals["calfire_missingness"]


def test_caveat_uses_thousands_separators_and_singular_grammar():
    quals, _ = _collect([_calfire_count(1234)])
    assert quals["calfire_missingness"].startswith("1,234 of the counted CAL FIRE incidents have ")
    quals, _ = _collect([_calfire_count(1)])
    assert quals["calfire_missingness"].startswith("1 of the counted CAL FIRE incidents has ")


def test_caveat_is_absent_when_no_counted_incident_is_untyped():
    quals, error = _collect([_calfire_count(0)])
    assert error is None
    assert "calfire_missingness" not in quals


def test_caveat_lists_each_result_when_several_report_untyped():
    quals, _ = _collect([_calfire_count(418), _calfire_count(0), _calfire_count(274)])
    assert quals["calfire_missingness"].startswith(
        "Counted CAL FIRE incidents with no incident type recorded: 418, 274 across these results."
    )


def test_qualification_companions_do_not_add_the_caveat():
    quals, _ = _collect([_calfire_count(0), _calfire_count(50, qualification_call=True)])
    assert "calfire_missingness" not in quals


def test_calfire_result_without_the_figure_suppresses_the_answer():
    _quals, error = _collect([_calfire_count(None)])
    assert error == "CAL FIRE result did not report how many counted incidents have no type."


def test_calfire_comparison_without_the_figure_suppresses_the_answer():
    comparison = _execution(
        "comparison_run",
        {"kind": "regions", "metric": "calfire_incident_count", "results": [], "metadata": {}},
    )
    _quals, error = _collect([comparison])
    assert error is not None


def test_spatial_summary_calfire_count_carries_the_caveat():
    summary = _execution(
        "data_query_spatial",
        {
            "kind": "summary",
            "region": {"kind": "iou", "id": "PGE"},
            "counts": {"ignitions": 0, "epss_outages": 0, "calfire_incidents": 300},
            "metadata": {CALFIRE_UNTYPED_COUNTED_KEY: 12},
        },
    )
    quals, error = _collect([summary])
    assert error is None
    assert quals["calfire_missingness"].startswith("12 of the counted CAL FIRE incidents have ")


def test_non_calfire_result_needs_no_figure():
    cpuc = _execution(
        "data_query_records",
        {"dataset": "cpuc_ignitions", "result_mode": "count", "total": 5, "metadata": {}},
    )
    quals, error = _collect([cpuc])
    assert "calfire_missingness" not in quals
    assert error is None or "no type" not in error


@pytest.mark.parametrize(
    "meta",
    [
        {CALFIRE_UNTYPED_COUNTED_KEY: 7, "calfire_excluded_incident_types": ["Earthquake"]},
    ],
)
def test_tool_summary_keeps_the_untyped_meta(meta):
    from services.agent.tools import _select_metadata

    kept = _select_metadata(meta)
    assert kept[CALFIRE_UNTYPED_COUNTED_KEY] == 7
    assert kept["calfire_excluded_incident_types"] == ["Earthquake"]


# ------------------------------------------------ the text follows the incident_type_mode used


def _mode_count(untyped: int, *, arg_mode: str | None = None, meta_mode: str | None = None, total: int = 13):
    metadata: dict[str, Any] = {CALFIRE_UNTYPED_COUNTED_KEY: untyped}
    if meta_mode:
        metadata["incident_type_mode"] = meta_mode
    arguments: dict[str, Any] = {"dataset": "calfire_incidents", "result_mode": "count",
                                 "county": "Butte", "year": 2018}
    if arg_mode:
        arguments["incident_type_mode"] = arg_mode
    return _execution(
        "data_query_records",
        {"dataset": "calfire_incidents", "result_mode": "count", "total": total, "returned": 1,
         "filters": dict(arguments), "records": [], "metadata": metadata},
        arguments=arguments,
    )


NON_WILDFIRE_WORDS = ("Earthquake", "Flood", "Hazmat")


def test_all_types_answer_never_claims_types_were_excluded():
    # All types in Butte County in 2018: 13 incidents, including the Flood row,
    # 11 of them with no type (tests/test_calfire_default.py checks the SQL).
    for kwargs in ({"arg_mode": "all"}, {"meta_mode": "all"}):
        quals, error = _collect([_mode_count(11, **kwargs)])
        assert error is None
        text = quals["calfire_missingness"]
        assert text == (
            "11 of the counted CAL FIRE incidents have no incident type recorded. "
            "This count includes every incident type, non-wildfire types too, so "
            "incidents with no type are counted."
        )
        assert not any(word in text for word in NON_WILDFIRE_WORDS)
        assert "except" not in text


def test_untyped_only_answer_never_claims_types_were_excluded():
    for kwargs in ({"arg_mode": "untyped"}, {"meta_mode": "untyped"}):
        quals, _ = _collect([_mode_count(11, total=11, **kwargs)])
        text = quals["calfire_missingness"]
        assert text == (
            "11 of the counted CAL FIRE incidents have no incident type recorded. "
            "This count is of incidents with no type recorded only."
        )
        assert not any(word in text for word in NON_WILDFIRE_WORDS)


def test_default_answer_names_the_excluded_types():
    for kwargs in ({}, {"arg_mode": "wildfire_default"}, {"meta_mode": "default_wildfire"}):
        quals, _ = _collect([_mode_count(11, total=12, **kwargs)])
        assert quals["calfire_missingness"].endswith(
            "CAL FIRE counts include every incident except the known non-wildfire "
            "types (Earthquake, Flood, Hazmat), so incidents with no type are counted."
        )


def test_mixed_modes_get_one_sentence_pair_each():
    quals, _ = _collect([_mode_count(11, total=12), _mode_count(11, arg_mode="all")])
    text = quals["calfire_missingness"]
    assert text.count("11 of the counted CAL FIRE incidents have no incident type recorded.") == 2
    assert "except the known non-wildfire types" in text
    assert "includes every incident type" in text
