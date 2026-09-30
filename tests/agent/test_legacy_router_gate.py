"""The retired gate is reproducible offline, never a selectable runtime mode."""

import gzip
import json
from pathlib import Path

import pytest

from services.agent.config import AgentSettings
from services.agent.eval.legacy_router_gate import decide_from_answers
from services.agent.eval.router_gate_compare import capture, main
from services.agent.routing import route_question
from tests.agent.test_jev_decide import _choice

COUNT = "How many PG&E utility-attributed ignitions were there in 2024?"
CHANGE = "How did the number of PG&E ignitions change from 2020 to 2023?"
EVAL = Path(__file__).resolve().parents[2] / "services/agent/eval"


def answers(route="router", intent="count", confidence=0.95):
    return {"route": _choice(route, confidence), "intent": _choice(intent)}


def test_historical_confident_acceptance_keeps_exact_calls():
    proposal = route_question(COUNT)
    result = decide_from_answers(COUNT, proposal, answers())
    assert result.decision is proposal and result.why == "router_accepted"


@pytest.mark.parametrize(
    "reading", [answers("agent", "compare"), answers("router", "compare")]
)
def test_historical_change_rejects_an_incompatible_span_count(reading):
    result = decide_from_answers(CHANGE, route_question(CHANGE), reading)
    assert result.decision.path == "model" and not result.decision.tool_calls


def test_historical_low_confidence_still_reproduces_the_old_policy():
    result = decide_from_answers(COUNT, route_question(COUNT), answers(confidence=0.6))
    assert result.decision.path == "model" and result.why == "below_gate"


@pytest.mark.parametrize("error", ["timeout", "daily_cap", "backend_error"])
def test_historical_error_reproduces_the_old_fallback(error):
    proposal = route_question(COUNT)
    result = decide_from_answers(COUNT, proposal, None, error=error)
    assert result.decision is proposal and result.error == error


def test_retired_mode_is_rejected_by_configuration(monkeypatch, fake_jev_credentials):
    monkeypatch.setenv("AGENT_JEV_MODE", "router_gate")
    with pytest.raises(ValueError, match="router_gate was removed"):
        AgentSettings.from_env()
    with pytest.raises(ValueError, match="AGENT_JEV_MODE must"):
        AgentSettings(jev_mode="router_gate").validate()
    monkeypatch.setenv("AGENT_JEV_MODE", "decide")
    assert AgentSettings.from_env().jev_mode == "decide"


def test_retired_gate_has_no_live_capture_entry():
    with pytest.raises(ValueError, match="archived"):
        capture({"question": COUNT}, "router_gate", 1, "2026-09-28")


def test_live_cli_rejects_retired_mode_before_loading_keys(monkeypatch):
    monkeypatch.setattr("sys.argv", ["compare", "--run", "--modes", "router_gate"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_all_original_captures_still_replay_without_network(tmp_path, monkeypatch):
    from services.agent.eval import router_gate_compare

    def forbid_network(*args, **kwargs):
        raise AssertionError("Historical replay must not call a provider")

    monkeypatch.setattr(router_gate_compare, "account_usage", forbid_network)
    monkeypatch.setattr(router_gate_compare, "OpenRouterJevBackend", forbid_network)
    source = EVAL / "runs/router_gate_review_20260928/captures.jsonl.gz"
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        assert len([json.loads(line) for line in stream]) == 540
    target = tmp_path / "replay"
    monkeypatch.setattr(
        "sys.argv", ["compare", "--replay", str(source), "--output", str(target)]
    )
    assert main() == 0
    report = json.loads((target / "report.json").read_text())
    assert {
        mode: stats["disposition_correct"] for mode, stats in report["modes"].items()
    } == {
        "decide_v3": 170,
        "prior_v4": 94,
        "router_gate": 155,
    }
