"""Decide mode: Jev owns the disposition, the router owns the wording.

Covers the production case "Show PSPS events around Santa Rosa", where Jev's generic
place question replaced the router's clarification, plus probes for each branch.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions.decide_mode import _REASON_TEXT, decide_from_answers
from services.agent.routing import route_question
from tests.agent.test_jev_decide import (
    COUNT_Q,
    FakeBackend,
    _answer_facts,
    _choice,
    _noul,
    _orchestrator,
)

SANTA_ROSA = "Show PSPS events around Santa Rosa"
# Jev reads "around Santa Rosa" as vague proximity with no named place: missing_location.
NEAR_NO_PLACE = {"vague_proximity": _noul(0.95), "names_specific_place": _noul(0.05)}


def _decide(question, **facts):
    decision = route_question(question)
    return decision, decide_from_answers(question, decision, _answer_facts(**facts), gate=0.8)


def test_santa_rosa_keeps_the_router_clarification():
    router, result = _decide(SANTA_ROSA, **NEAR_NO_PLACE)
    assert router.rule == "undefined_spatial_scope"
    assert result.winner == "jev" and result.why == "gate" and result.wording == "router"
    assert result.jev_rule == "missing_location"
    # The user sees the router's text, rule, and reason, with the clarify-all-missing year.
    assert result.decision.path == "clarification"
    assert result.decision.rule == router.rule
    assert result.decision.reason == router.reason
    assert result.decision.answer == router.answer
    assert result.decision.answer.startswith("How should that nearby area be defined?")
    assert "I also need a year or date range." in result.decision.answer
    assert "latitude/longitude or bounding box" not in result.decision.answer
    # Jev's reason stays in the log record.
    record = result.log_record(SANTA_ROSA, "r1")
    assert record["jev"]["rule"] == "missing_location"
    assert record["final"]["rule"] == "undefined_spatial_scope"
    assert record["wording"] == "router"
    assert result.reason_differs


def test_both_refuse_keeps_the_router_refusal():
    # The router refuses a leadership question as a backstop, so use a refusal Jev is
    # asked about: the router refuses an unexpressable county filter, Jev says injection.
    question = "How many wildfires were there in Sonoma County in 2024?"
    router = route_question(question)
    if router.path != "unsupported":
        pytest.skip(f"router routes this as {router.path}/{router.rule}")
    result = decide_from_answers(question, router, _answer_facts(prompt_injection=_noul(0.97)), gate=0.8)
    assert result.winner == "jev" and result.wording == "router"
    assert result.jev_rule == "prompt_injection"
    assert (result.decision.rule, result.decision.answer) == (router.rule, router.answer)


def test_same_rule_agreement_still_agrees():
    question = "List recent wildfire incidents"
    router = route_question(question)
    assert router.rule == "ambiguous_relative_time"
    facts = _answer_facts(vague_time=_noul(0.95), has_time_scope=_noul(0.05))
    result = decide_from_answers(question, router, facts, gate=0.8)
    assert result.winner == "router" and result.why == "agree"
    assert result.jev_confidence == pytest.approx(0.95)


def test_jev_clarification_over_a_router_answer_asks_for_everything_missing():
    question = "How many fires were there?"
    router, result = _decide(question, **NEAR_NO_PLACE)
    assert router.path == "model"
    assert result.winner == "jev" and result.wording == "jev"
    answer = result.decision.answer
    assert result.decision.rule == "missing_location"
    assert answer.startswith(_REASON_TEXT["missing_location"])
    assert "I also need a year or date range, and a dataset" in answer
    assert 'For example: "How many CPUC ignitions were there in [a county] in 2023?"' in answer


def test_jev_refusal_over_a_router_clarification_uses_the_jev_refusal():
    router, result = _decide(SANTA_ROSA, off_topic=_choice("cost_or_budget", 0.93))
    assert router.path == "clarification"
    assert result.winner == "jev" and result.wording == "jev"
    assert result.decision.path == "unsupported" and result.decision.rule == "unsupported_cost"


def test_agreement_on_answer_logs_jev_confidence():
    router, result = _decide(COUNT_Q)
    assert router.path == "deterministic"
    assert result.winner == "router" and result.why == "agree"
    # Lowest confidence among the decline facts: the off_topic choice at 0.95.
    assert result.jev_confidence == pytest.approx(0.95)


def _decide_settings(tmp_path):
    return replace(AgentSettings.from_env(), jev_mode="decide", jev_log_path=str(tmp_path / "jev.jsonl"))


def test_santa_rosa_end_to_end_response_and_stream(tmp_path, capsys):
    orchestrator = _orchestrator(_decide_settings(tmp_path), FakeBackend(_answer_facts(**NEAR_NO_PLACE)))
    events = []

    async def on_event(name, data):
        events.append((name, data))

    response = asyncio.run(orchestrator.ask(SANTA_ROSA, on_event=on_event)).response
    router = route_question(SANTA_ROSA)
    assert response["status"] == "clarification"
    assert response["answer_text"] == router.answer
    assert response["route"]["rule"] == "undefined_spatial_scope"
    # decision_source (PR #71) still reports Jev's clarify disposition and confidence.
    assert response["decision_source"]["source"] == "jev"
    assert response["decision_source"]["disposition"] == "clarify"
    assert response["decision_source"]["confidence"] == pytest.approx(0.95)
    routing = next(data for name, data in events if name == "routing")
    assert routing["decision_source"] == response["decision_source"]
    # Same disposition, different reason: Jev's reason is written to the log.
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"jev_decide"' in line]
    assert logged and logged[0]["jev"]["rule"] == "missing_location"
    on_disk = [json.loads(line) for line in (tmp_path / "jev.jsonl").read_text().splitlines()]
    assert on_disk[0]["wording"] == "router"


def test_agreement_summary_carries_confidence(tmp_path):
    orchestrator = _orchestrator(_decide_settings(tmp_path), FakeBackend(_answer_facts()))
    seen = {}

    async def routed(question, *, decision, **kwargs):
        seen["decision"] = decision
        return None

    orchestrator._ask_routed = routed
    asyncio.run(orchestrator.ask(COUNT_Q))
    summary = seen["decision"].slots["jev_decide"]
    assert summary["why"] == "agree" and summary["jev_confidence"] == pytest.approx(0.95)
    # decision_source (PR #71) now carries that confidence for an agreed answer.
    assert seen["decision"].slots["decision_source"]["jev_confidence"] == pytest.approx(0.95)

