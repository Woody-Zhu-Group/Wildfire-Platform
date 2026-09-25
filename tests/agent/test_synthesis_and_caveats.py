"""Model-path synthesis readability and path-independent CPUC caveats."""

from __future__ import annotations

import asyncio

import pytest

from services.agent.caveats import collect_qualifications, _needs_calfire_map_feed_caveat
from services.agent.orchestrator import _ensure_readable_answer, _render_deterministic
from services.agent.tools import ToolExecution, ToolExecutor
from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings


def test_bare_number_synthesis_expanded_to_prose():
    execution = ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "cpuc_ignitions", "year": 2023, "result_mode": "count"},
        ok=True,
        summary={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 480,
            "returned": 1,
            "filters": {"year": 2023},
            "records": [],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )
    expanded = _ensure_readable_answer("480", [execution])
    assert expanded != "480"
    assert "cpuc_ignitions count: 480" in expanded
    assert "2023" in expanded
    assert "statewide" in expanded
    prose = "There were 480 CPUC utility-caused ignitions statewide in 2023."
    assert _ensure_readable_answer(prose, [execution]) == prose


def test_ungrounded_example_clause_is_stripped():
    execution = ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "cpuc_ignitions", "year": 2024, "result_mode": "count"},
        ok=True,
        summary={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 532,
            "returned": 1,
            "filters": {"year": 2024, "utility": "PGE"},
            "records": [],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )
    with_failure = (
        "CPUC recorded 532 events in PGE during 2024. "
        "One listed example is not available in the records."
    )
    cleaned = _ensure_readable_answer(with_failure, [execution])
    assert "listed example" not in cleaned.lower()
    assert "532" in cleaned
    named = (
        "CAL FIRE recorded 11 incidents in Sacramento County during 2024. "
        "One listed example is the Marsh Fire."
    )
    sample = ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "calfire_incidents", "year": 2024, "result_mode": "records"},
        ok=True,
        summary={
            "dataset": "calfire_incidents",
            "result_mode": "records",
            "total": 11,
            "returned": 1,
            "filters": {"year": 2024, "county": "Sacramento"},
            "records": [{"incident_name": "Marsh Fire"}],
            "sample_examples": ["Marsh Fire"],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        qualification_call=True,
    )
    kept = _ensure_readable_answer(named, [execution, sample])
    assert "Marsh Fire" in kept


