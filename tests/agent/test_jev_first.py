"""Decide v4 owns intent without a semantic router or failure fallback."""

import asyncio
from dataclasses import replace

import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions.jev_first import decide_from_answers
from services.agent.decisions.v4 import calls_for
from services.agent.orchestrator import AgentOrchestrator
from services.agent.tools import ToolExecutor
from tests.agent.test_change_endpoints import (
    _ask_with_jev,
    _count_call,
    _primary_counts,
)
from tests.agent.test_jev_decide import FakeBackend, _answer_facts, _choice


def facts(intent="count", **extra):
    return _answer_facts(
        intent=_choice(intent), measure=_choice("event_count"), **extra
    )


@pytest.mark.parametrize(
    "question",
    [
        "How did the number of PG&E ignitions change from 2020 to 2023?",
        "What happened to SCE's CPUC ignition count between 2020 and 2023?",
        "How much did SDG&E's ignition tally move from 2020 to 2023?",
        "Describe the difference in PG&E ignition totals from 2020 to 2023.",
    ],
)
def test_jev_comparison_replaces_the_routers_span_count(question):
    decision = decide_from_answers(question, facts("compare"))
    assert decision.path == "model" and decision.rule == "jev_intent"
    assert decision.tool_calls == []
    assert "comparison_run" in decision.slots["candidate_tools"]
    assert decision.slots["jev_request"]["intent"] == "compare"


def test_runtime_never_calls_the_router_or_its_candidate_selector(monkeypatch):
    import services.agent.orchestrator as module

    def forbidden(*args, **kwargs):
        pytest.fail("decide must not invoke semantic routing")

    monkeypatch.setattr(module, "route_question", forbidden)
    monkeypatch.setattr(module, "candidate_tools", forbidden)
    question = "How did the number of PG&E ignitions change from 2020 to 2023?"
    response = _ask_with_jev(
        question,
        [
            _count_call(1, utility="PGE", year=2020),
            _count_call(2, utility="PGE", year=2023),
        ],
        facts("compare"),
    )
    assert response["status"] == "answer"
    assert [e["arguments"]["year"] for e in _primary_counts(response)] == [2020, 2023]
    assert response["decision_source"]["source"] == "jev"


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.79])
def test_uncertain_intent_clarifies_without_a_router_fallback(confidence):
    answers = facts()
    answers["intent"] = _choice("count", confidence)
    decision = decide_from_answers("How many PG&E ignitions in 2024?", answers)
    assert decision.path == "clarification" and decision.rule == "jev_uncertain"
    assert not decision.tool_calls


@pytest.mark.parametrize("error", ["timeout", "daily_cap", "backend unavailable"])
def test_backend_failure_is_an_error_not_a_count(error):
    decision = decide_from_answers(
        "How many PG&E ignitions in 2024?", None, error=error
    )
    assert decision.path == "error" and decision.rule == "jev_unavailable"
    assert not decision.tool_calls


def test_on_topic_jev_does_not_inherit_router_topic_keyword_refusals():
    decision = decide_from_answers(
        "For a budget meeting, compare PG&E ignitions in 2020 and 2023.",
        facts("compare"),
    )
    assert decision.path == "model"


def test_advice_option_refuses_without_leadership_keywords():
    decision = decide_from_answers(
        "Should PG&E be held responsible for 2023 ignitions?",
        facts(off_topic=_choice("advice_or_judgment")),
    )
    assert decision.path == "unsupported" and decision.rule == "unsupported_advice"


@pytest.mark.parametrize(
    "intent,question,measure,tool",
    [
        (
            "model_metrics",
            "How well does the fitted ignition model perform?",
            "model_performance",
            "risk_metrics",
        ),
        (
            "risk_surface",
            "Show the statewide fitted risk grid on 2024-08-15.",
            "risk_grid",
            "risk_surface",
        ),
    ],
)
def test_jev_can_select_the_previously_router_only_capabilities(
    intent, question, measure, tool
):
    answers = facts(intent, risk_map_kind=_choice("risk"))
    answers["measure"] = _choice(measure)
    answers["dataset"] = _choice("none")
    decision = decide_from_answers(question, answers)
    assert decision.path == "deterministic"
    assert decision.tool_calls[0][0] == tool
    assert decision.slots["jev_decide"]["winner"] == "jev"


