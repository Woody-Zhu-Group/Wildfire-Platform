"""ViewPlanner emits grounded component specs from tool executions, not the model."""

from __future__ import annotations

import pytest

from services.agent.tools import ToolExecution
from services.agent.views import (
    ComponentSpec,
    GroundingError,
    format_view_scope,
    ground_views,
    plan_views,
    scope_diverges,
)


def _exec(
    tool: str,
    arguments: dict,
    summary: dict,
    *,
    evidence_id: str,
    artifact_ref: str | None = None,
    qualification_call: bool = False,
    ok: bool = True,
) -> ToolExecution:
    return ToolExecution(
        tool=tool,
        arguments=arguments,
        ok=ok,
        summary=summary,
        raw={},
        error=None,
        artifact={"ref": artifact_ref} if artifact_ref else None,
        latency_ms=1.0,
        evidence_id=evidence_id,
        qualification_call=qualification_call,
    )


def _count(
    dataset: str,
    total: int,
    *,
    evidence_id: str,
    year: int = 2024,
    utility: str | None = "PGE",
    county: str | None = None,
) -> ToolExecution:
    arguments: dict = {
        "dataset": dataset,
        "result_mode": "count",
        "year": year,
    }
    filters: dict = {"year": year}
    if utility:
        arguments["utility"] = utility
        filters["utility"] = utility
    if county:
        arguments["county"] = county
        filters["county"] = county
    return _exec(
        "data_query_records",
        arguments,
        {
            "dataset": dataset,
            "result_mode": "count",
            "total": total,
            "returned": 1,
            "filters": filters,
        },
        evidence_id=evidence_id,
    )


def test_count_emits_stat_card_and_map():
    planned = plan_views(
        [_count("cpuc_ignitions", 532, evidence_id="ev_count")],
        status="answer",
        slots={"year": 2024, "utilities": ["PGE"]},
    )
    assert planned.view_status == "applied"
    assert [item.type for item in planned.views] == ["stat_card", "map"]
    card = planned.views[0].params
    assert card["value"] == 532.0
    assert card["source_dataset"] == "cpuc_ignitions"
    assert card["label"] == "CPUC ignitions"
    mapped = planned.views[1].params
    assert mapped["datasets"] == ["ignitions"]
    assert mapped["year"] == 2024
    assert mapped["utility"] == "PGE"
    assert mapped["extent"] == "auto_fit"
    assert planned.view_scope["year"] == 2024
    assert planned.view_scope["utility"] == "PGE"


