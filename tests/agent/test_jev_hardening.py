"""Jev hardening: startup checks, the per-call daily cap, the bounded admitted
map, the multi-process log writer, and the no-op diff. No network, no Jev calls.

Issues: #35 (key at startup), #37 (cap counts API calls; decide falls back to
the router), #38 (log writer safe across workers), #36 (bounded _admitted),
#62 (Jev modes need AGENT_ALLOW_REMOTE_PROVIDER), #34 (jev_noop_diff keys).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions.call_budget import DailyCallBudget
from services.agent.decisions.decide_mode import decide_live, jev_calls
from services.agent.decisions.provenance import decision_source
from services.agent.decisions.shadow import ShadowRunner
from services.agent.decisions.shadow_log import ShadowLog
from services.agent.eval import jev_noop_diff
from services.agent.routing import route_question
from shared.db import REPO_ROOT
from tests.agent.test_jev_decide import COUNT_Q, FakeBackend, _answer_facts, _orchestrator

JEV_MODES_ON = ["shadow", "tool_pick", "tool_pick_template", "decide"]


# ---------------------------------------------------------------- #35 key at startup


def _env_for_jev(monkeypatch, mode: str, backend: str) -> None:
    monkeypatch.setenv("AGENT_JEV_MODE", mode)
    monkeypatch.setenv("AGENT_JEV_BACKEND", backend)
    monkeypatch.setenv("AGENT_ALLOW_REMOTE_PROVIDER", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")


@pytest.mark.parametrize("mode", JEV_MODES_ON)
@pytest.mark.parametrize("missing", [None, "", "   "])
def test_jev_on_with_typesafe_backend_fails_at_startup_without_the_key(monkeypatch, mode, missing):
    _env_for_jev(monkeypatch, mode, "typesafe")
    if missing is None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", missing)
    with pytest.raises(ValueError) as info:
        AgentSettings.from_env()
    message = str(info.value)
    assert "TYPESAFE_API_KEY" in message
    assert f"AGENT_JEV_MODE={mode}" in message
    assert "sk-or-test-not-a-real-key" not in message


def test_jev_on_with_openrouter_backend_fails_at_startup_without_the_key(monkeypatch):
    _env_for_jev(monkeypatch, "decide", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-not-a-real-key")
    with pytest.raises(ValueError) as info:
        AgentSettings.from_env()
    assert "OPENROUTER_API_KEY" in str(info.value)
    assert "ts-not-a-real-key" not in str(info.value)


def test_jev_on_starts_with_the_active_backend_key(monkeypatch):
    _env_for_jev(monkeypatch, "decide", "openrouter")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert AgentSettings.from_env().jev_mode == "decide"
    _env_for_jev(monkeypatch, "shadow", "typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-not-a-real-key")
    assert AgentSettings.from_env().jev_mode == "shadow"


def test_jev_off_needs_no_jev_key(monkeypatch):
    _env_for_jev(monkeypatch, "off", "typesafe")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert AgentSettings.from_env().jev_mode == "off"


# ---------------------------------------------------------------- #62 remote gate


@pytest.mark.parametrize("mode", JEV_MODES_ON)
@pytest.mark.parametrize("backend", ["typesafe", "openrouter"])
def test_any_jev_mode_requires_the_remote_provider_gate(mode, backend):
    # A loopback model URL keeps the LLM gate quiet, so only the Jev gate can fire.
    settings = AgentSettings(
        model_base_url="http://127.0.0.1:11434/v1",
        allow_remote_provider=False,
        jev_mode=mode,
        jev_backend=backend,
    )
    with pytest.raises(ValueError) as info:
        settings.validate()
    message = str(info.value)
    assert f"AGENT_JEV_MODE={mode}" in message
    assert "AGENT_ALLOW_REMOTE_PROVIDER=true" in message
    assert backend in message
    replace(settings, allow_remote_provider=True).validate()


def test_jev_off_does_not_need_the_remote_gate_for_itself():
    AgentSettings(
        model_base_url="http://127.0.0.1:11434/v1", allow_remote_provider=False, jev_mode="off"
    ).validate()


def test_from_env_names_jev_when_the_gate_is_off(monkeypatch):
    _env_for_jev(monkeypatch, "decide", "openrouter")
    monkeypatch.setenv("AGENT_ALLOW_REMOTE_PROVIDER", "false")
    with pytest.raises(ValueError) as info:
        AgentSettings.from_env()
    assert "AGENT_JEV_MODE=decide" in str(info.value)


# ---------------------------------------------------------------- #37 the cap counts API calls


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


def test_budget_reserves_all_of_a_questions_calls_or_none():
    budget = DailyCallBudget(5)
    assert budget.reserve(3)
    assert budget.remaining == 2
    assert not budget.reserve(3)  # would reach 6
    assert budget.calls_today == 3 and budget.blocked == 1
    assert budget.reserve(2)
    assert budget.remaining == 0
    assert not budget.reserve(1)
    assert budget.reserve(0)


def test_budget_resets_at_the_utc_day_boundary():
    clock = _Clock(datetime(2026, 9, 23, 23, 59, tzinfo=timezone.utc))
    budget = DailyCallBudget(3, clock=clock)
    assert budget.reserve(3) and not budget.reserve(1)
    clock.advance(minutes=2)
    assert budget.remaining == 3
    assert budget.reserve(3)


def test_cap_zero_admits_nothing():
    assert not DailyCallBudget(0).reserve(1)


def test_decide_live_falls_back_to_the_router_when_the_cap_is_reached():
    decision = route_question(COUNT_Q)
    calls = len(jev_calls(COUNT_Q, "2026-09-23"))
    assert calls == 3, "v3_hybrid decide makes three calls per question"
    backend = FakeBackend(_answer_facts())
    budget = DailyCallBudget(calls)

    first = decide_live(COUNT_Q, decision, backend=backend, gate=0.8, budget=budget)
    assert first.why == "agree" and backend.calls == calls
    assert budget.remaining == 0

    second = decide_live(COUNT_Q, decision, backend=backend, gate=0.8, budget=budget)
    assert backend.calls == calls, "a blocked question sends nothing"
    assert second.winner == "router" and second.why == "daily_cap"
    assert second.error == "daily_cap"
    assert second.decision.path == decision.path and second.decision.rule == decision.rule
    assert budget.blocked == 1


def test_exempt_routes_do_not_spend_the_budget():
    decision = route_question("Who is the CEO of PG&E?")  # a backstop refusal
    budget = DailyCallBudget(3)
    result = decide_live("Who is the CEO of PG&E?", decision, backend=FakeBackend(), gate=0.8, budget=budget)
    assert result.why == "backstop"
    assert budget.calls_today == 0


def test_the_cap_is_a_decision_source_reason():
    decision = route_question(COUNT_Q)
    decision.slots = {
        **decision.slots,
        "jev_decide": {"winner": "router", "why": "daily_cap", "router_rule": decision.rule},
    }
    source = decision_source(
        path=decision.path, rule=decision.rule, slots=decision.slots, jev_mode="decide"
    )
    assert source == {"source": "router", "why": "jev_daily_cap", "mode": "decide"}


def test_orchestrator_decide_mode_records_the_cap_and_keeps_the_router_route(monkeypatch):
    settings = replace(AgentSettings.from_env(), jev_mode="decide", jev_daily_call_cap=3)
    backend = FakeBackend(_answer_facts())
    orchestrator = _orchestrator(settings, backend)
    assert orchestrator.jev_budget is not None and orchestrator.jev_budget.cap == 3
    seen: list = []

    async def routed(question, *, decision, **kwargs):
        seen.append(decision)
        return None

    monkeypatch.setattr(orchestrator, "_ask_routed", routed)
    asyncio.run(orchestrator.ask(COUNT_Q))
    asyncio.run(orchestrator.ask(COUNT_Q))
    assert backend.calls == 3
    first, second = seen
    assert first.slots["decision_source"]["why"] == "jev_agreed"
    assert second.slots["decision_source"] == {
        "source": "router",
        "why": "jev_daily_cap",
        "mode": "decide",
    }
    assert second.slots["jev_decide"]["why"] == "daily_cap"
    router = route_question(COUNT_Q)
    assert (second.path, second.rule) == (router.path, router.rule)


def test_off_mode_builds_no_budget():
    settings = replace(AgentSettings.from_env(), jev_mode="off")
    assert _orchestrator(settings, FakeBackend()).jev_budget is None


# ---------------------------------------------------------------- #36 bounded _admitted


class _Silent:
    """A backend that answers nothing, so no outcome or log line depends on it."""

    name = "silent"

    def evaluate(self, state, questions, *, request_id, question_hash):
        return None


def _shadow_runner(tmp_path, clock, **overrides) -> ShadowRunner:
    values = {
        "jev_mode": "shadow",
        "jev_log_path": str(tmp_path / "jev_shadow.jsonl"),
        "jev_log_max_mb": 1,
        "jev_sample_rate": 1,
        "jev_max_concurrency": 4,
        "jev_daily_call_cap": 5000,
        **overrides,
    }
    settings = replace(AgentSettings.from_env(), **values)
    log = ShadowLog(settings.jev_log_path, max_bytes=1024 * 1024)
    return ShadowRunner(settings, _Silent(), log=log, clock=clock)


def test_admitted_entries_expire_when_no_outcome_arrives(tmp_path, monkeypatch):
    from services.agent.decisions import shadow as shadow_mod

    monkeypatch.setattr(shadow_mod, "ADMITTED_TTL_SECONDS", 60.0)
    clock = _Clock(datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
    runner = _shadow_runner(tmp_path, clock)
    decision = route_question(COUNT_Q)
    for index in range(20):
        runner.submit_routing(f"r{index}", COUNT_Q, decision, "2026-09-23", forced=False)
    assert runner.admitted_size() == 20
    assert runner.was_admitted("r0")
    clock.advance(seconds=61)
    runner.submit_routing("late", COUNT_Q, decision, "2026-09-23", forced=False)
    assert runner.admitted_size() == 1
    assert not runner.was_admitted("r0")
    assert runner.was_admitted("late")
    runner.shutdown()


def test_admitted_map_never_exceeds_its_size_cap(tmp_path, monkeypatch):
    from services.agent.decisions import shadow as shadow_mod

    monkeypatch.setattr(shadow_mod, "ADMITTED_MAX_SIZE", 5)
    clock = _Clock(datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
    runner = _shadow_runner(tmp_path, clock)
    decision = route_question(COUNT_Q)
    for index in range(12):
        runner.submit_routing(f"r{index}", COUNT_Q, decision, "2026-09-23", forced=False)
    assert runner.admitted_size() == 5
    assert not runner.was_admitted("r0") and runner.was_admitted("r11")
    runner.shutdown()


def test_an_outcome_still_removes_its_entry(tmp_path):
    clock = _Clock(datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
    runner = _shadow_runner(tmp_path, clock, jev_sample_rate=0)
    decision = route_question(COUNT_Q)
    runner.submit_routing("a", COUNT_Q, decision, "2026-09-23", forced=False)
    assert runner.admitted_size() == 1 and not runner.was_admitted("a")
    runner._write_outcome("a", COUNT_Q, None)
    assert runner.admitted_size() == 0
    runner.shutdown()


# ---------------------------------------------------------------- #38 log writer across processes

_WRITER = """
import sys
from services.agent.decisions.shadow_log import ShadowLog
path, proc, count, max_bytes = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
log = ShadowLog(path, max_bytes=max_bytes)
for n in range(count):
    log.write({"type": "routing", "proc": proc, "n": n, "pad": "x" * 80})
