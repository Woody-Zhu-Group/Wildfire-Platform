"""decision_source: who made the answer, clarify, or refuse decision."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions.decide_mode import decide_from_answers
from services.agent.decisions.provenance import decision_source
from services.agent.routing import route_question
from services.agent.schemas import AskResponse, DecisionSource
from tests.agent.test_jev_decide import (
    COUNT_Q,
    FakeBackend,
    _answer_facts,
    _choice,
    _noul,
    _orchestrator,
)

LIVE_Q = "What fires are burning right now?"
ALLOWED_KEYS = {
    "source", "mode", "rule", "disposition", "confidence", "why",
    "jev_disposition", "jev_confidence",
}


def _source_for(decision, mode):
    return decision_source(
        path=decision.path, rule=decision.rule, slots=decision.slots, jev_mode=mode
    )


def _decided(result):
    """The final decision with the jev_decide summary the orchestrator stores."""
    final = result.decision
    final.slots = {
        **final.slots,
        "jev_decide": {
            "winner": result.winner,
            "why": result.why,
            "router_rule": result.router_rule,
            "jev_disposition": result.jev_disposition,
            "jev_rule": result.jev_rule,
            "jev_confidence": result.jev_confidence,
        },
    }
    return final


# ---- Pure mapping: one test per source and router reason ------------------


@pytest.mark.parametrize("mode", ["off", "shadow", "decide"])
def test_a_router_backstop_is_the_source_in_every_mode(mode):
    decision = route_question(LIVE_Q)
    assert decision.rule == "unsupported_live_web"
    if mode == "decide":
        decision = _decided(decide_from_answers(LIVE_Q, decision, None))
    assert _source_for(decision, mode) == {
        "source": "backstop",
        "rule": "unsupported_live_web",
        "mode": mode,
    }


def test_a_jev_decision_reports_disposition_and_confidence():
    answers = _answer_facts(off_topic=_choice("cost_or_budget", 0.93))
    decision = _decided(decide_from_answers(COUNT_Q, route_question(COUNT_Q), answers, gate=0.8))
    assert _source_for(decision, "decide") == {
        "source": "jev",
        "disposition": "unsupported",
        "confidence": 0.93,
        "mode": "decide",
    }


def test_a_jev_answer_over_a_router_decline_is_a_jev_answer():
    question = "Show me fires near Sacramento in 2024"
    router = route_question(question)
    assert router.path == "clarification"
    # Jev is sure the proximity is not vague (Noul 0.02, confidence 0.98).
    answers = _answer_facts(vague_proximity=_noul(0.02))
    result = decide_from_answers(question, router, answers, gate=0.8, answer_gate=0.9)
    assert (result.winner, result.why) == ("jev", "gate"), (router.rule, result.why)
    source = _source_for(_decided(result), "decide")
    assert source["source"] == "jev" and source["disposition"] == "answer"


@pytest.mark.parametrize(
    "why,expected",
    [
        ("below_gate", "jev_below_gate"),
        ("error", "jev_error"),
        ("timeout", "jev_timeout"),
        ("daily_cap", "jev_daily_cap"),
        ("code_verified", "verified_fact"),
        ("contradicts_slot", "verified_fact"),
        ("regex_only", "router_only_route"),
        ("router_only_tool", "router_only_route"),
        ("agree", "jev_agreed"),
    ],
)
def test_every_decide_reason_maps_to_a_router_why(why, expected):
    decision = route_question(COUNT_Q)
    decision.slots = {
        **decision.slots,
        "jev_decide": {
            "winner": "router",
            "why": why,
            "router_rule": decision.rule,
            "jev_disposition": "unsupported",
            "jev_rule": "unsupported_cost",
            "jev_confidence": 0.61,
        },
    }
    source = _source_for(decision, "decide")
    assert source["source"] == "router" and source["why"] == expected
    assert source["mode"] == "decide"
    if expected in {"jev_below_gate", "jev_agreed"}:
        assert source["jev_disposition"] == "unsupported"
        assert source["jev_confidence"] == 0.61
    else:
        assert "jev_confidence" not in source


def test_below_gate_comes_from_the_real_decide_policy():
    answers = _answer_facts(off_topic=_choice("cost_or_budget", 0.6))
    decision = _decided(decide_from_answers(COUNT_Q, route_question(COUNT_Q), answers, gate=0.8))
    assert _source_for(decision, "decide")["why"] == "jev_below_gate"


def test_a_router_only_route_comes_from_the_real_decide_policy():
    question = "Show the statewide ignition risk surface for 2024-08-15"
    router = route_question(question)
    result = decide_from_answers(question, router, None)
    assert result.why == "router_only_tool", (router.rule, result.why)
    assert _source_for(_decided(result), "decide")["why"] == "router_only_route"


@pytest.mark.parametrize(
    "mode,why",
    [
        ("off", "jev_off"),
        ("shadow", "jev_shadow"),
        ("tool_pick", "jev_tool_pick"),
        ("tool_pick_template", "jev_tool_pick"),
        ("decide", "jev_skipped"),
    ],
)
def test_without_a_decide_summary_the_router_decides(mode, why):
    assert _source_for(route_question(COUNT_Q), mode) == {
        "source": "router",
        "why": why,
        "mode": mode,
    }


def test_the_source_never_carries_jev_payload():
    answers = _answer_facts(off_topic=_choice("cost_or_budget", 0.93456789))
    decision = _decided(decide_from_answers(COUNT_Q, route_question(COUNT_Q), answers, gate=0.8))
    decision.slots["jev_decide"]["answers"] = answers  # must never leak
    source = _source_for(decision, "decide")
    assert set(source) <= ALLOWED_KEYS
    assert source["confidence"] == 0.9346
    assert "answers" not in str(source) and "probabilities" not in str(source)
    DecisionSource.model_validate(source)


# ---- Through the orchestrator: routing event and /ask response --------------


def _events_and_response(settings, backend, question, monkeypatch=None):
    events = []

    async def on_event(event, data):
        events.append((event, data))

    orchestrator = _orchestrator(settings, backend)
    result = asyncio.run(orchestrator.ask(question, on_event=on_event))
    routing = [data for event, data in events if event == "routing"]
    return routing, result.response


def test_ask_response_and_routing_event_carry_a_backstop():
    settings = replace(AgentSettings.from_env(), jev_mode="off")
    routing, response = _events_and_response(
        settings, FakeBackend(raises=AssertionError("Jev was called")), LIVE_Q
    )
    expected = {"source": "backstop", "rule": "unsupported_live_web", "mode": "off"}
    assert routing[0]["decision_source"] == expected
    assert response["decision_source"] == expected
    assert AskResponse.model_validate(response).decision_source.source == "backstop"


def test_ask_response_carries_a_jev_decision():
    settings = replace(AgentSettings.from_env(), jev_mode="decide")
    backend = FakeBackend(_answer_facts(off_topic=_choice("cost_or_budget", 0.93)))
    routing, response = _events_and_response(settings, backend, COUNT_Q)
    expected = {"source": "jev", "disposition": "unsupported", "confidence": 0.93, "mode": "decide"}
    assert routing[0]["decision_source"] == expected
    assert response["decision_source"] == expected
    AskResponse.model_validate(response)


def test_ask_response_carries_a_jev_timeout(monkeypatch):
    settings = replace(AgentSettings.from_env(), jev_mode="decide", jev_timeout_seconds=0.05)

    class Slow(FakeBackend):
        def evaluate(self, *args, **kwargs):
            import time

            time.sleep(1.5)
            return super().evaluate(*args, **kwargs)

    orchestrator = _orchestrator(settings, Slow(_answer_facts(off_topic=_choice("cost_or_budget", 0.99))))
    seen = {}

    async def routed(question, *, decision, **kwargs):
        seen["decision"] = decision
        return None

    monkeypatch.setattr(orchestrator, "_ask_routed", routed)
    asyncio.run(orchestrator.ask(COUNT_Q))
    assert seen["decision"].slots["decision_source"] == {
        "source": "router",
        "why": "jev_timeout",
        "mode": "decide",
    }


def test_off_mode_marks_the_router(monkeypatch):
    settings = replace(AgentSettings.from_env(), jev_mode="off")
    orchestrator = _orchestrator(settings, FakeBackend(raises=AssertionError("Jev was called")))
    seen = {}

    async def routed(question, *, decision, **kwargs):
        seen["decision"] = decision
        return None

    monkeypatch.setattr(orchestrator, "_ask_routed", routed)
    asyncio.run(orchestrator.ask(COUNT_Q))
    assert seen["decision"].slots["decision_source"] == {
        "source": "router",
        "why": "jev_off",
        "mode": "off",
    }