def test_map_and_time_series_and_comparison_and_table_and_point():
    map_exec = _exec(
        "visualization_create",
        {
            "kind": "map",
            "dataset": "ignitions",
            "utility": "PGE",
            "year": 2024,
        },
        {
            "kind": "map",
            "dataset": "ignitions",
            "total": 532,
            "filters": {"utility": "PGE", "year": 2024},
        },
        evidence_id="ev_map",
        artifact_ref="art_map",
    )
    series_exec = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "calfire",
            "interval": "monthly",
            "year": 2024,
        },
        {
            "kind": "time_series",
            "dataset": "calfire",
            "interval": "monthly",
            "total_events": 611,
            "filters": {"year": 2024},
        },
        evidence_id="ev_ts",
        artifact_ref="art_ts",
    )
    compare_exec = _exec(
        "comparison_run",
        {
            "kind": "utilities",
            "metric": "ignition_count",
            "utilities": ["PGE", "SCE"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
        {"kind": "utilities", "metric": "ignition_count", "results": []},
        evidence_id="ev_cmp",
        artifact_ref="art_cmp",
    )
    table_exec = _exec(
        "data_query_records",
        {
            "dataset": "psps_events",
            "result_mode": "records",
            "utility": "PGE",
            "year": 2021,
            "limit": 10,
        },
        {
            "dataset": "psps_events",
            "result_mode": "records",
            "total": 2,
            "returned": 2,
            "filters": {"utility": "PGE", "year": 2021},
        },
        evidence_id="ev_tbl",
    )
    point_exec = _exec(
        "data_query_spatial",
        {"kind": "point", "lat": 38.58, "lon": -121.49},
        {
            "kind": "point",
            "lat": 38.58,
            "lon": -121.49,
            "iou": {"utility_name": "Pacific Gas & Electric Company"},
            "hftd_tier": None,
            "county": "Sacramento",
            "grid_cell": {"cell_id": 186},
        },
        evidence_id="ev_pt",
    )
    risk_exec = _exec(
        "risk_forecast",
        {"cell_id": 400, "date": "2024-08-15"},
        {
            "cell_id": 400,
            "date": "2024-08-15",
            "risk": 0.004647524512780971,
            "expected_count": 0.004658,
            "xi": 0.1,
            "lookback_days": 90,
            "aggregation": "p_at_least_one",
            "cell_count": 1,
            "scope": {"type": "cell", "name": "cell 400"},
            "local_percentile": 72.0,
            "statewide_percentile": 55.0,
            "local_period": "August 2020–2025",
            "local_n": 186,
            "intensity": 0.004658,
        },
        evidence_id="ev_risk",
    )

    assert [item.type for item in plan_views([map_exec], status="answer").views] == [
        "map"
    ]
    mapped = plan_views([map_exec], status="answer").views[0].params
    assert mapped["extent"] == "territory"
    assert mapped["show_territory"] is True
    assert mapped["datasets"] == ["ignitions"]

    assert [
        item.type for item in plan_views([series_exec], status="answer").views
    ] == ["time_series"]
    assert [
        item.type for item in plan_views([compare_exec], status="answer").views
    ] == ["comparison"]
    assert [item.type for item in plan_views([table_exec], status="answer").views] == [
        "record_table"
    ]
    assert [item.type for item in plan_views([point_exec], status="answer").views] == [
        "spatial_context"
    ]
    risk_views = plan_views([risk_exec], status="answer").views
    assert [item.type for item in risk_views] == [
        "stat_card",
        "stat_card",
        "stat_card",
    ]
    assert risk_views[0].params["unit"] == "risk"
    assert risk_views[0].params["value"] == 0.004647524512780971
    assert risk_views[1].params["unit"] == "percentile"
    assert risk_views[1].params["value"] == 72.0
    assert risk_views[2].params["unit"] == "percentile"
    assert risk_views[2].params["value"] == 55.0
    fractional = _exec(
        "risk_forecast",
        {"cell_id": 400, "date": "2024-08-15"},
        {
            **risk_exec.summary,
            "local_percentile": 77.95698924731182,
            "statewide_percentile": 89.56310679611651,
        },
        evidence_id="ev_risk_frac",
    )
    rounded = plan_views([fractional], status="answer").views
    assert rounded[1].params["value"] == 78.0
    assert rounded[2].params["value"] == 90.0
    assert [
        item.type
        for item in plan_views([point_exec, risk_exec], status="answer").views
    ] == ["stat_card", "stat_card", "stat_card", "spatial_context"]


def test_mutated_year_fails_grounding():
    execution = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "calfire",
            "interval": "monthly",
            "year": 2024,
        },
        {
            "kind": "time_series",
            "dataset": "calfire",
            "interval": "monthly",
            "filters": {"year": 2024},
        },
        evidence_id="ev_ts",
        artifact_ref="art_ts",
    )
    planned = plan_views([execution], status="answer")
    assert planned.view_status == "applied"
    mutated = planned.views[0].model_copy(
        update={"params": {**planned.views[0].params, "year": 2019}}
    )
    with pytest.raises(GroundingError, match="year"):
        ground_views([mutated], [execution])

    fallback = plan_views([execution], status="answer")
    # Planner itself emits the evidenced year; forcing a bad spec is rejected.
    bad = ComponentSpec(
        type="time_series",
        params={**fallback.views[0].params, "year": 2019},
        evidence_ids=["ev_ts"],
        artifact_refs=["art_ts"],
    )
    rejected = None
    try:
        ground_views([bad], [execution])
    except GroundingError as exc:
        rejected = exc
    assert rejected is not None


def test_spatial_summary_emits_three_stat_cards():
    planned = plan_views(
        [
            _exec(
                "data_query_spatial",
                {
                    "kind": "summary",
                    "utility": "PGE",
                    "start_date": "2024-01-01",
                    "end_date": "2024-12-31",
                },
                {
                    "kind": "summary",
                    "region": {"kind": "utility", "id": "PGE"},
                    "start_date": "2024-01-01",
                    "end_date": "2024-12-31",
                    "counts": {
                        "ignitions": 536,
                        "epss_outages": 1201,
                        "calfire_incidents": 88,
                    },
                },
                evidence_id="ev_spatial",
            )
        ],
        status="answer",
        slots={"year": 2024, "utilities": ["PGE"]},
    )
    assert planned.view_status == "applied"
    types = [item.type for item in planned.views]
    assert types == ["stat_card", "stat_card", "stat_card"]
    cards = [item.params for item in planned.views]
    assert [item["kind"] for item in cards] == [
        "spatial_metric",
        "spatial_metric",
        "spatial_metric",
    ]
    assert [item["source_dataset"] for item in cards] == [
        "ignitions",
        "epss_outages",
        "calfire_incidents",
    ]
    assert [item["label"] for item in cards] == [
        "CPUC ignitions",
        "EPSS outages",
        "CAL FIRE incidents",
    ]
    assert [item["value"] for item in cards] == [536.0, 1201.0, 88.0]
    assert all(item.artifact_refs == [] for item in planned.views)


