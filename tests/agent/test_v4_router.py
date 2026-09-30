from datetime import date
import json
from pathlib import Path

import pytest

from services.agent.decisions import v4_router
from services.agent.decisions.backend import DecisionResult
from services.agent.routing import route_question
from tests.agent.test_jev_decide import _choice, _noul
from tests.agent.test_jev_first import facts

TODAY = date(2026, 9, 29)


def answers(intent="count", dataset="cpuc_ignitions", **extra):
    data = facts(intent, dataset=_choice(dataset))
    data.pop("broad_region")
    data.update(
        missing_geographic_scope=_noul(0.0),
        plan_fit=_choice("router", 0.01),
        risk_map_kind=_choice("none"),
    )
    data.update(extra)
    data["intent"].confidence = 0.01
    return data


def decide(question, data):
    return v4_router.decide_from_answers(
        question, route_question(question), data, today=TODAY
    ).decision


@pytest.mark.parametrize(
    "question",
    [
        "Count all SCE ignitions in 2024.",
        "For a budget presentation, count CPUC ignitions attributed to SCE in 2023.",
        "Give one total for SCE CPUC ignitions over 2021 through 2023.",
    ],
)
def test_supported_count_keeps_router_even_at_low_confidence(question):
    result = decide(question, answers())
    assert result.path == "deterministic"
    assert result.tool_calls[0][0] == "data_query_records"
    assert result.tool_calls[0][1]["utility"] == "SCE"


def test_period_change_is_not_a_union_total():
    result = decide(
        "How did PG&E ignitions change from 2019 to 2023?", answers("compare")
    )
    assert result.path == "deterministic"
    tool, args = result.tool_calls[0]
    assert tool == "comparison_run"
    assert (args["period_a_start"], args["period_a_end"]) == (
        "2019-01-01",
        "2019-12-31",
    )
    assert (args["period_b_start"], args["period_b_end"]) == (
        "2023-01-01",
        "2023-12-31",
    )


@pytest.mark.parametrize(
    "question,start,end",
    [
        (
            "Did EPSS outages go up from September 2023 to September 2024?",
            "2023-09-01",
            "2024-09-30",
        ),
        ("Compare EPSS counts in February and June 2024.", "2024-02-01", "2024-06-30"),
    ],
)
def test_endpoint_months_remain_complete_and_separate(question, start, end):
    result = decide(question, answers("compare", "epss_outages"))
    assert result.path == "deterministic"
    args = result.tool_calls[0][1]
    assert args["period_a_start"] == start and args["period_b_end"] == end
    assert args["scope"] == "PGE"
    if start == "2024-02-01":
        assert args["period_a_end"] == "2024-02-29"


def test_map_plus_chart_does_not_reuse_the_incomplete_map_plan():
    result = decide(
        "Map PG&E CPUC ignitions in 2023 and chart their monthly counts.",
        answers("map_plus_trend"),
    )
    assert result.path == "deterministic"
    assert [args["kind"] for _, args in result.tool_calls] == ["map", "time_series"]
    assert all(
        args["utility"] == "PGE" and args["year"] == 2023
        for _, args in result.tool_calls
    )
    assert result.tool_calls[1][1]["interval"] == "monthly"


def test_two_counties_are_preserved():
    result = decide(
        "Compare Butte and Shasta County ignition counts for 2023.", answers("compare")
    )
    assert result.path == "deterministic"
    assert set(result.tool_calls[0][1]["regions"]) == {"Butte", "Shasta"}


def test_point_lookup_does_not_need_a_keyword_match():
    result = decide(
        "Identify the utility territory containing the point 39.0, -121.0.",
        answers("spatial_context", "iou_territories"),
    )
    assert result.tool_calls == [
        ("data_query_spatial", {"kind": "point", "lat": 39.0, "lon": -121.0})
    ]


def test_rejected_plan_goes_to_agent_without_confidence_gate():
    result = decide(
        "Count all SCE ignitions in 2024.", answers(plan_fit=_choice("agent", 0.01))
    )
    assert result.path == "model" and not result.tool_calls
    assert result.rule == "v4_plan_incomplete"


def test_rejected_harness_only_plan_is_not_sent_to_an_incapable_agent():
    result = decide(
        "How well does the fitted ignition model perform?",
        answers(
            "model_metrics",
            "none",
            measure=_choice("model_performance"),
            plan_fit=_choice("agent"),
        ),
    )
    assert result.path == "clarification" and not result.tool_calls


