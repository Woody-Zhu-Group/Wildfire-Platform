"""AGENT_JEV_MODE=decide: backstops first, Jev disposition behind a gate, router fallback."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions.backend import Answer, DecisionResult
from services.agent.decisions.decide_mode import (
    BACKSTOP_RULES,
    decide_from_answers,
    decide_live,
    exemption,
)
from services.agent.routing import UNSUPPORTED, route_question

COUNT_Q = "How many PG&E utility-attributed ignitions were there in 2024?"


def _noul(value):
    return Answer(kind="noul", value=value, confidence=None, probabilities=None)


def _choice(value, confidence=0.95):
    return Answer(kind="choice", value=value, confidence=confidence, probabilities={value: confidence})


def _answer_facts(**overrides):
    """A clean on-topic count question in Jev's answer space."""
    base = {
        "has_time_scope": _noul(0.97),
        "vague_time": _noul(0.03),
        "future_time": _noul(0.03),
        "names_specific_place": _noul(0.3),
        "vague_proximity": _noul(0.02),
        "broad_region": _noul(0.03),
        "asks_risk": _noul(0.03),
        "names_risk_metric": _noul(0.1),
        "prompt_injection": _noul(0.02),
        "is_multi_intent": _noul(0.05),
        "mentions_multiple_datasets": _noul(0.05),
        "off_topic": _choice("on_topic"),
        "intent": _choice("count"),
        "dataset": _choice("cpuc_ignitions"),
        "measure": _choice("count"),
        "county": _choice("none"),
        "rank_dimension": _choice("none"),
    }
    base.update(overrides)
    return base


class FakeBackend:
    """Answers each call with the scripted answers whose names the call asks for."""

    def __init__(self, answers=None, error=None, raises=None):
        self.answers = answers or {}
        self.error = error
        self.raises = raises
        self.calls = 0

    def evaluate(self, state, questions, *, request_id, question_hash, capture=None):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self.error:
            return DecisionResult({}, None, 1.0, None, {}, request_id, question_hash, error=self.error)
        subset = {name: value for name, value in self.answers.items() if name in questions}
        return DecisionResult(subset, "jev-test", 1.0, 10, {}, request_id, question_hash)


def test_backstop_set_is_exact():
    expected = {
        "unsupported_live_web",
        "risk_future_date",
        "unsupported_future_prediction",
        "city_needs_place",
        "hftd_constraint_unavailable",
        *(f"unsupported_{key}" for key in UNSUPPORTED),
    }
    assert BACKSTOP_RULES == expected


@pytest.mark.parametrize(
    "question,rule",
    [
        ("Who is the CEO of PG&E?", "unsupported_leadership"),
        ("Predict which utility will have the most wildfire ignitions in 2027.", "unsupported_future_prediction"),
        ("Was a distribution circuit serving Auburn, California, in Tier 2 or Tier 3 HFTD in 2021?", "city_needs_place"),
        ("Display Bear Valley distribution circuits that intersect Tier 2 or Tier 3 HFTD areas.", "hftd_constraint_unavailable"),
    ],
)
def test_backstops_decide_before_jev(question, rule):
    decision = route_question(question)
    assert decision.rule == rule
    backend = FakeBackend(_answer_facts())
    result = decide_live(question, decision, backend=backend, gate=0.8)
    assert result.winner == "router" and result.why == "backstop"
    assert result.decision is decision and backend.calls == 0


def test_router_only_routes_are_exempt():
    surface = route_question("Show the risk surface for 2024-08-15.")
    assert surface.rule == "risk_surface"
    assert exemption(surface) == "router_only_tool"
    regex = route_question("show me medical baseline data")
    assert exemption(regex) == "regex_only"
    for question, decision in (("Show the risk surface for 2024-08-15.", surface), ("show me medical baseline data", regex)):
        # Even a confident Jev "missing place" never reaches a statewide surface question.
        backend = FakeBackend(_answer_facts(asks_risk=_noul(0.99), names_specific_place=_noul(0.01)))
        result = decide_live(question, decision, backend=backend, gate=0.8)
        assert result.winner == "router" and backend.calls == 0


def test_jev_decline_above_the_gate_wins():
    decision = route_question(COUNT_Q)
    assert decision.path == "deterministic"
    answers = _answer_facts(off_topic=_choice("cost_or_budget", 0.93))
    result = decide_from_answers(COUNT_Q, decision, answers, gate=0.8)
    assert result.winner == "jev" and result.why == "gate"
    assert result.decision.path == "unsupported" and result.decision.rule == "unsupported_cost"
    assert result.decision.answer
    assert result.jev_confidence == pytest.approx(0.93)
    assert result.disagrees


