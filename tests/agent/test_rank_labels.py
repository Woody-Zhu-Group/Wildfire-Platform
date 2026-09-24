"""Ranking rows carry the registry code and label, and answers show the label (issue #89)."""

from __future__ import annotations

import asyncio

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.orchestrator import _render_rank_answer
from services.agent.tools import ToolExecutor

ARGS = {"dataset": "cpuc_ignitions", "group_by": "utility", "year": 2024, "limit": 3}


def _rank(rows: list[dict]) -> dict:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/rank")
        return httpx.Response(
            200,
            json={
                "data": rows,
                "meta": {"total": len(rows), "returned": len(rows), "limit": 3, "filters": {"year": 2024}},
            },
        )

    executor = ToolExecutor(AgentSettings(), ArtifactStore(60), transport=httpx.MockTransport(handler))
    result = asyncio.run(executor.execute("data_query_rank", dict(ARGS), request_id="t", attempt=1))
    assert result.ok, result.error
    return result.summary


def test_rank_summary_keeps_the_code_key_and_passes_through_code_and_label():
    summary = _rank(
        [
            {"group_value": "PGE", "code": "PGE", "label": "PG&E", "metric_value": 30},
            {"group_value": "SDGE", "code": "SDGE", "label": "SDG&E", "metric_value": 10},
        ]
    )
    assert [(r["key"], r["code"], r["label"]) for r in summary["results"]] == [
        ("PGE", "PGE", "PG&E"),
        ("SDGE", "SDGE", "SDG&E"),
    ]


def test_rank_summary_fills_code_and_label_from_the_registry_for_an_older_service():
    summary = _rank(
        [
            {"group_value": "PGE", "metric_value": 30},
            {"group_value": "SCE", "metric_value": 20},
            {"group_value": "(unknown)", "metric_value": 5},
        ]
    )
    assert [(r["key"], r["code"], r["label"]) for r in summary["results"]] == [
        ("PGE", "PGE", "PG&E"),
        ("SCE", "SCE", "SCE"),
        ("(unknown)", "(unknown)", "(unknown)"),
    ]


def test_rank_answer_names_utilities_by_label_not_code():
    summary = _rank(
        [
            {"group_value": "PGE", "code": "PGE", "label": "PG&E", "metric_value": 30},
            {"group_value": "SCE", "code": "SCE", "label": "SCE", "metric_value": 20},
            {"group_value": "SDGE", "code": "SDGE", "label": "SDG&E", "metric_value": 10},
        ]
    )
    text = _render_rank_answer(ARGS, summary)
    assert "PG&E=30, SCE=20, SDG&E=10" in text
    assert "PGE=" not in text and "SDGE=" not in text


def test_rank_answer_for_circuits_still_shows_the_circuit_id_name_and_division():
    summary = {
        "dataset": "epss_outages",
        "group_by": "circuit",
        "metric": "count",
        "total": 1,
        "returned": 1,
        "limit": 1,
        "results": [
            {
                "key": "043371102",
                "code": "043371102",
                "label": "043371102",
                "value": 4,
                "circuit_name": "ALPHA 1102",
                "division": "North Bay",
            }
        ],
    }
    text = _render_rank_answer({"group_by": "circuit"}, summary)
    assert "043371102 (ALPHA 1102, division North Bay)=4" in text


def _compare(rows: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/compare-utilities")
        return httpx.Response(
            200,
            json={"metric": "ignition_count", "normalize": "none", "results": rows, "meta": {"filters": {}}},
        )

    executor = ToolExecutor(AgentSettings(), ArtifactStore(60), transport=httpx.MockTransport(handler))
    result = asyncio.run(
        executor.execute(
            "comparison_run",
            {
                "kind": "utilities",
                "metric": "ignition_count",
                "utilities": ["PGE", "SDGE"],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
            request_id="t",
            attempt=1,
            utilities=["PGE", "SDGE"],
        )
    )
    assert result.ok, result.error
    return result


def test_comparison_utility_rows_pass_code_and_label_through():
    result = _compare(
        [
            {"key": "PGE", "code": "PGE", "label": "PG&E", "value": 532, "reason": None},
            {"key": "SDGE", "code": "SDGE", "label": "SDG&E", "value": 40, "reason": None},
        ]
    )
    assert [(r["key"], r["code"], r["label"]) for r in result.summary["results"]] == [
        ("PGE", "PGE", "PG&E"),
        ("SDGE", "SDGE", "SDG&E"),
    ]


def test_comparison_utility_rows_get_code_and_label_before_the_service_is_updated():
    result = _compare(
        [
            {"key": "PGE", "value": 532, "reason": None},
            {"key": "SDGE", "value": None, "reason": "EPSS is PG&E-only"},
        ]
    )
    assert [(r["key"], r["code"], r["label"]) for r in result.summary["results"]] == [
        ("PGE", "PGE", "PG&E"),
        ("SDGE", "SDGE", "SDG&E"),
    ]


def test_comparison_answer_names_utilities_by_label_not_code():
    from services.agent.orchestrator import _render_deterministic

    result = _compare(
        [
            {"key": "PGE", "code": "PGE", "label": "PG&E", "value": 532, "reason": None},
            {"key": "SDGE", "code": "SDGE", "label": "SDG&E", "value": None, "reason": "EPSS is PG&E-only"},
        ]
    )
    text = _render_deterministic([result])
    # Comparison answers are sentences (PR #93); both sides use the row label.
    assert "comparison: PG&E 532." in text
    assert "SDG&E has no CPUC ignition count: EPSS is PG&E-only." in text
    assert "PGE" not in text.replace("PG&E", "") and "SDGE" not in text