"""


def test_two_processes_append_and_rotate_without_corrupting_the_log(tmp_path):
    path = tmp_path / "jev_shadow.jsonl"
    count = 300
    # About 120 bytes per record, two writers: several rotations inside 5 backups.
    max_bytes = 20_000
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, str(path), str(proc), str(count), str(max_bytes)],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for proc in (1, 2)
    ]
    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode("utf-8", "replace")

    files = [path] + [path.with_name(path.name + f".{i}") for i in range(1, 6)]
    seen: dict[int, set[int]] = {1: set(), 2: set()}
    lines = 0
    for file in files:
        if not file.exists():
            continue
        assert file.stat().st_size <= max_bytes + 200
        for line in file.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)  # every line is whole JSON
            seen[record["proc"]].add(record["n"])
            lines += 1
    assert lines == 2 * count
    assert seen[1] == set(range(count)) and seen[2] == set(range(count))
    assert sum(file.exists() for file in files) >= 3, "rotation happened during the test"
    assert ShadowLog(str(path), max_bytes=max_bytes).read_records().__len__() == 2 * count


def test_the_lock_file_sits_beside_the_log(tmp_path):
    log = ShadowLog(str(tmp_path / "x.jsonl"), max_bytes=1000)
    log.write({"a": 1})
    assert log.lock_path == tmp_path / "x.jsonl.lock"
    assert log.lock_path.exists()


def test_the_log_redacts_both_backend_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-secret-value")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret-value")
    log = ShadowLog(str(tmp_path / "x.jsonl"), max_bytes=10_000)
    log.write({"error": "auth failed for ts-secret-value and or-secret-value"})
    text = (tmp_path / "x.jsonl").read_text(encoding="utf-8")
    assert "ts-secret-value" not in text and "or-secret-value" not in text
    assert text.count("[redacted]") == 2


# ---------------------------------------------------------------- #34 jev_noop_diff keys


def _row(case_id: str, **overrides) -> dict:
    row = {
        "case": {"id": case_id},
        "score": {"route_ok": True, "elapsed_ms": 100, "latency_ms": 5},
        "response": {
            "status": "answer",
            "route": {"path": "deterministic", "rule": "count", "timings": {"total": 9}},
            "answer": "12",
            "caveats": [{"code": "attribute", "text": "x"}],
            "request_id": "abc",
        },
    }
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        row[section][field] = value
    return row


def test_rows_that_differ_only_in_ignored_keys_are_identical():
    left = _row("c1")
    right = _row("c1", score__elapsed_ms=900, score__latency_ms=1, response__request_id="zzz")
    right["response"]["route"]["timings"] = {"total": 500}
    assert jev_noop_diff._diff(jev_noop_diff._score_view(left), jev_noop_diff._score_view(right)) == {}


def test_an_ignored_key_present_on_one_side_only_is_not_a_difference():
    left = _row("c1")
    right = _row("c1")
    del right["score"]["elapsed_ms"]
    del right["response"]["route"]["timings"]
    assert jev_noop_diff._diff(jev_noop_diff._score_view(left), jev_noop_diff._score_view(right)) == {}


def test_nested_differences_are_reported_by_path():
    left = _row("c1")
    right = _row("c1")
    right["response"]["route"]["rule"] = "count_by_utility"
    right["response"]["caveats"] = []
    changes = jev_noop_diff._diff(jev_noop_diff._score_view(left), jev_noop_diff._score_view(right))
    assert changes == {
        "route.rule": ("count", "count_by_utility"),
        "caveats": ([{"code": "attribute", "text": "x"}], []),
    }


def test_a_key_missing_on_one_side_is_a_difference_when_not_ignored():
    left = jev_noop_diff._score_view(_row("c1"))
    right = jev_noop_diff._score_view(_row("c1"))
    del right["route"]["rule"]
    assert jev_noop_diff._diff(left, right) == {"route.rule": ("count", None)}


def _write_run(runs_dir, tag: str, rows: list[dict]) -> None:
    folder = runs_dir / f"20260923__{tag}"
    folder.mkdir(parents=True)
    (folder / "trajectories.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_main_is_clean_for_two_runs_that_differ_only_in_ignored_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(jev_noop_diff, "RUNS", tmp_path)
    _write_run(tmp_path, "jev-off", [_row("c1"), _row("c2")])
    _write_run(
        tmp_path,
        "jev-shadow",
        [_row("c1", score__elapsed_ms=1, response__request_id="other"), _row("c2", score__latency_ms=77)],
    )
    assert jev_noop_diff.main(["jev-off", "jev-shadow"]) == 0
    assert "NO-OP DIFF: CLEAN" in capsys.readouterr().out


def test_main_separates_llm_variance_from_shadow_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(jev_noop_diff, "RUNS", tmp_path)
    _write_run(tmp_path, "jev-off", [_row("c1"), _row("c2")])
    # c1's answer also moves between the two off runs: LLM variance. c2's route
    # changes only in the shadow run: a shadow effect.
    _write_run(tmp_path, "jev-off-2", [_row("c1", response__answer="13"), _row("c2")])
    shadow_c2 = _row("c2")
    shadow_c2["response"]["route"]["rule"] = "other"
    _write_run(tmp_path, "jev-shadow", [_row("c1", response__answer="14"), shadow_c2])
    assert jev_noop_diff.main(["jev-off", "jev-shadow", "--baseline-tag", "jev-off-2"]) == 1
    out = capsys.readouterr().out
    assert "c1 llm_variance ['answer']" in out
    assert "c2 shadow_effect" in out and "route.rule" in out
    assert "NO-OP DIFF: 1 DIFFERENCES" in out
