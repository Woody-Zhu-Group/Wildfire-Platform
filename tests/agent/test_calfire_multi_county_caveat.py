"""The multi-county CAL FIRE caveat (#78): how many counted incidents list
several counties, and that county totals can exceed the statewide total."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


def _calfire_execution(multi: int | None, *, qualification_call: bool = False):
    from services.agent.tools import ToolExecution

    metadata: dict[str, Any] = {"null_incident_type_count": 1234, "null_utility_records_in_table": 282}
    if multi is not None:
        metadata["multi_county_incidents"] = multi
    return ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "calfire_incidents", "result_mode": "count", "county": "Shasta", "year": 2020},
        ok=True,
        summary={
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "total": 6,
            "returned": 1,
            "filters": {"county": "Shasta", "year": 2020},
            "records": [],
            "metadata": metadata,
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        qualification_call=qualification_call,
    )


def _qualifications(executions) -> dict[str, str]:
    from services.agent.artifacts import ArtifactStore
    from services.agent.caveats import collect_qualifications
    from services.agent.config import AgentSettings
    from services.agent.tools import ToolExecutor

    executor = ToolExecutor(AgentSettings.from_env(), ArtifactStore(60))
    quals, _companions, error = asyncio.run(
        collect_qualifications(executions, executor, request_id="multi-county", start_attempt=1)
    )
    assert error is None
    return {item["id"]: item["text"] for item in quals}


def test_caveat_states_how_many_incidents_span_several_counties():
    quals = _qualifications([_calfire_execution(2)])
    text = quals["calfire_multi_county"]
    assert text.startswith("2 of the counted CAL FIRE incidents list more than one county")
    assert "more than the statewide total" in text
    assert chr(0x2014) not in text


def test_caveat_is_absent_without_multi_county_incidents():
    assert "calfire_multi_county" not in _qualifications([_calfire_execution(0)])
    assert "calfire_multi_county" not in _qualifications([_calfire_execution(None)])


def test_caveat_lists_each_result_when_several_report_multi_county():
    text = _qualifications([_calfire_execution(2), _calfire_execution(1)])["calfire_multi_county"]
    assert text.startswith("Counted CAL FIRE incidents that list more than one county: 2, 1")


def test_tool_summary_keeps_the_multi_county_meta():
    from services.agent.tools import _select_metadata

    assert _select_metadata({"multi_county_incidents": 2, "multi_county_rule": "x"}) == {
        "multi_county_incidents": 2
    }


@pytest.mark.parametrize("n,verb", [(1, "lists"), (5, "list")])
def test_caveat_grammar(n, verb):
    from services.agent.caveats import _multi_county_text

    assert _multi_county_text([n]).startswith(f"{n} of the counted CAL FIRE incidents {verb} ")
