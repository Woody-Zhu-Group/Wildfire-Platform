"""The new gate selects router versus agent without replacing v3 decide."""

import asyncio
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions.router_gate import decide_from_answers, decide_live
from services.agent.orchestrator import AgentOrchestrator
from services.agent.routing import route_question
from tests.agent.test_jev_decide import FakeBackend, _choice

COUNT = "How many PG&E utility-attributed ignitions were there in 2024?"
CHANGE = "How did the number of PG&E ignitions change from 2020 to 2023?"


def answers(route="router", intent="count", confidence=0.95):
    return {"route": _choice(route, confidence), "intent": _choice(intent)}


def test_confident_acceptance_keeps_exact_calls():
    proposal = route_question(COUNT)
    result = decide_from_answers(COUNT, proposal, answers())
    assert result.decision is proposal
    assert result.decision.path == "deterministic"
    assert result.why == "router_accepted"


@pytest.mark.parametrize(
    "reading", [answers("agent", "compare"), answers("router", "compare")]
)
def test_change_question_never_executes_an_incompatible_span_count(reading):
    proposal = route_question(CHANGE)
    assert proposal.path == "deterministic"
    result = decide_from_answers(CHANGE, proposal, reading)
    assert result.decision.path == "model"
    assert not result.decision.tool_calls
    assert "comparison_run" in result.decision.slots["candidate_tools"]


def test_uncertain_gate_uses_agent_instead_of_claiming_router_fit():
    result = decide_from_answers(COUNT, route_question(COUNT), answers(confidence=0.6))
    assert result.decision.path == "model" and result.why == "below_gate"


@pytest.mark.parametrize("error", ["timeout", "daily_cap", "backend_error"])
def test_unavailable_jev_preserves_router_fallback(error):
    proposal = route_question(COUNT)
    result = decide_from_answers(COUNT, proposal, None, error=error)
    assert result.decision is proposal and result.winner == "router"
    assert result.error == error


@pytest.mark.parametrize(
    "question",
    [
        "What wildfires are burning right now?",
        "Predict the number of ignitions in 2027.",
        "Display Bear Valley circuits intersecting HFTD Tier 2.",
        "How many fires in San Jose in 2024?",
    ],
)
def test_hard_constraints_run_before_jev(question):
    proposal = route_question(question)
    backend = FakeBackend(raises=AssertionError("Hard constraints must not call Jev"))
    result = decide_live(question, proposal, backend=backend, gate=0.8)
    assert result.decision is proposal and backend.calls == 0


def test_one_gate_call_per_question():
    backend = FakeBackend(answers())
    result = decide_live(COUNT, route_question(COUNT), backend=backend, gate=0.8)
    assert result.decision.path == "deterministic" and backend.calls == 1


def test_new_mode_validates_without_replacing_decide(monkeypatch, fake_jev_credentials):
    monkeypatch.setenv("AGENT_JEV_MODE", "router_gate")
    assert AgentSettings.from_env().jev_mode == "router_gate"
    monkeypatch.setenv("AGENT_JEV_MODE", "decide")
    assert AgentSettings.from_env().jev_mode == "decide"


def test_runtime_mode_and_failure_provenance(monkeypatch, tmp_path):
    settings = replace(
        AgentSettings(),
        jev_mode="router_gate",
        jev_log_path=str(tmp_path / "gate.jsonl"),
    )
    agent = AgentOrchestrator(
        settings,
        MagicMock(),
        MagicMock(),
        decide_backend=FakeBackend(error="unavailable"),
    )
    seen = []

    async def routed(question, *, decision, **kwargs):
        seen.append(decision)

    monkeypatch.setattr(agent, "_ask_routed", routed)
    asyncio.run(agent.ask(COUNT))
    assert seen[0].slots["decision_source"]["source"] == "router"
    assert seen[0].slots["decision_source"]["why"] == "jev_error"
    assert seen[0].path == "deterministic"


def test_log_failure_does_not_fail_the_question(monkeypatch, tmp_path):
    from services.agent.decisions.shadow_log import ShadowLog

    def broken(*args, **kwargs):
        raise RuntimeError("logging failure")

    monkeypatch.setattr(ShadowLog, "write", broken)
    settings = replace(
        AgentSettings(),
        jev_mode="router_gate",
        jev_log_path=str(tmp_path / "gate.jsonl"),
    )
    agent = AgentOrchestrator(
        settings, MagicMock(), MagicMock(), decide_backend=FakeBackend(answers())
    )
    seen = []

    async def routed(question, *, decision, **kwargs):
        seen.append(decision)

    monkeypatch.setattr(agent, "_ask_routed", routed)
    asyncio.run(agent.ask(COUNT))
    assert seen[0].path == "deterministic"


def test_rejected_span_count_runs_two_endpoint_reads(tmp_path):
    import httpx
    from services.agent.artifacts import ArtifactStore
    from services.agent.tools import ToolExecutor
    from tests.agent.test_change_endpoints import (
        ScriptedProvider,
        _handler,
        _count_call,
        _primary_counts,
    )

    settings = replace(
        AgentSettings(),
        jev_mode="router_gate",
        jev_log_path=str(tmp_path / "gate.jsonl"),
    )

    async def run():
        executor = ToolExecutor(
            settings, ArtifactStore(60), transport=httpx.MockTransport(_handler)
        )
        try:
            provider = ScriptedProvider(
                [
                    [_count_call(1, utility="PGE", year=2020)],
                    [_count_call(2, utility="PGE", year=2023)],
                ]
            )
            agent = AgentOrchestrator(
                settings,
                provider,
                executor,
                decide_backend=FakeBackend(answers("agent", "compare")),
            )
            return (await agent.ask(CHANGE)).response
        finally:
            await executor.close()

    response = asyncio.run(run())
    assert response["status"] == "answer"
    assert [row["arguments"]["year"] for row in _primary_counts(response)] == [
        2020,
        2023,
    ]
    assert any(row["tool"] == "harness_arithmetic" for row in response["evidence"])