def test_missing_location_clarifies_and_missing_backend_is_not_a_model_handoff():
    result = decide(
        "Identify the utility territory containing my current location.",
        answers(
            "spatial_context", "iou_territories", missing_geographic_scope=_noul(0.99)
        ),
    )
    assert result.path == "clarification" and result.rule == "missing_geographic_scope"
    result = v4_router.decide_from_answers(
        "Count ignitions in 2024.",
        route_question("Count ignitions in 2024."),
        None,
        error="timeout",
        today=TODAY,
    )
    assert result.decision.path == "error" and not result.decision.tool_calls


def test_obsolete_proximity_fact_cannot_override_complete_scope():
    result = decide(
        "Which utility territory contains 38.5, -121.5?",
        answers("spatial_context", "iou_territories", vague_proximity=_noul(0.99)),
    )
    assert result.path == "deterministic"
    request_facts = v4_router.calls_for(
        "Which utility contains this point?", TODAY.isoformat()
    )[0]["questions"]
    assert "vague_proximity" not in request_facts


def test_static_inventory_location_conflict_clarifies_instead_of_live_refusal():
    result = decide(
        "Identify the utility territory containing my current location.",
        answers(
            "spatial_context",
            "iou_territories",
            off_topic=_choice("live_or_web"),
            missing_geographic_scope=_noul(0.99),
        ),
    )
    assert result.path == "clarification" and result.rule == "missing_geographic_scope"


def test_composite_arithmetic_outside_fixed_templates_goes_to_agent():
    result = decide(
        "Count CPUC ignitions and CAL FIRE incidents in 2024, then compute their ratio.",
        answers("compare", "multiple", measure=_choice("other_measure")),
    )
    assert result.path == "model" and not result.tool_calls


def test_fixed_plan_needs_an_actual_fit_decision():
    data = answers()
    del data["plan_fit"]
    result = decide("Count PG&E ignitions in 2024.", data)
    assert result.path == "error" and not result.tool_calls


def test_live_path_asks_about_the_generated_plan():
    class Backend:
        def __init__(self):
            self.states = []

        def evaluate(self, state, questions, **kwargs):
            self.states.append(state)
            data = answers()
            for name in questions:
                if name.startswith("utility_"):
                    data[name] = _noul(1.0 if name == "utility_PGE" else 0.0)
            return DecisionResult(
                {name: data[name] for name in questions},
                "test",
                0,
                1,
                {},
                "test",
                "test",
            )

    backend = Backend()
    result = v4_router.decide_live(
        "Count PG&E ignitions in 2024.",
        route_question("Count PG&E ignitions in 2024."),
        backend=backend,
        today=TODAY,
    )
    assert result.decision.path == "deterministic"
    assert len(backend.states) == 4
    assert backend.states[-1]["tool_calls"] == result.decision.tool_calls


def test_runtime_error_never_calls_agent_or_tools(tmp_path):
    import asyncio
    from dataclasses import replace
    from unittest.mock import MagicMock
    from services.agent.config import AgentSettings
    from services.agent.orchestrator import AgentOrchestrator
    from tests.agent.test_jev_decide import FakeBackend

    provider, executor = MagicMock(), MagicMock()
    agent = AgentOrchestrator(
        replace(
            AgentSettings(), jev_mode="v4", jev_log_path=str(tmp_path / "jev.jsonl")
        ),
        provider,
        executor,
        decide_backend=FakeBackend(error="unavailable"),
    )
    result = asyncio.run(agent.ask("Count PG&E ignitions in 2024."))
    assert result.response["status"] == "error"
    assert result.response["decision_source"] == {
        "source": "jev",
        "why": "jev_error",
        "mode": "v4",
    }
    assert not provider.mock_calls and not executor.mock_calls


def test_v4_is_separate_from_v3_and_change_detection_ignores_confidence(
    monkeypatch, fake_jev_credentials
):
    from dataclasses import replace
    from unittest.mock import MagicMock
    from services.agent.config import AgentSettings
    from services.agent.orchestrator import AgentOrchestrator

    monkeypatch.setenv("AGENT_JEV_MODE", "v4")
    assert AgentSettings.from_env().jev_mode == "v4"
    monkeypatch.setenv("AGENT_JEV_MODE", "decide")
    assert AgentSettings.from_env().jev_mode == "decide"
    agent = AgentOrchestrator(
        replace(AgentSettings(), jev_mode="v4"), MagicMock(), MagicMock()
    )
    decision = route_question("How did PG&E ignitions change from 2020 to 2023?")
    decision.slots["jev_decide"] = {
        "jev_intent": "compare",
        "jev_intent_confidence": 0.01,
    }
    assert agent._jev_reads_change(decision)