def test_cpuc_and_us_counts_are_two_stat_cards_not_comparison():
    planned = plan_views(
        [
            _count("cpuc_ignitions", 532, evidence_id="ev_cpuc"),
            _count(
                "us_ignitions",
                3789,
                evidence_id="ev_us",
                utility=None,
            ),
        ],
        status="answer",
        slots={"year": 2024},
    )
    types = [item.type for item in planned.views]
    assert types == ["stat_card", "stat_card"]
    assert "comparison" not in types
    assert "map" not in types
    datasets = [item.params["source_dataset"] for item in planned.views]
    assert datasets == ["cpuc_ignitions", "us_ignitions"]
    assert planned.views[0].params["value"] == 532.0
    assert planned.views[1].params["value"] == 3789.0


def test_cpuc_and_us_time_series_are_not_overlaid():
    cpuc = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "ignitions",
            "interval": "monthly",
            "year": 2024,
        },
        {"kind": "time_series", "dataset": "ignitions", "interval": "monthly"},
        evidence_id="ev_cpuc_ts",
        artifact_ref="art_cpuc",
    )
    us = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "us_ignitions",
            "interval": "monthly",
            "year": 2024,
        },
        {"kind": "time_series", "dataset": "us_ignitions", "interval": "monthly"},
        evidence_id="ev_us_ts",
        artifact_ref="art_us",
    )
    planned = plan_views([cpuc, us], status="answer")
    types = [item.type for item in planned.views]
    assert types == ["time_series"]
    assert planned.views[0].params["dataset"] == "ignitions"


def test_map_plus_time_series_keeps_both():
    map_exec = _exec(
        "visualization_create",
        {
            "kind": "map",
            "dataset": "ignitions",
            "utility": "PGE",
            "year": 2024,
        },
        {
            "kind": "map",
            "dataset": "ignitions",
            "total": 532,
            "filters": {"utility": "PGE", "year": 2024},
        },
        evidence_id="ev_map_combo",
        artifact_ref="art_map_combo",
    )
    series_exec = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "ignitions",
            "interval": "monthly",
            "utility": "PGE",
            "year": 2024,
        },
        {
            "kind": "time_series",
            "dataset": "ignitions",
            "interval": "monthly",
            "filters": {"utility": "PGE", "year": 2024},
        },
        evidence_id="ev_ts_combo",
        artifact_ref="art_ts_combo",
    )
    planned = plan_views([map_exec, series_exec], status="answer")
    types = [item.type for item in planned.views]
    assert types == ["map", "time_series"]
    assert planned.views[1].params["dataset"] == "ignitions"
    assert planned.views[1].artifact_refs == ["art_ts_combo"]


def test_two_same_family_time_series_keep_first_only():
    first = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "ignitions",
            "interval": "monthly",
            "year": 2024,
        },
        {"kind": "time_series", "dataset": "ignitions", "interval": "monthly"},
        evidence_id="ev_ts_a",
        artifact_ref="art_ts_a",
    )
    second = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "ignitions",
            "interval": "weekly",
            "year": 2024,
        },
        {"kind": "time_series", "dataset": "ignitions", "interval": "weekly"},
        evidence_id="ev_ts_b",
        artifact_ref="art_ts_b",
    )
    planned = plan_views([first, second], status="answer")
    types = [item.type for item in planned.views]
    assert types == ["time_series"]
    assert planned.views[0].params["interval"] == "monthly"


def test_partial_year_count_does_not_emit_map():
    execution = _exec(
        "data_query_records",
        {
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "start_date": "2023-08-01",
            "end_date": "2023-08-31",
            "county": "Sacramento",
        },
        {
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "total": 2,
            "filters": {
                "start_date": "2023-08-01",
                "end_date": "2023-08-31",
                "county": "Sacramento",
            },
        },
        evidence_id="ev_aug",
    )
    planned = plan_views([execution], status="answer", slots={"year": 2023})
    assert planned.view_status == "applied"
    assert [item.type for item in planned.views] == ["stat_card", "time_series"]
    card = planned.views[0].params
    assert card["period"] == "August 2023"
    assert card["scope"] == "Sacramento County"
    series = planned.views[1].params
    assert series["dataset"] == "calfire"
    assert series["interval"] == "daily"
    assert series["start_date"] == "2023-08-01"
    assert series["end_date"] == "2023-08-31"
    assert series["county"] == "Sacramento"
    assert planned.views[1].artifact_refs == []