def test_preview_arguments_fills_harness_year():
    settings = AgentSettings.from_env()
    executor = ToolExecutor(settings, ArtifactStore(60))
    previewed = executor.preview_arguments(
        "data_query_records",
        {"dataset": "cpuc_ignitions", "result_mode": "count"},
        year=2023,
        years=[2023],
        utilities=[],
        time_resolution={
            "status": "explicit",
            "year": 2023,
            "years": [2023],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert previewed.get("year") == 2023


def test_cpuc_caveat_attaches_without_utility_scope():
    settings = AgentSettings.from_env()
    executor = ToolExecutor(settings, ArtifactStore(60))
    execution = ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "cpuc_ignitions", "year": 2023, "result_mode": "count"},
        ok=True,
        summary={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 480,
            "returned": 1,
            "filters": {"year": 2023},
            "records": [],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )

    async def _run():
        quals, _companions, error = await collect_qualifications(
            [execution], executor, request_id="test", start_attempt=1
        )
        return quals, error

    quals, error = asyncio.run(_run())
    assert error is None
    ids = {item["id"] for item in quals}
    assert "cpuc_utility_caused" in ids


@pytest.mark.requires_service(8000, name="data_query")
def test_cpuc_vs_us_companion_attaches_sample_caveat_without_us_primary():
    """Caveat must not depend on the model emitting a second primary US call."""
    settings = AgentSettings.from_env()
    executor = ToolExecutor(settings, ArtifactStore(60))
    cpuc = ToolExecution(
        tool="data_query_records",
        arguments={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
        ok=True,
        summary={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 741,
            "returned": 1,
            "filters": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
            "records": [],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )

    async def _run():
        return await collect_qualifications(
            [cpuc],
            executor,
            request_id="cpuc-vs-us",
            start_attempt=1,
            question="Compare CPUC and US ignition counts in 2024 and explain the difference.",
        )

    quals, companions, error = asyncio.run(_run())
    assert error is None
    ids = {item["id"] for item in quals}
    assert "us_ignitions_sample" in ids
    assert "cpuc_utility_caused" in ids
    assert any(
        item.ok and item.qualification_call and item.summary.get("dataset") == "us_ignitions"
        for item in companions
    )


def test_invented_utility_strip_surfaces_qualification():
    """Silently stripping an invented IOU must produce a user-visible qualification."""
    execution = ToolExecution(
        tool="data_query_records",
        arguments={
            "dataset": "cpuc_ignitions",
            "year": 2024,
            "result_mode": "count",
            "county": None,
        },
        ok=True,
        summary={
            "dataset": "cpuc_ignitions",
            "result_mode": "count",
            "total": 741,
            "returned": 1,
            "filters": {"year": 2024},
            "records": [],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        stripped_utilities=["SCE"],
    )
    settings = AgentSettings.from_env()
    executor = ToolExecutor(settings, ArtifactStore(60))

    async def _run():
        return await collect_qualifications(
            [execution], executor, request_id="util-strip", start_attempt=1
        )

    quals, _companions, error = asyncio.run(_run())
    assert error is None
    stripped = next(q for q in quals if q["id"] == "utility_filter_stripped")
    assert "SCE" in stripped["text"]
    assert "not named in the question" in stripped["text"]


def _records_execution(total: int, returned: int) -> ToolExecution:
    return ToolExecution(
        tool="data_query_records",
        arguments={
            "dataset": "calfire_incidents",
            "year": 2024,
            "county": "Sacramento",
            "result_mode": "records",
        },
        ok=True,
        summary={
            "dataset": "calfire_incidents",
            "result_mode": "records",
            "total": total,
            "returned": returned,
            "filters": {"year": 2024, "county": "Sacramento"},
            "records": [],
            "metadata": {},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )


def test_records_answer_omits_representative_when_complete():
    text = _render_deterministic([_records_execution(11, 11)])
    assert "11 matching records" in text
    assert "representative" not in text


def test_records_answer_names_truncation_when_previewed():
    text = _render_deterministic([_records_execution(40, 25)])
    assert "40 matching records" in text
    assert "returned 25 representative records" in text


def test_risk_answer_leads_with_rounded_prose_not_formula():
    execution = ToolExecution(
        tool="risk_forecast",
        arguments={"county": "Sacramento", "date": "2024-08-15"},
        ok=True,
        summary={
            "date": "2024-08-15",
            "risk": 0.06624759278909353,
            "expected_count": 0.06854396453478485,
            "aggregation": "p_at_least_one",
            "aggregation_note": (
                "1 - exp(-sum(lambda)); independent Poisson cells; "
                "expected_count is the sum of intensities"
            ),
            "cell_count": 11,
            "scope": {"type": "county", "name": "Sacramento County"},
            "local_percentile": 77.95698924731182,
            "statewide_percentile": 89.56310679611651,
            "local_period": "August 2020–2025",
            "local_n": 186,
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )
    text = _render_deterministic([execution])
    assert text.startswith(
        "Sacramento County had a 6.6% chance of at least one ignition on 2024-08-15."
    )
    assert "78% of August days there since 2020" in text
    assert "90% of California that day" in text
    assert "0.06624759278909353" not in text
    assert "1 - exp" not in text
    assert "expected_count" not in text


def _calfire_count(
    *,
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    qualification_call: bool = False,
    metric: str | None = None,
    kind: str | None = None,
    period_a_start: str | None = None,
    period_a_end: str | None = None,
    period_b_start: str | None = None,
    period_b_end: str | None = None,
    untyped: int = 0,
) -> ToolExecution:
    arguments: dict = {"dataset": "calfire_incidents", "result_mode": "count"}
    filters: dict = {}
    summary: dict = {
        "dataset": "calfire_incidents",
        "result_mode": "count",
        "total": 133,
        "returned": 1,
        "filters": filters,
        "records": [],
        "metadata": {
            "null_incident_type_count": 1234,
            "null_utility_records_in_table": 282,
            "untyped_incidents_counted": untyped,
        },
    }
    if year is not None:
        arguments["year"] = year
        filters["year"] = year
    if start_date:
        arguments["start_date"] = start_date
        filters["start_date"] = start_date
    if end_date:
        arguments["end_date"] = end_date
        filters["end_date"] = end_date
    if metric:
        arguments["metric"] = metric
        summary["metric"] = metric
        summary["dataset"] = None
    if kind:
        arguments["kind"] = kind
    if period_a_start:
        arguments.update(
            {
                "kind": "periods",
                "period_a_start": period_a_start,
                "period_a_end": period_a_end,
                "period_b_start": period_b_start,
                "period_b_end": period_b_end,
            }
        )
    return ToolExecution(
        tool="comparison_run" if metric or kind == "periods" else "data_query_records",
        arguments=arguments,
        ok=True,
        summary=summary,
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        qualification_call=qualification_call,
    )


def test_calfire_map_feed_caveat_triggers():
    assert _needs_calfire_map_feed_caveat([_calfire_count(year=2024)]) is False
    assert _needs_calfire_map_feed_caveat([_calfire_count(year=2023)]) is False
    assert (
        _needs_calfire_map_feed_caveat(
            [_calfire_count(start_date="2023-08-01", end_date="2023-08-31")]
        )
        is False
    )
    assert (
        _needs_calfire_map_feed_caveat(
            [_calfire_count(start_date="2023-01-01", end_date="2024-12-31")]
        )
        is True
    )
    assert (
        _needs_calfire_map_feed_caveat(
            [_calfire_count(year=2023), _calfire_count(year=2024)]
        )
        is True
    )
    assert (
        _needs_calfire_map_feed_caveat(
            [
                _calfire_count(
                    metric="calfire_incident_count",
                    period_a_start="2022-01-01",
                    period_a_end="2022-12-31",
                    period_b_start="2023-01-01",
                    period_b_end="2023-12-31",
                )
            ]
        )
        is True
    )
    assert (
        _needs_calfire_map_feed_caveat(
            [
                _calfire_count(
                    metric="acres_burned",
                    period_a_start="2021-01-01",
                    period_a_end="2021-12-31",
                    period_b_start="2022-01-01",
                    period_b_end="2022-12-31",
                )
            ]
        )
        is False
    )
    assert (
        _needs_calfire_map_feed_caveat(
            [
                _calfire_count(
                    metric="acres_burned",
                    period_a_start="2023-01-01",
                    period_a_end="2023-12-31",
                    period_b_start="2024-01-01",
                    period_b_end="2024-12-31",
                )
            ]
        )
        is True
    )
    assert _needs_calfire_map_feed_caveat([_calfire_count()]) is True
    ignored = _calfire_count(qualification_call=True)
    assert _needs_calfire_map_feed_caveat([ignored, _calfire_count(year=2024)]) is False
    monthly_2024 = _calfire_count(year=2024)
    monthly_2024.summary["kind"] = "time_series"
    assert _needs_calfire_map_feed_caveat([monthly_2024]) is False


def _risk_execution(
    *,
    cell_id: int | None = 400,
    cell_ids: list[int] | None = None,
    includes_cell_461: bool = False,
) -> ToolExecution:
    ids = cell_ids if cell_ids is not None else ([cell_id] if cell_id is not None else [])
    return ToolExecution(
        tool="risk_forecast",
        arguments={"cell_id": cell_id, "date": "2024-08-15"} if cell_id is not None else {
            "county": "Sacramento",
            "date": "2024-08-15",
        },
        ok=True,
        summary={
            "cell_id": cell_id,
            "date": "2024-08-15",
            "risk": 0.00464,
            "cell_ids": ids,
            "cell_count": len(ids) or 1,
            "includes_cell_461": includes_cell_461,
            "scope": {"type": "cell", "name": f"cell {cell_id}"},
        },
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )


def test_cnhpp_risk_caveats_attach_and_cell_461_is_conditional():
    settings = AgentSettings.from_env()
    executor = ToolExecutor(settings, ArtifactStore(60))

    async def _ids(execution: ToolExecution) -> set[str]:
        quals, _companions, error = await collect_qualifications(
            [execution], executor, request_id="cnhpp-risk", start_attempt=1
        )
        assert error is None
        return {item["id"] for item in quals}

    ordinary = asyncio.run(_ids(_risk_execution(cell_id=400)))
    assert "cnhpp_grid_resolution" in ordinary
    assert "cnhpp_contagion_tie" in ordinary
    assert "cnhpp_cell_461" not in ordinary

    with_461 = asyncio.run(
        _ids(_risk_execution(cell_id=461, includes_cell_461=True))
    )
    assert "cnhpp_cell_461" in with_461

    county_without = asyncio.run(
        _ids(_risk_execution(cell_id=None, cell_ids=[10, 11], includes_cell_461=False))
    )
    assert "cnhpp_grid_resolution" in county_without
    assert "cnhpp_cell_461" not in county_without

    calfire_only = asyncio.run(_ids(_calfire_count(year=2024)))
    assert "cnhpp_grid_resolution" not in calfire_only
    assert "cnhpp_contagion_tie" not in calfire_only


def test_calfire_map_feed_caveat_attaches_on_span_not_single_year():
    settings = AgentSettings.from_env()
    executor = ToolExecutor(settings, ArtifactStore(60))

    async def _ids(execution: ToolExecution) -> set[str]:
        quals, _companions, error = await collect_qualifications(
            [execution], executor, request_id="calfire-feed", start_attempt=1
        )
        assert error is None
        return {item["id"] for item in quals}

    # 2023 and 2024 have no untyped incidents, so no untyped caveat.
    single = asyncio.run(_ids(_calfire_count(year=2024)))
    assert "calfire_missingness" not in single
    assert "calfire_map_feed_counts" not in single

    spanned = asyncio.run(
        _ids(_calfire_count(start_date="2023-01-01", end_date="2024-12-31"))
    )
    assert "calfire_missingness" not in spanned
    assert "calfire_map_feed_counts" in spanned