def test_supported_count_executes_without_contacting_the_agent(tmp_path):
    import asyncio
    from dataclasses import replace
    from unittest.mock import MagicMock
    import httpx
    from services.agent.artifacts import ArtifactStore
    from services.agent.config import AgentSettings
    from services.agent.orchestrator import AgentOrchestrator
    from services.agent.tools import ToolExecutor
    from tests.agent.test_change_endpoints import _handler
    from tests.agent.test_jev_decide import FakeBackend

    settings = replace(
        AgentSettings(), jev_mode="v4", jev_log_path=str(tmp_path / "jev.jsonl")
    )
    provider = MagicMock()

    async def run():
        executor = ToolExecutor(
            settings, ArtifactStore(60), transport=httpx.MockTransport(_handler)
        )
        try:
            agent = AgentOrchestrator(
                settings, provider, executor, decide_backend=FakeBackend(answers())
            )
            return (await agent.ask("Count PG&E ignitions in 2024.")).response
        finally:
            await executor.close()

    response = asyncio.run(run())
    assert response["status"] == "answer"
    assert response["route"]["path"] == "deterministic"
    assert response["decision_source"]["mode"] == "v4"
    primary = [
        e
        for e in response["evidence"]
        if e["tool"] == "data_query_records" and not e.get("qualification_call")
    ]
    assert len(primary) == 1 and primary[0]["arguments"]["year"] == 2024
    assert not provider.mock_calls


def test_log_write_failure_does_not_fail_v4_question(monkeypatch, tmp_path):
    import asyncio
    from dataclasses import replace
    from unittest.mock import MagicMock
    from services.agent.config import AgentSettings
    from services.agent.orchestrator import AgentOrchestrator
    from services.agent.decisions.shadow_log import ShadowLog
    from tests.agent.test_jev_decide import FakeBackend

    def broken(*args, **kwargs):
        raise RuntimeError("logging failure")

    monkeypatch.setattr(ShadowLog, "write", broken)
    agent = AgentOrchestrator(
        replace(
            AgentSettings(), jev_mode="v4", jev_log_path=str(tmp_path / "jev.jsonl")
        ),
        MagicMock(),
        MagicMock(),
        decide_backend=FakeBackend(answers()),
    )
    seen = []

    async def routed(question, *, decision, **kwargs):
        seen.append(decision)

    monkeypatch.setattr(agent, "_ask_routed", routed)
    asyncio.run(agent.ask("Count PG&E ignitions in 2024."))
    assert seen[0].path == "deterministic"


ROUTER_CASES = [
    c
    for c in json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "services/agent/eval/v4_router_cases_v1.json"
        ).read_text()
    )["cases"]
    if c["expected_executor"] == ["router"]
]


@pytest.mark.parametrize("case", ROUTER_CASES, ids=lambda case: case["id"])
def test_reviewed_fixed_plan_contract_with_scripted_meaning(case):
    from services.agent.eval.executor_metrics import plan_matches
    from services.agent.routing import question_context

    gold = case["expected_tool_calls"]
    tool, args = gold[-1]
    intents = {
        "risk_metrics": "model_metrics",
        "risk_surface": "risk_surface",
        "risk_forecast": "risk",
        "data_query_spatial": "spatial_context",
        "comparison_run": "compare",
        "data_query_rank": "rank",
    }
    intent = intents.get(tool)
    if tool == "visualization_create":
        intent = (
            "map_plus_trend"
            if len(gold) > 1
            else "map"
            if args["kind"] == "map"
            else "trend"
        )
    elif tool == "data_query_records":
        intent = "count" if args["result_mode"] == "count" else "records_list"
    elif tool == "visualization_inspect":
        intent = (
            "circuit_detail" if args["kind"] == "event_detail" else "territory_boundary"
        )
    slots, _ = question_context(case["question"], today=TODAY)
    dataset = slots["dataset"] or "none"
    data = answers(intent, dataset)
    if intent in {"risk_surface", "model_metrics", "risk"}:
        data["measure"] = _choice(
            {
                "risk_surface": "risk_grid",
                "model_metrics": "model_performance",
                "risk": "historical_risk",
            }[intent]
        )
    if intent == "risk_surface":
        data["risk_map_kind"] = _choice(case["expected_output_selectors"]["map_mode"])
    if intent == "rank":
        data["rank_dimension"] = _choice(args["group_by"])
    result = decide(case["question"], data)
    row = {
        "tool_calls": result.tool_calls,
        "output_selectors": {
            k: result.slots[k]
            for k in ("map_mode", "stat_mode", "series_mode")
            if k in result.slots
        },
    }
    assert result.path == "deterministic", result
    assert plan_matches(case, row), row