def test_payload_is_versioned_and_contains_the_new_meanings():
    calls = calls_for("How well does the model perform?", "2026-09-27")
    assert len(calls) == 3
    assert all(call["state"]["schema_version"] == "v4" for call in calls)
    topic = next(call for call in calls if call["name"] == "topic")["questions"]
    assert {"risk_surface", "model_metrics"} <= topic["intent"].criteria.keys()
    assert "advice_or_judgment" in topic["off_topic"].criteria


def test_runtime_backend_error_runs_no_model_or_data_tools(tmp_path):
    class NoModel:
        async def complete(self, **kwargs):
            pytest.fail("backend failure must not start model execution")

    settings = replace(
        AgentSettings(), jev_mode="decide", jev_log_path=str(tmp_path / "jev.jsonl")
    )

    async def run():
        executor = ToolExecutor(settings, ArtifactStore(60))
        try:
            agent = AgentOrchestrator(
                settings,
                NoModel(),
                executor,
                decide_backend=FakeBackend(error="offline"),
            )
            return (await agent.ask("How many PG&E ignitions in 2024?")).response
        finally:
            await executor.close()

    response = asyncio.run(run())
    assert response["status"] == "error"
    assert response["evidence"] == []


def test_a_span_total_cannot_complete_a_jev_comparison():
    response = _ask_with_jev(
        "How did PG&E ignitions change from 2020 to 2023?",
        [_count_call(1, utility="PGE", start_date="2020-01-01", end_date="2023-12-31")],
        facts("compare"),
    )
    assert response["status"] == "error"
    assert any(
        row.get("type") == "uncovered_entities_stop" for row in response["trajectory"]
    )


def test_comparison_tool_can_cover_two_endpoint_months():
    from services.agent.grounding import uncovered_entities, uncovered_periods

    calls = [
        (
            "comparison_run",
            {
                "kind": "periods",
                "period_a_start": "2023-09-01",
                "period_a_end": "2023-09-30",
                "period_b_start": "2024-09-01",
                "period_b_end": "2024-09-30",
            },
        )
    ]
    assert not uncovered_entities({"month": ["2023-09", "2024-09"]}, calls)
    assert not uncovered_periods(
        frozenset({("2023-09-01", "2023-09-30"), ("2024-09-01", "2024-09-30")}), calls
    )


def test_city_lookup_uses_a_gazetteer_point_after_jev_selects_the_task():
    answers = facts("spatial_context", dataset=_choice("hftd"))
    answers["measure"] = _choice("other_measure")
    decision = decide_from_answers(
        "Which HFTD tier contains the center of Santa Rosa?", answers
    )
    assert decision.path == "deterministic"
    assert decision.tool_calls[0][0] == "data_query_spatial"
    assert decision.slots["city_point"]["name"] == "Santa Rosa"


def test_a_circuit_tier_constraint_cannot_disappear():
    decision = decide_from_answers(
        "List PG&E circuits intersecting HFTD Tier 2.",
        facts("records_list", dataset=_choice("circuits")),
    )
    assert decision.path == "clarification"
    assert not decision.tool_calls


def test_model_cannot_silently_drop_one_named_county():
    response = _ask_with_jev(
        "How many CPUC ignitions occurred in Butte County in 2023?",
        [_count_call(1, year=2023)],
        facts(),
    )
    assert response["status"] == "error"
    assert "county:Butte" in str(response["trajectory"])


def test_a_series_can_cover_month_endpoints_without_becoming_a_span_total():
    from services.agent.grounding import uncovered_entities

    args = {"kind": "time_series", "start_date": "2023-09-01", "end_date": "2024-09-30"}
    entities = {"month": ["2023-09", "2024-09"]}
    assert not uncovered_entities(entities, [("visualization_create", args)])
    assert uncovered_entities(entities, [("data_query_records", args)])


def test_an_address_is_not_replaced_by_a_city_center():
    answers = facts("risk")
    answers["measure"] = _choice("historical_risk")
    decision = decide_from_answers(
        "What was the ignition risk at an address in Paradise on 2024-08-15?", answers
    )
    assert decision.rule == "city_needs_place"
    assert not decision.tool_calls