def test_full_year_count_does_not_emit_time_series():
    planned = plan_views(
        [_count("cpuc_ignitions", 532, evidence_id="ev_count")],
        status="answer",
        slots={"year": 2024, "utilities": ["PGE"]},
    )
    assert [item.type for item in planned.views] == ["stat_card", "map"]


def test_explicit_time_series_wins_over_count_derived():
    count = _exec(
        "data_query_records",
        {
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "start_date": "2023-08-01",
            "end_date": "2023-08-31",
            "county": "Sacramento",
        },
        {
            "dataset": "calfire_incidents",
            "result_mode": "count",
            "total": 0,
            "filters": {
                "start_date": "2023-08-01",
                "end_date": "2023-08-31",
                "county": "Sacramento",
            },
        },
        evidence_id="ev_count",
    )
    series = _exec(
        "visualization_create",
        {
            "kind": "time_series",
            "dataset": "calfire",
            "interval": "monthly",
            "year": 2023,
            "county": "Sacramento",
        },
        {
            "kind": "time_series",
            "dataset": "calfire",
            "interval": "monthly",
            "total_events": 0,
            "filters": {"year": 2023, "county": "Sacramento"},
        },
        evidence_id="ev_ts",
        artifact_ref="art_ts",
    )
    planned = plan_views([count, series], status="answer", slots={"year": 2023})
    types = [item.type for item in planned.views]
    assert types == ["stat_card", "time_series"]
    assert planned.views[1].params["interval"] == "monthly"
    assert planned.views[1].artifact_refs == ["art_ts"]


def test_non_answer_status_emits_empty_views_and_keeps_scope():
    planned = plan_views(
        [_count("cpuc_ignitions", 532, evidence_id="ev_count")],
        status="unsupported",
        slots={"year": 2024, "utilities": ["PGE"], "county": None},
    )
    assert planned.views == []
    assert planned.view_status == "none"
    assert planned.view_scope["year"] == 2024
    assert planned.view_scope["utility"] == "PGE"
    assert format_view_scope(planned.view_scope) == "2024, PGE"


def test_view_scope_from_slots_when_no_primary_tools():
    planned = plan_views(
        [],
        status="clarification",
        slots={"year": 2023, "utilities": ["PGE"], "county": "Sacramento"},
    )
    assert planned.views == []
    assert planned.view_status == "none"
    assert planned.view_scope["year"] == 2023
    assert planned.view_scope["utility"] == "PGE"
    assert planned.view_scope["county"] == "Sacramento"
    assert "2023" in format_view_scope(planned.view_scope)
    assert "PGE" in format_view_scope(planned.view_scope)


def test_scope_diverges_for_stale_canvas():
    answer = {"year": 2024, "utility": "PGE", "county": None, "years": [2024]}
    canvas = {"year": 2023, "utility": "PGE", "county": None, "years": [2023]}
    assert scope_diverges(answer, canvas) is True
    assert scope_diverges(answer, answer) is False
    assert (
        scope_diverges(
            {"year": 2024, "utility": "SCE", "county": None},
            {"year": 2024, "utility": "PGE", "county": None},
        )
        is True
    )


def test_qualification_calls_are_not_views():
    companion = _count("cpuc_ignitions", 536, evidence_id="ev_qual", utility="PGE")
    companion.qualification_call = True
    empty = plan_views([companion], status="answer", slots={"year": 2024})
    assert empty.views == []
    assert empty.view_status == "none"
    assert empty.view_scope["year"] == 2024


def test_us_ignitions_map_uses_conus_without_california_filter():
    execution = _exec(
        "visualization_create",
        {"kind": "map", "dataset": "us_ignitions", "year": 2024},
        {
            "kind": "map",
            "dataset": "us_ignitions",
            "total": 3789,
            "filters": {"year": 2024},
        },
        evidence_id="ev_us_map",
        artifact_ref="art_us_map",
    )
    params = plan_views([execution], status="answer").views[0].params
    assert params["extent"] == "conus"
    assert params["datasets"] == ["us_ignitions"]


