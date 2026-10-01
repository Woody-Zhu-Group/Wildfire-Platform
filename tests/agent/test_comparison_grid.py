"""Comparison cells keep the values, scopes and evidence of primary results."""

from copy import deepcopy

import pytest

from services.agent.tools import ToolExecution
from services.agent.views import ComponentSpec, GroundingError, ground_views, plan_views


def count(scope, year, value, **filters):
    args = dict(
        dataset="cpuc_ignitions",
        result_mode="count",
        utility=scope,
        year=year,
        **filters,
    )
    return ToolExecution(
        tool="data_query_records",
        arguments=args,
        summary={
            "dataset": args["dataset"],
            "result_mode": "count",
            "total": value,
            "filters": args,
        },
        raw={},
        ok=True,
        error=None,
        artifact=None,
        latency_ms=1,
        evidence_id=f"{scope}_{year}",
        qualification_call=False,
    )


def comparison_grid(executions):
    views = plan_views(executions, status="answer").views
    comparisons = [v for v in views if v.type == "comparison"]
    assert len(comparisons) == 1
    assert not any(v.type == "stat_card" for v in views)
    return comparisons[0]


def test_utility_by_year_uses_one_comparison_with_every_cited_value():
    executions = [
        count("PGE", 2020, 510),
        count("PGE", 2023, 374),
        count("SCE", 2020, 145),
        count("SCE", 2023, 90),
    ]
    spec = comparison_grid(executions)
    grid = spec.params["grid"]
    assert grid["rows"] == ["PGE", "SCE"]
    assert grid["columns"] == ["2020", "2023"]
    assert [c["value"] for c in grid["cells"]] == [510, 374, 145, 90]
    assert [c["evidence_id"] for c in grid["cells"]] == spec.evidence_ids


def test_six_utilities_in_one_period_keep_all_six_values():
    spec = comparison_grid(
        [
            count(scope, 2023, i)
            for i, scope in enumerate(["PGE", "SCE", "SDGE", "PAC", "BVES", "LADWP"])
        ]
    )
    assert len(spec.params["grid"]["rows"]) == 6
    assert len(spec.params["grid"]["cells"]) == 6


@pytest.mark.parametrize(
    "field,value",
    [("value", 999), ("row", "SCE"), ("column", "2024"), ("evidence_id", "SCE_2023")],
)
def test_forged_cell_is_rejected(field, value):
    executions = [count("PGE", 2023, 374), count("SCE", 2023, 90)]
    original = comparison_grid(executions)
    params = deepcopy(original.params)
    params["grid"]["cells"][0][field] = value
    with pytest.raises((GroundingError, ValueError)):
        spec = ComponentSpec(
            type="comparison", params=params, evidence_ids=original.evidence_ids
        )
        ground_views([spec], executions)


def test_different_filters_and_duplicate_cells_keep_their_cards():
    for executions in (
        [count("PGE", 2023, 2, county="Butte"), count("SCE", 2023, 3, cause="VEG")],
        [count("PGE", 2023, 2), count("PGE", 2023, 2)],
    ):
        views = plan_views(executions, status="answer").views
        assert sum(v.type == "stat_card" for v in views) == 2
        assert not any(v.type == "comparison" for v in views)


def test_qualification_counts_never_enter_the_comparison():
    executions = [
        count("PGE", 2023, 374),
        count("SCE", 2023, 90),
        count("SDGE", 2023, 22),
    ]
    executions[-1].qualification_call = True
    spec = comparison_grid(executions)
    assert spec.evidence_ids == ["PGE_2023", "SCE_2023"]


def compare(kind, args, summary):
    return ToolExecution(
        tool="comparison_run",
        arguments={"kind": kind, **args},
        summary={"kind": kind, **summary},
        raw={},
        ok=True,
        error=None,
        artifact=None,
        latency_ms=1,
        evidence_id="comparison_1",
        qualification_call=False,
    )


@pytest.mark.parametrize(
    "kind,scope_args",
    [
        ("utilities", {"utilities": ["PGE", "SCE"]}),
        ("regions", {"region_type": "county", "regions": ["Butte", "Shasta"]}),
    ],
)
def test_service_comparison_cites_each_value_and_missing_reason(kind, scope_args):
    scopes = scope_args.get("utilities") or scope_args["regions"]
    execution = compare(
        kind,
        {
            "metric": "epss_outage_count",
            **scope_args,
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
        {
            "metric": "epss_outage_count",
            "results": [
                {"key": scopes[0], "value": 0, "reason": None},
                {"key": scopes[1], "value": None, "reason": "No coverage"},
            ],
        },
    )
    spec = comparison_grid([execution])
    cells = spec.params["grid"]["cells"]
    assert cells[0]["value"] == 0
    assert cells[1]["value"] is None
    assert cells[1]["reason"] == "No coverage"
    for cell in cells:
        assert cell["evidence_id"] == execution.evidence_id
    forged = deepcopy(spec.params)
    forged["grid"]["cells"][1].update(value=0, reason=None)
    with pytest.raises(GroundingError):
        ground_views(
            [
                ComponentSpec(
                    type="comparison", params=forged, evidence_ids=spec.evidence_ids
                )
            ],
            [execution],
        )


def test_period_comparison_keeps_exact_periods_and_normalized_values():
    execution = compare(
        "periods",
        {
            "metric": "ignition_count",
            "scope_type": "utility",
            "scope": "PGE",
            "normalize": "per_circuit",
            "period_a_start": "2020-01-01",
            "period_a_end": "2020-12-31",
            "period_b_start": "2023-01-01",
            "period_b_end": "2023-12-31",
        },
        {
            "metric": "ignition_count",
            "period_a": {"value": 0.5},
            "period_b": {"value": 0.25},
        },
    )
    grid = comparison_grid([execution]).params["grid"]
    assert grid["columns"] == ["2020", "2023"]
    assert grid["rows"] == ["PGE"]
    assert grid["label"].endswith(" per circuit")
    assert [c["value"] for c in grid["cells"]] == [0.5, 0.25]