def test_jev_decline_below_the_gate_leaves_the_router():
    decision = route_question(COUNT_Q)
    answers = _answer_facts(off_topic=_choice("cost_or_budget", 0.6))
    result = decide_from_answers(COUNT_Q, decision, answers, gate=0.8)
    assert result.winner == "router" and result.why == "below_gate"
    assert result.decision is decision
    assert result.jev_disposition == "unsupported" and result.disagrees


def test_noul_confidence_is_max_p_one_minus_p():
    question = "How many PG&E utility-attributed ignitions were there near the coast in 2024?"
    decision = route_question(COUNT_Q)
    # vague_proximity 0.1 means a confident "no": this is not a missing-location decline.
    near = _answer_facts(vague_proximity=_noul(0.9), names_specific_place=_noul(0.15))
    result = decide_from_answers(question, decision, near, gate=0.8)
    assert result.jev_rule == "missing_location"
    assert result.jev_confidence == pytest.approx(0.85)


def test_jev_answer_overrides_a_router_decline_only_above_the_gate():
    question = "How many ignitions near the coast in 2024?"
    decision = route_question(question)
    assert decision.path == "clarification" and decision.rule == "undefined_spatial_scope"
    # vague_proximity 0.3: Jev leans "not vague" but only at 0.7, below the gate.
    weak = decide_from_answers(question, decision, _answer_facts(vague_proximity=_noul(0.3)), gate=0.8)
    assert weak.jev_disposition == "answer"
    assert weak.winner == "router" and weak.why == "below_gate" and weak.decision is decision
    # vague_proximity 0.05: confident, so the answer wins and takes the model path.
    strong = decide_from_answers(question, decision, _answer_facts(vague_proximity=_noul(0.05)), gate=0.8)
    assert strong.winner == "jev" and strong.decision.path == "model"
    assert strong.decision.rule == "jev_decide_answer"
    assert strong.jev_confidence == pytest.approx(0.95)


def test_code_decided_router_rules_are_never_overridden():
    # time_out_of_coverage is decided from the question text; Jev has no fact for it.
    question = "How many ignitions were there in 2012?"
    decision = route_question(question)
    if decision.rule != "time_out_of_coverage":
        pytest.skip(f"router rule is {decision.rule}")
    result = decide_from_answers(question, decision, _answer_facts(), gate=0.8)
    assert result.winner == "router"


@pytest.mark.parametrize(
    "backend",
    [FakeBackend(error="ReadTimeout: timed out"), FakeBackend(raises=RuntimeError("boom"))],
)
def test_jev_error_falls_back_to_the_router(backend):
    decision = route_question(COUNT_Q)
    result = decide_live(COUNT_Q, decision, backend=backend, gate=0.8)
    assert result.winner == "router" and result.why == "error"
    assert result.decision is decision and result.error


def _orchestrator(settings, backend, executor=None):
    from services.agent.orchestrator import AgentOrchestrator

    class Provider:
        async def complete(self, **kwargs):
            raise AssertionError("model was called")

    return AgentOrchestrator(settings, Provider(), executor or object(), decide_backend=backend)


def test_off_mode_never_asks_jev(monkeypatch):
    settings = replace(AgentSettings.from_env(), jev_mode="off")
    backend = FakeBackend(raises=AssertionError("Jev was called"))
    orchestrator = _orchestrator(settings, backend)
    seen = {}

    async def routed(question, *, decision, **kwargs):
        seen["decision"] = decision
        return None

    monkeypatch.setattr(orchestrator, "_ask_routed", routed)
    asyncio.run(orchestrator.ask("Who is the CEO of PG&E?"))
    asyncio.run(orchestrator.ask(COUNT_Q))
    assert backend.calls == 0
    assert "jev_decide" not in seen["decision"].slots
    assert seen["decision"].rule == route_question(COUNT_Q).rule


def test_decide_mode_returns_a_jev_refusal_and_logs_the_disagreement(monkeypatch, tmp_path, capsys):
    settings = replace(
        AgentSettings.from_env(), jev_mode="decide", jev_log_path=str(tmp_path / "jev.jsonl")
    )
    backend = FakeBackend(_answer_facts(off_topic=_choice("cost_or_budget", 0.93)))
    orchestrator = _orchestrator(settings, backend)
    result = asyncio.run(orchestrator.ask(COUNT_Q))
    assert result.response["status"] == "unsupported"
    assert backend.calls == 3
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if '"jev_decide"' in line]
    assert logged and logged[0]["winner"] == "jev"
    assert logged[0]["router"]["rule"] == "filtered_records"
    assert logged[0]["jev"]["rule"] == "unsupported_cost"
    assert logged[0]["jev"]["confidence"] == pytest.approx(0.93)
    on_disk = [json.loads(line) for line in (tmp_path / "jev.jsonl").read_text().splitlines()]
    assert on_disk[0]["why"] == "gate"


def test_decide_mode_timeout_leaves_the_router(monkeypatch):
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
    assert seen["decision"].rule == "filtered_records"
    assert seen["decision"].slots["jev_decide"]["why"] == "timeout"