def test_county_map_uses_auto_fit():
    execution = _exec(
        "visualization_create",
        {
            "kind": "map",
            "dataset": "ignitions",
            "county": "Sacramento",
            "year": 2023,
        },
        {
            "kind": "map",
            "dataset": "ignitions",
            "total": 3,
            "filters": {"county": "Sacramento", "year": 2023},
        },
        evidence_id="ev_county_map",
        artifact_ref="art_county_map",
    )
    params = plan_views([execution], status="answer").views[0].params
    assert params["extent"] == "auto_fit"
    assert params["county"] == "Sacramento"


def test_ranking_spec_carries_group_by():
    for group_by, dataset, metric in (
        ("county", "cpuc_ignitions", "ignition_count"),
        ("utility", "cpuc_ignitions", "ignition_count"),
        ("cause", "epss_outages", "epss_outage_count"),
    ):
        execution = _exec(
            "data_query_rank",
            {
                "dataset": dataset,
                "group_by": group_by,
                "year": 2024,
                "limit": 10,
            },
            {
                "dataset": dataset,
                "group_by": group_by,
                "canvas_metric": metric,
                "results": [{"key": "example", "value": 3}],
                "limit": 10,
            },
            evidence_id=f"ev_{group_by}",
        )
        planned = plan_views([execution], status="answer")
        assert [item.type for item in planned.views] == ["comparison"]
        params = planned.views[0].params
        assert params["kind"] == "ranking"
        assert params["group_by"] == group_by
        assert params["dataset"] == dataset
        assert params["start_date"] == "2024-01-01"
        assert params["end_date"] == "2024-12-31"
        assert planned.views[0].evidence_ids == [f"ev_{group_by}"]


def test_summary_slot_emits_one_grounded_stat_card():
    planned = plan_views(
        [_count("epss_outages", 40, evidence_id="ev_summary", utility=None)],
        status="answer",
        slots={"stat_mode": "summary", "dataset": "epss_outages", "year": 2024},
    )
    assert planned.view_status == "applied"
    assert [item.type for item in planned.views] == ["stat_card"]
    params = planned.views[0].params
    assert params["stat_mode"] == "summary"
    assert params["view_id"] == "summary-stats"
    assert params["source_dataset"] == "epss_outages"
    assert params["value"] == 40
    assert params["year"] == 2024
    assert planned.views[0].evidence_ids == ["ev_summary"]
    plain = plan_views(
        [_count("cpuc_ignitions", 532, evidence_id="ev_count")],
        status="answer",
    )
    assert [item.type for item in plain.views] == ["stat_card", "map"]
    assert plain.views[0].params["stat_mode"] is None


def test_series_mode_requires_the_cited_dataset():
    calfire = _exec(
        "visualization_create",
        {"kind": "time_series", "dataset": "calfire", "interval": "monthly", "year": 2024},
        {"kind": "time_series", "dataset": "calfire", "interval": "monthly"},
        evidence_id="ev_calfire",
    )
    applied = plan_views(
        [calfire], status="answer", slots={"series_mode": "cumulative_acres"}
    )
    assert applied.view_status == "applied"
    assert applied.views[0].type == "time_series"
    assert applied.views[0].params["series_mode"] == "cumulative_acres"
    assert applied.views[0].params["dataset"] == "calfire"
    assert applied.views[0].evidence_ids == ["ev_calfire"]

    epss = _exec(
        "visualization_create",
        {"kind": "time_series", "dataset": "epss", "interval": "monthly", "year": 2024},
        {"kind": "time_series", "dataset": "epss", "interval": "monthly"},
        evidence_id="ev_epss_series",
    )
    rejected = plan_views(
        [epss], status="answer", slots={"series_mode": "cumulative_acres"}
    )
    assert rejected.view_status == "planner_fallback"
    assert rejected.views == []

    plain = plan_views([calfire], status="answer")
    assert plain.views[0].params["series_mode"] is None


def test_medical_exposure_slot_emits_one_stat_card_not_a_map():
    planned = plan_views(
        [_count("epss_outages", 88, evidence_id="ev_epss", utility=None)],
        status="answer",
        slots={"stat_mode": "medical_exposure", "dataset": "epss", "year": 2024},
    )
    assert planned.view_status == "applied"
    assert [item.type for item in planned.views] == ["stat_card"]
    params = planned.views[0].params
    assert params["stat_mode"] == "medical_exposure"
    assert params["view_id"] == "medical-exposure"
    assert params["source_dataset"] == "epss_outages"
    assert params["value"] == 88
    assert params["year"] == 2024
    assert planned.views[0].evidence_ids == ["ev_epss"]
    assert planned.views[0].artifact_refs == []
