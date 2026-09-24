"""Statewide risk surface: a router-only tool grounded on GET /surface for one day."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from services.agent.config import AgentSettings
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.schemas import (
    EXECUTABLE_TOOL_MODELS,
    TOOL_DESCRIPTIONS,
    TOOL_MODELS,
    RiskSurfaceArgs,
    openai_tools,
)
from services.agent.tools import ToolExecution, _summarize_risk_surface
from services.agent.views import ComponentSpec, GroundingError, ground_views, plan_views


def _grid(day: str = "2024-08-15", *, cells: int = 824) -> dict:
    return {
        "date": day,
        "lookback_days": 90,
        "cells": [
            {
                "cell_id": index,
                "lat": 35.0,
                "lon": -120.0,
                "risk": 0.5 if index == 461 else 0.001,
                "expected_count": 0.001,
                "intensity": 0.001,
            }
            for index in range(cells)
        ],
    }


def _surface_execution(day: str = "2024-08-15", *, evidence_id: str = "ev_surface") -> ToolExecution:
    args = RiskSurfaceArgs(date=date.fromisoformat(day))
    return ToolExecution(
        tool="risk_surface",
        arguments=args.model_dump(mode="json"),
        ok=True,
        summary=_summarize_risk_surface(args, _grid(day)),
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
        evidence_id=evidence_id,
    )


def test_surface_tool_is_never_offered_to_the_model_or_jev():
    assert "risk_surface" not in TOOL_MODELS
    assert "risk_surface" not in TOOL_DESCRIPTIONS
    assert "risk_surface" in EXECUTABLE_TOOL_MODELS
    names = {item["function"]["name"] for item in openai_tools()}
    assert "risk_surface" not in names


def test_date_with_no_place_uses_the_statewide_surface():
    for question, mode in (
        ("Show the risk surface for 2024-08-15", "risk"),
        ("Show the residual map for 2024-08-15", "residual"),
        ("Map ignition risk statewide on 2024-08-15", "risk"),
    ):
        decision = route_question(question)
        assert decision.path == "deterministic", question
        assert decision.rule == "risk_surface"
        assert decision.tool_calls == [("risk_surface", {"date": "2024-08-15"})]
        assert decision.slots["map_mode"] == mode


def test_surface_date_validation_matches_single_place_risk():
    pairs = (
        ("Show the risk surface for August 2024", "Show the risk surface for Butte County in August 2024"),
        ("Show the risk surface for 2026-01-03", "Show the risk surface for Butte County on 2026-01-03"),
        ("Show the risk surface for tomorrow", "Show the risk surface for Butte County tomorrow"),
        ("Show the risk surface", "Show the risk surface for Butte County"),
    )
    for statewide, placed in pairs:
        left = route_question(statewide)
        right = route_question(placed)
        assert left.path == "clarification", statewide
        assert left.tool_calls == []
        assert (left.rule, left.answer) == (right.rule, right.answer), statewide


def test_single_place_and_plain_risk_questions_are_unchanged():
    placed = route_question("Show the risk surface for Butte County on 2024-08-15")
    assert placed.rule == "county_risk"
    assert placed.tool_calls == [("risk_forecast", {"county": "Butte", "date": "2024-08-15"})]
    plain = route_question("What was the ignition risk on 2024-08-15?")
    assert plain.rule == "risk_missing_place"
    assert plain.answer.startswith("Which place should I score?")


def test_surface_summary_rejects_a_different_day_or_a_partial_grid():
    args = RiskSurfaceArgs(date=date(2024, 8, 15))
    summary = _summarize_risk_surface(args, _grid())
    assert summary["cell_count"] == 824
    assert summary["max_risk_cell_id"] == 461
    assert summary["includes_cell_461"] is True
    with pytest.raises(ValueError):
        _summarize_risk_surface(args, _grid("2024-08-16"))
    with pytest.raises(ValueError):
        _summarize_risk_surface(args, _grid(cells=823))
    broken = _grid()
    broken["cells"][3]["risk"] = 1.5
    with pytest.raises(ValueError):
        _summarize_risk_surface(args, broken)


@pytest.mark.parametrize("mode", ["risk", "residual"])
def test_planner_grounds_the_grid_on_the_surface_day(mode):
    planned = plan_views([_surface_execution()], status="answer", slots={"map_mode": mode})
    assert planned.view_status == "applied"
    [spec] = planned.views
    assert spec.type == "map"
    assert spec.params["map_mode"] == mode
    assert spec.params["risk_date"] == "2024-08-15"
    assert spec.evidence_ids == ["ev_surface"]


def test_an_invented_or_unscored_surface_date_is_rejected():
    spec = ComponentSpec(
        type="map",
        params={"datasets": [], "map_mode": "risk", "risk_date": "2024-08-16"},
        evidence_ids=["ev_surface"],
    )
    with pytest.raises(GroundingError):
        ground_views([spec], [_surface_execution()])
    unscored = ComponentSpec(
        type="map",
        params={"datasets": [], "map_mode": "risk", "risk_date": "2024-08-15"},
        evidence_ids=["ev_missing"],
    )
    with pytest.raises(GroundingError):
        ground_views([unscored], [_surface_execution()])


class _UnusedProvider:
    async def complete(self, **kwargs):  # pragma: no cover
        raise AssertionError("deterministic surface answers never call the model")


def test_statewide_surface_answer_is_grounded_end_to_end():
    class SurfaceExecutor:
        calls: list[tuple[str, dict]] = []

        async def execute(self, tool, arguments, **kwargs):
            self.calls.append((tool, arguments))
            assert tool == "risk_surface"
            return _surface_execution(arguments["date"])

    async def run():
        executor = SurfaceExecutor()
        orchestrator = AgentOrchestrator(
            AgentSettings(),
            _UnusedProvider(),  # type: ignore[arg-type]
            executor,  # type: ignore[arg-type]
        )
        result = await orchestrator.ask("Show the risk surface for 2024-08-15")
        response = result.response
        assert response["status"] == "answer"
        assert executor.calls == [("risk_surface", {"date": "2024-08-15"})]
        assert "824" in response["answer_text"]
        assert "cell 461" in response["answer_text"]
        maps = [view for view in response["views"] if view["type"] == "map"]
        assert len(maps) == 1
        assert maps[0]["params"]["map_mode"] == "risk"
        assert maps[0]["params"]["risk_date"] == "2024-08-15"
        ids = {item["id"] for item in response.get("qualifications") or []}
        assert {"cnhpp_grid_resolution", "cnhpp_contagion_tie"} <= ids

    asyncio.run(run())


def test_the_model_cannot_call_the_router_only_surface_tool():
    class GuessingProvider:
        async def complete(self, **kwargs):
            if kwargs.get("tool_routing"):
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "risk_surface",
                                "arguments": '{"date":"2024-08-15"}',
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1,
                    usage={},
                )
            return ModelReply(
                content='{"status":"answer","answer":"done","claims":[]}',
                tool_calls=[],
                raw={"choices": [{"message": {"content": "{}"}}]},
                latency_ms=1,
                usage={},
            )

    class RecordingExecutor:
        calls: list[str] = []

        async def execute(self, tool, arguments, **kwargs):
            self.calls.append(tool)
            return _surface_execution()

    async def run():
        executor = RecordingExecutor()
        orchestrator = AgentOrchestrator(
            AgentSettings(max_tool_steps=1),
            GuessingProvider(),  # type: ignore[arg-type]
            executor,  # type: ignore[arg-type]
        )
        result = await orchestrator.ask("Show me something about 2024-08-15", force_model=True)
        assert "risk_surface" not in executor.calls
        refused = [
            step
            for step in result.response["trajectory"]
            if step.get("tool") == "risk_surface"
        ]
        assert refused and all((step.get("error") or {}).get("code") == "unknown_tool" for step in refused)
        assert not any(item.get("tool") == "risk_surface" for item in result.response.get("evidence") or [])

    asyncio.run(run())
