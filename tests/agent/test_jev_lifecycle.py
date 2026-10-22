"""No Jev follow-up may start after its owning request has ended."""

import asyncio
import concurrent.futures
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions import decide_mode, v4_router
from services.agent.decisions.call_budget import DailyCallBudget
from services.agent.orchestrator import AgentOrchestrator
from services.agent.routing import route_question
from tests.agent.test_jev_decide import FakeBackend
from tests.agent.test_v4_router import answers

QUESTION = "Count PG&E ignitions in 2024."


@pytest.mark.parametrize("end", ["timeout", "disconnect", "task_cancel"])
def test_request_end_prevents_late_fourth_call(monkeypatch, tmp_path, end):
    entered, release, finished = (threading.Event() for _ in range(3))
    prepare = v4_router.prepare

    def blocked_prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return prepare(*args, **kwargs)

    live = v4_router.decide_live

    def observed_live(*args, **kwargs):
        try:
            return live(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(v4_router, "prepare", blocked_prepare)
    monkeypatch.setattr(v4_router, "decide_live", observed_live)
    backend = FakeBackend(answers())
    agent = AgentOrchestrator(
        replace(AgentSettings(), jev_mode="v4", jev_timeout_seconds=0.03,
                jev_log_path=str(tmp_path / "jev.jsonl")),
        MagicMock(), MagicMock(), decide_backend=backend,
    )
    routed = []

    async def dispatch(question, **kwargs):
        routed.append(kwargs["decision"])

    monkeypatch.setattr(agent, "_ask_routed", dispatch)

    async def run():
        cancelled = asyncio.Event()
        task = asyncio.create_task(agent.ask(QUESTION, cancel_event=cancelled))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            if end == "disconnect":
                cancelled.set()
            elif end == "task_cancel":
                task.cancel()
            if end == "timeout":
                await asyncio.wait_for(task, 2)
                assert routed[0].slots["decision_source"]["why"] == "jev_timeout"
            else:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                assert not routed
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 2)

    asyncio.run(run())
    assert backend.calls == 3
    assert agent.jev_budget.calls_today == 3


def test_queued_timeout_refunds_unsent_reservation(monkeypatch):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    entered, release = threading.Event(), threading.Event()

    def occupy():
        entered.set()
        release.wait(5)

    blocker = pool.submit(occupy)
    assert entered.wait(2)
    monkeypatch.setattr(decide_mode, "shared_executor", lambda: pool)
    budget = DailyCallBudget(3)
    backend = FakeBackend(answers())
    try:
        result = v4_router.decide_live(QUESTION, route_question(QUESTION),
                                      backend=backend, timeout=0.02, budget=budget)
        assert result.why == "timeout"
        assert budget.calls_today == 0
    finally:
        release.set()
        blocker.result(2)
        pool.shutdown()
    assert backend.calls == 0


def test_reservation_counts_started_failures_and_refunds_only_unused_slots():
    budget = DailyCallBudget(3)
    reservation = budget.allocate(3)
    assert reservation.start()
    reservation.close()
    reservation.close()
    assert not reservation.start()
    assert budget.calls_today == 1
    assert budget.reserve(2)
    assert not budget.reserve(1)


def test_old_reservation_refund_cannot_subtract_from_a_new_utc_day():
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    budget = DailyCallBudget(3, clock=lambda: now[0])
    reservation = budget.allocate(3)
    now[0] += timedelta(days=1)
    assert budget.reserve(2)
    reservation.close()
    assert budget.calls_today == 2


def test_started_backend_failures_are_still_counted():
    budget = DailyCallBudget(3)
    backend = FakeBackend(error="backend unavailable")
    result = v4_router.decide_live(QUESTION, route_question(QUESTION), backend=backend, budget=budget)
    assert result.why == "error"
    assert backend.calls == budget.calls_today == 3


def test_concurrent_batch_admission_cannot_overrun_the_cap():
    budget = DailyCallBudget(3)
    entered, release = threading.Event(), threading.Event()

    def hold_reservation():
        reservation = budget.allocate(3)
        entered.set()
        assert release.wait(2)
        reservation.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(hold_reservation)
        try:
            assert entered.wait(2)
            assert budget.allocate(3) is None
            assert budget.calls_today == 3
        finally:
            release.set()
        worker.result(2)
    assert budget.calls_today == 0
