"""Jev faults restore the existing keyword Router, including its Agent handoff."""

import asyncio
from dataclasses import asdict, replace
from unittest.mock import MagicMock

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions import v4_router
from services.agent.decisions.call_budget import DailyCallBudget
from services.agent.orchestrator import AgentOrchestrator
from services.agent.routing import route_question
from services.agent.tools import ToolExecutor
from tests.agent.test_change_endpoints import ScriptedProvider, _count_call, _handler
from tests.agent.test_jev_decide import FakeBackend, _choice
from tests.agent.test_v4_router import answers

COUNT = "Count PG&E ignitions in 2024."
AGENT = "Give one total for SCE CPUC ignitions over 2021 through 2023."


@pytest.mark.parametrize(
    "error,why",
    [
        ("timeout after 3s", "timeout"),
        ("backend disabled", "error"),
        ("daily_cap", "daily_cap"),
    ],
)
@pytest.mark.parametrize(
    "question",
    [COUNT, AGENT, "Count PG&E ignitions.", "What fires are burning right now?"],
)
def test_fault_restores_each_keyword_router_outcome_without_partial_jev_state(
    question, error, why
):
    original = route_question(question)
    before = asdict(original)
    result = v4_router.decide_from_answers(
        question, original, answers("compare"), error=error
    )
    assert result.winner == "router" and result.why == why
    assert result.error == error
    assert result.jev_disposition is None
    assert result.decision.path == original.path
    assert result.decision.rule == original.rule
    assert result.decision.tool_calls == original.tool_calls
    assert result.decision.answer == original.answer
    assert not result.extra.get("intent")
    assert not result.decision.slots.get("jev_request")
    assert (
        asdict(original) == before
    )  # timed-out workers must not mutate the live route


def test_daily_cap_routes_to_agent_without_calling_jev():
    backend = FakeBackend(raises=AssertionError("Jev must not run after the cap"))
    result = v4_router.decide_live(
        AGENT, route_question(AGENT), backend=backend, budget=DailyCallBudget(0)
    )
    assert result.decision.path == "model" and result.why == "daily_cap"
    assert backend.calls == 0


def test_fourth_call_cap_falls_back_without_sending_plan_fit():
    backend = FakeBackend(answers())
    result = v4_router.decide_live(
        COUNT, route_question(COUNT), backend=backend, budget=DailyCallBudget(3)
    )
    assert result.winner == "router" and result.why == "daily_cap"
    assert result.decision.path == "deterministic" and backend.calls == 3


def test_no_answers_or_invalid_probabilities_restore_the_keyword_router():
    original = route_question(COUNT)
    for data in (
        None,
        {"intent": {"kind": "choice", "value": "count", "probabilities": {}}},
    ):
        result = v4_router.decide_from_answers(COUNT, original, data)
        assert result.winner == "router" and result.decision.path == "deterministic"
        assert result.why == "error"


def test_fit_stage_fault_uses_the_original_router_not_the_pending_v4_plan():
    class FitError(FakeBackend):
        def evaluate(self, state, questions, **kwargs):
            if "plan_fit" in questions:
                self.error = "fit backend failed"
            return super().evaluate(state, questions, **kwargs)

    question = (
        "Give the numerical difference between PG&E's 2020 and 2023 ignition counts."
    )
    proposal = route_question(question)
    assert proposal.path == "model"
    result = v4_router.decide_live(
        question, proposal, backend=FitError(answers("compare"))
    )
    assert result.winner == "router" and result.decision.path == "model"
    assert not result.decision.tool_calls and result.why == "error"


def test_valid_jev_refusal_does_not_activate_fallback():
    result = v4_router.decide_from_answers(
        COUNT, route_question(COUNT), answers(off_topic=_choice("cpz"))
    )
    assert result.winner == "jev" and result.decision.path == "unsupported"
    assert not result.error


@pytest.mark.parametrize("failure", ["backend", "exception", "outer_timeout"])
def test_runtime_fault_enters_the_router_and_records_its_failure_source(
    monkeypatch, tmp_path, failure
):
    backend = (
        FakeBackend(error="unavailable")
        if failure == "backend"
        else FakeBackend(raises=RuntimeError("unavailable"))
    )
    agent = AgentOrchestrator(
        replace(
            AgentSettings(), jev_mode="v4", jev_log_path=str(tmp_path / "jev.jsonl")
        ),
        MagicMock(),
        MagicMock(),
        decide_backend=backend,
    )
    if failure == "outer_timeout":

        async def time_out(awaitable, **kwargs):
            awaitable.close()
            raise TimeoutError()

        monkeypatch.setattr("services.agent.orchestrator.asyncio.wait_for", time_out)
    seen = []

    async def routed(question, *, decision, **kwargs):
        seen.append(decision)

    monkeypatch.setattr(agent, "_ask_routed", routed)
    asyncio.run(agent.ask(COUNT))
    assert seen[0].path == "deterministic"
    assert seen[0].slots["decision_source"] == {
        "source": "router",
        "why": "jev_timeout" if failure == "outer_timeout" else "jev_error",
        "mode": "v4",
    }


@pytest.mark.parametrize("question,path", [(COUNT, "deterministic"), (AGENT, "model")])
def test_end_to_end_fallback_executes_fixed_reads_or_runs_the_agent(
    tmp_path, question, path
):
    settings = replace(
        AgentSettings(), jev_mode="v4", jev_log_path=str(tmp_path / "jev.jsonl")
    )
    provider = (
        MagicMock()
        if path == "deterministic"
        else ScriptedProvider(
            [
                [
                    _count_call(
                        1, utility="SCE", start_date="2021-01-01", end_date="2023-12-31"
                    )
                ]
            ]
        )
    )

    async def run():
        executor = ToolExecutor(
            settings, ArtifactStore(60), transport=httpx.MockTransport(_handler)
        )
        try:
            agent = AgentOrchestrator(
                settings,
                provider,
                executor,
                decide_backend=FakeBackend(error="unavailable"),
            )
            return (await agent.ask(question)).response
        finally:
            await executor.close()

    response = asyncio.run(run())
    assert response["status"] == "answer"
    assert response["route"]["path"] == path
    assert response["decision_source"] == {
        "source": "router",
        "why": "jev_error",
        "mode": "v4",
    }
    assert any(item["tool"] == "data_query_records" for item in response["evidence"])
    if path == "deterministic":
        assert not provider.mock_calls
